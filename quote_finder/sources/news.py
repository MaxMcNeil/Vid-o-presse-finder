"""Articles de presse / agences. GDELT et Google News sans clé ; Guardian et NYT avec clé gratuite (archives anciennes)."""
from __future__ import annotations

import email.utils
import logging
import time
import xml.etree.ElementTree as ET

import requests

from ..keys import KeyPool
from ..models import ArticleCandidate

log = logging.getLogger("qf.news")


def _get(http, url, **kw):
    try:
        r = http.get(url, timeout=40, **kw)
        if r.status_code == 200:
            return r
        log.warning("%s -> HTTP %s", url.split("?")[0], r.status_code)
    except requests.RequestException as e:
        log.warning("%s : %s", url.split("?")[0], e)
    return None


def gdelt(query: str, n: int = 50, http=None) -> list[ArticleCandidate]:
    """GDELT DOC 2.0 : ~3 derniers mois, énorme couverture mondiale, gratuit."""
    http = http or requests
    r = None
    for wait in (6, 20, 45):  # limite : 1 requête / 5 s ; GitHub partage des IP très sollicitées
        r = _get(http, "https://api.gdeltproject.org/api/v2/doc/doc", params={
            "query": query, "mode": "artlist", "format": "json", "maxrecords": n, "sort": "hybridrel"})
        if r:
            break
        time.sleep(wait)
    time.sleep(6)
    if not r:
        return []
    try:
        arts = r.json().get("articles", [])
    except ValueError:
        return []
    out = []
    for a in arts:
        sd = a.get("seendate", "")
        pub = f"{sd[:4]}-{sd[4:6]}-{sd[6:8]}" if len(sd) >= 8 else ""
        out.append(ArticleCandidate(a.get("domain", "gdelt"), a["url"], a.get("title", ""), pub))
    return out


def google_news(query: str, n: int = 30, lang: str = "en", http=None) -> list[ArticleCandidate]:
    http = http or requests
    country = {"en": "US", "fr": "FR", "es": "ES", "de": "DE", "it": "IT"}.get(lang, "US")
    r = _get(http, "https://news.google.com/rss/search", params={
        "q": query, "hl": f"{lang}-{country}", "gl": country, "ceid": f"{country}:{lang}"})
    if not r:
        return []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        pub = ""
        if it.findtext("pubDate"):
            try:
                pub = email.utils.parsedate_to_datetime(it.findtext("pubDate")).strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                pass
        src = it.findtext("source") or "Google News"
        out.append(ArticleCandidate(src, it.findtext("link") or "", it.findtext("title") or "", pub))
        if len(out) >= n:
            break
    return out


def guardian(pool: KeyPool, query: str, n: int = 30, http=None) -> list[ArticleCandidate]:
    """Archive complète depuis 1999, texte intégral inclus."""
    http = http or requests
    key = pool.acquire() if pool else None
    if not key:
        return []
    r = _get(http, "https://content.guardianapis.com/search", params={
        "q": query, "api-key": key, "page-size": n, "order-by": "relevance",
        "show-fields": "headline,bodyText"})
    if not r:
        return []
    out = []
    for a in r.json().get("response", {}).get("results", []):
        f = a.get("fields", {})
        out.append(ArticleCandidate("The Guardian", a["webUrl"], f.get("headline") or a.get("webTitle", ""),
                                    (a.get("webPublicationDate") or "")[:10], text=f.get("bodyText", "")))
    return out


def nyt(pool: KeyPool, query: str, n: int = 10, http=None) -> list[ArticleCandidate]:
    """Archive depuis 1851 ; l'API ne renvoie que résumé/chapô (vérification partielle)."""
    http = http or requests
    key = pool.acquire() if pool else None
    if not key:
        return []
    r = _get(http, "https://api.nytimes.com/svc/search/v2/articlesearch.json",
             params={"q": query, "api-key": key, "sort": "relevance"})
    if not r:
        return []
    out = []
    for d in r.json().get("response", {}).get("docs", [])[:n]:
        snip = " ".join(filter(None, [d.get("abstract"), d.get("lead_paragraph"), d.get("snippet")]))
        out.append(ArticleCandidate("The New York Times", d["web_url"], d.get("headline", {}).get("main", ""),
                                    (d.get("pub_date") or "")[:10], snippet=snip))
    return out
