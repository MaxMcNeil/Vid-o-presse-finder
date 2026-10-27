# Dernier diagnostic

```
Quote Finder — diagnostic
Clés chargées :
 ✔ GEMINI — 10 clé(s)
 ✔ YOUTUBE — 8 clé(s)
 ✔ FALLBACK_LLM — 1 clé(s)
 ✔ GUARDIAN — 1 clé(s)
 ✔ NYT — 1 clé(s)
Gemini (chaque clé, chaque modèle a son propre quota gratuit) :
 ✔ Gemini clé 1 — répond avec gemini-3.8-flash en 9s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
 ✘ Gemini clé 2 — TimeoutError: plus de 90 s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
 ✔ Gemini clé 3 — répond avec gemini-3.8-flash en 30s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
 ✘ Gemini clé 4 — TimeoutError: plus de 90 s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
 ✔ Gemini clé 5 — répond avec gemini-3.8-flash en 87s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
 ✘ Gemini clé 6 — TimeoutError: plus de 90 s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
Gemini gemini-3.8-flash surchargé : pause 2 min, on utilise d'autres modèles
Gemini gemini-3.7-flash surchargé : pause 2 min, on utilise d'autres modèles
 ✘ Gemini clé 7 — LLMError: HTTP 503 (gemini-3.7-flash): {
  "error": {
    "code": 503,
    "message": "This model is currently experienc — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
 ✘ Gemini clé 8 — TimeoutError: plus de 90 s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
 ✘ Gemini clé 9 — TimeoutError: plus de 90 s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
Gemini gemini-3.8-flash surchargé : pause 2 min, on utilise d'autres modèles
Gemini gemini-3.8-flash surchargé : pause 2 min, on utilise d'autres modèles
 ✘ Gemini clé 10 — LLMError: HTTP 503 (gemini-3.7-flash): {
  "error": {
    "code": 503,
    "message": "This model is currently experienc — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
Modèle de secours llama-3.3-70b-versatile refusé, on change de modèle
 ✔ groq clé 1 — openai/gpt-oss-120b, 1s
Lecture d'articles par Gemini (pour les sites fermés aux robots et les liens Google Actualités) :
 ✘ https://www.reuters.com/world/ — TimeoutError: plus de 90 s
Gemini gemini-3.8-flash surchargé : pause 2 min, on utilise d'autres modèles
 ✘ lien Google Actualités — TimeoutError: plus de 90 s
 ✘ vrai lien d'un article Google Actualités (recherche web Gemini) — LLMError: HTTP 429 (gemini-3.7-flash): {
  "error": {
    "code": 429,
    "message": "You exceeded your current quota, 
Vidéos :
 ✔ YouTube clé 1 — répond — ~9998 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube clé 2 — répond — ~9998 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube clé 3 — répond — ~9998 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube clé 4 — répond — ~9998 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube clé 5 — répond — ~9998 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube clé 6 — répond — ~9998 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube clé 7 — répond — ~9998 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube clé 8 — répond — ~9998 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube via yt-dlp (sans clé) — 3 résultat(s)
 ✔ Sous-titres YouTube : youtube-transcript-api — 173 lignes lues en 2s
 ✔ Sous-titres YouTube : yt-dlp — 172 lignes lues en 7s
 ✔ Sous-titres YouTube : yt-dlp (autres clients) — 172 lignes lues en 8s
 ✔ Sous-titres YouTube : Invidious — 112 lignes lues en 2s
 ✔ Dailymotion (recherche) — 10 résultat(s)
 ✔ ffmpeg — présent
 ✔ Téléchargement audio (Dailymotion) — 1457 Ko en 5s
Presse :
 ✔ Bing Actualités — 2 article(s)
 ✔ Google Actualités — 5 article(s)
https://api.gdeltproject.org/api/v2/doc/doc -> HTTP 429
https://api.gdeltproject.org/api/v2/doc/doc -> HTTP 429
