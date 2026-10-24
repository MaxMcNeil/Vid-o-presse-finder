"""Recherche YouTube : API Data v3 (clés en rotation) puis repli yt-dlp (sans quota)."""
from __future__ import annotations

import json
import logging
import subprocess
import sys

import requests

from ..keys import KeyPool
from ..models import VideoCandidate
from ..util import parse_iso_duration

log = logging.getLogger("qf.youtube")
API = "https://www.googleapis.com/youtube/v3"


def _call(pool: KeyPool, endpoint: str, params: dict, http) -> dict | None:
    for _ in range(max(3, len(pool._next_ok) + 1)):
        key = pool.acquire(wait=False) if pool else None
        if key is None:
            return None
        try:
            r = http.get(f"{API}/{endpoint}", params={**params, "key": key}, timeout=30)
        except requests.RequestException as e:
            log.warning("YouTube API réseau: %s", e)
            continue
        if r.status_code == 200:
            return r.json()
        body = r.text[:300]
        if r.status_code in (403, 429) and ("quota" in body.lower() or "rateLimit" in body):
            log.warning("YouTube API : quota atteint pour une clé, rotation")
            pool.exhaust(key)
            continue
        if r.status_code in (400, 401, 403):
            log.warning("YouTube API %s : %s", r.status_code, body)
            pool.exhaust(key)
            continue
    return None


def _details_api(pool, ids: list[str], http) -> dict[str, dict]:
    out = {}
    for i in range(0, len(ids), 50):
        d = _call(pool, "videos", {"part": "snippet,contentDetails", "id": ",".join(ids[i:i + 50])}, http)
        for it in (d or {}).get("items", []):
            sn = it["snippet"]
            out[it["id"]] = {
                "title": sn.get("title", ""), "description": sn.get("description", ""),
                "channel": sn.get("channelTitle", ""), "published": sn.get("publishedAt", ""),
                "duration": parse_iso_duration(it.get("contentDetails", {}).get("duration", "")),
            }
    return out


def search_api(pool: KeyPool, query: str, n: int = 25, lang: str | None = None, http=None) -> list[VideoCandidate] | None:
    """None = API indisponible (quota/aucune clé) → l'appelant bascule sur yt-dlp."""
    http = http or requests
    params = {"part": "id", "type": "video", "q": query, "maxResults": min(n, 50), "videoCaption": "any",
              "order": "relevance", "safeSearch": "none"}
    if lang:
        params["relevanceLanguage"] = lang
    d = _call(pool, "search", params, http)
    if d is None:
        return None
    ids = [it["id"]["videoId"] for it in d.get("items", []) if it.get("id", {}).get("videoId")]
    det = _details_api(pool, ids, http) if ids else {}
    res = []
    for vid in ids:
        x = det.get(vid, {})
        res.append(VideoCandidate("youtube", vid, f"https://www.youtube.com/watch?v={vid}",
                                  x.get("title", ""), x.get("description", ""), x.get("channel", ""),
                                  x.get("published", ""), x.get("duration")))
    return res


def _ytdlp(args: list[str], timeout: int = 120) -> list[dict]:
    try:
        p = subprocess.run([sys.executable, "-m", "yt_dlp", *args], capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("yt-dlp a échoué: %s", e)
        return []
    out = []
    for line in p.stdout.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    if not out and p.stderr:
        log.warning("yt-dlp: %s", p.stderr.strip().splitlines()[-1][:200])
    return out


def _from_ytdlp(d: dict) -> VideoCandidate:
    up = d.get("upload_date") or ""
    pub = f"{up[:4]}-{up[4:6]}-{up[6:8]}" if len(up) == 8 else ""
    vid = d.get("id", "")
    return VideoCandidate("youtube", vid, d.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
                          d.get("title", ""), d.get("description", "") or "", d.get("channel") or d.get("uploader") or "",
                          pub, int(d["duration"]) if d.get("duration") else None)


def search_ytdlp(query: str, n: int = 20) -> list[VideoCandidate]:
    return [_from_ytdlp(d) for d in _ytdlp(["--flat-playlist", "--dump-json", "--no-warnings", f"ytsearch{n}:{query}"])
            if d.get("id")]


def details_ytdlp(video_id: str) -> dict:
    r = _ytdlp(["--skip-download", "--dump-json", "--no-warnings", f"https://www.youtube.com/watch?v={video_id}"], 60)
    return _from_ytdlp(r[0]).__dict__ if r else {}


def search(pool: KeyPool, query: str, n: int = 25, lang: str | None = None, http=None) -> list[VideoCandidate]:
    res = search_api(pool, query, n, lang, http) if pool else None
    if res is None:
        res = search_ytdlp(query, min(n, 20))
        log.info("YouTube « %s » : API indisponible (clés absentes ou quota atteint) → yt-dlp : %d résultat(s)", query[:50], len(res))
    else:
        log.info("YouTube « %s » : API : %d résultat(s)", query[:50], len(res))
    return res
