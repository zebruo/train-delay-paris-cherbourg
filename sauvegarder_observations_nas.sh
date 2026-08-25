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
RETENTION_FICHIERS=3
SSH_OPTS_VPS="-i $SSH_KEY_VPS -o BatchMode=yes"
SSH_OPTS_NAS="-i $SSH_KEY_NAS -o BatchMode=yes"

FICHIER_TMP="observations_backup_tmp.db"
rsync -az -e "ssh $SSH_OPTS_VPS" "$VPS_HOST:train-delay-paris-cherbourg/observations.db" "$FICHIER_TMP"

DATE=$(date +%Y%m%d)
ssh $SSH_OPTS_NAS "$NAS_HOST" "mkdir -p '$NAS_DIR'"
rsync -az -e "ssh $SSH_OPTS_NAS" "$FICHIER_TMP" "$NAS_HOST:$NAS_DIR/observations_$DATE.db"
rm -f "$FICHIER_TMP"

# Purge : ne garde que les RETENTION_FICHIERS sauvegardes les plus
# récentes (par date de modification), quel que soit leur âge en jours —
# remplace l'ancienne rétention par ancienneté (14 jours glissants).
# Boucle "while read" plutôt que "xargs -r" : -r (ne pas lancer si l'entrée
# est vide) n'est pas garanti disponible sur toutes les variantes de xargs
# embarquées (BusyBox...), une boucle vide ne fait simplement rien.
ssh $SSH_OPTS_NAS "$NAS_HOST" "cd '$NAS_DIR' && ls -t observations_*.db 2>/dev/null | tail -n +\$(($RETENTION_FICHIERS + 1)) | while read -r f; do rm -f \"\$f\"; done"

echo "Sauvegarde envoyée : observations_$DATE.db"
