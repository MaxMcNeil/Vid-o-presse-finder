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
    from . import quota
    from .keys import KeyPool
    from .llm import PROVIDERS, GeminiClient, OpenAICompatClient, is_text_only
    print("Quote Finder — diagnostic\n")
    st = quota.state()
    bad = 0
    print("Clés chargées :")
    for n in ("GEMINI", "YOUTUBE", "GROQ", "CEREBRAS", "OPENROUTER", "FALLBACK_LLM", "GUARDIAN", "NYT"):
        k = keys_from_env(n)
        if k or n in ("GEMINI", "YOUTUBE"):
            line(OK if k else KO, n, f"{len(k)} clé(s)")
        if not k and n in ("GEMINI", "YOUTUBE"):
            bad += 1
    print("\nGemini (chaque clé, chaque modèle a son propre quota gratuit) :", flush=True)
    llm = LLMRouter.from_env()
    ok_g = 0
    for i, key in enumerate(keys_from_env("GEMINI"), 1):
        pool = KeyPool("gemini", [key], 0, persist=True)
        pool.labels[key] = f"Gemini clé {i}"  # numéro réel dans le secret (et non « clé 1 » à chaque fois)
        st.label("gemini", key, pool.labels[key])
        c = GeminiClient(pool, list(llm.gemini.models), preferred=llm.gemini.preferred, timeout=60)
        r, e, d = t(lambda c=c: c.generate_json('Réponds exactement {"ok": true}', max_attempts=4))
        models = c._key_models.get(key, [])
        fl = [m for m in models if not is_text_only(m)]
        gm = [m for m in models if is_text_only(m)]
        ok_g += bool(r)
        line(OK if r else KO, f"Gemini clé {i}", (f"répond avec {c.model} en {d:.0f}s" if r else e) +
             f" — {len(fl)} modèle(s) vidéo/texte, {len(gm)} Gemma (texte)")
    if keys_from_env("GEMINI") and not ok_g:
        bad += 1
    for fb in llm.fallbacks:
        for i, key in enumerate(fb.pool._next_ok, 1):
            c = OpenAICompatClient(KeyPool(fb.name, [key], 0, persist=True), fb.base, fb.models[0])
            c.name = fb.name
            r, e, d = t(lambda c=c: c.generate_json('Réponds exactement {"ok": true}'))
            line(OK if r else KO, f"{fb.name} clé {i}", f"{c.model}, {d:.0f}s" if r else e)
    if not llm.fallbacks:
        line(WARN, "Relais (Groq / Cerebras / OpenRouter)", "aucun : ajoute GROQ_API_KEYS (gratuit) pour ne jamais rester bloqué")
    print("\nVidéos :")
    for i, key in enumerate(keys_from_env("YOUTUBE"), 1):
        def yt(key=key):  # 1 unité seulement (au lieu de 100 pour une recherche)
            r = requests.get("https://www.googleapis.com/youtube/v3/videos", timeout=20,
                             params={"part": "id", "id": "dQw4w9WgXcQ", "key": key})
            st.use("youtube", key, 1)
            if r.status_code != 200:
                msg = r.json().get("error", {}).get("message", "")[:90]
                if "quota" in msg.lower():
                    st.exhaust_key("youtube", key)
                raise RuntimeError(f"HTTP {r.status_code} {msg}")
            return True
        st.label("youtube", key, f"YouTube clé {i}")
        r, e, d = t(yt)
        left = st.units_left("youtube", key)
        line(OK if r else KO, f"YouTube clé {i}", f"répond — ~{left} unités restantes aujourd'hui (≈ {left // 100} recherches)" if r else e)
    st.save()
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
    print("\nÉtat des clés (aussi dans state/QUOTAS.md après chaque recherche) :\n")
    print(st.markdown())
    print("\n" + ("Tout l'essentiel fonctionne." if not bad else f"{bad} problème(s) bloquant(s) ci-dessus : corrige-les avant de lancer une recherche."))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
