"""Requêtes ponctuelles à l'API Navitia SNCF (api.sncf.com) pour récupérer la
cause textuelle d'un trajet annulé — un seul appel par annulation nouvellement
détectée (voir perturbations.enregistrer_evenements), jamais un sondage
régulier : cette API n'expose pas d'endpoint fiable "toutes les perturbations
en cours sur cette ligne" (essayé /lines/{id}/disruptions -> 404 et
/disruptions?filter=stop_area.id=... -> 400 bad_filter, vérifié en direct le
2026-09-22) ; seule la requête par train (vehicle_journeys?headsign=...)
fonctionne de façon fiable.
"""
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

NAVITIA_URL = "https://api.sncf.com/v1/coverage/sncf/vehicle_journeys"


def recuperer_cause_annulation(numero_train, start_date):
    """Cause publiée par la SNCF (ex: "Panne d'un passage à niveau") pour le
    train `numero_train` annulé le `start_date` (format GTFS-RT YYYYMMDD,
    directement le format attendu par Navitia pour since/until — pas de
    reformatage). Chaîne vide si SNCF_API_TOKEN absent de config.py (poste de
    dev, ou VPS pas encore configurée), si la requête échoue/expire, ou si
    SNCF n'a publié aucun message : jamais d'exception propagée, ce n'est
    qu'un enrichissement optionnel, jamais un point de blocage de la collecte
    (voir perturbations.enregistrer_evenements, seul appelant)."""
    try:
        from config import SNCF_API_TOKEN
    except ImportError:
        return ""
    if not SNCF_API_TOKEN:
        return ""

    params = {
        "headsign": numero_train,
        "since": f"{start_date}T000000",
        "until": f"{start_date}T235900",
    }
    url = f"{NAVITIA_URL}?{urllib.parse.urlencode(params)}"
    identifiants = base64.b64encode(f"{SNCF_API_TOKEN}:".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {identifiants}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read())
    except Exception as erreur:
        # Journalisé (collect.log, via le cron de collect_realtime.py) plutôt
        # qu'avalé sans trace : jusqu'ici aucun moyen de savoir SI/POURQUOI un
        # appel échouait (timeout, erreur HTTP, panne réseau...). Repéré le
        # 2026-09-23 : les 55 annulations d'un mouvement social national sont
        # toutes restées sans cause malgré l'appel automatique — impossible de
        # confirmer la cause exacte de cet échec sans cette trace, la 1re fois.
        print(
            f"{datetime.now(timezone.utc).isoformat()} : échec recuperer_cause_annulation"
            f"({numero_train}, {start_date}) : {type(erreur).__name__}: {erreur}",
            file=sys.stderr,
        )
        return ""

    # severity.effect = "NO_SERVICE" pour une annulation complète, confirmé
    # en direct le 2026-09-22 (train 852334, 22/09) — pas de filtre dessus :
    # le headsign+since/until de la requête ciblent déjà précisément CE train
    # à CETTE date. À revoir si ça produit un faux positif un jour (plusieurs
    # perturbations différentes le même jour pour le même train).
    #
    # IMPORTANT : ne JAMAIS ajouter data_freshness=realtime à cette requête —
    # testé et confirmé le 2026-09-22 : ce paramètre fait échouer la requête
    # en 404 spécifiquement pour un trajet ENTIÈREMENT annulé (Navitia ne
    # peut visiblement pas construire un état "temps réel" pour un trajet qui
    # n'a jamais circulé), cassant exactement le cas d'usage principal de
    # cette fonction — alors qu'il fonctionne très bien pour un simple
    # retard. Sans ce paramètre (défaut "base_schedule"), les deux cas
    # marchent.
    for disruption in data.get("disruptions", []):
        for message in disruption.get("messages") or []:
            texte = (message.get("text") or "").strip()
            if texte:
                return texte
    return ""
