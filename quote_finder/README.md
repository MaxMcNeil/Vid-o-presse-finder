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

## Version Termux (téléphone, adresse IP non bloquée par YouTube)

```bash
cd ~/vpf && bash termux/install.sh   # une seule fois
qf keys                              # enregistre tes clés (stockées dans ~/.quote_finder/keys.env)
qf                                   # recherche interactive
qf "Bill Clinton" "Saddam a des armes de destruction massive" "" n 30   # ou en une ligne
qf update                            # récupère la dernière version
```
Le rapport est copié dans Téléchargements et peut être envoyé sur GitHub. Sans compilation : `rapidfuzz` et `trafilatura` sont optionnels (repli en Python pur).

## Déclenchement automatique du téléphone depuis GitHub

*Run workflow* envoie une demande **signée** (HMAC, valable 10 min) au téléphone via ntfy.sh. Termux (`qf listen`) la reçoit, lance la recherche, pousse les résultats dans le dépôt et copie le rapport dans Téléchargements. Sans accusé de réception sous 100 s, GitHub fait la recherche lui-même (option `where` : `phone-then-github` par défaut, `github` ou `phone`).

1. Sur le téléphone : `qf phone-setup` (affiche `NTFY_TOPIC` et `PHONE_SECRET`) puis ajoute-les comme **secrets** GitHub.
2. `qf listen` laisse Termux à l'écoute (`qf autostart` pour le lancer au démarrage, avec l'appli Termux:Boot).
3. Android : *Paramètres → Applis → Termux → Batterie → Sans restriction*, sinon le système tue l'écoute en arrière-plan.

Sécurité : le sujet ntfy est aléatoire et chaque message est signé avec `PHONE_SECRET` ; un message non signé, modifié ou ancien est ignoré. Les arguments ne passent jamais par un shell.

## Extraits pour le montage

Chaque passage trouvé a un **début** et une **fin** (rapport, `results.json`, et `moments.csv` : une ligne par extrait avec `debut`, `fin`, `duree_s`, `citation`, `lien_extrait`). Les fins non fournies par l'IA sont estimées d'après la longueur de la citation ; les horodatages d'une analyse vidéo/audio par IA peuvent varier de quelques secondes, d'où les marges ajoutées au téléchargement.

Télécharger **uniquement** ces extraits (avec 2 s avant et 1 s après), sur le téléphone :
```bash
qf clips                                   # derniers résultats
qf clips --own-voice-only --min-confidence 0.9
```
Ils sont copiés dans `Movies` (ou Téléchargements) avec un fichier `montage_list.txt` : `ffmpeg -f concat -safe 0 -i montage_list.txt -c copy montage.mp4`. En local : `python -m quote_finder.clips results/<recherche>/results.json`.

## Diagnostic

`qf check` (ou `python -m quote_finder.check`) teste, depuis le téléphone, chaque clé et chaque service (Gemini, Groq, YouTube, sous-titres, Dailymotion, audio, presse, ntfy) et dit ce qui ne marche pas. Chaque `report.md` se termine par une section **Diagnostic** (plan IA ou de secours, vidéos par plateforme, méthodes d'analyse, durée). Une recherche est limitée à 40 min (`--max-minutes`) et s'arrête franchement si l'IA ne répond pas pour établir le plan.
