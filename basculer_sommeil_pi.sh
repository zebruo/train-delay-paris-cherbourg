#!/bin/bash
# Mise en sommeil / remise en ligne du projet sur le Pi 4 (rapports
# quotidien/hebdomadaire/mensuel + sauvegarde d'observations.db vers le NAS)
# — appelé par le bouton de vps_control.py, en complément de
# basculer_sommeil.sh côté VPS (demande de l'utilisateur, 2026-10-19).
#
#   ./basculer_sommeil_pi.sh etat      -> en_ligne | en_sommeil | incoherent (...)
#   ./basculer_sommeil_pi.sh sommeil   -> suspend les crons du projet
#   ./basculer_sommeil_pi.sh reveil    -> les réactive
#
# Même principe que basculer_sommeil.sh : lignes de crontab préfixées par
# MARQUEUR (jamais supprimées), idempotent, crontab sauvegardée avant chaque
# modification. Les lignes de commentaire (#) ne sont jamais touchées. À ne
# PAS déployer/lancer sur le Pi 2 : ses lignes de secours sont déjà
# commentées à la main et doivent le rester.
set -euo pipefail
cd "$(dirname "$0")"

MARQUEUR="#SOMMEIL# "
MOTIF="train-delay-paris-cherbourg"

compter_crons() {
    ACTIVES=$(crontab -l 2>/dev/null | grep -c "^[^#].*$MOTIF" || true)
    DORMANTES=$(crontab -l 2>/dev/null | grep -c "^$MARQUEUR.*$MOTIF" || true)
}

etat() {
    compter_crons
    if [ "$ACTIVES" -gt 0 ] && [ "$DORMANTES" -eq 0 ]; then
        echo "en_ligne"
    elif [ "$ACTIVES" -eq 0 ] && [ "$DORMANTES" -gt 0 ]; then
        echo "en_sommeil"
    else
        echo "incoherent (crons actives=$ACTIVES, en sommeil=$DORMANTES)"
    fi
}

sauver_crontab() {
    mkdir -p backups_code
    crontab -l > "backups_code/crontab.$(date +%Y%m%d-%H%M%S).bak" 2>/dev/null || true
}

case "${1:-etat}" in
    etat)
        etat
        ;;
    sommeil)
        sauver_crontab
        crontab -l | sed "/^[^#].*$MOTIF/s/^/$MARQUEUR/" | crontab -
        etat
        ;;
    reveil)
        sauver_crontab
        crontab -l | sed "s/^$MARQUEUR//" | crontab -
        etat
        ;;
    *)
        echo "Usage : $0 etat|sommeil|reveil" >&2
        exit 2
        ;;
esac
