#!/bin/bash
# Équivalent de envoyer_rapport_nas.sh, mais pour tourner sur le Pi (Linux)
# plutôt que le PC/WSL : la version PC passe par powershell.exe et un
# partage UNC Windows, indisponibles ici. Utilise rsync/SSH avec la clé
# dédiée du Pi vers le NAS (~/.ssh/id_ed25519_nas), même mécanisme déjà en
# place pour sauvegarder_observations_nas.sh.
set -euo pipefail
cd "$(dirname "$0")"
source config.sh

NAS_DIR="/volume1/Documents/backups train-delay/rapports"
SSH_KEY="$SSH_KEY_NAS"

if [ $# -ne 1 ]; then
    echo "Usage : $0 <chemin_local_du_rapport>" >&2
    exit 1
fi
CHEMIN_LOCAL="$1"

# Reproduit le sous-dossier local (rapports/quotidien, hebdomadaire ou
# mensuel) côté NAS, même logique que la version PC — garde la même
# organisation aux deux endroits plutôt qu'un dossier plat.
SOUS_DOSSIER=$(basename "$(dirname "$(realpath "$CHEMIN_LOCAL")")")

SSH_OPTS="-i $SSH_KEY -o BatchMode=yes"
ssh $SSH_OPTS "$NAS_HOST" "mkdir -p '$NAS_DIR/$SOUS_DOSSIER'"
rsync -az -e "ssh $SSH_OPTS" "$CHEMIN_LOCAL" "$NAS_HOST:$NAS_DIR/$SOUS_DOSSIER/"
echo "Envoyé vers le NAS : $CHEMIN_LOCAL"
