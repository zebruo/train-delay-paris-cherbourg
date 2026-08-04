#!/bin/bash
# Sauvegarde datée quotidienne de observations.csv et alertes.csv sur le Pi
# lui-même (backups/<fichier>_AAAAMMJJ.csv), avec une rotation de 14 jours.
# Protège contre une corruption du fichier actif (voir memory du projet) —
# pas contre une panne totale de la carte SD, voir backup_to_nas.sh pour ça.
set -euo pipefail
cd "$(dirname "$0")"

mkdir -p backups
cp observations.csv "backups/observations_$(date +%Y%m%d).csv"
cp alertes.csv "backups/alertes_$(date +%Y%m%d).csv"

find backups -name 'observations_*.csv' -mtime +14 -delete
find backups -name 'alertes_*.csv' -mtime +14 -delete
