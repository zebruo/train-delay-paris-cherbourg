"""
Génère un rapport PDF (quotidien, hebdomadaire ou mensuel) résumant l'état de
la ligne Paris-Cherbourg sur la période écoulée, à partir de
observations.db et alertes.csv — mêmes calculs que la barre du haut / la
frise / "Suivi d'un train" de viewer.py, mais sans dépendance à Tkinter
(utilisable sans interface graphique).

Usage : python generer_rapport.py quotidien|hebdomadaire|mensuel

Tourne sur un Raspberry Pi (aarch64, matplotlib a un paquet précompilé sur
cette architecture — contrairement à l'ancien Pi ARM32 évoqué en mémoire du
projet, 2026-07-24, où ce n'était pas le cas), via executer_rapport_pi.sh,
planifié par cron. Depuis le 2026-09-01 : quotidien/hebdomadaire tournent
sur Pi 2, mensuel sur Pi 4 — "mensuel" est le plus gourmand en mémoire
(mois courant + mois précédent + 3 graphiques gare/jour/heure) et a été tué
par l'OOM killer sur Pi 2 (~942 Mo de RAM) le tout premier mois où ce cron
a eu l'occasion de tourner ; Pi 4 (3,7 Go) l'encaisse sans souci (voir
mémoire du projet). observations.db/alertes.csv/le référentiel GTFS sont
rapatriés depuis la VPS par ce même script juste avant l'appel à ce fichier
(la VPS est la seule source de collecte depuis le 2026-08-14, le Pi ne
collecte plus lui-même — voir mémoire du projet) — pas par ce module, qui
reste volontairement seulement responsable de la génération, pas du
rapatriement. L'ancienne chaîne PC/Planificateur de tâches Windows
(executer_rapport.sh/envoyer_rapport_nas.sh) a été retirée le 2026-09-01 :
maintenir le PC allumé en permanence irait à l'encontre de l'intérêt même
de la bascule vers le Pi/VPS.

Écrit dans rapports/<periode>/NNNN_rapport_<periode>_JJ-MM-AAAA.pdf (un
fichier par génération, numéroté, jamais écrasé — historique conservé comme
pour backups/). Envoyé ensuite vers le NAS par envoyer_rapport_nas_pi.sh
(rsync/SSH).
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
from matplotlib.transforms import blended_transform_factory

from formatting import (
    PARIS_TZ, build_stop_names, build_trip_data, calculer_periode, choisir_variante,
    cle_circulation, derniers_par_passage, derniers_par_passage_avec_date, estimer_passage_reel,
    format_gare, format_heure_avec_arret, format_min_sans_zero, format_numero_train,
    load_calendrier, load_reference, texte_categorie_maximale, texte_periode_rapport,
    titre_dynamique_jour_heure,
)

# Même seuil que SEUIL_FIABLE (app_fastapi.py) — pas d'import direct, ce
# script ne dépend jamais de app_fastapi.py (sens inverse : le Pi n'exécute
# que generer_rapport.py, pas l'appli web). Exprimé en CIRCULATIONS
# DISTINCTES, pas en relevés bruts, depuis le 2026-08-23 — voir le
# commentaire de SEUIL_FIABLE côté app_fastapi.py pour le raisonnement.
SEUIL_FIABLE_MENSUEL = 10
# Nombre max de numéros de train listés pour "Circulations annulées" (le
# reste devient "et N autres") — filet de sécurité pour un jour avec
# beaucoup plus de 10 annulations : au-delà, la ligne dépasse la largeur de
# la page (ax_stats.text ne retourne jamais à la ligne toute seule, voir
# plus bas) et se fait tronquer en plein milieu d'un numéro (repéré en
# usage réel, rapport n°39, 2026-08-28, 10 annulations le même jour — ce
# cas précis tient déjà sur une ligne sans plafonnement, vérifié visuellement).
SEUIL_TRAINS_ANNULES_AFFICHES = 10
# Même seuil que SEUIL_RETARD_MOYEN (app_fastapi.py/viewer.py) — pas
# d'import direct, même raisonnement que SEUIL_FIABLE_MENSUEL ci-dessus.
# Sépare une perturbation "significative" (> 5 min à un moment du trajet)
# d'une perturbation mineure, pour l'affichage recalibré "22 % au total —
# 9 % de perturbations significatives > 5 min" (demande explicite de
# l'utilisateur, 2026-09-04, même narratif que le second pourcentage déjà
# affiché sur Circulations perturbées/Trajets sans perturbation côté appli).
SEUIL_RETARD_MOYEN = 5

OBSERVATIONS_DB = "observations.db"
ALERTES_FILE = "alertes.csv"
PERTURBATIONS_FILE = "perturbations_detectees.csv"
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
# Même heure_locale que l'onglet web "Par jour / heure" (app_fastapi.py) —
# heure de COLLECTE (poll_time en local Paris), pas l'heure théorique du
# passage. 0-23 : mêmes libellés "0h".."23h" que labels_heure côté web.
HEURES_ORDRE = list(range(24))
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


def filtrer_periode_arrivees(df, evenements, variantes, calendrier, debut_utc, fin_utc):
    """(df_periode_complet, df_periode) sur [debut_utc, fin_utc) : toutes
    gares confondues, puis restreint aux 11 gares de la ligne — circulations
    non-arrivées exclues des deux (voir circulation_est_arrivee). Factorisé
    entre stats_perturbees_periode() et le corps de generer(), qui avaient
    ce même filtre dupliqué presque à l'identique.

    evenements (perturbations_detectees.csv, pas restreint à la période :
    une circulation annulée l'est indépendamment de la fenêtre de stats
    qu'on calcule) sert à exclure les circulations avec un événement
    "trajet_annule", même quand circulation_est_arrivee les considère
    arrivées — le flux GTFS-RT peut publier une prévision d'arrivée au
    terminus (avec un retard) quelques minutes avant que l'annulation
    officielle ne soit propagée (train 851116, 2026-08-20, 45 min prévus au
    terminus puis annulé 5 min plus tard) : sans cette exclusion, Retard
    max/Top5/Retard cumulé comptaient une circulation qui n'est en réalité
    jamais arrivée, en contradiction avec "Circulations annulées" (qui la
    listait déjà correctement, via evenements directement — voir plus bas
    dans generer())."""
    circulations_annulees = set(zip(
        evenements.loc[evenements["type"] == "trajet_annule", "trip_id"],
        evenements.loc[evenements["type"] == "trajet_annule", "start_date"].astype(str),
    ))
    sous_df = df[
        (df["poll_time"] >= debut_utc) & (df["poll_time"] < fin_utc)
    ].dropna(subset=["retard_min"]).copy()
    circulations_arrivees = {
        (trip_id, start_date)
        for (trip_id, start_date), circ in sous_df.groupby(["trip_id", "start_date"])
        if (trip_id, start_date) not in circulations_annulees
        and circulation_est_arrivee(circ, trip_id, start_date, variantes, calendrier)
    }
    idx = pd.MultiIndex.from_frame(sous_df[["trip_id", "start_date"]])
    df_periode_complet = sous_df[idx.isin(circulations_arrivees)]
    df_periode = df_periode_complet[df_periode_complet["gare"].isin(GARES_LIGNE)]
    return df_periode_complet, df_periode


def _compte_perturbees(df_periode):
    """(en_retard, total) à partir d'un df_periode déjà filtré (voir
    filtrer_periode_arrivees) — factorisé entre stats_perturbees_periode()
    et le corps de generer(), qui dupliquaient ce même calcul à l'identique
    (audit de nettoyage, 2026-08-20)."""
    circulation = cle_circulation(df_periode)
    total = circulation.nunique()
    en_retard = circulation[df_periode["retard_min"] > 0].nunique() if total else 0
    return en_retard, total


def _compte_severes(df_periode):
    """Nombre de circulations avec un retard dépassant SEUIL_RETARD_MOYEN
    (perturbation "significative", > 5 min) à un moment quelconque du
    trajet — même principe que _compte_perturbees (n'importe quel relevé
    au-delà du seuil suffit), mais avec ce seuil plutôt que > 0."""
    circulation = cle_circulation(df_periode)
    total = circulation.nunique()
    return circulation[df_periode["retard_min"] > SEUIL_RETARD_MOYEN].nunique() if total else 0


def stats_perturbees_periode(df, evenements, variantes, calendrier, debut_utc, fin_utc):
    """(en_retard, total) circulations perturbées sur [debut_utc, fin_utc) —
    factorisé pour être réutilisable sur une période différente de celle du
    rapport (ex: le mois précédent, pour la comparaison du rapport
    mensuel)."""
    _, df_periode = filtrer_periode_arrivees(df, evenements, variantes, calendrier, debut_utc, fin_utc)
    return _compte_perturbees(df_periode)


def charger_donnees(borne_debut_utc, fin_utc):
    """Même construction que viewer.App.load_local_data(), réduite à ce dont
    le rapport a besoin (pas d'heure théorique). Contrairement à avant,
    retourne le df complet, *non* filtré aux 11 gares de la ligne : les
    stats globales (retard moyen, gare la + touchée...) restent calculées
    sur ce périmètre restreint dans generer(), mais les mini-graphiques
    "retard gare par gare" ont besoin du trajet réel complet d'une
    circulation, jonctions hors ligne comprises (ex: Coutances, Granville,
    Rennes) — même logique que "Suivi d'un train" dans viewer.py, qui
    ignore volontairement ce filtre pour la même raison.

    borne_debut_utc/fin_utc bornent la lecture SQL — [debut_utc, fin_utc)
    de la période du rapport, ou [debut_mois_precedent_utc, fin_utc) pour un
    rapport mensuel (voir l'appelant) : sans cette borne, la lecture
    chargeait TOUT l'historique de la base à chaque génération, quelle que
    soit la période demandée (mesuré 2026-08-20 : 1,46 Go de RAM au pic pour
    708 Mo de base — sur une VPS/un Pi qui n'ont que 3,8 Go chacun — alors
    qu'un rapport quotidien n'a besoin que d'1 jour de données). Renvoie
    aussi premiere_donnee (vraie date de début de collecte, requête séparée
    et bon marché) : indispensable à la comparaison "mois précédent" du
    rapport mensuel (voir generer()), qui doit savoir si la collecte remonte
    vraiment avant borne_debut_utc — un simple df["poll_time"].min() sur les
    données déjà bornées donnerait toujours ~borne_debut_utc, qu'il y ait ou
    non de vraies données plus anciennes."""
    ref = load_reference()
    stop_names = build_stop_names(ref)
    variantes = build_trip_data(ref)
    calendrier = load_calendrier()
    connexion = sqlite3.connect(OBSERVATIONS_DB)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM observations WHERE poll_time >= ? AND poll_time < ? ORDER BY poll_time",
            connexion, params=(borne_debut_utc.isoformat(), fin_utc.isoformat()),
        )
        premiere_donnee = connexion.execute("SELECT MIN(poll_time) FROM observations").fetchone()[0]
    finally:
        connexion.close()
    premiere_donnee = pd.to_datetime(premiere_donnee, utc=True) if premiere_donnee else pd.NaT
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

    try:
        evenements = pd.read_csv(PERTURBATIONS_FILE)
        evenements["poll_time"] = pd.to_datetime(evenements["poll_time"], utc=True, errors="coerce")
    except (FileNotFoundError, pd.errors.EmptyDataError):
        evenements = pd.DataFrame(columns=["poll_time", "type", "trip_id", "start_date", "train", "gare"])
    return df, alertes, evenements, variantes, calendrier, premiere_donnee


def generer(nom_periode, maintenant=None):
    """maintenant : normalement laissé à None (l'heure réelle d'exécution)
    — surchargeable pour rééditer un rapport passé (ex: reconstituer
    l'historique après une remise à zéro du compteur), en simulant la date à
    laquelle le rapport aurait dû être généré plutôt que de dépendre de
    l'heure système actuelle."""
    titre = PERIODES[nom_periode]

    if maintenant is None:
        maintenant = pd.Timestamp.now(tz="UTC")
    debut_local, fin_local = calculer_periode(nom_periode, maintenant)
    debut_utc, fin_utc = debut_local.tz_convert("UTC"), fin_local.tz_convert("UTC")
    # Mensuel a besoin du mois précédent en plus de la période du rapport
    # (comparaison "% ce mois-ci / le mois précédent", plus bas) — seul
    # endroit du fichier qui regarde en dehors de [debut_utc, fin_utc).
    borne_debut_utc = (
        (debut_local - pd.DateOffset(months=1)).tz_convert("UTC") if nom_periode == "mensuel" else debut_utc
    )
    df, alertes, evenements, variantes, calendrier, premiere_donnee = charger_donnees(borne_debut_utc, fin_utc)

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
    df_periode_complet, df_periode = filtrer_periode_arrivees(df, evenements, variantes, calendrier, debut_utc, fin_utc)

    en_retard, total = _compte_perturbees(df_periode)
    severe = _compte_severes(df_periode)
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
    # Même motif que calculer_stats_bloc (formatting.py, appli web) pour
    # retrouver le train responsable, pas seulement la valeur — demande
    # explicite de l'utilisateur, 2026-08-18. .astype(str) avant .str.
    # split() : trip_id peut être de type category selon l'origine du
    # DataFrame, sur laquelle .str.split() renverrait la représentation
    # texte de la liste plutôt qu'une vraie liste (voir calculer_stats_bloc,
    # bug déjà rencontré et corrigé ailleurs, 2026-08-14).
    train_par_passage = derniers.index.get_level_values("trip_id").astype(str).str.split(":").str[0]
    maximums_par_train = derniers.groupby(train_par_passage).max()
    retard_max_texte, _ = texte_categorie_maximale(
        maximums_par_train, "train", "trains", format_numero_train, lambda v: f"{v:.0f} min",
    )
    moyennes_par_gare = df_periode.groupby("gare")["retard_min"].mean()
    # texte_categorie_maximale (formatting.py) : même motif que "Retard max"
    # juste au-dessus et que la barre de stats de l'appli web (calculer_
    # stats_bloc) — gère déjà l'égalité entre plusieurs gares (jusqu'à 3
    # listées, "+N autres" au-delà) sans le biais alphabétique d'idxmax().
    # Inclut désormais la valeur moyenne ("→ moy X min"), pas seulement le(s)
    # nom(s) de gare comme avant (demande explicite de l'utilisateur,
    # 2026-08-18 — annule le choix du 2026-07-30 de l'omettre)."""
    pire_gare, pire_gare_pluriel = texte_categorie_maximale(
        moyennes_par_gare, "", "", lambda g: g, lambda v: f"moy {format_min_sans_zero(v)} min",
    )
    label_pire_gare = "Gare les + touchées" if pire_gare_pluriel else "Gare la + touchée"

    # Retard max AFFICHÉ (titre + échelle Y, plus bas) calculé sur
    # df_periode_complet (trajet complet, toutes gares) : sinon un pic de
    # retard survenu sur une gare hors ligne (ex: Coutances, Granville)
    # resterait invisible dans le titre alors qu'il est bien visible sur le
    # mini-graphique de cette même circulation (même bug déjà rencontré et
    # corrigé dans viewer.py, voir mémoire du projet). Retard max basé sur
    # la dernière valeur connue par passage (pas le maximum brut sur tous
    # les relevés, même correctif que plus haut) : sinon les 5 circulations
    # "les plus perturbées" mises en avant dans le rapport pourraient être
    # choisies sur une prédiction ponctuelle depuis corrigée, pas sur un
    # vrai retard confirmé — repéré par l'utilisateur, 2026-08-03.
    #
    # Sélection (quelles 5 circulations entrent dans le classement) basée
    # séparément sur retard_max_ligne, restreint à df_periode (les 11 gares
    # de la ligne) : un retard survenu uniquement hors ligne (Coutances,
    # Granville, voire une tout autre branche du même train physique)
    # n'a été ressenti par aucun voyageur Paris-Cherbourg — il ne doit donc
    # pas, à lui seul, faire entrer une circulation dans le top 5, même s'il
    # reste visible (grisé) sur son mini-graphique une fois sélectionnée
    # pour une autre raison — demande explicite de l'utilisateur, 2026-08-18,
    # vérifié sur le rapport hebdomadaire réel : 3 circulations sur 5
    # changeaient avec ce critère (Granville/Elbeuf - Saint-Aubin/
    # Dol-de-Bretagne remplacées par Bernay/Bonnières/Gaillon-Aubevoye).
    infos = df_periode_complet.groupby(["trip_id", "start_date"]).agg(train=("train", "first")).reset_index()
    retard_max_par_circulation = derniers_par_passage(df_periode_complet).groupby(
        level=["trip_id", "start_date"]
    ).max().rename("retard_max")
    # derniers (pas un nouvel appel derniers_par_passage(df_periode)) : même
    # résultat déjà calculé plus haut pour Retard cumulé/max — audit de
    # nettoyage, 2026-08-20.
    retard_max_ligne_par_circulation = derniers.groupby(
        level=["trip_id", "start_date"]
    ).max().rename("retard_max_ligne")
    infos = infos.merge(retard_max_par_circulation, on=["trip_id", "start_date"], how="left")
    infos = infos.merge(retard_max_ligne_par_circulation, on=["trip_id", "start_date"], how="left")
    top5 = infos.sort_values(["retard_max_ligne", "start_date"], ascending=[False, False]).head(5).copy()
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

    # Nombre de circulations distinctes annulées (événement "trajet_annule",
    # voir perturbations.detecter_evenements) détectées sur la période ET
    # dont le trajet théorique touche au moins une des 11 gares de la ligne
    # — même définition "sur la ligne" que retard_max_ligne (top5 plus
    # haut), pour rester cohérent avec le reste du rapport ; un trajet
    # annulé n'atteint jamais son terminus dans les données, donc
    # circulation_est_arrivee ci-dessus l'exclut de toutes les stats de
    # retard (Top5, moyennes...) : sans ce compteur à part, une annulation
    # restait invisible dans le rapport alors que c'est la pire forme de
    # perturbation possible — repéré par l'utilisateur sur le train 851116,
    # 2026-08-20. Même compteur que app_fastapi.annulations_periode (web),
    # dupliqué ici plutôt qu'importé — même convention que alertes_periode
    # juste au-dessus (ce script ne dépend jamais de app_fastapi.py).
    annulations_periode = evenements[
        (evenements["type"] == "trajet_annule")
        & (evenements["poll_time"] >= debut_utc) & (evenements["poll_time"] < fin_utc)
    ]

    def _touche_la_ligne(ligne):
        # Pas de str(...) sur start_date : choisir_variante le normalise
        # déjà en interne (str(int(start_date))), comme partout ailleurs
        # dans ce fichier (ex. lignes 195, 836) — audit de nettoyage,
        # 2026-08-20.
        variante = choisir_variante(variantes, calendrier, ligne["trip_id"], ligne["start_date"])
        return variante is not None and any(g in GARES_LIGNE for g in variante["gares"])

    # Garde explicite sur .empty avant .apply(axis=1) : sur un DataFrame
    # filtré à 0 ligne, .apply(axis=1) renvoie parfois un DataFrame (pas une
    # Series) selon la version de pandas installée — .sum() tombe alors sur
    # la colonne poll_time (datetime64), qui ne supporte pas sum() ("'Date
    # timeArray' ... does not support operation 'sum'") — confirmé en
    # pratique : reproductible avec pandas 3.0.5 (Pi), pas avec 3.0.3 (PC),
    # même DataFrame vide en entrée. Repéré au premier déploiement de ce
    # compteur, 2026-08-20 (aucune annulation "sur la ligne" ce jour-là).
    if annulations_periode.empty:
        noms_annulations = []
    else:
        annulations_periode = annulations_periode[annulations_periode.apply(_touche_la_ligne, axis=1)]
        noms_annulations = sorted({format_numero_train(t) for t in annulations_periode["train"]})
    nb_annulations = len(noms_annulations)

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
        # Comparaison affichée seulement si le mois précédent est entièrement
        # couvert par la collecte — sinon (ex: tout premier rapport mensuel,
        # données commencées en cours de mois précédent) la comparaison
        # serait faussée par un mois partiel, pas vraiment comparable.
        # premiere_donnee vient de charger_donnees() (requête SQL séparée,
        # pas df["poll_time"].min()) : df est maintenant borné à partir de
        # borne_debut_utc, donc son minimum collerait toujours à cette borne
        # même si la collecte remonte moins loin en réalité. borne_debut_utc
        # réutilisé tel quel ici (déjà égal à "1 mois avant debut_local" pour
        # un rapport mensuel, voir plus haut) plutôt que recalculé une 2e
        # fois — audit de nettoyage, 2026-08-20.
        if moyenne_mois is not None and pd.notna(premiere_donnee) and premiere_donnee <= borne_debut_utc:
            en_retard_prec, total_prec = stats_perturbees_periode(
                df, evenements, variantes, calendrier, borne_debut_utc, debut_utc,
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
    # l'encombrement réel de chaque axe, ce pad s'ajoute par-dessus. w_pad
    # (marge gauche/droite entre le cadre des graphiques et le bord de la
    # page, au lieu de la valeur par défaut de matplotlib ~0.042 pouce) :
    # demande explicite de l'utilisateur, 2026-09-01.
    fig.set_constrained_layout_pads(hspace=0.04, w_pad=0.15)
    # Le top 5 des circulations les plus perturbées ne représente presque
    # rien à l'échelle d'un mois (5 sur ~1500-1800, sous 0,5 %, contre ~4 %
    # pour un rapport quotidien) et fait doublon avec "Vue d'ensemble du
    # mois" (qui répond déjà au besoin d'une vue mensuelle) — retiré pour ce
    # seul type de rapport, gardé inchangé pour quotidien/hebdomadaire où il
    # reste représentatif — demande explicite de l'utilisateur, 2026-08-03.
    afficher_top5 = nom_periode != "mensuel"
    n_graphiques = (max(len(top5), 1) if afficher_top5 else 0)
    lignes_entete_top5 = 1 if afficher_top5 else 0
    # Le résumé "Perturbations" tient sur une seule ligne sous la météo
    # (voir plus bas) — les 2 dernières rangées (espaceur + liste détaillée)
    # ne sont donc réservées que s'il y a vraiment des alertes à détailler.
    has_alertes = not alertes_periode.empty
    # 6 rangées de plus pour "Vue d'ensemble du mois" (comparaison texte + 5
    # graphiques : % perturbées/jour, retard cumulé croissant, retard moyen
    # par gare, par jour de semaine, par heure), uniquement pour le rapport
    # mensuel — voir plus haut. Retard moyen par heure ajouté le 2026-08-23
    # (même correctif de fiabilité que gare/jour de semaine, voir
    # SEUIL_FIABLE_MENSUEL) — empilé en pleine largeur comme les 4 autres,
    # même principe que les mini-graphiques du top 5 (quotidien/hebdomadaire,
    # gs[..., :] plus bas) plutôt qu'une grille à 2 colonnes.
    lignes_mensuel = 6 if nom_periode == "mensuel" else 0
    gs = GridSpec(
        3 + lignes_mensuel + lignes_entete_top5 + n_graphiques + (2 if has_alertes else 0), 2, figure=fig,
        height_ratios=(
            [0.6, 0.75] + ([0.4, 1.3, 1.3, 1.1, 1.1, 1.1] if nom_periode == "mensuel" else [])
            + ([0.35] if afficher_top5 else [])
            + [1.3] * n_graphiques + ([0.3, 1.1] if has_alertes else []) + [0.4]
        ),
        wspace=0.25,
    )

    ax_titre = fig.add_subplot(gs[0, :])
    ax_titre.axis("off")
    # x en coordonnées FIGURE (transform mixte), y en coordonnées AXE : le
    # rapport n°39 (2026-08-28) avait déjà un correctif xlim/ylim ici, mais
    # insuffisant — repéré sur le n°42 (2026-08-31, décalage de +22pt/595pt
    # de large) que ce n'était pas le texte qui était mal centré DANS
    # ax_titre, mais ax_titre lui-même mal positionné SUR LA PAGE :
    # constrained_layout calcule la largeur des colonnes du GridSpec à
    # partir de l'encombrement de TOUS les axes qui les partagent, et les
    # mini-graphiques plus bas (gs[..., :] aussi) ont des étiquettes d'axe Y
    # seulement à gauche, sans marge symétrique à droite — ça décale toute
    # la colonne partagée, donc aussi cette ligne de titre, d'une quantité
    # qui varie selon la largeur de ces étiquettes (donc d'un rapport à
    # l'autre). transform=blended_transform_factory(fig.transFigure,
    # ax_titre.transAxes) centre le x sur la page ENTIÈRE (toujours
    # symétrique, indépendant de ce que fait constrained_layout aux
    # colonnes) tout en gardant le y relatif à cet axe (même comportement
    # vertical qu'avant, aucun risque de chevaucher ax_stats juste en
    # dessous). Même correctif appliqué à ax_entete_circulations plus bas.
    transform_titre = blended_transform_factory(fig.transFigure, ax_titre.transAxes)
    # "généré le" utilise l'heure réelle d'exécution (maintenant), distincte
    # de fin_local (2h, la borne de la période) depuis que la période n'est
    # plus une fenêtre glissante se terminant "maintenant".
    maintenant_local = maintenant.tz_convert(PARIS_TZ)
    # "Circulations sur l'axe" plutôt que "Suivi retards Paris ↔ Cherbourg" :
    # le périmètre couvre toute circulation empruntant un tronçon de la
    # ligne, pas seulement les trains Paris-Cherbourg de bout en bout (ex:
    # Rennes → Caen), l'ancien intitulé laissait penser le contraire.
    ax_titre.text(0.5, 0.75, f"{titre} — Circulations sur l'axe Paris ↔ Cherbourg",
                  fontsize=15, fontweight="bold", va="top", ha="center", transform=transform_titre)
    ax_titre.text(0.5, 0.15,
                  f"({texte_periode_rapport(nom_periode, debut_local, fin_local)}"
                  f"   ·   créé le {maintenant_local.strftime('%d/%m/%Y à %Hh%M')})",
                  fontsize=9, color="#555", va="top", ha="center", transform=transform_titre)

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
    #
    # "Retard cumulé/Retard max" (ligne2) a sa PROPRE ligne, plutôt que
    # d'être accolé à la ligne "circulations perturbées" comme avant : une
    # fois les deux pourcentages (recalibré compris) ajoutés à cette
    # dernière, le texte combiné dépassait la largeur imprimable et se
    # faisait tronquer net (repéré en testant, 2026-09-04) — même leçon déjà
    # apprise pour "Gare la + touchée" ci-dessous.
    if total:
        fraction = f"{en_retard}/{total}"
        # "circulations perturbées" — même libellé que viewer.py (pas "trains
        # en retard") : évite toute confusion avec la ponctualité officielle
        # SNCF/ART (calculée au terminus uniquement) — ce compteur inclut
        # toute circulation ayant eu du retard à un moment de son trajet,
        # même rattrapé avant l'arrivée.
        reste_ligne1 = f" circulations perturbées ({100 * en_retard / total:.0f} % au total — "
        # Second pourcentage recalibré (perturbations "significatives",
        # > 5 min) — même narratif que Circulations perturbées/Trajets
        # sans perturbation côté appli (base.html/_stats.html), demande
        # explicite de l'utilisateur, 2026-09-04 : un chiffre "au total"
        # peut sembler alarmant sans être très parlant, dilué par de
        # simples minutes vite rattrapées.
        texte_severe = f"{100 * severe / total:.0f} %"
        reste_ligne1_suite = " de perturbations significatives > 5 min)"
        heures_cumulees, minutes_cumulees = divmod(round(retard_cumule), 60)
        # Volontairement peu chiffré (pas de "N passages impactés", pas de
        # "retard moyen / relevé" — trop sujet à mauvaise lecture, voir
        # mémoire du projet) : juste de quoi situer l'ampleur (cumulé, pire
        # cas) sans noyer le lecteur sous les chiffres — demande explicite de
        # l'utilisateur, 2026-07-30.
        ligne2 = f"Retard cumulé {heures_cumulees} h {minutes_cumulees:02d} min · Retard max : {retard_max_texte}"
        texte_pire_gare = f"{label_pire_gare} : {pire_gare}"
    else:
        fraction = ""
        reste_ligne1 = "Aucune circulation arrivée sur cette période."
        texte_severe = ""
        reste_ligne1_suite = ""
        ligne2 = ""
        texte_pire_gare = ""
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
            TextArea(texte_severe, textprops=dict(fontsize=9, fontweight="bold", color="#dc2626")),
            TextArea(reste_ligne1_suite, textprops=dict(fontsize=9, fontweight="bold")),
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
    texte_alertes = (
        f"Perturbations sur la période : {len(alertes_periode)} alerte(s) active(s) (détail ci-dessous)."
        if has_alertes else "Perturbations sur la période : aucune alerte connue."
    )
    # Invisible des stats de retard ci-dessus (voir circulation_est_arrivee)
    # — d'où sa propre ligne, plutôt qu'un chiffre de plus noyé dans ligne2.
    if nb_annulations > SEUIL_TRAINS_ANNULES_AFFICHES:
        reste = nb_annulations - SEUIL_TRAINS_ANNULES_AFFICHES
        noms_texte = (
            f"{', '.join(noms_annulations[:SEUIL_TRAINS_ANNULES_AFFICHES])} "
            f"et {reste} autre{'s' if reste > 1 else ''}"
        )
    else:
        noms_texte = ", ".join(noms_annulations)
    texte_annulations = (
        f"Circulations annulées  : {nb_annulations} ({noms_texte})."
        if nb_annulations else "Circulations annulées  : aucune."
    )
    # Un seul ax_stats.text() multi-lignes (\n) pour tout ce détail, plutôt
    # que 5 appels séparés à des fractions d'axe choisies à la main : chaque
    # ax.text() a sa propre boîte de police, dont la hauteur réelle dépend
    # des glyphes présents sur CETTE ligne (accents, hampes montantes...) —
    # un même pas fixe en fraction d'axe donnait donc un espacement
    # visuellement irrégulier d'une ligne à l'autre (jusqu'à ~5pt de
    # recouvrement mesuré entre "Gare la + touchée" et "Météo"), pas
    # seulement entre la 1re ligne et les suivantes comme supposé au
    # premier correctif — repéré par l'utilisateur sur quotidien et
    # hebdomadaire (pas mensuel, par coïncidence de contenu ce jour-là, pas
    # une vraie différence structurelle), 2026-09-04. matplotlib calcule
    # lui-même un interlignage cohérent pour un bloc multi-lignes unique
    # (linespacing, en multiples de la taille de police) — beaucoup plus
    # robuste que de deviner des fractions à la main.
    lignes_detail = [l for l in [ligne2, texte_pire_gare] if l] + [texte_meteo, texte_alertes, texte_annulations]
    ax_stats.text(0, 0.75, "\n".join(lignes_detail), fontsize=8, color="#555", va="top", ha="left", linespacing=1.8)
    ax_stats.set_xlim(0, 1)
    ax_stats.set_ylim(0, 1)

    if nom_periode == "mensuel":
        ax_d = fig.add_subplot(gs[2, :])
        ax_d.axis("off")
        ax_d.set_xlim(0, 1)
        ax_d.set_ylim(0, 1)
        # Ni titre "Vue d'ensemble du mois" (les 5 graphiques juste en
        # dessous ont déjà chacun leur propre titre explicite) ni phrase de
        # repli quand la comparaison est indisponible (elle ne faisait que
        # reformuler le % déjà donné dans la barre de stats juste au-dessus,
        # sans aucune information nouvelle) — demande explicite de
        # l'utilisateur, 2026-09-01. Cette ligne ne s'affiche donc que
        # lorsqu'elle apporte une vraie comparaison.
        if moyenne_mois is not None and moyenne_mois_precedent is not None:
            delta = moyenne_mois - moyenne_mois_precedent
            couleur_delta = "#c0392b" if delta > 0 else "#2f855a"
            texte_comparaison = (
                f"{moyenne_mois:.0f} % de circulations perturbées ce mois-ci, contre "
                f"{moyenne_mois_precedent:.0f} % le mois précédent "
                f"({'+' if delta >= 0 else ''}{delta:.0f} points)"
            )
            ax_d.text(0, 0.5, texte_comparaison, fontsize=8.5, color=couleur_delta, va="center")

        ax_a = fig.add_subplot(gs[3, :])
        ax_b = fig.add_subplot(gs[4, :])
        # Ordre d'affichage (pas de calcul) : "par heure" remonté juste après
        # le retard cumulé, avant gare/jour de semaine — demande explicite de
        # l'utilisateur, 2026-08-23, jugé le plus parlant des 3 répartitions
        # (motif de dégradation en fin de soirée, plus actionnable que les
        # variations gare/jour). Seule la ligne gs[N, :] de chaque ax change
        # ici — les blocs de calcul/tracé plus bas, eux, restent à leur place.
        ax_f = fig.add_subplot(gs[5, :])
        ax_c = fig.add_subplot(gs[6, :])
        ax_e = fig.add_subplot(gs[7, :])
        if df_periode.empty:
            # Rien à tracer (mois entièrement sans donnée) : un axe avec
            # uniquement des valeurs manquantes n'a pas de bornes valides
            # pour matplotlib (set_xlim/set_ylim refusent NaN) — même
            # traitement "message de repli" que le top 5 vide plus bas,
            # plutôt que de forcer un graphique sans contenu réel.
            for ax in (ax_a, ax_b, ax_c, ax_e, ax_f):
                ax.axis("off")
            ax_a.text(0, 0.5, "Aucune donnée sur ce mois.", fontsize=9, color="#555", va="center")
        else:
            ax_a.plot(jours_periode, pct_par_jour.values, color="#c2410c", marker="o", markersize=3, linewidth=1.3)
            marquer_ligne_moyenne(ax_a, moyenne_mois, "#c2410c", " %")
            ax_a.set_title("% de circulations perturbées, jour par jour", fontsize=9, fontweight="bold", loc="center")
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
            ax_b.set_title("Retard cumulé sur le mois (croissant)", fontsize=9, fontweight="bold", loc="center")
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
            # Titre dynamique ("— max : ..."), même principe que l'onglet
            # web Par jour/heure (titre_dynamique_jour_heure) — demande
            # explicite de l'utilisateur, 2026-08-20, pour la même
            # précision dans le rapport mensuel PDF.
            comptes_par_gare_mois = df_periode.groupby("gare")["retard_min"].count().reindex(GARES_LIGNE_ORDRE)
            # n_circulations (PAS comptes_par_gare_mois, des relevés bruts) :
            # utilisé par titre_dynamique_jour_heure pour la fiabilité, voir
            # SEUIL_FIABLE_MENSUEL ci-dessus.
            circulations_par_gare_mois = df_periode.assign(_cle=cle_circulation(df_periode)) \
                .groupby("gare")["_cle"].nunique().reindex(GARES_LIGNE_ORDRE)
            stats_gare_mois = pd.DataFrame({
                "moyenne": moyennes_par_gare_mois, "n": comptes_par_gare_mois,
                "n_circulations": circulations_par_gare_mois,
            })
            titre_gare_mois = titre_dynamique_jour_heure(
                "Retard moyen par gare", stats_gare_mois, "moyenne", GARES_LIGNE_ORDRE, str,
                lambda v: f"{v:.1f} min", SEUIL_FIABLE_MENSUEL,
            )
            ax_c.set_title(titre_gare_mois, fontsize=9, fontweight="bold", loc="center")
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
            comptes_par_jour_mois = df_periode.groupby(jour_semaine)["retard_min"].count().reindex(JOURS_SEMAINE_ORDRE)
            circulations_par_jour_mois = df_periode.assign(_cle=cle_circulation(df_periode)) \
                .groupby(jour_semaine)["_cle"].nunique().reindex(JOURS_SEMAINE_ORDRE)
            stats_jour_mois = pd.DataFrame({
                "moyenne": moyennes_par_jour_mois, "n": comptes_par_jour_mois,
                "n_circulations": circulations_par_jour_mois,
            })
            titre_jour_mois = titre_dynamique_jour_heure(
                "Retard moyen par jour", stats_jour_mois, "moyenne", JOURS_SEMAINE_ORDRE, str.lower,
                lambda v: f"{v:.1f} min", SEUIL_FIABLE_MENSUEL,
            )
            ax_e.set_title(titre_jour_mois, fontsize=9, fontweight="bold", loc="center")
            ax_e.set_ylabel("min", fontsize=8)
            ax_e.tick_params(axis="y", labelsize=7)
            ax_e.axhline(0, color="gray", linewidth=0.6)
            marquer_moyenne(ax_e, moyennes_par_jour_mois, "#5ba58c", " min")
            finaliser_axes(ax_e)

            # 3e vue "vue d'ensemble" (heure de collecte) — même donnée que
            # l'onglet web "Par jour / heure" (calculer_contexte_jour_heure_
            # sql), demande explicite de l'utilisateur, 2026-08-23, à la
            # suite du correctif de fiabilité (1h/2h grisées à tort par un
            # seul train très en retard, repollé sur plusieurs gares).
            labels_heure_mois = [f"{h}h" for h in HEURES_ORDRE]
            moyennes_par_heure_mois = df_periode.groupby("heure_locale")["retard_min"].mean().reindex(HEURES_ORDRE)
            ax_f.bar(range(len(HEURES_ORDRE)), moyennes_par_heure_mois.values, color="#2a8f8f")
            ax_f.set_xticks(range(len(HEURES_ORDRE)))
            ax_f.set_xticklabels(labels_heure_mois, fontsize=6)
            comptes_par_heure_mois = df_periode.groupby("heure_locale")["retard_min"].count().reindex(HEURES_ORDRE)
            circulations_par_heure_mois = df_periode.assign(_cle=cle_circulation(df_periode)) \
                .groupby("heure_locale")["_cle"].nunique().reindex(HEURES_ORDRE)
            stats_heure_mois = pd.DataFrame({
                "moyenne": moyennes_par_heure_mois, "n": comptes_par_heure_mois,
                "n_circulations": circulations_par_heure_mois,
            })
            titre_heure_mois = titre_dynamique_jour_heure(
                "Retard moyen par heure", stats_heure_mois, "moyenne", labels_heure_mois, lambda l: f"à {l}",
                lambda v: f"{v:.1f} min", SEUIL_FIABLE_MENSUEL,
            )
            ax_f.set_title(titre_heure_mois, fontsize=9, fontweight="bold", loc="center")
            ax_f.set_ylabel("min", fontsize=8)
            ax_f.tick_params(axis="y", labelsize=7)
            ax_f.axhline(0, color="gray", linewidth=0.6)
            marquer_moyenne(ax_f, moyennes_par_heure_mois, "#2a8f8f", " min")
            finaliser_axes(ax_f)

    if afficher_top5:
        ax_entete_circulations = fig.add_subplot(gs[2 + lignes_mensuel, :])
        ax_entete_circulations.axis("off")
        # x en coordonnées FIGURE, y en coordonnées AXE — même correctif et
        # même raison que transform_titre plus haut (constrained_layout).
        transform_entete_circulations = blended_transform_factory(fig.transFigure, ax_entete_circulations.transAxes)
        ax_entete_circulations.text(0.5, 0.5, "Évolution du retard gare par gare des 5 circulations les plus perturbées", fontsize=10, fontweight="bold",
                                     va="center", ha="center", transform=transform_entete_circulations)

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
                # Suffixe "(X sur la ligne)" quand le pic toutes gares
                # confondues (retard_max) et celui restreint aux 11 gares de
                # la ligne (retard_max_ligne, le chiffre utilisé pour le
                # classement top5 ci-dessus et pour le "Retard max" de la
                # barre de stats) diffèrent une fois arrondis — même fix que
                # app_fastapi.py (rapport_top5), pour garder les deux titres
                # identiques, 2026-08-19.
                retard_max_arrondi = round(ligne["retard_max"])
                retard_max_ligne_arrondi = (
                    round(ligne["retard_max_ligne"]) if pd.notna(ligne["retard_max_ligne"]) else None
                )
                suffixe_ligne = (
                    f" ({retard_max_ligne_arrondi} min sur la ligne)"
                    if retard_max_ligne_arrondi is not None and retard_max_ligne_arrondi != retard_max_arrondi
                    else ""
                )
                ax_g.set_title(
                    f"train {format_numero_train(ligne['train'])} ({ligne['sens']}) — {date_str} — "
                    f"max {retard_max_arrondi} min{suffixe_ligne}",
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
    # generer() est rappelable en boucle dans le même process (voir son
    # docstring, réédition d'un rapport passé pour un backfill) — sans
    # cette fermeture, chaque figure matplotlib resterait en mémoire d'un
    # appel à l'autre. Audit de nettoyage, 2026-08-20.
    plt.close(fig)
    print(f"Rapport généré : {chemin}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in PERIODES:
        print("Usage : python generer_rapport.py quotidien|hebdomadaire|mensuel")
        sys.exit(1)
    generer(sys.argv[1])
