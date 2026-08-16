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
CALENDRIER_FILE = "reference_paris_cherbourg_calendrier.csv"
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


RE_NUMERO_TRAIN = re.compile(r"^OCESN(\d+)[A-Z]1187_[A-Z]$")


def format_numero_train(train):
    """Numéro de train nu (ex: "OCESN852332F1187_F" -> "852332"), le même
    identifiant que celui affiché sur les horaires SNCF officiels (voir
    capture partagée par l'utilisateur, 2026-08-14) — le "train" tel que
    dérivé de trip_id ailleurs dans le projet (df["train"] =
    trip_id.str.split(":").str[0]) est correct pour filtrer/regrouper mais
    peu lisible tel quel. Motif vérifié sur les 289 trains distincts
    présents dans observations.db à cette date, sans exception (couvre
    aussi bien les trains "normaux" ...F1187_F que les bus de substitution
    ...R1187_R, ex: "OCESN446872R1187_R" -> "446872"). Renvoie la chaîne
    telle quelle si elle ne correspond pas à ce motif (un futur format SNCF
    inconnu ne doit pas produire un numéro vide ou tronqué)."""
    m = RE_NUMERO_TRAIN.match(train) if isinstance(train, str) else None
    return m.group(1) if m else train


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


def duree_theorique(depart_gtfs, arrivee_gtfs, start_date):
    """Durée théorique d'un trajet complet (premier départ -> dernière
    arrivée), à partir des heures GTFS brutes (pas déjà formatées pour
    l'affichage) et de start_date — réutilise estimer_passage_reel
    (retard=0) plutôt que de comparer les chaînes "HH:MM:SS" à la main, pour
    gérer correctement un trajet à cheval sur minuit (heure GTFS >= 24:00,
    voir le docstring de format_heure_avec_date). Partagée entre
    app_fastapi.py et viewer.py (contrairement à la plupart des fonctions de
    préparation de données, dupliquées entre les deux — celle-ci gère un cas
    limite sensible, mieux vaut une seule version à tenir à jour)."""
    depart_dt = estimer_passage_reel(depart_gtfs, start_date, 0)
    arrivee_dt = estimer_passage_reel(arrivee_gtfs, start_date, 0)
    if depart_dt is None or arrivee_dt is None:
        return ""
    minutes = int((arrivee_dt - depart_dt).total_seconds() // 60)
    return f"{minutes // 60}h{minutes % 60:02d}"


VALEUR_MANQUANTE = "–"  # tiret cadratin (U+2013), PAS un simple "-" (U+002D) :
# un "-" seul en début de cellule est interprété comme une liste à puce par
# le rendu Markdown de st.table (chaque cellule y est rendue en Markdown) —
# provoquait un "<ul><li></li></ul>" vide (le "•" visible) avec une hauteur
# de ligne différente des autres (marges par défaut d'une liste), repéré
# par l'utilisateur, 2026-08-05.


def format_valeur(valeur):
    """Affiche VALEUR_MANQUANTE plutôt que 'nan' pour une valeur manquante
    (pandas NaN). Renvoie toujours une chaîne (voir format_retard pour la
    raison : mélange str/float dans une même colonne, inoffensif dans
    Tkinter mais qui fait échouer la sérialisation Arrow de Streamlit)."""
    return VALEUR_MANQUANTE if pd.isna(valeur) else str(valeur)


def format_entier(valeur):
    """Comme format_valeur, mais sans le '.0' — pandas charge une colonne
    entière en float64 dès qu'elle contient au moins une valeur manquante
    (ex: 'arrets_restants', vide tant que le terminus du trajet n'est pas
    connu), ce qui affiche '2.0' au lieu de '2'."""
    return VALEUR_MANQUANTE if pd.isna(valeur) else f"{int(valeur):d}"


def format_retard(valeur):
    """Comme format_valeur, avec un '+' devant les retards strictement
    positifs (ex: '+15.0') pour les distinguer visuellement des trains à
    l'heure (0.0, sans '+'). Renvoie toujours une chaîne (jamais le float
    brut) : Tkinter affichait les deux de façon identique (Treeview
    stringifie tout), mais un mélange str/float dans la même colonne fait
    échouer la sérialisation Arrow de Streamlit (app_streamlit.py, repéré
    2026-08-04)."""
    if pd.isna(valeur):
        return VALEUR_MANQUANTE
    return f"+{valeur}" if valeur > 0 else str(valeur)


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
    restreindre/grouper par circulation plutôt que par trip_id seul.
    astype(str) sur les deux colonnes (pas seulement start_date) : trip_id
    peut être de type category (voir app_fastapi.preparer_donnees, VPS,
    2026-08-13) — l'opérateur + n'est pas défini entre Categorical et str,
    contrairement à un Series object normal."""
    return df["trip_id"].astype(str) + "|" + df["start_date"].astype(str)


def titre_dynamique_jour_heure(base, stats, colonne_valeur, labels, formater_label, formater_valeur, seuil_fiable):
    """Titre dynamique "{base} — max : {label} ({valeur})" pour les 4
    graphiques concernés de l'onglet "Par jour / heure" (retard moyen/%
    en retard, jour et heure) — ignore les catégories peu fiables (moins de
    seuil_fiable relevés), mêmes hachures grisées que les barres elles-mêmes,
    pour ne pas mettre en avant une catégorie non représentative. Reste sur
    le titre statique `base` si aucune catégorie n'est fiable."""
    candidats = [
        (label, valeur)
        for label, valeur, n in zip(labels, stats[colonne_valeur], stats["n"])
        if pd.notna(valeur) and n >= seuil_fiable
    ]
    if not candidats:
        return base
    label, valeur = max(candidats, key=lambda c: c[1])
    return f"{base} — max : {formater_label(label)} ({formater_valeur(valeur)})"


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


def format_min_sans_zero(valeur):
    """Une décimale, sauf si elle est nulle (ex: '2' plutôt que '2.0', mais
    '3.6' reste '3.6') — utilisé pour les moyennes affichées dans les barres
    de stats, où ".0" n'apporte rien et alourdit la lecture."""
    arrondi = round(valeur, 1)
    return f"{arrondi:.0f}" if arrondi == int(arrondi) else f"{arrondi:.1f}"


def texte_categorie_maximale(serie, mot_singulier, mot_pluriel, formater_nom, formater_valeur):
    """Motif partagé par "Gare la + touchée" et "Retard max" (calculer_stats_
    bloc ci-dessous) : parmi une Series pandas indexée par nom de catégorie,
    le(s) nom(s) au maximum. N'utilise PAS idxmax() : il ne renverrait que la
    première catégorie dans l'ordre alphabétique en cas d'égalité — arbitraire
    du point de vue de l'utilisateur, et les égalités sont courantes ici
    (retards fréquemment des valeurs rondes) — donc toutes les catégories à
    égalité sont listées (plafond à 3, "+N autres" au-delà, même convention
    que les exemples de verifier_gtfs.py — demande explicite de
    l'utilisateur, 2026-08-15). mot_singulier/mot_pluriel vides ("") pour
    omettre le préfixe (Gare la + touchée n'a pas de mot avant les noms de
    gare, contrairement à "train"/"trains"). Renvoie (texte, pluriel) — ce
    2e booléen (≥ 2 catégories à égalité) sert à accorder un label externe
    au pluriel ("Gare la + touchée" → "Gare les + touchées", demande de
    l'utilisateur 2026-08-16) quand le mot lui-même vit dans le label plutôt
    que dans le texte produit ici (mot_singulier/mot_pluriel vides). Même
    motif que la version SQL équivalente (app_fastapi._texte_categorie_
    maximale, sur des tuples plutôt qu'une Series — pas factorisé entre
    pandas et SQL)."""
    if serie.empty or serie.max() <= 0:
        return "aucun retard significatif", False
    valeur_max = serie.max()
    a_egalite = serie[serie == valeur_max].index.tolist()
    noms = [formater_nom(n) for n in a_egalite[:3]]
    suffixe = f" (+{len(a_egalite) - 3} autres)" if len(a_egalite) > 3 else ""
    pluriel = len(a_egalite) > 1
    mot = mot_pluriel if pluriel else mot_singulier
    debut = f"{mot} {', '.join(noms)}{suffixe}" if mot else f"{', '.join(noms)}{suffixe}"
    return f"{debut} → {formater_valeur(valeur_max)}", pluriel


def calculer_stats_bloc(df):
    """Calcule le bloc de statistiques partagé entre la barre du haut de
    viewer.py (_render_stats) et sa ligne de stats par période de l'onglet
    Graphique (_render_chart) : ratio de circulations perturbées, retard
    cumulé, gare la + touchée et retard max par train — mêmes formules dans
    les deux cas, seul le DataFrame passé en argument change (historique
    filtré complet, ou la période choisie seulement). Factorisé ici (pas
    seulement dans viewer.py) car app_streamlit.py en a besoin aussi, sans
    dépendre de tkinter. Suppose df non vide sur retard_min (les appelants
    vérifient déjà ce cas avant d'appeler cette fonction)."""
    circulation = cle_circulation(df)
    total = circulation.nunique()
    en_retard = circulation[df["retard_min"] > 0].nunique() if total else 0

    # Une seule valeur par passage réel (dernier relevé connu), pas une par
    # relevé : un même passage est vu à plusieurs relevés tant qu'il reste
    # dans la fenêtre du flux temps réel, sommer directement sur df le
    # compterait autant de fois (voir mémoire du projet, 2026-07-28).
    derniers = derniers_par_passage(df)
    passages_impactes = derniers[derniers > 0]
    heures, minutes = divmod(round(passages_impactes.sum()), 60)

    # Même correctif dans les 2 blocs ci-dessous (idxmax() choisirait
    # arbitrairement la première catégorie par ordre alphabétique en cas
    # d'égalité) — une égalité exacte entre moyennes (valeur continue, Gare
    # la + touchée) est plus rare qu'un retard max souvent rond (Retard max),
    # mais reste possible (peu de relevés sur une gare, ou gares au
    # comportement identique).
    moyennes_par_gare = df.groupby("gare")["retard_min"].mean()
    pire_gare_texte, pire_gare_pluriel = texte_categorie_maximale(
        moyennes_par_gare, "", "", lambda g: g, lambda v: f"moy {format_min_sans_zero(v)} min",
    )
    label_pire_gare = "Gare les + touchées" if pire_gare_pluriel else "Gare la + touchée"

    # Basé sur la dernière valeur connue par passage (derniers ci-dessus),
    # pas le maximum brut sur tous les relevés : sinon une prédiction
    # ponctuelle depuis corrigée (ex: 50 min révisés ensuite à 20) resterait
    # affichée comme "le" retard max, alors que la réalité est très
    # différente — repéré par l'utilisateur, 2026-08-03.
    # .astype(str) avant .str.split() : trip_id peut être de type category
    # (voir app_fastapi.preparer_donnees/viewer.py load_local_data) — sur un
    # CategoricalIndex, .str.split(":") ne renvoie pas de vraies listes mais
    # la représentation texte de la liste (ex: "['A', 'B']" au lieu de
    # ['A', 'B']), donc .str[0] ne récupère que le caractère "[" — bug réel
    # rencontré (affichait "train [ → X min"), reproduit indépendamment de
    # la version de pandas (3.0.3 et 3.0.5 testées), corrigé le 2026-08-14.
    # Masqué en pratique sur la VPS avant ce correctif : le cache incrémental
    # de app_fastapi.charger_observations() finit par dégrader trip_id de
    # category à str après assez de cycles de pd.concat (vérifié : 50 concats
    # suffisent), ce qui évite le bug par accident sans le corriger — un
    # simple redémarrage du service (rechargement à froid, donc de nouveau
    # category) le fait réapparaître, comme ça a été le cas ce jour-là.
    train_par_passage = derniers.index.get_level_values("trip_id").astype(str).str.split(":").str[0]
    maximums_par_train = derniers.groupby(train_par_passage).max()
    # "train"/"trains" (pluriel éventuel) vit déjà dans le texte lui-même
    # (mot_singulier/mot_pluriel non vides ci-dessus) — pas besoin du 2e
    # élément du tuple ici, contrairement à Gare la + touchée un peu plus
    # haut (voir label_pire_gare).
    retard_max_texte, _ = texte_categorie_maximale(
        maximums_par_train, "train", "trains", format_numero_train, lambda v: f"{v:.0f} min",
    )

    return dict(
        total=total, en_retard=en_retard,
        heures=heures, minutes=minutes, nb_passages_impactes=len(passages_impactes),
        moyen=df["retard_min"].mean(),
        pire_gare_texte=pire_gare_texte,
        label_pire_gare=label_pire_gare,
        retard_max_texte=retard_max_texte,
    )


# Codes 5 lettres "maison" (pas des codes officiels SNCF confirmés), utilisés
# uniquement pour raccourcir l'affichage de la colonne "Sens".
STATION_CODES = {
    "Cherbourg": "CHERB",
    "Valognes": "VALOG",
    "Carentan": "CAREN",
    "Lison": "LISON",
    "Bayeux": "BAYEU",
    "Caen": "CAEN",
    "Lisieux": "LISIE",
    "Bernay": "BERNA",
    "Évreux Normandie": "EVREU",
    "Paris Saint-Lazare": "PARIS",
    "Mantes-la-Jolie": "MANTE",
    "Coutances": "COUTA",
    "Dol-de-Bretagne": "DOLDE",
    "Elbeuf - Saint-Aubin": "ELBEU",
    "Granville": "GRANV",
    "Oissel": "OISSE",
    "Rennes": "RENNE",
    "Rouen Rive Droite": "ROUEN",
    "Saint-Lô": "ST-LÔ",
    "Serquigny": "SERQU",
    "Trouville - Deauville": "TROUV",
    "Vernon - Giverny": "VERNO",
}


def trajet_sens(trip_id, variantes):
    """Sens d'un trajet ('CHERB → PARIS') à partir de ses gares de départ et
    d'arrivée théoriques (variantes, voir build_trip_data) — chaîne vide si
    le trajet théorique du train n'est plus dans le référentiel actuel
    (voir sans_date_trip_id) ou n'a qu'une seule gare.

    Pas sensible à la date, contrairement à choisir_variante : l'origine et
    la destination d'un train ne changent pas d'une variante d'horaire à
    l'autre en pratique (seuls les horaires précis varient selon la
    période) — la dernière variante connue suffit donc ici, plutôt que de
    payer le coût d'un .apply(axis=1) juste pour ce champ (appelé via un
    simple .map() par trip_id, sans accès à start_date)."""
    liste = variantes.get(sans_date_trip_id(trip_id))
    if not liste:
        return ""
    gares = liste[-1]["gares"]
    if len(gares) < 2:
        return ""
    origine = STATION_CODES.get(gares[0], gares[0])
    destination = STATION_CODES.get(gares[-1], gares[-1])
    return f"{origine} → {destination}"


def load_reference():
    # dtype=str sur service_id : sinon pandas le déduit numérique et perd le
    # zéro-padding (ex. "000399" -> 399), le désynchronisant des clés du
    # calendrier chargées par load_calendrier() (elles aussi en str) — bug
    # réel rencontré le 2026-08-12 : choisir_variante() ne trouvait plus
    # jamais de correspondance et retombait systématiquement sur le repli
    # "dernière variante", recréant silencieusement le bug que ce correctif
    # visait justement à corriger.
    return pd.read_csv(REFERENCE_FILE, dtype={"service_id": str})


def load_calendrier():
    """service_id -> ensemble des dates valides ('AAAAMMJJ'), depuis
    CALENDRIER_FILE (généré par build_reference.py à partir de
    calendar_dates.txt) — ce flux GTFS national n'a pas de calendar.txt
    (motif hebdomadaire + exceptions), juste des lignes "service_id,date"
    explicites, une par date réellement valide. Utilisé par
    choisir_variante() pour savoir, parmi les variantes d'horaire connues
    d'un même train, laquelle est valide à une date donnée."""
    calendrier = {}
    df = pd.read_csv(CALENDRIER_FILE, dtype=str)
    for service_id, date in zip(df["service_id"], df["date"]):
        calendrier.setdefault(service_id, set()).add(date)
    return calendrier


def choisir_variante(variantes, calendrier, trip_id, start_date):
    """Parmi les variantes d'horaire connues pour ce train (plusieurs
    peuvent exister pour un même trip_id sans date — voir build_trip_data,
    ex: horaire légèrement différent en vacances/service d'été), choisit
    celle dont le service_id est réellement valide à start_date (voir
    load_calendrier) — plutôt que de toujours retenir la même variante
    quelle que soit la date interrogée. Bug réel trouvé le 2026-08-12 :
    l'heure théorique affichée pour un train ne correspondait pas à la
    vraie fiche horaire SNCF pour le jour consulté (2 gares décalées de
    1-2 min sur 7, train 3306 du 12/08 — la variante retenue jusqu'ici
    était toujours "la plus tardive dans l'ordre du trip_id", sans lien
    avec la date réellement demandée).

    Repli sur la dernière variante connue (comportement d'avant ce
    correctif) si aucune ne correspond exactement — ex: une date hors de
    la fenêtre couverte par calendar_dates.txt (le GTFS national n'expose
    qu'une fenêtre glissante d'environ 151 jours, voir mémoire du projet).
    Retourne None si le train est totalement inconnu du référentiel."""
    liste = variantes.get(sans_date_trip_id(trip_id))
    if not liste:
        return None
    if start_date not in (None, ""):
        cible = str(int(start_date))
        for variante in liste:
            if cible in calendrier.get(variante["service_id"], ()):
                return variante
    return liste[-1]


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
    """Un seul passage sur reference_paris_cherbourg.csv (regroupé par
    trip_id brut) pour construire, pour chaque train (sans_date_trip_id),
    la liste de toutes ses variantes d'horaire connues — plusieurs lignes
    du référentiel peuvent partager le même train réel sous des dates
    échantillonnées différentes (parfois avec un horaire légèrement
    différent selon la période : vacances, service d'été/hiver...), voir
    choisir_variante() pour comment la bonne variante est choisie selon la
    date réellement demandée (PAS géré ici — ce n'était pas le cas avant
    le 2026-08-12, où seule la variante la plus tardive était gardée sans
    lien avec la date interrogée, un bug réel trouvé ce jour-là).

    Retourne un seul dict, `variantes`, indexé par sans_date_trip_id(trip_id)
    -> liste de variantes (une par service_id distinct rencontré), chacune
    un dict :
    - service_id : pour le croisement avec calendrier (voir load_calendrier) ;
    - gares : liste ordonnée des gares théoriques du trajet, y compris
      celles jamais observées en temps réel (ex: Paris Saint-Lazare
      pendant les travaux, voir mémoire projet) ;
    - horaires : l'heure théorique brute ('HH:MM:SS', encore au format
      GTFS, éventuellement >= 24:00) à chaque arrêt, dans le même ordre que
      gares. Priorité au départ (comme SNCF Connect, vérifié
      empiriquement) — l'écart de quelques minutes avec l'arrivée
      correspond au temps d'arrêt en gare intermédiaire ; repli sur
      l'arrivée pour le terminus, qui n'a pas d'heure de départ. Passer
      par format_heure_avec_date()/format_heure_avec_arret() pour
      l'affichage (nécessite aussi le jour de circulation réel) ;
    - arrets : durée d'arrêt en gare intermédiaire (en minutes, None pour
      l'origine/le terminus), même ordre que gares/horaires ;
    - horaires_par_stop / arrets_par_stop : les deux mêmes listes, mais
      indexées par stop_id pour un accès direct ligne par ligne (préparer_
      données, dans app_fastapi.py/viewer.py, itère sur des relevés bruts
      identifiés par stop_id, pas par position dans la liste)."""
    variantes = {}
    for trip_id, groupe in ref.groupby("trip_id"):
        cle = sans_date_trip_id(trip_id)
        groupe = groupe.sort_values("stop_sequence")
        heures = groupe["scheduled_departure"].fillna(groupe["scheduled_arrival"])
        heures_brutes = [h if isinstance(h, str) else None for h in heures]
        arrets = [
            calculer_temps_arret_min(arrivee, depart)
            for arrivee, depart in zip(groupe["scheduled_arrival"], groupe["scheduled_departure"])
        ]
        stop_ids = groupe["stop_id"].tolist()
        variantes.setdefault(cle, []).append({
            "service_id": str(groupe["service_id"].iloc[0]),
            "gares": groupe["stop_name"].tolist(),
            "horaires": heures_brutes,
            "arrets": arrets,
            "horaires_par_stop": dict(zip(stop_ids, heures_brutes)),
            "arrets_par_stop": dict(zip(stop_ids, arrets)),
        })
    return variantes
