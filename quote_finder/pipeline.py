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
except ImportError:  # extraction simplifiée
    trafilatura = None

from . import authority, media, scan, transcripts
from .keys import KeyPool, keys_from_env
from .llm import LLMError, QuotaExhausted
from .models import ArticleCandidate, Hit, Plan, Segment, VideoCandidate
from .planner import make_plan
from . import registry
from .sources import media_sites, news, other_video, youtube
from .util import call_with_timeout, normalize

log = logging.getLogger("qf.pipeline")
TIMED_OUT = threading.Event()


class PlanError(Exception):
    """Le plan IA n'a pas pu être établi : mieux vaut s'arrêter que chercher n'importe quoi."""


@dataclass
class Options:
    target: str                      # personne, pays, entité ou sujet
    claim: str = ""                  # affirmation / sujet précis (facultatif si la cible est un sujet)
    target_type: str = "auto"        # auto | personne | pays | entite | sujet
    speakers: str = "auto"           # auto | cible (la cible ou ses représentants) | tous (toute voix qui fait autorité)
    also: list[str] = field(default_factory=list)
    langs: list[str] = field(default_factory=lambda: ["fr", "en"])
    max_videos: int = 8              # vidéos analysées (les plus solides seulement)
    max_articles: int = 6            # articles retenus au maximum
    authority: str = "forte"         # forte | moyenne | toutes
    video_fallback_max: int = 8      # vidéos YouTube sans transcription analysées directement par Gemini
    audio_fallback_max: int = 8      # vidéos sans sous-titres hors YouTube (sites des médias...) : audio écouté par Gemini
    min_confidence: float = 0.6
    strict: bool = False
    platforms: list[str] = field(default_factory=lambda: ["youtube", "dailymotion", "archive"])
    articles: bool = True
    workers: int = 3
    max_minutes: float = 30          # au-delà, on rend ce qui est déjà trouvé
    allow_heuristic: bool = False
    since: str = ""                  # AAAA-MM-JJ : ignorer ce qui est plus ancien (facultatif)

    @property
    def person(self) -> str:  # compatibilité
        return self.target

    @property
    def min_authority(self) -> float:
        return authority.LEVELS.get(self.authority, 0.6)


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


def _recent_enough(published: str, since: str) -> bool:
    return not since or not published or published[:10] >= since


def discover_videos(plan: Plan, o: Options, yt_pool: KeyPool, http=None, llm=None,
                    site_arts: list[ArticleCandidate] | None = None) -> list[VideoCandidate]:
    found: dict[str, VideoCandidate] = {}

    def add(vs, q):
        for v in vs:
            cur = found.setdefault(v.key, v)
            cur.found_by.add(q)
            for attr in ("title", "description", "channel", "published", "duration", "channel_id", "channel_subs"):
                if not getattr(cur, attr) and getattr(v, attr):
                    setattr(cur, attr, getattr(v, attr))

    queries = list(plan.youtube_queries)
    # requêtes ciblant directement les sources de référence (ex. « RTS Suisse Microsoft »)
    short = " ".join((o.claim or "").split()[:6])
    for name in [s for s in plan.authoritative_sources if "." not in s][:3]:
        queries.append(f"{name} {o.target} {short}".strip())
    for i, q in enumerate(queries):
        lang = o.langs[i % len(o.langs)] if o.langs else None
        if "youtube" in o.platforms:
            add(_safe(youtube.search, yt_pool, q, 25, lang, http), q)  # 25 ou 15 résultats : même coût (100 unités)
        if "dailymotion" in o.platforms and i < 3:
            add(_safe(other_video.dailymotion, q, 10, http), q)
        if "archive" in o.platforms and i < 2:
            add(_safe(other_video.archive_org, q, 8, http), q)
    # sources de référence : vidéos de leurs sites (souvent sous-titrées) et recherche dans leurs chaînes YouTube
    sq = plan.site_query or f"{o.target} {short}".strip()
    if site_arts:
        from . import screen
        ranked = sorted(site_arts, key=lambda a: -screen.local_relevance(a.title, a.snippet, plan, o.target_type))
        sv = _safe(media_sites.videos_from_articles, ranked, 12)
        plan.diag["site_pages_probed"] = media_sites.LAST.get("probed", 0)
        plan.diag["site_videos"] = len(sv)
        add(sv, "site")
    if "youtube" in o.platforms:
        names = registry.youtube_channels(plan.countries, o.langs, 3)
        cv = _safe(media_sites.search_in_channels, yt_pool, names, sq, 8, http)
        plan.diag["official_channels"] = {n: media_sites.LAST.get("channels", {}).get(n, "?") for n in names}
        add(cv, "chaînes")
    vids = [v for v in found.values() if (v.duration is None or v.duration < 4 * 3600) and _recent_enough(v.published, o.since)]
    seen_titles, uniq = set(), []
    for v in vids:  # même vidéo publiée deux fois (ex. « Le Point » et « Le Point Abonnés »)
        k = normalize(v.title)[:70]
        if len(k) >= 15 and k in seen_titles:
            continue
        seen_titles.add(k)
        uniq.append(v)
    vids = uniq
    for v in vids:
        v.authority, v.authority_why = authority.score_video(v, plan)
    plan.diag["videos_candidates"] = len(vids)
    return select(vids, plan, o, llm, kind="vidéos", cap=o.max_videos,
                  meta=lambda v: (v.title, v.channel, v.published, v.description), diag="videos")


def select(items: list, plan: Plan, o: Options, llm, kind: str, cap: int, meta, diag: str) -> list:
    """Pertinence d'abord (filtre local puis note de l'IA sur les titres), autorité ensuite.
    Avec l'autorité « forte », si moins de la moitié des places sont remplies par des sources fortes et pertinentes,
    on complète avec des sources moyennes (≥ 0.4) TRÈS pertinentes ; elles sont signalées dans le rapport."""
    from . import screen
    rel = {id(x): screen.local_relevance(meta(x)[0], meta(x)[3], plan, o.target_type) for x in items}
    floor = min(o.min_authority, 0.4)
    pool = [x for x in items if x.authority >= floor and rel[id(x)] >= 1.0]
    pool.sort(key=lambda x: -(rel[id(x)] + 0.5 * len(getattr(x, "found_by", ()) or ()) + 3 * x.authority))
    pool = pool[:60]
    scores = screen.llm_scores(llm, plan, o, [meta(x) for x in pool], kind) if pool else None
    if scores is not None:
        for i, x in enumerate(pool):
            x.relevance = scores.get(i, 0.0)
        pool = [x for x in pool if x.relevance >= 6]
        log.info("%s : %d pertinentes d'après l'IA (note ≥ 6/10)", kind, len(pool))
    else:  # IA indisponible : il faut au moins ~60 % des concepts du sujet dans le titre/la description
        need = scan.needed(plan.term_groups)
        for x in pool:
            g = scan.group_score(f"{meta(x)[0]} {meta(x)[3][:800]}", plan.term_groups)
            x.relevance = 7.0 if g >= need else 3.0
        pool = [x for x in pool if x.relevance >= 6]
    pool.sort(key=lambda x: -(x.relevance / 10 * 0.6 + x.authority * 0.4))
    strong = [x for x in pool if x.authority >= o.min_authority]
    chosen = strong[:cap]
    if len(chosen) < max(1, cap // 2) and o.authority == "forte":
        extra = [x for x in pool if x not in chosen and x.authority >= 0.4 and x.relevance >= 7][: cap - len(chosen)]
        for x in extra:
            x.authority_why += " — ajoutée faute de source forte sur ce sujet"
        chosen += extra
        if extra:
            log.info("%s : %d source(s) d'autorité moyenne ajoutée(s) faute de source forte assez pertinente", kind, len(extra))
    if diag == "videos":  # pour le rapport : ce qu'ont donné les vidéos prises chez les sources de référence
        plan.diag["site_videos_scored"] = [f"{x.title[:60]} ({getattr(x, 'relevance', 0):.0f}/10)" for x in items
                                           if "site" in (getattr(x, "found_by", None) or ()) or
                                           any(str(f).startswith("chaîne:") for f in (getattr(x, "found_by", None) or ()))][:8]
    plan.diag[f"{diag}_low_authority"] = sum(1 for x in items if x.authority < o.min_authority)
    plan.diag[f"{diag}_relevant"] = len(pool)
    log.info("%s : %d candidates → %d pertinentes → %d retenues", kind, len(items), len(pool), len(chosen))
    return chosen


def discover_articles(plan: Plan, o: Options, pools: dict, http=None, llm=None,
                      site_arts: list[ArticleCandidate] | None = None) -> list[ArticleCandidate]:
    found: dict[str, ArticleCandidate] = {a.url: a for a in (site_arts or []) if a.url}
    queries = list(plan.news_queries)
    doms = [d for d in plan.authoritative_sources if "." in d and " " not in d][:3]
    base = f"{o.target} {' '.join((o.claim or '').split()[:6])}".strip()
    queries += [f"{base} site:{authority.domain_of('https://' + d.split('//')[-1])}" for d in doms]
    for i, q in enumerate(queries):
        lg = o.langs[i % len(o.langs)] if o.langs else "en"
        batch = _safe(news.gdelt, q, 30, http) if i < 2 else []
        batch += _safe(news.bing_news, q, 20, lg, http)
        if i < 4 or "site:" in q:
            batch += _safe(news.google_news, q, 20, lg, http)
        if i < 3:
            batch += _safe(news.guardian, pools["guardian"], q, 15, http)
            batch += _safe(news.nyt, pools["nyt"], q, 10, http)
        for a in batch:
            if a.url and _recent_enough(a.published, o.since):
                found.setdefault(a.url, a).found_by.add(q)
    arts = list(found.values())
    for a in arts:
        a.authority, a.authority_why = authority.score_article(a, plan)
    plan.diag["articles_candidates"] = len(arts)
    # on en lit 2x plus que demandé : certains seront illisibles (paywall) ou hors sujet
    return select(arts, plan, o, llm, kind="articles", cap=o.max_articles * 2,
                  meta=lambda a: (a.title, a.source, a.published, a.snippet or a.text[:300]), diag="articles")


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
            hits = scan.judge_excerpts(llm, plan, o, meta, ex,
                                       "sous-titres automatiques" if "auto" in t.kind else "sous-titres")
            scan.refine_times(hits, t.segments)  # début/fin calés sur la transcription réelle
            scan.finish_times(hits, v.duration)
            return VideoResult(v, hits, "transcript")
        # pas de transcription : analyse directe de la vidéo par Gemini (YouTube uniquement)
        if v.platform == "youtube" and llm.has_video and _take(fallback_budget):
            hits = scan.judge_video_direct(llm, plan, o, v.url)
            scan.finish_times(hits, v.duration)
            return VideoResult(v, hits, "gemini-video")
        if v.platform != "youtube" and llm.has_video and audio_budget is not None and _take(audio_budget):
            audio = media.fetch_audio(v.url)
            if audio:
                hits = scan.judge_video_direct(llm, plan, o, v.url, audio=audio)
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


def resolve_google_news(a: ArticleCandidate, http=None) -> str:
    """Lien Google News -> lien réel de l'article. 1) décodeur ; 2) recherche du titre exact sur Bing, même média."""
    try:
        from googlenewsdecoder import gnewsdecoder
        dec = call_with_timeout(gnewsdecoder, 30, a.url, interval=1)
        if dec and dec.get("status"):
            a.url = dec["decoded_url"]
            a.domain = authority.domain_of(a.url)
            return a.url
        log.info("lien Google News non décodé : %s", str((dec or {}).get("message"))[:100])
    except Exception as e:  # noqa: BLE001
        log.info("décodeur Google News en erreur : %s", type(e).__name__)
    if a.title:
        for lg in ("fr", "en"):
            for b in _safe(news.bing_news, f'"{a.title[:150]}"', 5, lg, http):
                same_site = a.domain and (b.domain == a.domain or b.domain.endswith("." + a.domain))
                if same_site or scan.term_present(normalize(b.title), normalize(a.title)[:80]):
                    a.url, a.domain = b.url, b.domain or a.domain
                    return a.url
    return ""


def analyze_article(a: ArticleCandidate, plan: Plan, o: Options, llm, http=None) -> ArticleResult:
    text = a.text
    if not text:
        try:
            url = a.url
            if "news.google.com" in url:
                url = resolve_google_news(a, http) or url
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
    meta = {"title": a.title, "channel": f"{a.source} ({a.domain})" if a.domain else a.source,
            "published": a.published, "description": ""}
    try:
        hits = scan.judge_excerpts(llm, plan, o, meta, ex, "article de presse")
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
    TIMED_OUT.clear()
    plan = make_plan(llm, o.target, o.claim, o.also, o.langs, o.speakers, o.target_type)
    if o.target_type == "auto":
        o.target_type = plan.target_type
    # langues des pays concernés ajoutées automatiquement (sous-titres, requêtes) : ex. Italie -> it, Golfe -> ar
    for c in registry.expand(plan.countries):
        lg = registry.COUNTRY_LANG.get(c)
        if lg and lg not in o.langs and len(o.langs) < 4:
            o.langs.append(lg)
    if o.speakers == "auto":  # une personne : ses propres paroles ; un pays/une entité/un sujet : toute voix qui fait autorité
        o.speakers = "cible" if o.target_type == "personne" else "tous"
    if plan.source == "heuristic" and not o.allow_heuristic and getattr(llm, "has_text", False):
        raise PlanError("L'IA (Gemini et le LLM de secours) n'a pas répondu pour préparer la recherche. "
                        "Rien n'a été cherché pour ne pas produire de résultats médiocres : relance dans quelques minutes.")
    log.info("Plan (%s) : cible %s (%s), locuteurs : %s | %s | groupes : %s", plan.source, o.target, o.target_type,
             o.speakers, plan.claim_summary, plan.term_groups)
    yt_pool = KeyPool("youtube", keys_from_env("YOUTUBE"), persist=True)
    pools = {"guardian": KeyPool("guardian", keys_from_env("GUARDIAN"), 1.0, persist=True),
             "nyt": KeyPool("nyt", keys_from_env("NYT"), 13.0, persist=True)}
    plan.diag = {"youtube_keys": len(yt_pool._next_ok), "queries_video": len(plan.youtube_queries),
                 "queries_news": len(plan.news_queries), "authority": o.authority, "speakers": o.speakers}

    srcs = registry.for_search(plan.countries, o.langs, [x for x in plan.authoritative_sources if "." in x], limit=8)
    sq = plan.site_query or f"{o.target} {' '.join((o.claim or '').split()[:6])}".strip()
    site_arts = _safe(media_sites.search_articles, srcs, sq, o.langs, http)
    plan.diag["reference_sites"] = [x.domain for x in srcs]
    plan.diag["site_articles"] = len(site_arts)
    plan.diag["site_articles_by_site"] = media_sites.LAST.get("by_site", {})
    vids = discover_videos(plan, o, yt_pool, http, llm, site_arts)
    by_plat: dict[str, int] = {}
    for v in vids:
        by_plat[v.platform] = by_plat.get(v.platform, 0) + 1
    plan.diag["videos_by_platform"] = by_plat
    if "youtube" in o.platforms and not by_plat.get("youtube"):
        log.warning("AUCUNE vidéo YouTube retenue (clés YouTube : %d). Lance le workflow « Diagnostic ».", len(yt_pool._next_ok))
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
            if scan.keep_hit(h, plan, o):
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
            arts = discover_articles(plan, o, pools, http, llm, site_arts)

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
                r.hits = [h for h in r.hits if scan.keep_hit(h, plan, o)]
                tn = normalize(r.article.title)
                r.relevant_title = scan.group_score(r.article.title, plan.term_groups) >= 1 and \
                    (o.target_type == "sujet" or any(a and a in tn for a in plan.target_aliases))
            ares.sort(key=lambda r: (not r.hits, -r.article.authority))
            kept = [r for r in ares if r.hits][: o.max_articles]
            ares = kept + [r for r in ares if not r.hits]
        except Exception as e:  # noqa: BLE001 - les résultats vidéo sont conservés quoi qu'il arrive
            log.error("Étape articles abandonnée (%s: %s) : le rapport vidéo est tout de même produit", type(e).__name__, e)
    elif o.articles:
        log.warning("Plus assez de temps pour les articles")
    plan.diag["elapsed_min"] = round((time.time() - t0) / 60, 1)
    plan.diag["youtube_keys_status"] = yt_pool.status()
    if hasattr(llm, "usage"):
        plan.diag["llm_usage"] = llm.usage()
    from . import quota
    quota.state().save()
    plan.diag["timed_out"] = TIMED_OUT.is_set()
    return plan, vres, ares
