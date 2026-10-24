"""Auto-diagnostic : teste chaque service depuis GitHub (workflow « Diagnostic ») et dit ce qui marche, en clair."""
from __future__ import annotations

import os
import shutil
import sys
import time

import requests

from .keys import keys_from_env
from .llm import LLMRouter

OK, KO, WARN = "✔", "✘", "⚠"


def line(sym, name, detail=""):
    print(f" {sym} {name}" + (f" — {detail}" if detail else ""), flush=True)


def t(fn):
    t0 = time.time()
    try:
        return fn(), None, time.time() - t0
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {str(e)[:110]}", time.time() - t0


def main() -> int:
    print("Quote Finder — diagnostic\n")
    bad = 0
    print("Clés chargées :")
    for n in ("GEMINI", "YOUTUBE", "FALLBACK_LLM", "GUARDIAN", "NYT"):
        k = keys_from_env(n)
        line(OK if k else (WARN if n in ("GUARDIAN", "NYT") else KO), n, f"{len(k)} clé(s)")
        if not k and n in ("GEMINI", "YOUTUBE"):
            bad += 1
    print("\nIA :")
    llm = LLMRouter.from_env()
    if llm.gemini:
        from .keys import KeyPool
        from .llm import GeminiClient
        print("  (60 s max par clé ; même méthode que la vraie recherche)", flush=True)
        for i, key in enumerate(keys_from_env("GEMINI"), 1):
            c = GeminiClient(KeyPool("t", [key], 0), list(llm.gemini.models), preferred=llm.gemini.preferred, timeout=60)
            r, e, d = t(lambda c=c: c.generate_json('Réponds exactement {"ok": true}', max_attempts=3))
            line(OK if r else KO, f"Gemini clé {i}", f"{c.model} (parmi {len(c._key_models.get(key, []))} modèles de cette clé), {d:.0f}s" if r else e)
    if llm.fallback:
        r, e, d = t(lambda: llm.fallback.generate_json('Réponds exactement {"ok": true}'))
        line(OK if r else KO, "LLM de secours (Groq…)", f"{llm.fallback.model}, {d:.0f}s" if r else e)
    print("\nVidéos :")
    yk = keys_from_env("YOUTUBE")
    if yk:
        def yt():
            r = requests.get("https://www.googleapis.com/youtube/v3/search", timeout=20,
                             params={"part": "id", "q": "test", "maxResults": 1, "type": "video", "key": yk[0]})
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} {r.json().get('error', {}).get('message', '')[:90]}")
            return len(r.json().get("items", []))
        r, e, d = t(yt)
        line(OK if r is not None else KO, "YouTube API (1re clé)", "répond" if r is not None else e)
        bad += 0 if r is not None else 1
    from .sources import youtube
    r, e, d = t(lambda: youtube.search_ytdlp("actualité", 3))
    line(OK if r else KO, "YouTube via yt-dlp (sans clé)", f"{len(r)} résultat(s)" if r else (e or "0 résultat"))
    from youtube_transcript_api import YouTubeTranscriptApi
    r, e, d = t(lambda: len(YouTubeTranscriptApi().fetch("dQw4w9WgXcQ", languages=["en"])))
    line(OK if r else KO, "Sous-titres YouTube", f"{r} lignes lues" if r else (e or "vide") + "  (YouTube bloque cette connexion : l'analyse vidéo par IA prend le relais)")
    from .sources import other_video
    dm, e, d = t(lambda: other_video.dailymotion("actualités", 10))
    line(OK if dm else KO, "Dailymotion (recherche)", f"{len(dm)} résultat(s)" if dm else (e or "0 résultat"))
    line(OK if shutil.which("ffmpeg") else KO, "ffmpeg", "présent" if shutil.which("ffmpeg") else "ABSENT : sudo apt-get install ffmpeg")
    if dm and shutil.which("ffmpeg"):
        from . import media
        short = next((v for v in dm if v.duration and v.duration <= 120), dm[0])
        r, e, d = t(lambda: media.fetch_audio(short.url, 10))
        line(OK if r else KO, "Téléchargement audio (Dailymotion)", f"{len(r) // 1024} Ko en {d:.0f}s" if r else (e or "échec : voir le message ci-dessus ou `yt-dlp -U`"))
        if not r:
            import subprocess
            p = subprocess.run([sys.executable, "-m", "yt_dlp", "-F", short.url], capture_output=True, text=True, timeout=60)
            print("      yt-dlp :", (p.stderr.strip().splitlines() or p.stdout.strip().splitlines() or ["(aucun message)"])[-1][:200])
    print("\nPresse :")
    from .sources import news
    for name, fn in (("Bing Actualités", lambda: news.bing_news("Bardella", 5, "fr")),
                     ("Google Actualités", lambda: news.google_news("Bardella", 5, "fr")),
                     ("GDELT", lambda: news.gdelt("Bardella", 5))):
        r, e, d = t(fn)
        line(OK if r else WARN, name, f"{len(r)} article(s)" if r else (e or "0 résultat (souvent un blocage temporaire)"))
    print("\n" + ("Tout l'essentiel fonctionne." if not bad else f"{bad} problème(s) bloquant(s) ci-dessus : corrige-les avant de lancer une recherche."))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
