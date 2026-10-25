#!/data/data/com.termux/files/usr/bin/bash
# Un passage de précision. --auto : silencieux, et seulement si le dernier passage date de plus de 2 h.
QF="$HOME/.qf"; REPO="$(cat "$QF/repo" 2>/dev/null || echo "$HOME/vpf")"; LOG="$QF/precis.log"
mkdir -p "$QF"
# YouTube bloque parfois une connexion (ex. un wifi) et pas une autre (ex. la SIM) : la pause de 6 h après un refus
# est donc propre à chaque réseau, repéré par son adresse publique
NET="$(curl -s -m 10 https://api.ipify.org 2>/dev/null | tr -cd '0-9a-f.:')"; NET="${NET:-inconnu}"
# type de réseau pour ETAT.md (jamais l'adresse) : wifi si Termux:API le confirme, sinon « données mobiles ou wifi »
QF_NETTYPE="réseau"
if command -v termux-wifi-connectioninfo >/dev/null 2>&1; then
  if timeout 10 termux-wifi-connectioninfo 2>/dev/null | grep -q '"supplicant_state": "COMPLETED"'; then QF_NETTYPE="wifi"; else QF_NETTYPE="données mobiles"; fi
fi
export QF_NETTYPE
QF_AUTO_MODE="$(cat "$QF/auto_mode" 2>/dev/null || echo "à l'ouverture de Termux")"; export QF_AUTO_MODE
BLK="$QF/blocked_${NET//[:.]/_}"
if [ "${1:-}" = "--auto" ] && [ -f "$BLK" ] && [ "$(date +%s)" -lt "$(cat "$BLK")" ]; then exit 0; fi
if [ "${1:-}" = "--auto" ] && [ -f "$QF/last_ok" ] && [ $(( $(date +%s) - $(stat -c %Y "$QF/last_ok") )) -lt 7200 ] \
   && [ "$(cat "$QF/last_net" 2>/dev/null)" = "$NET" ]; then exit 0; fi
# un seul passage à la fois (verrou), un verrou de plus de 1 h est considéré comme abandonné
if ! mkdir "$QF/lock" 2>/dev/null; then
  [ $(( $(date +%s) - $(stat -c %Y "$QF/lock" 2>/dev/null || date +%s) )) -gt 3600 ] && rm -rf "$QF/lock" && mkdir "$QF/lock" || exit 0
fi
trap 'rm -rf "$QF/lock"' EXIT
# pas de réseau : on reporte (aucun réveil forcé du téléphone, compatible économie d'énergie)
curl -s -m 15 -o /dev/null https://github.com || { echo "$(date '+%F %T') pas de réseau, passage reporté" >> "$LOG"; exit 0; }
{
  echo "===== $(date '+%F %T')"
  # mise à jour du dépôt : faite par le programme lui-même (repart de GitHub sans jamais fusionner)
  cd "$REPO" || exit 1
  timeout 1800 python -m quote_finder.phone_precise --repo "$REPO" 2>&1 | grep -v "^ *$" | tail -15
} >> "$LOG" 2>&1
RC=$?
tail -n 3000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
touch "$QF/last_ok"; echo "$NET" > "$QF/last_net"
if tail -n 6 "$LOG" | grep -q "YouTube a limité"; then
  echo $(( $(date +%s) + 21600 )) > "$BLK"
  echo "$(date '+%F %T') réseau $NET refusé par YouTube : pause de 6 h sur ce réseau (les autres réseaux restent essayés)" >> "$LOG"
else
  rm -f "$BLK"
fi
find "$QF" -name 'blocked_*' -mtime +2 -delete 2>/dev/null
LAST="$(grep -E "extraits recalés" "$LOG" | tail -1)"
if [ "${1:-}" != "--auto" ]; then tail -n 12 "$LOG"; fi
if command -v termux-notification >/dev/null && echo "$LAST" | grep -qE "recalés : [1-9]"; then
  timeout 10 termux-notification --id 4242 --title "Vidéo-presse : extraits précisés" --content "$(echo "$LAST" | cut -c1-140)" >/dev/null 2>&1
fi
exit $RC
