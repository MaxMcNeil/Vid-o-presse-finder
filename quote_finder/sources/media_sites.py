"""Recherche directement chez les sources de référence (annuaire registry.py + sources proposées par l'IA).

1. Articles : recherche « mots-clés site:domaine » (Bing Actualités, Google Actualités) — gratuit, sans quota.
2. Vidéos de leurs sites : chaque page trouvée chez un média vidéo est ouverte par yt-dlp ; s'il y a une vidéo
   (RTS, SRF, franceinfo, BBC, Tagesschau...), elle devient une candidate, souvent avec sous-titres
   (horodatage précis) et sans le blocage YouTube.
3. Chaînes YouTube officielles : recherche à l'intérieur de la chaîne avec yt-dlp (0 unité de quota) ;
   l'identifiant de la chaîne est trouvé une fois (100 unités) puis gardé en cache.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
import urllib.parse as up
from concurrent.futures import ThreadPoolExecutor

from .. import cache, registry
from ..authority import domain_of
from ..models import ArticleCandidate, VideoCandidate
from . import news, youtube

log = logging.getLogger("qf.sites")
LAST: dict = {}  # compteurs de la dernière recherche, repris dans le diagnostic du rapport


def _safe(fn, *a, **k):
    try:
        return fn(*a, **k) or []
    except Exception as e:  # noqa: BLE001
        log.info("%s : %s", getattr(fn, "__name__", "?"), str(e)[:100])
        return []


def search_articles(sources: list, query: str, langs: list[str], http=None) -> list[ArticleCandidate]:
    LAST.clear()  # nouvelle recherche : compteurs remis à zéro
    out: dict[str, ArticleCandidate] = {}
    lg = (langs or ["fr"])[0]
    for s in sources:
        q = f"{query} site:{s.domain}"
        batch = _safe(news.bing_news, q, 10, lg, http) + _safe(news.google_news, q, 10, lg, http)
        for a in batch:
            d = a.domain or domain_of(a.url)
            if d and not (d == s.domain or d.endswith("." + s.domain)) and "news.google.com" not in a.url:
                continue  # Bing renvoie parfois d'autres sites malgré site:
            a.domain = a.domain or s.domain
            a.source = a.source or s.name
            a.found_by.add(f"site:{s.domain}")
            out.setdefault(a.url, a)
    by = {}
    for a in out.values():
        k = next((s.domain for s in sources if a.domain == s.domain or a.domain.endswith("." + s.domain)), a.domain)
        by[k] = by.get(k, 0) + 1
    LAST["by_site"] = by
    log.info("Sources de référence (%s) : %d article(s) %s", ", ".join(s.domain for s in sources), len(out), by)
    return list(out.values())


def _ytdlp_json(url: str, timeout: int = 60) -> dict | None:
    try:
        p = subprocess.run([sys.executable, "-m", "yt_dlp", "--dump-single-json", "--skip-download", "--no-warnings",
                            "--no-playlist", "--socket-timeout", "20", url], capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return None
    try:
        return json.loads(p.stdout) if p.stdout.strip() else None
    except json.JSONDecodeError:
        return None


def probe_video(a: ArticleCandidate, source_name: str = "") -> VideoCandidate | None:
    """La page de cet article contient-elle une vidéo lisible ? (résultat gardé 7 jours en cache)"""
    ck = a.url
    c = cache.get("probe", ck)
    if c is not None:
        return VideoCandidate(**c) if c else None
    d = _ytdlp_json(a.url)
    if d and d.get("_type") == "playlist" and d.get("entries"):
        d = next((e for e in d["entries"] if e), None)
    if not d or not (d.get("duration") or d.get("formats") or d.get("url")):
        cache.put("probe", ck, {})
        return None
    ex = (d.get("extractor_key") or d.get("extractor") or "").lower()
    up_date = d.get("upload_date") or ""
    pub = f"{up_date[:4]}-{up_date[4:6]}-{up_date[6:8]}" if len(up_date) == 8 else (a.published or "")
    if ex.startswith("youtube") and d.get("id"):
        v = VideoCandidate("youtube", d["id"], f"https://www.youtube.com/watch?v={d['id']}", d.get("title") or a.title,
                           (d.get("description") or "")[:1500], d.get("channel") or d.get("uploader") or source_name, pub,
                           int(d["duration"]) if d.get("duration") else None)
    else:
        url = d.get("webpage_url") or a.url
        vid = str(d.get("id") or hashlib.sha1(url.encode()).hexdigest()[:12])
        v = VideoCandidate("web", vid, url, d.get("title") or a.title, (d.get("description") or a.snippet or "")[:1500],
                           source_name or a.source, pub, int(d["duration"]) if d.get("duration") else None)
    if v.duration and v.duration < 20:  # bande-annonce, jingle
        cache.put("probe", ck, {})
        return None
    data = dict(v.__dict__)
    data.pop("found_by", None)
    cache.put("probe", ck, data)
    return v


def videos_from_articles(arts: list[ArticleCandidate], limit: int = 12, workers: int = 4) -> list[VideoCandidate]:
    vsites = {s.domain: s for s in registry.all_domains().values() if s.video}
    todo = []
    for a in arts:
        d = a.domain or domain_of(a.url)
        src = next((s for dom, s in vsites.items() if d == dom or d.endswith("." + dom)), None)
        if src:
            todo.append((a, src.name))
    todo = todo[:limit]
    LAST["probed"] = len(todo)
    if not todo:
        return []
    from ..pipeline import resolve_google_news  # import tardif : évite une boucle d'import

    def one(item):
        a, name = item
        if "news.google.com" in a.url and not resolve_google_news(a):
            return None
        return probe_video(a, name)
    with ThreadPoolExecutor(workers) as ex:
        res = [v for v in ex.map(one, todo) if v]
    log.info("Vidéos trouvées sur les sites des médias : %d (sur %d page(s) ouvertes)", len(res), len(todo))
    return res


def channel_id(pool, name: str, http=None) -> str:
    """Identifiant d'une chaîne YouTube à partir de son nom : 100 unités une seule fois, puis cache."""
    c = cache.get("ytchannel", name.lower())
    if c is not None:
        return c.get("id", "")
    d = youtube._call(pool, "search", {"part": "snippet", "type": "channel", "q": name, "maxResults": 1}, http) if pool else None
    cid = ""
    for it in (d or {}).get("items", []):
        cid = (it.get("id") or {}).get("channelId", "") or (it.get("snippet") or {}).get("channelId", "")
        break
    if d is not None:
        cache.put("ytchannel", name.lower(), {"id": cid})
    return cid


def search_in_channels(pool, names: list[str], query: str, per_channel: int = 8, http=None) -> list[VideoCandidate]:
    out = []
    for name in names:
        cid = channel_id(pool, name, http)
        LAST.setdefault("channels", {})[name] = "chaîne introuvable" if not cid else 0
        if not cid:
            continue
        url = f"https://www.youtube.com/channel/{cid}/search?query={up.quote(query)}"
        items = youtube._ytdlp(["--flat-playlist", "--dump-json", "--no-warnings", "--playlist-end", str(per_channel), url], 90)
        vs = [youtube._from_ytdlp(d) for d in items if d.get("id") and len(str(d.get("id"))) == 11]
        for v in vs:
            v.channel = v.channel or name
            v.channel_id = cid
            v.found_by.add(f"chaîne:{name}")
        out += vs
        LAST["channels"][name] = len(vs)
        log.info("Chaîne YouTube « %s » : %d vidéo(s) pour « %s »", name, len(vs), query[:40])
    return out
