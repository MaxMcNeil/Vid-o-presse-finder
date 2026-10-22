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

log = logging.getLogger("qf.transcripts")


def _to_cache(t: Transcript) -> dict:
    return {"lang": t.lang, "kind": t.kind, "segs": [[s.start, s.text] for s in t.segments]}


def _from_cache(d: dict) -> Transcript | None:
    if not d or not d.get("segs"):
        return None
    return Transcript([Segment(a, b) for a, b in d["segs"]], d.get("lang", "?"), d.get("kind", "unknown"))


# ---------- YouTube (youtube-transcript-api) ----------
def _yt_api(video_id: str, langs: list[str]) -> Transcript | None:
    from youtube_transcript_api import YouTubeTranscriptApi
    kw = {}
    proxy = os.environ.get("YT_PROXY_URL")
    if proxy:
        from youtube_transcript_api.proxies import GenericProxyConfig
        kw["proxy_config"] = GenericProxyConfig(http_url=proxy, https_url=proxy)
    api = YouTubeTranscriptApi(**kw)
    tl = api.list(video_id)
    tr, kind = None, "manual"
    try:
        tr = tl.find_manually_created_transcript(langs)
    except Exception:  # noqa: BLE001
        try:
            tr, kind = tl.find_generated_transcript(langs), "auto"
        except Exception:  # noqa: BLE001
            pass
    if tr is None:  # n'importe quelle langue, traduite vers la première langue voulue si possible
        for cand in tl:
            tr, kind = cand, "auto" if cand.is_generated else "manual"
            try:
                if cand.is_translatable and cand.language_code.split("-")[0] not in langs:
                    tr = cand.translate(langs[0])
                    kind += "+translated"
            except Exception:  # noqa: BLE001
                pass
            break
    if tr is None:
        return None
    f = tr.fetch()
    segs = [Segment(float(s.start), re.sub(r"\s+", " ", html.unescape(s.text)).strip()) for s in f]
    return Transcript([s for s in segs if s.text], f.language_code, kind)


# ---------- yt-dlp (toutes plateformes) ----------
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
                ln = re.sub(r"<[^>]+>", "", ln).strip()
                ln = html.unescape(ln)
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


def _ytdlp_subs(url: str, langs: list[str]) -> Transcript | None:
    with tempfile.TemporaryDirectory() as d:
        sl = ",".join(f"{l}.*" for l in langs) + ",en.*"
        cmd = [sys.executable, "-m", "yt_dlp", "--skip-download", "--write-subs", "--write-auto-subs",
               "--sub-langs", sl, "--sub-format", "vtt/srt/best", "--no-warnings", "-o", os.path.join(d, "s.%(ext)s"), url]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=120)
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
            t = _yt_api(v.id, langs)
        except Exception as e:  # noqa: BLE001 (blocage IP, sous-titres désactivés...)
            log.info("transcript API %s: %s", v.id, str(e).splitlines()[0][:120] if str(e) else type(e).__name__)
        if t is None:
            t = _ytdlp_subs(v.url, langs)
    elif v.platform == "archive":
        t = _archive_subs(v.id) or _ytdlp_subs(v.url, langs)
    else:
        t = _ytdlp_subs(v.url, langs)
    if t:
        cache.put("transcript", ck, _to_cache(t))
    return t
