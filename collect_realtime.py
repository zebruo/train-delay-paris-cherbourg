"""
Interroge le flux temps réel SNCF (GTFS-RT) et enregistre les retards observés
pour les trajets Paris-Cherbourg (identifiés via reference_paris_cherbourg.csv),
avec la météo actuelle de la gare (Météo-France — vraie observation de
station, repli sur Open-Meteo si absente/en échec, voir fetch_weather), le
type de jour et les vacances scolaires (calendar_data.py), et le nombre
d'arrêts restants avant le terminus (reference_paris_cherbourg.csv).

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
from formatting import PARIS_TZ, build_trip_data, load_reference, sans_date_trip_id, trajet_sens
from perturbations import CANCELED, SKIPPED, detecter_evenements, enregistrer_evenements

FEED_URL = "https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_METEOFRANCE_URL = "https://public-api.meteofrance.fr/public/DPObs/v2/station/horaire"
METEOFRANCE_ETAT_FILE = "meteofrance_etat.json"
# Nombre d'heures sans le moindre succès Météo-France avant l'alerte SMS
# (demande explicite de l'utilisateur, 2026-09-04) — assez large pour ne
# pas alerter sur une simple panne ponctuelle de l'API elle-même (le token
# expire d'un coup, une vraie panne API se résout généralement bien avant
# ce délai). Détecte aussi bien une panne de l'API qu'un token expiré
# (METEOFRANCE_API_KEY, voir config.example.py) — fetch_weather retombe
# déjà sur Open-Meteo dans les deux cas, cette alerte sert juste à prévenir
# que la source la plus fiable est indisponible depuis un moment.
SEUIL_HEURES_ALERTE_SMS_METEOFRANCE = 48
OBSERVATIONS_DB = "observations.db"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS observations (
    poll_time TEXT NOT NULL,
    trip_id TEXT NOT NULL,
    start_date TEXT NOT NULL,
    stop_id TEXT NOT NULL,
    arrival_delay_s INTEGER,
    departure_delay_s INTEGER,
    arrival_time INTEGER,
    departure_time INTEGER,
    temperature_c REAL,
    precipitation_mm REAL,
    wind_speed_kmh REAL,
    weather_code INTEGER,
    type_jour TEXT,
    vacances_scolaires INTEGER,
    arrets_restants INTEGER,
    gare TEXT,
    sens TEXT,
    heure_locale INTEGER
);
CREATE INDEX IF NOT EXISTS idx_observations_trip ON observations(trip_id, start_date);
CREATE INDEX IF NOT EXISTS idx_observations_poll_time ON observations(poll_time);
"""
# gare/sens/heure_locale : ajoutées le 2026-08-15 pour permettre des
# agrégations SQL (onglet "Par jour/heure", Graphique "tout l'historique")
# sans devoir garder tout l'historique en mémoire côté app_fastapi.py (voir
# mémoire du projet — 1 Go de RAM pour 932k lignes, sans limite). Calculées
# une fois ici, à l'écriture (mêmes fonctions que preparer_donnees,
# app_fastapi.py, pour rester identiques à l'existant), plutôt qu'à chaque
# lecture. ALTER TABLE ci-dessous : une base déployée avant ce changement
# n'a pas encore ces colonnes — CREATE TABLE IF NOT EXISTS ne les ajoute
# pas à une table déjà existante, contrairement à une base neuve.
ALTER_TABLE_SQL = {
    "gare": "ALTER TABLE observations ADD COLUMN gare TEXT",
    "sens": "ALTER TABLE observations ADD COLUMN sens TEXT",
    "heure_locale": "ALTER TABLE observations ADD COLUMN heure_locale INTEGER",
    # arrival_time/departure_time : ajoutées le 2026-08-19 — heure réelle
    # (timestamp Unix, StopTimeEvent.time) rapportée par le flux pour un
    # arrêt sans correspondance théorique (stop_id StopArea:* plutôt que
    # StopPoint:*, souvent un arrêt ajouté en temps réel — horaires_par_stop
    # ne le trouve jamais, voir formatting.format_heure_reelle et mémoire du
    # projet). Distinct de arrival_delay_s/departure_delay_s (écart relatif
    # à un horaire théorique qui, ici, n'existe pas).
    "arrival_time": "ALTER TABLE observations ADD COLUMN arrival_time INTEGER",
    "departure_time": "ALTER TABLE observations ADD COLUMN departure_time INTEGER",
}


def connecter_db():
    """WAL (Write-Ahead Logging) plutôt que le mode journal par défaut :
    autorise une lecture concurrente (l'appli web) pendant qu'un insert est
    en cours, sans que l'une bloque l'autre — pertinent ici puisque la
    collecte (cron 5 min) et les requêtes de l'appli tournent sur la même
    machine, contrairement au Pi où seule la collecte écrivait."""
    connexion = sqlite3.connect(OBSERVATIONS_DB)
    connexion.execute("PRAGMA journal_mode=WAL")
    connexion.executescript(SCHEMA_SQL)
    colonnes_existantes = {row[1] for row in connexion.execute("PRAGMA table_info(observations)")}
    for colonne, sql in ALTER_TABLE_SQL.items():
        if colonne not in colonnes_existantes:
            connexion.execute(sql)
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
    """(latitude, longitude, id_station_meteofrance) par gare —
    id_station_meteofrance : station RADOME/ETENDU la plus proche (priorité
    RADOME, réseau principal mieux instrumenté — une station ETENDU proche
    peut n'avoir aucun pluviomètre, ex. CHERBOURG-HOMET, repéré en testant
    en direct, 2026-09-04), choisie à la main une fois pour ces 11 gares
    plutôt que recalculée dynamiquement à chaque appel."""
    coords = {}
    with open("stations_coords.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            coords[row["gare"]] = (row["latitude"], row["longitude"], row["id_station_meteofrance"])
    return coords


# Météo inconnue (échec Open-Meteo, ou gare sans coordonnées connues) —
# jamais mutée après coup (lue via dict.update() ou renvoyée telle quelle),
# donc une seule constante partagée suffit (audit de nettoyage, 2026-08-19 :
# ce dict littéral était dupliqué à l'identique dans fetch_weather et main()).
METEO_INCONNUE = {"temperature_c": None, "precipitation_mm": None, "wind_speed_kmh": None, "weather_code": None}


def fetch_feed():
    feed = gtfs_realtime_pb2.FeedMessage()
    with urllib.request.urlopen(FEED_URL, timeout=30) as response:
        feed.ParseFromString(response.read())
    return feed


def fetch_weather_meteofrance(id_station):
    """Observation réelle Météo-France (réseau RADOME/ETENDU, cadence
    horaire) pour une station donnée — source primaire depuis le
    2026-09-04 : Open-Meteo (même en résolution AROME ~1,5km, testé en
    direct) a raté une pluie réelle de 1,2mm à Cherbourg le 03/09/2026,
    confirmée par une vraie station au sol (voir mémoire du projet) — un
    modèle de prévision, aussi fin soit-il, n'est pas une mesure. Renvoie
    None (jamais d'exception propagée) si la clé API est absente, la
    requête échoue, ou la station ne renvoie aucune observation — dans
    tous ces cas l'appelant (fetch_weather) retombe sur Open-Meteo."""
    try:
        from config import METEOFRANCE_API_KEY
    except ImportError:
        return None
    if not METEOFRANCE_API_KEY:
        return None
    params = {"id_station": id_station, "format": "json"}
    url = f"{WEATHER_METEOFRANCE_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"apikey": METEOFRANCE_API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read())
        if not data:
            return None
        obs = data[0]
        temperature_k = obs.get("t")
        wind_speed_ms = obs.get("ff")
        precipitation_mm = obs.get("rr1")
        # round(..., 1) : la conversion Kelvin->°C/m/s->km/h introduit du
        # bruit de virgule flottante (ex: 19.80000000000001) absent des
        # valeurs déjà arrondies renvoyées telles quelles par Open-Meteo —
        # repéré par l'utilisateur sur l'affichage, 2026-09-04.
        # max(0.0, ...) sur rr1 : les pluviomètres chauffés de ce réseau
        # renvoient parfois une petite valeur négative (-0.1, la résolution
        # de l'appareil) par dérive due à l'évaporation résiduelle après
        # chauffage anti-gel — un artefact connu du capteur, pas une vraie
        # pluie négative (physiquement impossible) — repéré par
        # l'utilisateur sur plusieurs gares au même relevé, 2026-09-04.
        return {
            "temperature_c": round(temperature_k - 273.15, 1) if temperature_k is not None else None,
            "precipitation_mm": max(0.0, precipitation_mm) if precipitation_mm is not None else None,
            "wind_speed_kmh": round(wind_speed_ms * 3.6, 1) if wind_speed_ms is not None else None,
            # weather_code (code WMO synthétique) : spécifique à Open-Meteo,
            # aucun équivalent direct dans les paramètres bruts Météo-France
            # (n, etat_sol...) — colonne jamais relue ailleurs dans le
            # projet (grep, 2026-09-04), laissée à None plutôt que
            # d'inventer une correspondance approximative.
            "weather_code": None,
        }
    except Exception:
        return None


def fetch_weather(latitude, longitude, id_station_meteofrance):
    """(dict météo, True) si la donnée vient de Météo-France, sinon (dict
    météo, False) — le 2e élément n'est utilisé que pour détecter une panne
    prolongée de Météo-France (voir verifier_alerte_meteofrance), il ne
    fait pas partie de la ligne insérée en base."""
    if id_station_meteofrance:
        resultat = fetch_weather_meteofrance(id_station_meteofrance)
        if resultat is not None:
            return resultat, True
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
        }, False
    except Exception:
        return METEO_INCONNUE, False


def envoyer_sms_free_mobile(message):
    """Dupliqué de verifier_gtfs.py plutôt qu'importé : même raisonnement
    que le reste de ce fichier (chaque script top-niveau du projet reste
    indépendant des autres, voir charger_donnees/formatting.py). Best-
    effort : un échec d'envoi ne doit jamais faire planter la collecte."""
    try:
        from config import FREE_MOBILE_USER, FREE_MOBILE_PASS
    except ImportError:
        return
    params = urllib.parse.urlencode({"user": FREE_MOBILE_USER, "pass": FREE_MOBILE_PASS, "msg": message})
    try:
        # 30s (pas 15 comme fetch_weather/fetch_weather_meteofrance) : la
        # réponse HTTP de smsapi.free-mobile.fr depuis la VPS peut mettre
        # plus de 15s à revenir, alors que le SMS part bien avant — un
        # timeout à 15s faisait déclencher ce except sans refléter le vrai
        # échec/succès de l'envoi (repéré par l'utilisateur en testant,
        # 2026-09-04 : SMS bien reçu malgré un TimeoutError côté script).
        urllib.request.urlopen(f"https://smsapi.free-mobile.fr/sendmsg?{params}", timeout=30)
    except Exception:
        pass


def verifier_alerte_meteofrance(meteofrance_a_reussi, maintenant):
    """Alerte SMS unique par épisode (comme verifier_gtfs.py) si Météo-
    France n'a plus renvoyé la moindre donnée exploitable depuis
    SEUIL_HEURES_ALERTE_SMS_METEOFRANCE heures. Best-effort complet (lecture/
    écriture d'état comprises) : un souci ici ne doit jamais empêcher la
    collecte normale, déjà indépendante de Météo-France (repli Open-Meteo)."""
    try:
        try:
            with open(METEOFRANCE_ETAT_FILE, encoding="utf-8") as f:
                etat = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            etat = {}

        if meteofrance_a_reussi or "dernier_succes_iso" not in etat:
            # Reussite, ou tout premier passage (rien à comparer encore) :
            # (ré)initialise plutôt que d'alerter à tort dès le premier
            # échec rencontré sans historique de succès.
            etat = {"dernier_succes_iso": maintenant.isoformat(), "alerte_sms_envoyee": False}
        else:
            dernier_succes = datetime.fromisoformat(etat["dernier_succes_iso"])
            heures_ecoulees = (maintenant - dernier_succes).total_seconds() / 3600
            if heures_ecoulees >= SEUIL_HEURES_ALERTE_SMS_METEOFRANCE and not etat.get("alerte_sms_envoyee"):
                envoyer_sms_free_mobile(
                    f"Météo-France : aucune donnée reçue depuis plus de "
                    f"{SEUIL_HEURES_ALERTE_SMS_METEOFRANCE}h (dernier succès : "
                    f"{etat['dernier_succes_iso']}) — source probablement en panne ou "
                    f"token expiré, la collecte continue sur Open-Meteo en repli."
                )
                etat["alerte_sms_envoyee"] = True

        with open(METEOFRANCE_ETAT_FILE, "w", encoding="utf-8") as f:
            json.dump(etat, f, indent=2)
    except Exception:
        pass


def main():
    known_trip_ids, stop_names, stop_sequences, terminus_par_trajet = load_reference_data()
    # Lecture indépendante du même fichier via pandas (formatting.py) plutôt
    # que de dupliquer la logique origine/destination de trajet_sens ici :
    # un train par cron (pas par ligne), coût négligeable, et garantit un
    # résultat identique à preparer_donnees() (app_fastapi.py), qui utilise
    # exactement les mêmes fonctions.
    variantes = build_trip_data(load_reference())
    station_coords = load_station_coords()
    calendrier = Calendrier()
    feed = fetch_feed()
    maintenant = datetime.now(timezone.utc)
    poll_time = maintenant.isoformat()
    heure_locale = maintenant.astimezone(PARIS_TZ).hour

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
                # StopTimeEvent.time (timestamp Unix) : rempli par la SNCF
                # pour un arrêt sans horaire théorique de référence (ex:
                # arrêt ajouté en temps réel) — vérifié en direct sur le
                # flux national, 2026-08-19, valeurs cohérentes avec
                # l'heure réellement annoncée (SNCF Connect).
                "arrival_time": stu.arrival.time if stu.HasField("arrival") and stu.arrival.HasField("time") else None,
                "departure_time": stu.departure.time if stu.HasField("departure") and stu.departure.HasField("time") else None,
            })

    # Une requête météo par gare distincte présente dans ce relevé, pas par ligne
    # (plusieurs trains peuvent passer par la même gare au même moment).
    weather_cache = {}
    meteofrance_a_reussi = False
    new_rows = []
    for row in raw_rows:
        gare = stop_names.get(row["stop_id"])
        if gare not in weather_cache:
            if gare in station_coords:
                lat, lon, id_station_meteofrance = station_coords[gare]
                weather_cache[gare], via_meteofrance = fetch_weather(lat, lon, id_station_meteofrance)
                meteofrance_a_reussi = meteofrance_a_reussi or via_meteofrance
            else:
                weather_cache[gare] = METEO_INCONNUE
        row.update(weather_cache[gare])

        row["type_jour"] = calendrier.type_jour(row["start_date"])
        row["vacances_scolaires"] = int(calendrier.en_vacances(row["start_date"]))
        # gare : repli sur le stop_id brut si non résolu, comme
        # preparer_donnees() (app_fastapi.py, .fillna(df["stop_id"])) — pas
        # de NULL en base pour un stop_id inconnu (ex: StopArea:OCE... non
        # couvert par gtfs/stops.txt).
        row["gare"] = gare if gare is not None else row["stop_id"]
        row["sens"] = trajet_sens(row["trip_id"], variantes)
        row["heure_locale"] = heure_locale

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
                arrival_time, departure_time,
                temperature_c, precipitation_mm, wind_speed_kmh, weather_code,
                type_jour, vacances_scolaires, arrets_restants, gare, sens, heure_locale
            ) VALUES (
                :poll_time, :trip_id, :start_date, :stop_id, :arrival_delay_s, :departure_delay_s,
                :arrival_time, :departure_time,
                :temperature_c, :precipitation_mm, :wind_speed_kmh, :weather_code,
                :type_jour, :vacances_scolaires, :arrets_restants, :gare, :sens, :heure_locale
            )""",
            new_rows,
        )
    connexion.close()

    print(f"{poll_time} : {len(new_rows)} observations Paris-Cherbourg ajoutées "
          f"(sur {len(feed.entity)} trains dans le flux national, "
          f"météo interrogée pour {len(weather_cache)} gare(s), "
          f"{n_evenements} nouvel(s) arrêt(s) supprimé(s)/trajet(s) annulé(s)).")
    verifier_alerte_meteofrance(meteofrance_a_reussi, maintenant)


if __name__ == "__main__":
    main()
