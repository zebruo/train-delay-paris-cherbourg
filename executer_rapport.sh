#!/bin/bash
# Génère un rapport (quotidien, hebdomadaire ou mensuel) et l'envoie vers le
# NAS — pensé pour être appelé par le Planificateur de tâches Windows via
# wsl.exe. Génération manuelle depuis le PC : la planification automatique
# tourne désormais sur le Pi 4 (voir executer_rapport_pi.sh), ce script reste
# disponible pour un lancement ponctuel depuis le PC.
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

./envoyer_rapport_nas.sh "$CHEMIN_LOCAL"
