"""
Interroge le flux temps réel SNCF (GTFS-RT) et enregistre les retards observés
pour les trajets Paris-Cherbourg (identifiés via reference_paris_cherbourg.csv),
avec la météo actuelle de la gare (Open-Meteo), le type de jour et les
vacances scolaires (calendar_data.py), et le nombre d'arrêts restants avant
le terminus (reference_paris_cherbourg.csv).

À lancer périodiquement (toutes les 5-10 minutes, via une tâche planifiée) :
chaque appel ajoute une ligne par arrêt observé dans observations.db (SQLite
— choisi le 2026-08-13 au moment du passage à la VPS IONOS, plutôt que le
CSV utilisé jusqu'ici sur le Pi : écritures atomiques, colonnes typées,
requêtes ciblées sans recharger tout le fichier à chaque lecture — voir
mémoire du projet). Comme un même train n'apparaît dans le flux que dans les
~60 minutes avant son passage, il faut accumuler ces appels dans la durée
pour couvrir tous les trajets de la journée.
"""
import csv
import json
import sqlite3
import urllib.parse
from datetime import datetime, timezone

import urllib.request
from google.transit import gtfs_realtime_pb2

from calendar_data import Calendrier
from formatting import sans_date_trip_id
from perturbations import CANCELED, SKIPPED, detecter_evenements, enregistrer_evenements

FEED_URL = "https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
OBSERVATIONS_DB = "observations.db"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS observations (
    poll_time TEXT NOT NULL,
    trip_id TEXT NOT NULL,
    start_date TEXT NOT NULL,
    stop_id TEXT NOT NULL,
    arrival_delay_s INTEGER,
    departure_delay_s INTEGER,
    temperature_c REAL,
    precipitation_mm REAL,
    wind_speed_kmh REAL,
    weather_code INTEGER,
    type_jour TEXT,
    vacances_scolaires INTEGER,
    arrets_restants INTEGER
);
CREATE INDEX IF NOT EXISTS idx_observations_trip ON observations(trip_id, start_date);
CREATE INDEX IF NOT EXISTS idx_observations_poll_time ON observations(poll_time);
"""


def connecter_db():
    """WAL (Write-Ahead Logging) plutôt que le mode journal par défaut :
    autorise une lecture concurrente (l'appli web) pendant qu'un insert est
    en cours, sans que l'une bloque l'autre — pertinent ici puisque la
    collecte (cron 5 min) et les requêtes de l'appli tournent sur la même
    machine, contrairement au Pi où seule la collecte écrivait."""
    connexion = sqlite3.connect(OBSERVATIONS_DB)
    connexion.execute("PRAGMA journal_mode=WAL")
    connexion.executescript(SCHEMA_SQL)
    return connexion


def load_reference_data():
    """Un seul passage sur reference_paris_cherbourg.csv pour extraire tout ce
    dont le collecteur a besoin : les trip_id connus, le nom de chaque gare, et
    la position de chaque arrêt dans son trajet (+ la position du terminus).

    Indexé par sans_date_trip_id(trip_id), pas le trip_id brut du
    référentiel : un même train réel republie un trip_id quasi identique
    d'un jour à l'autre, sauf son dernier segment (la date) — sans ignorer
    ce suffixe, ~27 % des trains pertinents restaient invisibles un jour
    donné (voir formatting.sans_date_trip_id et mémoire du projet,
    2026-07-31)."""
    trip_ids = set()
    stop_names = {}
    sequences = {}
    terminus = {}
    with open("reference_paris_cherbourg.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            trip_id, stop_id = sans_date_trip_id(row["trip_id"]), row["stop_id"]
            trip_ids.add(trip_id)
            stop_names[stop_id] = row["stop_name"]
            seq = int(row["stop_sequence"])
            sequences[(trip_id, stop_id)] = seq
            terminus[trip_id] = max(terminus.get(trip_id, 0), seq)
    return trip_ids, stop_names, sequences, terminus


def load_station_coords():
    coords = {}
    with open("stations_coords.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            coords[row["gare"]] = (row["latitude"], row["longitude"])
    return coords


def fetch_feed():
    feed = gtfs_realtime_pb2.FeedMessage()
    with urllib.request.urlopen(FEED_URL, timeout=30) as response:
        feed.ParseFromString(response.read())
    return feed


def fetch_weather(latitude, longitude):
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,precipitation,wind_speed_10m,weather_code",
    }
    url = f"{WEATHER_URL}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            data = json.loads(response.read())
        current = data["current"]
        return {
            "temperature_c": current["temperature_2m"],
            "precipitation_mm": current["precipitation"],
            "wind_speed_kmh": current["wind_speed_10m"],
            "weather_code": current["weather_code"],
        }
    except Exception:
        return {"temperature_c": None, "precipitation_mm": None, "wind_speed_kmh": None, "weather_code": None}


def main():
    known_trip_ids, stop_names, stop_sequences, terminus_par_trajet = load_reference_data()
    station_coords = load_station_coords()
    calendrier = Calendrier()
    feed = fetch_feed()
    poll_time = datetime.now(timezone.utc).isoformat()

    # Arrêts supprimés / trajets annulés (voir perturbations.py) : détectés
    # sur le même feed déjà récupéré ci-dessus, pas un appel réseau
    # supplémentaire. Best-effort — un souci ici ne doit jamais empêcher la
    # collecte normale des retards juste en dessous.
    try:
        evenements = detecter_evenements(feed, known_trip_ids, stop_names)
        n_evenements = enregistrer_evenements(evenements)
    except Exception as exc:
        n_evenements = 0
        print(f"Détection arrêts supprimés/trajets annulés : échec ({exc})")

    raw_rows = []
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        trip = entity.trip_update.trip
        if sans_date_trip_id(trip.trip_id) not in known_trip_ids:
            continue
        # Un trajet annulé (CANCELED) liste parfois quand même ses arrêts en
        # détail dans stop_time_update, avec un delay résiduel jamais mis à
        # jour (souvent 0) — constaté en pratique, 2026-08-12 (804 lignes sur
        # 2 trajets annulés dans observations.csv, apparaissant "à l'heure"
        # alors que le trajet n'a jamais eu lieu), malgré le commentaire de
        # detecter_evenements (perturbations.py) supposant qu'un trajet
        # annulé n'a "normalement" pas d'arrêts détaillés. On ignore donc
        # explicitement le trajet entier ici, pas seulement dans la détection
        # d'événements.
        if trip.schedule_relationship == CANCELED:
            continue

        for stu in entity.trip_update.stop_time_update:
            # Même logique pour un arrêt isolé supprimé (SKIPPED) sur un
            # trajet qui continue par ailleurs : son delay résiduel ne
            # reflète plus une vraie prédiction, mais restait enregistré tel
            # quel (voir mémoire du projet et perturbations.detecter_evenements,
            # qui capture déjà l'événement séparément dans
            # perturbations_detectees.csv — mais observations.csv l'ignorait).
            if stu.schedule_relationship == SKIPPED:
                continue
            raw_rows.append({
                "poll_time": poll_time,
                "trip_id": trip.trip_id,
                "start_date": trip.start_date,
                "stop_id": stu.stop_id,
                "arrival_delay_s": stu.arrival.delay if stu.HasField("arrival") else None,
                "departure_delay_s": stu.departure.delay if stu.HasField("departure") else None,
            })

    # Une requête météo par gare distincte présente dans ce relevé, pas par ligne
    # (plusieurs trains peuvent passer par la même gare au même moment).
    weather_cache = {}
    new_rows = []
    for row in raw_rows:
        gare = stop_names.get(row["stop_id"])
        if gare not in weather_cache:
            if gare in station_coords:
                lat, lon = station_coords[gare]
                weather_cache[gare] = fetch_weather(lat, lon)
            else:
                weather_cache[gare] = {"temperature_c": None, "precipitation_mm": None, "wind_speed_kmh": None, "weather_code": None}
        row.update(weather_cache[gare])

        row["type_jour"] = calendrier.type_jour(row["start_date"])
        row["vacances_scolaires"] = int(calendrier.en_vacances(row["start_date"]))

        cle_trip_id = sans_date_trip_id(row["trip_id"])
        terminus_seq = terminus_par_trajet.get(cle_trip_id)
        stop_seq = stop_sequences.get((cle_trip_id, row["stop_id"]))
        if terminus_seq is not None and stop_seq is not None:
            row["arrets_restants"] = terminus_seq - stop_seq
        else:
            row["arrets_restants"] = None

        new_rows.append(row)

    connexion = connecter_db()
    with connexion:
        connexion.executemany(
            """INSERT INTO observations (
                poll_time, trip_id, start_date, stop_id, arrival_delay_s, departure_delay_s,
                temperature_c, precipitation_mm, wind_speed_kmh, weather_code,
                type_jour, vacances_scolaires, arrets_restants
            ) VALUES (
                :poll_time, :trip_id, :start_date, :stop_id, :arrival_delay_s, :departure_delay_s,
                :temperature_c, :precipitation_mm, :wind_speed_kmh, :weather_code,
                :type_jour, :vacances_scolaires, :arrets_restants
            )""",
            new_rows,
        )
    connexion.close()

    print(f"{poll_time} : {len(new_rows)} observations Paris-Cherbourg ajoutées "
          f"(sur {len(feed.entity)} trains dans le flux national, "
          f"météo interrogée pour {len(weather_cache)} gare(s), "
          f"{n_evenements} nouvel(s) arrêt(s) supprimé(s)/trajet(s) annulé(s)).")


if __name__ == "__main__":
    main()
