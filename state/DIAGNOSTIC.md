# Dernier diagnostic

```
Quote Finder — diagnostic
Clés chargées :
 ✔ GEMINI — 1 clé(s)
 ✔ YOUTUBE — 1 clé(s)
 ✔ FALLBACK_LLM — 1 clé(s)
 ✔ GUARDIAN — 1 clé(s)
 ✔ NYT — 1 clé(s)
Gemini (chaque clé, chaque modèle a son propre quota gratuit) :
Gemini gemini-3.8-flash surchargé : pause 2 min, on utilise d'autres modèles
 ⚠ Gemini clé 1 — clé valide (47 modèles), mais pas de réponse maintenant : Google surchargé en ce moment (503 / lenteur) — pas un problème de clé, la rotation passera aux autres modèles / clés
Modèle de secours llama-3.3-70b-versatile refusé, on change de modèle
 ✔ groq clé 1 — openai/gpt-oss-120b, 1s
 ⚠ IA — Google surchargé en ce moment : le relais (Groq…) répond, les recherches marchent (l'analyse directe des vidéos reprendra quand Google répondra)
Lecture d'articles par Gemini (pour les sites fermés aux robots et les liens Google Actualités) :
 ⚠ https://www.reuters.com/world/ — TimeoutError: plus de 60 s (passager le plus souvent : non bloquant)
Gemini gemini-3.8-flash surchargé : pause 2 min, on utilise d'autres modèles
Gemini gemini-3.7-flash surchargé : pause 2 min, on utilise d'autres modèles
 ⚠ lien Google Actualités — RuntimeError: page non lue (réponse vide ou « INACCESSIBLE ») (passager le plus souvent : non bloquant)
Gemini : gemini-2.5-flash inaccessible avec la clé …hpsw, on passe à un autre modèle
Gemini : gemini-2.5-flash-lite inaccessible avec la clé …hpsw, on passe à un autre modèle
 ⚠ vrai lien d'un article Google Actualités (recherche web Gemini) — LLMError: HTTP 404 (gemini-2.5-flash-lite): {
  "error": {
    "code": 404,
    "message": "This model models/gemini-2.5 (non bloquant)
Vidéos :
 ✔ YouTube clé 1 — répond — ~9999 unités restantes aujourd'hui (≈ 99 recherches)
 ✔ YouTube via yt-dlp (sans clé) — 3 résultat(s)
 ✔ Sous-titres YouTube : youtube-transcript-api — 173 lignes lues en 1s
 ✔ Sous-titres YouTube : yt-dlp — 172 lignes lues en 7s
 ✔ Sous-titres YouTube : yt-dlp (autres clients) — 172 lignes lues en 9s
 ✔ Sous-titres YouTube : Invidious — 112 lignes lues en 1s
 ✔ Dailymotion (recherche) — 10 résultat(s)
 ✔ ffmpeg — présent
 ✔ Téléchargement audio (Dailymotion) — 1457 Ko en 16s
Presse :
 ✔ Bing Actualités — 5 article(s)
 ✔ Google Actualités — 5 article(s)
https://api.gdeltproject.org/api/v2/doc/doc -> HTTP 429
 ✔ GDELT — 5 article(s)
 ⚠ Recherche Google par Gemini (vrais liens) — LLMError: HTTP 429 (gemini-3.1-flash-lite-preview): {
  "error": {
    "code": 429,
    "message": "You exceeded your cu
État des clés (aussi dans state/QUOTAS.md après chaque recherche) :
# État des clés API
Mis à jour : 09/11/2026 10:02 UTC. Les quotas gratuits Google repartent à zéro à **minuit heure du Pacifique (9 h à Paris/Rome)**.
| Clé | État | Consommation | Erreurs | Dernier message |
|---|---|---|---|---|
| Gemini clé 1 | ✔ disponible | 0 appels — gemini-3.8-flash ×28, gemini-3.6-flash ×25, gemini-3-flash-preview ×6 aujourd'hui | 0 | gemini-3-flash-preview: HTTP 429 { "error": { "code": 429, "message": "You exceeded your c |
| Gemini clé 1 | ✔ disponible | 1 appels — gemini-3.6-flash ×1 aujourd'hui | 23 | gemini-3.1-flash-lite-preview: HTTP 429 { "error": { "code": 429, "message": "You exceeded |
| Gemini clé 10 | ✔ disponible | 0 appels — gemini-3-flash-preview ×5, gemini-3.6-flash ×2, gemini-3.7-flash ×2 aujourd'hui | 0 | gemini-3.6-flash: HTTP 429 { "error": { "code": 429, "message": "You exceeded your current |
| Gemini clé 2 | ✔ disponible | 0 appels — gemini-3.6-flash ×22, gemini-3.8-flash ×19, gemini-3-flash-preview ×3 aujourd'hui | 0 | gemini-3-flash-preview: HTTP 429 { "error": { "code": 429, "message": "You exceeded your c |
| Gemini clé 3 | ✔ disponible | 0 appels — gemini-3.6-flash ×22, gemini-3.8-flash ×20, gemini-3.7-flash ×5 aujourd'hui | 0 | gemini-3-flash-preview: HTTP 429 { "error": { "code": 429, "message": "You exceeded your c |
| Gemini clé 4 | ✔ disponible | 0 appels — gemini-3.6-flash ×17, gemini-3.8-flash ×15, gemini-3.7-flash ×7 aujourd'hui | 0 | gemini-3-flash-preview: HTTP 429 { "error": { "code": 429, "message": "You exceeded your c |
| Gemini clé 5 | ✔ disponible | 0 appels — gemini-3.6-flash ×19, gemini-3.8-flash ×17, gemini-3-flash-preview ×7 aujourd'hui | 0 | gemini-3-flash-preview: HTTP 429 { "error": { "code": 429, "message": "You exceeded your c |
| Gemini clé 6 | ✔ disponible | 0 appels — gemini-3-flash-preview ×5, gemini-3.6-flash ×3, gemini-3.8-flash ×3 aujourd'hui | 0 | gemini-3-flash-preview: HTTP 429 { "error": { "code": 429, "message": "You exceeded your c |
| Gemini clé 7 | ✔ disponible | 0 appels — gemini-3.6-flash ×4, gemini-3.5-flash ×1, gemini-3.5-flash-lite ×1 aujourd'hui | 0 | gemini-3-flash-preview: HTTP 429 { "error": { "code": 429, "message": "You exceeded your c |
| Gemini clé 8 | ✔ disponible | 0 appels — gemini-3.6-flash ×2, gemini-3.5-flash ×2, gemini-3.8-flash ×2 aujourd'hui | 0 | gemini-3-flash-preview: HTTP 429 { "error": { "code": 429, "message": "You exceeded your c |
| Gemini clé 9 | ✔ disponible | 0 appels — gemini-3.6-flash ×2, gemini-3.5-flash ×2, gemini-3.7-flash ×1 aujourd'hui | 0 | gemini-3.5-flash: HTTP 503 { "error": { "code": 503, "message": "This model is currently e |
| Groq clé 1 | ✔ disponible | 1 appels — openai/gpt-oss-120b ×37 aujourd'hui | 0 |  |
| Guardian clé 1 | ✔ disponible | 0 appels aujourd'hui | 0 |  |
| NYT clé 1 | ✔ disponible | 0 appels aujourd'hui | 0 |  |
| YouTube clé 1 | ✔ disponible | 0 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 1 | ✔ disponible | 1 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 2 | ✔ disponible | 0 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 3 | ✔ disponible | 0 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 5 | ✔ disponible | 0 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 6 | ✔ disponible | 0 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 7 | ✔ disponible | 0 unités / 10000 aujourd'hui | 0 |  |
| YouTube clé 8 | ✔ disponible | 0 unités / 10000 aujourd'hui | 0 |  |
Tout l'essentiel fonctionne.
Telegram : ✅ connecté (compte Kosta)
Reddit : ✅ accessible (22 résultat(s) test)
```
