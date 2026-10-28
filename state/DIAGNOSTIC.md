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
Gemini gemini-3.8-flash surchargé : pause 2 min, on utilise d'autres modèles
 ✘ Gemini clé 1 — TimeoutError: plus de 40 s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
 ✘ Gemini clé 2 — TimeoutError: plus de 40 s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
Gemini gemini-3.8-flash surchargé : pause 2 min, on utilise d'autres modèles
 ✘ Gemini clé 3 — TimeoutError: plus de 40 s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
 ✘ Gemini clé 4 — TimeoutError: plus de 40 s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
 ✘ Gemini clé 5 — TimeoutError: plus de 40 s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
 ✘ Gemini clé 6 — TimeoutError: plus de 40 s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
Gemini gemini-3.8-flash surchargé : pause 2 min, on utilise d'autres modèles
Gemini gemini-3.7-flash surchargé : pause 2 min, on utilise d'autres modèles
 ✘ Gemini clé 7 — LLMError: HTTP 503 (gemini-3.7-flash): {
  "error": {
    "code": 503,
    "message": "This model is currently experienc — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
Gemini gemini-3.8-flash surchargé : pause 2 min, on utilise d'autres modèles
 ✘ Gemini clé 8 — TimeoutError: plus de 40 s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
Gemini gemini-3.8-flash surchargé : pause 2 min, on utilise d'autres modèles
Gemini gemini-3.8-flash surchargé : pause 2 min, on utilise d'autres modèles
Gemini gemini-3.7-flash surchargé : pause 2 min, on utilise d'autres modèles
Gemini gemini-3.8-flash surchargé : pause 2 min, on utilise d'autres modèles
 ✘ Gemini clé 9 — LLMError: HTTP 503 (gemini-3.7-flash): {
  "error": {
    "code": 503,
    "message": "This model is currently experienc — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
 ✘ Gemini clé 10 — TimeoutError: plus de 40 s — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
Modèle de secours llama-3.3-70b-versatile refusé, on change de modèle
 ✔ groq clé 1 — openai/gpt-oss-120b, 1s
Lecture d'articles par Gemini (pour les sites fermés aux robots et les liens Google Actualités) :
Gemini gemini-3.8-flash surchargé : pause 2 min, on utilise d'autres modèles
 ✘ https://www.reuters.com/world/ — TimeoutError: plus de 60 s
Gemini gemini-3.7-flash surchargé : pause 2 min, on utilise d'autres modèles
 ✘ lien Google Actualités — RuntimeError: page non lue (réponse vide ou « INACCESSIBLE »)
 ✘ vrai lien d'un article Google Actualités (recherche web Gemini) — LLMError: HTTP 429 (gemini-3.6-flash): {
  "error": {
    "code": 429,
    "message": "You exceeded your current quota, 
Vidéos :
 ✔ YouTube clé 1 — répond — ~9999 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube clé 2 — répond — ~9999 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube clé 3 — répond — ~9999 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube clé 4 — répond — ~9999 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube clé 5 — répond — ~9999 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube clé 6 — répond — ~9999 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube clé 7 — répond — ~9999 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube clé 8 — répond — ~9999 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube via yt-dlp (sans clé) — 3 résultat(s)
 ✔ Sous-titres YouTube : youtube-transcript-api — 173 lignes lues en 1s
 ✔ Sous-titres YouTube : yt-dlp — 172 lignes lues en 7s
 ✔ Sous-titres YouTube : yt-dlp (autres clients) — 172 lignes lues en 7s
 ✔ Sous-titres YouTube : Invidious — 112 lignes lues en 6s
 ✔ Dailymotion (recherche) — 10 résultat(s)
 ✔ ffmpeg — présent
 ✔ Téléchargement audio (Dailymotion) — 1457 Ko en 17s
Presse :
 ✔ Bing Actualités — 2 article(s)
 ✔ Google Actualités — 5 article(s)
https://api.gdeltproject.org/api/v2/doc/doc -> HTTP 429
 ✔ GDELT — 5 article(s)
 ⚠ Recherche Google par Gemini (vrais liens) — LLMError: HTTP 429 (gemini-3.8-flash): {
  "error": {
    "code": 429,
    "message": "You exceeded your current quota, 
État des clés (aussi dans state/QUOTAS.md après chaque recherche) :
# État des clés API
Mis à jour : 28/10/2026 13:33 UTC. Les quotas gratuits Google repartent à zéro à **minuit heure du Pacifique (9 h à Paris/Rome)**.
| Clé | État | Consommation | Erreurs | Dernier message |
|---|---|---|---|---|
| Gemini clé 1 | ✔ disponible | 0 appels — gemini-3.8-flash ×28, gemini-3.6-flash ×25, gemini-3-flash-preview ×6 aujourd'hui | 6 | gemini-3.8-flash: HTTP 429 { "error": { "code": 429, "message": "You exceeded your current |
| Gemini clé 10 | ✔ disponible | 0 appels — gemini-3-flash-preview ×4, gemini-3.6-flash ×2, gemini-3.7-flash ×2 aujourd'hui | 3 | gemini-3.8-flash: HTTP 429 { "error": { "code": 429, "message": "You exceeded your current |
| Gemini clé 2 | ✔ disponible | 0 appels — gemini-3.6-flash ×22, gemini-3.8-flash ×19, gemini-3-flash-preview ×3 aujourd'hui | 3 | gemini-3.8-flash: HTTP 429 { "error": { "code": 429, "message": "You exceeded your current |
| Gemini clé 3 | ✔ disponible | 0 appels — gemini-3.6-flash ×22, gemini-3.8-flash ×20, gemini-3.7-flash ×5 aujourd'hui | 6 | gemini-3.8-flash: HTTP 429 { "error": { "code": 429, "message": "You exceeded your current |
| Gemini clé 4 | ✔ disponible | 0 appels — gemini-3.6-flash ×17, gemini-3.8-flash ×15, gemini-3.7-flash ×7 aujourd'hui | 3 | gemini-3.6-flash: HTTP 429 { "error": { "code": 429, "message": "You exceeded your current |
| Gemini clé 5 | ✔ disponible | 1 appels — gemini-3.6-flash ×19, gemini-3.8-flash ×17, gemini-3-flash-preview ×7 aujourd'hui | 3 | gemini-3.6-flash: HTTP 429 { "error": { "code": 429, "message": "You exceeded your current |
| Gemini clé 6 | ✔ disponible | 0 appels — gemini-3-flash-preview ×5, gemini-3.6-flash ×3, gemini-3.8-flash ×3 aujourd'hui | 4 | gemini-3.8-flash: HTTP 429 { "error": { "code": 429, "message": "You exceeded your current |
| Gemini clé 7 | ✔ disponible | 1 appels — gemini-3.6-flash ×4, gemini-3.5-flash ×1, gemini-3.5-flash-lite ×1 aujourd'hui | 5 | gemini-3.8-flash: HTTP 429 { "error": { "code": 429, "message": "You exceeded your current |
| Gemini clé 8 | ✔ disponible | 0 appels — gemini-3.6-flash ×2, gemini-3.5-flash ×2, gemini-3.8-flash ×2 aujourd'hui | 5 | gemini-3.8-flash: HTTP 429 { "error": { "code": 429, "message": "You exceeded your current |
| Gemini clé 9 | ✔ disponible | 0 appels — gemini-3.6-flash ×2, gemini-3.5-flash ×2, gemini-3.7-flash ×1 aujourd'hui | 6 | gemini-3.8-flash: HTTP 429 { "error": { "code": 429, "message": "You exceeded your current |
| Groq clé 1 | ✔ disponible | 1 appels — openai/gpt-oss-120b ×35 aujourd'hui | 0 |  |
| Guardian clé 1 | ✔ disponible | 0 appels aujourd'hui | 0 |  |
| NYT clé 1 | ✔ disponible | 0 appels aujourd'hui | 0 |  |
| YouTube clé 1 | ✔ disponible | 1 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 2 | ✔ disponible | 1 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 3 | ✔ disponible | 1 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 4 | ✔ disponible | 1 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 5 | ✔ disponible | 1 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 6 | ✔ disponible | 1 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 7 | ✔ disponible | 1 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 8 | ✔ disponible | 1 unités / 10000 aujourd'hui | 0 |  |
1 problème(s) bloquant(s) ci-dessus : corrige-les avant de lancer une recherche.
Telegram : ❌ erreur error : unpack requires a buffer of 275 bytes
Reddit : ✅ accessible (22 résultat(s) test)
```
