"""Audio d'une vidéo (Dailymotion, Archive...) pour les contenus sans sous-titres : yt-dlp + ffmpeg, puis Gemini l'écoute."""
from __future__ import annotations

import glob
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading

log = logging.getLogger("qf.media")
MAX_BYTES = 14 * 1024 * 1024  # limite d'envoi direct à Gemini (~20 Mo avec l'encodage)


LAST_REASON = threading.local()


def fetch_audio(url: str, max_minutes: int = 45) -> bytes | None:
    """Audio mono 16 kHz / 32 kbit/s (~14 Mo pour 55 min). None si indisponible (raison : LAST_REASON.text)."""
    LAST_REASON.text = ""
    if not shutil.which("ffmpeg"):
        LAST_REASON.text = "ffmpeg absent"
        log.warning("ffmpeg absent : analyse audio impossible")
        return None
    with tempfile.TemporaryDirectory() as d:
        cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings", "-f", "worstaudio/worst", "-x", "--audio-format", "mp3",
               "--postprocessor-args", "ffmpeg:-ac 1 -ar 16000 -b:a 32k", "--match-filter", f"duration<={max_minutes * 60}",
               "--no-playlist", "-o", os.path.join(d, "a.%(ext)s"), url]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except (subprocess.SubprocessError, OSError) as e:
            LAST_REASON.text = "téléchargement trop long ou interrompu"
            log.warning("audio %s : %s", url, e)
            return None
        files = glob.glob(os.path.join(d, "a.*"))
        if not files:
            err = (p.stderr.strip().splitlines() or p.stdout.strip().splitlines() or ["?"])[-1]
            if "does not pass filter" in p.stdout + p.stderr or "match-filter" in p.stdout + p.stderr:
                LAST_REASON.text = f"vidéo de plus de {max_minutes} min ignorée"
            else:
                LAST_REASON.text = err[:110]
            log.info("audio indisponible %s : %s", url, LAST_REASON.text)
            return None
        data = open(files[0], "rb").read()
        if len(data) > MAX_BYTES:
            LAST_REASON.text = f"audio trop gros ({len(data) // 1048576} Mo)"
            log.info("audio trop gros (%d Mo) : %s", len(data) // 1048576, url)
            return None
        return data
