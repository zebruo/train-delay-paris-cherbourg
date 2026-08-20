"""
Tout ce qui concerne l'onglet "Perturbations" de viewer.py : chargement
des alertes SNCF officielles (alertes.csv, alimenté par collect_alertes.py)
et détection des arrêts supprimés / trajets annulés dans le flux temps réel
(alimenté par collect_realtime.py) — aucune dépendance à Tkinter, testable
seul en ligne de commande, importé à la fois côté collecteur (écriture) et
côté viewer.py (lecture/affichage).

Les arrêts supprimés/trajets annulés sont un événement différent des alertes
SNCF officielles (pas de plage de dates ni de gares concernées, juste un
train + une date précise) — d'où un fichier dédié, PERTURBATIONS_FILE,
plutôt que de les mélanger dans alertes.csv.
"""
import csv
import os
from datetime import datetime, timezone

import pandas as pd
from google.transit import gtfs_realtime_pb2

from formatting import sans_date_trip_id

CANCELED = gtfs_realtime_pb2.TripDescriptor.CANCELED
SKIPPED = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.SKIPPED

LOCAL_ALERTES = "alertes.csv"
PERTURBATIONS_FILE = "perturbations_detectees.csv"
PERTURBATIONS_FIELDNAMES = ["poll_time", "type", "trip_id", "start_date", "train", "gare"]


def charger_alertes(fichier=LOCAL_ALERTES):
    """Charge alertes.csv (travaux/perturbations SNCF recoupées par gare de
    la ligne, voir collect_alertes.py) — absence de fichier ou corruption
    traitées comme "aucune alerte connue" plutôt qu'une erreur : contrairement
    à observations.csv, ce n'est qu'un complément, pas la donnée centrale de
    l'appli (peut légitimement ne pas encore exister sur le Pi tant que
    collect_alertes.py n'y est pas déployé)."""
    colonnes = ["id", "gares", "cause", "effet", "debut", "fin", "texte", "description", "poll_time"]
    try:
        alertes = pd.read_csv(fichier)
    except (FileNotFoundError, pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError):
        alertes = pd.DataFrame(columns=colonnes)
    alertes["debut"] = pd.to_datetime(alertes["debut"], utc=True, errors="coerce")
    alertes["fin"] = pd.to_datetime(alertes["fin"], utc=True, errors="coerce")
    return alertes


def detecter_evenements(feed, trip_ids_connus, stop_names):
    """Repère, dans un FeedMessage GTFS-RT déjà décodé, les arrêts supprimés
    et les trajets entièrement annulés — seulement pour les trajets de notre
    ligne (trip_ids_connus, indexés par sans_date_trip_id — voir
    formatting.py et collect_realtime.load_reference_data()). Deux champs
    GTFS-RT distincts à vérifier, pas un seul : un trajet annulé
    (trip.schedule_relationship = CANCELED) ne liste généralement AUCUN
    arrêt un par un dans stop_time_update, contrairement à un arrêt
    supprimé isolé sur un trajet qui continue par ailleurs
    (stop_time_update.schedule_relationship = SKIPPED, arrêt par arrêt) —
    se limiter au second cas raterait donc les annulations complètes (voir
    mémoire du projet, 2026-07-31). Retourne une liste de dicts prêts à
    écrire via enregistrer_evenements()."""
    poll_time = datetime.now(timezone.utc).isoformat()
    evenements = []
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        trip = entity.trip_update.trip
        if sans_date_trip_id(trip.trip_id) not in trip_ids_connus:
            continue
        train = trip.trip_id.split(":")[0]

        if trip.schedule_relationship == CANCELED:
            evenements.append({
                "poll_time": poll_time, "type": "trajet_annule",
                "trip_id": trip.trip_id, "start_date": trip.start_date,
                "train": train, "gare": "",
            })
            continue  # un trajet annulé n'a normalement pas d'arrêts détaillés

        for stu in entity.trip_update.stop_time_update:
            if stu.schedule_relationship == SKIPPED:
                evenements.append({
                    "poll_time": poll_time, "type": "arret_supprime",
                    "trip_id": trip.trip_id, "start_date": trip.start_date,
                    "train": train, "gare": stop_names.get(stu.stop_id, stu.stop_id),
                })
    return evenements


def enregistrer_evenements(evenements, fichier=PERTURBATIONS_FILE):
    """Ajoute les événements réellement nouveaux à fichier — un arrêt
    supprimé ou un trajet annulé reste visible dans le flux sur plusieurs
    sondages consécutifs (toutes les 5 min) tant que la situation ne change
    pas ; sans dédoublonnage, le même événement se réécrirait à chaque
    passage du collecteur. Clé de dédoublonnage : (type, trip_id,
    start_date, gare) — pas poll_time, qui change à chaque sondage."""
    if not evenements:
        return 0

    deja_connus = set()
    if os.path.isfile(fichier):
        with open(fichier, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                deja_connus.add((row["type"], row["trip_id"], row["start_date"], row["gare"]))

    nouveaux = [
        e for e in evenements
        if (e["type"], e["trip_id"], e["start_date"], e["gare"]) not in deja_connus
    ]
    if not nouveaux:
        return 0

    fichier_existe = os.path.isfile(fichier)
    with open(fichier, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PERTURBATIONS_FIELDNAMES)
        if not fichier_existe:
            writer.writeheader()
        writer.writerows(nouveaux)
    return len(nouveaux)


def charger_evenements(fichier=PERTURBATIONS_FILE):
    """Charge perturbations_detectees.csv pour l'affichage — même logique
    tolérante aux fichiers absents/corrompus que charger_alertes()."""
    try:
        evenements = pd.read_csv(fichier)
    except (FileNotFoundError, pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError):
        evenements = pd.DataFrame(columns=PERTURBATIONS_FIELDNAMES)
    evenements["poll_time"] = pd.to_datetime(evenements["poll_time"], utc=True, errors="coerce")
    return evenements
