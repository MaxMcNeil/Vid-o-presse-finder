"""Annuaire des sources de référence par pays : agences, médias publics, grands journaux, institutions.

Sert à chercher DIRECTEMENT chez eux (articles + vidéos de leurs sites, souvent sous-titrées) au lieu d'espérer
qu'ils sortent dans les résultats YouTube. L'IA ajoute à chaque recherche les sources propres au sujet
(plan.authoritative_sources) ; cet annuaire garantit un socle fiable même si l'IA en oublie.

Chaque entrée : domaine, nom, vidéo (le site publie des vidéos que yt-dlp sait lire), noms de chaînes YouTube.
Pour ajouter un pays ou un média : une ligne dans SOURCES, rien d'autre à changer.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Source:
    domain: str
    name: str
    video: bool = False                 # vidéos sur le site, lisibles par yt-dlp
    youtube: tuple = field(default=())  # noms de chaînes YouTube officielles
    official: bool = False              # institution (gouvernement, parlement...)


S = Source
SOURCES: dict[str, list[Source]] = {
    "INT": [S("reuters.com", "Reuters", True, ("Reuters",)), S("apnews.com", "Associated Press", True, ("Associated Press",)),
            S("afp.com", "AFP", False, ("AFP News Agency", "AFP")), S("france24.com", "France 24", True, ("FRANCE 24", "FRANCE 24 English")),
            S("dw.com", "DW", True, ("DW News",)), S("euronews.com", "Euronews", True, ("euronews",)),
            S("bbc.com", "BBC", True, ("BBC News",)), S("aljazeera.com", "Al Jazeera", True, ("Al Jazeera English",)),
            S("un.org", "Nations unies", True, ("United Nations",), True), S("europarl.europa.eu", "Parlement européen", True,
            ("European Parliament",), True), S("ec.europa.eu", "Commission européenne", True, ("European Commission",), True)],
    "CH": [S("rts.ch", "RTS", True, ("RTS - Radio Télévision Suisse", "RTS Info")), S("srf.ch", "SRF", True, ("SRF News",)),
           S("rsi.ch", "RSI", True, ("RSI",)), S("swissinfo.ch", "SWI swissinfo.ch", True, ("SWI swissinfo.ch", "SWI swissinfo.ch - English")),
           S("letemps.ch", "Le Temps", False, ("Le Temps",)), S("nzz.ch", "NZZ", True, ("NZZ",)), S("tdg.ch", "Tribune de Genève"),
           S("24heures.ch", "24 heures"), S("heidi.news", "Heidi.news"), S("keystone-sda.ch", "Keystone-ATS"),
           S("admin.ch", "Confédération suisse", True, ("Der Bundesrat - Le Conseil fédéral",), True),
           S("parlament.ch", "Parlement suisse", True, (), True)],
    "FR": [S("francetvinfo.fr", "franceinfo", True, ("franceinfo",)), S("radiofrance.fr", "Radio France", True, ("France Inter",)),
           S("lemonde.fr", "Le Monde", True, ("Le Monde",)), S("lefigaro.fr", "Le Figaro", True, ("Le Figaro",)),
           S("liberation.fr", "Libération"), S("lesechos.fr", "Les Échos"), S("publicsenat.fr", "Public Sénat", True, ("Public Sénat",)),
           S("lcp.fr", "LCP", True, ("LCP - Assemblée nationale",)), S("tf1info.fr", "TF1 Info", True, ("TF1 INFO",)),
           S("bfmtv.com", "BFMTV", True, ("BFMTV",)), S("arte.tv", "Arte", True, ("ARTE",)), S("rfi.fr", "RFI", True, ("RFI",)),
           S("elysee.fr", "Élysée", True, ("Élysée",), True), S("gouvernement.fr", "Gouvernement", True, ("Gouvernement",), True),
           S("vie-publique.fr", "Vie publique", False, (), True), S("assemblee-nationale.fr", "Assemblée nationale", True, (), True),
           S("senat.fr", "Sénat", True, (), True)],
    "BE": [S("rtbf.be", "RTBF", True, ("RTBF",)), S("lesoir.be", "Le Soir"), S("lalibre.be", "La Libre"), S("vrt.be", "VRT", True)],
    "CA": [S("radio-canada.ca", "Radio-Canada", True, ("Radio-Canada Info",)), S("cbc.ca", "CBC", True, ("CBC News",)),
           S("ledevoir.com", "Le Devoir"), S("lapresse.ca", "La Presse")],
    "US": [S("nytimes.com", "The New York Times", True, ("The New York Times",)), S("washingtonpost.com", "The Washington Post", True),
           S("npr.org", "NPR", True, ("NPR",)), S("pbs.org", "PBS NewsHour", True, ("PBS NewsHour",)), S("cnn.com", "CNN", True, ("CNN",)),
           S("nbcnews.com", "NBC News", True, ("NBC News",)), S("cbsnews.com", "CBS News", True, ("CBS News",)),
           S("abcnews.go.com", "ABC News", True, ("ABC News",)), S("c-span.org", "C-SPAN", True, ("C-SPAN",)),
           S("whitehouse.gov", "Maison-Blanche", True, ("The White House",), True), S("wsj.com", "Wall Street Journal", True),
           S("bloomberg.com", "Bloomberg", True, ("Bloomberg Television",)), S("politico.com", "Politico")],
    "GB": [S("bbc.co.uk", "BBC", True, ("BBC News",)), S("theguardian.com", "The Guardian", True, ("The Guardian",)),
           S("ft.com", "Financial Times", True, ("Financial Times",)), S("news.sky.com", "Sky News", True, ("Sky News",)),
           S("itv.com", "ITV News", True, ("ITV News",)), S("channel4.com", "Channel 4 News", True, ("Channel 4 News",)),
           S("gov.uk", "Gouvernement britannique", False, (), True), S("parliament.uk", "Parlement britannique", True, ("UK Parliament",), True)],
    "DE": [S("tagesschau.de", "Tagesschau", True, ("tagesschau",)), S("zdf.de", "ZDF", True, ("ZDFheute Nachrichten",)),
           S("spiegel.de", "Der Spiegel", True, ("DER SPIEGEL",)), S("zeit.de", "Die Zeit"), S("faz.net", "FAZ"),
           S("sueddeutsche.de", "Süddeutsche Zeitung"), S("bundesregierung.de", "Bundesregierung", True, ("Bundesregierung",), True)],
    "AT": [S("orf.at", "ORF", True, ("ORF",)), S("derstandard.at", "Der Standard")],
    "IT": [S("rainews.it", "Rai News", True, ("Rai News",)), S("ansa.it", "ANSA", True, ("ANSA",)), S("corriere.it", "Corriere della Sera", True),
           S("repubblica.it", "la Repubblica", True), S("governo.it", "Governo italiano", True, (), True)],
    "ES": [S("rtve.es", "RTVE", True, ("RTVE Noticias",)), S("elpais.com", "El País", True, ("EL PAÍS",)), S("efe.com", "EFE", True, ("Agencia EFE",))],
}

LANG_COUNTRIES = {"fr": ["FR", "CH", "BE", "CA"], "en": ["US", "GB"], "de": ["DE", "CH", "AT"], "it": ["IT", "CH"], "es": ["ES"]}


def for_search(countries: list[str], langs: list[str], extra_domains: list[str] | None = None, limit: int = 10) -> list[Source]:
    """Sources à interroger directement : celles des pays concernés d'abord, puis l'international,
    puis celles proposées par l'IA pour ce sujet."""
    out: list[Source] = []
    seen: set[str] = set()

    def add(s: Source):
        if s.domain not in seen:
            seen.add(s.domain)
            out.append(s)
    cs = [c.upper() for c in countries if c] or [c for l in langs for c in LANG_COUNTRIES.get(l, [])][:2]
    # 1) pays concernés : institutions et médias publics vidéo d'abord
    for c in cs:
        for s in sorted(SOURCES.get(c, []), key=lambda s: (not s.official, not s.video)):
            add(s)
    # 2) sources proposées par l'IA pour ce sujet (domaines)
    for d in extra_domains or []:
        d = d.lower().strip().removeprefix("https://").removeprefix("http://").removeprefix("www.").split("/")[0]
        if "." in d and " " not in d:
            add(next((s for c in SOURCES.values() for s in c if s.domain == d), Source(d, d)))
    # 3) international
    for s in SOURCES["INT"]:
        add(s)
    return out[:limit]


def all_domains() -> dict[str, Source]:
    return {s.domain: s for c in SOURCES.values() for s in c}


def youtube_channels(countries: list[str], langs: list[str], limit: int = 4) -> list[str]:
    names = []
    for s in for_search(countries, langs, limit=30):
        for n in s.youtube:
            if n not in names:
                names.append(n)
    return names[:limit]
