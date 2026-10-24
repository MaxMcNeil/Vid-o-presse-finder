#!/data/data/com.termux/files/usr/bin/bash
# Quote Finder sur Termux.  qf | qf keys | qf update
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
CONF="$HOME/.quote_finder"
ENVF="$CONF/keys.env"
mkdir -p "$CONF"
[ -f "$ENVF" ] && . "$ENVF"

KEYVARS="GEMINI_API_KEYS YOUTUBE_API_KEYS FALLBACK_LLM_KEYS GUARDIAN_API_KEYS NYT_API_KEYS NTFY_TOPIC PHONE_SECRET"
ASKVARS="GEMINI_API_KEYS YOUTUBE_API_KEYS FALLBACK_LLM_KEYS GUARDIAN_API_KEYS NYT_API_KEYS"

save_keys() {
  : > "$ENVF"; chmod 600 "$ENVF"
  for v in $KEYVARS FALLBACK_LLM_PROVIDER; do
    [ -n "${!v}" ] && printf 'export %s="%s"\n' "$v" "${!v}" >> "$ENVF"
  done
}

case "$1" in
  keys|set)
    # Saisie multi-lignes : colle tes clés (une par ligne ou séparées par espaces/virgules), puis une LIGNE VIDE pour finir.
    # Entrée tout de suite = garder l'actuel / ignorer ce service.
    if [ "$1" = "set" ] && [ -n "$2" ]; then LIST="$2"; else LIST="$ASKVARS"; fi
    echo "Pour chaque service : colle la/les clé(s), puis appuie sur Entrée sur une ligne vide. Entrée seule = ignorer."
    for v in $LIST; do
      cur="${!v}"; hint=""; [ -n "$cur" ] && hint=" [déjà défini]"
      echo; echo ">> $v$hint"
      buf=""
      while IFS= read -r line; do
        [ -z "$line" ] && break
        buf="$buf $line"
      done
      if [ -n "$buf" ]; then
        printf -v "$v" '%s' "$(echo "$buf" | tr -s ' ,;\r' ',' | sed 's/^,//; s/,$//')"
        export "$v"
      fi
    done
    [ -n "$FALLBACK_LLM_KEYS" ] && [ -z "$FALLBACK_LLM_PROVIDER" ] && FALLBACK_LLM_PROVIDER=groq
    save_keys
    echo; echo "Enregistré dans $ENVF. Services définis :"
    for v in $KEYVARS; do [ -n "${!v}" ] && echo "  - $v ($(echo "${!v}" | tr ',' '\n' | wc -l) valeur(s))"; done
    exit 0 ;;
  clips)
    # qf clips [results.json] [--own-voice-only] [--min-confidence 0.8] : télécharge SEULEMENT les extraits (début -> fin)
    shift
    RJ="$1"
    if [ -z "$RJ" ] || [ "${RJ#--}" != "$RJ" ]; then
      RJ="$(ls -t "$REPO"/results/*/results.json 2>/dev/null | head -1)"
    else
      shift
    fi
    [ -z "$RJ" ] && { echo "Aucun results.json trouvé : lance d'abord une recherche."; exit 1; }
    cd "$REPO" && python -m quote_finder.clips "$RJ" --out "$HOME/qf_clips/$(basename "$(dirname "$RJ")")" "$@"
    D="$HOME/storage/movies"; [ -d "$D" ] || D="$HOME/storage/downloads"
    if [ -d "$D" ]; then
      cp -r "$HOME/qf_clips/$(basename "$(dirname "$RJ")")" "$D/" && echo "Copié dans $(basename "$D")/$(basename "$(dirname "$RJ")")"
    fi
    exit 0 ;;
  check)
    cd "$REPO" && exec python -m quote_finder.check ;;
  phone-setup)
    if [ -z "$NTFY_TOPIC" ] || [ -z "$PHONE_SECRET" ]; then
      NTFY_TOPIC="qf-$(python -c 'import secrets;print(secrets.token_hex(12))')"
      PHONE_SECRET="$(python -c 'import secrets;print(secrets.token_hex(16))')"
      save_keys
    fi
    echo "Ajoute ces 2 SECRETS dans GitHub (Settings > Secrets and variables > Actions) :"
    echo
    echo "  NTFY_TOPIC   = $NTFY_TOPIC"
    echo "  PHONE_SECRET = $PHONE_SECRET"
    echo
    echo "(appui long sur une valeur pour la copier). Ensuite :  qf listen"
    exit 0 ;;
  listen)
    [ -z "$NTFY_TOPIC" ] && { echo "Lance d'abord :  qf phone-setup"; exit 1; }
    command -v termux-wake-lock >/dev/null && termux-wake-lock
    export QF_CONF="$CONF" NTFY_TOPIC PHONE_SECRET
    cd "$REPO" && exec python -m quote_finder.phone ;;
  autostart)
    mkdir -p "$HOME/.termux/boot"
    printf '#!/data/data/com.termux/files/usr/bin/bash\ntermux-wake-lock\nqf listen >> %s/listen.log 2>&1\n' "$CONF" > "$HOME/.termux/boot/qf-listen.sh"
    chmod +x "$HOME/.termux/boot/qf-listen.sh"
    echo "Démarrage automatique configuré (nécessite l'appli Termux:Boot, installée depuis F-Droid, ouverte une fois)."
    exit 0 ;;
  update)
    cd "$REPO" && git pull --rebase -q && pip install -q -r requirements-termux.txt && echo "À jour."
    exit 0 ;;
esac

if [ -z "$GEMINI_API_KEYS" ]; then
  echo "Aucune clé Gemini. Lance d'abord :  qf keys"; exit 1
fi

if [ -n "$1" ] && [ -n "$2" ]; then
  PERSON="$1"; CLAIM="$2"; ALSO="$3"; ANYONE="${4:-n}"; MAXV="${5:-30}"
else
  read -rp "Personne (ex: Bill Clinton) : " PERSON
  read -rp "Sujet / affirmation : " CLAIM
  read -rp "Autres locuteurs acceptés, séparés par des virgules (Entrée = aucun) : " ALSO
  read -rp "Accepter n'importe quel locuteur ? (o/N) : " ANYONE
  read -rp "Nombre de vidéos à analyser [30] : " MAXV
  MAXV="${MAXV:-30}"
fi
[ -z "$PERSON" ] || [ -z "$CLAIM" ] && { echo "Personne et sujet obligatoires."; exit 1; }

args=(--person "$PERSON" --claim "$CLAIM" --also "$ALSO" --max-videos "$MAXV" --out "$REPO/results")
case "$ANYONE" in o|O|y|Y|oui|yes) args+=(--anyone) ;; esac

command -v termux-wake-lock >/dev/null && termux-wake-lock   # évite qu'Android coupe le calcul écran éteint
export QF_CACHE_DIR="$CONF/cache"
cd "$REPO" || exit 1
python -m quote_finder "${args[@]}" 2>&1 | tee "$CONF/last.log"
command -v termux-wake-unlock >/dev/null && termux-wake-unlock

OUT="$(ls -td "$REPO"/results/*/ 2>/dev/null | head -1)"
[ -z "$OUT" ] && { echo "Pas de rapport produit : voir $CONF/last.log"; exit 1; }
if [ -d "$HOME/storage/downloads" ]; then
  DEST="$HOME/storage/downloads/quote_finder_$(basename "$OUT").md"
  cp "$OUT/report.md" "$DEST" && echo "Rapport copié dans Téléchargements : $(basename "$DEST")"
fi
echo; head -n 60 "$OUT/report.md"
echo; read -rp "Envoyer les résultats sur GitHub ? (o/N) : " PUSH
case "$PUSH" in o|O|y|Y)
  git add results && git commit -qm "résultats (Termux)" && git pull --rebase -q && git push -q && echo "Envoyé." ;;
esac
