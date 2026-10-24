# Vidéo-presse finder

Tu donnes une **cible** (une **personne**, un **pays**, une **entité** ou un **sujet**) et, si tu veux, un **sujet précis**.
L'automate trouve **où et quand** c'est dit, dans des vidéos (YouTube, Dailymotion, Internet Archive) et des articles de presse.
Il garde **peu de résultats, mais solides** : agences, grands médias, chaînes officielles, institutions.
Tout tourne sur **GitHub** et les résultats sont enregistrés **uniquement dans le dépôt** (`results/`).

## Lancer une ou plusieurs recherches (depuis le téléphone)

*Actions → Find quotes → Run workflow*, puis remplis le champ **recherches** :

```
La Suisse | abandonne Microsoft pour la souveraineté numérique | pays ; Jordan Bardella | mensonges ; Microsoft | espionnage des données | entite
```

- Une recherche = `cible | sujet | type`. Plusieurs recherches : sépare-les par `;` (10 maximum par lancement).
- Le **sujet** est facultatif (`souveraineté numérique européenne` seul est une recherche de type sujet).
- Le **type** est facultatif : `personne`, `pays`, `entite`, `sujet`. Sans type, l'IA le devine.
- Champ vide : les recherches du fichier `recherches.txt` sont utilisées (une par ligne, modifiable sur GitHub).

Les autres réglages du formulaire :

| Réglage | Défaut | Rôle |
|---|---|---|
| `locuteurs` | auto | `cible` : seulement la cible (ou, pour un pays/une entité, ses représentants officiels : gouvernement, ministres, PDG…). `tous` : n'importe qui, à condition de faire autorité. `auto` : `cible` pour une personne, `tous` sinon |
| `autorite` | forte | `forte` : agences, médias de référence, officiels. `moyenne` : élargit aux médias nationaux et aux grosses chaînes. `toutes` : aucun filtre |
| `videos_par_recherche` | 8 | vidéos analysées (les plus solides) |
| `articles_par_recherche` | 6 | articles retenus |
| `langs` | fr,en | langues des requêtes et des sous-titres |
| `depuis` | — | ignore ce qui est plus ancien (AAAA-MM-JJ) |
| `telecharger_extraits` | non | télécharge les extraits vidéo et un premier `montage.mp4`, fournis en **artefact** GitHub (14 jours, jamais dans le dépôt) |
| `en_parallele` | 2 | recherches menées en même temps (les quotas Gemini sont partagés : 2 ou 3 maximum) |

Chaque recherche tourne dans son propre job (visible séparément dans *Actions*). Un dernier job rassemble tout et fait **un seul commit**.

## Où sont les résultats

```
results/
  README.md                        ← liste des lots, le plus récent en haut
  2026-10-24_1415_n12/             ← un lot = un lancement
    INDEX.md                       ← tableau des recherches + meilleurs extraits
    montage.json / montage.csv     ← TOUS les extraits du lot, du plus solide au moins solide
    01-la-suisse-abandonne-microsoft/
      report.md                    ← rapport lisible
      montage.json / montage.csv   ← extraits de cette recherche
      moments.csv, videos.csv, articles.csv, results.json
```

Le résumé du job (*Actions → le lancement → Summary*) affiche aussi l'index et les rapports.

## Timestamps pour le montage

Chaque extrait de `montage.json` / `montage.csv` contient :

| Champ | Contenu |
|---|---|
| `start`, `end` (+ `start_tc`, `end_tc`) | début et fin **exacts** de la déclaration, en secondes et en `HH:MM:SS.mmm` |
| `in`, `out` (+ `in_tc`, `out_tc`) | mêmes points **avec marges** (prêts à couper) |
| `timing` | `sous-titres` : calé sur la ligne de transcription où la phrase commence et finit (±1–2 s, marges 1 s / 0,8 s) ; `ia` : estimé par l'IA quand la vidéo n'a pas de sous-titres (±5 s, marges 2,5 s / 2 s) |
| `video_id`, `source_url`, `platform` | pour télécharger la source |
| `quote`, `speaker`, `speaker_role`, `verdict`, `confidence` | ce qui est dit, par qui (officiel, expert, journaliste…) |
| `source_authority`, `score`, `order` | solidité de la source et ordre conseillé |

Exemple pour couper un extrait à la main : `yt-dlp --download-sections "*102.2-117.8" <source_url>`.
Automatiquement : `python -m quote_finder.clips results/<lot>/montage.json` (ou l'option `telecharger_extraits`).

## Secrets à créer

*Settings → Secrets and variables → Actions → New repository secret* (plusieurs clés par secret : une par ligne ou séparées par des virgules).

| Secret | Rôle | Obligatoire |
|---|---|---|
| `GEMINI_API_KEYS` | comprendre la recherche, juger le contenu, analyser les vidéos sans sous-titres | oui |
| `YOUTUBE_API_KEYS` | recherche YouTube + nombre d'abonnés des chaînes (sert au score d'autorité) | fortement conseillé |
| `FALLBACK_LLM_KEYS` + variable `FALLBACK_LLM_PROVIDER` (`groq`, `cerebras` ou `openrouter`) | relais gratuit quand Gemini est à court de quota (texte seulement) | conseillé |
| `GUARDIAN_API_KEYS` | archives du Guardian, texte intégral | optionnel |
| `NYT_API_KEYS` | archives du New York Times (résumés) | optionnel |
| `YT_PROXY_URL` | proxy si YouTube bloque les transcriptions depuis GitHub | optionnel |

Les anciens secrets `NTFY_TOPIC` et `PHONE_SECRET` ne servent plus : tu peux les supprimer.

## Comment ça marche

1. **Plan (IA)** : type de cible, alias (ex. *Suisse, Swiss, helvétique*), **représentants officiels** (Conseil fédéral, ministres…), **sources de référence** pour ce sujet (ex. RTS, Le Temps, admin.ch), requêtes multilingues orientées vers ces sources.
2. **Découverte** puis **score d'autorité** de chaque source : liste de médias et d'institutions, domaines officiels (`.gouv.fr`, `admin.ch`, `.gov`, `europa.eu`…), abonnés de la chaîne ; pénalités pour les #shorts et les titres racoleurs. Seules les meilleures sont analysées.
3. **Le contenu prime sur le titre** : la transcription est lue, un filtre local repère les passages utiles, l'IA juge (à la lettre / dans le sens / simple mention / démenti), dit **qui** parle et son rôle.
4. **Horodatage calé** sur les lignes de sous-titres, puis classement : sûreté du passage + autorité de la source + rôle du locuteur.
5. Sans sous-titres : Gemini regarde la vidéo (YouTube) ou écoute l'audio (Dailymotion, Archive).
6. Garde-fous : 5 min max par vidéo analysée par IA, 35 min max par recherche, une recherche en échec n'empêche pas les autres d'être enregistrées.

## Diagnostic

*Actions → Diagnostic → Run workflow* teste chaque clé et chaque service depuis GitHub et dit ce qui ne marche pas.

## En local (facultatif)

```bash
pip install -r requirements.txt
python -m quote_finder --target "La Suisse" --claim "abandonne Microsoft" --type pays
python -m quote_finder --batch "La Suisse | abandonne Microsoft | pays ; Jordan Bardella | mensonges"
python -m pytest -q          # tests (pip install -r requirements-dev.txt)
```

## Limites

- Les sous-titres automatiques n'indiquent pas qui parle : le locuteur est déduit du contexte ; « propos rapportés » et « locuteur incertain » sont signalés.
- YouTube bloque parfois les transcriptions depuis GitHub : l'analyse vidéo par Gemini prend le relais (horodatage moins précis, `timing = ia`).
- Avec `autorite = forte`, une recherche sur un sujet peu couvert peut ne rien rendre : relance en `moyenne`. Le rapport indique combien de sources ont été écartées pour autorité insuffisante.
- Articles sous paywall : vérifiés sur le chapô seulement (signalé), ou listés « à vérifier à la main ».
