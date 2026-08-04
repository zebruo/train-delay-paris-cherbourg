#!/bin/bash
# Copie un rapport généré vers le NAS, depuis le PC (WSL) plutôt que depuis
# le Pi : matplotlib a fait planter le Pi à la compilation (RAM insuffisante
# sur cet ARM, voir mémoire du projet du 2026-07-24) — les rapports sont donc
# générés côté PC, où matplotlib est déjà disponible.
#
# Utilise powershell.exe (interop WSL) plutôt qu'un accès SSH/rsync : le
# partage \\NAS-4TO\Documents\backups train-delay est déjà accessible depuis
# Windows (testé, pas besoin de nouvelle clé/identifiants).
set -euo pipefail
cd "$(dirname "$0")"

NAS_UNC='\\NAS-4TO\Documents\backups train-delay\rapports'

if [ $# -ne 1 ]; then
    echo "Usage : $0 <chemin_local_du_rapport>" >&2
    exit 1
fi
CHEMIN_LOCAL="$1"

CHEMIN_WINDOWS=$(wslpath -w "$(realpath "$CHEMIN_LOCAL")")
# Reproduit le sous-dossier local (rapports/quotidien ou rapports/
# hebdomadaire) côté NAS, pour garder la même organisation aux deux
# endroits plutôt qu'un dossier plat qui remélangerait les deux historiques.
SOUS_DOSSIER=$(basename "$(dirname "$(realpath "$CHEMIN_LOCAL")")")
NAS_UNC_CIBLE="$NAS_UNC\\$SOUS_DOSSIER"

powershell.exe -NoProfile -Command "
    New-Item -ItemType Directory -Force -Path '$NAS_UNC_CIBLE' | Out-Null
    Copy-Item -Path '$CHEMIN_WINDOWS' -Destination '$NAS_UNC_CIBLE' -Force
"
echo "Envoyé vers le NAS : $CHEMIN_LOCAL"
