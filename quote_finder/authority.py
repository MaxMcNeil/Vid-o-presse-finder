"""Autorité des sources : agences, médias de référence, chaînes officielles, institutions.

Score de 0 à 1. Sert à ne garder que peu de vidéos/articles, mais solides.
- 1.0  institution officielle (gouvernement, parlement, ONU, UE...) ou agence de presse
- 0.85 grand média de référence / service public
- 0.7  média national reconnu
- sinon : abonnés de la chaîne (YouTube), avec pénalités pour #shorts et titres racoleurs
"""
from __future__ import annotations

import math
import re
import urllib.parse as up

from .util import normalize

# ---------------------------------------------------------------- domaines (presse)
OFFICIAL_SUFFIXES = (".gouv.fr", ".gov", ".gov.uk", ".admin.ch", "admin.ch", ".europa.eu", "europa.eu", ".int",
                     ".gc.ca", ".gov.au", ".bund.de", ".gob.es", ".governo.it", "senat.fr", "assemblee-nationale.fr",
                     "elysee.fr", "parlament.ch", "un.org", "who.int", "nato.int", "imf.org", "worldbank.org", "oecd.org",
                     ".gov.sa", ".gov.ae", ".gov.qa", ".gov.kw", ".gov.bh", ".gov.om", ".gov.eg", ".gov.it", "belgium.be",
                     "europarl.europa.eu", "consilium.europa.eu")

DOMAINS: dict[str, float] = {}
_TIERS = {
    1.0: "reuters.com apnews.com afp.com ap.org keystone-sda.ch ats.ch dpa.com ansa.it efe.com",
    0.9: ("bbc.com bbc.co.uk nytimes.com washingtonpost.com theguardian.com ft.com wsj.com economist.com lemonde.fr "
          "francetvinfo.fr franceinfo.fr radiofrance.fr rfi.fr france24.com arte.tv rts.ch srf.ch swissinfo.ch letemps.ch "
          "nzz.ch dw.com spiegel.de zeit.de faz.net sueddeutsche.de elpais.com corriere.it repubblica.it npr.org pbs.org "
          "bloomberg.com politico.eu politico.com euronews.com aljazeera.com nhk.or.jp cbc.ca abc.net.au lesechos.fr "
          "tagesschau.de orf.at rtbf.be lesoir.be ledevoir.com radio-canada.ca nature.com science.org"),
    0.75: ("lefigaro.fr liberation.fr lexpress.fr lepoint.fr lobs.fr nouvelobs.com mediapart.fr la-croix.com "
           "humanite.fr challenges.fr latribune.fr courrierinternational.com huffingtonpost.fr bfmtv.com tf1info.fr lci.fr "
           "rtl.fr europe1.fr 20minutes.fr ouest-france.fr sudouest.fr leparisien.fr publicsenat.fr lcp.fr tdg.ch 24heures.ch "
           "watson.ch blick.ch tagesanzeiger.ch heidi.news rtl.be lalibre.be cnn.com nbcnews.com cbsnews.com abcnews.go.com "
           "usatoday.com latimes.com axios.com time.com newsweek.com theatlantic.com newyorker.com cnbc.com "
           "independent.co.uk telegraph.co.uk thetimes.co.uk skynews.com news.sky.com handelsblatt.com welt.de "
           "elmundo.es lavanguardia.com lastampa.it ilsole24ore.com heise.de golem.de arstechnica.com wired.com "
           "theverge.com techcrunch.com zdnet.com zdnet.fr lemondeinformatique.fr 01net.com numerama.com nextinpact.com "
           "next.ink theregister.com"),
}
for _score, _ds in _TIERS.items():
    for _d in _ds.split():
        DOMAINS.setdefault(_d, _score)

# ---------------------------------------------------------------- chaînes vidéo
CHANNELS: dict[str, float] = {}
_CH = {
    1.0: ("reuters|associated press|afp|afp news agency|afp francais|c span|the white house|elysee|assemblee nationale|"
          "senat|public senat|lcp assemblee nationale|european parliament|european commission|parlement europeen|"
          "united nations|nations unies|nato|conseil federal|der bundesrat|le conseil federal|admin ch|bundesregierung|"
          "gouvernement|10 downing street|uk parliament|cspan"),
    0.9: ("bbc news|bbc|franceinfo|france info|france 24|france 24 english|arte|le monde|rts info|rts|srf news|srf|"
          "swissinfo|swi swissinfo ch|dw news|dw|euronews|euronews english|pbs newshour|npr|cbc news|nhk world japan|"
          "al jazeera english|bloomberg television|bloomberg|financial times|the guardian|the new york times|"
          "the washington post|wall street journal|the economist|rfi|france inter|radio france|tagesschau|zdf|ard|"
          "rtbf|radio canada info|abc news australia|sky news|cnbc|cnn|nbc news|cbs news|abc news|c a vous france "
          "televisions|france televisions|tv5monde|tv5monde info"),
    0.75: ("bfmtv|lci|rtl|europe 1|tf1info|tf1|france 2|m6 info|le figaro|liberation|mediapart|le point|l express|"
           "le huffpost|lehuffpost|brut|konbini news|cnews|blick|watson|letemps|20 minutes|ouest france|le parisien|"
           "rmc|bfm business|les echos|l obs|courrier international|heise|the verge|wired|ars technica|"
           "msnbc|fox news|politico|time|vice news|channel 4 news|itv news|the telegraph|the independent"),
}
for _score, _cs in _CH.items():
    for _c in _cs.split("|"):
        CHANNELS.setdefault(normalize(_c), _score)

def _load_registry():
    """L'annuaire par pays (registry.py) complète les tables : un média ajouté là-bas est reconnu partout."""
    from . import registry
    for d, src in registry.all_domains().items():
        DOMAINS.setdefault(d, 1.0 if src.official else 0.85)
        for ch in src.youtube:
            CHANNELS.setdefault(normalize(ch), 1.0 if src.official else 0.9)


_load_registry()
for _c in ("aljazeera arabic", "al jazeera arabic", "الجزيرة", "france 24 arabic", "bbc news عربي", "sky news arabia",
           "سكاي نيوز عربية", "al arabiya", "الشرق للأخبار", "asharq news", "dw عربية", "dw arabic", "rt arabic"):
    CHANNELS.setdefault(normalize(_c), 0.85 if _c in ("rt arabic",) else 0.9)

LEVELS = {"forte": 0.6, "moyenne": 0.4, "toutes": 0.0}


def domain_of(url: str) -> str:
    try:
        h = up.urlparse(url).netloc.lower()
    except ValueError:
        return ""
    h = h.split("@")[-1].split(":")[0]
    return h[4:] if h.startswith("www.") else h


def _match_domain(dom: str, table: dict[str, float]) -> float | None:
    parts = dom.split(".")
    for i in range(len(parts) - 1):  # sous-domaines : info.lemonde.fr -> lemonde.fr
        cand = ".".join(parts[i:])
        if cand in table:
            return table[cand]
    return None


def _plan_sources(plan) -> tuple[set[str], set[str]]:
    """Sources de référence proposées par l'IA pour CETTE recherche (ex. RTS, admin.ch pour la Suisse)."""
    doms, names = set(), set()
    for s in getattr(plan, "authoritative_sources", None) or []:
        s = str(s).strip().lower()
        if not s:
            continue
        if "." in s and " " not in s:
            doms.add(domain_of(s if "//" in s else "https://" + s))
        else:
            names.add(normalize(s))
    return doms, names


def score_domain(dom: str, plan=None) -> tuple[float, str]:
    dom = (dom or "").lower()
    if not dom:
        return 0.3, "source inconnue"
    if any(dom == s.lstrip(".") or dom.endswith(s if s.startswith(".") else "." + s) for s in OFFICIAL_SUFFIXES):
        return 1.0, "source officielle"
    s = _match_domain(dom, DOMAINS)
    if s is not None:
        return s, {1.0: "agence de presse", 0.9: "média de référence", 0.75: "média national"}.get(s, "média reconnu")
    doms, _ = _plan_sources(plan) if plan else (set(), set())
    if any(dom == d or dom.endswith("." + d) for d in doms if d):
        return 0.8, "source de référence pour ce sujet"
    return 0.3, "média non répertorié"


def score_article(a, plan=None) -> tuple[float, str]:
    dom = a.domain or domain_of(a.url)
    if "news.google.com" in dom:
        dom = ""
    if not dom and a.source:  # Google News sans domaine : on tente le nom du média
        s = CHANNELS.get(normalize(a.source))
        if s is not None:
            return s, "média reconnu"
    return score_domain(dom, plan)


CLICKBAIT = re.compile(r"(!{2,}|\?!|😱|🔥|🚨|choquant|shocking|you won.t believe|incroyable|exposed|destroyed|"
                       r"humiliates|just lost|completely|bans\b|#shorts|#viral|#fyp)", re.I)


def _caps_words(title: str) -> int:
    return sum(1 for w in re.findall(r"[A-ZÀ-Ý]{3,}", title or "") if w not in ("USA", "ONU", "OTAN", "NATO", "UE", "EU", "FBI", "CIA", "BBC", "CNN", "RTS", "SRF", "AFP", "TV"))


def score_video(v, plan=None) -> tuple[float, str]:
    if v.platform == "web":  # vidéo prise sur le site du média : son domaine fait foi
        s, why = score_domain(domain_of(v.url), plan)
        return s, why + " (vidéo du site)"
    name = normalize(v.channel)
    base, why = None, ""
    if name:
        base = CHANNELS.get(name)
        if base is None:  # "franceinfo" dans "franceinfo tv", "BBC News Afrique", "فرانس 24 / FRANCE 24 Arabic"...
            for k, s in sorted(CHANNELS.items(), key=lambda x: -len(x[0])):
                if (len(k) >= 4 and (name.startswith(k + " ") or name == k)) or \
                        (len(k) >= 7 and f" {k} " in f" {name} "):
                    base = s
                    break
        if base is not None:
            why = {1.0: "chaîne officielle / agence", 0.9: "média de référence", 0.75: "média national"}.get(base, "média reconnu")
        else:
            _, names = _plan_sources(plan) if plan else (set(), set())
            if any(n and (n == name or (len(n) >= 4 and n in name)) for n in names):
                base, why = 0.8, "source de référence pour ce sujet"
    if base is None:
        if v.channel_subs:
            base = min(0.7, max(0.1, 0.12 * math.log10(max(10, v.channel_subs)) - 0.05))  # 1 M ≈ 0.67 ; 100 k ≈ 0.55 ; 10 k ≈ 0.43
            why = f"chaîne non répertoriée ({v.channel_subs:,} abonnés)".replace(",", " ")
        else:
            base, why = 0.3, "chaîne non répertoriée"
    pen = []
    if (v.duration is not None and v.duration < 75) or "#short" in (v.title or "").lower():
        base -= 0.3
        pen.append("format court")
    if CLICKBAIT.search(v.title or "") or _caps_words(v.title) >= 3:
        base -= 0.2
        pen.append("titre racoleur")
    s = round(max(0.0, min(1.0, base)), 2)
    return s, why + (" — " + ", ".join(pen) if pen else "")


def stars(score: float) -> str:
    return "★★★" if score >= 0.85 else "★★☆" if score >= 0.6 else "★☆☆" if score >= 0.35 else "☆☆☆"
