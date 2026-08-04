#!/bin/bash
# Copie quotidienne vers le NAS (hors du Pi) : les fichiers actifs + les
# sauvegardes datées de backup_local.sh, pour que le NAS ait le même
# historique que le Pi — une panne de carte SD ne fait perdre au pire qu'une
# journée de données. Un seul passage par jour (3h20, juste après
# backup_local.sh, groupé avec les rapports du matin sur le Pi 4) plutôt que
# plusieurs fois par jour : évite de réveiller le NAS de sa mise en veille
# disque à répétition dans la journée (usure/consommation, voir mémoire du
# projet) — accepté comme compromis par l'utilisateur, 2026-07-30, quitte à
# resserrer si le besoin s'en fait sentir.
set -euo pipefail
cd "$(dirname "$0")"
source config.sh

NAS_DIR="/volume1/Documents/backups train-delay"
SSH_KEY="$SSH_KEY_NAS"

mkdir -p backups
# S'assure que le fichier existe avant le rsync (set -e ferait échouer tout
# le script sinon) : verifier_gtfs.py (cron 3h15) tourne juste avant ce
# script (3h20) et le crée normalement, mais un tout premier déploiement,
# avant le premier passage de verifier_gtfs.py, ne l'aurait pas encore créé.
touch verification_gtfs.log
# Pas de "/" après "backups" : on veut recréer le sous-dossier backups/ côté
# NAS (contenu de observations.csv/ irait sinon se mélanger au même niveau).
rsync -az -e "ssh -i $SSH_KEY -o BatchMode=yes" \
    observations.csv alertes.csv verification_gtfs.log backups \
    "$NAS_HOST:$NAS_DIR/"
