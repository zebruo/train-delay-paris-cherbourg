#!/bin/bash
# Mise en sommeil / remise en ligne du projet sur la VPS — appelé par le
# bouton « Mettre le projet en sommeil » / « Remettre le projet en ligne »
# de vps_control.py (demande de l'utilisateur, 2026-10-19), le Pi 4 ayant
# son propre script (basculer_sommeil_pi.sh).
#
#   ./basculer_sommeil.sh etat      -> en_ligne | en_sommeil | incoherent (...)
#   ./basculer_sommeil.sh sommeil   -> arrête le service web + suspend les crons du projet
#   ./basculer_sommeil.sh reveil    -> l'inverse, puis recalcule les caches
#
# Pendant le sommeil, nginx affiche « Ce site n'est plus maintenu » sur toutes
# les URL (error_page 502/503/504 -> /var/www/paris-cherbourg-sommeil/,
# ajouté le 2026-10-19 dans /etc/nginx/sites-available/paris-cherbourg) :
# ce script n'a donc pas à toucher nginx. Le site isbn.generalbol.fr (autre
# service, autre port) n'est pas concerné.
#
# Les lignes de crontab du projet ne sont jamais supprimées : elles sont
# préfixées par MARQUEUR (et le préfixe retiré au réveil) — idempotent,
# relançable sans risque. Crontab sauvegardée avant chaque modification.
# Le service est désactivé (disable --now), pas seulement arrêté, pour
# rester en sommeil même après un redémarrage de la VPS.
set -euo pipefail
cd "$(dirname "$0")"

MARQUEUR="#SOMMEIL# "
MOTIF="train-delay-paris-cherbourg"
SERVICE="train-delay"

compter_crons() {
    ACTIVES=$(crontab -l 2>/dev/null | grep -c "^[^#].*$MOTIF" || true)
    DORMANTES=$(crontab -l 2>/dev/null | grep -c "^$MARQUEUR.*$MOTIF" || true)
}

etat() {
    compter_crons
    local svc
    svc=$(systemctl is-active "$SERVICE" || true)
    if [ "$svc" = "active" ] && [ "$ACTIVES" -gt 0 ] && [ "$DORMANTES" -eq 0 ]; then
        echo "en_ligne"
    elif [ "$svc" != "active" ] && [ "$ACTIVES" -eq 0 ] && [ "$DORMANTES" -gt 0 ]; then
        echo "en_sommeil"
    else
        echo "incoherent (service=$svc, crons actives=$ACTIVES, en sommeil=$DORMANTES)"
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
        sudo systemctl disable --now "$SERVICE" 2>&1 | grep -v "^Removed" || true
        etat
        ;;
    reveil)
        sauver_crontab
        crontab -l | sed "s/^$MARQUEUR//" | crontab -
        sudo systemctl enable --now "$SERVICE" 2>&1 | grep -v "^Created symlink" || true
        sleep 5
        # Les caches précalculés ont pu vieillir pendant le sommeil : un
        # passage tout de suite plutôt qu'attendre le prochain cron (peut
        # échouer en « database is locked » s'il tombe en même temps qu'un
        # cron — sans gravité, le cron suivant le refera).
        .venv/bin/python rafraichir_caches_historique.py >> rafraichir_caches_historique.log 2>&1 \
            || echo "(rafraîchissement des caches en échec — le cron suivant le refera)"
        etat
        ;;
    *)
        echo "Usage : $0 etat|sommeil|reveil" >&2
        exit 2
        ;;
esac
