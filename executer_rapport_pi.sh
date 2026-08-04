#!/bin/bash
# Équivalent de executer_rapport.sh, mais pour tourner sur le Pi (cron) —
# appelle envoyer_rapport_nas_pi.sh (rsync/SSH) plutôt que la version PC
# (powershell.exe/UNC, indisponible ici).
set -euo pipefail
cd "$(dirname "$0")"

if [ $# -ne 1 ]; then
    echo "Usage : $0 quotidien|hebdomadaire|mensuel" >&2
    exit 1
fi
PERIODE="$1"

SORTIE=$(.venv/bin/python generer_rapport.py "$PERIODE")
echo "$SORTIE"
CHEMIN_LOCAL=$(echo "$SORTIE" | sed -n 's/^Rapport généré : //p')

./envoyer_rapport_nas_pi.sh "$CHEMIN_LOCAL"
