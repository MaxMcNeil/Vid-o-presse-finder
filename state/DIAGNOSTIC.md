# Dernier diagnostic

```
Quote Finder — diagnostic
Clés chargées :
 ✔ GEMINI — 5 clé(s)
 ✔ YOUTUBE — 5 clé(s)
 ✔ FALLBACK_LLM — 1 clé(s)
 ✔ GUARDIAN — 1 clé(s)
 ✔ NYT — 1 clé(s)
Gemini (chaque clé, chaque modèle a son propre quota gratuit) :
 ✘ Gemini clé 1 — LLMError: HTTPSConnectionPool(host='generativelanguage.googleapis.com', port=443): Read timed out. (read timeout=60) — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
 ✘ Gemini clé 2 — LLMError: HTTP 429 (gemini-3.8-flash): {
  "error": {
    "code": 429,
    "message": "You exceeded your current quota,  — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
Gemini gemini-3.8-flash surchargé : pause 2 min, on utilise d'autres modèles
 ✘ Gemini clé 3 — LLMError: HTTP 503 (gemini-3.8-flash): {
  "error": {
    "code": 503,
    "message": "This model is currently experienc — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
 ✘ Gemini clé 4 — LLMError: HTTP 429 (gemini-3.7-flash): {
  "error": {
    "code": 429,
    "message": "You exceeded your current quota,  — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
 ✘ Gemini clé 5 — LLMError: HTTP 429 (gemini-3.7-flash): {
  "error": {
    "code": 429,
    "message": "You exceeded your current quota,  — 14 modèle(s) vidéo/texte, 2 Gemma (texte)
Modèle de secours llama-3.3-70b-versatile refusé, on change de modèle
 ✔ groq clé 1 — openai/gpt-oss-120b, 1s
Lecture d'articles par Gemini (pour les sites fermés aux robots et les liens Google Actualités) :
 ✘ https://www.reuters.com/world/ — RuntimeError: page non lue (réponse vide ou « INACCESSIBLE »)
 ✘ lien Google Actualités — RuntimeError: page non lue (réponse vide ou « INACCESSIBLE »)
Vidéos :
 ✔ YouTube clé 1 — répond — ~8881 unités restantes aujourd'hui (≈ 88 recherches)
 ✔ YouTube clé 2 — répond — ~8781 unités restantes aujourd'hui (≈ 87 recherches)
 ✔ YouTube clé 3 — répond — ~8881 unités restantes aujourd'hui (≈ 88 recherches)
 ✔ YouTube clé 4 — répond — ~8981 unités restantes aujourd'hui (≈ 89 recherches)
 ✔ YouTube clé 5 — répond — ~8882 unités restantes aujourd'hui (≈ 88 recherches)
 ✔ YouTube via yt-dlp (sans clé) — 3 résultat(s)
 ✔ Sous-titres YouTube — 61 lignes lues
 ✔ Dailymotion (recherche) — 10 résultat(s)
 ✔ ffmpeg — présent
 ✔ Téléchargement audio (Dailymotion) — 1457 Ko en 8s
Presse :
 ✔ Bing Actualités — 5 article(s)
 ✔ Google Actualités — 5 article(s)
https://api.gdeltproject.org/api/v2/doc/doc -> HTTP 429
https://api.gdeltproject.org/api/v2/doc/doc -> HTTP 429
 ⚠ GDELT — 0 résultat (souvent un blocage temporaire)
État des clés (aussi dans state/QUOTAS.md après chaque recherche) :
# État des clés API
Mis à jour : 25/10/2026 09:42 UTC. Les quotas gratuits Google repartent à zéro à **minuit heure du Pacifique (9 h à Paris/Rome)**.
| Clé | État | Consommation | Erreurs | Dernier message |
|---|---|---|---|---|
| Gemini clé 1 | ✔ disponible | 19 appels — gemini-3.8-flash ×22, gemini-3.6-flash ×21, gemini-3-flash-preview ×3 aujourd'hui | 11 | gemini-3.7-flash: HTTP 503 { "error": { "code": 503, "message": "This model is currently e |
| Gemini clé 2 | ✔ disponible | 13 appels — gemini-3.6-flash ×18, gemini-3.8-flash ×14, gemini-3-flash-preview ×2 aujourd'hui | 13 | gemini-3.8-flash: HTTP 429 { "error": { "code": 429, "message": "You exceeded your current |
| Gemini clé 3 | ✔ disponible | 18 appels — gemini-3.6-flash ×19, gemini-3.8-flash ×16, gemini-3.7-flash ×4 aujourd'hui | 12 | gemini-3.8-flash: HTTP 503 { "error": { "code": 503, "message": "This model is currently e |
| Gemini clé 4 | ✔ disponible | 10 appels — gemini-3.6-flash ×15, gemini-3.8-flash ×13, gemini-3-flash-preview ×6 aujourd'hui | 18 | gemini-3.7-flash: HTTP 429 { "error": { "code": 429, "message": "You exceeded your current |
| Gemini clé 5 | ✔ disponible | 13 appels — gemini-3.6-flash ×18, gemini-3.8-flash ×11, gemini-3.7-flash ×6 aujourd'hui | 17 | gemini-3.7-flash: HTTP 429 { "error": { "code": 429, "message": "You exceeded your current |
| Groq clé 1 | ✔ disponible | 8 appels — openai/gpt-oss-120b ×28 aujourd'hui | 0 |  |
| Guardian clé 1 | ✔ disponible | 0 appels aujourd'hui | 0 |  |
| NYT clé 1 | ✔ disponible | 0 appels aujourd'hui | 0 |  |
| YouTube clé 1 | ✔ disponible | 1119 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 2 | ✔ disponible | 1219 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 3 | ✔ disponible | 1119 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 4 | ✔ disponible | 1019 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 5 | ✔ disponible | 1118 unités / 10000 aujourd'hui | 0 |  |
1 problème(s) bloquant(s) ci-dessus : corrige-les avant de lancer une recherche.
```
