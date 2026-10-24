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
| `timing` | `sous-titres` : calé sur la transcription (±1–2 s) ; `ia-affiné` : vidéo YouTube réécoutée par Gemini sur une fenêtre de quelques secondes autour du passage (±1 s) ; `ia` : estimé par l'IA (±5 s, marges plus larges). Les passages « non retrouvés » à la réécoute sont exclus du montage |
| `video_id`, `source_url`, `platform` | pour télécharger la source |
| `quote`, `speaker`, `speaker_role`, `verdict`, `confidence` | ce qui est dit, par qui (officiel, expert, journaliste…) |
| `source_authority`, `score`, `order` | solidité de la source et ordre conseillé |

Exemple pour couper un extrait à la main : `yt-dlp --download-sections "*102.2-117.8" <source_url>`.
Automatiquement : `python -m quote_finder.clips results/<lot>/montage.json` (ou l'option `telecharger_extraits`).

## Secrets à créer

*Settings → Secrets and variables → Actions → New repository secret*. Plusieurs clés dans un même secret : une par ligne (ou séparées par des virgules).

| Secret | Rôle | Obligatoire |
|---|---|---|
| `GEMINI_API_KEYS` | comprendre la recherche, juger le contenu, analyser les vidéos sans sous-titres | oui |
| `YOUTUBE_API_KEYS` | recherche YouTube + abonnés des chaînes (score d'autorité) | fortement conseillé |
| `GROQ_API_KEYS` | relais gratuit n° 1 quand toutes les clés Gemini sont vides (texte) | conseillé |
| `CEREBRAS_API_KEYS` | relais gratuit n° 2 | optionnel |
| `OPENROUTER_API_KEYS` | relais gratuit n° 3 (modèles `:free`) | optionnel |
| `GUARDIAN_API_KEYS` / `NYT_API_KEYS` | archives de presse | optionnel |
| `YT_PROXY_URL` | proxy si YouTube bloque les transcriptions depuis GitHub | optionnel |

Une clé déjà rangée dans l'ancien secret `FALLBACK_LLM_KEYS` est reconnue toute seule (`gsk_…` = Groq, `csk-…` = Cerebras, `sk-or-…` = OpenRouter) : pas besoin de la déplacer.

⚠️ **Une clé = un projet Google différent.** Le quota gratuit de Gemini et celui de YouTube sont comptés **par projet Google Cloud**,
pas par clé : 5 clés créées dans le même projet partagent un seul quota. Dans Google AI Studio, crée chaque clé avec
« Create API key in new project » ; pour YouTube, une clé dans 5 projets différents (API « YouTube Data API v3 » activée dans chacun).

## Gestion des quotas (automatique)

L'ordre d'utilisation, pour avoir le maximum de chances :

1. **Gemini** : les 5 clés en rotation (une requête sur chaque à tour de rôle, pour ne pas toucher la limite par minute).
   Sur chaque clé, tous les modèles gratuits accessibles sont découverts et utilisés du meilleur au plus léger :
   `flash` → `flash-lite` → **Gemma** (texte seulement, quota très large). Chaque modèle a son propre quota :
   quand l'un est vide sur une clé, on passe au modèle suivant, puis à la clé suivante.
2. **Relais** quand tout Gemini est vide : Groq → Cerebras → OpenRouter (texte seulement).
3. **YouTube** : les 5 clés en rotation ; chaque appel est compté (recherche = 100 unités sur 10 000 par jour et par clé) ;
   une clé presque vide est laissée de côté ; quand toutes sont vides → recherche par yt-dlp (gratuite, sans clé).
   Une même recherche relancée dans les 36 h est servie depuis le cache (0 unité).
4. **Mémoire entre les lancements** : `state/quota.json` (enregistré dans le dépôt) retient quelles clés ou quels modèles
   sont vides jusqu'à la remise à zéro (minuit heure du Pacifique = 9 h en Italie/France). Le lancement suivant ne perd
   pas de temps avec eux. Les clés n'y figurent jamais en clair (seulement « Gemini clé 2 »…).
5. **Recherches en parallèle** : chaque job commence par une clé différente (job 1 → clé 1, job 2 → clé 2…).

**Savoir quelles clés sont disponibles** : ouvre `state/QUOTAS.md` (mis à jour après chaque lancement et chaque Diagnostic),
ou lance *Actions → Diagnostic* qui teste chaque clé de chaque api et donne les unités YouTube restantes.

## Comment ça marche

1. **Plan (IA)** : type de cible, alias (ex. *Suisse, Swiss, helvétique*), **représentants officiels** (Conseil fédéral, ministres…), **sources de référence** pour ce sujet (ex. RTS, Le Temps, admin.ch), requêtes multilingues orientées vers ces sources.
2. **Tri par pertinence** : un filtre local écarte ce qui n'a rien à voir, puis l'IA note en un seul appel les 60 meilleurs
   titres (0 à 10) ; seuls ceux notés ≥ 6 sont analysés. Avec l'autorité « forte », si les sources fortes pertinentes manquent,
   des sources moyennes très pertinentes (≥ 7/10) complètent, signalées dans le rapport.
3. **Recherche directe chez les sources de référence** (`quote_finder/registry.py`, par pays : agences, médias publics,
   institutions — complété à chaque recherche par l'IA) : articles « mots-clés site:rts.ch… », **vidéos de leurs sites**
   (RTS, SRF, franceinfo, BBC, Tagesschau…, lues par yt-dlp, souvent avec sous-titres = horodatage précis, sans le blocage
   YouTube) et recherche **dans** leurs chaînes YouTube officielles (0 unité de quota). Pour ajouter un pays ou un média :
   une ligne dans `registry.py`.
4. **Découverte YouTube / Dailymotion / Archive** puis **score d'autorité** de chaque source : liste de médias et d'institutions, domaines officiels (`.gouv.fr`, `admin.ch`, `.gov`, `europa.eu`…), abonnés de la chaîne ; pénalités pour les #shorts et les titres racoleurs. Seules les meilleures sont analysées.
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
- Gemini gratuit limite la quantité de vidéo envoyée **par minute** : les vidéos sont envoyées à 1 image / 4 s (la parole
  compte, pas l'image), les vidéos de plus de 60 min sans sous-titres sont ignorées, et les vidéos courtes sont préférées.
- Depuis GitHub, YouTube bloque sous-titres et téléchargements (« Sign in to confirm you're not a bot », vérifié le 24/10/2026).
  Contournement gratuit intégré : Gemini (qui lit la vidéo chez Google) analyse la vidéo, puis **réécoute uniquement
  la fenêtre de chaque passage** pour l'horodater au dixième de seconde (`timing = ia-affiné`) et vérifier qu'il existe.
- Avec `autorite = forte`, une recherche sur un sujet peu couvert peut ne rien rendre : relance en `moyenne`. Le rapport indique combien de sources ont été écartées pour autorité insuffisante.
- Articles sous paywall : vérifiés sur le chapô seulement (signalé), ou listés « à vérifier à la main ».
