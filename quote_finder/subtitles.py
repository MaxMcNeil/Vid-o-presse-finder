"""Sous-titres YouTube horodatés : TOUTES les méthodes gratuites, de la plus fiable à la moins fiable, en cascade.

Utilisé par GitHub (recherche) ET par le téléphone (recalage). Chaque vidéo essaie les méthodes dans l'ordre ; la première
qui rend des sous-titres gagne. Une méthode refusée deux fois de suite par YouTube (blocage de l'adresse, anti-robot)
est sautée pour le reste du lancement : on ne perd pas de temps dessus. Ce qui a marché est compté (STATS) et affiché
dans le rapport et dans ETAT.md.

Ordre :
  1. youtube-transcript-api            — le plus rapide ; horodatage de chaque ligne (début + durée)
  2. youtube-transcript-api + proxy    — seulement si le secret YT_PROXY_URL existe
  3. yt-dlp                            — client par défaut ; jeton PO automatique si le fournisseur bgutil tourne (GitHub)
  4. yt-dlp, autres clients YouTube    — tv, web_safari, mweb, android_vr : YouTube ne les bloque pas tous en même temps
  5. yt-dlp + cookies                  — seulement si un fichier cookies existe (compte YouTube SECONDAIRE)
  6. Invidious (miroirs publics)       — les sous-titres sont lus par un autre serveur que le nôtre
Si rien ne marche : la recherche garde l'horodatage de Gemini (réécoute ciblée, ±1 s) et le téléphone réessaiera.

Note : l'API officielle YouTube Data v3 (captions.download) ne télécharge que les sous-titres des vidéos dont on est
propriétaire : elle ne sert à rien ici.
"""
from __future__ import annotations

import glob
import html
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .models import Segment

log = logging.getLogger("qf.subtitles")

BLOCK_NAMES = ("RequestBlocked", "IpBlocked", "TooManyRequests", "YouTubeRequestFailed", "PoTokenRequired")
NONE_NAMES = ("TranscriptsDisabled", "NoTranscriptFound", "VideoUnavailable", "VideoUnplayable", "InvalidVideoId",
              "AgeRestricted", "NotTranslatable")
YTDLP_CLIENTS = "tv,web_safari,mweb,android_vr"


class Blocked(Exception):
    """YouTube refuse cette méthode depuis ce réseau (blocage d'adresse, anti-robot, trop de requêtes)."""


class NoSubtitles(Exception):
    """La vidéo n'a vraiment pas de sous-titres (désactivés, privée, supprimée) : inutile d'essayer ailleurs."""


@dataclass
class Track:
    segs: list[Segment]
    lang: str = "?"
    kind: str = "auto"       # manual | auto | translated
    original: bool = False   # langue d'origine de la vidéo (celle des citations)


@dataclass
class Stats:
    ok: dict = field(default_factory=dict)        # méthode -> vidéos réussies
    blocked: dict = field(default_factory=dict)   # méthode -> refus
    none: int = 0                                 # vidéos sans sous-titres
    failed: int = 0                               # vidéos où toutes les méthodes ont échoué

    def as_dict(self) -> dict:
        return {"ok": dict(self.ok), "blocked": dict(self.blocked), "sans_sous_titres": self.none, "échecs": self.failed}


STATS = Stats()
_lock = threading.Lock()
_strikes: dict[str, int] = {}   # refus consécutifs par méthode (disjoncteur pour ce lancement)
STRIKES_MAX = 2


def reset() -> None:
    global STATS
    with _lock:
        STATS = Stats()
        _strikes.clear()


def cookies_file() -> Path | None:
    """Cookies d'un compte YouTube secondaire : fichier QF_YT_COOKIES (GitHub, écrit depuis un secret) ou ~/.qf/cookies.txt."""
    for p in (os.environ.get("QF_YT_COOKIES", ""), str(Path.home() / ".qf" / "cookies.txt")):
        if p and Path(p).is_file() and Path(p).stat().st_size > 50:
            return Path(p)
    return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


# ------------------------------------------------------------------ méthode 1-2 : youtube-transcript-api
def _api(video_id: str, langs: list[str], proxy: str = "", translate_to: str = "") -> list[Track]:
    from youtube_transcript_api import YouTubeTranscriptApi
    kw = {}
    if proxy:
        from youtube_transcript_api.proxies import GenericProxyConfig
        kw["proxy_config"] = GenericProxyConfig(http_url=proxy, https_url=proxy)
    try:
        tl = YouTubeTranscriptApi(**kw).list(video_id)
    except Exception as e:  # noqa: BLE001
        name = type(e).__name__
        if name in BLOCK_NAMES:
            raise Blocked(name) from e
        if name in NONE_NAMES:
            raise NoSubtitles(name) from e
        raise
    cands = list(tl)
    if not cands:
        raise NoSubtitles("aucune piste")
    # langue d'origine : la piste automatique (elle est toujours dans la langue parlée), sinon la 1re manuelle
    orig = next((c.language_code for c in cands if c.is_generated), cands[0].language_code).split("-")[0]
    cands.sort(key=lambda c: (c.language_code.split("-")[0] != orig, c.language_code.split("-")[0] not in langs, c.is_generated))
    out = []
    for c in cands[:3]:
        try:
            f = c.fetch()
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ in BLOCK_NAMES:
                raise Blocked(type(e).__name__) from e
            continue
        segs = [Segment(float(s.start), _clean(s.text)) for s in f]
        segs = [s for s in segs if s.text]
        if segs:
            lg = c.language_code.split("-")[0]
            out.append(Track(segs, lg, "auto" if c.is_generated else "manual", lg == orig))
    if translate_to and out and not any(t.lang in langs for t in out):  # GitHub : une version dans la langue de travail
        try:
            c = next(c for c in cands if c.is_translatable)
            f = c.translate(translate_to).fetch()
            segs = [Segment(float(s.start), _clean(s.text)) for s in f if _clean(s.text)]
            if segs:
                out.append(Track(segs, translate_to, "translated", False))
        except Exception:  # noqa: BLE001
            pass
    return out


# ------------------------------------------------------------------ méthodes 3-5 : yt-dlp
_TS = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})\s+-->")


def parse_vtt_srt(raw: str) -> list[Segment]:
    segs: list[Segment] = []
    cur_t, buf = None, []
    last_line = ""

    def flush():
        nonlocal cur_t, buf, last_line
        if cur_t is not None:
            lines = []
            for ln in buf:
                ln = html.unescape(re.sub(r"<[^>]+>", "", ln)).strip()
                if ln and ln != last_line:  # les sous-titres auto YouTube répètent la ligne précédente
                    lines.append(ln)
                    last_line = ln
            if lines:
                segs.append(Segment(cur_t, " ".join(lines)))
        cur_t, buf = None, []

    for line in raw.splitlines():
        m = _TS.search(line)
        if m:
            flush()
            h, mi, s, ms = (int(x or 0) for x in m.groups())
            cur_t = h * 3600 + mi * 60 + s + ms / 1000
        elif cur_t is not None and line.strip() and not line.strip().isdigit() and not line.startswith(("NOTE", "WEBVTT")):
            buf.append(line)
    flush()
    return segs


def _ytdlp(video_id: str, langs: list[str], clients: str = "", cookies: Path | None = None,
           url: str = "", timeout: int = 90) -> list[Track]:
    want = ",".join([".*-orig"] + list(langs) + [f"{l}-.*" for l in langs])
    with tempfile.TemporaryDirectory() as d:
        cmd = [sys.executable, "-m", "yt_dlp", "--skip-download", "--write-subs", "--write-auto-subs",
               "--sub-langs", want, "--sub-format", "vtt/srt/best", "--no-warnings", "--sleep-subtitles", "1",
               "--socket-timeout", "20", "-o", os.path.join(d, "s.%(ext)s")]
        if clients:
            cmd += ["--extractor-args", f"youtube:player_client={clients}"]
        if cookies:
            cmd += ["--cookies", str(cookies)]
        try:
            p = subprocess.run(cmd + [url or f"https://www.youtube.com/watch?v={video_id}"], capture_output=True,
                               text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise Blocked("yt-dlp : délai dépassé") from e
        except OSError:
            return []
        err = (p.stderr or "") + (p.stdout or "")
        files = sorted(glob.glob(os.path.join(d, "*.vtt")) + glob.glob(os.path.join(d, "*.srt")),
                       key=lambda f: (0 if "-orig" in f else 1, f))
        if not files:
            if re.search(r"not a bot|Sign in to confirm|HTTP Error 429|Too Many Requests|PO Token|po_token|"
                         r"blocked|unable to download video subtitles", err, re.I):
                raise Blocked("yt-dlp : " + ("anti-robot" if "bot" in err else "refus de YouTube"))
            if re.search(r"Private video|Video unavailable|has been removed|members-only", err, re.I):
                raise NoSubtitles("vidéo indisponible")
            if re.search(r"There are no subtitles|no subtitles for the requested|has no subtitles", err, re.I):
                raise NoSubtitles("aucun sous-titre")
            return []  # pas de piste dans ces langues (ou aucune)
        out = []
        for f in files[:3]:
            segs = parse_vtt_srt(open(f, encoding="utf-8", errors="replace").read())
            if segs:
                code = os.path.basename(f).split(".")[1]
                out.append(Track(segs, code.split("-")[0], "auto", "-orig" in code))
        return out


# ------------------------------------------------------------------ méthode 6 : Invidious (miroirs publics)
_instances: list[str] | None = None


def _invidious_instances() -> list[str]:
    global _instances
    if _instances is None:
        _instances = []
        try:
            r = requests.get("https://api.invidious.io/instances.json?sort_by=health", timeout=15)
            for name, info in r.json():
                info = info or {}
                if info.get("type") == "https" and info.get("api") and info.get("uri"):
                    _instances.append(info["uri"].rstrip("/"))
        except Exception:  # noqa: BLE001
            pass
        _instances = _instances[:4]
    return _instances


def _invidious(video_id: str, langs: list[str]) -> list[Track]:
    insts = _invidious_instances()
    if not insts:
        raise Blocked("aucun miroir Invidious joignable")
    for base in insts:
        try:
            r = requests.get(f"{base}/api/v1/captions/{video_id}", timeout=15)
            if r.status_code != 200:
                continue
            caps = (r.json() or {}).get("captions") or []
            if not caps:
                raise NoSubtitles("aucune piste (Invidious)")
            caps.sort(key=lambda c: ("auto" not in (c.get("label") or "").lower(),  # piste auto = langue parlée
                                     (c.get("language_code") or c.get("languageCode") or "")[:2] not in langs))
            out = []
            for c in caps[:2]:
                u = c.get("url") or ""
                v = requests.get(base + u if u.startswith("/") else u, timeout=20)
                segs = parse_vtt_srt(v.text) if v.status_code == 200 else []
                if segs:
                    lg = (c.get("language_code") or c.get("languageCode") or "?")[:2]
                    out.append(Track(segs, lg, "auto" if "auto" in (c.get("label") or "").lower() else "manual",
                                     "auto" in (c.get("label") or "").lower()))
            if out:
                return out
        except NoSubtitles:
            raise
        except Exception:  # noqa: BLE001
            continue
    raise Blocked("miroirs Invidious indisponibles")


# ------------------------------------------------------------------ cascade
def methods(langs: list[str], translate_to: str = "") -> list[tuple[str, object]]:
    m = [("youtube-transcript-api", lambda vid: _api(vid, langs, translate_to=translate_to))]
    proxy = os.environ.get("YT_PROXY_URL", "")
    if proxy:
        m.append(("youtube-transcript-api + proxy", lambda vid: _api(vid, langs, proxy, translate_to)))
    m.append(("yt-dlp", lambda vid: _ytdlp(vid, langs)))
    m.append(("yt-dlp (autres clients)", lambda vid: _ytdlp(vid, langs, clients=YTDLP_CLIENTS)))
    ck = cookies_file()
    if ck:
        m.append(("yt-dlp + cookies", lambda vid: _ytdlp(vid, langs, clients=YTDLP_CLIENTS, cookies=ck)))
    if os.environ.get("QF_NO_INVIDIOUS") != "1":
        m.append(("Invidious", lambda vid: _invidious(vid, langs)))
    return m


def fetch(video_id: str, langs: list[str], translate_to: str = "") -> tuple[list[Track], str]:
    """Pistes de sous-titres (langue d'origine d'abord) et nom de la méthode qui a marché.
    [] si la vidéo n'a pas de sous-titres. Blocked si TOUTES les méthodes ont été refusées (réessayer plus tard)."""
    refused = []
    for name, fn in methods(langs, translate_to):
        with _lock:
            if _strikes.get(name, 0) >= STRIKES_MAX:
                refused.append(f"{name} (sauté)")
                continue
        try:
            tracks = fn(video_id)
        except NoSubtitles:
            with _lock:
                STATS.none += 1
            return [], name
        except Blocked as e:
            with _lock:
                _strikes[name] = _strikes.get(name, 0) + 1
                STATS.blocked[name] = STATS.blocked.get(name, 0) + 1
                if _strikes[name] == STRIKES_MAX:
                    log.warning("Sous-titres : %s refusé par YouTube 2 fois de suite → sauté pour ce lancement", name)
            refused.append(f"{name} ({e})")
            continue
        except ImportError:
            continue
        except Exception as e:  # noqa: BLE001 - une méthode en panne ne doit jamais bloquer les suivantes
            log.info("Sous-titres %s : %s (%s)", video_id, name, type(e).__name__)
            continue
        with _lock:
            _strikes[name] = 0
        if tracks:
            with _lock:
                STATS.ok[name] = STATS.ok.get(name, 0) + 1
            tracks.sort(key=lambda t: (not t.original, t.kind == "translated"))
            return tracks, name
        time.sleep(0.5)
    if refused and len(refused) >= 2:
        with _lock:
            STATS.failed += 1
        raise Blocked(" ; ".join(refused)[:300])
    return [], ""
