#!/bin/bash
# publish_lot.sh <LOT> [FIRST_ID] : enregistre dl/ (résultats) et dlq/ (quotas) dans results/<LOT>.
# Repart du dépôt à jour à chaque essai : jamais de conflit avec une écriture simultanée.
set -u
LOT="$1"; FIRST_ID="${2:-01-recherche}"
[ -d dl ] || { echo "::error::Aucun résultat produit (voir les étapes « Recherche »)"; exit 1; }
# Résultats et quotas restent dans dl/ et dlq/ (non suivis) : à chaque essai on repart du dépôt À JOUR,
# on y replace NOTRE lot, on refusionne les quotas et on régénère index et ETAT.md. Ainsi, une écriture
# faite entre-temps (téléphone, workflow État, autre recherche) n'est jamais écrasée ni en conflit.
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
for i in 1 2 3 4 5 6; do
  git fetch -q origin main && git reset -q --hard origin/main
  rm -rf "results/$LOT"; mkdir -p "results/$LOT"
  if ls dl/res-* >/dev/null 2>&1; then
    for d in dl/res-*; do n=$(basename "$d"); cp -r "$d" "results/$LOT/${n#res-}"; done
  else
    mkdir -p "results/$LOT/$FIRST_ID" && cp -r dl/. "results/$LOT/$FIRST_ID/"
  fi
  # lot lancé par OSINT-teleprompter- : on garde le lien (le montage s'en sert pour se synchroniser)
  if [ -n "${ORIGINE:-}" ] && [ "${ORIGINE}" != "null" ]; then printf '%s\n' "$ORIGINE" > "results/$LOT/ORIGINE.json"; fi
  files=$(find dlq -name quota.json 2>/dev/null)
  python3 -m quote_finder.quota merge state/quota.json $files > /dev/null
  python3 -m quote_finder.batch merge "results/$LOT" > /dev/null
  python3 -m quote_finder.status > /dev/null   # ETAT.md (garde la section « Workflows »)
  git add "results/$LOT" results/README.md state ETAT.md
  git diff --cached --quiet && break
  git commit -q -m "résultats : $LOT"
  if git push -q origin HEAD:main; then echo "Enregistré (essai $i)"; break; fi
  [ "$i" = 6 ] && { echo "::error::Impossible d'enregistrer les résultats (les fichiers restent dans l'artefact res-*)"; exit 1; }
  sleep $((i * 7))
done
{ cat ETAT.md; echo; cat "results/$LOT/INDEX.md"; } >> "$GITHUB_STEP_SUMMARY"
