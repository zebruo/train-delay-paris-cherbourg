"""Interroge le flux SNCF GTFS-RT des alertes (travaux, incidents,
perturbations...) et enregistre celles concernant une gare de la ligne
Paris-Cherbourg dans alertes.csv.

Contrairement à collect_realtime.py (retards, toutes les 5 min), une alerte
reste active plusieurs heures/jours d'affilée — inutile de la sonder aussi
souvent : à lancer toutes les 60 min via une tâche planifiée séparée.

Le recoupement se fait par gare (informed_entity.stop_id), pas par trip_id :
la plupart des trip_id du flux d'alertes utilisent un format court
incompatible avec celui du flux de retards (voir mémoire du projet), alors
que les stop_id sont au même format ("StopArea:OCE<code>") que
gtfs/stops.txt, déjà utilisé pour résoudre les gares du flux de retards.
"""
import csv
import os
import re
from datetime import datetime, timezone

import urllib.request
from google.transit import gtfs_realtime_pb2

FEED_URL = "https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-service-alerts"
ALERTES_FILE = "alertes.csv"
FIELDNAMES = ["id", "gares", "cause", "effet", "debut", "fin", "texte", "description", "poll_time"]

# Les 11 gares réellement sur la ligne (voir GARES_LIGNE dans viewer.py) —
# une alerte n'est retenue que si elle cite au moins une de ces gares.
GARES_LIGNE = {
    "Paris Saint-Lazare", "Mantes-la-Jolie", "Évreux Normandie", "Bernay",
    "Lisieux", "Caen", "Bayeux", "Lison", "Carentan", "Valognes", "Cherbourg",
}


def build_stop_names():
    """Correspondance stop_id -> nom de gare, à partir du référentiel
    (StopPoint) complété par gtfs/stops.txt (StopArea, format utilisé par la
    plupart des alertes) — même logique que formatting.build_stop_names(),
    dupliquée ici plutôt qu'importée : ce script tourne sur le Pi, où
    formatting.py (dépendance de viewer.py uniquement) n'est pas déployé."""
    noms = {}
    with open("reference_paris_cherbourg.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            noms[row["stop_id"]] = row["stop_name"]
    try:
        with open("gtfs/stops.txt", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                noms.setdefault(row["stop_id"], row["stop_name"])
    except FileNotFoundError:
        pass  # gtfs/ n'est qu'un complément local optionnel, pas indispensable
    return noms


def fetch_feed():
    feed = gtfs_realtime_pb2.FeedMessage()
    with urllib.request.urlopen(FEED_URL, timeout=30) as response:
        feed.ParseFromString(response.read())
    return feed


def texte_fr(champ_traduit):
    """Un TranslatedString GTFS-RT (header_text/description_text) contient
    une traduction par langue — on préfère le français, à défaut la première
    disponible plutôt que de laisser le champ vide."""
    for t in champ_traduit.translation:
        if t.language == "fr":
            return nettoyer_texte(t.text)
    return nettoyer_texte(champ_traduit.translation[0].text) if champ_traduit.translation else ""


def nettoyer_texte(texte):
    """Les description_text de ce flux contiennent parfois du HTML brut (ex:
    "<p>Guichets fermés...</p>") — rien dans l'appli ne rend du HTML, donc les
    balises seraient affichées telles quelles sans ce nettoyage."""
    sans_balises = re.sub(r"<[^>]+>", " ", texte)
    return " ".join(sans_balises.split())


def formater_horodatage(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""


def alertes_pertinentes(feed, stop_names):
    """Filtre les alertes du flux à celles citant au moins une gare de la
    ligne, avec les gares concernées déjà résolues en noms lisibles."""
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        alert = entity.alert
        stop_ids = [ie.stop_id for ie in alert.informed_entity if ie.HasField("stop_id")]
        gares = sorted({stop_names[sid] for sid in stop_ids if sid in stop_names} & GARES_LIGNE)
        if not gares:
            continue
        debut = alert.active_period[0].start if alert.active_period else None
        fin = alert.active_period[0].end if alert.active_period else None
        yield {
            "id": entity.id,
            "gares": ", ".join(gares),
            "cause": gtfs_realtime_pb2.Alert.Cause.Name(alert.cause),
            "effet": gtfs_realtime_pb2.Alert.Effect.Name(alert.effect),
            "debut": formater_horodatage(debut),
            "fin": formater_horodatage(fin),
            "texte": texte_fr(alert.header_text),
            "description": texte_fr(alert.description_text),
        }


def main():
    stop_names = build_stop_names()
    feed = fetch_feed()
    poll_time = datetime.now(timezone.utc).isoformat()

    ids_connus = set()
    if os.path.isfile(ALERTES_FILE):
        with open(ALERTES_FILE, encoding="utf-8") as f:
            ids_connus = {row["id"] for row in csv.DictReader(f)}

    # Une alerte reste dans le flux national à chaque sondage tant qu'elle
    # est active — dédoublonnée par id plutôt que ré-écrite à chaque fois
    # (même logique d'accumulation append-only que observations.csv). Ne
    # capture donc que l'état de l'alerte au moment où elle est vue pour la
    # première fois : si son texte ou sa période changent ensuite, cette
    # mise à jour n'est pas répercutée (limite acceptée pour une v1).
    nouvelles = [a for a in alertes_pertinentes(feed, stop_names) if a["id"] not in ids_connus]
    for a in nouvelles:
        a["poll_time"] = poll_time

    file_exists = os.path.isfile(ALERTES_FILE)
    with open(ALERTES_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerows(nouvelles)

    print(f"{poll_time} : {len(nouvelles)} nouvelle(s) alerte(s) pertinente(s) "
          f"(sur {len(feed.entity)} alertes dans le flux national).")


if __name__ == "__main__":
    main()
