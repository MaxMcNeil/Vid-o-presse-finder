from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field

import re
import requests

try:
    import trafilatura
except ImportError:  # Termux : extraction simplifiée
    trafilatura = None

from . import media, scan, transcripts
from .keys import KeyPool, keys_from_env
from .llm import LLMError, QuotaExhausted
from .models import ArticleCandidate, Hit, Plan, Segment, VideoCandidate
from .planner import make_plan
from .sources import news, other_video, youtube
from .util import normalize

log = logging.getLogger("qf.pipeline")
TIMED_OUT = threading.Event()


class PlanError(Exception):
    """Le plan IA n'a pas pu être établi : mieux vaut s'arrêter que chercher n'importe quoi."""


@dataclass
class Options:
    person: str
    claim: str
    also: list[str] = field(default_factory=list)
    anyone: bool = False
    langs: list[str] = field(default_factory=lambda: ["en", "fr"])
    max_videos: int = 40
    max_articles: int = 25
    video_fallback_max: int = 15  # vidéos YouTube sans transcription analysées directement par Gemini
    audio_fallback_max: int = 10  # vidéos sans transcription (Dailymotion...) : audio écouté par Gemini
    min_confidence: float = 0.55
    strict: bool = False
    platforms: list[str] = field(default_factory=lambda: ["youtube", "dailymotion", "archive"])
    articles: bool = True
    workers: int = 4
    max_minutes: int = 40       # durée maximale : au-delà, on rend ce qui est déjà trouvé
    allow_heuristic: bool = False


@dataclass
class VideoResult:
    video: VideoCandidate
    hits: list[Hit]
    method: str  # transcript | gemini-video | none
    note: str = ""


@dataclass
class ArticleResult:
    article: ArticleCandidate
    hits: list[Hit]
    verified: bool
    note: str = ""
    relevant_title: bool = True  # le titre évoque le sujet (sert à trier les articles illisibles)


# ------------------------------------------------------------------ découverte
def _safe(fn, *a, **k):
    try:
        return fn(*a, **k) or []
    except Exception as e:  # noqa: BLE001
        log.warning("source %s en erreur (%s: %s), ignorée", getattr(fn, "__name__", fn), type(e).__name__, str(e)[:120])
        return []


def discover_videos(plan: Plan, o: Options, yt_pool: KeyPool, http=None) -> list[VideoCandidate]:
    found: dict[str, VideoCandidate] = {}

    def add(vs, q):
        for v in vs:
            cur = found.setdefault(v.key, v)
            cur.found_by.add(q)
            for attr in ("title", "description", "channel", "published", "duration"):
                if not getattr(cur, attr) and getattr(v, attr):
                    setattr(cur, attr, getattr(v, attr))

    for i, q in enumerate(plan.youtube_queries):
        lang = o.langs[i % len(o.langs)] if o.langs else None
        if "youtube" in o.platforms:
            add(_safe(youtube.search, yt_pool, q, 25, lang, http), q)
        if "dailymotion" in o.platforms and i < 4:
            add(_safe(other_video.dailymotion, q, 12, http), q)
        if "archive" in o.platforms and i < 3:
            add(_safe(other_video.archive_org, q, 10, http), q)
    vids = list(found.values())
    log.info("%d vidéos candidates", len(vids))
    # Priorité (pas d'exclusion sur le titre, qui peut mentir) : trouvées par plusieurs requêtes + indices dans les métadonnées.
    def prio(v: VideoCandidate):
        meta = normalize(f"{v.title} {v.description[:600]}")
        return -(len(v.found_by) * 2 + scan.group_score(meta, plan.term_groups) +
                 (2 if any(a in meta for a in plan.person_aliases) else 0))
    vids.sort(key=prio)
    vids = [v for v in vids if v.duration is None or v.duration < 4 * 3600]
    return vids[: o.max_videos]


def discover_articles(plan: Plan, o: Options, pools: dict, http=None) -> list[ArticleCandidate]:
    found: dict[str, ArticleCandidate] = {}
    for i, q in enumerate(plan.news_queries):
        lg = o.langs[i % len(o.langs)] if o.langs else "en"
        batch = _safe(news.gdelt, q, 40, http) if i < 3 else []
        batch += _safe(news.bing_news, q, 20, lg, http)
        if i < 4:
            batch += _safe(news.google_news, q, 20, lg, http)
            batch += _safe(news.guardian, pools["guardian"], q, 25, http)
        if i < 3:
            batch += _safe(news.nyt, pools["nyt"], q, 10, http)
        for a in batch:
            if a.url:
                found.setdefault(a.url, a).found_by.add(q)
    arts = list(found.values())
    by_src: dict[str, int] = {}
    for a in arts:
        k = "google" if "news.google.com" in a.url else a.source.split(".")[0][:14]
        by_src[k] = by_src.get(k, 0) + 1
    log.info("%d articles candidats : %s", len(arts), dict(sorted(by_src.items(), key=lambda x: -x[1])[:8]))

    def prio(a: ArticleCandidate):
        meta = normalize(f"{a.title} {a.snippet}")
        return -(len(a.found_by) + scan.group_score(meta, plan.term_groups) + (3 if a.text else 0))
    arts.sort(key=prio)
    return arts[: o.max_articles]


# ------------------------------------------------------------------ analyse
_budget_lock = threading.Lock()


def _take(budget: list[int]) -> bool:
    with _budget_lock:
        if budget[0] > 0:
            budget[0] -= 1
            return True
    return False


def analyze_video(v: VideoCandidate, plan: Plan, o: Options, llm, fallback_budget: list[int],
                  audio_budget: list[int] | None = None) -> VideoResult:
    meta = {"title": v.title, "channel": v.channel, "published": v.published, "description": v.description}
    t = transcripts.get_transcript(v, o.langs)
    try:
        if t and t.segments:
            if not (v.title and v.published) and v.platform == "youtube":
                d = youtube.details_ytdlp(v.id)
                for k in ("title", "published", "channel", "description"):
                    if d.get(k) and not getattr(v, k):
                        setattr(v, k, d[k])
                meta.update(title=v.title, channel=v.channel, published=v.published, description=v.description)
            ex = scan.find_excerpts(t.segments, plan)
            if not ex:
                return VideoResult(v, [], "transcript", "aucun passage ne contient les termes recherchés")
            hits = scan.judge_excerpts(llm, plan, o.person, o.also, o.claim, meta, ex,
                                       "sous-titres automatiques" if "auto" in t.kind else "sous-titres", o.anyone)
            scan.finish_times(hits, v.duration)
            return VideoResult(v, hits, "transcript")
        # pas de transcription : analyse directe de la vidéo par Gemini (YouTube uniquement)
        if v.platform == "youtube" and llm.has_video and _take(fallback_budget):
            hits = scan.judge_video_direct(llm, plan, o.person, o.also, o.claim, v.url, anyone=o.anyone)
            scan.finish_times(hits, v.duration)
            return VideoResult(v, hits, "gemini-video")
        if v.platform != "youtube" and llm.has_video and audio_budget is not None and _take(audio_budget):
            audio = media.fetch_audio(v.url)
            if audio:
                hits = scan.judge_video_direct(llm, plan, o.person, o.also, o.claim, v.url, audio=audio, anyone=o.anyone)
                scan.finish_times(hits, v.duration)
                return VideoResult(v, hits, "gemini-audio")
            return VideoResult(v, [], "none", "pas de sous-titres ; audio : " + (getattr(media.LAST_REASON, "text", "") or "non téléchargeable"))
        return VideoResult(v, [], "none", "pas de transcription disponible (budget d'analyse IA épuisé)")
    except QuotaExhausted as e:
        return VideoResult(v, [], "none", f"quota LLM épuisé ({e})")
    except LLMError as e:
        return VideoResult(v, [], "none", f"erreur LLM: {str(e)[:120]}")


class _P(__import__("html.parser", fromlist=["HTMLParser"]).HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip, self.cur, self.paras = 0, [], []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "header", "aside", "form"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "header", "aside", "form"):
            self.skip = max(0, self.skip - 1)
        if tag in ("p", "h1", "h2", "h3", "li", "blockquote") and self.cur:
            t = " ".join("".join(self.cur).split())
            if len(t) > 40:
                self.paras.append(t)
            self.cur = []

    def handle_data(self, d):
        if not self.skip:
            self.cur.append(d)


def fetch_article_text(url: str) -> str:
    """Texte d'un article : trafilatura si installé, sinon extraction des paragraphes <p>."""
    try:
        if trafilatura is not None:
            html = trafilatura.fetch_url(url)
            return (trafilatura.extract(html) or "") if html else ""
        r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0 (compatible; quote-finder)"})
        if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
            return ""
        p = _P()
        p.feed(r.text)
        return "\n".join(p.paras)
    except Exception:  # noqa: BLE001
        return ""


def analyze_article(a: ArticleCandidate, plan: Plan, o: Options, llm, http=None) -> ArticleResult:
    text = a.text
    if not text:
        try:
            url = a.url
            if "news.google.com" in url:
                try:
                    from googlenewsdecoder import gnewsdecoder
                    dec = gnewsdecoder(url, interval=1)
                    if dec.get("status"):
                        url = a.url = dec["decoded_url"]
                    else:
                        log.info("lien Google News non décodé : %s", str(dec.get("message"))[:100])
                except Exception as e:  # noqa: BLE001
                    log.info("décodeur Google News en erreur : %s", type(e).__name__)
            text = fetch_article_text(url) if "news.google.com" not in url else ""
        except Exception:  # noqa: BLE001
            text = ""
    verified = bool(text)
    text = text or a.snippet
    if not text:
        return ArticleResult(a, [], False, "texte inaccessible (paywall/redirection)")
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    segs = [Segment(float(i), p) for i, p in enumerate(paras)]
    # 1 paragraphe = 1 « seconde » fictive : fenêtre de 3 paragraphes
    ex = scan.find_excerpts(segs, plan, max_excerpts=5, max_chars=3000, win=3.0, step=1.0, context=1.0, merge_gap=1.0)
    if not ex:
        return ArticleResult(a, [], verified, "")
    for e in ex:
        e.text = "\n".join(l.split("] ", 1)[-1] for l in e.text.splitlines())
    meta = {"title": a.title, "channel": a.source, "published": a.published, "description": ""}
    try:
        hits = scan.judge_excerpts(llm, plan, o.person, o.also, o.claim, meta, ex, "article de presse", o.anyone)
    except LLMError as e:
        return ArticleResult(a, [], verified, f"erreur LLM: {str(e)[:100]}")
    for h in hits:
        h.t = None
    return ArticleResult(a, hits, verified, "" if verified else "vérifié sur le chapô/résumé seulement")


def run(o: Options, llm, http=None) -> tuple[Plan, list[VideoResult], list[ArticleResult]]:
    t0 = time.time()
    deadline = t0 + o.max_minutes * 60
    if hasattr(llm, "set_deadline"):
        llm.set_deadline(deadline)
    plan = make_plan(llm, o.person, o.claim, o.also, o.langs, o.anyone)
    if plan.source == "heuristic" and not o.allow_heuristic and getattr(llm, "has_text", False):
        raise PlanError("L'IA (Gemini et le LLM de secours) n'a pas répondu pour préparer la recherche. "
                        "Rien n'a été cherché pour ne pas produire de résultats médiocres : relance dans quelques minutes.")
    log.info("Plan (%s) : %s | groupes : %s", plan.source, plan.claim_summary, plan.term_groups)
    yt_pool = KeyPool("youtube", keys_from_env("YOUTUBE"))
    pools = {"guardian": KeyPool("guardian", keys_from_env("GUARDIAN"), 1.0),
             "nyt": KeyPool("nyt", keys_from_env("NYT"), 13.0)}
    plan.diag = {"youtube_keys": len(yt_pool._next_ok), "queries_video": len(plan.youtube_queries), "queries_news": len(plan.news_queries)}

    vids = discover_videos(plan, o, yt_pool, http)
    by_plat: dict[str, int] = {}
    for v in vids:
        by_plat[v.platform] = by_plat.get(v.platform, 0) + 1
    plan.diag["videos_by_platform"] = by_plat
    if "youtube" in o.platforms and not by_plat.get("youtube"):
        log.warning("AUCUNE vidéo YouTube trouvée (clés YouTube : %d). Lance `qf check` pour tester.", len(yt_pool._next_ok))
    budget = [o.video_fallback_max]
    abudget = [o.audio_fallback_max]
    vres: list[VideoResult] = []

    def safe_video(v):
        try:
            return analyze_video(v, plan, o, llm, budget, abudget)
        except Exception as e:  # noqa: BLE001 - une vidéo en erreur ne doit pas perdre les autres
            return VideoResult(v, [], "none", f"erreur interne ({type(e).__name__}: {str(e)[:100]})")

    video_deadline = t0 + o.max_minutes * 60 * 0.75  # on garde 25 % du temps pour la presse
    ex = ThreadPoolExecutor(o.workers)
    futs = {ex.submit(safe_video, v): v for v in vids}
    handled = set()
    try:
        for i, f in enumerate(as_completed(futs, timeout=max(1.0, video_deadline - time.time())), 1):
            handled.add(f)
            r = f.result()
            vres.append(r)
            log.info("[%d/%d] %s → %d passage(s) bruts (%s)%s", i, len(vids), r.video.title[:60] or r.video.id, len(r.hits),
                     r.method, f" — {r.note}" if r.note else "")
    except FuturesTimeout:
        TIMED_OUT.set()
        log.warning("Temps imparti pour les vidéos écoulé : on rend ce qui est déjà analysé")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    for f, v in futs.items():
        if f not in handled:  # en cours ou annulée à l'échéance : listée, jamais perdue
            vres.append(VideoResult(v, [], "none", "temps imparti écoulé"))
    for r in vres:
        kept = []
        for h in r.hits:
            if scan.keep_hit(h, plan, o.anyone, o.also, o.min_confidence, o.strict):
                kept.append(h)
            else:
                log.info("  écarté (%s, %s, %s, conf %.2f) : %s", h.verdict, h.who_says, h.speaker_name or "?", h.confidence,
                         h.quote[:80])
        r.hits = sorted(kept, key=lambda h: h.t if h.t is not None else 0)
    blocked = sum(1 for r in vres if r.method in ("gemini-video", "gemini-audio"))
    if blocked:
        log.info("%d/%d vidéos analysées sans transcription (YouTube bloque souvent les transcriptions depuis GitHub)",
                 blocked, len(vres))

    ares: list[ArticleResult] = []
    if o.articles and deadline - time.time() > 90:
        try:
            arts = discover_articles(plan, o, pools, http)

            def safe_article(a):
                try:
                    return analyze_article(a, plan, o, llm, http)
                except Exception as e:  # noqa: BLE001
                    return ArticleResult(a, [], False, f"erreur interne ({type(e).__name__})")
            ex = ThreadPoolExecutor(o.workers)
            afuts = {ex.submit(safe_article, a): a for a in arts}
            try:
                for f in as_completed(afuts, timeout=max(1.0, deadline - time.time())):
                    ares.append(f.result())
            except FuturesTimeout:
                TIMED_OUT.set()
                log.warning("Temps imparti pour les articles écoulé")
            finally:
                ex.shutdown(wait=False, cancel_futures=True)
            for r in ares:
                r.hits = [h for h in r.hits if scan.keep_hit(h, plan, o.anyone, o.also, o.min_confidence, o.strict)]
                tn = normalize(r.article.title)
                r.relevant_title = scan.group_score(r.article.title, plan.term_groups) >= 1 and \
                    any(a and a in tn for a in plan.person_aliases)
        except Exception as e:  # noqa: BLE001 - les résultats vidéo sont conservés quoi qu'il arrive
            log.error("Étape articles abandonnée (%s: %s) : le rapport vidéo est tout de même produit", type(e).__name__, e)
    elif o.articles:
        log.warning("Plus assez de temps pour les articles")
    plan.diag["elapsed_min"] = round((time.time() - t0) / 60, 1)
    plan.diag["timed_out"] = TIMED_OUT.is_set()
    return plan, vres, ares
