from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import requests
import trafilatura

from . import media, scan, transcripts
from .keys import KeyPool, keys_from_env
from .llm import LLMError, QuotaExhausted
from .models import ArticleCandidate, Hit, Plan, Segment, VideoCandidate
from .planner import make_plan
from .sources import news, other_video, youtube
from .util import normalize

log = logging.getLogger("qf.pipeline")


@dataclass
class Options:
    person: str
    claim: str
    also: list[str] = field(default_factory=list)
    anyone: bool = False
    langs: list[str] = field(default_factory=lambda: ["en", "fr"])
    max_videos: int = 40
    max_articles: int = 25
    video_fallback_max: int = 6   # vidéos YouTube sans transcription analysées directement par Gemini
    audio_fallback_max: int = 10  # vidéos sans transcription (Dailymotion...) : audio écouté par Gemini
    min_confidence: float = 0.55
    strict: bool = False
    platforms: list[str] = field(default_factory=lambda: ["youtube", "dailymotion", "archive"])
    articles: bool = True
    workers: int = 4


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


# ------------------------------------------------------------------ découverte
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
            add(youtube.search(yt_pool, q, 25, lang, http), q)
        if "dailymotion" in o.platforms and i < 4:
            add(other_video.dailymotion(q, 12, http), q)
        if "archive" in o.platforms and i < 3:
            add(other_video.archive_org(q, 10, http), q)
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
        batch = news.gdelt(q, 40, http) if i < 3 else []
        batch += news.bing_news(q, 20, o.langs[i % len(o.langs)] if o.langs else "en", http)
        if i < 4:
            batch += news.google_news(q, 20, o.langs[i % len(o.langs)] if o.langs else "en", http)
        if i < 4:
            batch += news.guardian(pools["guardian"], q, 25, http)
        if i < 3:
            batch += news.nyt(pools["nyt"], q, 10, http)
        for a in batch:
            if a.url:
                found.setdefault(a.url, a).found_by.add(q)
    arts = list(found.values())

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
                                       "sous-titres automatiques" if "auto" in t.kind else "sous-titres")
            return VideoResult(v, hits, "transcript")
        # pas de transcription : analyse directe de la vidéo par Gemini (YouTube uniquement)
        if v.platform == "youtube" and llm.has_video and _take(fallback_budget):
            hits = scan.judge_video_direct(llm, plan, o.person, o.also, o.claim, v.url)
            return VideoResult(v, hits, "gemini-video")
        if v.platform != "youtube" and llm.has_video and audio_budget is not None and _take(audio_budget):
            audio = media.fetch_audio(v.url)
            if audio:
                hits = scan.judge_video_direct(llm, plan, o.person, o.also, o.claim, v.url, audio=audio)
                return VideoResult(v, hits, "gemini-audio")
            return VideoResult(v, [], "none", "pas de sous-titres et audio non téléchargeable")
        return VideoResult(v, [], "none", "pas de transcription disponible (budget d'analyse IA épuisé)")
    except QuotaExhausted as e:
        return VideoResult(v, [], "none", f"quota LLM épuisé ({e})")
    except LLMError as e:
        return VideoResult(v, [], "none", f"erreur LLM: {str(e)[:120]}")


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
                except Exception:  # noqa: BLE001
                    pass
            html = trafilatura.fetch_url(url) if "news.google.com" not in url else None
            text = (trafilatura.extract(html) or "") if html else ""
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
        hits = scan.judge_excerpts(llm, plan, o.person, o.also, o.claim, meta, ex, "article de presse")
    except LLMError as e:
        return ArticleResult(a, [], verified, f"erreur LLM: {str(e)[:100]}")
    for h in hits:
        h.t = None
    return ArticleResult(a, hits, verified, "" if verified else "vérifié sur le chapô/résumé seulement")


def run(o: Options, llm, http=None) -> tuple[Plan, list[VideoResult], list[ArticleResult]]:
    plan = make_plan(llm, o.person, o.claim, o.also, o.langs)
    log.info("Plan : %s | groupes : %s", plan.claim_summary, plan.term_groups)
    yt_pool = KeyPool("youtube", keys_from_env("YOUTUBE"))
    pools = {"guardian": KeyPool("guardian", keys_from_env("GUARDIAN"), 1.0),
             "nyt": KeyPool("nyt", keys_from_env("NYT"), 13.0)}

    vids = discover_videos(plan, o, yt_pool, http)
    budget = [o.video_fallback_max]
    abudget = [o.audio_fallback_max]
    vres: list[VideoResult] = []
    with ThreadPoolExecutor(o.workers) as ex:
        futs = {ex.submit(analyze_video, v, plan, o, llm, budget, abudget): v for v in vids}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            vres.append(r)
            log.info("[%d/%d] %s → %d passage(s) (%s)", i, len(vids), r.video.title[:60] or r.video.id, len(r.hits), r.method)
    for r in vres:
        r.hits = sorted([h for h in r.hits if scan.keep_hit(h, plan, o.anyone, o.also, o.min_confidence, o.strict)],
                        key=lambda h: h.t if h.t is not None else 0)

    ares: list[ArticleResult] = []
    if o.articles:
        arts = discover_articles(plan, o, pools, http)
        with ThreadPoolExecutor(o.workers) as ex:
            ares = list(ex.map(lambda a: analyze_article(a, plan, o, llm, http), arts))
        for r in ares:
            r.hits = [h for h in r.hits if scan.keep_hit(h, plan, o.anyone, o.also, o.min_confidence, o.strict)]
    return plan, vres, ares
