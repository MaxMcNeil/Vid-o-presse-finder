"""Autres plateformes gratuites sans clé : Dailymotion et Internet Archive (dont archives TV)."""
from __future__ import annotations

import datetime as dt
import logging

import requests

from ..models import VideoCandidate

log = logging.getLogger("qf.video")


def dailymotion(query: str, n: int = 15, http=None) -> list[VideoCandidate]:
    http = http or requests
    try:
        r = http.get("https://api.dailymotion.com/videos", timeout=30, params={
            "search": query, "limit": n, "sort": "relevance",
            "fields": "id,title,description,created_time,duration,url,owner.screenname"})
        r.raise_for_status()
        items = r.json().get("list", [])
    except (requests.RequestException, ValueError) as e:
        log.warning("Dailymotion: %s", e)
        return []
    out = []
    for it in items:
        pub = dt.datetime.fromtimestamp(it["created_time"], dt.timezone.utc).strftime("%Y-%m-%d") if it.get("created_time") else ""
        out.append(VideoCandidate("dailymotion", it["id"], it.get("url") or f"https://www.dailymotion.com/video/{it['id']}",
                                  it.get("title", ""), it.get("description") or "", it.get("owner.screenname", ""),
                                  pub, it.get("duration")))
    return out


def archive_org(query: str, n: int = 15, http=None) -> list[VideoCandidate]:
    http = http or requests
    q = f"({query}) AND mediatype:(movies)"
    try:
        r = http.get("https://archive.org/advancedsearch.php", timeout=40, params={
            "q": q, "fl[]": ["identifier", "title", "date", "description", "creator"], "rows": n,
            "output": "json", "sort[]": "downloads desc"})
        r.raise_for_status()
        docs = r.json()["response"]["docs"]
    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning("Internet Archive: %s", e)
        return []
    out = []
    for d in docs:
        desc = d.get("description", "")
        if isinstance(desc, list):
            desc = " ".join(desc)
        out.append(VideoCandidate("archive", d["identifier"], f"https://archive.org/details/{d['identifier']}",
                                  str(d.get("title", "")), str(desc)[:1000], str(d.get("creator", "")),
                                  str(d.get("date", ""))[:10]))
    return out
