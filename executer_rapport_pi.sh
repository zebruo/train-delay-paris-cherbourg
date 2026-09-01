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
# Comme observations.db (pas best-effort) : "Déployer vers la VPS"
# (viewer.py) ne pousse le référentiel régénéré QUE vers la VPS, jamais
# vers les Pi — un référentiel local resté sur le disque du Pi peut donc
# dériver silencieusement de celui de la VPS pendant des semaines (repéré
# le 2026-09-01 : référentiel du Pi daté du 12/08, celui de la VPS mis à
# jour la veille, rapport PDF et onglet web en désaccord sur la quasi-
# totalité des statistiques du mois, pas seulement les annulations). Mieux
# vaut un rapport qui échoue franchement (VPS injoignable) qu'un rapport
# généré avec un référentiel qu'on sait daté.
rsync -az -e "ssh $SSH_OPTS_VPS" "$VPS_HOST:train-delay-paris-cherbourg/reference_paris_cherbourg.csv" reference_paris_cherbourg.csv
rsync -az -e "ssh $SSH_OPTS_VPS" \
    "$VPS_HOST:train-delay-paris-cherbourg/reference_paris_cherbourg_calendrier.csv" \
    reference_paris_cherbourg_calendrier.csv
# Best-effort : contrairement à observations.db, l'absence d'alertes.csv (ex:
# aucune alerte pertinente détectée depuis le début de la collecte sur la
# VPS) ne doit pas empêcher la génération du rapport — generer_rapport.py
# gère déjà lui-même son absence (charger_donnees()).
rsync -az -e "ssh $SSH_OPTS_VPS" "$VPS_HOST:train-delay-paris-cherbourg/alertes.csv" alertes.csv || true
# Même best-effort qu'alertes.csv ci-dessus — oublié lors de l'ajout du
# compteur "Circulations annulées" (2026-08-20), repéré tout de suite après
# coup : le fichier local du Pi datait du 14/08, jamais rapatrié depuis la
# VPS avant ce correctif, donnant "aucune" annulation quel que soit l'état
# réel de la VPS.
rsync -az -e "ssh $SSH_OPTS_VPS" "$VPS_HOST:train-delay-paris-cherbourg/perturbations_detectees.csv" perturbations_detectees.csv || true

SORTIE=$(.venv/bin/python generer_rapport.py "$PERIODE")
echo "$SORTIE"
CHEMIN_LOCAL=$(echo "$SORTIE" | sed -n 's/^Rapport généré : //p')

./envoyer_rapport_nas_pi.sh "$CHEMIN_LOCAL"
./envoyer_rapport_vps_pi.sh "$PERIODE" "$CHEMIN_LOCAL"
