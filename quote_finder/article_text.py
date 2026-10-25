"""Lecture du texte d'un article : plusieurs méthodes gratuites en cascade, de la moins coûteuse à la plus coûteuse.

Testé depuis GitHub le 25/10/2026 sur 10 articles illisibles :
  - les liens Google Actualités ne se convertissent plus en vrai lien (googlenewsdecoder : 0/7) ;
  - lecture directe : OK pour beaucoup de sites (ex. copies MSN de Reuters), refusée par reuters.com / nytimes.com ;
  - archives d'Internet (archive.org) : OK quand l'article y est (ex. NYT) ;
  - recherche du titre sur Bing / DuckDuckGo : inutilisable depuis GitHub (résultats sans rapport / blocage) ;
  - Gemini qui ouvre lui-même la page (outil url_context, chez Google) : OK.
Ajouté le 25/10/2026 : un lien Google Actualités est retrouvé par Gemini (outil google_search, même média exigé),
et tout texte lu est vérifié contre le titre (jamais le texte d'un autre article).
Chaque article garde la méthode qui a marché (« via ») : si une méthode cesse de fonctionner, ça se voit dans ETAT.md.
"""
from __future__ import annotations

import logging
import re
import threading

import requests

from .authority import domain_of

log = logging.getLogger("qf.articles")
UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Mobile Safari/537.36",
      "Accept-Language": "fr,en;q=0.8,de;q=0.6,it;q=0.6,ar;q=0.5"}
MIN_LEN = 400
_lock = threading.Lock()


def _extract(html: str) -> str:
    try:
        import trafilatura
        return trafilatura.extract(html) or ""
    except ImportError:
        from .pipeline import _P
        p = _P()
        p.feed(html)
        return "\n".join(p.paras)


def direct(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=25, allow_redirects=True)
    if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
        return ""
    return _extract(r.text)


def wayback(url: str) -> str:
    r = requests.get("https://archive.org/wayback/available", params={"url": url}, timeout=25)
    try:
        snap = ((r.json().get("archived_snapshots") or {}).get("closest") or {}).get("url")
    except ValueError:
        return ""
    if not snap:
        return ""
    h = requests.get(snap, headers=UA, timeout=40)
    return _extract(h.text) if h.status_code == 200 else ""


GEMINI_PROMPT = """Ouvre cette page : {url}
C'est un article de presse ({source}, titre : « {title} »).
Recopie MOT À MOT le texte de l'article (paragraphes du corps de l'article, sans menus, publicités ni liens),
jusqu'à 6000 caractères, en gardant en priorité les passages qui parlent de : {subject}.
Ne résume pas, ne traduis pas, n'ajoute rien. Un paragraphe par ligne.
Si la page est inaccessible ou n'est pas cet article, réponds exactement : INACCESSIBLE"""


def gemini(a, llm, subject: str) -> tuple[str, str]:
    d = llm.read_page(GEMINI_PROMPT.format(url=a.url, source=a.source, title=a.title, subject=subject))
    text = (d or {}).get("text", "") or ""
    meta = (d or {}).get("meta") or {}
    urls = meta.get("urlMetadata") or meta.get("url_metadata") or []
    ok = any("SUCCESS" in str(u.get("urlRetrievalStatus") or u.get("url_retrieval_status") or "") for u in urls)
    real = next((u.get("retrievedUrl") or u.get("retrieved_url") for u in urls if (u.get("retrievedUrl") or u.get("retrieved_url"))), "")
    text = re.sub(r"^(voici|here is)[^\n]*\n", "", text.strip(), flags=re.I)  # formule d'introduction éventuelle
    if "INACCESSIBLE" in text[:40] or (urls and not ok):
        return "", ""
    return text, real


SEARCH_PROMPT = """Trouve sur le web cet article de presse et donne son adresse exacte.
Titre : « {title} »
Média : {source}{dom}
Date : {date}
Réponds uniquement par l'adresse (URL) complète de l'article sur le site du média, sans aucun autre texte.
Si tu ne le trouves pas avec certitude, réponds exactement : INTROUVABLE"""


def _follow(u: str) -> str:
    """Lien de redirection (ex. vertexaisearch… de Google) -> adresse finale, sans télécharger la page."""
    if "vertexaisearch" not in u and "grounding-api-redirect" not in u:
        return u
    try:
        r = requests.head(u, allow_redirects=False, timeout=15, headers=UA)
        loc = r.headers.get("location") or ""
        if not loc:
            r = requests.get(u, allow_redirects=True, timeout=20, headers=UA, stream=True)
            loc = r.url
            r.close()
        return loc
    except requests.RequestException:
        return ""


def _same_site(dom: str, a) -> bool:
    if not dom or "google." in dom or "vertexaisearch" in dom:
        return False
    if a.domain:
        return dom == a.domain or dom.endswith("." + a.domain) or a.domain.endswith("." + dom)
    src = re.sub(r"[^a-z0-9]", "", (a.source or "").lower())
    return bool(src) and len(src) >= 3 and src[:8] in dom.replace(".", "").replace("-", "")


def find_real_url(a, llm) -> str:
    """Lien Google Actualités -> vrai lien de l'article : Gemini le cherche sur le web (outil google_search).
    N'accepte qu'une adresse du MÊME média ; le texte lu ensuite est vérifié contre le titre (title_matches)."""
    d = llm.search_web(SEARCH_PROMPT.format(title=a.title, source=a.source, dom=f" ({a.domain})" if a.domain else "",
                                            date=a.published or "inconnue"))
    text = (d or {}).get("text", "") or ""
    chunks = ((d or {}).get("grounding") or {}).get("groundingChunks") or \
        ((d or {}).get("grounding") or {}).get("grounding_chunks") or []
    cands = [] if "INTROUVABLE" in text[:40] else re.findall(r"https?://[^\s)>\]\"'<]+", text)
    cands += [(c.get("web") or {}).get("uri", "") for c in chunks]
    for u in cands:
        real = _follow(u.rstrip(".,;"))
        dom = domain_of(real) if real else ""
        if real and "news.google.com" not in real and _same_site(dom, a) and len(real.split(dom, 1)[-1]) > 8:
            return real
    return ""


def title_matches(title: str, text: str) -> bool:
    """Le texte lu est-il bien celui de l'article ? (au moins la moitié des mots marquants du titre s'y trouvent)"""
    from .util import normalize
    words = [w for w in normalize(title).split() if len(w) >= 5][:12]
    if len(words) < 3:
        return True
    body = normalize(text[:20000])
    return sum(1 for w in words if w in body) >= max(2, len(words) // 2)


def read(a, llm=None, subject: str = "", gemini_budget: list[int] | None = None,
         search_budget: list[int] | None = None) -> tuple[str, str]:
    """Texte de l'article et méthode qui a marché (« direct », « archive.org », « gemini »), ou ("", raison)."""
    if a.text:
        return a.text, "api"
    url = a.url
    google = "news.google.com" in url
    found_by_search = False
    if google:  # ancien décodeur : gardé car gratuit, au cas où il refonctionne
        try:
            from .pipeline import resolve_google_news
            url = resolve_google_news(a) or url
            google = "news.google.com" in url
        except Exception:  # noqa: BLE001
            pass
    if google and llm is not None and getattr(llm, "search_web", None) and search_budget is not None:
        with _lock:
            take = search_budget[0] > 0
            if take:
                search_budget[0] -= 1
        if take:
            try:
                real = find_real_url(a, llm)
                if real:
                    log.info("lien Google Actualités retrouvé : %s", real[:90])
                    a.url, a.domain, url, google, found_by_search = real, domain_of(real) or a.domain, real, False, True
            except Exception as e:  # noqa: BLE001
                log.info("recherche du vrai lien : %s", str(e)[:100])
    if not google:
        for name, fn in (("direct", direct), ("archive.org", wayback)):
            try:
                t = fn(url)
                if len(t) >= MIN_LEN and (not found_by_search or title_matches(a.title, t)):
                    return t, name + (" (lien retrouvé)" if found_by_search else "")
            except Exception as e:  # noqa: BLE001
                log.info("%s %s : %s", name, url[:60], type(e).__name__)
    if llm is not None and getattr(llm, "read_page", None) and gemini_budget is not None:
        with _lock:
            take = gemini_budget[0] > 0
            if take:
                gemini_budget[0] -= 1
        if take:
            try:
                t, real = gemini(a, llm, subject)
                if len(t) >= MIN_LEN // 2 and title_matches(a.title, t):
                    if real and "news.google.com" not in real:
                        a.url, a.domain = real, domain_of(real) or a.domain
                    return t, "gemini"
            except Exception as e:  # noqa: BLE001
                log.info("gemini %s : %s", a.url[:60], str(e)[:100])
    return "", ("lien Google Actualités non converti" if google else "site fermé aux robots, pas d'archive")
