#!/bin/bash
# Génère un rapport (quotidien, hebdomadaire ou mensuel) et l'envoie vers le
# NAS — pensé pour être appelé par le Planificateur de tâches Windows via
# wsl.exe. Génération manuelle depuis le PC : la planification automatique
# tourne désormais sur le Pi 4 (voir executer_rapport_pi.sh), ce script reste
# disponible pour un lancement ponctuel depuis le PC.
set -euo pipefail
cd "$(dirname "$0")"
source config.sh

if [ $# -ne 1 ]; then
    echo "Usage : $0 quotidien|hebdomadaire|mensuel" >&2
    exit 1
fi
PERIODE="$1"

# Rapatrie les données depuis la VPS avant de générer — même besoin
# qu'executer_rapport_pi.sh (voir son commentaire), oublié ici jusqu'à
# présent : ce script se contentait des fichiers locaux déjà présents sur
# le PC, potentiellement périmés (observations.db/alertes.csv) ou
# carrément absents des annulations récentes (perturbations_detectees.csv,
# jamais rapatrié du tout) — repéré par l'utilisateur, 2026-08-20, sur
# executer_rapport_pi.sh d'abord, appliqué ici pour la même raison. Pas de
# -i $SSH_KEY_VPS (contrairement à executer_rapport_pi.sh, qui tourne sur
# le Pi où cette clé dédiée existe) : ce PC utilise déjà son identité SSH
# par défaut, déjà autorisée sur la VPS.
rsync -az -e ssh "$VPS_HOST:train-delay-paris-cherbourg/observations.db" observations.db
# Best-effort (comme sur le Pi) : l'absence de l'un de ces 2 fichiers ne
# doit pas empêcher la génération — generer_rapport.py gère déjà lui-même
# leur absence (charger_donnees()).
rsync -az -e ssh "$VPS_HOST:train-delay-paris-cherbourg/alertes.csv" alertes.csv || true
rsync -az -e ssh "$VPS_HOST:train-delay-paris-cherbourg/perturbations_detectees.csv" perturbations_detectees.csv || true

SORTIE=$(.venv/bin/python generer_rapport.py "$PERIODE")
echo "$SORTIE"
CHEMIN_LOCAL=$(echo "$SORTIE" | sed -n 's/^Rapport généré : //p')

./envoyer_rapport_nas.sh "$CHEMIN_LOCAL"
