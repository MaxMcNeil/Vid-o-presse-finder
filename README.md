# Quote Finder

Entre une **personne** et un **sujet / une affirmation** (un mot, une phrase, un thème, un pays, une affirmation, court ou long).
L'automate trouve **où et quand** elle le dit : vidéos (YouTube, Dailymotion, Internet Archive) et articles de presse. Gratuit, aucune API payante.

Exemple : `Bill Clinton` + `Saddam a des armes de destruction massive`.

## Ce que tu obtiens

**Vidéos** : titre · date d'upload · timestamp(s) (un lien cliquable `&t=…s` par occurrence si la phrase est répétée) · URL · citation · « à la lettre » ou « dans le sens » · qui parle · confiance.
**Articles** : titre · date · source · URL (+ avertissement si seul le chapô a pu être lu).
Fichiers générés dans `results/<recherche>/` : `report.md`, `results.json`, `videos.csv`, `articles.csv`.

## Utilisation (100 % depuis GitHub, même sur mobile)

1. Ajoute les secrets : *Settings → Secrets and variables → Actions → New repository secret*
2. *Actions → Find quotes → Run workflow*, remplis personne + sujet.
3. Résultat dans le résumé du job, dans `results/` (commit automatique) et dans l'artefact `resultats`.

Options du formulaire : autres locuteurs acceptés (`Colin Powell, Tony Blair`), « n'importe qui » (`anyone`), langues, nombre de vidéos, `strict` (exclut les propos rapportés par un tiers), articles oui/non.

En local : `pip install -r requirements.txt` puis
`python -m quote_finder --person "Bill Clinton" --claim "Saddam a des armes de destruction massive" --also "Colin Powell"`

## Secrets à créer (plusieurs clés par secret : une par ligne ou séparées par des virgules)

| Secret | Rôle | Obligatoire |
|---|---|---|
| `GEMINI_API_KEYS` | comprendre la recherche, juger le contenu, analyser les vidéos sans sous-titres | oui |
| `YOUTUBE_API_KEYS` | recherche YouTube (sans clé : repli yt-dlp, moins fiable depuis GitHub) | recommandé |
| `FALLBACK_LLM_KEYS` + variable `FALLBACK_LLM_PROVIDER` (`groq`, `cerebras` ou `openrouter`) | relais gratuit quand Gemini est à court de quota (texte seulement) | recommandé |
| `GUARDIAN_API_KEYS` | archives presse complètes depuis 1999, texte intégral | optionnel |
| `NYT_API_KEYS` | archives du New York Times (résumés) | optionnel |
| `YT_PROXY_URL` | proxy si YouTube bloque les transcriptions depuis GitHub | optionnel |

Variable optionnelle `GEMINI_MODEL` (défaut `gemini-2.5-flash`) : à changer si Google renomme ses modèles gratuits.

## Comment ça marche

1. **Plan (Gemini)** : reformule la recherche, génère requêtes multilingues, synonymes, variantes de transcription.
2. **Découverte** : YouTube (API en rotation de clés, repli yt-dlp), Dailymotion, Internet Archive ; presse : GDELT, Bing Actualités, Google News, Guardian, NYT.
3. **Le contenu prime sur le titre** : la transcription de chaque vidéo est lue. Un filtre local gratuit repère les passages contenant les concepts recherchés (tolère les erreurs de transcription auto), puis Gemini ne juge que ces passages : affirmation à la lettre / dans le sens / simple mention / démenti, et **qui** parle (la personne elle-même, un tiers, ou propos rapportés).
4. **Sans transcription** : YouTube → Gemini analyse directement la vidéo (`--video-fallback-max`) ; Dailymotion/Archive → l'audio est téléchargé (yt-dlp + ffmpeg) puis écouté par Gemini (`--audio-fallback-max`, vidéos de 45 min max).
5. **Quotas** : rotation des clés, cadence par clé, quarantaine sur quota journalier, bascule vers le LLM de repli, cache des transcriptions et réponses entre exécutions.

## Limites à connaître

- Les sous-titres automatiques n'identifient pas le locuteur : il est déduit du contexte. Les propos « rapportés » ou « locuteur incertain » sont étiquetés ; `strict` les exclut.
- YouTube bloque parfois les IP de GitHub pour les transcriptions : le repli yt-dlp, l'analyse vidéo Gemini ou `YT_PROXY_URL` compensent. Les vidéos non vérifiables sont listées à part.
- GDELT ne couvre que ~3 mois ; pour les archives anciennes (ex. 1998), les clés Guardian/NYT sont utiles.
- Si Gemini est surchargé (erreur 503), le programme change de modèle automatiquement ; garde quand même `FALLBACK_LLM_KEYS` (Groq) comme filet de sécurité.
- Articles sous paywall : vérifiés sur le chapô seulement (signalé).
