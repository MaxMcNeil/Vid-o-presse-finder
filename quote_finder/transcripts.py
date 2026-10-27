"""Récupération des sous-titres/transcriptions horodatés (gratuit)."""
from __future__ import annotations

import glob
import html
import json
import logging
import os
import re
import subprocess
import sys
import tempfile

import requests

from . import cache
from .models import Segment, Transcript, VideoCandidate
from .util import call_with_timeout

log = logging.getLogger("qf.transcripts")


def _to_cache(t: Transcript) -> dict:
    return {"lang": t.lang, "kind": t.kind, "via": t.via, "segs": [[s.start, s.text] for s in t.segments]}


def _from_cache(d: dict) -> Transcript | None:
    if not d or not d.get("segs"):
        return None
    return Transcript([Segment(a, b) for a, b in d["segs"]], d.get("lang", "?"), d.get("kind", "unknown"),
                      (d.get("via") or "") + " (cache)")


# ---------- YouTube : toutes les méthodes gratuites en cascade (subtitles.py) ----------
def _youtube(video_id: str, langs: list[str]) -> Transcript | None:
    from . import subtitles
    try:
        tracks, via = subtitles.fetch(video_id, langs, translate_to=langs[0] if langs else "")
    except subtitles.Blocked as e:
        log.info("sous-titres %s refusés partout (%s) : analyse par Gemini", video_id, str(e)[:120])
        return None
    if not tracks:
        return None
    # pour la recherche : une piste dans une langue de travail si possible (les termes cherchés y sont), sinon l'originale
    t = next((t for t in tracks if t.lang in langs and t.kind != "translated"), None) or \
        next((t for t in tracks if t.lang in langs), None) or tracks[0]
    kind = {"manual": "manual", "translated": "auto+translated"}.get(t.kind, "auto")
    return Transcript(t.segs, t.lang, kind, via)


# ---------- yt-dlp (toutes plateformes) ----------
from .subtitles import parse_vtt_srt  # noqa: E402,F401 - utilisé aussi par le téléphone
_TS = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})\s+-->")


def _ytdlp_subs(url: str, langs: list[str]) -> Transcript | None:
    with tempfile.TemporaryDirectory() as d:
        sl = ",".join(f"{l}.*" for l in langs) + ",en.*"
        cmd = [sys.executable, "-m", "yt_dlp", "--skip-download", "--write-subs", "--write-auto-subs",
               "--sub-langs", sl, "--sub-format", "vtt/srt/best", "--no-warnings", "-o", os.path.join(d, "s.%(ext)s"), url]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        except (subprocess.SubprocessError, OSError):
            return None
        files = sorted(glob.glob(os.path.join(d, "s.*.vtt")) + glob.glob(os.path.join(d, "s.*.srt")))
        if not files:
            return None
        # préférer les langues demandées, dans l'ordre
        def rank(p):
            code = os.path.basename(p).split(".")[1]
            for i, l in enumerate(langs):
                if code.startswith(l):
                    return i
            return 99
        files.sort(key=rank)
        segs = parse_vtt_srt(open(files[0], encoding="utf-8", errors="replace").read())
        return Transcript(segs, os.path.basename(files[0]).split(".")[1], "subtitles") if segs else None


# ---------- Internet Archive ----------
def _archive_subs(identifier: str) -> Transcript | None:
    try:
        meta = requests.get(f"https://archive.org/metadata/{identifier}", timeout=30).json()
        names = [f["name"] for f in meta.get("files", []) if f["name"].lower().endswith((".srt", ".vtt"))]
        if not names:
            return None
        names.sort(key=lambda n: (0 if "asr" in n.lower() else 1, n))  # transcription auto d'abord
        raw = requests.get(f"https://archive.org/download/{identifier}/{names[0]}", timeout=60).text
        segs = parse_vtt_srt(raw)
        return Transcript(segs, "?", "subtitles") if segs else None
    except (requests.RequestException, ValueError):
        return None


def get_transcript(v: VideoCandidate, langs: list[str]) -> Transcript | None:
    ck = f"{v.key}|{','.join(langs)}"
    c = cache.get("transcript", ck)
    if c is not None:
        return _from_cache(c)
    t = None
    if v.platform == "youtube":
        try:
            t = call_with_timeout(_youtube, 360, v.id, langs)
        except Exception as e:  # noqa: BLE001 - jamais bloquant : Gemini prend le relais
            log.info("sous-titres %s : %s", v.id, type(e).__name__)
    elif v.platform == "archive":
        t = _archive_subs(v.id) or _ytdlp_subs(v.url, langs)
    else:
        t = _ytdlp_subs(v.url, langs)
    if t:
        cache.put("transcript", ck, _to_cache(t))
    return t
