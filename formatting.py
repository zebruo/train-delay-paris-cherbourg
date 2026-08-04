"""
Fonctions pures de formatage et de chargement des données de référence pour
viewer.py — aucune ne dépend de Tkinter, toutes sont calculables/testables
sans interface graphique.
"""
import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

PARIS_TZ = ZoneInfo("Europe/Paris")

REFERENCE_FILE = "reference_paris_cherbourg.csv"
GTFS_STOPS_FILE = "gtfs/stops.txt"
SEUIL_ARRET_NOTABLE = 10  # minutes


def sans_date_trip_id(trip_id):
    """Un trip_id GTFS(-RT) SNCF se termine par une date ('...:AAAAMMJJ') qui
    n'est pas stable pour un même train réel : le référentiel statique
    (reference_paris_cherbourg.csv, construit une fois à partir d'un
    instantané GTFS) échantillonne quelques dates par train, mais le flux
    temps réel republie ce même train sous un trip_id quasi identique, sauf
    ce dernier segment, à chaque nouvelle date de circulation — sans retirer
    ce suffixe avant de comparer, un train réel dont la date ne tombe pas
    pile sur une des dates échantillonnées devient invisible (mesuré :
    ~27 % des trains pertinents manqués un jour donné, voir mémoire du
    projet, 2026-07-31). Utilisé pour indexer/interroger trajet_gares,
    trajet_horaires, scheduled_times, trajet_arrets, temps_arret (voir
    build_trip_data ci-dessous) et pour filtrer le flux temps réel
    (collect_realtime.py, perturbations.py)."""
    return re.sub(r":\d{8}$", "", trip_id)


def format_poll_time(iso_string):
    """Convertit un timestamp UTC ISO (ex: collecté sur le Pi) en heure locale
    française lisible, ex: '13/07/2026 à 10:25'. Pas de secondes : la
    collecte tourne toutes les 5 min (AUTO_REFRESH_MS), donc elles ne
    reflètent que l'instant arbitraire d'exécution du script, pas une info
    utile sur les trains."""
    try:
        dt = datetime.fromisoformat(iso_string).astimezone(PARIS_TZ)
        return dt.strftime("%d/%m/%Y à %H:%M")
    except (ValueError, TypeError):
        return iso_string


def format_gare(nom):
    """Abrège 'Saint' en 'St' pour l'affichage, ex: 'Paris Saint-Lazare' -> 'Paris St-Lazare'."""
    return nom.replace("Saint-", "St-") if isinstance(nom, str) else nom


def format_gare_frise(nom):
    """Nom de gare pour la frise : abrégé comme format_gare(), puis en
    majuscules sans accents (ex: 'Paris St-Lazare' -> 'PARIS ST-LAZARE',
    'Évreux Normandie' -> 'EVREUX NORMANDIE')."""
    sans_accents = unicodedata.normalize("NFKD", format_gare(nom)).encode("ascii", "ignore").decode("ascii")
    return sans_accents.upper()


def format_heure_avec_date(heure_gtfs, start_date):
    """Convertit une heure GTFS 'HH:MM:SS' en 'HH:MM'. Le GTFS autorise des
    heures >= 24:00 pour un service commencé la veille (ex: '24:09:00' pour
    00:09 après minuit) — ramenées ici à 00-23h, avec la vraie date du
    lendemain entre parenthèses (calculée à partir de start_date, le jour de
    circulation GTFS 'YYYYMMDD') pour ne pas perdre cette info."""
    if not isinstance(heure_gtfs, str):
        return ""
    h = int(heure_gtfs[:2])
    heure_str = f"{h % 24:02d}:{heure_gtfs[3:5]}"
    if h >= 24 and start_date:
        lendemain = datetime.strptime(str(int(start_date)), "%Y%m%d") + timedelta(days=1)
        heure_str += f" ({lendemain.strftime('%d/%m')})"
    return heure_str


def calculer_temps_arret_min(arrivee, depart):
    """Durée d'arrêt en gare intermédiaire, en minutes (None si arrivée ou
    départ manquant — cas de l'origine/du terminus, qui n'ont chacun qu'une
    des deux heures). Le GTFS garantit des heures croissantes au sein d'un
    même trajet (jamais de retour en arrière, même après minuit avec la
    convention >= 24:00), donc une simple soustraction suffit."""
    if not isinstance(arrivee, str) or not isinstance(depart, str):
        return None
    vers_minutes = lambda h: int(h[:2]) * 60 + int(h[3:5])
    return vers_minutes(depart) - vers_minutes(arrivee)


def format_duree(minutes):
    """Ex: 85 -> '1h25', 45 -> '45 min'."""
    minutes = int(round(minutes))
    heures, reste = divmod(minutes, 60)
    return f"{heures}h{reste:02d}" if heures else f"{reste} min"


def format_heure_avec_arret(heure_gtfs, start_date, temps_arret_min):
    """Comme format_heure_avec_date, avec la durée d'arrêt ajoutée entre
    parenthèses quand elle dépasse SEUIL_ARRET_NOTABLE — la plupart des
    arrêts intermédiaires ne durent que 1 à 3 min, les afficher
    systématiquement n'apporterait rien."""
    heure_str = format_heure_avec_date(heure_gtfs, start_date)
    if temps_arret_min is not None and temps_arret_min >= SEUIL_ARRET_NOTABLE:
        heure_str += f" (arrêt {format_duree(temps_arret_min)})"
    return heure_str


def estimer_passage_reel(heure_gtfs, start_date, retard_min):
    """Datetime (UTC) estimée du passage réel du train à une gare : heure
    théorique GTFS (ajustée si >= 24:00, voir format_heure_avec_date) +
    retard observé. Sert à déterminer si une gare a déjà été dépassée au
    moment d'un relevé donné — utile car, une fois qu'un train apparaît
    dans le flux GTFS-RT, toutes ses gares (déjà passées ou non) continuent
    à être rapportées ensemble à chaque relevé : leur seule présence dans
    les données ne dit pas ce qui a été dépassé ou non. Retourne None si
    l'heure ou le retard sont inconnus."""
    if not isinstance(heure_gtfs, str) or pd.isna(retard_min) or not start_date:
        return None
    h = int(heure_gtfs[:2])
    base = datetime.strptime(str(int(start_date)), "%Y%m%d")
    dt_theo = base + timedelta(days=1 if h >= 24 else 0, hours=h % 24, minutes=int(heure_gtfs[3:5]))
    dt_theo = dt_theo.replace(tzinfo=PARIS_TZ)
    dt_reel = dt_theo + timedelta(minutes=float(retard_min))
    return pd.Timestamp(dt_reel.astimezone(ZoneInfo("UTC")))


def format_valeur(valeur):
    """Affiche '-' plutôt que 'nan' pour une valeur manquante (pandas NaN)."""
    return "-" if pd.isna(valeur) else valeur


def format_entier(valeur):
    """Comme format_valeur, mais sans le '.0' — pandas charge une colonne
    entière en float64 dès qu'elle contient au moins une valeur manquante
    (ex: 'arrets_restants', vide tant que le terminus du trajet n'est pas
    connu), ce qui affiche '2.0' au lieu de '2'."""
    return "-" if pd.isna(valeur) else f"{int(valeur):d}"


def format_retard(valeur):
    """Comme format_valeur, avec un '+' devant les retards strictement
    positifs (ex: '+15.0') pour les distinguer visuellement des trains à
    l'heure (0.0, sans '+')."""
    if pd.isna(valeur):
        return "-"
    return f"+{valeur}" if valeur > 0 else valeur


def format_bool_oui_non(valeur):
    """'Oui'/'Non' pour un booléen, '' si manquant (pandas NaN). Compare par
    égalité plutôt que par identité (`is True`) : selon la présence ou non de
    NaN dans la colonne, pandas peut charger ces valeurs en bool Python natif
    ou en numpy.bool_, et `numpy.bool_(True) is True` vaut False."""
    if pd.isna(valeur):
        return ""
    return "Oui" if valeur else "Non"


def cle_circulation(df):
    """Identifiant d'une circulation réelle : trip_id + start_date. Un même
    trip_id peut être réutilisé pour plusieurs circulations réelles (ex: le
    même service un jour puis un autre) — s'en tenir au trip_id seul
    mélangerait leurs relevés (voir _update_trajet_list). Utilisé pour
    restreindre/grouper par circulation plutôt que par trip_id seul."""
    return df["trip_id"] + "|" + df["start_date"].astype(str)


def derniers_par_passage_avec_date(df):
    """Comme derniers_par_passage (voir plus bas), mais conserve aussi
    poll_time (date du dernier relevé de ce passage) — utilisé par
    generer_rapport.py pour répartir le retard cumulé jour par jour dans le
    rapport mensuel, là où derniers_par_passage seul ne suffit pas."""
    return df.sort_values("poll_time").groupby(
        ["trip_id", "start_date", "gare"]
    ).agg(retard_min=("retard_min", "last"), poll_time=("poll_time", "last"))


def derniers_par_passage(df):
    """Dernière valeur de retard_min connue par passage réel (trip_id,
    start_date, gare) — pas par relevé brut, qui verrait un même passage
    compté 20-40 fois vu la fréquence de sondage du flux temps réel.
    Factorisé (identique dans viewer.py et generer_rapport.py) : base
    commune à "Retard cumulé" (somme des valeurs > 0 de cette série) et
    "Retard max" (max de ces mêmes valeurs, groupé par train) — les deux
    doivent ignorer les prédictions intermédiaires depuis corrigées, pas
    seulement les doublons de relevé. Sans ça, une prédiction ponctuelle
    revue à la baisse ensuite (ex: 50 min affichés un temps, réellement
    corrigés à 20) reste comptée comme si elle avait tenu jusqu'au bout —
    repéré par l'utilisateur, 2026-08-03."""
    return derniers_par_passage_avec_date(df)["retard_min"]


def load_reference():
    return pd.read_csv(REFERENCE_FILE)


def build_stop_names(ref):
    """Correspondance stop_id -> nom de gare. Priorité au référentiel (les
    gares réellement suivies), complétée par gtfs/stops.txt pour les
    StopArea que le flux temps réel rapporte parfois au lieu du StopPoint
    habituel (~0,2 % des relevés, un même trajet vers Rouen Rive Droite) —
    sans ça, ces gares s'affichent avec leur code technique brut
    ("StopArea:OCE87444182") au lieu de leur nom (voir mémoire du projet,
    2026-07-23)."""
    noms = dict(zip(ref["stop_id"], ref["stop_name"]))
    try:
        stops_supplementaires = pd.read_csv(GTFS_STOPS_FILE, usecols=["stop_id", "stop_name"])
        for stop_id, stop_name in zip(stops_supplementaires["stop_id"], stops_supplementaires["stop_name"]):
            noms.setdefault(stop_id, stop_name)
    except FileNotFoundError:
        pass  # gtfs/ n'est qu'un complément local optionnel, pas indispensable au lancement
    return noms


def build_trip_data(ref):
    """Un seul passage sur reference_paris_cherbourg.csv (regroupé par trip_id)
    pour construire, pour chaque trajet :
    - trajet_gares : la liste complète et ordonnée des gares théoriques du
      trajet, y compris celles jamais observées en temps réel (ex: Paris
      Saint-Lazare pendant les travaux, voir mémoire projet) ;
    - trajet_horaires : l'heure théorique brute ('HH:MM:SS', encore au format
      GTFS, éventuellement >= 24:00) à chaque arrêt, dans le même ordre.
      Priorité au départ (comme SNCF Connect, vérifié empiriquement) —
      l'écart de quelques minutes avec l'arrivée correspond au temps d'arrêt
      en gare intermédiaire ; repli sur l'arrivée pour le terminus, qui n'a
      pas d'heure de départ. Passer par format_heure_avec_date()/
      format_heure_avec_arret() pour l'affichage (nécessite aussi le jour de
      circulation réel) ;
    - scheduled_times : le même horaire théorique brut, mais indexé par
      (trip_id, stop_id) pour un accès direct ligne par ligne ;
    - trajet_arrets / temps_arret : durée d'arrêt en gare intermédiaire (en
      minutes, None pour l'origine/le terminus), dans le même ordre que
      trajet_horaires, et indexée par (trip_id, stop_id).

    Les 4 dicts sont indexés par sans_date_trip_id(trip_id), pas le trip_id
    brut du référentiel (voir cette fonction) : plusieurs lignes du
    référentiel peuvent partager le même train réel sous des dates
    échantillonnées différentes (parfois avec un horaire légèrement
    différent selon la période — vacances, service d'été/hiver...). Dans ce
    cas, seule la variante à la date la plus tardive est gardée (itération
    dans l'ordre croissant du trip_id via groupby, donc de la date) — un
    choix arbitraire mais déterministe, sans impact sur le retard réel
    (toujours donné directement par SNCF, jamais recalculé depuis ce
    référentiel) — voir mémoire du projet, 2026-07-31."""
    trajet_gares = {}
    trajet_horaires = {}
    scheduled_times = {}
    trajet_arrets = {}
    temps_arret = {}
    for trip_id, groupe in ref.groupby("trip_id"):
        cle = sans_date_trip_id(trip_id)
        groupe = groupe.sort_values("stop_sequence")
        heures = groupe["scheduled_departure"].fillna(groupe["scheduled_arrival"])
        heures_brutes = [h if isinstance(h, str) else None for h in heures]
        arrets = [
            calculer_temps_arret_min(arrivee, depart)
            for arrivee, depart in zip(groupe["scheduled_arrival"], groupe["scheduled_departure"])
        ]
        trajet_gares[cle] = groupe["stop_name"].tolist()
        trajet_arrets[cle] = arrets
        for stop_id, arret in zip(groupe["stop_id"], arrets):
            temps_arret[(cle, stop_id)] = arret
        trajet_horaires[cle] = heures_brutes
        for stop_id, heure in zip(groupe["stop_id"], heures_brutes):
            scheduled_times[(cle, stop_id)] = heure
    return trajet_gares, trajet_horaires, scheduled_times, trajet_arrets, temps_arret
