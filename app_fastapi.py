"""Interface web (FastAPI + Jinja2 + htmx) — remplace la piste Streamlit
(app_streamlit.py, conservé pour l'instant sans modification) : même besoin
de contrôle total sur le HTML/CSS/JS que la piste précédente cherchait à
éviter en luttant contre les internals de Streamlit. Onglets Tableau et
Graphique (celui-ci en Plotly.js, un vrai graphique interactif — pas une
image statique) — voir mémoire du projet.

À lancer sur la VPS, dans le dossier du projet, là où se trouve
observations.db (SQLite, écrit en continu par collect_realtime.py — voir
ce fichier, correctif du 2026-08-13). Contrairement à une version
antérieure, cette appli ne rapatrie plus rien depuis un Raspberry Pi
distant (bouton "Rafraîchir"/route /rafraichir, retirés) : elle tourne sur
la même machine que la collecte, qui écrit déjà localement.

    uvicorn app_fastapi:app --reload
"""
import html
import json
import math
import os
import re
import sqlite3
import textwrap
from contextlib import asynccontextmanager
from datetime import datetime

import pandas as pd
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from formatting import (
    PARIS_TZ,
    build_stop_names,
    build_trip_data,
    calculer_stats_bloc,
    choisir_variante,
    cle_circulation,
    derniers_par_passage,
    duree_theorique,
    estimer_passage_reel,
    format_bool_oui_non,
    format_entier,
    format_gare,
    format_heure_avec_arret,
    format_min_sans_zero,
    format_poll_time,
    format_retard,
    format_valeur,
    load_calendrier,
    load_reference,
    sans_date_trip_id,
    trajet_sens,
)
from perturbations import charger_alertes, charger_evenements
from verifier_gtfs import charger_journal

OBSERVATIONS_DB = "observations.db"

# Fichiers locaux écrits directement par la collecte tournant sur cette même
# machine (collect_alertes.py, perturbations.py, verifier_gtfs.py) — plus de
# distinction chemin distant/local depuis le retrait du bouton Rafraîchir
# (2026-08-13, voir docstring du module) : ce module tire google.transit au
# chargement (utilisé par la seule fonction d'écriture de perturbations.py,
# detecter_evenements, jamais appelée ici), coût déjà accepté par la
# collecte donc pas un nouveau poids pour ce fichier.
LOCAL_ALERTES = "alertes.csv"
PERTURBATIONS_FILE = "perturbations_detectees.csv"
GTFS_LOG_FILE = "verification_gtfs.log"

SEUIL_RETARD_FORT = 10  # minutes
SEUIL_RETARD_MOYEN = 5  # minutes
SEUIL_FIABLE = 30  # nb minimal de relevés pour considérer une barre fiable (viewer.py:1868)

# Mêmes 11 gares que viewer.py/generer_rapport.py/app_streamlit.py — copie
# locale volontaire, GARES_LIGNE existe déjà sous plusieurs formes
# indépendantes dans ce projet, les unifier serait un refactor séparé, hors
# scope ici (voir plan).
GARES_LIGNE_ORDRE = (
    "Paris Saint-Lazare", "Mantes-la-Jolie", "Évreux Normandie", "Bernay",
    "Lisieux", "Caen", "Bayeux", "Lison", "Carentan", "Valognes", "Cherbourg",
)
GARES_LIGNE = set(GARES_LIGNE_ORDRE)

TITRE_APP = "Suivi des circulations sur l'axe Paris ↔ Cherbourg"

COLONNES = ["poll_time", "train", "sens", "gare", "heure_theorique", "retard_arrivee_min", "retard_depart_min",
            "temperature_c", "precipitation_mm", "wind_speed_kmh", "type_jour", "vacances_scolaires",
            "arrets_restants"]
ENTETES = {
    "poll_time": "Relevé", "train": "Train", "sens": "Sens", "gare": "Gare",
    "heure_theorique": "Heure théo.", "retard_arrivee_min": "Arr. (min)", "retard_depart_min": "Dép. (min)",
    "temperature_c": "Temp. (°C)", "precipitation_mm": "Pluie (mm)", "wind_speed_kmh": "Vent (km/h)",
    "type_jour": "Jour", "vacances_scolaires": "Vacances", "arrets_restants": "Arrêts",
}

# Même sélecteur que periode_graphique_var (viewer.py) / app_streamlit.py.
PERIODE_OPTIONS = ["dernières 24h", "3 derniers jours", "7 derniers jours", "tout l'historique"]
DUREE_PAR_PERIODE = {
    "dernières 24h": pd.Timedelta(hours=24),
    "3 derniers jours": pd.Timedelta(days=3),
    "7 derniers jours": pd.Timedelta(days=7),
}
# Un relevé toutes les 5 min (cron de collecte) : un écart nettement supérieur
# signale un vrai trou de collecte, pas une série de vraies valeurs à 0 (même
# seuil que tracer_serie_temporelle, graphiques.py).
SEUIL_TROU = pd.Timedelta(minutes=10)


def mise_en_forme_hover(texte, largeur=55):
    """Plotly ne retourne pas automatiquement le texte d'une info-bulle à
    la ligne (contrairement à un <div> HTML normal) — une longue phrase
    d'explication s'affiche alors sur une seule ligne aussi large que tout
    le graphique. Insère des <br> aux mêmes endroits qu'un retour à la
    ligne classique (textwrap), repéré en pratique (Playwright, 2026-08-05)."""
    return "<br>".join(textwrap.wrap(texte, largeur))


def json_pour_script(valeur):
    """json.dumps() qui échappe aussi "</" — une chaîne contenant
    "</script>" (ex: un nom de gare hypothétique) ne doit pas fermer
    prématurément la balise <script> dans laquelle ce JSON est injecté
    (_graphique.html/_jour_heure.html/_train.html). Centralise un motif qui
    était répété à l'identique à plusieurs endroits (audit du 2026-08-10)."""
    return json.dumps(valeur).replace("</", "<\\/")


def _format_start_date(start_date):
    """AAAAMMJJ (start_date GTFS, ex: "20260810") -> JJ/MM/AAAA. Distinct de
    _format_date_gtfs (plus bas) : format source différent ("AAAA-MM-JJ",
    horodatages du journal verifier_gtfs.py)."""
    return datetime.strptime(str(start_date), "%Y%m%d").strftime("%d/%m/%Y")


# Chargé une fois au démarrage (voir lifespan plus bas), équivalent du
# @st.cache_data de la version Streamlit — change rarement.
reference_donnees = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    reference = load_reference()
    stop_names = build_stop_names(reference)
    reference_donnees["stop_names"] = stop_names
    # variantes/calendrier : remplacent les anciens trajet_gares/
    # trajet_horaires/scheduled_times/trajet_arrets/temps_arret séparés —
    # un même train peut avoir plusieurs variantes d'horaire selon la
    # période, choisir_variante() choisit la bonne selon la date réelle
    # demandée (voir formatting.py, correctif du 2026-08-12).
    reference_donnees["variantes"] = build_trip_data(reference)
    reference_donnees["calendrier"] = load_calendrier()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# Cache mémoire du référentiel préparé (df + colonnes dérivées), invalidé
# sur MAX(poll_time) — voir charger_observations.
_cache_observations = {"dernier_poll": None, "df": None}


def charger_observations():
    """observations.db (SQLite) est réécrit en continu par
    collect_realtime.py (toutes les ~5 min) : on veut refléter des données
    fraîches, mais pas payer le coût de la relecture + préparation complète
    à CHAQUE requête, y compris un simple changement d'onglet qui ne
    change rien aux données elles-mêmes (repéré par l'utilisateur,
    2026-08-05, à l'époque du CSV — le raisonnement reste identique).
    Cache invalidé via MAX(poll_time) (requête bon marché, poll_time déjà
    indexé) plutôt qu'un mtime sur le fichier .db (en mode WAL, le fichier
    .db principal ne change pas forcément de date de modification à chaque
    écriture tant qu'aucun checkpoint WAL n'a eu lieu) — PRAGMA data_version
    essayé en premier (documenté comme fait exactement pour ce cas d'usage),
    mais rejeté après un test empirique en conditions réelles, 2026-08-13 :
    sa valeur ne bougeait pas d'une connexion à l'autre malgré un vrai
    insert commité entre-temps par une connexion tierce (cron
    collect_realtime.py) — bug utilisateur réel provoqué par ça (page
    figée sur d'anciennes données malgré des rafraîchissements répétés),
    diagnostiqué en isolant le problème avant de changer d'approche plutôt
    que de deviner.

    Rechargement INCRÉMENTAL (seulement les lignes dont poll_time est
    postérieur au dernier chargement, concaténées au cache existant) plutôt
    que tout relire + tout repasser par preparer_donnees() (notamment son
    .apply() ligne à ligne coûteux pour heure_theorique) à chaque nouvelle
    collecte — repéré le jour même de l'import de l'historique du Pi
    (889k lignes) : l'ancienne version, correcte à la petite échelle
    d'avant l'import, rechargeait et retraitait la table ENTIÈRE toutes les
    ~5 min dès qu'un seul nouveau relevé arrivait, jusqu'à ~1,6 Go de RAM
    par rechargement — a fait planter la VPS entière (mémoire saturée,
    plus aucune réponse réseau, redémarrage matériel nécessaire depuis le
    panneau IONOS). Voir mémoire du projet, 2026-08-13.

    Même gestion d'erreur que load_local_data (viewer.py) : un fichier
    absent/corrompu ne doit pas planter la requête, renvoyé comme message
    d'erreur affiché proprement au lieu d'une 500."""
    if not os.path.isfile(OBSERVATIONS_DB):
        return None, f"Aucune donnée locale ({OBSERVATIONS_DB} introuvable)."

    # sqlite3.connect() crée silencieusement un fichier vide s'il n'existe
    # pas — d'où la vérification os.path.isfile ci-dessus, faite AVANT de
    # se connecter, pour ne jamais créer par erreur un observations.db vide
    # juste en le lisant.
    connexion = sqlite3.connect(OBSERVATIONS_DB)
    try:
        dernier_poll = connexion.execute("SELECT MAX(poll_time) FROM observations").fetchone()[0]

        if _cache_observations["dernier_poll"] != dernier_poll:
            try:
                # ORDER BY poll_time (pas rowid) : le reste du code (ex:
                # resume_collecte, plus bas) suppose df["poll_time"].iloc[0]/
                # iloc[-1] égal au premier/dernier relevé chronologique, comme
                # le garantissait l'ordre d'ajout du CSV — rowid le
                # garantirait aussi pour un simple append-only, mais plus une
                # fois un historique importé après coup (voir mémoire du
                # projet, migration Pi -> VPS 2026-08-13 : rowid ne
                # correspondait alors plus à l'ordre chronologique réel).
                # Déjà indexé (idx_observations_poll_time, collect_realtime.py).
                if _cache_observations["df"] is None:
                    nouvelles = pd.read_sql_query(
                        "SELECT * FROM observations ORDER BY poll_time", connexion,
                    )
                else:
                    nouvelles = pd.read_sql_query(
                        "SELECT * FROM observations WHERE poll_time > ? ORDER BY poll_time",
                        connexion, params=(_cache_observations["dernier_poll"],),
                    )
            except (sqlite3.DatabaseError, pd.errors.DatabaseError) as exc:
                return None, f"{OBSERVATIONS_DB} semble corrompu ({type(exc).__name__})."
            nouvelles = preparer_donnees(
                nouvelles, reference_donnees["stop_names"], reference_donnees["variantes"],
                reference_donnees["calendrier"],
            )
            if _cache_observations["df"] is None:
                _cache_observations["df"] = nouvelles
            else:
                _cache_observations["df"] = pd.concat(
                    [_cache_observations["df"], nouvelles], ignore_index=True,
                )
            _cache_observations["dernier_poll"] = dernier_poll
    finally:
        connexion.close()

    # Copie : les appelants filtrent/dérivent à partir de ce df (filtrer_df,
    # construire_lignes_tableau...) — une copie évite tout risque qu'une
    # mutation accidentelle d'un appelant corrompe le cache partagé entre
    # requêtes.
    return _cache_observations["df"].copy(), None


def preparer_donnees(df, stop_names, variantes, calendrier):
    df["gare"] = df["stop_id"].map(stop_names).fillna(df["stop_id"])
    # trip_id doit encore être en object/str ici, pas category (conversion
    # plus bas) : .str.split() sur un CategoricalIndex/une Series category
    # ne renvoie pas de vraies listes mais leur représentation texte (bug
    # rencontré dans formatting.calculer_stats_bloc, corrigé le 2026-08-14)
    # — ce .str.split()-ci reste sûr tant que cet ordre n'est pas inversé.
    df["train"] = df["trip_id"].str.split(":").str[0]
    df["sens"] = df["trip_id"].map(lambda t: trajet_sens(t, variantes))

    # Mémoïse choisir_variante par (trip_id, start_date) : beaucoup de
    # lignes (une par gare, répétées à chaque relevé) partagent la même
    # circulation réelle — pas la peine de refaire la recherche parmi les
    # variantes/le calendrier à chaque ligne individuellement.
    cache_variante = {}

    def variante_pour_ligne(trip_id, start_date):
        cle = (trip_id, start_date)
        if cle not in cache_variante:
            cache_variante[cle] = choisir_variante(variantes, calendrier, trip_id, start_date)
        return cache_variante[cle]

    def heure_theorique_ligne(r):
        variante = variante_pour_ligne(r["trip_id"], r["start_date"])
        if variante is None:
            return format_heure_avec_arret(None, r["start_date"], None)
        return format_heure_avec_arret(
            variante["horaires_par_stop"].get(r["stop_id"]), r["start_date"],
            variante["arrets_par_stop"].get(r["stop_id"]),
        )

    df["heure_theorique"] = df.apply(heure_theorique_ligne, axis=1)
    df["retard_arrivee_min"] = (df["arrival_delay_s"] / 60).round(1)
    df["retard_depart_min"] = (df["departure_delay_s"] / 60).round(1)
    df["retard_min"] = df["retard_arrivee_min"].fillna(df["retard_depart_min"])

    # category plutôt que object (chaîne Python "normale") pour les colonnes
    # à faible cardinalité : quelques dizaines/milliers de valeurs distinctes
    # répétées des centaines de milliers de fois (ex: ~20 gares, quelques
    # centaines de trains) — category ne stocke chaque valeur unique qu'une
    # fois + un petit code entier par ligne, au lieu d'un objet chaîne par
    # ligne. Ajouté le 2026-08-13 après un vrai incident : garder tout
    # l'historique en mémoire (df["gare"]/["train"]/... en object) faisait
    # tourner l'appli à ~1,6 Go de RAM sur la VPS (889k lignes) — voir
    # mémoire du projet. Vérifié avant déploiement qu'aucun tri
    # (sort_values) ne porte directement sur ces colonnes ailleurs dans le
    # fichier (seul un sorted() sur une liste déjà convertie en Python pur
    # via .tolist() les utilise, insensible à l'ordre des catégories).
    for colonne in ("gare", "train", "sens", "type_jour", "trip_id", "stop_id", "heure_theorique", "start_date"):
        df[colonne] = df[colonne].astype("category")

    return df


def options_gare(df):
    """Même regroupement "Toutes" / "— Gares de la ligne —" / "— Autres
    gares (jonction) —" que viewer.py/app_streamlit.py. Les entrées
    "— ... —" sont de simples séparateurs visuels, jamais un vrai filtre
    (voir filtrer_df)."""
    gares_vues = df["gare"].dropna().unique().tolist()
    gares_de_la_ligne = sorted(g for g in gares_vues if g in GARES_LIGNE)
    gares_hors_ligne = sorted(g for g in gares_vues if g not in GARES_LIGNE)
    options = ["Toutes"]
    if gares_de_la_ligne:
        options += ["— Gares de la ligne —"] + gares_de_la_ligne
    if gares_hors_ligne:
        options += ["— Autres gares (jonction) —"] + gares_hors_ligne
    return options


def _gare_est_filtree(gare):
    """Une "Gare" réellement sélectionnée dans le filtre — pas "Toutes" ni
    un séparateur visuel "— ... —" (voir options_gare). Même garde répétée
    à 2 endroits (filtrer_df, calculer_contexte_graphique) avant cette
    factorisation (audit du 2026-08-10)."""
    return bool(gare) and gare != "Toutes" and not gare.startswith("—")


def filtrer_df(df, gare, train, sens, appliquer_limite_ligne):
    """Comme _filtered_df_avant_retard (viewer.py) : Gare/Train/Sens +
    "Limiter aux gares de la ligne", sans "Limiter aux trains avec retard"
    (voir restreindre_aux_trains_en_retard, appliqué à part)."""
    if _gare_est_filtree(gare):
        df = df[df["gare"] == gare]
    if train and train != "Tous":
        df = df[df["train"] == train]
    if sens and sens != "Tous":
        df = df[df["sens"] == sens]
    if appliquer_limite_ligne:
        df = df[df["gare"].isin(GARES_LIGNE)]
    return df


def restreindre_aux_trains_en_retard(df):
    """Comme _restreindre_aux_trains_en_retard (viewer.py) : ne garde que
    les circulations ayant eu au moins un retard > 0 quelque part dans df
    (clé trip_id + start_date, voir cle_circulation — un même trip_id peut
    être réutilisé par plusieurs circulations réelles)."""
    circulation = cle_circulation(df)
    circulations_en_retard = circulation[df["retard_min"] > 0].unique()
    return df[circulation.isin(circulations_en_retard)]


def couleur_ligne(row):
    """Réplique la priorité à 4 niveaux de _render_table (viewer.py) :
    hors_ligne > retard_fort > retard_moyen > depart_retard > défaut.
    Renvoie une classe CSS (les couleurs elles-mêmes vivent dans
    static/style.css) plutôt qu'un hex inline comme app_streamlit.py —
    plus proche de l'esprit "CSS écrit à la main, sous contrôle". Appelée
    sur les valeurs NUMÉRIQUES d'origine (avant format_retard etc.), sinon
    les comparaisons >= échoueraient sur du texte ("+5.0", "–")."""
    if row["gare"] not in GARES_LIGNE:
        return "hors-ligne"
    if pd.notna(row["retard_arrivee_min"]) and row["retard_arrivee_min"] >= SEUIL_RETARD_FORT:
        return "retard-fort"
    if pd.notna(row["retard_arrivee_min"]) and row["retard_arrivee_min"] >= SEUIL_RETARD_MOYEN:
        return "retard-moyen"
    if pd.notna(row["retard_depart_min"]) and row["retard_depart_min"] >= SEUIL_RETARD_MOYEN:
        return "depart-retard"
    return None


def lire_checkbox(request: Request, nom: str, defaut: bool) -> bool:
    """Une case à cocher HTML non cochée n'est pas envoyée du tout dans la
    requête — contrairement à un <select>. Pour lever l'ambiguïté entre
    "case décochée" et "paramètre absent car première visite", le
    formulaire (voir base.html) envoie systématiquement un couple <input
    type=hidden value=false> + <input type=checkbox value=true> de même
    nom : la présence de "true" dans la liste des valeurs reçues pour ce
    nom fait foi. `defaut` ne s'applique donc que si le paramètre est
    complètement absent de la requête (tout premier chargement de la page,
    sans aucun query param)."""
    if nom not in request.query_params:
        return defaut
    return "true" in request.query_params.getlist(nom)


def calculer_alertes_actives(alertes_df):
    """Porte le calcul d'activité de _render_travaux_tab (viewer.py:
    684-687) : une alerte sans début/fin connu (champ vide dans le flux
    SNCF) est traitée comme active plutôt qu'ignorée silencieusement.
    Renvoie (masque actif, libellé du badge d'onglet — voir #onglets,
    base.html)."""
    maintenant = pd.Timestamp.now(tz="UTC")
    actif = (alertes_df["debut"].isna() | (alertes_df["debut"] <= maintenant)) & \
            (alertes_df["fin"].isna() | (maintenant <= alertes_df["fin"]))
    n_actives = int(actif.sum())
    libelle = f"Travaux / Alertes ⚠ ({n_actives})" if n_actives else "Travaux / Alertes"
    return actif, libelle


# Système de coordonnées fixe pour la frise (#pied-de-page) : contrairement
# au tk.Canvas de viewer.py, qui recalculait les positions à chaque
# redimensionnement (largeur réelle en pixels), le SVG se redimensionne
# lui-même dans le navigateur (viewBox + preserveAspectRatio="none",
# _frise.html) — ces constantes ne sont qu'un repère interne, jamais des
# pixels réels.
FRISE_VIEWBOX_W = 1000
FRISE_MARGE_X = 90
FRISE_Y_LIGNE = 30


def _infos_trajet_depuis_route(route):
    """Porte _infos_trajet_sens (viewer.py:1152-1200), mais appliquée à une
    circulation précise plutôt qu'à un Sens : à partir de sa liste réelle de
    gares parcourues (route, déjà dans l'ordre réel de circulation — voir
    reference_donnees["variantes"]/choisir_variante), repère si elle
    entre/sort de la ligne par une gare hors des 11 (ex: Saint-Lô via
    Lison).

    Ancienne version (repéré par l'utilisateur, 2026-08-11, corrigé puis
    abandonné le jour même) : cette fonction essayait de deviner une
    circulation "représentative" pour tout un Sens (ex: "PARIS → CHERB"),
    d'abord en prenant juste la première trouvée, puis celle qui dessert le
    plus de gares — mais un Sens recouvre plusieurs trajets physiques
    réellement différents (certains Paris-Cherbourg sautent Évreux
    Normandie/Bernay/Lisieux, d'autres s'y arrêtent, d'autres encore filent
    direct de Paris à Caen), donc aucun représentant unique n'est correct
    pour tous. La frise n'estompe plus désormais que pour une circulation
    précise et réellement sélectionnée (Suivi d'un train), plus jamais pour
    un Sens générique (Tableau/Graphique/...), voir calculer_contexte_frise.

    Retourne None si route est vide/absente. Sinon :
    (gares_sur_trajet, ordre_reel, connecteur_avant, connecteur_apres) —
    voir le docstring de la méthode d'origine pour le détail des deux
    connecteurs."""
    if not route:
        return None
    gares_set = set(route)
    gares_sur_trajet = [g for g in GARES_LIGNE_ORDRE if g in gares_set]
    if not gares_sur_trajet:
        return None
    pos = {g: i for i, g in enumerate(route)}
    i_debut, i_fin = gares_sur_trajet[0], gares_sur_trajet[-1]
    premier, dernier = (i_debut, i_fin) if pos[i_debut] <= pos[i_fin] else (i_fin, i_debut)
    connecteur_avant = (GARES_LIGNE_ORDRE.index(premier), route[0]) if pos[premier] > 0 else None
    connecteur_apres = (
        (GARES_LIGNE_ORDRE.index(dernier), route[-1]) if pos[dernier] < len(route) - 1 else None
    )
    ordre_reel = sorted(gares_sur_trajet, key=lambda g: pos[g])
    return gares_sur_trajet, ordre_reel, connecteur_avant, connecteur_apres


def _connecteur_frise(xs, connecteur, entrant, vers_la_gauche):
    """Porte _dessiner_connecteur_hors_ligne (viewer.py:1202-1234) : géométrie
    d'une flèche pointillée en angle (10°, vers le haut) reliant une gare de
    la ligne à une gare hors ligne. entrant=True : la pointe touche la gare
    de la ligne (le trajet y arrive depuis l'extérieur) ; entrant=False :
    la pointe s'en éloigne. vers_la_gauche : sens horizontal de la flèche.
    Renvoie des coordonnées (pas un dessin direct, contrairement à
    l'original) dans le système FRISE_VIEWBOX_W/FRISE_Y_LIGNE."""
    if connecteur is None:
        return None
    index_gare, nom_hors_ligne = connecteur
    x_gare = xs[index_gare]
    angle = math.radians(10)
    signe = -1 if vers_la_gauche else 1
    rayon_point, longueur = 13, 65
    x_bord = x_gare + signe * rayon_point * math.cos(angle)
    y_bord = FRISE_Y_LIGNE - rayon_point * math.sin(angle)
    x_bout = x_gare + signe * longueur * math.cos(angle)
    y_bout = FRISE_Y_LIGNE - longueur * math.sin(angle)
    depart, arrivee = ((x_bout, y_bout), (x_bord, y_bord)) if entrant else ((x_bord, y_bord), (x_bout, y_bout))
    return {
        "x1": depart[0], "y1": depart[1], "x2": arrivee[0], "y2": arrivee[1],
        "label_x": x_bout + 4 * signe, "label_y": y_bout,
        "label_texte": format_gare(nom_hors_ligne),
        "label_ancrage": "end" if vers_la_gauche else "start",
    }


def calculer_contexte_frise(df_avant_retard, vue, trajet_choisi):
    """Porte _render_frise (viewer.py:1236-1367) : vue d'ensemble des 11
    gares de la ligne, un point par gare coloré selon le retard moyen
    (mêmes seuils que le Tableau), calculée sur df_avant_retard — jamais
    "Limiter aux trains avec retard", qui gonflerait artificiellement la
    moyenne en excluant les trains ponctuels. Toujours les 11 gares, même
    si "Limiter aux gares de la ligne" est décochée (ce filtre ne retire
    jamais ces 11-là de df_avant_retard, seulement d'éventuelles gares hors
    ligne).

    Les gares non desservies ne sont estompées QUE sur l'onglet Suivi d'un
    train (vue == "train"), lorsqu'une circulation précise est choisie
    (trajet_choisi) — jamais selon le Sens seul (voir le docstring de
    _infos_trajet_depuis_route pour pourquoi ça a été essayé puis
    abandonné, 2026-08-11) : sur les autres onglets, qui montrent
    potentiellement des dizaines de circulations différentes à la fois, il
    n'existe pas de "trajet" unique et correct à représenter."""
    moyennes = df_avant_retard.groupby("gare")["retard_min"].mean()
    route = None
    if vue == "train" and trajet_choisi:
        trip_id, start_date = trajet_choisi.split("|")
        variante = choisir_variante(
            reference_donnees["variantes"], reference_donnees["calendrier"], trip_id, start_date,
        )
        route = variante["gares"] if variante else None
    infos_trajet = _infos_trajet_depuis_route(route) if route else None

    n = len(GARES_LIGNE_ORDRE)
    largeur_utile = FRISE_VIEWBOX_W - 2 * FRISE_MARGE_X
    xs = [FRISE_MARGE_X + i * (largeur_utile / (n - 1)) for i in range(n)]

    gares_sur_trajet = set(GARES_LIGNE_ORDRE)
    ligne_trajet = None
    connecteur_avant = connecteur_apres = None
    legende = None

    if infos_trajet is not None:
        gares_sur_trajet_liste, ordre_reel, conn_avant, conn_apres = infos_trajet
        gares_sur_trajet = set(gares_sur_trajet_liste)
        i_debut = GARES_LIGNE_ORDRE.index(gares_sur_trajet_liste[0])
        i_fin = GARES_LIGNE_ORDRE.index(gares_sur_trajet_liste[-1])
        if i_fin > i_debut:
            ligne_trajet = {"x1": xs[i_debut], "x2": xs[i_fin]}
        connecteur_avant = _connecteur_frise(
            xs, conn_avant, entrant=True,
            vers_la_gauche=conn_avant is not None and conn_avant[0] == i_debut,
        )
        connecteur_apres = _connecteur_frise(
            xs, conn_apres, entrant=False,
            vers_la_gauche=conn_apres is not None and conn_apres[0] == i_debut,
        )
        segments = [format_gare(g) for g in ordre_reel]
        if conn_avant is not None:
            segments.insert(0, f"{format_gare(conn_avant[1])} (hors ligne)")
        if conn_apres is not None:
            segments.append(f"{format_gare(conn_apres[1])} (hors ligne)")
        legende = "Trajet : " + " → ".join(segments)

    gares = []
    for x, gare_nom in zip(xs, GARES_LIGNE_ORDRE):
        retard = moyennes.get(gare_nom)
        sur_trajet = gare_nom in gares_sur_trajet
        if pd.isna(retard):
            couleur, texte_retard = "#bbbbbb", ""
        elif retard >= SEUIL_RETARD_FORT:
            couleur, texte_retard = "#ef4444", f"{format_min_sans_zero(retard)} min"
        elif retard >= SEUIL_RETARD_MOYEN:
            couleur, texte_retard = "#f97316", f"{format_min_sans_zero(retard)} min"
        else:
            couleur, texte_retard = "#22c55e", f"{format_min_sans_zero(retard)} min"
        gares.append({
            "x": x, "nom": format_gare(gare_nom), "sur_trajet": sur_trajet,
            "rayon": 5 if sur_trajet else 3,
            "point_fill": couleur if sur_trajet else None,
            "texte_retard": texte_retard if sur_trajet else "",
        })

    nb_releves = int(df_avant_retard["retard_min"].count())
    tooltip = (
        "Retard moyen par relevé propre à chaque gare ≠ du « Retard moyen par relevé » "
        "affiché en haut, qui suit les filtres actifs, alors que cette frise "
        f"(calculée sur {nb_releves} relevés) reste toujours limitée aux 11 gares "
        "de la ligne et ignore « Limiter aux trains avec retard ». Point gris plein : "
        "aucune donnée pour cette gare sous les filtres actuels. Point creux (Suivi "
        "d'un train uniquement) : gare que le train sélectionné ne dessert pas du tout."
    )

    return {"frise": {
        "gares": gares,
        "ligne_x1": xs[0], "ligne_x2": xs[-1], "y_ligne": FRISE_Y_LIGNE,
        "ligne_estompee": infos_trajet is not None,
        "ligne_trajet": ligne_trajet,
        "connecteur_avant": connecteur_avant,
        "connecteur_apres": connecteur_apres,
        "legende": legende,
        "tooltip": tooltip,
    }}


def preparer_contexte_commun(request: Request, gare: str, train: str, sens: str):
    """Préfixe partagé par les quatre vues filtrées (Tableau/Graphique/
    Suivi d'un train/Par jour-heure) : chargement, préparation, filtres
    Gare/Train/Sens/Limiter-aux-gares-de-la-ligne, et les stats de l'entête
    (communes aux quatre vues, indépendantes de `vue`). Renvoie (contexte,
    df, df_avant_retard, df_filtre, alertes_df, alertes_actif) — les trois
    df valent None en cas d'erreur de chargement (voir contexte["erreur"]),
    alertes_df/alertes_actif restent valides même dans ce cas (source
    indépendante d'observations.csv — voir Travaux/Alertes, qui n'a pas
    besoin des quatre premiers)."""
    limiter_ligne = lire_checkbox(request, "limiter_ligne", True)
    limiter_retard = lire_checkbox(request, "limiter_retard", True)
    vue = request.query_params.get("vue") or "tableau"

    contexte = {
        "titre_app": TITRE_APP, "vue": vue,
        "gare": gare, "train": train, "sens": sens,
        "limiter_ligne": limiter_ligne, "limiter_retard": limiter_retard,
        "nb_gares_ligne": len(GARES_LIGNE),
        "erreur": None,
    }

    # Badge d'onglet (#onglets, base.html) : calculé avant le chargement
    # d'observations.csv ci-dessous, sur CHAQUE requête (peu importe la vue
    # active, la barre d'onglets fait partie de l'en-tête partagé) — reste
    # correct même si observations.csv est cassé.
    alertes_df = charger_alertes(LOCAL_ALERTES)
    alertes_actif, libelle_travaux = calculer_alertes_actives(alertes_df)
    contexte["libelle_travaux"] = libelle_travaux

    df, erreur = charger_observations()
    if erreur:
        contexte["erreur"] = erreur
        return contexte, None, None, None, alertes_df, alertes_actif

    df_avant_retard = filtrer_df(df, gare, train, sens, limiter_ligne)
    df_filtre = restreindre_aux_trains_en_retard(df_avant_retard) if limiter_retard else df_avant_retard
    stats_ratio = calculer_stats_bloc(df_avant_retard)

    # Porte self.summary_var (viewer.py:1459-1464) — nb de relevés APRÈS
    # filtres (df_filtre, comme le Tableau) sur le total collecté, avec la
    # date de début (premier poll_time) et l'horodatage du tout dernier
    # relevé reçu, peu importe les filtres. df n'est jamais trié
    # explicitement par poll_time : fiable seulement parce que le fichier
    # collecté est strictement en ajout (append-only), comme côté viewer.py.
    date_debut_collecte = format_poll_time(df["poll_time"].iloc[0]).split(" à ")[0]
    contexte["resume_collecte"] = (
        f"{len(df_filtre)} relevés (sur {len(df)} au total, depuis le "
        f"{date_debut_collecte}) — "
        f"dernier relevé : {format_poll_time(df['poll_time'].iloc[-1])}"
    )

    df_stats = df_filtre.dropna(subset=["retard_min"])
    stats = calculer_stats_bloc(df_stats) if not df_stats.empty else None
    pct_perturbe = 100 * stats_ratio["en_retard"] / stats_ratio["total"] if stats_ratio["total"] else None

    # Tooltips de la barre de stats générales (_stats.html) — portées de
    # _render_stats (viewer.py:1481-1536), icône ℹ commune à celle de la
    # frise (.icone-info, style.css). "Retard max" n'a pas d'équivalent
    # côté viewer.py (jamais eu de tooltip).
    if stats_ratio["total"]:
        # sans_date_trip_id : indispensable ici, pas juste par cohérence
        # avec ailleurs — le trip_id brut se termine par un suffixe de date
        # qui change chaque jour pour un même train réel, donc un .nunique()
        # direct compte (motifs de train × jours observés) et grossit sans
        # borne avec le temps, incomparable à len(variantes) (indexé
        # sans cette date) — bug réel trouvé et corrigé ici (et dans
        # viewer.py, qui avait le même calcul), 2026-08-10 : affichait
        # 562/562 alors que le vrai chiffre est 478/562.
        nb_trains_observes = df_avant_retard["trip_id"].map(sans_date_trip_id).nunique()
        tooltip_ratio_retard = (
            f"{stats_ratio['en_retard']} circulations perturbées (retard à un moment de "
            f"leur trajet, même rattrapé ensuite) sur {stats_ratio['total']} déjà observées "
            f"depuis le début de la collecte (issues de {nb_trains_observes} trains "
            f"différents parmi les {len(reference_donnees['variantes'])} du référentiel), "
            f"soit {100 * stats_ratio['en_retard'] / stats_ratio['total']:.0f} %."
        )
    else:
        tooltip_ratio_retard = ""

    if stats is not None:
        nb_releves = int(df_stats["retard_min"].count())
        tooltip_moyen = (
            f"Moyenne brute sur les {nb_releves} relevés issus des filtres actifs "
            "ci-dessus, pas seulement sur les 300 dernières lignes affichées dans le "
            "tableau — un même passage réel est vu à plusieurs relevés tant qu'il reste "
            "dans la fenêtre du flux temps réel, d'où une moyenne « par relevé » très "
            "diluée par rapport au retard cumulé réel."
        )
        tooltip_pire_gare = (
            f"Gare avec le retard moyen / relevé le plus élevé, sur les {nb_releves} "
            "relevés issus des filtres actifs ci-dessus (pas seulement les 300 dernières "
            "lignes affichées dans le tableau)."
        )
        # "Jours cumulés perdus" : reformulation volontairement parlante du
        # même chiffre que stats.heures/minutes, pour illustrer concrètement
        # l'intérêt réel de cette stat (communication/ampleur), pas une
        # analyse fine — repéré par l'utilisateur, 2026-08-10 : la valeur
        # brute en heures ne dit rien seule tant qu'on ne sait pas depuis
        # quand elle cumule ni ce qu'elle représente à l'échelle.
        jours_cumules, heures_restantes = divmod(stats["heures"], 24)
        tooltip_cumule = (
            "Additionne le dernier retard connu pour chaque passage impacté (un train "
            f"à une gare précise), depuis le tout début de la collecte, le "
            f"{date_debut_collecte}. Son intérêt est surtout de donner une idée de l'ampleur du "
            f"volume total de retard généré par la ligne sur toute cette période "
            f"(soit environ {jours_cumules} jours et {heures_restantes} h cumulés)."
        )
    else:
        tooltip_moyen = tooltip_pire_gare = tooltip_cumule = ""

    contexte.update({
        "gare_options": options_gare(df),
        "train_options": ["Tous"] + sorted(df["train"].dropna().unique().tolist()),
        "sens_options": ["Tous"] + sorted(v for v in df["sens"].dropna().unique() if v),
        "stats_ratio": stats_ratio,
        "pct_perturbe": pct_perturbe,
        "stats": stats,
        "format_min_sans_zero": format_min_sans_zero,
        "tooltip_ratio_retard": tooltip_ratio_retard,
        "tooltip_moyen": tooltip_moyen,
        "tooltip_pire_gare": tooltip_pire_gare,
        "tooltip_cumule": tooltip_cumule,
    })
    return contexte, df, df_avant_retard, df_filtre, alertes_df, alertes_actif


def construire_contexte(request: Request, gare: str, train: str, sens: str):
    contexte, df, df_avant_retard, df_filtre, alertes_df, alertes_actif = preparer_contexte_commun(request, gare, train, sens)
    if df_avant_retard is None:
        return contexte

    if contexte["vue"] == "graphique":
        periode = request.query_params.get("periode") or PERIODE_OPTIONS[0]
        contexte["periode"] = periode
        contexte["periode_options"] = PERIODE_OPTIONS
        contexte.update(calculer_contexte_graphique(df_avant_retard, periode, gare, train, sens))
    elif contexte["vue"] == "train":
        contexte.update(calculer_contexte_train(request, df, gare, train, sens))
    elif contexte["vue"] == "jour_heure":
        contexte.update(calculer_contexte_jour_heure(df_avant_retard))
    elif contexte["vue"] == "travaux":
        contexte.update(calculer_contexte_travaux(alertes_df, alertes_actif))
    elif contexte["vue"] == "gtfs":
        contexte.update(calculer_contexte_gtfs(request))
    else:
        contexte.update({
            "entetes": [ENTETES[c] for c in COLONNES],
            "lignes": construire_lignes_tableau(df_filtre),
        })

    # Frise (#pied-de-page, base.html) : comme le badge d'onglet, présente
    # sur CHAQUE requête indépendamment de `vue` (widget persistant sous
    # tous les onglets) — calculée ici (après la branche par vue, pas dans
    # preparer_contexte_commun) pour pouvoir accéder à contexte["trajet_choisi"]
    # (Suivi d'un train uniquement, absent/vide sur les autres onglets).
    contexte.update(calculer_contexte_frise(df_avant_retard, contexte["vue"], contexte.get("trajet_choisi")))
    return contexte


def _pct_en_retard(groupe):
    """Comme _render_chart (viewer.py) : proportion de circulations en
    retard à ce relevé précis (pas la même notion que "Limiter aux trains
    avec retard", qui regarde tout le trajet)."""
    total = groupe["circulation"].nunique()
    en_retard = groupe.loc[groupe["retard_min"] > 0, "circulation"].nunique()
    return 100 * en_retard / total if total else float("nan")


def serie_avec_trous(serie, unite, explication_max, explication_moyenne):
    """Équivalent JSON de tracer_serie_temporelle + marquer_maximum +
    marquer_moyenne (graphiques.py), pour un tracé Plotly côté navigateur
    au lieu d'un ax matplotlib. Même détection de trou (écart > 10 min) et
    même coupure de la ligne principale juste avant chaque reprise — un
    point `null` dans "points" est un vrai trou pour Plotly (pas une
    interpolation), les segments "trous" sont tracés séparément en
    pointillé gris côté JS."""
    serie = serie.sort_index()
    if serie.empty:
        return {"points": {"x": [], "y": []}, "trous": [], "max": None, "moyenne": None}

    ecarts = serie.index.to_series().diff()
    apres_trou = ecarts[ecarts > SEUIL_TROU].index

    trous = []
    for t in apres_trou:
        i = serie.index.get_loc(t)
        trous.append({
            "x": [serie.index[i - 1].isoformat(), serie.index[i].isoformat()],
            "y": [serie.iloc[i - 1], serie.iloc[i]],
        })

    coupures = pd.Series(float("nan"), index=apres_trou - pd.Timedelta(milliseconds=1))
    avec_coupures = pd.concat([serie, coupures]).sort_index()
    points = {
        "x": [t.isoformat() for t in avec_coupures.index],
        "y": [None if pd.isna(v) else round(float(v), 1) for v in avec_coupures.values],
    }

    serie_valide = serie.dropna()
    maximum, moyenne = None, None
    if not serie_valide.empty:
        t_max = serie_valide.idxmax()
        v_max = serie_valide.loc[t_max]
        if v_max > 0:
            t_max_local = pd.Timestamp(t_max).tz_convert(PARIS_TZ)
            maximum = {
                "x": t_max.isoformat(), "y": round(float(v_max), 1),
                "texte_repere": f"Max : {v_max:.1f}{unite}<br>{t_max_local.strftime('%d/%m %Hh%M')}",
                "texte_explication": mise_en_forme_hover(explication_max),
            }
        moyenne_valeur = serie_valide.mean()
        moyenne = {
            "valeur": round(float(moyenne_valeur), 1),
            "texte_repere": f"moy. {moyenne_valeur:.1f}{unite}",
            "texte_explication": mise_en_forme_hover(explication_moyenne),
        }

    return {"points": points, "trous": trous, "max": maximum, "moyenne": moyenne}


def calculer_contexte_graphique(df_avant_retard, periode, gare, train, sens):
    """Porte le bloc `with tab_graphique:` d'app_streamlit.py (période →
    plot_df, agrégation par relevé, calcul des deux séries) — basé sur
    df_avant_retard (jamais df_filtre) : "Limiter aux trains avec retard"
    biaiserait la moyenne vers le haut (même règle que la barre de stats du
    haut)."""
    plot_df = df_avant_retard.dropna(subset=["retard_min"]).copy()
    if not plot_df.empty:
        plot_df["poll_time"] = pd.to_datetime(plot_df["poll_time"])
        duree = DUREE_PAR_PERIODE.get(periode)
        if duree is not None:
            plot_df = plot_df[plot_df["poll_time"] >= plot_df["poll_time"].max() - duree]

    if plot_df.empty:
        return {"graphique_vide": True}

    moyenne_par_releve = plot_df.groupby("poll_time")["retard_min"].mean().sort_index()
    plot_df["circulation"] = cle_circulation(plot_df)
    pct_par_releve = plot_df.groupby("poll_time").apply(_pct_en_retard, include_groups=False).sort_index()

    explication_max_retard = (
        "Indique à quel moment la moyenne, tous trains confondus, a été la plus haute. "
        "C'est différent du \"Retard max\" affiché en haut de l'appli, qui est le pire "
        "retard d'un seul train à un instant donné, pas une moyenne."
    )
    explication_max_pct = "Indique le pic de trains simultanément en retard."
    explication_moyenne = (
        "Moyenne sur la période actuellement affichée — sert de repère pour juger si un "
        "point de la courbe est au-dessus ou en dessous de la tendance générale de cette période."
    )

    elements_filtres = []
    if _gare_est_filtree(gare):
        elements_filtres.append(f"Gare {gare}")
    if train and train != "Tous":
        elements_filtres.append(f"Train {train}")
    if sens and sens != "Tous":
        elements_filtres.append(sens)
    suffixe_filtres = f" — {' · '.join(elements_filtres)}" if elements_filtres else ""

    donnees = {
        "retard": serie_avec_trous(moyenne_par_releve, " min", explication_max_retard, explication_moyenne),
        "pct": serie_avec_trous(pct_par_releve, " %", explication_max_pct, explication_moyenne),
        "titre_haut": "Évolution du retard moyen dans le temps" + suffixe_filtres,
        "titre_bas": "Évolution de la proportion de trains en retard" + suffixe_filtres,
        "periode": periode,
    }
    donnees_json = json_pour_script(donnees)

    stats_periode = calculer_stats_bloc(plot_df)
    segments_periode = [
        f"{stats_periode['en_retard']}/{stats_periode['total']} circulations perturbées "
        f"({100 * stats_periode['en_retard'] / stats_periode['total']:.0f} %)",
        f"Retard cumulé : {stats_periode['heures']} h {stats_periode['minutes']:02d} min "
        f"({stats_periode['nb_passages_impactes']} passages impactés)",
        f"Retard moyen / relevé : {stats_periode['moyen']:.1f} min",
        f"Retard max : {stats_periode['retard_max_texte']}",
        f"Gare la + touchée : {stats_periode['pire_gare_texte']}",
    ]

    return {
        "graphique_vide": False,
        "donnees_json": donnees_json,
        "nb_releves": len(plot_df),
        "segments_periode": segments_periode,
    }


def _stats_par_categorie(df, colonne, ordre=None):
    """Porte _stats_par_categorie (viewer.py:1870-1883) : pour chaque
    valeur de `colonne`, retard moyen, nombre de relevés (n) et % de
    circulations (pas de relevés) en retard — base commune aux 6
    graphiques de l'onglet "Par jour / heure"."""
    groupes = df.groupby(colonne)
    moyenne = groupes["retard_min"].mean()
    n = groupes.size()
    # _pct_en_retard : même formule que le lambda utilisé ici avant cette
    # factorisation (audit du 2026-08-10) — signature déjà compatible
    # (reçoit directement un groupe), en plus protégée contre une division
    # par zéro (sans conséquence pratique ici, groupby ne produit jamais un
    # groupe réellement vide, mais plus sûr).
    pct = groupes.apply(_pct_en_retard, include_groups=False)
    stats = pd.DataFrame({"moyenne": moyenne, "n": n, "pct": pct})
    return stats.reindex(ordre) if ordre is not None else stats


def _construire_barre(stats, colonne_valeur, labels, unite, avec_moyenne):
    """Porte _tracer_barres_fiabilite (viewer.py:1885-1927), version JSON :
    une entrée par catégorie (valeur, n, fiable), plus un repère "moy."
    optionnel — le tracé (couleurs/hachures/annotation) est fait côté JS
    (jour_heure.js), comme serie_avec_trous le fait déjà pour Graphique."""
    ns = stats["n"].fillna(0)
    barres = [
        {"label": label, "valeur": None if pd.isna(v) else round(float(v), 1),
         "n": int(n), "fiable": bool(n >= SEUIL_FIABLE)}
        for label, v, n in zip(labels, stats[colonne_valeur], ns)
    ]
    donnees = {"barres": barres, "unite": unite}
    if avec_moyenne:
        valides = stats[colonne_valeur].dropna()
        if not valides.empty:
            moyenne_valeur = valides.mean()
            donnees["moyenne"] = {
                "valeur": round(float(moyenne_valeur), 1),
                "texte": f"moy. {moyenne_valeur:.1f}{unite}",
            }
    return donnees


def calculer_contexte_jour_heure(df_avant_retard):
    """Porte _render_jour_heure_tab (viewer.py:1929-1999) — basé sur
    df_avant_retard, jamais df_filtre (même règle que
    calculer_contexte_graphique, commentaire juste au-dessus) : "Limiter
    aux trains avec retard" biaiserait les moyennes vers le haut."""
    plot_df = df_avant_retard.dropna(subset=["retard_min"]).copy()
    if plot_df.empty:
        return {"jour_heure_vide": True}
    plot_df["circulation"] = cle_circulation(plot_df)

    jours_ordre = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    plot_df["jour_semaine"] = pd.to_datetime(
        plot_df["start_date"], format="%Y%m%d"
    ).dt.dayofweek.map(dict(enumerate(jours_ordre)))
    stats_jour = _stats_par_categorie(plot_df, "jour_semaine", jours_ordre)
    labels_jour = [j[:3] for j in jours_ordre]

    plot_df["heure"] = pd.to_datetime(plot_df["poll_time"]).dt.tz_convert(PARIS_TZ).dt.hour
    stats_heure = _stats_par_categorie(plot_df, "heure", list(range(24)))
    labels_heure = [f"{h}h" for h in range(24)]

    # "ouvre" (sans accent) est une ancienne graphie du même statut que
    # "ouvré" — glitch d'encodage corrigé côté collecteur depuis, mais
    # présent dans les vieilles lignes (voir mémoire du projet).
    type_jour_map = {"ouvré": "Ouvré", "ouvre": "Ouvré", "weekend": "Weekend/Férié", "férié": "Weekend/Férié"}
    plot_df["type_jour_simple"] = plot_df["type_jour"].map(type_jour_map)
    ordre_type_jour = ["Ouvré", "Weekend/Férié"]
    stats_type_jour = _stats_par_categorie(
        plot_df.dropna(subset=["type_jour_simple"]), "type_jour_simple", ordre_type_jour,
    )

    # Comparaison par égalité (==), pas par identité (is True) : selon la
    # présence ou non de NaN dans la colonne, pandas peut charger ces
    # valeurs en bool Python natif ou en numpy.bool_, pour lesquels
    # numpy.bool_(True) is True vaut False (même piège que
    # format_bool_oui_non, voir l'audit de code du 2026-07-18).
    plot_df["vacances_texte"] = plot_df["vacances_scolaires"].apply(
        lambda v: "Vacances" if v == True else ("Hors vacances" if v == False else None)  # noqa: E712
    )
    ordre_vacances = ["Hors vacances", "Vacances"]
    stats_vacances = _stats_par_categorie(
        plot_df.dropna(subset=["vacances_texte"]), "vacances_texte", ordre_vacances,
    )

    donnees = {
        "jour_moyenne": _construire_barre(stats_jour, "moyenne", labels_jour, " min", True),
        "jour_pct": _construire_barre(stats_jour, "pct", labels_jour, " %", True),
        "heure_moyenne": _construire_barre(stats_heure, "moyenne", labels_heure, " min", True),
        "heure_pct": _construire_barre(stats_heure, "pct", labels_heure, " %", True),
        "type_jour": _construire_barre(stats_type_jour, "moyenne", ordre_type_jour, " min", False),
        "vacances": _construire_barre(stats_vacances, "moyenne", ordre_vacances, " min", False),
    }
    return {
        "jour_heure_vide": False,
        "donnees_json": json_pour_script(donnees),
    }


def calculer_contexte_travaux(alertes_df, alertes_actif):
    """Porte _render_travaux_tab (viewer.py: 673-724) : les deux tables de
    l'onglet Travaux / Alertes. alertes_df/alertes_actif proviennent déjà de
    preparer_contexte_commun (calcul du badge d'onglet) — pas rechargés ici,
    pour ne lire alertes.csv qu'une seule fois par requête."""
    n_actives = int(alertes_actif.sum())
    if alertes_df.empty:
        resume_travaux = "Aucune alerte connue pour l'instant."
    elif n_actives == 0:
        resume_travaux = f"Aucune alerte active en ce moment ({len(alertes_df)} archivée(s))."
    else:
        resume_travaux = f"⚠ {n_actives} alerte(s) active(s) en ce moment"

    ordre = alertes_df.assign(_actif=alertes_actif).sort_values(["_actif", "fin"], ascending=[False, True])
    lignes_alertes = []
    for _, ligne in ordre.iterrows():
        depuis = ligne["debut"].tz_convert(PARIS_TZ).strftime("%d/%m %Hh%M") if pd.notna(ligne["debut"]) else "-"
        jusqua = ligne["fin"].tz_convert(PARIS_TZ).strftime("%d/%m %Hh%M") if pd.notna(ligne["fin"]) else "-"
        lignes_alertes.append({
            "actif": bool(ligne["_actif"]), "gares": ligne["gares"],
            "depuis": depuis, "jusqua": jusqua,
            "texte": ligne["texte"], "description": ligne["description"] or ligne["texte"],
        })

    evenements_df = charger_evenements(PERTURBATIONS_FILE)
    lignes_evenements = []
    for _, ligne in evenements_df.sort_values("poll_time", ascending=False).iterrows():
        date_str = _format_start_date(ligne["start_date"])
        evenement = "Trajet annulé (entier)" if ligne["type"] == "trajet_annule" else f"Arrêt supprimé : {ligne['gare']}"
        lignes_evenements.append({
            "train": ligne["train"], "date": date_str, "evenement": evenement,
            "detecte": format_poll_time(ligne["poll_time"].isoformat()),
        })

    return {"resume_travaux": resume_travaux, "lignes_alertes": lignes_alertes, "lignes_evenements": lignes_evenements}


def _format_date_gtfs(valeur, avec_heure=False):
    """Porte _format_date_fr (onglet_verification_gtfs.py:35-47) :
    AAAA-MM-JJ (ou AAAA-MM-JJ HH:MM:SS) -> JJ/MM/AAAA (ou JJ/MM/AAAA à
    HH:MM). Réimplémentée ici plutôt qu'importée : nom préfixé "_" dans son
    module d'origine, qui n'est par ailleurs pas importé ici."""
    try:
        if avec_heure:
            return datetime.strptime(valeur, "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y à %H:%M")
        return datetime.strptime(valeur, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return valeur


TITRES_EXEMPLES_GTFS = (
    "Exemples de services modifiés :",
    "Exemples de services renommés (mêmes arrêts/horaires, identifiant changé) :",
    "Exemples de services disparus :",
    "Exemples de services nouveaux :",
)


def _reformater_horodatage_detail(texte, export=None, reference=None):
    """Reformate en JJ/MM/AAAA les 3 dates ISO du bloc brut d'une entrée
    verifier_gtfs.py (e["texte"]) : le préfixe "[AAAA-MM-JJ HH:MM:SS]", et
    (pour une entrée réussie, absentes sur un "Échec") "export du jour :
    AAAA-MM-JJ" et "référence datée du AAAA-MM-JJ" dans la phrase elle-même
    — repéré par l'utilisateur en 2 temps (2026-08-14), le préfixe d'abord,
    puis ces deux-là oubliées au premier passage. export/reference passés
    par l'appelant plutôt que re-régexés ici : déjà extraits et validés par
    charger_journal() (RE_RESUME), une substitution de chaîne exacte sur
    ces valeurs connues est plus sûre qu'une regex générique sur tout le
    bloc (qui pourrait aussi, en théorie, croiser une date dans un exemple
    de service listé plus bas). Cohérent avec le reste de l'appli (colonne
    Date, Référence datée du, Export SNCF du jour, déjà dans ce format) —
    verification_gtfs.log lui-même reste en ISO (format stable attendu par
    charger_journal(), voir son regex de découpage des blocs)."""
    texte = re.sub(
        r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]",
        lambda m: f"[{_format_date_gtfs(m.group(1), avec_heure=True)}]",
        texte,
    )
    if export:
        texte = texte.replace(f"export du jour : {export}", f"export du jour : {_format_date_gtfs(export)}")
    if reference:
        texte = texte.replace(
            f"référence datée du {reference}", f"référence datée du {_format_date_gtfs(reference)}",
        )
    return texte


def _surligner_exemples_gtfs(texte):
    """Entoure d'un <span> les lignes-titres "Exemples de services ..." du
    bloc brut d'une entrée verifier_gtfs.py, pour les distinguer visuellement
    du reste (voir .gtfs-exemple-titre, style.css) — demande explicite de
    l'utilisateur, 2026-08-10. Échappement HTML fait ligne à ligne ici
    plutôt que de compter sur l'échappement automatique de Jinja, puisque
    le résultat est injecté tel quel via |safe pour pouvoir y mêler ce
    balisage."""
    lignes_html = []
    for ligne in texte.splitlines():
        echappee = html.escape(ligne)
        if ligne.strip() in TITRES_EXEMPLES_GTFS:
            lignes_html.append(f'<span class="gtfs-exemple-titre">{echappee}</span>')
        else:
            lignes_html.append(echappee)
    return "\n".join(lignes_html)


def calculer_contexte_gtfs(request):
    """Porte _render_verification_gtfs_tab (onglet_verification_gtfs.py:
    178-229) : historique des exécutions de verifier_gtfs.py (cron
    quotidien sur le Pi), en lecture seule — les 3 boutons d'action de
    viewer.py ("Lancer la vérification maintenant"/"Régénérer"/"Déployer
    vers le Pi"), dont le dernier écrit sur le Pi, restent volontairement
    hors de cette version web tant qu'il n'y a pas d'authentification
    (décision prise avec l'utilisateur, 2026-08-10)."""
    aggravations = lire_checkbox(request, "gtfs_aggravations", False)
    entrees = charger_journal(GTFS_LOG_FILE)
    if aggravations:
        entrees = [e for e in entrees if e.get("aggrave") or "communs" not in e]

    lignes = []
    for e in reversed(entrees):
        date_affichee = _format_date_gtfs(e["horodatage"], avec_heure=True)
        if "communs" not in e:
            lignes.append({
                "date": date_affichee, "reference": "-", "export": "-",
                "communs": "-", "identiques": "-", "modifies": "-",
                "disparus": "-", "nouveaux": "-", "renommes": "-", "statut": "Échec",
                "css": "gtfs-echec", "detail": _surligner_exemples_gtfs(_reformater_horodatage_detail(e["texte"])),
            })
            continue
        lignes.append({
            "date": date_affichee,
            "reference": _format_date_gtfs(e["reference"]),
            "export": _format_date_gtfs(e["export"]),
            "communs": e["communs"], "identiques": e["identiques"],
            "modifies": e["modifies"], "disparus": e["disparus"],
            "nouveaux": e["nouveaux"], "renommes": e["renommes"],
            "statut": "⚠ Aggravation" if e["aggrave"] else "Stable",
            "css": "gtfs-aggrave" if e["aggrave"] else "",
            "detail": _surligner_exemples_gtfs(
                _reformater_horodatage_detail(e["texte"], export=e["export"], reference=e["reference"]),
            ),
        })

    if lignes:
        message_vide = None
    elif aggravations:
        message_vide = "Aucune aggravation dans l'historique — décoche le filtre pour voir toutes les vérifications."
    else:
        message_vide = "Aucune vérification enregistrée pour l'instant — attends le prochain passage cron (3h15 sur le Pi)."

    return {"lignes_gtfs": lignes, "gtfs_aggravations": aggravations, "gtfs_vide_message": message_vide}


def couleur_degradee(t, depart=(0x2c, 0x6e, 0xa5), arrivee=(0xf2, 0xa5, 0x3d)):
    """Dégradé linéaire bleu → orange (mêmes couleurs que TRAJET_COLORMAP,
    viewer.py:79-81 — "ancien -> récent" pour la vue Détail de Suivi d'un
    train), réimplémenté en RGB pur plutôt que d'importer
    matplotlib.colors dans app_fastapi.py juste pour cette interpolation.
    t entre 0 et 1."""
    r = round(depart[0] + (arrivee[0] - depart[0]) * t)
    g = round(depart[1] + (arrivee[1] - depart[1]) * t)
    b = round(depart[2] + (arrivee[2] - depart[2]) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def segments_par_etat(points):
    """points : liste de (x, y, fige) triés par x croissant. Regroupe les
    segments consécutifs (entre deux points) en "runs" de même état — un
    segment est "figé" seulement si ses DEUX extrémités le sont (comme
    `f1 and f2` dans _render_train_escalier/_render_train_detail,
    viewer.py). Le point de jonction est partagé entre deux runs
    consécutifs pour que le tracé reste continu. Un run devient une seule
    trace Plotly côté JS (voir static/train.js) — évite de dessiner un
    segment matplotlib à la fois comme le fait la version Tkinter, puisque
    `line.dash` s'applique par trace entière, pas par segment."""
    if len(points) < 2:
        return []
    runs = []
    run_courant = [points[0]]
    etat_courant = points[0][2] and points[1][2]
    for i in range(len(points) - 1):
        p1, p2 = points[i], points[i + 1]
        etat = p1[2] and p2[2]
        if etat != etat_courant:
            runs.append({"points": run_courant, "fige": etat_courant})
            run_courant = [p1]
            etat_courant = etat
        run_courant.append(p2)
    runs.append({"points": run_courant, "fige": etat_courant})
    return runs


def _runs_json(points):
    return [
        {"x": [p[0] for p in r["points"]], "y": [p[1] for p in r["points"]], "fige": r["fige"]}
        for r in segments_par_etat(points)
    ]


def construire_options_trajet(df_pour_trajets, df_complet, filtre_jour_retard):
    """Porte _update_trajet_list (viewer.py:1089-1150). df_pour_trajets :
    Gare/Train/Sens seulement, SANS "Limiter aux gares de la ligne" (voir
    filtrer_df(..., appliquer_limite_ligne=False), même règle que
    _filtered_df_pour_trajets) — établit quelles circulations proposer.
    df_complet : non filtré du tout — sert à calculer le vrai retard max de
    chaque circulation, y compris sur une gare hors ligne exclue de
    df_pour_trajets. Renvoie une liste de (valeur, libellé), valeur =
    "trip_id|start_date" (aucun des deux ne contient jamais "|")."""
    infos = df_pour_trajets.groupby(["trip_id", "start_date"]).agg(train=("train", "first")).reset_index()
    retard_max_reel = derniers_par_passage(df_complet).groupby(
        level=["trip_id", "start_date"]
    ).max().rename("retard_max")
    infos = infos.merge(retard_max_reel, on=["trip_id", "start_date"], how="left")

    if filtre_jour_retard:
        aujourdhui = datetime.now(PARIS_TZ).strftime("%Y%m%d")
        infos = infos[(infos["start_date"].astype(str) == aujourdhui) & (infos["retard_max"] > 0)]

    ordre = infos.sort_values(["retard_max", "start_date"], ascending=[False, False])
    options = []
    for _, row in ordre.iterrows():
        trip_id, start_date = row["trip_id"], row["start_date"]
        date_str = _format_start_date(start_date)
        label = f"{row['train']} du {date_str} (retard max {row['retard_max']:.0f} min)"
        options.append((f"{trip_id}|{start_date}", label))
    return options


def calculer_escalier(trajet, ordre_gares, position_gare, start_date, horaires_par_gare):
    """Porte _render_train_escalier (viewer.py:2113-2158) : dernière valeur
    connue par gare, jugée "figée" (déjà passée) ou non par rapport au tout
    dernier relevé du trajet."""
    dernier_poll_trajet = pd.to_datetime(trajet["poll_time"]).max()
    dernieres = trajet.sort_values("poll_time").groupby("gare").last()

    points = []
    for gare in ordre_gares:
        if gare not in dernieres.index:
            continue
        retard = dernieres.loc[gare, "retard_min"]
        if pd.isna(retard):
            continue
        passage_estime = estimer_passage_reel(horaires_par_gare.get(gare), start_date, retard)
        fige = passage_estime is not None and passage_estime <= dernier_poll_trajet
        points.append((position_gare[gare], round(float(retard), 1), bool(fige)))

    return {
        "points": [{"x": x, "y": y, "fige": f} for x, y, f in points],
        "runs": _runs_json(points),
    }


def calculer_detail(trajet, ordre_gares, position_gare, start_date, horaires_par_gare):
    """Porte _render_train_detail (viewer.py:2160-2221) : une ligne par
    relevé retenu (dédoublonné par signature — ignore un relevé qui ne
    change rien par rapport au précédent, sauf le tout dernier), dégradé
    bleu → orange ancien → récent (couleur_degradee), style plein/pointillé
    par segment évalué par rapport à l'heure de CE relevé précis (pas le
    dernier du trajet, contrairement à l'Escalier)."""
    tous_les_polls = sorted(trajet["poll_time"].unique())
    polls, signature_precedente = [], None
    for poll_time in tous_les_polls:
        snapshot_poll = trajet[trajet["poll_time"] == poll_time].sort_values(
            by="gare", key=lambda s: s.map(position_gare)
        )
        signature = tuple(snapshot_poll["retard_min"].fillna(-1))
        if signature != signature_precedente or poll_time == tous_les_polls[-1]:
            polls.append(poll_time)
            signature_precedente = signature

    n = len(polls)
    releves = []
    for i, poll_time in enumerate(polls):
        snapshot = trajet[trajet["poll_time"] == poll_time].copy()
        snapshot["position"] = snapshot["gare"].map(position_gare)
        snapshot = snapshot.dropna(subset=["position"]).sort_values("position")
        poll_dt = pd.Timestamp(poll_time)

        points = []
        for _, row in snapshot.iterrows():
            h = estimer_passage_reel(horaires_par_gare.get(row["gare"]), start_date, row["retard_min"])
            fige = h is not None and h <= poll_dt
            points.append((row["position"], round(float(row["retard_min"]), 1), bool(fige)))

        releves.append({
            "poll_time": format_poll_time(poll_time),
            "couleur": couleur_degradee(i / max(n - 1, 1)),
            "runs": _runs_json(points),
        })

    return {"releves": releves}


def calculer_contexte_train(request: Request, df, gare, train, sens):
    """Porte _render_train_tab (viewer.py:2011-2111). df : préparé, non
    filtré du tout — la liste de trajets proposée se base volontairement
    sur Gare/Train/Sens sans "Limiter aux gares de la ligne"
    (construire_options_trajet), pas sur df_avant_retard/df_filtre."""
    filtre_jour_retard = lire_checkbox(request, "filtre_jour_retard", True)
    df_pour_trajets = filtrer_df(df, gare, train, sens, appliquer_limite_ligne=False)
    options_trajet = construire_options_trajet(df_pour_trajets, df, filtre_jour_retard)

    trajet_demande = request.query_params.get("trajet") or ""
    valeurs_valides = {valeur for valeur, _ in options_trajet}
    trajet_choisi = trajet_demande if trajet_demande in valeurs_valides else (
        options_trajet[0][0] if options_trajet else ""
    )
    vue_train = request.query_params.get("vue_train") or "Escalier"
    # Porte l'aide contextuelle de _render_train_tab (viewer.py:2078-2090),
    # différente selon la vue sélectionnée.
    if vue_train == "Escalier":
        aide_vue_train = (
            "Une seule ligne : dernière valeur connue par gare. Trait plein = gare déjà passée "
            "(figé) — pointillé = pas encore atteinte (peut encore changer), ou jamais confirmée "
            "si le train est sorti du flux avant son heure d'arrivée annoncée (plus fréquent en "
            "cas de fort retard)."
        )
    else:
        aide_vue_train = (
            "Chaque ligne = un relevé (bleu = ancien, orange = récent) — montre l'historique "
            "complet des révisions de prévision, gare par gare. Cliquer un relevé dans la "
            "légende l'affiche/le masque."
        )

    contexte = {
        "filtre_jour_retard": filtre_jour_retard,
        "options_trajet": options_trajet,
        "trajet_choisi": trajet_choisi,
        "vue_train": vue_train,
        "vue_train_options": ["Escalier", "Détail des relevés"],
        "aide_vue_train": aide_vue_train,
        "depart_arrivee": "",
        "erreur_trajet": None,
        "train_vide": not options_trajet,
    }
    if not trajet_choisi:
        return contexte

    trip_id, start_date = trajet_choisi.split("|")
    trajet = df[(df["trip_id"] == trip_id) & (df["start_date"].astype(str) == start_date)].copy()

    variante = choisir_variante(reference_donnees["variantes"], reference_donnees["calendrier"], trip_id, start_date)
    ordre_gares = variante["gares"] if variante else []
    if not ordre_gares:
        contexte["erreur_trajet"] = (
            "Trajet théorique introuvable dans le référentiel actuel — ce train a peut-être "
            "été retiré de la desserte SNCF depuis. Il reste néanmoins comptabilisé dans les "
            "statistiques (Tableau, Graphique...), qui ne dépendent pas du référentiel."
        )
        return contexte
    if trajet.empty:
        return contexte

    position_gare = {g: i for i, g in enumerate(ordre_gares)}
    horaires_bruts = variante["horaires"]
    arrets_bruts = variante["arrets"]
    horaires_par_gare = dict(zip(ordre_gares, horaires_bruts))
    horaires_affiches = [
        format_heure_avec_arret(h, start_date, a) for h, a in zip(horaires_bruts, arrets_bruts)
    ]

    if horaires_affiches and horaires_affiches[0] and horaires_affiches[-1]:
        duree = duree_theorique(horaires_bruts[0], horaires_bruts[-1], start_date)
        # <span> autour de l'icône seule (agrandie en CSS, .duree-icone) —
        # rendu via |safe côté template : gares/horaires viennent du
        # référentiel GTFS/de nos propres calculs, jamais d'une saisie
        # utilisateur, donc aucun risque d'y injecter du HTML.
        contexte["depart_arrivee"] = (
            (f'<span class="duree-icone">⏱</span> {duree}  —  ' if duree else "")
            + f"Départ {format_gare(ordre_gares[0])} à {horaires_affiches[0]}  →  "
            f"Arrivée {format_gare(ordre_gares[-1])} à {horaires_affiches[-1]}"
        )

    if vue_train == "Escalier":
        donnees_vue = calculer_escalier(trajet, ordre_gares, position_gare, start_date, horaires_par_gare)
    else:
        donnees_vue = calculer_detail(trajet, ordre_gares, position_gare, start_date, horaires_par_gare)

    labels = [
        f"{format_gare(g)}<br>{h}" if h else format_gare(g)
        for g, h in zip(ordre_gares, horaires_affiches + [""] * (len(ordre_gares) - len(horaires_affiches)))
    ]
    hors_ligne = [g not in GARES_LIGNE for g in ordre_gares]

    donnees = {
        "vue": vue_train,
        "labels": labels,
        "hors_ligne": hors_ligne,
        "titre": f"Évolution du retard gare par gare — {dict(options_trajet).get(trajet_choisi, '')}",
        **donnees_vue,
    }
    contexte["donnees_json"] = json_pour_script(donnees)
    return contexte


def construire_lignes_tableau(df_filtre):
    """Même construction que app_streamlit.py (300 relevés les plus
    récents, tri stable, ligne vide entre groupes (train, poll_time)
    différents, couleur calculée AVANT le formatage d'affichage)."""
    recent = df_filtre.tail(300).sort_values("poll_time", ascending=False, kind="stable").reset_index(drop=True)

    # .apply(axis=1) convertit les None renvoyés par couleur_ligne() en NaN
    # (float) dans la Series résultante dès qu'elle mélange des chaînes et
    # des None — vérifié en pratique (couleurs.dtype reste "object" mais
    # couleurs.iloc[i] est bien nan, pas None). Sans ce fillna, le nan
    # traversait tel quel jusqu'au template ({{ ligne.css_class or '' }} ne
    # le filtre pas : nan est "truthy" en Python), donnant class="nan" dans
    # le HTML — sans effet visuel (aucune règle CSS ne cible .nan), mais
    # incorrect (repéré en construisant un mockup, 2026-08-10).
    couleurs = recent.apply(couleur_ligne, axis=1).fillna("")
    groupes = list(zip(recent["train"], recent["poll_time"]))

    recent["poll_time"] = recent["poll_time"].map(format_poll_time)
    recent["gare"] = recent["gare"].map(format_gare)
    recent["retard_arrivee_min"] = recent["retard_arrivee_min"].map(format_retard)
    recent["retard_depart_min"] = recent["retard_depart_min"].map(format_retard)
    recent["temperature_c"] = recent["temperature_c"].map(format_valeur)
    recent["precipitation_mm"] = recent["precipitation_mm"].map(format_valeur)
    recent["wind_speed_kmh"] = recent["wind_speed_kmh"].map(format_valeur)
    recent["type_jour"] = recent["type_jour"].map(format_valeur)
    recent["vacances_scolaires"] = recent["vacances_scolaires"].map(format_bool_oui_non)
    recent["arrets_restants"] = recent["arrets_restants"].map(format_entier)

    table_brut = recent[COLONNES]

    lignes = []
    for i in range(len(table_brut)):
        if i > 0 and groupes[i] != groupes[i - 1]:
            lignes.append({"separateur": True})
        lignes.append({
            "separateur": False,
            "css_class": couleurs.iloc[i],
            "cellules": table_brut.iloc[i].tolist(),
        })
    return lignes


@app.get("/", response_class=HTMLResponse)
def index(request: Request, gare: str = "Toutes", train: str = "Tous", sens: str = "Tous"):
    contexte = construire_contexte(request, gare, train, sens)
    return templates.TemplateResponse(request, "base.html", contexte)


@app.get("/contenu", response_class=HTMLResponse)
def contenu(request: Request, gare: str = "Toutes", train: str = "Tous", sens: str = "Tous"):
    contexte = construire_contexte(request, gare, train, sens)
    return templates.TemplateResponse(request, "_contenu_reponse.html", contexte)


