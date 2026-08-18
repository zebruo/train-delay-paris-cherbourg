#!/bin/bash
# Équivalent de executer_rapport.sh, mais pour tourner sur le Pi (cron) —
# appelle envoyer_rapport_nas_pi.sh (rsync/SSH) plutôt que la version PC
# (powershell.exe/UNC, indisponible ici).
#
# Rapatrie observations.db/alertes.csv depuis la VPS avant de générer :
# depuis le 2026-08-14, la VPS est la seule source de collecte (le Pi ne
# fait plus tourner collect_realtime.py/collect_alertes.py lui-même — voir
# mémoire du projet) — le Pi garde son rôle de relais vers le NAS
# uniquement parce qu'il est sur le même réseau local que lui, la VPS
# (serveur public) ne pouvant pas atteindre le NAS directement.
set -euo pipefail
cd "$(dirname "$0")"
source config.sh

if [ $# -ne 1 ]; then
    echo "Usage : $0 quotidien|hebdomadaire|mensuel" >&2
    exit 1
fi
PERIODE="$1"

SSH_OPTS_VPS="-i $SSH_KEY_VPS -o BatchMode=yes"
rsync -az -e "ssh $SSH_OPTS_VPS" "$VPS_HOST:train-delay-paris-cherbourg/observations.db" observations.db
# Best-effort : contrairement à observations.db, l'absence d'alertes.csv (ex:
# aucune alerte pertinente détectée depuis le début de la collecte sur la
# VPS) ne doit pas empêcher la génération du rapport — generer_rapport.py
# gère déjà lui-même son absence (charger_donnees()).
rsync -az -e "ssh $SSH_OPTS_VPS" "$VPS_HOST:train-delay-paris-cherbourg/alertes.csv" alertes.csv || true

SORTIE=$(.venv/bin/python generer_rapport.py "$PERIODE")
echo "$SORTIE"
CHEMIN_LOCAL=$(echo "$SORTIE" | sed -n 's/^Rapport généré : //p')

./envoyer_rapport_nas_pi.sh "$CHEMIN_LOCAL"
./envoyer_rapport_vps_pi.sh "$PERIODE" "$CHEMIN_LOCAL"
