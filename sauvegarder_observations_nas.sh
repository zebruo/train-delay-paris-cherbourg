#!/bin/bash
# Sauvegarde quotidienne d'observations.db (VPS, seule copie "vivante")
# vers le NAS, avec rotation — la VPS ne peut pas atteindre le NAS
# directement (IP privée), d'où ce relais par le Pi, même mécanisme que
# executer_rapport_pi.sh/envoyer_rapport_nas_pi.sh. Script indépendant de
# la génération de rapport (pas chaîné) : un échec de l'un ne doit pas
# affecter l'autre.
set -euo pipefail
cd "$(dirname "$0")"
source config.sh

NAS_DIR="/volume1/Documents/backups train-delay/observations_db"
RETENTION_JOURS=14
SSH_OPTS_VPS="-i $SSH_KEY_VPS -o BatchMode=yes"
SSH_OPTS_NAS="-i $SSH_KEY_NAS -o BatchMode=yes"

FICHIER_TMP="observations_backup_tmp.db"
rsync -az -e "ssh $SSH_OPTS_VPS" "$VPS_HOST:train-delay-paris-cherbourg/observations.db" "$FICHIER_TMP"

DATE=$(date +%Y%m%d)
ssh $SSH_OPTS_NAS "$NAS_HOST" "mkdir -p '$NAS_DIR'"
rsync -az -e "ssh $SSH_OPTS_NAS" "$FICHIER_TMP" "$NAS_HOST:$NAS_DIR/observations_$DATE.db"
rm -f "$FICHIER_TMP"

# Purge des sauvegardes de plus de RETENTION_JOURS jours.
ssh $SSH_OPTS_NAS "$NAS_HOST" "find '$NAS_DIR' -name 'observations_*.db' -mtime +$RETENTION_JOURS -delete"

echo "Sauvegarde envoyée : observations_$DATE.db"
