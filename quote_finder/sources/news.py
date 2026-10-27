"""Articles de presse / agences. GDELT et Google News sans clé ; Guardian et NYT avec clé gratuite (archives anciennes)."""
from __future__ import annotations

import email.utils
import logging
import time
import xml.etree.ElementTree as ET

import requests

from ..authority import domain_of
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


_gdelt_fail = 0  # disjoncteur : GDELT bloque souvent les IP de GitHub (HTTP 429)


def gdelt(query: str, n: int = 50, http=None) -> list[ArticleCandidate]:
    """GDELT DOC 2.0 : ~3 derniers mois, énorme couverture mondiale, gratuit."""
    global _gdelt_fail
    if _gdelt_fail >= 2:
        return []
    http = http or requests
    r = None
    for wait in (6, 20):  # limite : 1 requête / 5 s ; GitHub partage des IP très sollicitées
        r = _get(http, "https://api.gdeltproject.org/api/v2/doc/doc", params={
            "query": query, "mode": "artlist", "format": "json", "maxrecords": n, "sort": "hybridrel"})
        if r:
            break
        time.sleep(wait)
    time.sleep(6)
    if not r:
        _gdelt_fail += 1
        if _gdelt_fail >= 2:
            log.warning("GDELT injoignable : abandonné pour cette exécution")
        return []
    _gdelt_fail = 0
    try:
        arts = (r.json() or {}).get("articles") or []
    except ValueError:
        return []
    out = []
    for a in arts:
        sd = a.get("seendate", "")
        pub = f"{sd[:4]}-{sd[4:6]}-{sd[6:8]}" if len(sd) >= 8 else ""
        out.append(ArticleCandidate(a.get("domain", "gdelt"), a["url"], a.get("title", ""), pub, domain=domain_of(a["url"])))
    return out


def google_news(query: str, n: int = 30, lang: str = "en", http=None) -> list[ArticleCandidate]:
    http = http or requests
    country = {"en": "US", "fr": "FR", "es": "ES", "de": "DE", "it": "IT", "ar": "AE"}.get(lang, "US")
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
        src_el = it.find("source")
        src = (src_el.text if src_el is not None else "") or "Google News"
        dom = domain_of(src_el.get("url", "")) if src_el is not None else ""
        title = it.findtext("title") or ""
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        out.append(ArticleCandidate(src, it.findtext("link") or "", title, pub, domain=dom))
        if len(out) >= n:
            break
    return out


def bing_news(query: str, n: int = 20, lang: str = "en", http=None) -> list[ArticleCandidate]:
    """Bing Actualités en RSS (sans clé) : liens directs vers les articles, plus fiable que Google News."""
    import urllib.parse as up
    http = http or requests
    mkt = {"en": "en-US", "fr": "fr-FR", "es": "es-ES", "de": "de-DE", "it": "it-IT", "ar": "ar-XA"}.get(lang, "en-US")
    r = _get(http, "https://www.bing.com/news/search", params={"q": query, "format": "rss", "mkt": mkt, "setlang": lang},
             headers={"User-Agent": "Mozilla/5.0 (compatible; quote-finder)"})
    if not r:
        return []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        link = it.findtext("link") or ""
        real = up.parse_qs(up.urlparse(link).query).get("url", [link])[0]
        pub = ""
        if it.findtext("pubDate"):
            try:
                pub = email.utils.parsedate_to_datetime(it.findtext("pubDate")).strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                pass
        src = ""
        for ch in it:
            if ch.tag.endswith("Source"):
                src = ch.text or ""
        out.append(ArticleCandidate(src or up.urlparse(real).netloc, real, it.findtext("title") or "", pub,
                                    snippet=it.findtext("description") or "", domain=domain_of(real)))
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
    for a in ((r.json() or {}).get("response") or {}).get("results") or []:
        f = a.get("fields") or {}
        out.append(ArticleCandidate("The Guardian", a["webUrl"], f.get("headline") or a.get("webTitle", ""),
                                    (a.get("webPublicationDate") or "")[:10], text=f.get("bodyText", ""),
                                    domain="theguardian.com"))
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
    for d in (((r.json() or {}).get("response") or {}).get("docs") or [])[:n]:
        snip = " ".join(filter(None, [d.get("abstract"), d.get("lead_paragraph"), d.get("snippet")]))
        out.append(ArticleCandidate("The New York Times", d["web_url"], (d.get("headline") or {}).get("main", ""),
                                    (d.get("pub_date") or "")[:10], snippet=snip, domain="nytimes.com"))
    return out


# ------------------------------------------------------------------ recherche Google via Gemini (vrais liens)
GSEARCH_PROMPT = """Fais une recherche Google d'ARTICLES DE PRESSE et de pages officielles sur ce sujet :
« {query} »
Langue des résultats : {lang}. Privilégie agences de presse, grands médias, institutions et sites officiels.
Donne jusqu'à {n} articles réellement trouvés (aucun inventé), du plus pertinent au moins pertinent.
Réponds en JSON : {{"articles": [{{"title": "titre exact", "source": "nom du média", "url": "adresse complète", "date": "AAAA-MM-JJ ou vide", "summary": "1 phrase : ce que dit l'article"}}]}}"""


def _resolve(u: str) -> str:
    from ..article_text import _follow
    return _follow(u) if u else ""


def gemini_search(llm, query: str, n: int = 8, lang: str = "fr") -> list[ArticleCandidate]:
    """« Une simple recherche Google » : Gemini cherche avec l'outil google_search. Les adresses retenues sont celles
    renvoyées par Google lui-même (métadonnées de recherche, redirections résolues) ; une adresse écrite par l'IA
    n'est gardée que si Google l'a aussi renvoyée (même site), jamais inventée."""
    if llm is None or not getattr(llm, "search_web", None):
        return []
    d = llm.search_web(GSEARCH_PROMPT.format(query=query, lang=lang, n=n)) or {}
    from ..util import items_of, parse_json_loose
    g = d.get("grounding") or {}
    chunks = g.get("groundingChunks") or g.get("grounding_chunks") or []
    real = []  # (url, domaine) renvoyés par Google
    for c in chunks:
        w = c.get("web") or {}
        u = _resolve(w.get("uri", ""))
        if u and "google." not in domain_of(u):
            real.append((u, domain_of(u), w.get("title", "")))
    try:
        items = items_of(parse_json_loose(d.get("text", "") or "{}"), "articles")
    except Exception:  # noqa: BLE001
        items = []
    out, used = [], set()
    for it in items:
        if not isinstance(it, dict):
            continue
        u, title = str(it.get("url") or ""), str(it.get("title") or "")
        dom = domain_of(u) if u else ""
        same = [r for r in real if r[0] not in used and (r[0].rstrip("/") == u.rstrip("/") or
                                                         (dom and (r[1] == dom or r[1].endswith("." + dom))))]
        if not same:
            continue  # adresse que Google n'a pas renvoyée : jamais gardée
        exact = next((r for r in same if r[0].rstrip("/") == u.rstrip("/")), same[0])
        used.add(exact[0])
        out.append(ArticleCandidate(str(it.get("source") or exact[1]), exact[0], title or exact[2],
                                    str(it.get("date") or "")[:10], snippet=str(it.get("summary") or ""), domain=exact[1]))
    for u, dom, t in real:  # résultats Google sans fiche : gardés avec le titre tiré de l'adresse
        if u not in used and len(out) < n:
            slug = u.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ")
            out.append(ArticleCandidate(t or dom, u, slug[:150], "", domain=dom))
    return out[:n]
