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


def format_heure_reelle(timestamp):
    """Convertit un timestamp Unix (StopTimeEvent.arrival.time/departure.time
    GTFS-RT) en 'HH:MM' heure de Paris, préfixé "~" pour le distinguer
    visuellement d'une vraie heure théorique (format_heure_avec_date) — cette
    valeur vient du temps réel, pas de l'horaire statique, et n'existe que
    pour les arrêts hors référentiel (stop_id StopArea:* sans correspondance
    StopPoint:*, souvent un arrêt ajouté en temps réel, voir mémoire du
    projet). "" si timestamp est None (rien capté pour cet arrêt)."""
    if timestamp is None or pd.isna(timestamp):
        return ""
    dt = datetime.fromtimestamp(int(timestamp), tz=ZoneInfo("UTC")).astimezone(PARIS_TZ)
    return f"~{dt.strftime('%H:%M')}"


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


def calculer_periode(nom_periode, maintenant_utc):
    """Bornes (heure locale Paris) de la période couverte par un rapport,
    calées sur des horaires fixes plutôt que sur une fenêtre glissante de
    N heures avant l'instant d'exécution : une fenêtre glissante ferait
    dériver la période d'un jour sur l'autre selon l'heure exacte de
    l'appel (PC éteint/en veille, tâche planifiée en retard...) — peu
    comparable et pas intuitif ("hier"/"cette semaine" doivent correspondre
    au calendrier). 2h du matin est choisi comme frontière car c'est le
    creux du trafic nocturne (peu ou pas de trains, voir mémoire du projet) :
    une circulation a très peu de chances d'être en plein trajet pile à cet
    instant, ce qui limite le risque d'en couper une en deux périodes
    consécutives — complète le filtre "circulations arrivées uniquement"
    plutôt que de le contredire.
    Quotidien : le dernier cycle 2h→2h déjà terminé (ex: exécuté le 28 à
    07h00, période = 27 à 2h → 28 à 2h). Hebdomadaire : du lundi 2h au lundi
    suivant 2h, le dernier cycle déjà terminé. Mensuel : même principe à
    l'échelle du mois calendaire (1er du mois 2h → 1er du mois suivant 2h).
    Partagée entre generer_rapport.py et l'onglet web "Rapports"."""
    maintenant_local = maintenant_utc.tz_convert(PARIS_TZ)
    if nom_periode == "hebdomadaire":
        fin_local = (maintenant_local - pd.Timedelta(days=maintenant_local.weekday())).normalize() \
            + pd.Timedelta(hours=2)
        if fin_local > maintenant_local:
            fin_local -= pd.Timedelta(days=7)
        debut_local = fin_local - pd.Timedelta(days=7)
    elif nom_periode == "mensuel":
        # pd.DateOffset(months=1) gère les mois de longueur différente
        # correctement (contrairement à un simple pd.Timedelta(days=30)).
        fin_local = maintenant_local.replace(day=1).normalize() + pd.Timedelta(hours=2)
        if fin_local > maintenant_local:
            fin_local -= pd.DateOffset(months=1)
        debut_local = fin_local - pd.DateOffset(months=1)
    else:
        fin_local = maintenant_local.normalize() + pd.Timedelta(hours=2)
        if fin_local > maintenant_local:
            fin_local -= pd.Timedelta(days=1)
        debut_local = fin_local - pd.Timedelta(hours=24)
    return debut_local, fin_local


# Noms de mois en dur (pas strftime("%B")) : dépendrait de la locale système
# du serveur, jamais garantie en français — même choix que les jours de
# semaine (JOURS_ORDRE, app_fastapi.py / _EXPR_JOUR_SEMAINE, SQL).
MOIS_NOMS = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
]
# Élision "de" -> "d'" devant une voyelle ("Mois d'Avril", pas "Mois de
# Avril") — seuls ces 3 mois du français commencent par une voyelle (Août :
# le h est muet, la liaison se fait comme devant une vraie voyelle).
MOIS_AVEC_ELISION = {"Avril", "Août", "Octobre"}


def texte_periode_rapport(nom_periode, debut_local, fin_local):
    """Texte décrivant la période d'un rapport (PDF et onglet web
    "Rapports") — "Journée du :"/"Semaine du :" avec les bornes complètes ;
    "Mois de X" seul pour le mensuel (pas de bornes horaires, juste le nom
    du mois) — demande explicite de l'utilisateur, 2026-08-17. debut_local
    est toujours le 1er du mois à 2h pour "mensuel" (calculer_periode), son
    mois est donc celui du rapport."""
    if nom_periode == "quotidien":
        return f"Journée du : {debut_local.strftime('%d/%m/%Y %Hh%M')} au {fin_local.strftime('%d/%m/%Y %Hh%M')}"
    if nom_periode == "hebdomadaire":
        return f"Semaine du : {debut_local.strftime('%d/%m/%Y %Hh%M')} au {fin_local.strftime('%d/%m/%Y %Hh%M')}"
    nom_mois = MOIS_NOMS[debut_local.month - 1]
    prefixe = "d'" if nom_mois in MOIS_AVEC_ELISION else "de "
    return f"Mois {prefixe}{nom_mois}"


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
    """Titre dynamique "{base} — max : {label} ({valeur})" pour les
    graphiques par catégorie (onglets "Par jour / heure" et "Rapports"
    mensuel — retard moyen/% en retard par jour/heure/gare) — ignore les
    catégories peu fiables (moins de seuil_fiable CIRCULATIONS DISTINCTES,
    colonne stats["n_circulations"] — PAS de relevés bruts, voir le
    commentaire de SEUIL_FIABLE dans app_fastapi.py), mêmes hachures
    grisées que les barres elles-mêmes, pour ne pas mettre en avant une
    catégorie non représentative. Reste sur le titre statique `base` si
    aucune catégorie n'est fiable."""
    candidats = [
        (label, valeur)
        for label, valeur, n in zip(labels, stats[colonne_valeur], stats["n_circulations"])
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
    que dans le texte produit ici (mot_singulier/mot_pluriel vides).
    Réutilisée telle quelle par app_fastapi.py pour ses résultats SQL
    (converti en Series via pd.Series(dict(lignes)) — une copie locale sur
    tuples existait avant d'être fusionnée ici, audit de nettoyage,
    2026-08-19)."""
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


def _gares_extremes(trip_id, variantes):
    """Gare de départ et d'arrivée théoriques d'un trajet (variantes, voir
    build_trip_data) — None si le trajet théorique du train n'est plus dans
    le référentiel actuel (voir sans_date_trip_id) ou n'a qu'une seule gare.
    Lookup partagé par trajet_sens/trajet_origine_destination ci-dessous,
    qui ne diffèrent ensuite que par le formatage des 2 noms (codes abrégés
    vs noms complets) — factorisé lors d'un audit de nettoyage, 2026-08-19.

    Pas sensible à la date, contrairement à choisir_variante : l'origine et
    la destination d'un train ne changent pas d'une variante d'horaire à
    l'autre en pratique (seuls les horaires précis varient selon la
    période) — la dernière variante connue suffit donc ici, plutôt que de
    payer le coût d'un .apply(axis=1) juste pour ce champ (appelé via un
    simple .map() par trip_id, sans accès à start_date)."""
    liste = variantes.get(sans_date_trip_id(trip_id))
    if not liste:
        return None
    gares = liste[-1]["gares"]
    if len(gares) < 2:
        return None
    return gares[0], gares[-1]


def trajet_sens(trip_id, variantes):
    """Sens d'un trajet ('CHERB → PARIS'), en codes abrégés (STATION_CODES)
    — pensé pour le menu déroulant "Sens" compact. Chaîne vide si
    _gares_extremes ne trouve rien (voir son docstring)."""
    extremes = _gares_extremes(trip_id, variantes)
    if extremes is None:
        return ""
    origine, destination = extremes
    origine = STATION_CODES.get(origine, origine)
    destination = STATION_CODES.get(destination, destination)
    return f"{origine} → {destination}"


def trajet_origine_destination(trip_id, variantes):
    """Origine et destination d'un trajet en noms complets ('Coutances →
    Caen'), contrairement à trajet_sens ci-dessus (codes abrégés 'COUTA →
    CAEN'). Pour "Trajet annulé" dans Perturbations, où le nom complet
    doit rester cohérent avec "Arrêt supprimé : {gare}" juste à côté (lui
    aussi en nom complet) — repéré par l'utilisateur, 2026-08-19. Chaîne
    vide si _gares_extremes ne trouve rien (voir son docstring)."""
    extremes = _gares_extremes(trip_id, variantes)
    if extremes is None:
        return ""
    origine, destination = extremes
    return f"{format_gare(origine)} → {format_gare(destination)}"


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


def _minutes_gtfs(horaire):
    """'HH:MM:SS' (éventuellement >= 24:00:00, voir build_trip_data) ->
    minutes depuis minuit local, ramenées dans [0, 1440) par modulo. None si
    horaire est None/vide/mal formé — un trou dans l'horaire théorique
    (arrêt jamais desservi en pratique) ne doit pas faire planter l'index
    gare->heure (construire_index_gare_heure), juste être ignoré pour cette
    gare précise."""
    if not isinstance(horaire, str):
        return None
    try:
        h, m, _s = horaire.split(":")
        return (int(h) * 60 + int(m)) % 1440
    except ValueError:
        return None


def _minutes_hhmm(heure_hhmm):
    """'HH:MM' (saisie utilisateur, ou valeur d'un <select> Départ peuplé
    par heures_disponibles_pour_gare) -> minutes depuis minuit."""
    h, m = heure_hhmm.split(":")
    return int(h) * 60 + int(m)


def _distance_circulaire(a, b):
    """Écart en minutes entre deux instants de la journée, en tenant compte
    du passage minuit (ex: 23h55 et 00h05 -> 10 min, pas 1430) — sans quoi
    un train juste avant/après minuit ne matcherait jamais un horaire proche
    de l'autre côté de la frontière minuit, alors que ce sont bien 10 min
    d'écart réel."""
    d = abs(a - b)
    return min(d, 1440 - d)


def _jours_semaine_actifs(service_id, calendrier):
    """(jours de la semaine actifs, date_min, date_max) pour ce service —
    jours_actifs : entiers 0=lundi..6=dimanche (même convention que
    date.weekday()) où ce service circule au moins une fois ; date_min/
    date_max ('JJ/MM', sans année — les variantes en jeu se recouvrent
    toujours sur quelques mois, jamais sur 2 années civiles différentes en
    pratique) : période de validité complète du service, utilisée pour
    expliquer à l'utilisateur pourquoi un même train peut apparaître à 2
    horaires proches selon la période (voir variante_proche_info). Déduit
    des dates valides (load_calendrier, depuis calendar_dates.txt), qui ne
    contient pas de motif hebdomadaire tout fait ('lundi-vendredi'), juste
    des dates exactes une par une : il faut le déduire empiriquement plutôt
    que le lire directement."""
    dates = sorted(calendrier.get(service_id, ()))
    if not dates:
        return frozenset(), None, None
    jours_actifs = frozenset(datetime.strptime(d, "%Y%m%d").weekday() for d in dates)
    date_min = datetime.strptime(dates[0], "%Y%m%d").strftime("%d/%m")
    date_max = datetime.strptime(dates[-1], "%Y%m%d").strftime("%d/%m")
    return jours_actifs, date_min, date_max


def construire_index_gare_heure(variantes, calendrier):
    """gare (nom complet, ex: 'Caen', même valeur que stop_name/GARES_LIGNE_
    ORDRE) -> liste de (train, minutes, destination, jours_actifs, date_min,
    date_max, minutes_arrivee), UNE ENTRÉE PAR COUPLE (arrêt de départ, arrêt
    suivant) au sein d'une même variante — pas seulement (arrêt de départ,
    terminus final) : un train Cherbourg -> Paris Saint-Lazare dessert aussi
    Valognes/Carentan/Lison/Bayeux/Caen en chemin, et ces arrêts intermédiaires
    doivent être sélectionnables comme destination au même titre que le
    terminus (demande explicite, 2026-08-26 : la plupart des trajets réels
    sont partiels, pas bout-en-bout). Pas dédoublonnée par train — le
    dédoublonnage se fait à la résolution, voir resoudre_trains_pour_gare_
    heure, pour garder la variante la plus proche de l'heure demandée plutôt
    qu'une variante arbitraire parmi plusieurs, ex: horaire vacances vs
    normal). train = sans_date_trip_id.split(':')[0], identique à la clé
    'train' déjà utilisée partout ailleurs pour le filtre Train/format_
    numero_train — permet de réutiliser directement les fonctions de stats
    existantes (_construire_where_sql) une fois un train résolu. jours_
    actifs/date_min/date_max (voir _jours_semaine_actifs) : mémoïsés par
    service_id (plusieurs variantes/arrêts partagent souvent le même
    service_id) pour ne pas reparser les mêmes dates plusieurs fois.

    Construit UNE SEULE FOIS au démarrage (voir lifespan, app_fastapi.py) à
    partir de reference_donnees['variantes']/['calendrier'] — un seul
    passage sur toutes les variantes déjà en mémoire, pas recalculé à
    chaque requête mobile. O(arrêts²) par variante (jusqu'à 15 arrêts sur
    cette ligne, donc 225 paires au pire, 569 variantes au total) — toujours
    négligeable pour un calcul fait une seule fois.

    Le DERNIER arrêt d'une variante n'est jamais utilisé comme GARE DE
    DÉPART (boucle `for i in range(derniere)`) : à une gare intermédiaire/
    d'origine, `horaires` contient l'heure de DÉPART (build_trip_data :
    scheduled_departure en priorité) — au terminus, faute de départ, c'est
    l'heure d'ARRIVÉE qui s'y trouve à la place (repli scheduled_arrival).
    Sans cette exclusion, un train qui TERMINE à la gare demandée
    apparaissait à tort comme candidat pour 'un train au départ de cette
    gare à telle heure' (repéré en testant en direct : rechercher 'Caen,
    07h42' retournait un train dont Caen est le terminus, donc sans aucun
    départ possible à cette heure depuis Caen).

    minutes_arrivee, pour une destination INTERMÉDIAIRE (pas le terminus) :
    horaires[j] à cet arrêt est un DÉPART, pas une arrivée — on retranche le
    temps d'arrêt en gare (variante['arrets'][j], voir build_trip_data) pour
    obtenir la vraie heure d'ARRIVÉE à cette gare, plutôt que de surestimer
    légèrement la durée du trajet avec l'heure de redépart."""
    index = {}
    cache_jours = {}
    for sans_date_id, liste_variantes in variantes.items():
        train = sans_date_id.split(":")[0]
        for variante in liste_variantes:
            gares = variante["gares"]
            horaires = variante["horaires"]
            arrets = variante["arrets"]
            if len(gares) < 2:
                continue  # trajet à 0-1 arrêt connu : rien d'exploitable pour une résolution gare+heure
            service_id = variante["service_id"]
            if service_id not in cache_jours:
                cache_jours[service_id] = _jours_semaine_actifs(service_id, calendrier)
            jours_actifs, date_min, date_max = cache_jours[service_id]
            minutes_par_arret = [_minutes_gtfs(h) for h in horaires]
            derniere = len(gares) - 1
            for i in range(derniere):
                minutes_depart = minutes_par_arret[i]
                if minutes_depart is None:
                    continue
                for j in range(i + 1, len(gares)):
                    minutes_brut = minutes_par_arret[j]
                    if minutes_brut is None:
                        continue
                    if j == derniere:
                        minutes_arrivee = minutes_brut
                    else:
                        minutes_arrivee = (minutes_brut - (arrets[j] or 0)) % 1440
                    index.setdefault(gares[i], []).append(
                        (train, minutes_depart, gares[j], jours_actifs, date_min, date_max, minutes_arrivee)
                    )
    return index


def destinations_disponibles_pour_gare(gare, index_gare_heure):
    """Destinations distinctes (noms bruts du référentiel, PAS formatées
    via format_gare — la valeur doit rester comparable telle quelle au
    'destination' brut stocké dans index_gare_heure, réutilisé ensuite par
    heures_disponibles_pour_trajet/resoudre_trains_pour_gare_heure) triées
    alphabétiquement — alimente le <select> 'Gare d'arrivée' de l'écran
    Mon train/Favoris mobile, une fois la gare de départ choisie. Pas de
    filtre par jour ici : le champ 'Jour' vient après 'Gare d'arrivée' dans
    le formulaire (voir _mobile_choix_train.html), donc pas encore connu à
    ce stade."""
    return sorted({destination for _, _, destination, _, _, _, _ in index_gare_heure.get(gare, [])})


def heures_disponibles_pour_trajet(gare, destination, jour_semaine, index_gare_heure):
    """Heures théoriques ('HH:MM') triées et dédoublonnées pour le couple
    (gare de départ, gare d'arrivée), restreintes aux variantes qui
    circulent réellement au jour de la semaine choisi (jour_semaine, entier
    0=lundi..6=dimanche — voir _jours_semaine_actifs) — alimente le
    <select> 'Heure théo.' une fois gare/destination/jour choisis : une
    heure de cette liste correspond garanti à un train qui dessert bien
    cette destination précise ce jour-là, pas seulement cette gare de
    départ (sinon la liste mélangerait des trains partant vers d'autres
    destinations, ou ne circulant pas du tout ce jour-là)."""
    minutes_vues = sorted({
        m for _, m, d, jours, _, _, _ in index_gare_heure.get(gare, [])
        if d == destination and jour_semaine in jours
    })
    return [f"{m // 60:02d}:{m % 60:02d}" for m in minutes_vues]


def resoudre_trains_pour_gare_heure(
    gare, heure_hhmm, jour_semaine, index_gare_heure, destination=None, tolerance_min=0, max_candidats=3,
):
    """Résout (gare, heure, jour[, destination]) -> jusqu'à max_candidats
    trains candidats, triés par proximité horaire, DÉDOUBLONNÉS PAR TRAIN
    (une seule entrée par train, la variante la plus proche de heure_hhmm
    l'emporte — plusieurs variantes du même train, ex: horaire vacances vs
    normal, ne doivent jamais compter comme 2 candidats distincts).

    jour_semaine (entier 0=lundi..6=dimanche, voir _jours_semaine_actifs) :
    ne garde que les variantes qui circulent réellement ce jour-là — sans
    ce filtre, un horaire "habituel" du lundi pouvait résoudre vers un
    train qui ne circule en réalité que le samedi (même minute de départ,
    services différents), demande explicite de l'utilisateur (2026-08-25) :
    savoir si LA circulation qu'il prendra tel jour précis a souvent des
    problèmes, pas une moyenne mélangeant plusieurs services distincts.

    destination (nom brut du référentiel, comme renvoyé par
    destinations_disponibles_pour_gare — PAS formaté via format_gare) :
    filtre optionnel, exact. Le menu 'Départ' est déjà scopé au trajet
    (gare, destination, jour) choisi via heures_disponibles_pour_trajet,
    donc ce filtre ne change quasiment jamais le résultat en pratique —
    gardé explicite plutôt qu'implicite pour rester correct même dans le
    rare cas d'une collision exacte même gare+minute+destination+jour (2
    variantes du même trajet, ex: un service normal et un bus de
    substitution).

    tolerance_min=0 (exact) par défaut : le seul appelant (route /mobile/
    resoudre_train) reçoit toujours une heure_hhmm choisie dans le menu
    déroulant "Départ" — donc déjà un horaire théorique réel et précis,
    jamais une saisie approximative. Une tolérance > 0 ferait ressortir des
    trains à d'autres horaires déjà proposés séparément dans ce même menu
    (source de confusion constatée en usage réel). Ambiguïté encore réelle
    et conservée : 2 trains DIFFÉRENTS peuvent partager l'exacte même
    minute à la même gare, même vers la même destination et le même jour
    (rare mais possible : service normal + substitution) — c'est pourquoi
    ceci renvoie une LISTE, pas un train unique ; l'appelant (app_fastapi.py)
    affiche tous les candidats pour que l'utilisateur confirme lui-même,
    plutôt que de deviner arbitrairement.

    Toujours pas de filtrage sur une DATE précise (choisir_variante existe
    pour ça) : jour_semaine est récurrent (tous les lundis), pas une date
    cible — un train annulé ce lundi précis reste proposé ; ses stats
    historiques restent correctes (elles portent sur les VRAIS relevés
    passés), seule la disponibilité du jour même n'est pas garantie.

    Chaque candidat porte aussi 'horaire_variable_texte' (voir
    _horaire_variable_texte) : None la plupart du temps, ou une phrase du
    type 'HHhMM → valable du JJ/MM au JJ/MM.' quand ce même train a une
    AUTRE variante d'horaire à moins de SEUIL_VARIANTE_PROCHE_MIN minutes à
    cette gare (ex: 852221 à Lison, 16h56 ET 16h59 selon la période) — sert
    à expliquer, sans jamais dédoublonner les 2 entrées du menu déroulant
    (décision explicite, 2026-08-26 : l'utilisateur veut voir les 2 horaires
    car il consulte l'historique des retards d'UNE circulation précise, pas
    'quel train prendre' — les 2 variantes restent donc toutes les deux
    proposées, seule cette phrase informative change)."""
    candidats_par_train = {}
    entrees_par_train = {}
    cible = _minutes_hhmm(heure_hhmm)
    for train, minutes, dest, jours, date_min, date_max, _minutes_arrivee in index_gare_heure.get(gare, []):
        if jour_semaine not in jours:
            continue
        if destination is not None and dest != destination:
            continue
        entrees_par_train.setdefault(train, []).append((minutes, date_min, date_max))
        delta = _distance_circulaire(cible, minutes)
        if delta > tolerance_min:
            continue
        existant = candidats_par_train.get(train)
        if existant is None or delta < existant["delta_min"]:
            candidats_par_train[train] = {
                "train": train,
                "train_affiche": format_numero_train(train),
                "heure_theorique": f"{minutes // 60:02d}h{minutes % 60:02d}",
                "destination": dest,
                "delta_min": delta,
                "_minutes": minutes,
                "_date_min": date_min,
                "_date_max": date_max,
            }
    resultats = sorted(candidats_par_train.values(), key=lambda c: c["delta_min"])[:max_candidats]
    for candidat in resultats:
        candidat["horaire_variable_texte"] = _horaire_variable_texte(
            candidat.pop("_minutes"), candidat.pop("_date_min"), candidat.pop("_date_max"),
            entrees_par_train[candidat["train"]],
        )
    return resultats


SEUIL_VARIANTE_PROCHE_MIN = 5


def _horaire_variable_texte(minutes, date_min, date_max, entrees_train):
    """Phrase 'HHhMM → du JJ/MM au JJ/MM. Stats hors plage mais conservées
    ci-dessous.' à afficher sur la carte d'un train résolu, UNIQUEMENT quand
    une autre variante du même train, à la même gare, circule à moins de
    SEUIL_VARIANTE_PROCHE_MIN minutes de celle-ci (ex: train 852221 à Lison,
    16h56 ET 16h59 selon la période) — évite la confusion '2 trains
    identiques à 3 min d'intervalle' repérée en usage réel (2026-08-26). Les
    2 horaires sont réels, chacun programmé sur sa propre période (voir
    choisir_variante) ; le terme 'théorique' est volontairement évité
    (retour utilisateur explicite : sous-entend à tort qu'il existerait un
    horaire 'pratique' différent en plus). Le mot 'valable' aussi évité
    (retour utilisateur, 2026-08-27) : laissait entendre à tort que les
    stats affichées sous la carte étaient bornées à cette même période,
    alors qu'elles portent sur tout l'historique du train (voir
    calculer_carte_stats_train_sql, filtrée par numéro de train seul, pas
    par variante/plage de dates) — d'où la 2e phrase, qui le précise
    explicitement plutôt que de laisser 'valable' le sous-entendre à tort.
    date_min/date_max : période de la VARIANTE SÉLECTIONNÉE uniquement (pas
    une heuristique de proximité — juste les vraies bornes de dates de ce
    service_id, voir _jours_semaine_actifs)."""
    a_un_voisin = any(
        m != minutes and abs(m - minutes) <= SEUIL_VARIANTE_PROCHE_MIN
        for m, _, _ in entrees_train
    )
    if not a_un_voisin or date_min is None or date_max is None:
        return None
    return (
        f"{minutes // 60:02d}h{minutes % 60:02d} → du {date_min} au {date_max}. "
        "Stats hors plage mais conservées ci-dessous — (voir aide)."
    )


def informations_horaire_train(train, gare_depart, heure_hhmm, destination, index_gare_heure):
    """(heure_arrivee 'HH:MM', duree 'HhMM') pour la variante de `train`
    partant de `gare_depart` à `heure_hhmm` — recherchée dans index_gare_
    heure (même donnée que la résolution initiale, voir resoudre_trains_
    pour_gare_heure) plutôt que transportée depuis la résolution via l'URL
    (hx-vals) : recalculée à chaque affichage de la carte, donc fonctionne
    aussi bien pour un train tout juste résolu que pour un favori enregistré
    (qui n'a jamais connu que train/gare/heure/destination, voir ajouterFavori
    mobile.js) — pas besoin de jour_semaine ici, train+gare_depart+heure_hhmm
    identifie déjà la variante de façon quasi-univoque en pratique. (None,
    None) si aucune correspondance (favori désormais orphelin d'un
    changement d'horaire GTFS, ou horaire d'arrivée absent du référentiel).

    Durée = (minutes_arrivee - minutes_depart) % 1440 : suppose un trajet de
    moins de 24h (toujours vrai sur cette ligne, aucun train de nuit) — le
    modulo gère juste le cas d'un départ juste avant minuit/arrivée juste
    après, sans avoir besoin d'une date réelle (résolution par jour de la
    semaine récurrent, pas par date précise, voir _jours_semaine_actifs)."""
    if not heure_hhmm:
        return None, None
    cible = _minutes_hhmm(heure_hhmm)
    for t, minutes, dest, _jours, _dmin, _dmax, minutes_arrivee in index_gare_heure.get(gare_depart, []):
        if t != train or minutes != cible or minutes_arrivee is None:
            continue
        if destination and dest != destination:
            continue
        duree_min = (minutes_arrivee - minutes) % 1440
        heure_arrivee = f"{minutes_arrivee // 60:02d}:{minutes_arrivee % 60:02d}"
        duree = f"{duree_min // 60}h{duree_min % 60:02d}"
        return heure_arrivee, duree
    return None, None
