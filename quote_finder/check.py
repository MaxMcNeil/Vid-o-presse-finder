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


def t(fn, limit: float = 90):
    """Un test = au plus 90 s : un service qui ne répond pas ne doit jamais faire annuler tout le Diagnostic."""
    from .util import call_with_timeout
    t0 = time.time()
    try:
        return call_with_timeout(fn, limit), None, time.time() - t0
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {str(e)[:110]}", time.time() - t0


def gemini_key_status(key: str, http=None) -> tuple[str, str, int]:
    """("valid" | "invalid" | "quota" | "busy", explication, nb de modèles). 2 essais, 20 s chacun."""
    s = http or requests
    last = ""
    for attempt in (1, 2):
        try:
            r = s.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": key, "pageSize": 200},
                      timeout=20)
        except requests.RequestException as e:
            last = f"Google injoignable ({type(e).__name__})"
            time.sleep(3)
            continue
        if r.status_code == 200:
            try:
                n = sum(1 for m in r.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", []))
            except (ValueError, AttributeError, TypeError):
                n = 0
            return "valid", "clé valide", n
        if r.status_code in (400, 401, 403):
            txt = (r.text or "")[:300]
            why = ("clé invalide" if "API_KEY_INVALID" in txt or "API key not valid" in txt else
                   "API Gemini non activée pour ce projet" if "SERVICE_DISABLED" in txt or "has not been used" in txt else
                   "clé refusée")
            return "invalid", f"{why} par Google (HTTP {r.status_code}) : remplace-la dans GEMINI_API_KEYS", 0
        if r.status_code == 429:
            return "quota", "limite atteinte pour l'instant (429) : se débloque seule", 0
        last = f"Google ne répond pas correctement (HTTP {r.status_code}) — pas un problème de clé"
        time.sleep(3)
    return "busy", last or "Google ne répond pas — pas un problème de clé", 0


def _safe_list(fn):
    try:
        return fn() or []
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    from . import quota
    from .keys import KeyPool
    from .llm import GeminiClient, OpenAICompatClient
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
    # 1) validité de chaque clé : simple liste des modèles (ne consomme aucun quota, ne dépend pas d'une surcharge)
    # 2) réponse réelle : sur 3 clés valides seulement (inutile de brûler le quota de toutes)
    # Une surcharge passagère de Google (503, lenteur) N'EST PAS une panne de clé : ⚠, jamais bloquant.
    g_valid, g_dead, g_answer, g_busy, tested = 0, 0, 0, 0, 0
    for i, key in enumerate(keys_from_env("GEMINI"), 1):
        name = f"Gemini clé {i}"
        kind, detail, n_models = gemini_key_status(key)
        if kind == "invalid":
            g_dead += 1
            line(KO, name, detail)
            continue
        if kind != "valid":
            line(WARN, name, detail)
            continue
        g_valid += 1
        if tested >= 3:
            line(OK, name, f"clé valide ({n_models} modèles) — réponse non testée (quota épargné)")
            continue
        tested += 1
        pool = KeyPool("gemini", [key], 0, persist=True)
        pool.labels[key] = name  # numéro réel dans le secret (et non « clé 1 » à chaque fois)
        st.label("gemini", key, pool.labels[key])
        c = GeminiClient(pool, list(llm.gemini.models), preferred=llm.gemini.preferred, timeout=40)
        r, e, d = t(lambda c=c: c.generate_json('Réponds exactement {"ok": true}', max_attempts=3), 45)
        if r:
            g_answer += 1
            line(OK, name, f"clé valide, répond avec {c.model} en {d:.0f}s")
        else:
            g_busy += 1
            why = ("quota du jour atteint sur les modèles essayés" if e and "429" in e else
                   "Google surchargé en ce moment (503 / lenteur)" if e and ("503" in e or "Timeout" in e or "plus de" in e)
                   else (e or "pas de réponse"))
            line(WARN, name, f"clé valide ({n_models} modèles), mais pas de réponse maintenant : {why} — pas un problème de "
                             "clé, la rotation passera aux autres modèles / clés")
    fb_ok = 0
    for fb in llm.fallbacks:
        for i, key in enumerate(fb.pool._next_ok, 1):
            c = OpenAICompatClient(KeyPool(fb.name, [key], 0, persist=True), fb.base, fb.models[0])
            c.name = fb.name
            r, e, d = t(lambda c=c: c.generate_json('Réponds exactement {"ok": true}'))
            fb_ok += bool(r)
            line(OK if r else WARN, f"{fb.name} clé {i}", f"{c.model}, {d:.0f}s" if r else e)
    if keys_from_env("GEMINI"):
        if g_dead and not g_valid:
            bad += 1
            line(KO, "Gemini", f"aucune clé acceptée par Google ({g_dead} refusée(s)) : vérifie le secret GEMINI_API_KEYS")
        elif g_dead:
            line(WARN, "Gemini", f"{g_dead} clé(s) refusée(s) par Google : remplace-les quand tu peux (les autres suffisent)")
        if g_valid and not g_answer and not fb_ok:
            line(WARN, "IA", "Google surchargé et aucun relais disponible en ce moment : relance une recherche plus tard")
        elif g_valid and not g_answer:
            line(WARN, "IA", "Google surchargé en ce moment : le relais (Groq…) répond, les recherches marchent "
                             "(l'analyse directe des vidéos reprendra quand Google répondra)")
    if not llm.fallbacks:
        line(WARN, "Relais (Groq / Cerebras / OpenRouter)", "aucun : ajoute GROQ_API_KEYS (gratuit) pour ne jamais rester bloqué")
    print("\nLecture d'articles par Gemini (pour les sites fermés aux robots et les liens Google Actualités) :", flush=True)
    if llm.gemini:
        from types import SimpleNamespace
        from . import article_text
        from .sources import news
        tests = [SimpleNamespace(url="https://www.reuters.com/world/", source="Reuters", title="World news", domain="reuters.com", text="")]
        g = _safe_list(lambda: news.google_news("FAO food crisis", 2, "en"))
        if g:
            tests.append(g[0])
        for a in tests:
            def rd(a=a):
                t, real = article_text.gemini(a, llm, "actualité")
                if not t:
                    raise RuntimeError("page non lue (réponse vide ou « INACCESSIBLE »)")
                return f"{len(t)} caractères lus" + (f", vrai lien : {real[:70]}" if real else "")
            r, e, d = t(rd, 60)
            line(OK if r else WARN, ("lien Google Actualités" if "news.google.com" in a.url else a.url[:40]),
                 r or f"{e} (passager le plus souvent : non bloquant)")
        if g:  # vrai lien d'un article Google Actualités retrouvé par la recherche web de Gemini
            def fr(a=g[0]):
                import copy
                real = article_text.find_real_url(copy.copy(a), llm)
                if not real:
                    raise RuntimeError("vrai lien non retrouvé")
                return real[:80]
            r, e, d = t(fr, 60)
            line(OK if r else WARN, "vrai lien d'un article Google Actualités (recherche web Gemini)", r or f"{e} (non bloquant)")
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
    from . import subtitles   # chaque méthode de la cascade, une par une (la 1re qui marche sert en recherche)
    for name, fn in subtitles.methods(["en"]):
        r, e, d = t(lambda fn=fn: sum(len(x.segs) for x in fn("dQw4w9WgXcQ")))
        line(OK if r else KO, f"Sous-titres YouTube : {name}", f"{r} lignes lues en {d:.0f}s" if r else (e or "vide")
             + "  (la méthode suivante prend le relais)")
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
                     ("GDELT", lambda: news.gdelt("Bardella", 5)),
                     ("Recherche Google par Gemini (vrais liens)", lambda: news.gemini_search(llm, "Bardella Assemblée nationale", 5, "fr"))):
        r, e, d = t(fn, 60)
        line(OK if r else WARN, name, f"{len(r)} article(s)" if r else (e or "0 résultat (souvent un blocage temporaire)"))
    print("\nÉtat des clés (aussi dans state/QUOTAS.md après chaque recherche) :\n")
    print(st.markdown())
    print("\n" + ("Tout l'essentiel fonctionne." if not bad else f"{bad} problème(s) bloquant(s) ci-dessus : corrige-les avant de lancer une recherche."))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
