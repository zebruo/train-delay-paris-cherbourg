"""
Génère un rapport PDF (quotidien, hebdomadaire ou mensuel) résumant l'état de
la ligne Paris-Cherbourg sur la période écoulée, à partir de
observations.db et alertes.csv — mêmes calculs que la barre du haut / la
frise / "Suivi d'un train" de viewer.py, mais sans dépendance à Tkinter
(utilisable sans interface graphique).

Usage : python generer_rapport.py quotidien|hebdomadaire|mensuel

Tourne sur le Pi 4 (aarch64, matplotlib a un paquet précompilé sur cette
architecture — contrairement à l'ancien Pi ARM32 évoqué en mémoire du
projet, 2026-07-24, où ce n'était pas le cas), via executer_rapport_pi.sh,
planifié par cron sur le Pi. observations.db/alertes.csv sont rapatriés
depuis la VPS par ce même script juste avant l'appel à ce fichier (la VPS
est la seule source de collecte depuis le 2026-08-14, le Pi ne collecte
plus lui-même — voir mémoire du projet) — pas par ce module, qui reste
volontairement seulement responsable de la génération, pas du
rapatriement. executer_rapport.sh (Planificateur de tâches Windows) reste
disponible pour un lancement ponctuel depuis le PC, sur sa propre copie
locale (rapatriée par viewer.py).

Écrit dans rapports/<periode>/NNNN_rapport_<periode>_JJ-MM-AAAA.pdf (un
fichier par génération, numéroté, jamais écrasé — historique conservé comme
pour backups/). Envoyé ensuite vers le NAS par envoyer_rapport_nas_pi.sh
(rsync/SSH) — pas envoyer_rapport_nas.sh (powershell.exe/UNC), qui ne
fonctionne que depuis le PC.
"""
import json
import os
import sqlite3
import sys

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.gridspec import GridSpec
from matplotlib.offsetbox import AnnotationBbox, HPacker, TextArea
from matplotlib.ticker import AutoMinorLocator

from formatting import (
    PARIS_TZ, build_stop_names, build_trip_data, choisir_variante, cle_circulation,
    derniers_par_passage, derniers_par_passage_avec_date, estimer_passage_reel, format_gare,
    format_heure_avec_arret, format_numero_train, load_calendrier, load_reference,
)

OBSERVATIONS_DB = "observations.db"
ALERTES_FILE = "alertes.csv"
RAPPORTS_DIR = "rapports"
COMPTEUR_FILE = f"{RAPPORTS_DIR}/.compteur.json"

# Ordonnées Paris -> Cherbourg — pour le graphique "Retard moyen par gare" du
# rapport mensuel. Même ordre que GARES_LIGNE_ORDRE dans viewer.py.
GARES_LIGNE_ORDRE = (
    "Paris Saint-Lazare", "Mantes-la-Jolie", "Évreux Normandie", "Bernay",
    "Lisieux", "Caen", "Bayeux", "Lison", "Carentan", "Valognes", "Cherbourg",
)
# Même 11 gares que GARES_LIGNE_ORDRE, en set pour les tests d'appartenance
# (un set n'a pas d'ordre, d'où GARES_LIGNE_ORDRE séparé pour l'axe du
# graphique).
GARES_LIGNE = set(GARES_LIGNE_ORDRE)
JOURS_SEMAINE_ORDRE = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")
PERIODES = {
    "quotidien": "Rapport quotidien",
    "hebdomadaire": "Rapport hebdomadaire",
    "mensuel": "Rapport mensuel",
}


def trajet_sens(trip_id, start_date, variantes, calendrier):
    """Origine -> destination théoriques d'un trip_id, en toutes lettres —
    contrairement à viewer.App._trajet_sens() (codes 5 lettres, pensés pour
    la place réduite d'un menu déroulant), un PDF a la place pour les noms
    complets, plus lisibles pour une lecture ponctuelle. choisir_variante
    (pas juste sans_date_trip_id) : un même train peut avoir plusieurs
    variantes d'horaire selon la période, voir formatting.py, correctif du
    2026-08-12."""
    variante = choisir_variante(variantes, calendrier, trip_id, start_date)
    if not variante or len(variante["gares"]) < 2:
        return ""
    return f"{variante['gares'][0]} → {variante['gares'][-1]}"


def finaliser_axes(ax, marge_x_min=0.1, y_max_min=None):
    """Port de App._finalize_axes(marge_bas=True) de viewer.py — même mise en
    forme (zéro marge par défaut de matplotlib, remplacée par une marge Y
    resserrée à 2 % du range, graduations Y bornées à >= 0, petits traits
    intermédiaires, marge X calculée sur dataLim, bordures haut/droite
    retirées). Sans ce portage, les mini-graphiques du rapport gardaient le
    padding par défaut de matplotlib (5 %, pas de plancher à 0, pas de
    graduations mineures) — une échelle visuellement différente du graphique
    équivalent dans "Suivi d'un train", repéré par l'utilisateur en comparant
    les deux côte à côte (2026-07-27).
    y_max_min : plancher optionnel pour la borne haute — sans lui, un jour où
    la ligne serait très ponctuelle (les 5 pires circulations culminant
    autour de 3-5 min), l'axe zoomerait sur cette plage minuscule et ferait
    paraître ces petites variations disproportionnées."""
    ax.margins(x=0, y=0)
    y_min, y_max = ax.get_ylim()
    if y_max - y_min < 1:
        centre = (y_min + y_max) / 2
        ax.set_ylim(centre - 0.1, centre + 1)
    else:
        marge = max((y_max - y_min) * 0.02, 0.75)
        marge_haut = max((y_max - y_min) * 0.02, 1)
        ax.set_ylim(y_min - marge, y_max + marge_haut)
    if y_max_min is not None and ax.get_ylim()[1] < y_max_min:
        ax.set_ylim(ax.get_ylim()[0], y_max_min)
    y_max_actuel = ax.get_ylim()[1]
    ax.set_yticks([t for t in ax.get_yticks() if 0 <= t <= y_max_actuel])
    ax.yaxis.set_minor_locator(AutoMinorLocator(2))
    ax.set_yticks([t for t in ax.yaxis.get_minorticklocs() if 0 <= t <= y_max_actuel], minor=True)
    ax.tick_params(axis="y", which="minor", length=3)
    x_min, x_max = ax.dataLim.intervalx
    marge_x = max((x_max - x_min) * 0.02, marge_x_min)
    ax.set_xlim(x_min - marge_x, x_max + marge_x)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def marquer_ligne_moyenne(ax, valeur, couleur, unite):
    """Port statique de App._marquer_moyenne de viewer.py (onglet Par
    jour/heure) : ligne pointillée horizontale à une valeur de référence,
    avec étiquette "moy. X min" — sans l'info-bulle au survol, non pertinente
    dans un PDF. Prend directement une valeur plutôt qu'une série pour les
    graphiques où la moyenne affichée doit rester cohérente avec un chiffre
    déjà calculé ailleurs (ex. ax_a et le "24 %" du résumé en tête de page)."""
    if valeur is None or pd.isna(valeur):
        return
    ax.axhline(valeur, color=couleur, linestyle="--", linewidth=1, alpha=0.6, zorder=1)
    ax.annotate(
        f"moy. {valeur:.1f}{unite}",
        xy=(1, valeur), xycoords=("axes fraction", "data"),
        xytext=(-4, 4), textcoords="offset points",
        fontsize=7, color=couleur, ha="right",
        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8),
    )


def marquer_moyenne(ax, serie, couleur, unite):
    """Comme marquer_ligne_moyenne, mais calcule la moyenne à partir d'une
    série (barres par gare/jour de semaine, où aucun chiffre équivalent
    n'existe déjà ailleurs sur la page)."""
    serie_valide = serie.dropna()
    if serie_valide.empty:
        return
    marquer_ligne_moyenne(ax, serie_valide.mean(), couleur, unite)


def numero_suivant(nom_periode):
    """Numéro chrono du prochain rapport, un compteur séparé par type
    (quotidien/hebdomadaire) puisqu'ils ne sont pas générés au même rythme —
    un compteur partagé donnerait des sauts de numéro déroutants entre les
    deux. Persisté dans un petit fichier JSON à côté des PDF plutôt que
    déduit du nombre de fichiers déjà présents dans rapports/ : plus simple
    et robuste si d'anciens rapports (sans numéro dans leur nom) sont encore
    là, ou si des fichiers sont déplacés/supprimés localement."""
    try:
        with open(COMPTEUR_FILE) as f:
            compteurs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        compteurs = {}
    numero = compteurs.get(nom_periode, 0) + 1
    compteurs[nom_periode] = numero
    os.makedirs(RAPPORTS_DIR, exist_ok=True)
    with open(COMPTEUR_FILE, "w") as f:
        json.dump(compteurs, f)
    return numero


def calculer_periode(nom_periode, maintenant_utc):
    """Bornes (heure locale Paris) de la période couverte par le rapport,
    calées sur des horaires fixes plutôt que sur une fenêtre glissante de
    N heures avant l'instant d'exécution du script : la tâche planifiée
    Windows se déclenche vers 07h00/07h05, une heure pas garantie exacte
    (PC éteint/en veille, tâche en retard...), donc une fenêtre glissante
    ferait dériver la période d'un jour sur l'autre — peu comparable et pas
    intuitif ("hier"/"cette semaine" doivent correspondre au calendrier).
    2h du matin est choisi comme frontière car c'est le creux du trafic
    nocturne (peu ou pas de trains, voir mémoire du projet) : une
    circulation a très peu de chances d'être en plein trajet pile à cet
    instant, ce qui limite le risque d'en couper une en deux périodes
    consécutives — complète le filtre "circulations arrivées uniquement"
    plutôt que de le contredire.
    Quotidien : le dernier cycle 2h→2h déjà terminé (ex: exécuté le 28 à
    07h00, période = 27 à 2h → 28 à 2h). Hebdomadaire : du lundi 2h au
    lundi suivant 2h, le dernier cycle déjà terminé."""
    maintenant_local = maintenant_utc.tz_convert(PARIS_TZ)
    if nom_periode == "hebdomadaire":
        fin_local = (maintenant_local - pd.Timedelta(days=maintenant_local.weekday())).normalize() \
            + pd.Timedelta(hours=2)
        if fin_local > maintenant_local:
            fin_local -= pd.Timedelta(days=7)
        debut_local = fin_local - pd.Timedelta(days=7)
    elif nom_periode == "mensuel":
        # Même principe que "hebdomadaire", à l'échelle du mois calendaire
        # (1er du mois 2h → 1er du mois suivant 2h) — pd.DateOffset(months=1)
        # gère les mois de longueur différente correctement (contrairement à
        # un simple pd.Timedelta(days=30)).
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


def circulation_est_arrivee(circ, trip_id, start_date, variantes, calendrier):
    """Une circulation est considérée arrivée si son terminus théorique est
    figé (dépassé) au dernier relevé connu — même logique, et même horloge
    de référence, que le figé/pas-encore-atteint des mini-graphiques
    (comparaison à circ["poll_time"].max(), PAS à l'heure actuelle : un
    relevé plus ancien que l'heure de passage estimée du terminus reste une
    simple prédiction non confirmée, même si "maintenant" le train a
    théoriquement fini son trajet — piège rencontré en vérifiant le rendu,
    voir mémoire du projet 2026-07-27). Le rapport ne doit porter que sur
    des trajets terminés : un "retard max" ou une moyenne calculés sur un
    trajet encore en cours mélangeraient du retard confirmé avec une simple
    prédiction, pas encore définitive, pour les gares pas encore atteintes.
    Si le terminus n'a même pas été observé (train sorti de la fenêtre de
    60 min du flux temps réel, ou trou de collecte), la circulation est
    exclue par prudence plutôt que supposée arrivée sans preuve."""
    variante = choisir_variante(variantes, calendrier, trip_id, start_date)
    if not variante:
        return False
    route = variante["gares"]
    terminus = route[-1]
    lignes_terminus = circ[circ["gare"] == terminus]
    if lignes_terminus.empty:
        return False
    derniere = lignes_terminus.sort_values("poll_time").iloc[-1]
    dernier_poll = circ["poll_time"].max()
    heure_terminus = dict(zip(route, variante["horaires"])).get(terminus)
    passage_estime = estimer_passage_reel(heure_terminus, derniere["start_date"], derniere["retard_min"])
    return passage_estime is not None and passage_estime <= dernier_poll


def filtrer_periode_arrivees(df, variantes, calendrier, debut_utc, fin_utc):
    """(df_periode_complet, df_periode) sur [debut_utc, fin_utc) : toutes
    gares confondues, puis restreint aux 11 gares de la ligne — circulations
    non-arrivées exclues des deux (voir circulation_est_arrivee). Factorisé
    entre stats_perturbees_periode() et le corps de generer(), qui avaient
    ce même filtre dupliqué presque à l'identique."""
    sous_df = df[
        (df["poll_time"] >= debut_utc) & (df["poll_time"] < fin_utc)
    ].dropna(subset=["retard_min"]).copy()
    circulations_arrivees = {
        (trip_id, start_date)
        for (trip_id, start_date), circ in sous_df.groupby(["trip_id", "start_date"])
        if circulation_est_arrivee(circ, trip_id, start_date, variantes, calendrier)
    }
    idx = pd.MultiIndex.from_frame(sous_df[["trip_id", "start_date"]])
    df_periode_complet = sous_df[idx.isin(circulations_arrivees)]
    df_periode = df_periode_complet[df_periode_complet["gare"].isin(GARES_LIGNE)]
    return df_periode_complet, df_periode


def stats_perturbees_periode(df, variantes, calendrier, debut_utc, fin_utc):
    """(en_retard, total) circulations perturbées sur [debut_utc, fin_utc) —
    factorisé pour être réutilisable sur une période différente de celle du
    rapport (ex: le mois précédent, pour la comparaison du rapport
    mensuel)."""
    _, df_periode = filtrer_periode_arrivees(df, variantes, calendrier, debut_utc, fin_utc)
    circulation = cle_circulation(df_periode)
    total = circulation.nunique()
    en_retard = circulation[df_periode["retard_min"] > 0].nunique() if total else 0
    return en_retard, total


def charger_donnees():
    """Même construction que viewer.App.load_local_data(), réduite à ce dont
    le rapport a besoin (pas d'heure théorique). Contrairement à avant,
    retourne le df complet, *non* filtré aux 11 gares de la ligne : les
    stats globales (retard moyen, gare la + touchée...) restent calculées
    sur ce périmètre restreint dans generer(), mais les mini-graphiques
    "retard gare par gare" ont besoin du trajet réel complet d'une
    circulation, jonctions hors ligne comprises (ex: Coutances, Granville,
    Rennes) — même logique que "Suivi d'un train" dans viewer.py, qui
    ignore volontairement ce filtre pour la même raison."""
    ref = load_reference()
    stop_names = build_stop_names(ref)
    variantes = build_trip_data(ref)
    calendrier = load_calendrier()
    connexion = sqlite3.connect(OBSERVATIONS_DB)
    try:
        df = pd.read_sql_query("SELECT * FROM observations ORDER BY poll_time", connexion)
    finally:
        connexion.close()
    df["gare"] = df["stop_id"].map(stop_names).fillna(df["stop_id"])
    df["train"] = df["trip_id"].str.split(":").str[0]
    df["retard_arrivee_min"] = (df["arrival_delay_s"] / 60).round(1)
    df["retard_depart_min"] = (df["departure_delay_s"] / 60).round(1)
    df["retard_min"] = df["retard_arrivee_min"].fillna(df["retard_depart_min"])
    df["poll_time"] = pd.to_datetime(df["poll_time"])

    try:
        alertes = pd.read_csv(ALERTES_FILE)
        alertes["debut"] = pd.to_datetime(alertes["debut"], utc=True, errors="coerce")
        alertes["fin"] = pd.to_datetime(alertes["fin"], utc=True, errors="coerce")
    except (FileNotFoundError, pd.errors.EmptyDataError):
        alertes = pd.DataFrame(columns=["gares", "texte", "debut", "fin"])
    return df, alertes, variantes, calendrier


def generer(nom_periode, maintenant=None):
    """maintenant : normalement laissé à None (l'heure réelle d'exécution)
    — surchargeable pour rééditer un rapport passé (ex: reconstituer
    l'historique après une remise à zéro du compteur), en simulant la date à
    laquelle le rapport aurait dû être généré plutôt que de dépendre de
    l'heure système actuelle."""
    titre = PERIODES[nom_periode]
    df, alertes, variantes, calendrier = charger_donnees()

    if maintenant is None:
        maintenant = pd.Timestamp.now(tz="UTC")
    debut_local, fin_local = calculer_periode(nom_periode, maintenant)
    debut_utc, fin_utc = debut_local.tz_convert("UTC"), fin_local.tz_convert("UTC")

    # Le rapport ne porte que sur des circulations arrivées à destination
    # (voir mémoire du projet, 2026-07-27) : sinon un "retard max" ou une
    # moyenne calculés sur un trajet encore en cours mélangeraient du retard
    # confirmé (gares déjà passées, figées) avec une simple prédiction pour
    # les gares pas encore atteintes, pas encore définitive — trompeur si
    # affiché comme un fait acquis (ex: "retard max 55 min" alors que le
    # dernier relevé connu ne dépasse pas 25 min). Filtre appliqué ici, en
    # amont de tout calcul (stats globales, sélection du top 5, mini-
    # graphiques), pour que tout le rapport reste cohérent avec lui-même.
    # df_periode_complet : toutes gares confondues — les mini-graphiques
    # "retard gare par gare" du top 5 en ont besoin (une fois une circulation
    # sélectionnée, son trajet réel doit s'afficher en entier, jonctions hors
    # ligne comprises). df_periode : restreint aux 11 gares de la ligne,
    # utilisé pour les stats globales (retard moyen, gare la + touchée,
    # sélection du top 5...) — même périmètre par défaut que l'appli
    # (limiter_ligne_var coché).
    df_periode_complet, df_periode = filtrer_periode_arrivees(df, variantes, calendrier, debut_utc, fin_utc)

    circulation = cle_circulation(df_periode)
    total = circulation.nunique()
    en_retard = circulation[df_periode["retard_min"] > 0].nunique() if total else 0
    # Une seule valeur par passage réel (dernier relevé connu), pas par
    # relevé — même dédoublonnement que "Retard cumulé" dans viewer.py (voir
    # mémoire du projet, 2026-07-28), sinon un même retard vu 20-40 fois par
    # le flux temps réel serait compté autant de fois. Factorisé dans
    # formatting.py (derniers_par_passage) : sert aussi de base à "retard
    # max" ci-dessous, pas seulement au cumulé.
    derniers = derniers_par_passage(df_periode)
    retard_cumule = derniers[derniers > 0].sum()
    # Basé sur la dernière valeur connue par passage (derniers ci-dessus),
    # pas le maximum brut sur tous les relevés : sinon une prédiction
    # ponctuelle depuis corrigée (ex: 50 min révisés ensuite à 20) resterait
    # affichée comme "le" retard max — repéré par l'utilisateur, 2026-08-03.
    maximum = derniers.max()
    moyennes_par_gare = df_periode.groupby("gare")["retard_min"].mean()
    pire_gare = (
        moyennes_par_gare.idxmax()
        if moyennes_par_gare.notna().any() and moyennes_par_gare.max() > 0
        else None
    )

    # Sélection + retard max calculés sur df_periode_complet (le trajet
    # complet, toutes gares), pas sur df_periode (restreint aux 11 gares de
    # la ligne) : sinon un pic de retard survenu sur une gare hors ligne
    # (ex: Coutances, Granville) resterait invisible ici alors qu'il est
    # bien visible sur le mini-graphique de cette même circulation (même
    # bug déjà rencontré et corrigé dans viewer.py, voir mémoire du projet).
    # Retard max basé sur la dernière valeur connue par passage (pas le
    # maximum brut sur tous les relevés, même correctif que plus haut) :
    # sinon les 5 circulations "les plus perturbées" mises en avant dans le
    # rapport pourraient être choisies sur une prédiction ponctuelle depuis
    # corrigée, pas sur un vrai retard confirmé — repéré par l'utilisateur,
    # 2026-08-03.
    infos = df_periode_complet.groupby(["trip_id", "start_date"]).agg(train=("train", "first")).reset_index()
    retard_max_par_circulation = derniers_par_passage(df_periode_complet).groupby(
        level=["trip_id", "start_date"]
    ).max().rename("retard_max")
    infos = infos.merge(retard_max_par_circulation, on=["trip_id", "start_date"], how="left")
    top5 = infos.sort_values(["retard_max", "start_date"], ascending=[False, False]).head(5).copy()
    top5["sens"] = top5.apply(lambda r: trajet_sens(r["trip_id"], r["start_date"], variantes, calendrier), axis=1)

    # Bornée aux deux extrémités (pas juste "fin >= début") maintenant que la
    # période a une vraie fin fixe (2h), potentiellement plusieurs heures
    # avant l'exécution du script : sans la borne du haut, une alerte
    # démarrée après la fin de la période (donc hors período) serait quand
    # même comptée.
    alertes_periode = alertes[
        (alertes["debut"].isna() | (alertes["debut"] <= fin_utc))
        & (alertes["fin"].isna() | (alertes["fin"] >= debut_utc))
    ]

    # Météo : une valeur par (poll_time, gare) dans les données sources (une
    # requête par gare distincte, voir collect_realtime.py), mais répétée sur
    # chaque ligne des trains présents à ce moment-là — dédoublonnée ici avant
    # de moyenner/sommer, sinon un poll avec 5 trains à la même gare
    # compterait sa météo 5 fois.
    meteo = df_periode.drop_duplicates(["poll_time", "gare"])
    # Open-Meteo (interrogée par collect_realtime.py, mais qui ne se met à
    # jour côté serveur qu'environ une fois par heure) n'a pas la même
    # granularité que la collecte (un sondage toutes les 5 min) : un même
    # relevé météo horaire se retrouve dupliqué sur plusieurs sondages
    # consécutifs — un seul relevé gardé par (gare, heure civile), le
    # dernier connu de cette heure, avant de moyenner/sommer. Impact mesuré
    # sur un vrai rapport : mineur pour température/vent (quelques
    # dixièmes, peut faire basculer le chiffre arrondi affiché d'une
    # unité), mais ×11 de surestimation pour la pluie (precipitation_mm est
    # un cumul glissant sur la dernière heure côté Open-Meteo — sommer les
    # doublons revient à compter la même pluie plusieurs fois, 20.7 mm
    # affiché pour 1.9 mm réels avant ce correctif) — voir mémoire du
    # projet, 2026-07-31.
    meteo_par_gare_heure = meteo.assign(
        heure=meteo["poll_time"].dt.floor("h")
    ).sort_values("poll_time").groupby(["gare", "heure"]).last()
    temp_moy = meteo_par_gare_heure["temperature_c"].mean()
    vent_moy = meteo_par_gare_heure["wind_speed_kmh"].mean()
    pluie_totale = meteo_par_gare_heure["precipitation_mm"].sum()

    # Section "Vue d'ensemble du mois", propre au rapport mensuel : le top 5
    # (voir plus bas) ne représente qu'une poignée de circulations sur les
    # ~800-1000 du mois, insuffisant pour donner une vue d'ensemble à cette
    # échelle (contrairement à un jour/une semaine) — demande explicite de
    # l'utilisateur, 2026-07-30.
    if nom_periode == "mensuel":
        jours_periode = pd.date_range(debut_local.normalize(), fin_local.normalize(), freq="D", inclusive="left")

        def _pct_perturbees(g):
            circ = cle_circulation(g)
            tot = circ.nunique()
            return 100 * circ[g["retard_min"] > 0].nunique() / tot if tot else float("nan")

        # Mois entièrement vide (aucune donnée du tout, ex: un mois
        # antérieur au début de la collecte) traité à part, sans passer par
        # groupby().apply() : sur un DataFrame vide, pandas ne peut pas
        # exécuter la fonction ne serait-ce qu'une fois pour déduire la
        # forme du résultat, et renvoie quelque chose qui n'a ni la forme ni
        # le dtype attendus (repéré en conditions réelles lors du
        # déploiement sur le Pi, 2026-07-30 — plantait plus loin au moment
        # de tracer le graphique, avec une erreur de type peu parlante).
        if df_periode.empty:
            pct_par_jour = pd.Series(float("nan"), index=jours_periode, dtype=float)
            cumule_par_jour_h = pd.Series(0.0, index=jours_periode, dtype=float)
        else:
            jour = df_periode["poll_time"].dt.tz_convert(PARIS_TZ).dt.normalize()
            pct_par_jour = df_periode.groupby(jour).apply(_pct_perturbees, include_groups=False)
            pct_par_jour = pct_par_jour.astype(float).reindex(jours_periode)

            # Même dédoublonnement "un retard par passage réel" que "Retard
            # cumulé" plus haut (une ligne par (trip_id, start_date, gare),
            # pas par relevé), mais avec poll_time conservé cette fois pour
            # répartir le cumul jour par jour (jour du dernier relevé connu
            # de ce passage, presque toujours le jour réel du passage), et
            # construire une courbe cumulative sur le mois.
            derniers_avec_date = derniers_par_passage_avec_date(df_periode)
            derniers_avec_date = derniers_avec_date[derniers_avec_date["retard_min"] > 0]
            jour_dernier = derniers_avec_date["poll_time"].dt.tz_convert(PARIS_TZ).dt.normalize()
            cumule_par_jour_min = derniers_avec_date.groupby(jour_dernier)["retard_min"].sum()
            cumule_par_jour_h = cumule_par_jour_min.reindex(jours_periode, fill_value=0.0).astype(float).cumsum() / 60

        moyenne_mois = 100 * en_retard / total if total else None
        debut_mois_precedent = debut_local - pd.DateOffset(months=1)
        # Comparaison affichée seulement si le mois précédent est entièrement
        # couvert par la collecte — sinon (ex: tout premier rapport mensuel,
        # données commencées en cours de mois précédent) la comparaison
        # serait faussée par un mois partiel, pas vraiment comparable.
        premiere_donnee = df["poll_time"].min()
        if moyenne_mois is not None and pd.notna(premiere_donnee) and premiere_donnee <= debut_mois_precedent:
            en_retard_prec, total_prec = stats_perturbees_periode(
                df, variantes, calendrier, debut_mois_precedent.tz_convert("UTC"), debut_utc,
            )
            moyenne_mois_precedent = 100 * en_retard_prec / total_prec if total_prec else None
        else:
            moyenne_mois_precedent = None

    # ==================== Construction du PDF (A4 portrait) ====================
    # constrained_layout plutôt qu'un hspace fixe : hspace est un pourcentage
    # uniforme entre TOUTES les rangées, il pénalisait donc aussi le titre/
    # les stats (qui n'en ont pas besoin) sans pour autant suffire à écarter
    # les étiquettes de gares pivotées de chaque mini-graphique du titre du
    # suivant. constrained_layout calcule l'espace nécessaire à partir de
    # l'encombrement réel de chaque axe (étiquettes comprises).
    fig = plt.figure(figsize=(8.27, 11.69), constrained_layout=True)
    # Espace supplémentaire entre les graphiques (et les autres blocs) —
    # constrained_layout calcule déjà l'espace nécessaire à partir de
    # l'encombrement réel de chaque axe, ce pad s'ajoute par-dessus.
    fig.set_constrained_layout_pads(hspace=0.04)
    # Le top 5 des circulations les plus perturbées ne représente presque
    # rien à l'échelle d'un mois (5 sur ~1500-1800, sous 0,5 %, contre ~4 %
    # pour un rapport quotidien) et fait doublon avec "Vue d'ensemble du
    # mois" (qui répond déjà au besoin d'une vue mensuelle) — retiré pour ce
    # seul type de rapport, gardé inchangé pour quotidien/hebdomadaire où il
    # reste représentatif — demande explicite de l'utilisateur, 2026-08-03.
    afficher_top5 = nom_periode != "mensuel"
    n_graphiques = (max(len(top5), 1) if afficher_top5 else 0)
    lignes_entete_top5 = 1 if afficher_top5 else 0
    # Le résumé "Travaux / alertes" tient sur une seule ligne sous la météo
    # (voir plus bas) — les 2 dernières rangées (espaceur + liste détaillée)
    # ne sont donc réservées que s'il y a vraiment des alertes à détailler.
    has_alertes = not alertes_periode.empty
    # 5 rangées de plus pour "Vue d'ensemble du mois" (comparaison texte + 4
    # graphiques : % perturbées/jour, retard cumulé croissant, retard moyen
    # par gare, retard moyen par jour de semaine), uniquement pour le
    # rapport mensuel — voir plus haut.
    lignes_mensuel = 5 if nom_periode == "mensuel" else 0
    gs = GridSpec(
        3 + lignes_mensuel + lignes_entete_top5 + n_graphiques + (2 if has_alertes else 0), 2, figure=fig,
        height_ratios=(
            [0.6, 0.75] + ([0.4, 1.3, 1.3, 1.1, 1.1] if nom_periode == "mensuel" else [])
            + ([0.35] if afficher_top5 else [])
            + [1.3] * n_graphiques + ([0.3, 1.1] if has_alertes else []) + [0.4]
        ),
        wspace=0.25,
    )

    ax_titre = fig.add_subplot(gs[0, :])
    ax_titre.axis("off")
    # "généré le" utilise l'heure réelle d'exécution (maintenant), distincte
    # de fin_local (2h, la borne de la période) depuis que la période n'est
    # plus une fenêtre glissante se terminant "maintenant".
    maintenant_local = maintenant.tz_convert(PARIS_TZ)
    # "Circulations sur l'axe" plutôt que "Suivi retards Paris ↔ Cherbourg" :
    # le périmètre couvre toute circulation empruntant un tronçon de la
    # ligne, pas seulement les trains Paris-Cherbourg de bout en bout (ex:
    # Rennes → Caen), l'ancien intitulé laissait penser le contraire.
    ax_titre.text(0.5, 0.75, f"{titre} — Circulations sur l'axe Paris ↔ Cherbourg",
                  fontsize=15, fontweight="bold", va="top", ha="center")
    ax_titre.text(0.5, 0.15,
                  f"(Période : {debut_local.strftime('%d/%m/%Y %Hh%M')} → {fin_local.strftime('%d/%m/%Y %Hh%M')}"
                  f"   ·   généré le {maintenant_local.strftime('%d/%m/%Y à %Hh%M')})",
                  fontsize=9, color="#555", va="top", ha="center")

    ax_stats = fig.add_subplot(gs[1, :])
    ax_stats.axis("off")
    # Même contenu que la barre de stats de "Graphique" dans viewer.py
    # (self.stats_periode_var, une seule ligne), pour rester cohérent entre
    # l'appli et le rapport — mais réparti sur plusieurs lignes ici : sur une
    # page A4 étroite, une seule ligne avec un nom de gare long + des
    # valeurs à 3 chiffres dépasse la largeur imprimable et se fait
    # tronquer. Seul le chiffre principal ("N/M trains en retard") reste en
    # gras ; le détail (moyenne/max/gare) passe en petits caractères, même
    # style que la météo juste en dessous.
    if total:
        fraction = f"{en_retard}/{total}"
        # "circulations perturbées" — même libellé que viewer.py (pas "trains
        # en retard") : évite toute confusion avec la ponctualité officielle
        # SNCF/ART (calculée au terminus uniquement) — ce compteur inclut
        # toute circulation ayant eu du retard à un moment de son trajet,
        # même rattrapé avant l'arrivée.
        reste_ligne1 = f" circulations perturbées ({100 * en_retard / total:.0f} %)"
        heures_cumulees, minutes_cumulees = divmod(round(retard_cumule), 60)
        # Volontairement peu chiffré (pas de "N passages impactés", pas de
        # "retard moyen / relevé" — trop sujet à mauvaise lecture, voir
        # mémoire du projet) : juste de quoi situer l'ampleur (cumulé, pire
        # cas, où) sans noyer le lecteur sous les chiffres — demande
        # explicite de l'utilisateur, 2026-07-30.
        ligne2 = (
            f"  · Retard cumulé {heures_cumulees} h {minutes_cumulees:02d} min · "
            f"retard max {maximum:.0f} min · "
            f"Gare la + touchée : {pire_gare if pire_gare else 'aucun retard significatif'}"
        )
    else:
        fraction = ""
        reste_ligne1 = "Aucune circulation arrivée sur cette période."
        ligne2 = ""
    # Sur une seule ligne mais plusieurs styles (fraction en couleur, reste
    # du compteur en gras noir, détail en petits caractères comme la météo) :
    # un seul ax.text() ne sait pas mélanger plusieurs tailles/couleurs dans
    # une même chaîne — HPacker assemble les TextArea côte à côte et calcule
    # lui-même le décalage horizontal (largeur réelle de chaque segment
    # rendu), pas besoin de l'estimer à la main.
    boite_stats = HPacker(
        children=[
            TextArea(fraction, textprops=dict(fontsize=9, fontweight="bold", color="#c2410c")),
            TextArea(reste_ligne1, textprops=dict(fontsize=9, fontweight="bold")),
            TextArea(ligne2, textprops=dict(fontsize=8, color="#555")),
        ],
        align="baseline", pad=0, sep=0,
    )
    ax_stats.add_artist(AnnotationBbox(
        boite_stats, (0, 0.97), xycoords="axes fraction", box_alignment=(0, 1), frameon=False,
    ))
    if pd.notna(temp_moy):
        texte_meteo = (
            f"Météo sur la période : {temp_moy:.1f}°C en moyenne · "
            f"{pluie_totale:.1f} mm de pluie cumulée · {vent_moy:.0f} km/h de vent moyen"
        )
    else:
        texte_meteo = "Météo : non disponible sur cette période."
    ax_stats.text(0, 0.55, texte_meteo, fontsize=8, color="#555", va="top", ha="left")
    texte_alertes = (
        f"Travaux / alertes sur la période : {len(alertes_periode)} alerte(s) active(s) (détail ci-dessous)."
        if has_alertes else "Travaux / alertes sur la période : aucune alerte connue."
    )
    ax_stats.text(0, 0.15, texte_alertes, fontsize=8, color="#555", va="top", ha="left")
    ax_stats.set_xlim(0, 1)
    ax_stats.set_ylim(0, 1)

    if nom_periode == "mensuel":
        ax_d = fig.add_subplot(gs[2, :])
        ax_d.axis("off")
        ax_d.set_xlim(0, 1)
        ax_d.set_ylim(0, 1)
        ax_d.text(0, 0.7, "Vue d'ensemble du mois", fontsize=10, fontweight="bold", va="center")
        if moyenne_mois is not None:
            if moyenne_mois_precedent is not None:
                delta = moyenne_mois - moyenne_mois_precedent
                couleur_delta = "#c0392b" if delta > 0 else "#2f855a"
                texte_comparaison = (
                    f"{moyenne_mois:.0f} % de circulations perturbées ce mois-ci, contre "
                    f"{moyenne_mois_precedent:.0f} % le mois précédent "
                    f"({'+' if delta >= 0 else ''}{delta:.0f} points)"
                )
            else:
                couleur_delta = "#555"
                texte_comparaison = (
                    f"{moyenne_mois:.0f} % de circulations perturbées ce mois-ci "
                    "(comparaison au mois précédent indisponible)"
                )
            ax_d.text(0, 0.15, texte_comparaison, fontsize=8.5, color=couleur_delta, va="center")

        ax_a = fig.add_subplot(gs[3, :])
        ax_b = fig.add_subplot(gs[4, :])
        ax_c = fig.add_subplot(gs[5, :])
        ax_e = fig.add_subplot(gs[6, :])
        if df_periode.empty:
            # Rien à tracer (mois entièrement sans donnée) : un axe avec
            # uniquement des valeurs manquantes n'a pas de bornes valides
            # pour matplotlib (set_xlim/set_ylim refusent NaN) — même
            # traitement "message de repli" que le top 5 vide plus bas,
            # plutôt que de forcer un graphique sans contenu réel.
            for ax in (ax_a, ax_b, ax_c, ax_e):
                ax.axis("off")
            ax_a.text(0, 0.5, "Aucune donnée sur ce mois.", fontsize=9, color="#555", va="center")
        else:
            ax_a.plot(jours_periode, pct_par_jour.values, color="#c2410c", marker="o", markersize=3, linewidth=1.3)
            marquer_ligne_moyenne(ax_a, moyenne_mois, "#c2410c", " %")
            ax_a.set_title("% de circulations perturbées, jour par jour", fontsize=9, fontweight="bold", loc="left")
            ax_a.set_ylabel("% perturbées", fontsize=8)
            ax_a.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
            ax_a.tick_params(labelsize=7)
            ax_a.set_ylim(bottom=0)
            finaliser_axes(ax_a)

            ax_b.plot(jours_periode, cumule_par_jour_h.values, color="#2c6ea5", linewidth=1.6)
            ax_b.fill_between(jours_periode, cumule_par_jour_h.values, color="#2c6ea5", alpha=0.08)
            # Pas de ligne de moyenne ici, contrairement aux 3 autres
            # graphiques : sur une courbe cumulée croissante, la moyenne ne
            # dépend que de la forme de la montée (linéaire vs. tardive) et
            # n'est pas un repère comparatif utile — jugé peu pertinent par
            # l'utilisateur, 2026-08-03.
            ax_b.set_title("Retard cumulé sur le mois (croissant)", fontsize=9, fontweight="bold", loc="left")
            ax_b.set_ylabel("Heures cumulées", fontsize=8)
            ax_b.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
            ax_b.tick_params(labelsize=7)
            ax_b.set_ylim(bottom=0)
            finaliser_axes(ax_b)

            # Deux nouveaux graphiques (à la place du top 5, retiré du
            # mensuel — voir plus haut) : une vue géographique (quelle gare
            # concentre le retard sur le mois, prolonge "Gare la + touchée"
            # qui ne donne qu'un seul chiffre) et une vue par jour de
            # semaine (motif plus lisible sur un mois entier que sur une
            # seule semaine) — demande explicite de l'utilisateur, 2026-08-03.
            moyennes_par_gare_mois = df_periode.groupby("gare")["retard_min"].mean().reindex(GARES_LIGNE_ORDRE)
            ax_c.bar(range(len(GARES_LIGNE_ORDRE)), moyennes_par_gare_mois.values, color="#8a5cb5")
            ax_c.set_xticks(range(len(GARES_LIGNE_ORDRE)))
            ax_c.set_xticklabels([format_gare(g) for g in GARES_LIGNE_ORDRE], fontsize=6.5, rotation=30, ha="right")
            ax_c.set_title("Retard moyen par gare, sur le mois", fontsize=9, fontweight="bold", loc="left")
            ax_c.set_ylabel("min", fontsize=8)
            ax_c.tick_params(axis="y", labelsize=7)
            ax_c.axhline(0, color="gray", linewidth=0.6)
            marquer_moyenne(ax_c, moyennes_par_gare_mois, "#8a5cb5", " min")
            finaliser_axes(ax_c)

            jour_semaine = pd.to_datetime(df_periode["start_date"], format="%Y%m%d").dt.dayofweek.map(
                dict(enumerate(JOURS_SEMAINE_ORDRE))
            )
            moyennes_par_jour_mois = df_periode.groupby(jour_semaine)["retard_min"].mean().reindex(JOURS_SEMAINE_ORDRE)
            ax_e.bar(range(len(JOURS_SEMAINE_ORDRE)), moyennes_par_jour_mois.values, color="#5ba58c")
            ax_e.set_xticks(range(len(JOURS_SEMAINE_ORDRE)))
            ax_e.set_xticklabels([j[:3] for j in JOURS_SEMAINE_ORDRE], fontsize=7)
            ax_e.set_title("Retard moyen par jour de semaine, sur le mois", fontsize=9, fontweight="bold", loc="left")
            ax_e.set_ylabel("min", fontsize=8)
            ax_e.tick_params(axis="y", labelsize=7)
            ax_e.axhline(0, color="gray", linewidth=0.6)
            marquer_moyenne(ax_e, moyennes_par_jour_mois, "#5ba58c", " min")
            finaliser_axes(ax_e)

    if afficher_top5:
        ax_entete_circulations = fig.add_subplot(gs[2 + lignes_mensuel, :])
        ax_entete_circulations.axis("off")
        ax_entete_circulations.text(0.5, 0.5, "Évolution du retard gare par gare des 5 circulations les plus perturbées", fontsize=10, fontweight="bold",
                                     va="center", ha="center")

        if top5.empty:
            ax_vide = fig.add_subplot(gs[3 + lignes_mensuel, :])
            ax_vide.axis("off")
            ax_vide.text(0, 0.5, "Aucun retard significatif sur cette période.", fontsize=9, color="#555", va="center")
        else:
            # Même style que la vue "Détail des relevés" de viewer.py
            # (_render_train_detail), mais réduit à une seule ligne — le dernier
            # relevé de la circulation, dans la couleur "récent" (extrémité
            # orange de TRAJET_COLORMAP) — plutôt que d'empiler tous les relevés
            # comme le fait l'appli (repliée à 2 lignes par défaut) : un rapport
            # imprimé n'a pas de légende interactive pour en démasquer d'autres,
            # une seule ligne reste lisible. Contrairement à l'ancien style
            # "Escalier" (palier tenu jusqu'à la gare suivante), les segments
            # sont ici de vraies diagonales entre gares réellement observées à
            # ce relevé — une gare non observée à ce relevé (poll manqué,
            # gare hors ligne jamais interrogée...) n'apparaît simplement pas,
            # au lieu d'être comblée par une valeur d'un relevé plus ancien.
            couleur_recent = "#f2a53d"
            for i, (_, ligne) in enumerate(top5.iterrows()):
                ax_g = fig.add_subplot(gs[3 + lignes_mensuel + i, :])
                trip_id, start_date = ligne["trip_id"], ligne["start_date"]
                variante = choisir_variante(variantes, calendrier, trip_id, start_date)
                route = variante["gares"] if variante else []
                position_gare = {g: p for p, g in enumerate(route)}
                horaires_bruts = variante["horaires"] if variante else []
                arrets = variante["arrets"] if variante else []
                heure_par_gare = dict(zip(route, horaires_bruts))

                circ = df_periode_complet[
                    (df_periode_complet["trip_id"] == trip_id) & (df_periode_complet["start_date"] == start_date)
                ]
                dernier_poll = circ["poll_time"].max()
                snapshot = circ[circ["poll_time"] == dernier_poll].copy()
                snapshot["position"] = snapshot["gare"].map(position_gare)
                snapshot = snapshot.dropna(subset=["position", "retard_min"]).sort_values("position")
                points = list(zip(snapshot["position"], snapshot["retard_min"], snapshot["gare"]))

                # Figé segment par segment (une gare pas encore atteinte au
                # moment de ce relevé peut suivre une gare déjà passée). Tracer
                # chaque segment dans un appel plot() séparé (comme fait un
                # instant plus tôt) crée un vrai artefact visuel en PDF : deux
                # segments consécutifs de même style sont deux traits
                # indépendants dont les bouts arrondis se chevauchent à la
                # jointure, ce qui épaissit visiblement le trait aux gares
                # intermédiaires (repéré par l'utilisateur, 2026-07-27) — un
                # seul plot() par *série continue* de même style (le cas courant
                # : tout figé, une seule série) trace une vraie polyligne sans
                # ce chevauchement.
                figes = []
                for (_, v1, g1), (_, v2, g2) in zip(points, points[1:]):
                    h1 = estimer_passage_reel(heure_par_gare.get(g1), start_date, v1)
                    h2 = estimer_passage_reel(heure_par_gare.get(g2), start_date, v2)
                    figes.append(h1 is not None and h1 <= dernier_poll and h2 is not None and h2 <= dernier_poll)

                # k/m plutôt que i/j : cette boucle interne ne doit pas
                # réutiliser i, l'index de la boucle externe sur top5 (déjà
                # utilisé juste au-dessus pour placer ax_g dans la grille) —
                # inoffensif tant que rien après ne suppose i encore égal à
                # cet index, mais fragile à la moindre relecture/évolution.
                k = 0
                while k < len(figes):
                    m = k
                    while m + 1 < len(figes) and figes[m + 1] == figes[k]:
                        m += 1
                    serie = points[k:m + 2]  # segments k..m couvrent les points k..m+1
                    style = "-" if figes[k] else (0, (4, 2))
                    ax_g.plot([p for p, v, g in serie], [v for p, v, g in serie],
                              linewidth=1.5, linestyle=style, color=couleur_recent, zorder=2)
                    k = m + 1
                for p, v, _ in points:
                    ax_g.plot(p, v, marker="o", markersize=4, color=couleur_recent, zorder=3)

                # Échelle Y alignée sur ligne['retard_max'] (déjà calculé plus
                # haut, dernière valeur connue par passage — voir la sélection
                # du top5) plutôt que sur le seul segment tracé : sans ce point
                # invisible l'axe se limiterait à son propre maximum visible,
                # potentiellement plus bas que le "retard max" du titre puisque
                # la vue "Détail des relevés" de viewer.py, elle, trace une
                # ligne par relevé (voir mémoire du projet, 2026-07-27).
                # Utilise désormais la même valeur corrigée que le titre (plus
                # le maximum brut sur tous les relevés, qui pouvait inclure une
                # prédiction ponctuelle depuis corrigée) — 2026-08-03.
                if points and pd.notna(ligne["retard_max"]):
                    ax_g.plot([points[0][0]], [ligne["retard_max"]], alpha=0)

                ax_g.axhline(0, color="gray", linewidth=0.6, linestyle=(0, (4, 3)), zorder=1)

                labels = []
                for gare, h, a in zip(route, horaires_bruts, arrets):
                    h_fmt = format_heure_avec_arret(h, start_date, a)
                    labels.append(f"{format_gare(gare)}\n{h_fmt}" if h_fmt else format_gare(gare))
                x = range(len(route))
                ax_g.set_xticks(list(x))
                ax_g.set_xticklabels(labels, fontsize=5.5, rotation=30, ha="right")
                # Gares hors des 11 de la ligne (trains de jonction) grisées, pour
                # les distinguer des vraies gares de la ligne — même traitement
                # que viewer.py.
                for tick, gare in zip(ax_g.get_xticklabels(), route):
                    if gare not in GARES_LIGNE:
                        tick.set_color("#999999")

                date_str = pd.to_datetime(str(start_date), format="%Y%m%d").strftime("%d/%m/%Y")
                ax_g.set_title(
                    f"train {format_numero_train(ligne['train'])} ({ligne['sens']}) — {date_str} — "
                    f"max {ligne['retard_max']:.0f} min",
                    fontsize=8, fontweight="bold", loc="center",
                )
                ax_g.set_ylabel("min", fontsize=7)
                ax_g.tick_params(labelsize=6)
                finaliser_axes(ax_g, y_max_min=15)

    if has_alertes:
        # Le résumé ("N alerte(s) active(s)") est déjà affiché sous la météo
        # (voir plus haut) — cette rangée ne porte plus que le détail.
        ax_alertes = fig.add_subplot(gs[2 + lignes_mensuel + lignes_entete_top5 + n_graphiques + 1, :])
        ax_alertes.axis("off")
        y = 1.0
        for _, a in alertes_periode.iterrows():
            ax_alertes.text(0, y, f"⚠ {a['gares']} — {a['texte']}", fontsize=8.5, va="top")
            y -= 0.18

    # Pied de page : mêmes sources que le bouton "Sources des données" de
    # viewer.py, réduites à celles réellement exploitées dans ce rapport
    # (retards + horaires théoriques + météo + alertes) — jours fériés et
    # vacances scolaires n'apparaissent nulle part dans le PDF, pas listés.
    ligne_pied_page = 2 + lignes_mensuel + lignes_entete_top5 + n_graphiques + (2 if has_alertes else 0)
    ax_pied = fig.add_subplot(gs[ligne_pied_page, :])
    ax_pied.axis("off")
    # Sur 2 lignes : la longueur totale (3 sources + libellés) dépasse la
    # largeur imprimable sur une seule ligne, même à cette petite taille.
    ax_pied.text(
        0.5, 0.75,
        "Sources : proxy.transport.data.gouv.fr (retards et alertes GTFS-RT SNCF) · "
        "api.open-meteo.com (météo)",
        fontsize=6.5, color="#999", va="center", ha="center",
    )
    ax_pied.text(
        0.5, 0.25,
        "eu.ftp.opendatasoft.com/sncf/plandata (horaires théoriques, GTFS statique SNCF)",
        fontsize=6.5, color="#999", va="center", ha="center",
    )

    # Un sous-dossier par type de rapport (quotidien/hebdomadaire), pour ne
    # pas mélanger les deux historiques dans un seul dossier plat.
    dossier_periode = f"{RAPPORTS_DIR}/{nom_periode}"
    os.makedirs(dossier_periode, exist_ok=True)
    numero = numero_suivant(nom_periode)
    chemin = f"{dossier_periode}/{numero:04d}_rapport_{nom_periode}_{fin_local.strftime('%d-%m-%Y')}.pdf"
    fig.savefig(chemin)
    print(f"Rapport généré : {chemin}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in PERIODES:
        print("Usage : python generer_rapport.py quotidien|hebdomadaire|mensuel")
        sys.exit(1)
    generer(sys.argv[1])
