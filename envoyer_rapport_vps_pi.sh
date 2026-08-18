#!/bin/bash
# Pousse une copie à nom fixe (écrasée à chaque envoi, pas d'historique
# côté VPS, contrairement au NAS ci-dessus) du dernier rapport PDF vers
# l'appli web, pour le bouton de téléchargement de l'onglet "Rapports" —
# même choix "période la plus récente uniquement" déjà appliqué à l'onglet
# web lui-même.
set -euo pipefail
cd "$(dirname "$0")"
source config.sh

if [ $# -ne 2 ]; then
    echo "Usage : $0 quotidien|hebdomadaire|mensuel <chemin_local_du_rapport>" >&2
    exit 1
fi
PERIODE="$1"
CHEMIN_LOCAL="$2"

SSH_OPTS_VPS="-i $SSH_KEY_VPS -o BatchMode=yes"
RAPPORTS_DIR_VPS="train-delay-paris-cherbourg/rapports"
ssh $SSH_OPTS_VPS "$VPS_HOST" "mkdir -p '$RAPPORTS_DIR_VPS'"
rsync -az -e "ssh $SSH_OPTS_VPS" "$CHEMIN_LOCAL" "$VPS_HOST:$RAPPORTS_DIR_VPS/$PERIODE.pdf"
echo "Envoyé vers la VPS : $RAPPORTS_DIR_VPS/$PERIODE.pdf"
