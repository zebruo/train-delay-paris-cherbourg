"""
Construit une table de référence des trajets desservant (au moins un tronçon
de) la ligne Paris-Cherbourg, à partir des horaires théoriques (GTFS
statique), pour ensuite reconnaître ces trajets dans le flux temps réel et
comparer heure prévue / heure réelle.
"""
import csv
import io
import json
import os
import shutil
import subprocess
import urllib.request
import zipfile

from config import CHEMIN_DISTANT_PI

# Codes gare des 11 gares de la ligne. Un trajet est retenu s'il en dessert
# au moins 2 — capture aussi bien les liaisons de bout en bout (Paris-
# Cherbourg) que les dessertes régionales partielles (Bernay-Caen,
# Lisieux-Paris, etc.), pas seulement les trajets touchant Cherbourg.
GARES_LIGNE = {
    "87384008": "Paris Saint-Lazare",
    "87381509": "Mantes-la-Jolie",
    "87387001": "Évreux Normandie",
    "87444299": "Bernay",
    "87444265": "Lisieux",
    "87444000": "Caen",
    "87444067": "Bayeux",
    "87447219": "Lison",
    "87447243": "Carentan",
    "87447284": "Valognes",
    "87444877": "Cherbourg",
}
GTFS_DIR = "gtfs"
# Zip GTFS national SNCF (celui téléchargeable depuis la page "le fichier
# GTFS en vigueur" de transport.data.gouv.fr) — utilisé par verifier_gtfs.py
# pour le téléchargement quotidien de comparaison (pas par ce script, dont le
# téléchargement/extraction dans GTFS_DIR reste manuel).
GTFS_URL = "https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip"
META_FILE = "reference_paris_cherbourg.meta.json"
REFERENCE_FILE = "reference_paris_cherbourg.csv"
# Compagnon de REFERENCE_FILE : dates de validité (calendar_dates.txt, ce
# flux GTFS n'a pas de calendar.txt — chaque service_id liste explicitement
# ses dates valides une par une) restreintes aux service_id réellement
# utilisés par nos trajets — permet à formatting.py de choisir, pour un
# même train, la bonne variante d'horaire selon la date réelle plutôt que
# de deviner (voir build_trip_data, mémoire du projet 2026-08-12).
CALENDRIER_FILE = "reference_paris_cherbourg_calendrier.csv"


def load_stop_names():
    names = {}
    with open(f"{GTFS_DIR}/stops.txt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            names[row["stop_id"]] = row["stop_name"]
    return names


def trips_sur_la_ligne():
    """Premier passage sur stop_times.txt : pour chaque trajet, les gares de
    la ligne qu'il dessert. Un trip_id ne suffit pas ici (il n'encode que
    l'origine/la destination, pas les arrêts intermédiaires) — il faut donc
    bien regarder chaque arrêt réel."""
    gares_par_trajet = {}
    with open(f"{GTFS_DIR}/stop_times.txt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            # stop_id est du type "StopPoint:OCETrain TER-87444877" (le
            # préfixe varie : OCETrain, OCECar en cas de bus de substitution,
            # OCENavette...) — seul le code numérique final nous intéresse.
            code = row["stop_id"].rsplit("-", 1)[-1]
            gare = GARES_LIGNE.get(code)
            if gare:
                gares_par_trajet.setdefault(row["trip_id"], set()).add(gare)
    return {trip_id for trip_id, gares in gares_par_trajet.items() if len(gares) >= 2}


def load_service_ids(trip_ids_retenus):
    """trip_id -> service_id (trips.txt), restreint à trip_ids_retenus —
    un même train réel (même dateless trip_id, voir sans_date_trip_id)
    peut avoir plusieurs variantes d'horaire selon la période, chacune
    associée à un service_id différent qui définit ses dates réelles de
    validité (voir enregistrer_calendrier)."""
    service_par_trip = {}
    with open(f"{GTFS_DIR}/trips.txt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["trip_id"] in trip_ids_retenus:
                service_par_trip[row["trip_id"]] = row["service_id"]
    return service_par_trip


def enregistrer_calendrier(service_ids_utiles):
    """Écrit CALENDRIER_FILE : les dates réellement valides (calendar_dates.txt)
    pour chacun des service_id utilisés par nos trajets — ce flux GTFS n'a
    pas de calendar.txt (motif hebdomadaire + exceptions), juste des lignes
    "service_id,date,exception_type" explicites (toujours exception_type=1
    en pratique, vérifié). Filtré aux service_ids_utiles pour rester petit
    (quelques milliers de lignes, contre 230k+ dans le fichier source pour
    tout le réseau SNCF) — inutile de garder les autres."""
    lignes = []
    with open(f"{GTFS_DIR}/calendar_dates.txt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["service_id"] in service_ids_utiles and row["exception_type"] == "1":
                lignes.append({"service_id": row["service_id"], "date": row["date"]})
    with open(CALENDRIER_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["service_id", "date"])
        writer.writeheader()
        writer.writerows(lignes)
    return len(lignes)


def enregistrer_meta():
    """Note, dans META_FILE, la version du GTFS (feed_version, lu localement
    dans GTFS_DIR/feed_info.txt) utilisée pour générer
    reference_paris_cherbourg.csv — utilisé par verifier_gtfs.py pour
    afficher depuis quand la référence date. Pas de requête réseau ici : le
    Last-Modified du zip s'est révélé changer quotidiennement (la SNCF
    régénère l'export chaque jour, même sans changement substantiel — la
    fenêtre glissante de 151 jours avance d'un jour), donc inutilisable comme
    indicateur de fraîcheur — voir verifier_gtfs.py, qui compare directement
    le contenu plutôt que cette date."""
    with open(f"{GTFS_DIR}/feed_info.txt", encoding="utf-8") as f:
        feed_version = next(csv.DictReader(f))["feed_version"]
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump({"feed_version": feed_version}, f, indent=2)


def main():
    stop_names = load_stop_names()
    trip_ids_retenus = trips_sur_la_ligne()
    service_par_trip = load_service_ids(trip_ids_retenus)

    # Second passage : tous les arrêts réels de ces trajets (pas seulement
    # ceux sur nos 11 gares), pour ne pas tronquer leur trajet théorique.
    reference_rows = []
    with open(f"{GTFS_DIR}/stop_times.txt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["trip_id"] in trip_ids_retenus:
                stop_id = row["stop_id"]
                reference_rows.append({
                    "trip_id": row["trip_id"],
                    "stop_id": stop_id,
                    "stop_name": stop_names.get(stop_id, "?"),
                    "stop_sequence": row["stop_sequence"],
                    "scheduled_arrival": row["arrival_time"],
                    "scheduled_departure": row["departure_time"],
                    "service_id": service_par_trip.get(row["trip_id"], ""),
                })

    with open(REFERENCE_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=reference_rows[0].keys())
        writer.writeheader()
        writer.writerows(reference_rows)

    trip_ids = {r["trip_id"] for r in reference_rows}
    print(f"{len(reference_rows)} arrêts de référence sauvegardés, "
          f"couvrant {len(trip_ids)} trajets distincts.")

    service_ids_utiles = set(service_par_trip.values())
    nb_dates = enregistrer_calendrier(service_ids_utiles)
    print(f"{nb_dates} dates de validité sauvegardées dans {CALENDRIER_FILE} "
          f"({len(service_ids_utiles)} service_id distincts).")

    enregistrer_meta()


def telecharger_et_regenerer():
    """Équivalent complet de télécharger le zip GTFS national à la main
    (page "le fichier GTFS en vigueur" de transport.data.gouv.fr) +
    l'extraire dans GTFS_DIR (écrase le contenu existant) + lancer main() —
    en un seul appel, pour le bouton "Régénérer" de viewer.py (voir
    onglet_verification_gtfs.py). Local uniquement : ne touche ni au Pi ni
    à quoi que ce soit hors de ce dossier — le déploiement du nouveau
    reference_paris_cherbourg.csv vers le Pi (rsync) reste une étape
    manuelle séparée, volontairement pas automatisée ici (même logique que
    verifier_gtfs.py : une régénération reste une décision consciente, mais
    une fois prise, pas de raison de la faire à la main)."""
    with urllib.request.urlopen(GTFS_URL, timeout=120) as r:
        zip_bytes = r.read()

    if os.path.isdir(GTFS_DIR):
        shutil.rmtree(GTFS_DIR)
    os.makedirs(GTFS_DIR, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        z.extractall(GTFS_DIR)

    main()


def lire_feed_version_distante(pi_host, chemin_distant=CHEMIN_DISTANT_PI, timeout=10):
    """Lit par SSH le feed_version actuellement déployé sur le Pi (son
    META_FILE) — pour comparer avant un déploiement (voir bouton "Déployer
    vers le Pi" dans onglet_verification_gtfs.py). Retourne None si le Pi
    est injoignable, ou si le fichier n'existe pas encore là-bas (premier
    déploiement)."""
    commande = f"cat {chemin_distant}/{META_FILE} 2>/dev/null"
    try:
        resultat = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", pi_host, commande],
            capture_output=True, text=True, timeout=timeout,
        )
        return json.loads(resultat.stdout).get("feed_version")
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return None


def deployer_vers_pi(pi_host, chemin_distant=CHEMIN_DISTANT_PI, timeout=30):
    """Envoie REFERENCE_FILE + CALENDRIER_FILE + META_FILE vers le Pi par
    rsync — pour le bouton "Déployer vers le Pi" de viewer.py. Ne relance
    pas verifier_gtfs.py là-bas ni ne touche à autre chose : ça reste à la
    charge de l'appelant (voir onglet_verification_gtfs.py), pour garder
    cette fonction simple et testable isolément. Retourne True/False."""
    try:
        subprocess.run(
            ["rsync", "-az", REFERENCE_FILE, CALENDRIER_FILE, META_FILE, f"{pi_host}:{chemin_distant}/"],
            check=True, capture_output=True, timeout=timeout,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


if __name__ == "__main__":
    main()
