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
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from formatting import (
    PARIS_TZ,
    build_stop_names,
    build_trip_data,
    calculer_periode,
    calculer_stats_bloc,
    choisir_variante,
    cle_circulation,
    construire_index_gare_heure,
    derniers_par_passage,
    derniers_par_passage_avec_date,
    destinations_disponibles_pour_gare,
    duree_theorique,
    estimer_passage_reel,
    format_bool_oui_non,
    format_entier,
    format_gare,
    format_heure_avec_arret,
    format_heure_reelle,
    format_min_sans_zero,
    format_numero_train,
    format_poll_time,
    format_retard,
    format_valeur,
    heures_disponibles_pour_trajet,
    load_calendrier,
    load_reference,
    resoudre_trains_pour_gare_heure,
    texte_categorie_maximale,
    texte_periode_rapport,
    titre_dynamique_jour_heure,
    trajet_origine_destination,
    trajet_sens,
)
from perturbations import charger_alertes, charger_evenements
from verifier_gtfs import charger_journal

OBSERVATIONS_DB = "observations.db"

# Copies à nom fixe des PDF (poussées par le Pi via envoyer_rapport_vps_pi.sh,
# écrasées à chaque nouvelle génération, pas d'historique ici) — pour le
# bouton de téléchargement de l'onglet "Rapports" ; cette appli ne génère
# jamais elle-même de PDF (voir README, section Architecture).
RAPPORTS_PDF_DIR = "rapports"

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
# nb minimal de CIRCULATIONS DISTINCTES (pas de relevés bruts) pour
# considérer une barre fiable. Était à l'origine 30 relevés (viewer.py:1868)
# — abandonné, 2026-08-23 : un relevé n'est pas un train, et un train
# unique très en retard, repollé toutes les 5 min sur plusieurs gares,
# franchissait facilement ce seuil à lui seul (cas réel trouvé : heure_
# locale=1h, 2 circulations distinctes seulement sur tout l'historique,
# mais 152 relevés bruts — largement au-dessus de l'ancien seuil de 30,
# donnant une barre "fiable" reposant sur un seul incident). 10
# circulations distinctes, calibré sur observations.db réel : la catégorie
# la plus faible après les cas quasi-vides (heures 1h/2h, 1-2 circulations)
# en compte déjà 28 (3h) — 10 laisse une marge confortable sous ce palier
# tout en réduisant le poids qu'un train isolé peut avoir (décision
# utilisateur, 2026-08-23 : 5 jugé encore trop fragile statistiquement).
SEUIL_FIABLE = 10

# Carte "Train N" mobile (Accueil/Favoris) : nb max de retards listés dans
# "Retards des 90 derniers jours" avant de résumer le reste en "+N autres"
# — calibré sur observations.db réel (2026-08-24) : parmi les trains ayant
# eu au moins un retard sur 30 jours, médiane 3, 75e centile 5, seuls
# 16/237 dépassent 8 (max observé 14) — la grande majorité des trains
# n'est donc jamais tronquée.
MAX_RETARDS_AFFICHES_MOBILE = 8

# Borne basse "depuis toujours" pour calculer_stats_globales_sql_avec_cache
# (voir son docstring, 2026-08-24) : un simple repère antérieur à toute
# collecte possible, PAS la vraie date de début — inutile de la requêter
# (MIN(poll_time)), seule chose qui compte : n'exclure aucune ligne réelle.
BORNE_DEBUT_COLLECTE_ISO = "2000-01-01T00:00:00+00:00"

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
    # Index gare -> trains desservant cette gare (mobile, écran "Mon train"/
    # Favoris — résolution "gare + heure habituelle, tel jour" -> train) —
    # construit une seule fois ici, pas recalculé à chaque requête (voir
    # formatting.construire_index_gare_heure).
    reference_donnees["index_gare_heure"] = construire_index_gare_heure(
        reference_donnees["variantes"], reference_donnees["calendrier"],
    )
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _charger_observations_incremental(cache, fenetre_jours=None, calculer_stats=False):
    """Cœur de charger_observations() (fenêtre glissante) — utilisait
    aussi charger_observations_stats_globales() (cache complet paresseux)
    jusqu'au 2026-08-16, remplacé depuis par calculer_stats_globales_sql
    (barre de stats du haut passée en SQL direct, plus de second cache
    pandas séparé). `cache` est le dict à lire/écrire (clés "dernier_poll"/
    "df", plus "debut_collecte"/"total_lignes" si calculer_stats=True).
    `fenetre_jours` : purge les
    lignes plus anciennes après chaque ajout incrémental si fourni (fenêtre
    glissante) ; None = tout l'historique reste en cache. Renvoie un
    message d'erreur (ou None si tout s'est bien passé) — jamais le df
    directement, l'appelant le lit dans cache["df"].

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
        return f"Aucune donnée locale ({OBSERVATIONS_DB} introuvable)."

    # sqlite3.connect() crée silencieusement un fichier vide s'il n'existe
    # pas — d'où la vérification os.path.isfile ci-dessus, faite AVANT de
    # se connecter, pour ne jamais créer par erreur un observations.db vide
    # juste en le lisant.
    connexion = sqlite3.connect(OBSERVATIONS_DB)
    try:
        dernier_poll = connexion.execute("SELECT MAX(poll_time) FROM observations").fetchone()[0]
        if cache["dernier_poll"] == dernier_poll:
            return None
        try:
            # ORDER BY poll_time (pas rowid) : le reste du code (ex:
            # resume_collecte, plus bas) suppose df["poll_time"].iloc[0]/
            # iloc[-1] égal au premier/dernier relevé chronologique, comme le
            # garantissait l'ordre d'ajout du CSV — rowid le garantirait
            # aussi pour un simple append-only, mais plus une fois un
            # historique importé après coup (voir mémoire du projet,
            # migration Pi -> VPS 2026-08-13 : rowid ne correspondait alors
            # plus à l'ordre chronologique réel). Déjà indexé
            # (idx_observations_poll_time, collect_realtime.py).
            if cache["df"] is None:
                if calculer_stats:
                    # debut_collecte ne change jamais une fois fixé (collecte
                    # strictement en ajout) — calculé une seule fois ici,
                    # jamais recalculé ensuite, contrairement à total_lignes.
                    cache["debut_collecte"] = connexion.execute(
                        "SELECT MIN(poll_time) FROM observations",
                    ).fetchone()[0]
                if fenetre_jours is not None:
                    seuil_fenetre = (
                        pd.Timestamp(dernier_poll) - pd.Timedelta(days=fenetre_jours)
                    ).isoformat()
                    nouvelles = pd.read_sql_query(
                        "SELECT * FROM observations WHERE poll_time >= ? ORDER BY poll_time",
                        connexion, params=(seuil_fenetre,),
                    )
                else:
                    nouvelles = pd.read_sql_query(
                        "SELECT * FROM observations ORDER BY poll_time", connexion,
                    )
            else:
                nouvelles = pd.read_sql_query(
                    "SELECT * FROM observations WHERE poll_time > ? ORDER BY poll_time",
                    connexion, params=(cache["dernier_poll"],),
                )
            if calculer_stats:
                cache["total_lignes"] = connexion.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        except (sqlite3.DatabaseError, pd.errors.DatabaseError) as exc:
            return f"{OBSERVATIONS_DB} semble corrompu ({type(exc).__name__})."
        nouvelles = preparer_donnees(
            nouvelles, reference_donnees["stop_names"], reference_donnees["variantes"],
            reference_donnees["calendrier"],
        )
        if cache["df"] is None:
            cache["df"] = nouvelles
        else:
            fusion = pd.concat([cache["df"], nouvelles], ignore_index=True)
            if fenetre_jours is not None:
                seuil_purge = pd.Timestamp(dernier_poll) - pd.Timedelta(days=fenetre_jours)
                fusion = fusion[pd.to_datetime(fusion["poll_time"]) >= seuil_purge].reset_index(drop=True)
            cache["df"] = fusion
        cache["dernier_poll"] = dernier_poll
    finally:
        connexion.close()
    return None


# Cache mémoire du référentiel préparé (df + colonnes dérivées), invalidé
# sur MAX(poll_time) — voir _charger_observations_incremental.
# FENETRE_CACHE_JOURS : ce cache ne garde plus qu'une fenêtre glissante
# (7 jours, borne naturelle — couvre déjà "24h"/"3 derniers jours"/
# "7 derniers jours" du Graphique) plutôt que tout l'historique en
# permanence — mesuré à ~1 Go de RAM pour 932k lignes sans limite
# (2026-08-15, voir mémoire du projet), sur une VM à 3,8 Go. "Par
# jour/heure" (calculer_contexte_jour_heure_sql) et "tout l'historique" du
# Graphique (calculer_donnees_graphique_historique_sql) n'utilisent plus ce
# cache, justement parce qu'ils ont besoin de tout l'historique — c'est ce qui
# rend cette fenêtre sûre pour tout le reste.
_cache_observations = {"dernier_poll": None, "df": None, "debut_collecte": None, "total_lignes": None}
FENETRE_CACHE_JOURS = 7


def charger_observations():
    """observations.db (SQLite) est réécrit en continu par
    collect_realtime.py (toutes les ~5 min) : on veut refléter des données
    fraîches, mais pas payer le coût de la relecture + préparation complète
    à CHAQUE requête, y compris un simple changement d'onglet qui ne change
    rien aux données elles-mêmes (repéré par l'utilisateur, 2026-08-05, à
    l'époque du CSV — le raisonnement reste identique). Voir
    _charger_observations_incremental pour le détail du mécanisme.

    Renvoie (df, erreur, debut_collecte, total_lignes) : debut_collecte
    (MIN(poll_time)) et total_lignes (COUNT(*)) sont des requêtes bon marché
    sur toute la table (pas seulement la fenêtre en cache), nécessaires
    depuis que df lui-même ne couvre plus tout l'historique — resume_collecte
    (plus bas) affichait auparavant df["poll_time"].iloc[0]/len(df)
    directement, correct tant que df représentait tout l'historique."""
    erreur = _charger_observations_incremental(_cache_observations, FENETRE_CACHE_JOURS, calculer_stats=True)
    if erreur:
        return None, erreur, None, None
    # Copie : les appelants filtrent/dérivent à partir de ce df (filtrer_df,
    # construire_lignes_tableau...) — une copie évite tout risque qu'une
    # mutation accidentelle d'un appelant corrompe le cache partagé entre
    # requêtes.
    return (
        _cache_observations["df"].copy(), None,
        _cache_observations["debut_collecte"], _cache_observations["total_lignes"],
    )


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
        if variante is not None:
            heure_theo = variante["horaires_par_stop"].get(r["stop_id"])
            if heure_theo is not None:
                return format_heure_avec_arret(
                    heure_theo, r["start_date"], variante["arrets_par_stop"].get(r["stop_id"]),
                )
        # Repli : pas de correspondance théorique (stop_id StopArea:* sans
        # StopPoint:*, souvent un arrêt ajouté en temps réel — voir mémoire
        # du projet et formatting.format_heure_reelle) — utilise l'heure
        # réelle rapportée par le flux si elle a été captée, plutôt qu'une
        # case vide.
        return format_heure_reelle(r.get("arrival_time") or r.get("departure_time"))

    df["heure_theorique"] = df.apply(heure_theorique_ligne, axis=1)
    # "~" : seul format_heure_reelle (repli ci-dessus) le produit jamais —
    # sert de signal pour le badge "ARRÊT AJOUTÉ" (construire_lignes_tableau)
    # sans refaire la recherche de variante une 2e fois.
    df["arret_ajoute"] = df["heure_theorique"].str.startswith("~")
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
    return ["Toutes"] + _regrouper_gares_ligne(gares_vues)


def _regrouper_gares_ligne(gares):
    """Sépare `gares` en "— Gares de la ligne —" / "— Autres gares
    (jonction) —" (même convention que options_gare, desktop), chaque
    groupe trié alphabétiquement — factorisé pour être réutilisé à la fois
    par gares_options_mobile (départ) et la route /mobile/destinations_gare
    (arrivée), pour que les 2 sélecteurs de l'écran Accueil/Favoris mobile
    partagent le même regroupement visuel."""
    gares = sorted(gares)
    gares_de_la_ligne = [g for g in gares if g in GARES_LIGNE]
    gares_hors_ligne = [g for g in gares if g not in GARES_LIGNE]
    options = []
    if gares_de_la_ligne:
        options += ["— Gares de la ligne —"] + gares_de_la_ligne
    if gares_hors_ligne:
        options += ["— Autres gares (jonction) —"] + gares_hors_ligne
    return options


def gares_options_mobile(index_gare_heure):
    """Même regroupement que options_gare (desktop), pour le <select> 'Gare
    de départ' de l'écran Accueil/Favoris mobile — mais basé sur les gares
    ayant AU MOINS UN départ théorique connu (clés de index_gare_heure,
    référentiel GTFS) plutôt que sur les gares réellement observées dans
    observations.db (contrairement à options_gare) : la recherche mobile
    résout un trajet via le référentiel, donc c'est cette liste-là qui doit
    être exhaustive. Pas d'option "Toutes" ici (contrairement au filtre
    desktop) : la recherche mobile a besoin d'un point de départ précis."""
    return _regrouper_gares_ligne(index_gare_heure.keys())


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
    libelle = f"Perturbations ⚠ ({n_actives})" if n_actives else "Perturbations"
    return actif, libelle


def alertes_periode(alertes_df, debut_local, fin_local):
    """Alertes actives à un moment de [debut_local, fin_local) — porte le
    filtre de generer_rapport.py (variable alertes_periode dans generer()),
    même règle que calculer_alertes_actives mais bornée aux deux extrémités
    de la période plutôt qu'à l'instant présent (sans la borne du haut, une
    alerte démarrée après la fin de la période serait comptée à tort).
    alertes_df minuscule (perturbations.charger_alertes) : filtre pandas
    direct, pas de brique SQL nécessaire ici — hors discipline RAM du
    reste de l'onglet Rapports."""
    debut_utc = debut_local.tz_convert("UTC")
    fin_utc = fin_local.tz_convert("UTC")
    return alertes_df[
        (alertes_df["debut"].isna() | (alertes_df["debut"] <= fin_utc))
        & (alertes_df["fin"].isna() | (alertes_df["fin"] >= debut_utc))
    ]


def annulations_periode(evenements_df, debut_local, fin_local, variantes, calendrier):
    """Numéros des trains annulés (événement "trajet_annule", voir
    perturbations.detecter_evenements) détectées dans [debut_local,
    fin_local) ET dont le trajet théorique touche au moins une des 11 gares
    de la ligne — même définition "sur la ligne" que retard_max_ligne
    (_construire_donnees_top5_sql), pour rester cohérent avec le reste du
    rapport (Retard max, Top5, moyennes...) : sans ce filtre, une
    annulation sur une branche jamais reliée à l'axe (ex: un aller-retour
    Rennes) aurait compté de la même façon qu'une vraie perturbation de
    l'axe Paris-Cherbourg — repéré par l'utilisateur, 2026-08-20 (trains
    852610/853430, qui ne croisent la ligne qu'à Lison/Bayeux/Caen, sans
    jamais aller côté Cherbourg ni côté Paris — restent comptés malgré
    tout, cette règle n'exclut que les branches qui ne croisent la ligne
    nulle part).

    Un trajet annulé n'atteint jamais son terminus dans les données, donc
    circulation_est_arrivee (generer_rapport.py) l'exclut de toutes les
    stats de retard (Top5, moyennes...) : sans ce compteur à part, une
    annulation restait invisible dans le rapport alors que c'est la pire
    forme de perturbation possible — repéré par l'utilisateur sur le train
    851116, 2026-08-20. enregistrer_evenements (perturbations.py)
    dédoublonne déjà à l'écriture (une ligne par (type, trip_id,
    start_date)), pas besoin de re-dédoublonner ici.

    Renvoie la liste des numéros de train formatés (format_numero_train),
    triés — pas juste un compte : demande explicite de l'utilisateur,
    2026-08-20, pour voir directement QUELS trains plutôt qu'un chiffre nu."""
    debut_utc = debut_local.tz_convert("UTC")
    fin_utc = fin_local.tz_convert("UTC")
    annules = evenements_df[
        (evenements_df["type"] == "trajet_annule")
        & (evenements_df["poll_time"] >= debut_utc)
        & (evenements_df["poll_time"] < fin_utc)
    ]

    def touche_la_ligne(ligne):
        variante = choisir_variante(variantes, calendrier, ligne["trip_id"], str(ligne["start_date"]))
        return variante is not None and any(g in GARES_LIGNE for g in variante["gares"])

    # Garde explicite sur .empty avant .apply(axis=1) : sur un DataFrame
    # filtré à 0 ligne, .apply(axis=1) renvoie parfois un DataFrame (pas une
    # Series) selon la version de pandas installée, ce qui casse tout
    # calcul en aval sur son résultat — confirmé en pratique sur
    # generer_rapport.py (pandas 3.0.5 sur le Pi, pas 3.0.3 en local),
    # 2026-08-20.
    if annules.empty:
        return []
    annules = annules[annules.apply(touche_la_ligne, axis=1)]
    return sorted({format_numero_train(t) for t in annules["train"]})


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
        "affiché en haut, qui suit les filtres actifs sur tout l'historique, alors que "
        f"cette frise (calculée sur {nb_releves} relevés, limités aux 7 derniers jours) "
        "reste toujours limitée aux 11 gares de la ligne et ignore « Limiter aux trains "
        "avec retard ». Gris plein : aucune donnée pour cette gare sous les filtres "
        "actuels. Contour grisé : gare que le train sélectionné ne dessert pas du tout."
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
    indépendante d'observations.csv — voir Perturbations, qui n'a pas
    besoin des quatre premiers)."""
    limiter_ligne = lire_checkbox(request, "limiter_ligne", True)
    # False par défaut (décoché) — cochée, cette case donne une vue plus
    # étroite du trafic (seulement les circulations ayant eu du retard),
    # pas une vue d'ensemble du trafic réel — demande explicite de
    # l'utilisateur, 2026-08-19.
    limiter_retard = lire_checkbox(request, "limiter_retard", False)
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

    df, erreur, debut_collecte, total_lignes = charger_observations()
    if erreur:
        contexte["erreur"] = erreur
        return contexte, None, None, None, alertes_df, alertes_actif

    df_avant_retard = filtrer_df(df, gare, train, sens, limiter_ligne)
    df_filtre = restreindre_aux_trains_en_retard(df_avant_retard) if limiter_retard else df_avant_retard

    contexte.update({
        "gare_options": options_gare(df),
        "train_options": [("Tous", "Tous")] + [
            (t, format_numero_train(t)) for t in sorted(df["train"].dropna().unique().tolist())
        ],
        "sens_options": ["Tous"] + sorted(v for v in df["sens"].dropna().unique() if v),
    })

    # Barre de stats du haut (Retard cumulé/max/Gare la + touchée...) :
    # affiche depuis toujours le total depuis le tout début de la collecte,
    # pas seulement la fenêtre glissante ci-dessus (df) — calculée en SQL sur
    # observations.db (voir calculer_stats_globales_sql, Phase 2 du bornage
    # RAM, 2026-08-16), plus de cache complet séparé ni de bouton
    # d'activation. Calcul intrinsèquement coûteux sur ~950k lignes (mesuré :
    # 1-4s sans filtre Gare, le cas le plus fréquent — voir le docstring de
    # calculer_stats_globales_sql) ; calculer_stats_globales_sql_avec_cache
    # mémorise le petit résultat déjà calculé pour éviter de repayer ce coût
    # à chaque changement d'onglet sur le même filtre (cache de résultats
    # borné, PAS de données brutes — voir son docstring).
    connexion_stats = sqlite3.connect(OBSERVATIONS_DB)
    try:
        contexte.update(
            calculer_stats_globales_sql_avec_cache(
                connexion_stats, gare, train, sens, limiter_ligne, limiter_retard,
            )
        )
    finally:
        connexion_stats.close()

    return contexte, df, df_avant_retard, df_filtre, alertes_df, alertes_actif


def construire_contexte(request: Request, gare: str, train: str, sens: str):
    contexte, df, df_avant_retard, df_filtre, alertes_df, alertes_actif = preparer_contexte_commun(request, gare, train, sens)
    if df_avant_retard is None:
        return contexte

    if contexte["vue"] == "graphique":
        periode = request.query_params.get("periode") or PERIODE_OPTIONS[0]
        contexte["periode"] = periode
        contexte["periode_options"] = PERIODE_OPTIONS
        limiter_ligne = lire_checkbox(request, "limiter_ligne", True)
        connexion = sqlite3.connect(OBSERVATIONS_DB)
        try:
            contexte.update(
                calculer_contexte_graphique(connexion, df_avant_retard, periode, gare, train, sens, limiter_ligne)
            )
        finally:
            connexion.close()
    elif contexte["vue"] == "train":
        contexte.update(calculer_contexte_train(request, df, gare, train, sens))
    elif contexte["vue"] == "jour_heure":
        limiter_ligne = lire_checkbox(request, "limiter_ligne", True)
        connexion = sqlite3.connect(OBSERVATIONS_DB)
        try:
            contexte.update(
                calculer_contexte_jour_heure_sql_avec_cache(connexion, gare, train, sens, limiter_ligne)
            )
        finally:
            connexion.close()
    elif contexte["vue"] == "travaux":
        contexte.update(calculer_contexte_travaux(alertes_df, alertes_actif))
    elif contexte["vue"] == "rapports":
        # Périmètre fixe (11 gares de la ligne, comme le PDF), pas de
        # filtre Gare/Train/Sens — gare/train/sens ci-dessus (issus de
        # preparer_contexte_commun) ne sont pas utilisés par cette branche.
        rapports_periode = request.query_params.get("rapports_periode") or "quotidien"
        contexte["rapports_periode"] = rapports_periode
        contexte["rapports_periode_options"] = ["quotidien", "hebdomadaire", "mensuel"]
        # Masque le bouton de téléchargement tant que le Pi n'a pas encore
        # poussé de copie pour cette période (ex: juste après un déploiement,
        # avant le premier cron) plutôt que de proposer un lien mort.
        contexte["rapport_pdf_disponible"] = os.path.isfile(
            os.path.join(RAPPORTS_PDF_DIR, f"{rapports_periode}.pdf")
        )
        connexion_rapport = sqlite3.connect(OBSERVATIONS_DB)
        try:
            contexte.update(calculer_contexte_rapport_pour_affichage(connexion_rapport, rapports_periode))
        finally:
            connexion_rapport.close()
    elif contexte["vue"] == "gtfs":
        contexte.update(calculer_contexte_gtfs(request))
    else:
        evenements_df = charger_evenements(PERTURBATIONS_FILE)
        annules = evenements_df[evenements_df["type"] == "trajet_annule"]
        circulations_annulees = set(zip(annules["trip_id"], annules["start_date"].astype(str)))
        # Arrêt supprimé : contrairement à un trajet annulé, les autres
        # gares de la même circulation continuent d'être suivies normalement
        # — le badge cible donc la (gare, trip_id, start_date) précise, pas
        # tout le train (voir _construire_lignes_tableau).
        supprimes = evenements_df[evenements_df["type"] == "arret_supprime"]
        arrets_supprimes = set(
            zip(supprimes["trip_id"], supprimes["start_date"].astype(str), supprimes["gare"])
        )
        contexte.update({
            "entetes": [ENTETES[c] for c in COLONNES],
            "lignes": construire_lignes_tableau(df_filtre, circulations_annulees, arrets_supprimes),
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


def calculer_contexte_graphique(connexion, df_avant_retard, periode, gare, train, sens, limiter_ligne):
    """Porte le bloc `with tab_graphique:` d'app_streamlit.py (période →
    plot_df, agrégation par relevé, calcul des deux séries) — basé sur
    df_avant_retard (jamais df_filtre) : "Limiter aux trains avec retard"
    biaiserait la moyenne vers le haut (même règle que la barre de stats du
    haut). "tout l'historique" est un cas à part, calculé entièrement en SQL
    (calculer_donnees_graphique_historique_sql) plutôt qu'en pandas sur
    df_avant_retard (qui ne couvre que quelques jours, voir
    _cache_observations) — remplace l'ancien chargement complet en pandas
    (charger_observations_completes, retiré : ~30s / pic RAM ~2,6 Go mesurés
    sur la VPS avant cette migration, 2026-08-24, sans plafond avec la
    croissance de la collecte). Les deux chemins convergent ensuite vers le
    même tail commun (_construire_reponse_graphique), pour ne jamais laisser
    leurs textes/tooltips diverger."""
    if periode == "tout l'historique":
        # Repli sur la fenêtre glissante (df_avant_retard, partielle mais
        # pas une erreur) UNIQUEMENT en cas d'échec SQL (ex: DB verrouillée)
        # — un total SQL à 0 est en revanche définitif (l'historique complet
        # a déjà été consulté, contrairement à un repli pandas qui ne
        # verrait qu'une fenêtre partielle et pourrait à tort y trouver des
        # circulations que le vrai historique complet exclut).
        try:
            resultat = calculer_donnees_graphique_historique_sql(connexion, gare, train, sens, limiter_ligne)
        except sqlite3.Error:
            resultat = False
        if resultat is None:
            return {"graphique_vide": True}
        if resultat is not False:
            moyenne_par_releve, pct_par_releve, nb_releves, stats_periode = resultat
            return _construire_reponse_graphique(
                moyenne_par_releve, pct_par_releve, nb_releves, stats_periode, periode, gare, train, sens,
            )

    plot_df = df_avant_retard.dropna(subset=["retard_min"]).copy()
    if not plot_df.empty:
        plot_df["poll_time"] = pd.to_datetime(plot_df["poll_time"])
        duree = DUREE_PAR_PERIODE.get(periode)
        if duree is not None:
            plot_df = plot_df[plot_df["poll_time"] >= plot_df["poll_time"].max() - duree]

    # Exclut les circulations avec un événement "trajet_annule" (voir
    # perturbations_detectees.csv) — le flux GTFS-RT peut publier une
    # prévision d'arrivée au terminus (avec un retard) quelques minutes
    # avant que l'annulation officielle ne soit propagée (train 851116,
    # 2026-08-20 : 45 min prévus au terminus, annulé 5 min plus tard) :
    # sans cette exclusion, cette circulation polluerait aussi bien la
    # courbe que Retard max/cumulé de ce bloc, alors qu'elle n'est jamais
    # réellement arrivée.
    if not plot_df.empty:
        annulations = charger_evenements(PERTURBATIONS_FILE)
        annulations = annulations[annulations["type"] == "trajet_annule"]
        circulations_annulees = set(zip(annulations["trip_id"], annulations["start_date"].astype(str)))
        if circulations_annulees:
            cles = pd.MultiIndex.from_frame(plot_df[["trip_id", "start_date"]].astype(str))
            plot_df = plot_df[~cles.isin(circulations_annulees)]

    # Exclut aussi les circulations pas encore confirmées arrivées (voir
    # _cles_circulations_arrivees_globales) : sans ça, une circulation
    # encore en cours de trajet apparaît avec son dernier retard connu,
    # potentiellement provisoire — même correctif que la barre de stats
    # globale (calculer_stats_globales_sql, depuis_debut_collecte), ajouté
    # ici après avoir repéré un écart de méthode en comparant les chiffres
    # desktop/mobile pour un même train (2026-08-24).
    if not plot_df.empty:
        cles_arrivees = _cles_circulations_arrivees_globales(connexion)
        cles = plot_df["trip_id"].astype(str) + "|" + plot_df["start_date"].astype(str)
        plot_df = plot_df[cles.isin(cles_arrivees)]

    if plot_df.empty:
        return {"graphique_vide": True}

    moyenne_par_releve = plot_df.groupby("poll_time")["retard_min"].mean().sort_index()
    plot_df["circulation"] = cle_circulation(plot_df)
    pct_par_releve = plot_df.groupby("poll_time").apply(_pct_en_retard, include_groups=False).sort_index()
    stats_periode = calculer_stats_bloc(plot_df)

    return _construire_reponse_graphique(
        moyenne_par_releve, pct_par_releve, len(plot_df), stats_periode, periode, gare, train, sens,
    )


def _construire_reponse_graphique(moyenne_par_releve, pct_par_releve, nb_releves, stats_periode, periode, gare, train, sens):
    """Assemble le contexte final de l'onglet Graphique (JSON Plotly +
    segments_periode) à partir des 2 séries temporelles déjà calculées
    (moyenne/pct par poll_time, pas forcément triées — serie_avec_trous
    trie déjà) et du dict stats_periode (mêmes clés que formatting.
    calculer_stats_bloc) — partagé par les 2 chemins de calcul de
    calculer_contexte_graphique (SQL pour "tout l'historique", pandas pour
    les 3 autres périodes), pour ne jamais laisser leurs textes/tooltips
    diverger."""
    explication_max_retard = (
        "Indique le pic de tous les relevés à cet instant (selon les filtres actifs). "
        "C'est différent du « Retard max » affiché juste au-dessus de ce graphique, qui est "
        "le plus grand retard d'un seul train à un instant donné, pas une moyenne."
    )
    explication_max_pct = (
        "Indique le pic en % de trains simultanément en retard, à cet instant précis. "
        "C'est différent des « circulations perturbées » affichées juste au-dessus de ce "
        "graphique, qui comptent, sur toute la période, le nombre de circulations ayant eu "
        "du retard à un moment de leur trajet, même rattrapé ensuite."
    )
    explication_moyenne = "Moyenne sur la période affichée, pour repérer si un point est au-dessus ou en dessous de la tendance."

    elements_filtres = []
    if _gare_est_filtree(gare):
        elements_filtres.append(f"Gare {gare}")
    if train and train != "Tous":
        elements_filtres.append(f"Train {format_numero_train(train)}")
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

    segments_periode = [
        f"{stats_periode['en_retard']}/{stats_periode['total']} circulations perturbées "
        f"({100 * stats_periode['en_retard'] / stats_periode['total']:.0f} %)",
        f"Retard cumulé : {stats_periode['heures']} h {stats_periode['minutes']:02d} min "
        f"({stats_periode['nb_passages_impactes']} passages impactés)",
        f"Retard moyen / relevé : {stats_periode['moyen']:.1f} min",
        f"Retard max : {stats_periode['retard_max_texte']}",
        f"{stats_periode['label_pire_gare']} : {stats_periode['pire_gare_texte']}",
    ]

    return {
        "graphique_vide": False,
        "donnees_json": donnees_json,
        "nb_releves": nb_releves,
        "segments_periode": segments_periode,
    }


def _construire_barre(stats, colonne_valeur, labels, unite, avec_moyenne):
    """Porte _tracer_barres_fiabilite (viewer.py:1885-1927), version JSON :
    une entrée par catégorie (valeur, n, n_circulations, fiable), plus un
    repère "moy." optionnel — le tracé (couleurs/hachures/annotation) est
    fait côté JS (jour_heure.js), comme serie_avec_trous le fait déjà pour
    Graphique. "fiable" est basé sur n_circulations (circulations
    distinctes), pas n (relevés bruts) — voir le commentaire de
    SEUIL_FIABLE ; n reste affiché tel quel (tooltip "X relevés"), la
    colonne stats["n_circulations"] doit exister (voir _stats_par_
    categorie_sql/_moyenne_retard_par_categorie_sql)."""
    ns = stats["n"].fillna(0)
    ns_circulations = stats["n_circulations"].fillna(0)
    barres = [
        {"label": label, "valeur": None if pd.isna(v) else round(float(v), 1),
         "n": int(n), "n_circulations": int(nc), "fiable": bool(nc >= SEUIL_FIABLE)}
        for label, v, n, nc in zip(labels, stats[colonne_valeur], ns, ns_circulations)
    ]
    # seuil_fiable transmis au JS (pas recopié en dur dans jour_heure.js) :
    # le tooltip d'une barre peu fiable cite ce nombre, il doit rester en
    # phase avec SEUIL_FIABLE sans édition manuelle des deux côtés.
    donnees = {"barres": barres, "unite": unite, "seuil_fiable": SEUIL_FIABLE}
    if avec_moyenne:
        valides = stats[colonne_valeur].dropna()
        if not valides.empty:
            moyenne_valeur = valides.mean()
            donnees["moyenne"] = {
                "valeur": round(float(moyenne_valeur), 1),
                "texte": f"moy. {moyenne_valeur:.1f}{unite}",
            }
    return donnees


JOURS_ORDRE = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
ORDRE_TYPE_JOUR = ["Ouvré", "Weekend/Férié"]
ORDRE_VACANCES = ["Hors vacances", "Vacances"]

# jour_semaine calculé en SQL directement en nom français (pas l'entier
# %w de SQLite, 0=dimanche) : évite tout remappage côté Python, s'aligne
# directement sur JOURS_ORDRE comme le fait déjà dt.dayofweek.map(...)
# côté pandas. substr(...) reformate start_date ("AAAAMMJJ") en
# "AAAA-MM-JJ" — strftime('%w', ...) exige ce format pour une vraie date
# calendaire, mais sans aucun souci de fuseau horaire/DST ici : start_date
# est une date pure, pas un horodatage (contrairement à poll_time — voir
# heure_locale, ajoutée à l'écriture par collect_realtime.py plutôt que
# recalculée en SQL, précisément pour éviter ce risque).
_EXPR_JOUR_SEMAINE = """
    CASE CAST(strftime('%w', substr(start_date,1,4) || '-' || substr(start_date,5,2)
                             || '-' || substr(start_date,7,2)) AS INTEGER)
        WHEN 1 THEN 'Lundi' WHEN 2 THEN 'Mardi' WHEN 3 THEN 'Mercredi'
        WHEN 4 THEN 'Jeudi' WHEN 5 THEN 'Vendredi' WHEN 6 THEN 'Samedi' WHEN 0 THEN 'Dimanche'
    END
"""
# "ouvre" (sans accent) : même glitch d'encodage historique que côté pandas
# (calculer_contexte_jour_heure, voir mémoire du projet) — tout ce qui ne
# correspond à aucun des 4 cas connus donne NULL, exclu ensuite comme le
# ferait dropna(subset=["type_jour_simple"]) côté pandas.
_EXPR_TYPE_JOUR = """
    CASE WHEN type_jour IN ('ouvré', 'ouvre') THEN 'Ouvré'
         WHEN type_jour IN ('weekend', 'férié') THEN 'Weekend/Férié'
    END
"""
_EXPR_VACANCES = "CASE WHEN vacances_scolaires = 1 THEN 'Vacances' WHEN vacances_scolaires = 0 THEN 'Hors vacances' END"


def _construire_where_sql(
    gare, train, sens, limiter_ligne, limiter_retard=False, exiger_retard_connu=True,
    debut_iso=None, fin_iso=None,
):
    """Équivalent SQL de filtrer_df() — mêmes règles exactes
    (_gare_est_filtree, GARES_LIGNE). debut_iso/fin_iso (par défaut None,
    tous les appels existants restent inchangés) : borne sur poll_time ET
    restreint aux circulations dont le terminus est déjà "arrivé" à la fin
    de la période (table temporaire `circulations_arrivees_periode(cle)`,
    que l'appelant DOIT avoir déjà matérialisée — même contrat que
    limiter_retard/circulations_retard ci-dessous). Les deux bornes sont
    toujours fournies ensemble (jamais l'une sans l'autre). Utilisé par
    Rapports (sa propre période) ET par la barre de stats globale
    (calculer_stats_globales_sql, depuis_debut_collecte=True, avec des
    bornes "depuis toujours -> maintenant" — voir BORNE_DEBUT_COLLECTE_ISO
    — pour bénéficier de la même vérification d'arrivée que Rapports/la
    carte de stats mobile, ajouté le 2026-08-24 après avoir repéré un écart
    de méthode en comparant les chiffres desktop/mobile pour un même
    train). exiger_retard_connu (par défaut True) :
    ajoute le dropna(subset=["retard_min"]) fait par la plupart des
    appelants pandas, MAIS PAS calculer_stats_globales (app_fastapi.py) pour
    stats_ratio/resume_collecte — ces deux-là opèrent sur df_avant_retard,
    jamais filtré sur retard_min non-null (bug réel trouvé en validant
    cette requête contre pandas, 2026-08-16 : total de circulations sous-
    compté, des circulations n'ayant jamais eu de retard connu exclues à
    tort). limiter_retard (seulement utilisé par la barre de stats
    globales) : équivalent SQL de restreindre_aux_trains_en_retard — ne
    garde que les circulations ayant eu AU MOINS UNE ligne en retard
    n'importe où dans ce même périmètre (PAS "dernière valeur connue",
    contrairement à Retard max/cumulé plus bas — une seule ligne positive
    suffit). Référence la table temporaire `circulations_retard(cle)` —
    l'appelant DOIT l'avoir déjà matérialisée via
    _materialiser_circulations_retard() avant tout appel avec
    limiter_retard=True (voir calculer_stats_globales_sql). Avant le
    2026-08-16, cette condition réévaluait une sous-requête complète (mêmes
    params dupliqués, "params + params") à chaque appel — mesuré sur
    observations.db réel : jusqu'à ~1s cumulés par barre de stats sur un
    filtre Gare, la même sous-requête étant recalculée 3 fois (Retard max/
    cumulé, Gare la + touchée, Retard moyen). Matérialiser une seule fois
    par requête HTTP ramène ce coût à une seule évaluation."""
    conditions = []
    if exiger_retard_connu:
        conditions.append("(arrival_delay_s IS NOT NULL OR departure_delay_s IS NOT NULL)")
    params = []
    if _gare_est_filtree(gare):
        conditions.append("gare = ?")
        params.append(gare)
    if train and train != "Tous":
        conditions.append("substr(trip_id, 1, instr(trip_id, ':') - 1) = ?")
        params.append(train)
    if sens and sens != "Tous":
        conditions.append("sens = ?")
        params.append(sens)
    if limiter_ligne:
        conditions.append(f"gare IN ({','.join('?' * len(GARES_LIGNE_ORDRE))})")
        params.extend(GARES_LIGNE_ORDRE)
    if limiter_retard:
        conditions.append("(trip_id || '|' || start_date) IN (SELECT cle FROM circulations_retard)")
    if debut_iso is not None:
        conditions.append("poll_time >= ? AND poll_time < ?")
        params.append(debut_iso)
        params.append(fin_iso)
        conditions.append(
            "(trip_id || '|' || start_date) IN (SELECT cle FROM circulations_arrivees_periode)"
        )
    return (" AND ".join(conditions) if conditions else "1=1"), params


def _materialiser_circulations_retard(
    connexion, gare, train, sens, limiter_ligne, debut_iso=None, fin_iso=None,
):
    """Construit la table temporaire `circulations_retard(cle)` — les
    circulations (trip_id|start_date) ayant eu AU MOINS UNE ligne en retard
    n'importe où dans le périmètre Gare/Train/Sens/Limiter (SANS
    limiter_retard lui-même, pour éviter la référence circulaire). À
    appeler une seule fois par requête HTTP quand limiter_retard=True,
    avant tout appel de _construire_where_sql(..., limiter_retard=True)
    (voir son docstring). Appelant responsable du DROP TABLE ensuite.
    debut_iso/fin_iso (Rapports uniquement) : si fournis, l'appelant DOIT
    avoir déjà matérialisé circulations_arrivees_periode (voir
    _construire_where_sql)."""
    where, params = _construire_where_sql(
        gare, train, sens, limiter_ligne, exiger_retard_connu=False,
        debut_iso=debut_iso, fin_iso=fin_iso,
    )
    connexion.execute(
        f"""
        CREATE TEMP TABLE circulations_retard AS
        SELECT DISTINCT trip_id || '|' || start_date AS cle FROM observations
        WHERE {where} AND COALESCE(arrival_delay_s, departure_delay_s) > 0
        """,
        params,
    )


def _materialiser_jour_heure(connexion, gare, train, sens, limiter_ligne):
    """Matérialise UNE fois (trip_id, start_date, retard_min + les 4
    catégorisations jour/heure/type_jour/vacances) dans une table
    temporaire — les 4 appels à _stats_par_categorie_sql (jour, heure,
    type_jour, vacances), qui portaient chacun sur le MÊME périmètre
    filtré (même gare/train/sens/limiter_ligne), scannaient chacun
    observations.db 2 fois (moyenne+n, puis pct) de façon indépendante,
    soit 8 scans complets de l'historique filtré pour cet onglet — mesuré
    2026-08-16 sur observations.db réel (sans filtre Gare, le cas par
    défaut) : ~9,8s au total. Un seul scan ici, puis les 8 agrégations
    portent sur cette petite table déjà en mémoire (même stratégie que
    _retard_max_et_cumule_sql/_pire_gare_et_moyenne_sql pour la barre de
    stats du haut). Appelant responsable du DROP TABLE ensuite."""
    where, params = _construire_where_sql(gare, train, sens, limiter_ligne)
    expr_retard = "ROUND(COALESCE(arrival_delay_s, departure_delay_s) / 60.0, 1)"
    connexion.execute(
        f"""
        CREATE TEMP TABLE jour_heure_filtre AS
        SELECT trip_id, start_date, {expr_retard} AS retard_min,
               {_EXPR_JOUR_SEMAINE} AS jour_semaine,
               heure_locale,
               {_EXPR_TYPE_JOUR} AS type_jour_simple,
               {_EXPR_VACANCES} AS vacances
        FROM observations WHERE {where}
        """,
        params,
    )


def _stats_par_categorie_sql(connexion, nom_colonne_categorie, ordre):
    """Équivalent SQL de _stats_par_categorie (moyenne/n/n_circulations/pct
    par catégorie) — interroge la table temporaire déjà matérialisée par
    _materialiser_jour_heure (voir son docstring) plutôt que observations
    directement. moyenne/n en une requête (moyenne simple par ligne, comme
    groupes["retard_min"].mean() + groupes.size()) ; pct/n_circulations en
    une seconde, sur le même regroupement à deux niveaux (par circulation
    distincte dans chaque catégorie, comme _pct_en_retard) : pct est la
    proportion de circulations avec au moins une ligne en retard,
    n_circulations leur nombre total — utilisé par _construire_barre pour
    la fiabilité (voir SEUIL_FIABLE), n (relevés bruts) resterait trompeur
    ici (un seul train très en retard, repollé sur plusieurs gares, peut
    à lui seul dépasser un seuil exprimé en relevés). ROUND(...,1) sans
    risque de divergence avec l'arrondi pandas (.round(1)) : les retards
    sont toujours des multiples exacts de 5 minutes (voir mémoire du
    projet), donc jamais de cas à mi-chemin où les conventions d'arrondi
    de SQLite et pandas pourraient diverger."""
    lignes_moyenne = connexion.execute(
        f"""
        SELECT {nom_colonne_categorie} AS categorie, AVG(retard_min) AS moyenne, COUNT(*) AS n
        FROM jour_heure_filtre WHERE {nom_colonne_categorie} IS NOT NULL
        GROUP BY categorie
        """,
    ).fetchall()
    lignes_pct = connexion.execute(
        f"""
        SELECT categorie, AVG(en_retard) * 100 AS pct, COUNT(*) AS n_circulations FROM (
            SELECT {nom_colonne_categorie} AS categorie, trip_id, start_date,
                   MAX(CASE WHEN retard_min > 0 THEN 1 ELSE 0 END) AS en_retard
            FROM jour_heure_filtre WHERE {nom_colonne_categorie} IS NOT NULL
            GROUP BY categorie, trip_id, start_date
        )
        GROUP BY categorie
        """,
    ).fetchall()

    stats_pct = {ligne[0]: ligne[1:] for ligne in lignes_pct}
    stats = pd.DataFrame(
        [
            {
                "categorie": cat, "moyenne": moyenne, "n": n,
                "pct": stats_pct.get(cat, (None, 0))[0],
                "n_circulations": stats_pct.get(cat, (None, 0))[1],
            }
            for cat, moyenne, n in lignes_moyenne
        ],
        columns=["categorie", "moyenne", "n", "pct", "n_circulations"],
    ).set_index("categorie")
    return stats.reindex(ordre) if ordre is not None else stats


def _cte_dernier_par_passage_complet(where):
    """Texte SQL (à préfixer devant une requête) de la CTE "dernier connu
    par passage réel" — équivalent SQL de formatting.derniers_par_passage :
    pour chaque (trip_id, start_date, gare), seule la ligne la plus récente
    (poll_time DESC) via ROW_NUMBER() (SQLite >= 3.25, largement présent).
    Base commune à Retard max et Retard cumulé (calculer_stats_bloc,
    formatting.py) — contrairement à Gare la + touchée, qui est une
    moyenne brute sur TOUTES les lignes, pas sur ce dernier-connu. Garde
    trip_id/start_date/gare/poll_time (pas juste retard_min) : nécessaire à
    _materialiser_circulations_arrivees_periode, qui doit retrouver la
    circulation et l'horodatage de chaque ligne — les appelants qui n'ont
    besoin que d'un sous-ensemble (ex: train, retard_min) le projettent
    eux-mêmes dans leur SELECT final plutôt que de dupliquer cette CTE avec
    une projection plus étroite (audit de nettoyage, 2026-08-17 : une
    version "_cte_dernier_par_passage" plus étroite existait ici, retirée)."""
    return f"""
        WITH filtre AS (
            SELECT trip_id, start_date, gare, poll_time,
                   ROUND(COALESCE(arrival_delay_s, departure_delay_s) / 60.0, 1) AS retard_min
            FROM observations WHERE {where}
        ),
        avec_rang AS (
            SELECT trip_id, start_date, gare, poll_time, retard_min,
                   ROW_NUMBER() OVER (
                       PARTITION BY trip_id, start_date, gare ORDER BY poll_time DESC
                   ) AS rn
            FROM filtre
        ),
        derniers AS (
            SELECT trip_id, start_date, gare, poll_time, retard_min FROM avec_rang WHERE rn = 1
        )
    """


def _materialiser_circulations_annulees(connexion):
    """Construit la table temporaire `circulations_annulees(cle)` — les
    circulations avec un événement "trajet_annule" (perturbations_
    detectees.csv), sans restriction de période (une circulation annulée
    l'est indépendamment de la fenêtre de stats calculée). Référencée à la
    fois par _materialiser_circulations_arrivees_periode (onglet Rapports)
    et _retard_max_et_cumule_sql (barre de stats globale, non bornée à une
    période) : dans les deux cas, le flux GTFS-RT peut publier une
    prévision d'arrivée au terminus (avec un retard) quelques minutes avant
    que l'annulation officielle ne soit propagée (train 851116, 2026-08-20 :
    45 min prévus au terminus, annulé 5 min plus tard) — sans cette
    exclusion, "dernier connu par passage" prendrait cette prévision pour
    un vrai retard final, alors que la circulation n'est jamais arrivée.
    Appelant responsable du DROP TABLE ensuite."""
    evenements = charger_evenements(PERTURBATIONS_FILE)
    annulations = evenements[evenements["type"] == "trajet_annule"]
    cles = (annulations["trip_id"] + "|" + annulations["start_date"].astype(str)).unique()
    connexion.execute("DROP TABLE IF EXISTS temp.circulations_annulees")
    connexion.execute("CREATE TEMP TABLE circulations_annulees (cle TEXT PRIMARY KEY)")
    connexion.executemany(
        "INSERT OR IGNORE INTO circulations_annulees (cle) VALUES (?)",
        [(cle,) for cle in cles],
    )


def _materialiser_circulations_arrivees_periode(connexion, debut_iso, fin_iso):
    """Construit la table temporaire `circulations_arrivees_periode(cle)`
    référencée par _construire_where_sql(debut_iso=..., fin_iso=...) —
    reproduit exactement generer_rapport.circulation_est_arrivee (même
    logique, même horloge de référence : comparaison au dernier poll_time
    CONNU DE LA CIRCULATION DANS LA PÉRIODE, jamais à l'heure actuelle,
    voir son docstring) plutôt que de la réimplémenter en SQL — le calcul
    dépend de choisir_variante/estimer_passage_reel, tous deux déjà en
    Python et non traduisibles en SQL pur (référentiel horaire + gestion
    des variantes selon la date).

    Toutes gares confondues (pas de limiter_ligne) : le terminus d'une
    circulation peut être hors des 11 gares de la ligne (ex: Coutances,
    Granville) et doit malgré tout être observé pour juger la circulation
    arrivée. Périmètre restreint à la période et aux lignes avec un retard
    connu (arrival_delay_s OU departure_delay_s non NULL), comme le
    dropna(subset=["retard_min"]) de filtrer_periode_arrivees.

    S'appuie sur _cte_dernier_par_passage_complet : pour chaque circulation,
    le dernier retard connu par gare DANS LA PÉRIODE suffit à reproduire
    à la fois `derniere` (dernière ligne connue au terminus) et
    `dernier_poll` (max(poll_time) de la circulation) de la version pandas
    — le max des derniers-par-gare est égal au max de toutes les lignes,
    puisque le regroupement par gare partitionne l'ensemble des lignes.

    Nombre de circulations distinctes en jeu : petit vis-à-vis de la RAM
    même sur un mois complet (voir le plan — jamais chargé en pandas sur
    tout l'historique, seulement joint à la table déjà matérialisée par
    la CTE ci-dessus). Appelant responsable du DROP TABLE ensuite.

    Exclut aussi toute circulation présente dans circulations_annulees (voir
    _materialiser_circulations_annulees, que l'appelant DOIT avoir déjà
    matérialisée), même si le calcul ci-dessous la considérerait arrivée."""
    where = "(arrival_delay_s IS NOT NULL OR departure_delay_s IS NOT NULL) AND poll_time >= ? AND poll_time < ?"
    params = [debut_iso, fin_iso]
    cte = _cte_dernier_par_passage_complet(where)
    connexion.execute("DROP TABLE IF EXISTS temp.derniers_complet_periode")
    connexion.execute(
        "CREATE TEMP TABLE derniers_complet_periode AS " + cte
        + "SELECT trip_id, start_date, gare, poll_time, retard_min FROM derniers "
        + "WHERE (trip_id || '|' || start_date) NOT IN (SELECT cle FROM circulations_annulees)",
        params,
    )
    lignes = connexion.execute(
        "SELECT trip_id, start_date, gare, poll_time, retard_min FROM derniers_complet_periode",
    ).fetchall()

    variantes = reference_donnees["variantes"]
    calendrier = reference_donnees["calendrier"]
    par_circulation = {}
    for trip_id, start_date, gare, poll_time, retard_min in lignes:
        par_circulation.setdefault((trip_id, start_date), []).append((gare, poll_time, retard_min))

    cles_arrivees = []
    for (trip_id, start_date), circ in par_circulation.items():
        variante = choisir_variante(variantes, calendrier, trip_id, start_date)
        if not variante:
            continue
        terminus = variante["gares"][-1]
        ligne_terminus = next((l for l in circ if l[0] == terminus), None)
        if ligne_terminus is None:
            continue
        dernier_poll = max(l[1] for l in circ)
        heure_terminus = dict(zip(variante["gares"], variante["horaires"])).get(terminus)
        passage_estime = estimer_passage_reel(heure_terminus, start_date, ligne_terminus[2])
        if passage_estime is not None and pd.Timestamp(passage_estime) <= pd.Timestamp(dernier_poll):
            cles_arrivees.append(f"{trip_id}|{start_date}")

    connexion.execute("DROP TABLE IF EXISTS temp.circulations_arrivees_periode")
    connexion.execute("CREATE TEMP TABLE circulations_arrivees_periode (cle TEXT PRIMARY KEY)")
    connexion.executemany(
        "INSERT INTO circulations_arrivees_periode (cle) VALUES (?)",
        [(cle,) for cle in cles_arrivees],
    )


# Cache mémoire des CLÉS "circulations confirmées arrivées" pour la barre de
# stats globale (BORNE_DEBUT_COLLECTE_ISO -> maintenant), invalidé sur
# nouveau relevé comme _cache_resultats_stats_globales — voir
# _cles_circulations_arrivees_globales.
_cache_circulations_arrivees_globales = {"dernier_poll": None, "cles": None}


def _cles_circulations_arrivees_globales(connexion):
    """Renvoie l'ensemble des clés "trip_id|start_date" confirmées arrivées
    (BORNE_DEBUT_COLLECTE_ISO -> maintenant), via un cache mémoire —
    _materialiser_circulations_arrivees_periode ne dépend d'AUCUN filtre
    Gare/Train/Sens (toujours les mêmes bornes pour la barre globale) : sans
    ce cache, chaque nouvelle combinaison de filtres visitée dans la même
    fenêtre de 5 min entre 2 relevés repayait sa boucle Python choisir_
    variante/estimer_passage_reel sur tout l'historique (~9-11s, 2026-08-
    24), alors que le résultat est rigoureusement identique d'un filtre à
    l'autre. Réutilisée par _obtenir_circulations_arrivees_globales (accès
    SQL, TEMP TABLE) ET par calculer_contexte_graphique (accès pandas,
    filtrage direct d'un DataFrame via .isin()) — un TEMP TABLE seul ne
    suffit pas à éviter ce recalcul (portée : une connexion SQLite, recréée
    à chaque requête HTTP), d'où ce cache mémoire des clés elles-mêmes
    (petites chaînes, quelques Ko)."""
    dernier_poll = connexion.execute("SELECT MAX(poll_time) FROM observations").fetchone()[0]
    cache = _cache_circulations_arrivees_globales
    if cache["dernier_poll"] != dernier_poll:
        fin_iso = pd.Timestamp.now(tz="UTC").isoformat()
        _materialiser_circulations_arrivees_periode(connexion, BORNE_DEBUT_COLLECTE_ISO, fin_iso)
        cache["cles"] = [r[0] for r in connexion.execute("SELECT cle FROM circulations_arrivees_periode").fetchall()]
        connexion.execute("DROP TABLE IF EXISTS temp.circulations_arrivees_periode")
        cache["dernier_poll"] = dernier_poll
    return set(cache["cles"])


def _obtenir_circulations_arrivees_globales(connexion):
    """(Re)matérialise le TEMP TABLE `circulations_arrivees_periode` pour
    la barre de stats globale (voir _construire_where_sql), à partir du
    cache mémoire des clés (_cles_circulations_arrivees_globales) plutôt
    que de recalculer. Appelant responsable du DROP TABLE ensuite, comme
    pour un appel direct à _materialiser_circulations_arrivees_periode."""
    cles = _cles_circulations_arrivees_globales(connexion)
    connexion.execute("DROP TABLE IF EXISTS temp.circulations_arrivees_periode")
    connexion.execute("CREATE TEMP TABLE circulations_arrivees_periode (cle TEXT PRIMARY KEY)")
    connexion.executemany(
        "INSERT INTO circulations_arrivees_periode (cle) VALUES (?)",
        [(cle,) for cle in cles],
    )


def calculer_carte_stats_train_sql(connexion, train, gare="Toutes", jours=90, limiter_ligne=False):
    """Carte 'Train N' (écrans Accueil/Favoris mobile), bornée aux `jours`
    derniers jours : % à l'heure, retard moyen/max, historique quotidien.
    Réutilise les mêmes briques que la barre de stats globale
    (_cte_dernier_par_passage_complet, _materialiser_circulations_annulees/
    _arrivees_periode) plutôt que dupliquer leur logique. train étant déjà
    fixé (contrairement à _retard_max_et_cumule_sql, qui GROUP BY train sur
    plusieurs trains à la fois), une requête dédiée plus légère suffit ici.

    limiter_ligne=False (pas True) : bug réel trouvé le 2026-08-24 (train
    852868 à Avranches introuvable sur mobile mais 549 relevés sur
    desktop) — `gare` est TOUJOURS une gare précise ici (jamais "Toutes" en
    usage réel, contrairement à Retard max/cumulé qui peut agréger
    plusieurs gares), donc limiter_ligne=True ajoutait "gare IN (11 gares
    de la ligne)" EN PLUS de "gare = <gare choisie>" — un ET impossible à
    satisfaire dès que la gare choisie est une gare de jonction (Avranches,
    Audrieu, Granville...), justement celles ajoutées aux sélecteurs mobile
    cette même session (gares_options_mobile). limiter_ligne=True n'a donc
    jamais de sens ici, quelle que soit la gare.

    jours=90 (pas 30) : vérifié sur observations.db réel (2026-08-24) qu'un
    échantillon à 30 jours est statistiquement fragile pour beaucoup de
    trains (38% des trains sous SEUIL_FIABLE=10 circulations). 90 jours ne
    change presque rien aujourd'hui (la collecte n'a que ~44 jours
    d'historique, donc la fenêtre est de facto plafonnée), mais ne coûte
    rien non plus (requête déjà indexée sur train/gare, jamais un scan de
    toute la table) et donnera le ×3 attendu automatiquement une fois la
    collecte plus ancienne que 90 jours — pas besoin d'y revenir."""
    fin = pd.Timestamp.now(tz="UTC")
    debut = fin - pd.Timedelta(days=jours)
    debut_iso, fin_iso = debut.isoformat(), fin.isoformat()

    connexion.execute("DROP TABLE IF EXISTS temp.circulations_annulees")
    connexion.execute("DROP TABLE IF EXISTS temp.circulations_arrivees_periode")
    try:
        _materialiser_circulations_annulees(connexion)
        _materialiser_circulations_arrivees_periode(connexion, debut_iso, fin_iso)
        where, params = _construire_where_sql(
            gare, train, "Tous", limiter_ligne, debut_iso=debut_iso, fin_iso=fin_iso,
        )
        cte = _cte_dernier_par_passage_complet(where)
        lignes = connexion.execute(
            cte + "SELECT trip_id, start_date, MAX(retard_min) AS retard_min FROM derniers "
            "WHERE (trip_id || '|' || start_date) NOT IN (SELECT cle FROM circulations_annulees) "
            "GROUP BY trip_id, start_date ORDER BY start_date",
            params,
        ).fetchall()

        # Contexte (type de jour/vacances/météo) pour la modale "détail d'un
        # jour" (mobile) — même logique "dernier connu par circulation" que
        # le retard ci-dessus, mais en requête séparée plutôt que d'étendre
        # _cte_dernier_par_passage_complet (partagée avec Retard max/cumulé
        # et Rapports, hors scope de cette seule carte). Réutilise le même
        # `where`/`params` (gare/train/période/arrivée confirmée déjà
        # appliqués) pour rester rigoureusement sur le même périmètre.
        lignes_contexte = connexion.execute(
            f"""
            WITH filtre AS (
                SELECT trip_id, start_date, poll_time, type_jour, vacances_scolaires,
                       temperature_c, precipitation_mm, wind_speed_kmh
                FROM observations WHERE {where}
            ),
            avec_rang AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY trip_id, start_date ORDER BY poll_time DESC
                ) AS rn
                FROM filtre
            )
            SELECT start_date, type_jour, vacances_scolaires,
                   temperature_c, precipitation_mm, wind_speed_kmh
            FROM avec_rang WHERE rn = 1
            """,
            params,
        ).fetchall()
    finally:
        connexion.execute("DROP TABLE IF EXISTS temp.circulations_annulees")
        connexion.execute("DROP TABLE IF EXISTS temp.circulations_arrivees_periode")

    if not lignes:
        return {"donnees_disponibles": False}

    contexte_par_date = {
        sd: {
            "type_jour": tj, "vacances_scolaires": vac,
            "temperature_c": temp, "precipitation_mm": pluie, "wind_speed_kmh": vent,
        }
        for sd, tj, vac, temp, pluie, vent in lignes_contexte
    }
    variantes = reference_donnees["variantes"]
    calendrier = reference_donnees["calendrier"]

    def _horaire_texte(trip_id, start_date, retard_min):
        """"Prévu à HH:MM → passage à HH:MM", ou None si le train est
        inconnu du référentiel ou ne dessert pas `gare` dans cette variante
        — réutilise estimer_passage_reel (retard=0 pour le théorique, déjà
        établi ailleurs dans l'appli plutôt que de reformater la chaîne GTFS
        brute à la main, notamment pour gérer correctement une heure GTFS
        >= 24:00)."""
        variante = choisir_variante(variantes, calendrier, trip_id, start_date)
        if variante is None:
            return None
        heure_theo = dict(zip(variante["gares"], variante["horaires"])).get(gare)
        if heure_theo is None:
            return None
        theo = estimer_passage_reel(heure_theo, start_date, 0)
        reel = estimer_passage_reel(heure_theo, start_date, retard_min)
        if theo is None or reel is None:
            return None
        theo_local = theo.tz_convert(PARIS_TZ).strftime("%H:%M")
        reel_local = reel.tz_convert(PARIS_TZ).strftime("%H:%M")
        return f"Prévu à {theo_local} → passage à {reel_local}"

    def _contexte_texte(sd):
        ctx = contexte_par_date.get(sd)
        if not ctx:
            return None
        libelle_jour = {
            "ouvré": "Jour ouvré", "ouvre": "Jour ouvré",
            "weekend": "Weekend", "férié": "Jour férié",
        }.get(ctx["type_jour"])
        vac = ctx["vacances_scolaires"]
        libelle_vacances = (
            "Vacances scolaires" if vac == 1
            else "Hors vacances scolaires" if vac == 0
            else None
        )
        parties = [p for p in (libelle_jour, libelle_vacances) if p]
        return " · ".join(parties) if parties else None

    # MAX(retard_min) par circulation (pire gare du jour) — même logique
    # "dernier connu, pire cas" que Retard max ailleurs dans l'appli.
    retards = [r for _, _, r in lignes]
    pct_a_lheure = 100 * sum(1 for r in retards if r == 0) / len(retards)
    retard_max_periode = max(retards)
    # Gare hors ligne (Autres gares (jonction), voir gares_options_mobile) :
    # même convention que tr.hors-ligne (desktop, style.css) — prioritaire
    # sur la couleur de retard habituelle (verte/orange/rouge), pas juste
    # une nuance de plus. L'appli se concentre sur les 11 gares de l'axe
    # Paris-Cherbourg ; une gare de jonction reste consultable (voir le
    # correctif limiter_ligne du 2026-08-24) mais ses chiffres ne doivent
    # pas se parer des mêmes couleurs "fiabilité de l'axe" (demande
    # utilisateur, 2026-08-24).
    hors_ligne = gare not in GARES_LIGNE
    # hauteur_pct (0-100, relative au pire jour de la période) : même
    # principe que barres_pct/hauteur_pct de calculer_contexte_tendances_
    # mobile — calculé ici plutôt qu'en Jinja pour garder le template muet.
    historique = [
        {
            "date": _format_start_date(sd), "retard_min": r,
            "classe": "grise" if hors_ligne else (
                "verte" if r == 0 else "orange" if r < SEUIL_RETARD_FORT else "rouge"
            ),
            "hauteur_pct": round(100 * r / retard_max_periode) if retard_max_periode else 0,
            "horaire_texte": _horaire_texte(trip_id, sd, r),
            "contexte_texte": _contexte_texte(sd),
            "meteo_temp": contexte_par_date.get(sd, {}).get("temperature_c"),
            "meteo_pluie": contexte_par_date.get(sd, {}).get("precipitation_mm"),
            "meteo_vent": contexte_par_date.get(sd, {}).get("wind_speed_kmh"),
        }
        for trip_id, sd, r in lignes
    ]
    # Retards seuls (pas "derniers passages" tous confondus) : un petit
    # échantillon des derniers jours, majoritairement à l'heure, tombait
    # facilement "tout vert" par coïncidence et semblait contredire le %
    # affiché juste au-dessus — lister uniquement les retards explique le %
    # au lieu de sembler le contredire (retour utilisateur, 2026-08-24).
    retards_liste = [j for j in reversed(historique) if j["retard_min"] > 0]
    n_retards_masques = max(0, len(retards_liste) - MAX_RETARDS_AFFICHES_MOBILE)

    return {
        "donnees_disponibles": True,
        "pct_a_lheure": round(pct_a_lheure, 1),
        "retard_moyen": round(sum(retards) / len(retards), 1),
        "retard_max": retard_max_periode,
        "historique_quotidien": historique,
        "retards": retards_liste[:MAX_RETARDS_AFFICHES_MOBILE],
        "n_retards_masques": n_retards_masques,
        "hors_ligne": hors_ligne,
        # Même seuil que "Par jour/heure" (SEUIL_FIABLE) — repris ici pour
        # signaler qu'un train encore peu observé peut voir son % bouger
        # beaucoup au prochain relevé (demande différée du 2026-08-24).
        "n_circulations": len(retards),
        "fiable": len(retards) >= SEUIL_FIABLE,
        "seuil_fiable": SEUIL_FIABLE,
    }


def _retard_max_et_cumule_sql(
    connexion, gare, train, sens, limiter_ligne, limiter_retard, debut_iso=None, fin_iso=None,
):
    """Retard max (par train, avec gestion des égalités jusqu'à 3 —
    même motif texte que calculer_stats_bloc, formatting.py), Retard cumulé
    (somme des dernières valeurs positives, + nb_passages_impactes, le
    nombre de passages qui la composent — utilisé par calculer_contexte_
    graphique, pas par la barre de stats globale) et % à l'heure, tous
    dérivés de la même CTE "dernier par passage". Matérialisée UNE fois dans une table
    temporaire plutôt que réévaluée par 2 requêtes séparées (mesuré 2026-
    08-16 sur observations.db réel : la fenêtre ROW_NUMBER coûtait ~200ms
    par passage sur un filtre Gare, doublé inutilement par les 2 requêtes
    initiales — passage plus filtre Gare gare="Caen" : ~500ms -> ~200ms).
    Nom de table fixe car une seule requête HTTP == un seul connexion.execute
    à la fois sur cette connexion (pas de risque de collision concurrente).

    Exclut aussi les circulations de circulations_annulees (voir
    _materialiser_circulations_annulees, que l'appelant DOIT avoir déjà
    matérialisée) : sans cette exclusion, une circulation annulée dont le
    flux GTFS-RT a publié une prévision d'arrivée au terminus juste avant
    l'annulation officielle (train 851116, 2026-08-20) apparaîtrait comme
    arrivée avec un vrai retard, alors qu'elle n'est jamais arrivée."""
    where, params = _construire_where_sql(
        gare, train, sens, limiter_ligne, limiter_retard, debut_iso=debut_iso, fin_iso=fin_iso,
    )
    cte = _cte_dernier_par_passage_complet(where)

    connexion.execute("DROP TABLE IF EXISTS temp.derniers_par_passage")
    try:
        connexion.execute(
            "CREATE TEMP TABLE derniers_par_passage AS "
            + cte + "SELECT substr(trip_id, 1, instr(trip_id, ':') - 1) AS train, retard_min FROM derniers "
            + "WHERE (trip_id || '|' || start_date) NOT IN (SELECT cle FROM circulations_annulees)",
            params,
        )
        lignes_train = connexion.execute(
            "SELECT train, MAX(retard_min) AS max_retard FROM derniers_par_passage GROUP BY train",
        ).fetchall()
        somme = connexion.execute(
            "SELECT SUM(retard_min) FROM derniers_par_passage WHERE retard_min > 0",
        ).fetchone()[0] or 0
        # Nombre de passages (dernier connu) au retard cumulé ci-dessus —
        # même table déjà construite, pas de scan supplémentaire. Utilisé
        # par calculer_contexte_graphique (segments_periode, "X passages
        # impactés"), pas par la barre de stats globale.
        nb_passages_impactes = connexion.execute(
            "SELECT COUNT(*) FROM derniers_par_passage WHERE retard_min > 0",
        ).fetchone()[0]
        # % de relevés (dernier connu par gare, PAS par circulation — un
        # même train à l'heure sur 10 gares et en retard sur une seule
        # compte pour 10 relevés "à l'heure" ici) à 0 min pile — même table
        # déjà construite ci-dessus (circulations annulées déjà exclues),
        # pas de scan supplémentaire. Demande explicite de l'utilisateur,
        # 2026-08-21 ("88,2 % des relevés du flux temps réel SNCF indiquent
        # un train à l'heure").
        nb_releves, nb_a_lheure = connexion.execute(
            "SELECT COUNT(*), SUM(CASE WHEN retard_min = 0 THEN 1 ELSE 0 END) FROM derniers_par_passage",
        ).fetchone()
        pct_a_lheure = 100 * nb_a_lheure / nb_releves if nb_releves else None
    finally:
        connexion.execute("DROP TABLE IF EXISTS temp.derniers_par_passage")

    # "train"/"trains" (pluriel éventuel) vit déjà dans le texte lui-même
    # (mot_singulier/mot_pluriel non vides ci-dessus) — pas besoin du 2e
    # élément du tuple ici, contrairement à Gare la + touchée plus bas
    # (voir label_pire_gare, _pire_gare_et_moyenne_sql). dict(lignes_train)
    # -> Series : GROUP BY garantit des clés (train) uniques, aucun risque
    # de collision — réutilise formatting.texte_categorie_maximale (version
    # pandas) au lieu d'une copie locale sur tuples (audit de nettoyage,
    # 2026-08-19 : les deux étaient identiques à la structure d'entrée près).
    retard_max_texte, _ = texte_categorie_maximale(
        pd.Series(dict(lignes_train)), "train", "trains", format_numero_train, lambda v: f"{v:.0f} min",
    )
    heures, minutes = divmod(round(somme), 60)

    return retard_max_texte, heures, minutes, pct_a_lheure, nb_passages_impactes


def _pire_gare_et_moyenne_sql(
    connexion, gare, train, sens, limiter_ligne, limiter_retard, debut_iso=None, fin_iso=None,
):
    """Gare la + touchée (moyenne BRUTE de retard_min par gare, toutes les
    lignes, pas seulement le dernier relevé connu par passage — contrairement
    à Retard max/cumulé) ET Retard moyen / relevé (même moyenne, non groupée)
    — mesuré 2026-08-16 sur observations.db réel (défaut, sans filtre Gare) :
    ces 2 stats scannaient chacune indépendamment ~520k lignes (~0,4s
    chacune) alors qu'elles portent sur EXACTEMENT le même périmètre filtré
    — matérialisé ici une seule fois (gare, retard_min brut) puis les 2
    agrégats en sont dérivés à faible coût, comme déjà fait pour Retard max/
    cumulé (_retard_max_et_cumule_sql)."""
    where, params = _construire_where_sql(
        gare, train, sens, limiter_ligne, limiter_retard, debut_iso=debut_iso, fin_iso=fin_iso,
    )
    expr_retard = "ROUND(COALESCE(arrival_delay_s, departure_delay_s) / 60.0, 1)"

    connexion.execute("DROP TABLE IF EXISTS temp.releves_filtres")
    try:
        connexion.execute(
            f"CREATE TEMP TABLE releves_filtres AS "
            f"SELECT gare, {expr_retard} AS retard_min FROM observations WHERE {where}",
            params,
        )
        moyenne, nb_releves = connexion.execute(
            "SELECT AVG(retard_min), COUNT(*) FROM releves_filtres",
        ).fetchone()
        nb_releves = nb_releves or 0
        lignes_gare = connexion.execute(
            "SELECT gare, AVG(retard_min) AS moyenne FROM releves_filtres GROUP BY gare",
        ).fetchall()
    finally:
        connexion.execute("DROP TABLE IF EXISTS temp.releves_filtres")

    pire_gare_texte, pire_gare_pluriel = texte_categorie_maximale(
        pd.Series(dict(lignes_gare)), "", "", lambda g: g, lambda v: f"moy {format_min_sans_zero(v)} min",
    )
    label_pire_gare = "Gare les + touchées" if pire_gare_pluriel else "Gare la + touchée"
    return moyenne, nb_releves, pire_gare_texte, label_pire_gare


def _serie_temporelle_graphique_sql(connexion, gare, train, sens, limiter_ligne, debut_iso, fin_iso):
    """Série temporelle du Graphique période "tout l'historique" (moyenne et
    % en retard par poll_time) — équivalent SQL de
    plot_df.groupby("poll_time")... (calculer_contexte_graphique), plus le
    ratio total/en_retard ("circulations perturbées"). Même stratégie que
    _pire_gare_et_moyenne_sql/_retard_max_et_cumule_sql : un seul scan du
    périmètre filtré, matérialisé dans une table temporaire, plusieurs
    agrégats dérivés à faible coût.

    PAS le même calcul que _circulations_et_trains_stats_sql (barre de stats
    globale) pour total/en_retard : celle-ci compte délibérément une
    annulation comme "en retard" (voir son docstring) ; ici une circulation
    annulée est déjà absente du périmètre filtré (exclue en amont par
    circulations_arrivees_periode, voir _construire_where_sql) et ne peut
    donc jamais être comptée comme "en retard" — comportement de
    formatting.calculer_stats_bloc, que ce Graphique reproduit."""
    where, params = _construire_where_sql(gare, train, sens, limiter_ligne, debut_iso=debut_iso, fin_iso=fin_iso)

    connexion.execute("DROP TABLE IF EXISTS temp.graphique_releves_filtres")
    try:
        connexion.execute(
            "CREATE TEMP TABLE graphique_releves_filtres AS "
            "SELECT poll_time, trip_id || '|' || start_date AS circulation, "
            "ROUND(COALESCE(arrival_delay_s, departure_delay_s) / 60.0, 1) AS retard_min "
            f"FROM observations WHERE {where}",
            params,
        )
        nb_releves = connexion.execute("SELECT COUNT(*) FROM graphique_releves_filtres").fetchone()[0]
        total, en_retard = connexion.execute(
            "SELECT COUNT(DISTINCT circulation), "
            "COUNT(DISTINCT CASE WHEN retard_min > 0 THEN circulation END) "
            "FROM graphique_releves_filtres",
        ).fetchone()
        lignes_moyenne = connexion.execute(
            "SELECT poll_time, AVG(retard_min) FROM graphique_releves_filtres GROUP BY poll_time",
        ).fetchall()
        lignes_pct = connexion.execute(
            "SELECT poll_time, 100.0 * COUNT(DISTINCT CASE WHEN retard_min > 0 THEN circulation END) "
            "/ COUNT(DISTINCT circulation) FROM graphique_releves_filtres GROUP BY poll_time",
        ).fetchall()
    finally:
        connexion.execute("DROP TABLE IF EXISTS temp.graphique_releves_filtres")

    moyenne_par_releve = pd.Series(
        [l[1] for l in lignes_moyenne], index=pd.to_datetime([l[0] for l in lignes_moyenne], utc=True),
    )
    pct_par_releve = pd.Series(
        [l[1] for l in lignes_pct], index=pd.to_datetime([l[0] for l in lignes_pct], utc=True),
    )
    return moyenne_par_releve, pct_par_releve, nb_releves, total, en_retard


def calculer_donnees_graphique_historique_sql(connexion, gare, train, sens, limiter_ligne):
    """Équivalent SQL, pour la période "tout l'historique" du Graphique, de
    (charger_observations_completes + filtrer_df + exclusions annulées/non-
    confirmées + groupby poll_time + calculer_stats_bloc) — mêmes bornes
    (BORNE_DEBUT_COLLECTE_ISO -> maintenant) et même orchestration de tables
    temporaires que calculer_stats_globales_sql(depuis_debut_collecte=True) :
    n'a plus besoin de charger toute la table observations en pandas (voir
    mémoire du projet — mesuré ~30s / pic RAM ~2,6 Go sur la VPS en
    2026-08-24, 1,24M lignes, sans plafond avec la croissance de la
    collecte). Renvoie None si aucune circulation ne correspond au périmètre
    filtré (équivalent de plot_df.empty)."""
    fin_iso = pd.Timestamp.now(tz="UTC").isoformat()
    _materialiser_circulations_annulees(connexion)
    _obtenir_circulations_arrivees_globales(connexion)
    try:
        moyenne_par_releve, pct_par_releve, nb_releves, total, en_retard = _serie_temporelle_graphique_sql(
            connexion, gare, train, sens, limiter_ligne, BORNE_DEBUT_COLLECTE_ISO, fin_iso,
        )
        if total == 0:
            return None
        retard_max_texte, heures, minutes, _pct_a_lheure, nb_passages_impactes = _retard_max_et_cumule_sql(
            connexion, gare, train, sens, limiter_ligne, limiter_retard=False,
            debut_iso=BORNE_DEBUT_COLLECTE_ISO, fin_iso=fin_iso,
        )
        moyen, _nb_releves, pire_gare_texte, label_pire_gare = _pire_gare_et_moyenne_sql(
            connexion, gare, train, sens, limiter_ligne, limiter_retard=False,
            debut_iso=BORNE_DEBUT_COLLECTE_ISO, fin_iso=fin_iso,
        )
    finally:
        connexion.execute("DROP TABLE IF EXISTS temp.circulations_annulees")
        connexion.execute("DROP TABLE IF EXISTS temp.circulations_arrivees_periode")

    stats_bloc = {
        "total": total, "en_retard": en_retard, "heures": heures, "minutes": minutes,
        "nb_passages_impactes": nb_passages_impactes, "moyen": moyen,
        "pire_gare_texte": pire_gare_texte, "label_pire_gare": label_pire_gare,
        "retard_max_texte": retard_max_texte,
    }
    return moyenne_par_releve, pct_par_releve, nb_releves, stats_bloc


def _circulations_et_trains_stats_sql(
    connexion, gare, train, sens, limiter_ligne, debut_iso=None, fin_iso=None,
):
    """Circulations perturbées (total de circulations distinctes trip_id+
    start_date dans le périmètre filtré, et combien d'entre elles ont eu au
    moins une ligne en retard n'importe où — PAS "dernière valeur connue",
    une correction à la baisse ensuite ne les fait pas sortir du compte,
    contrairement à Retard max/cumulé) ET nombre de trains distincts (au
    sens sans_date_trip_id — trip_id sans son suffixe ':AAAAMMJJ' final,
    utilisé par tooltip_ratio_retard) — mesuré 2026-08-16 : ces 2 comptes
    portent sur le même périmètre, regroupés en une seule requête plutôt que
    2 scans séparés du même ~520k lignes. Le GLOB isole les trip_id se
    terminant bien par le suffixe date avant de le retirer (SUBSTR aveugle
    aurait tronqué à tort les trip_id ne le portant pas, cas rare mais
    existant en pratique).

    exiger_retard_connu=True (défaut de _construire_where_sql, pas
    surchargé ici) depuis le 2026-08-24 — avant cette date, cette fonction
    utilisait exiger_retard_connu=False (comme calculer_stats_bloc/pandas,
    jamais dropna sur retard_min), pour rester au plus près de la version
    pandas d'origine. Repassé à True après avoir constaté un écart de
    dénominateur avec Rapports/mobile/le bloc période du Graphique (tous
    déjà en True) sur un cas réel : une circulation avec un arrêt supprimé
    à la gare filtrée (donc sans valeur de retard exploitable CE JOUR-LÀ à
    CETTE gare) restait comptée ici mais pas ailleurs. Effet mesuré sur
    observations.db réel : seules 4 circulations dans tout l'historique ont
    eu un arrêt supprimé, et sans filtre Gare (le cas par défaut), leur
    retard est presque toujours connu ailleurs sur leur trajet — impact
    quasi nul en pratique, sauf sur une vue filtrée précisément sur la gare
    sautée ce jour-là.

    Une circulation annulée (circulations_annulees, que l'appelant DOIT
    avoir déjà matérialisée) compte aussi comme "en retard" même sans
    aucune ligne à retard > 0 — contrairement à Retard max/cumulé (où une
    annulation est exclue entièrement, faute de valeur de retard finale
    exploitable), ici la question posée est "cette circulation a-t-elle
    posé un problème", et une annulation en est un, pire qu'un simple
    retard. Sans ce OR, la majorité des annulations connues (10 sur 12,
    2026-08-21 : celles jamais vues avec un retard positif avant leur
    annulation) tombaient à tort du côté "sans problème"."""
    where, params = _construire_where_sql(
        gare, train, sens, limiter_ligne, debut_iso=debut_iso, fin_iso=fin_iso,
    )
    expr_retard = "COALESCE(arrival_delay_s, departure_delay_s)"
    expr_sans_date = (
        "CASE WHEN trip_id GLOB '*:[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]' "
        "THEN SUBSTR(trip_id, 1, LENGTH(trip_id) - 9) ELSE trip_id END"
    )
    expr_annulee = "(trip_id || '|' || start_date) IN (SELECT cle FROM circulations_annulees)"
    ligne = connexion.execute(
        f"""
        SELECT COUNT(DISTINCT trip_id || '|' || start_date) AS total,
               COUNT(DISTINCT CASE WHEN {expr_retard} > 0 OR {expr_annulee} THEN trip_id || '|' || start_date END) AS en_retard,
               COUNT(DISTINCT {expr_sans_date}) AS nb_trains
        FROM observations WHERE {where}
        """,
        params,
    ).fetchone()
    return ligne[0] or 0, ligne[1] or 0, ligne[2] or 0


def calculer_stats_globales_sql(
    connexion, gare, train, sens, limiter_ligne, limiter_retard,
    debut_iso=None, fin_iso=None, depuis_debut_collecte=False,
):
    """Porte calculer_stats_globales en SQL — même structure de sortie
    (mêmes clés de contexte, mêmes textes de tooltips), mais n'a plus
    besoin de charger tout l'historique en pandas (voir mémoire du projet,
    2026-08-16 : le cache complet séparé, une fois activé, redevenait non
    borné exactement comme le problème d'origine). Chaque sous-calcul
    factorisé dans sa propre fonction ci-dessus, testée/validée
    séparément avant assemblage. Si limiter_retard : matérialise
    `circulations_retard` UNE fois ici (voir _construire_where_sql/
    _materialiser_circulations_retard) — Retard max/cumulé, Gare la +
    touchée et Retard moyen la référencent tous les 3 au lieu de
    recalculer chacun leur propre sous-requête.

    depuis_debut_collecte (par défaut False) : True uniquement pour l'appel
    "barre de stats globale" (calculer_stats_globales_sql_avec_cache), avec
    debut_iso=BORNE_DEBUT_COLLECTE_ISO/fin_iso=maintenant — PAS la même
    chose que "debut_iso est fourni" : Rapports fournit aussi debut_iso/
    fin_iso (sa propre période), mais avec depuis_debut_collecte resté
    False. Ce distinguo a été introduit le 2026-08-24 quand la barre
    globale a commencé, elle aussi, à exiger l'arrivée confirmée (voir
    _materialiser_circulations_arrivees_periode), exactement comme Rapports
    et la carte de stats mobile (calculer_carte_stats_train_sql) — jusque-
    là, "Circulations perturbées"/"Retard max" de la barre globale
    comptaient une circulation encore en cours avec son dernier retard
    connu, potentiellement provisoire : un vrai écart de méthode entre
    desktop et mobile, repéré en comparant leurs chiffres pour un même
    train (2026-08-24). Une première version bornait plutôt sur une petite
    fenêtre récente (48h) pour éviter de rejouer cette vérification sur
    tout l'historique à chaque calcul (mesuré : 9,2s sur ~4400 circulations
    contre 0,13s sur 48h) — écartée : elle laissait un écart résiduel sur
    d'anciennes circulations jamais officiellement confirmées (dernier poll
    juste avant l'heure d'arrivée théorique, un cas réel rencontré), pour
    un gain de temps jugé pas assez important vu le trafic de cette appli
    (usage perso/familial, un éventuel 1er appel à ~10s après une nouvelle
    collecte, entre 2 collectes le reste du temps grâce au cache de
    résultats, jugé acceptable — décision utilisateur, 2026-08-24). Sert
    aussi à décider QUI matérialise circulations_annulees/circulations_
    arrivees_periode (Rapports le fait déjà lui-même avant cet appel, car
    elle sert aussi à d'autres calculs du rapport — voir
    _construire_where_sql) ET, dans _calculer_stats_globales_sql_interne, à
    choisir le bon texte ("depuis le début de la collecte" vs "sur cette
    période") indépendamment du fait que des bornes soient passées ou non.

    Calcul intrinsèquement coûteux (2026-08-16, mesuré sur observations.db
    réel, ~950k lignes) : "dernier retard connu par passage" (Retard max/
    cumulé) nécessite un vrai passage sur toutes les lignes du périmètre
    filtré (pas de raccourci SQL possible, testé — fenêtre ROW_NUMBER et
    GROUP BY+JOIN donnent le même ordre de grandeur) — de l'ordre de 1 à 4s
    sans filtre Gare (le cas le plus fréquent, ~520k lignes concernées),
    nettement moins avec un filtre Gare actif (sous-ensemble plus petit).
    depuis_debut_collecte=True ajoute par-dessus le coût de circulations_
    arrivees_periode sur tout l'historique (~9-11s mesuré 2026-08-24, sur
    un historique encore petit — ce coût grossira avec la collecte). Voir
    calculer_stats_globales_sql_avec_cache : c'est ce coût qui justifie le
    petit cache de RÉSULTATS (pas de données brutes) ajouté autour de
    cette fonction plutôt que de l'appeler directement à chaque requête —
    ne pèse donc qu'une seule fois par nouveau relevé (~5 min), pas à
    chaque affichage."""
    if limiter_retard:
        connexion.execute("DROP TABLE IF EXISTS temp.circulations_retard")
        _materialiser_circulations_retard(
            connexion, gare, train, sens, limiter_ligne, debut_iso=debut_iso, fin_iso=fin_iso,
        )
    if depuis_debut_collecte:
        _materialiser_circulations_annulees(connexion)
        _obtenir_circulations_arrivees_globales(connexion)
    try:
        return _calculer_stats_globales_sql_interne(
            connexion, gare, train, sens, limiter_ligne, limiter_retard,
            debut_iso=debut_iso, fin_iso=fin_iso, depuis_debut_collecte=depuis_debut_collecte,
        )
    finally:
        if limiter_retard:
            connexion.execute("DROP TABLE IF EXISTS temp.circulations_retard")
        if depuis_debut_collecte:
            connexion.execute("DROP TABLE IF EXISTS temp.circulations_annulees")
            connexion.execute("DROP TABLE IF EXISTS temp.circulations_arrivees_periode")


# Cache des RÉSULTATS déjà calculés (pas des données brutes, contrairement à
# l'ancien _cache_stats_globales pandas supprimé le 2026-08-16) — quelques
# Ko par combinaison de filtres (Gare/Train/Sens/Limiter...) déjà vue, donc
# borné en pratique par le nombre de combinaisons réellement visitées entre
# 2 collectes, jamais par la taille d'observations.db. Invalidé entièrement
# à chaque nouveau relevé (MAX(poll_time) différent, requête bon marché déjà
# indexée) plutôt que ligne par ligne — simple, et une collecte a lieu
# toutes les ~5 min donc le gain (2e+ affichage d'un même filtre entre 2
# collectes quasi instantané) reste très utile en pratique (changement
# d'onglet, retour en arrière sur le même filtre Gare...).
_cache_resultats_stats_globales = {"dernier_poll": None, "resultats": {}}


def calculer_stats_globales_sql_avec_cache(connexion, gare, train, sens, limiter_ligne, limiter_retard):
    """Enrobe calculer_stats_globales_sql d'un cache mémoire des résultats
    déjà calculés pour la combinaison de filtres actuelle — voir le
    docstring de calculer_stats_globales_sql et _cache_resultats_stats_
    globales ci-dessus pour le pourquoi (calcul intrinsèquement coûteux,
    cache borné car il ne stocke que le petit dict de résultats, jamais de
    DataFrame)."""
    dernier_poll = connexion.execute("SELECT MAX(poll_time) FROM observations").fetchone()[0]
    cache = _cache_resultats_stats_globales
    if cache["dernier_poll"] != dernier_poll:
        cache["dernier_poll"] = dernier_poll
        cache["resultats"] = {}
    cle = (gare, train, sens, limiter_ligne, limiter_retard)
    if cle not in cache["resultats"]:
        cache["resultats"][cle] = calculer_stats_globales_sql(
            connexion, gare, train, sens, limiter_ligne, limiter_retard,
            debut_iso=BORNE_DEBUT_COLLECTE_ISO, fin_iso=pd.Timestamp.now(tz="UTC").isoformat(),
            depuis_debut_collecte=True,
        )
    return cache["resultats"][cle]


def _calculer_stats_globales_sql_interne(
    connexion, gare, train, sens, limiter_ligne, limiter_retard,
    debut_iso=None, fin_iso=None, depuis_debut_collecte=False,
):
    total, en_retard, nb_trains_observes = _circulations_et_trains_stats_sql(
        connexion, gare, train, sens, limiter_ligne, debut_iso=debut_iso, fin_iso=fin_iso,
    )
    pct_perturbe = 100 * en_retard / total if total else None
    # Complément de "Circulations perturbées" (barre de stats générale
    # uniquement, PAS l'onglet Rapports — voir templates/_stats.html) :
    # demande explicite de l'utilisateur, 2026-08-21.
    sans_perturbation = total - en_retard
    pct_sans_perturbation_texte = (
        f"{100 * sans_perturbation / total:.1f}".replace(".", ",") if total else None
    )

    debut_collecte = connexion.execute("SELECT MIN(poll_time) FROM observations").fetchone()[0]
    fin_collecte = connexion.execute("SELECT MAX(poll_time) FROM observations").fetchone()[0]
    total_lignes = connexion.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    # len(df_filtre) côté pandas incluait les lignes sans retard connu (pas
    # de dropna) — exiger_retard_connu=False pour ce seul compte, comme
    # tooltip_resume_collecte l'explique déjà (plus large que "Retard
    # moyen"/"Gare la + touchée"). limiter_retard=limiter_retard : len(df_
    # filtre) applique restreindre_aux_trains_en_retard quand la case est
    # cochée (df_filtre, pas df_avant_retard, contrairement à total/en_retard
    # ci-dessus).
    where_filtre, params_filtre = _construire_where_sql(
        gare, train, sens, limiter_ligne, limiter_retard=limiter_retard, exiger_retard_connu=False,
        debut_iso=debut_iso, fin_iso=fin_iso,
    )
    nb_relevés_filtre = connexion.execute(
        f"SELECT COUNT(*) FROM observations WHERE {where_filtre}", params_filtre,
    ).fetchone()[0]

    date_debut_collecte = format_poll_time(debut_collecte).split(" à ")[0]
    resume_collecte = (
        f"{nb_relevés_filtre} relevés (sur {total_lignes} au total, depuis le "
        f"{date_debut_collecte}) — dernier relevé : {format_poll_time(fin_collecte)}"
    )
    tooltip_resume_collecte = (
        "Compte tous les relevés correspondant aux filtres actifs (Gare/Train/Sens/"
        "Limiter...), y compris ceux sans aucune valeur de retard connue (arrivée et "
        "départ tous deux vides) — visibles dans le Tableau avec « – » sur les colonnes "
        "Arr. et Dép., mais qui ne peuvent pas contribuer à une moyenne. C'est pourquoi ce "
        "nombre est légèrement supérieur à celui utilisé par « Retard moyen / relevé »/"
        "« Gare la + touchée » ci-dessous, qui excluent ces cas."
    )

    # "depuis le début de la collecte" n'a de sens que pour la barre de
    # stats globale (depuis_debut_collecte=True) — pour un appel borné à une
    # période (Rapports, depuis_debut_collecte=False même si debut_iso est
    # fourni), ce même texte affirmerait à tort porter sur tout l'historique
    # alors que les chiffres sont bornés à la période choisie (bug réel
    # trouvé en testant l'onglet Rapports en direct, 2026-08-17 : les
    # NOMBRES étaient déjà correctement bornés, mais ce texte restait celui
    # écrit pour la barre globale). Ne PAS retester sur debut_iso : depuis le
    # 2026-08-24, la barre globale fournit elle aussi debut_iso/fin_iso (pour
    # bénéficier de la vérification "arrivée confirmée"), ce qui rendrait ce
    # test toujours vrai.
    portee_texte = "sur cette période" if not depuis_debut_collecte else "depuis le début de la collecte"
    if total:
        tooltip_ratio_retard = (
            f"{en_retard} circulations perturbées (retard à un moment de "
            f"leur trajet, même rattrapé ensuite, ou annulation) sur {total} déjà "
            f"observées {portee_texte} (issues de {nb_trains_observes} trains "
            f"différents parmi les {len(reference_donnees['variantes'])} du référentiel), "
            f"soit {100 * en_retard / total:.0f} %."
        )
        tooltip_sans_perturbation = (
            f"{sans_perturbation} circulations n'ayant eu aucun retard ni "
            f"annulation sur leur trajet, sur {total} déjà observées {portee_texte} "
            f"(issues de {nb_trains_observes} trains différents parmi les "
            f"{len(reference_donnees['variantes'])} du référentiel), soit "
            f"{pct_sans_perturbation_texte} %."
        )
    else:
        tooltip_ratio_retard = ""
        tooltip_sans_perturbation = ""

    moyen, nb_releves, pire_gare_texte, label_pire_gare = _pire_gare_et_moyenne_sql(
        connexion, gare, train, sens, limiter_ligne, limiter_retard, debut_iso=debut_iso, fin_iso=fin_iso,
    )
    if nb_releves:
        retard_max_texte, heures, minutes, pct_a_lheure, _nb_passages_impactes = _retard_max_et_cumule_sql(
            connexion, gare, train, sens, limiter_ligne, limiter_retard,
            debut_iso=debut_iso, fin_iso=fin_iso,
        )
        stats = {
            "heures": heures, "minutes": minutes, "moyen": moyen,
            "retard_max_texte": retard_max_texte, "pire_gare_texte": pire_gare_texte,
            "label_pire_gare": label_pire_gare,
            "pct_a_lheure_texte": f"{pct_a_lheure:.1f}".replace(".", ",") if pct_a_lheure is not None else None,
        }
        # "filtres actifs ci-dessus"/"300 dernières lignes affichées dans le
        # tableau" n'ont de sens que pour la barre de stats globale — sur
        # l'onglet Rapports (depuis_debut_collecte=False), il n'y a ni
        # filtres (formulaire #filtres entièrement masqué) ni tableau du
        # tout, référence trompeuse à des éléments d'interface absents.
        # Repéré en relisant les tooltips affichés en vrai sur cet onglet,
        # 2026-08-18. Testé sur depuis_debut_collecte, pas debut_iso (voir
        # portee_texte plus haut) : la barre globale fournit elle aussi des
        # bornes depuis le 2026-08-24.
        portee_releves_texte = (
            "issus de la période" if not depuis_debut_collecte
            else "issus des filtres actifs ci-dessus, pas seulement sur les 300 dernières "
                 "lignes affichées dans le tableau"
        )
        tooltip_moyen = (
            f"Moyenne brute sur les {nb_releves} relevés {portee_releves_texte} — un même "
            "passage réel est vu à plusieurs relevés tant qu'il reste dans la fenêtre du "
            "flux temps réel, d'où une moyenne « par relevé » très diluée par rapport au "
            "retard cumulé réel."
        )
        tooltip_pire_gare = (
            f"Gare avec le retard moyen / relevé le plus élevé, sur les {nb_releves} "
            f"relevés {portee_releves_texte} — une moyenne brute (voir « Retard moyen / "
            "relevé » ci-dessus) peut donc être dominée par un seul train très en retard, "
            "sondé à répétition tant qu'il reste dans le flux temps réel, plutôt que "
            "refléter une vraie difficulté récurrente de cette gare."
        )
        jours_cumules, heures_restantes = divmod(heures, 24)
        depuis_texte = (
            "sur cette période" if not depuis_debut_collecte
            else f"depuis le tout début de la collecte, le {date_debut_collecte}"
        )
        tooltip_cumule = (
            "Additionne le dernier retard connu pour chaque passage impacté (un train "
            f"à une gare précise), {depuis_texte}. Son intérêt est surtout de donner une "
            f"idée de l'ampleur du volume total de retard généré par la ligne sur toute "
            f"cette période (soit environ {jours_cumules} jours et {heures_restantes} h cumulés)."
        )
    else:
        stats = None
        tooltip_moyen = tooltip_pire_gare = tooltip_cumule = ""

    tooltip_retard_max = (
        "Le plus grand retard observé, avec le train concerné. Peut provenir d'une "
        "circulation ancienne dont l'horaire théorique a changé depuis (la SNCF republie "
        "régulièrement des ajustements) — dans ce cas, l'onglet « Suivi d'un train » "
        "affichera « trajet théorique introuvable », mais le retard lui-même reste bien "
        "réel et compté."
    )

    tooltip_pct_a_lheure = (
        "% des derniers relevés connus (un train à une gare précise) à exactement 0 min "
        "de retard, parmi les filtres actifs, circulations annulées exclues."
    )

    return {
        "resume_collecte": resume_collecte,
        "tooltip_resume_collecte": tooltip_resume_collecte,
        "stats_ratio": {"total": total, "en_retard": en_retard, "sans_perturbation": sans_perturbation},
        "pct_perturbe": pct_perturbe,
        "pct_sans_perturbation_texte": pct_sans_perturbation_texte,
        "stats": stats,
        "format_min_sans_zero": format_min_sans_zero,
        "tooltip_ratio_retard": tooltip_ratio_retard,
        "tooltip_sans_perturbation": tooltip_sans_perturbation,
        "tooltip_moyen": tooltip_moyen,
        "tooltip_pire_gare": tooltip_pire_gare,
        "tooltip_cumule": tooltip_cumule,
        "tooltip_retard_max": tooltip_retard_max,
        "tooltip_pct_a_lheure": tooltip_pct_a_lheure,
    }


def calculer_contexte_jour_heure_sql(connexion, gare, train, sens, limiter_ligne):
    """Porte calculer_contexte_jour_heure en SQL — même structure de sortie
    (donnees_json), mais interroge observations.db directement au lieu de
    recevoir un DataFrame déjà chargé/filtré : cet onglet a besoin de voir
    tout l'historique pour être pertinent (tendances par jour/heure), ce
    que _cache_observations (fenêtre glissante de 7 jours) ne peut plus
    fournir. Pas de test d'un DataFrame "vide" en amont comme la version
    pandas (plot_df.empty) : chaque requête gère nativement l'absence de
    résultat (stats vides, reindex → tout NaN), _construire_barre gère déjà
    ce cas (barre.valeur = None)."""
    connexion.execute("DROP TABLE IF EXISTS temp.jour_heure_filtre")
    try:
        _materialiser_jour_heure(connexion, gare, train, sens, limiter_ligne)
        stats_jour = _stats_par_categorie_sql(connexion, "jour_semaine", JOURS_ORDRE)
        stats_heure = _stats_par_categorie_sql(connexion, "heure_locale", list(range(24)))
        stats_type_jour = _stats_par_categorie_sql(connexion, "type_jour_simple", ORDRE_TYPE_JOUR)
        stats_vacances = _stats_par_categorie_sql(connexion, "vacances", ORDRE_VACANCES)
    finally:
        connexion.execute("DROP TABLE IF EXISTS temp.jour_heure_filtre")
    labels_jour = [j[:3] for j in JOURS_ORDRE]
    labels_heure = [f"{h}h" for h in range(24)]

    if stats_jour["n"].fillna(0).sum() == 0:
        return {"jour_heure_vide": True}

    donnees = {
        "jour_moyenne": _construire_barre(stats_jour, "moyenne", labels_jour, " min", True),
        "jour_pct": _construire_barre(stats_jour, "pct", labels_jour, " %", True),
        "heure_moyenne": _construire_barre(stats_heure, "moyenne", labels_heure, " min", True),
        "heure_pct": _construire_barre(stats_heure, "pct", labels_heure, " %", True),
        "type_jour": _construire_barre(stats_type_jour, "moyenne", ORDRE_TYPE_JOUR, " min", False),
        "vacances": _construire_barre(stats_vacances, "moyenne", ORDRE_VACANCES, " min", False),
    }
    # Titres dynamiques (label + valeur de la catégorie au maximum) pour les
    # 4 graphiques à catégories multiples (jour/heure) — pas pour
    # type_jour/vacances, qui n'ont que 2 barres chacun, où un "max" n'a pas
    # de valeur explicative (demande explicite de l'utilisateur, 2026-08-15).
    donnees["jour_moyenne"]["titre"] = titre_dynamique_jour_heure(
        "Retard moyen par jour", stats_jour, "moyenne", JOURS_ORDRE, str.lower,
        lambda v: f"{v:.1f} min", SEUIL_FIABLE,
    )
    donnees["jour_pct"]["titre"] = titre_dynamique_jour_heure(
        "% en retard par jour", stats_jour, "pct", JOURS_ORDRE, str.lower,
        lambda v: f"{v:.0f} %", SEUIL_FIABLE,
    )
    donnees["heure_moyenne"]["titre"] = titre_dynamique_jour_heure(
        "Retard moyen par heure", stats_heure, "moyenne", labels_heure, lambda l: f"à {l}",
        lambda v: f"{v:.1f} min", SEUIL_FIABLE,
    )
    donnees["heure_pct"]["titre"] = titre_dynamique_jour_heure(
        "% en retard par heure", stats_heure, "pct", labels_heure, lambda l: f"à {l}",
        lambda v: f"{v:.1f} %".replace(".", ","), SEUIL_FIABLE,
    )
    return {
        "jour_heure_vide": False,
        "donnees_json": json_pour_script(donnees),
    }


# Même principe que _cache_resultats_stats_globales (cache de RÉSULTATS, pas
# de données brutes — voir son commentaire) : calculer_contexte_jour_heure_
# sql reste coûteux même après avoir regroupé ses 8 scans en 1 seul (mesuré
# 2026-08-16 sur observations.db réel, sans filtre Gare : ~9,8s -> ~3,6s,
# encore trop lent pour chaque changement d'onglet). Dict séparé de celui
# de la barre de stats (clé différente : pas de limiter_retard ici), mais
# même règle d'invalidation (MAX(poll_time) différent depuis la dernière
# collecte).
_cache_resultats_jour_heure = {"dernier_poll": None, "resultats": {}}


def calculer_contexte_jour_heure_sql_avec_cache(connexion, gare, train, sens, limiter_ligne):
    """Enrobe calculer_contexte_jour_heure_sql d'un cache mémoire des
    résultats déjà calculés pour la combinaison de filtres actuelle — voir
    _cache_resultats_jour_heure ci-dessus."""
    dernier_poll = connexion.execute("SELECT MAX(poll_time) FROM observations").fetchone()[0]
    cache = _cache_resultats_jour_heure
    if cache["dernier_poll"] != dernier_poll:
        cache["dernier_poll"] = dernier_poll
        cache["resultats"] = {}
    cle = (gare, train, sens, limiter_ligne)
    if cle not in cache["resultats"]:
        cache["resultats"][cle] = calculer_contexte_jour_heure_sql(connexion, gare, train, sens, limiter_ligne)
    return cache["resultats"][cle]


def _meilleur_et_pire_creneau(stats, labels, granularite):
    """Calcule les puces 'Meilleur créneau'/'À éviter' de l'écran Tendances
    mobile, à partir des mêmes stats déjà utilisées pour les barres desktop
    (_stats_par_categorie_sql) — ignore les catégories peu fiables
    (n_circulations < SEUIL_FIABLE, même règle que titre_dynamique_jour_
    heure) pour ne jamais mettre en avant une catégorie non représentative.
    Renvoie (meilleur, pire), chacun soit None (aucune catégorie fiable)
    soit {"label": ..., "pct": ...}.

    granularite == "jour" : un seul jour (pas de plage) pour chaque puce.
    granularite == "heure" : 'à éviter' reste une heure unique, mais
    'meilleur créneau' est une fenêtre de 3h glissante (les 3 heures
    consécutives les plus fiables) — reproduit l'asymétrie du mockup
    ('6h-9h' / '23h'), un choix de présentation, pas une nouvelle règle
    statistique. Fenêtres non circulaires (jamais 22h-23h-0h) : la nuit n'a
    pas de service continu, une fenêtre à cheval sur minuit n'aurait pas de
    sens ici. Repli sur le label unique le plus bas si aucune fenêtre n'est
    entièrement fiable."""
    fiables = [
        (label, valeur) for label, valeur, n in zip(labels, stats["pct"], stats["n_circulations"].fillna(0))
        if pd.notna(valeur) and n >= SEUIL_FIABLE
    ]
    if not fiables:
        return None, None
    pire_label, pire_valeur = max(fiables, key=lambda c: c[1])
    pire = {"label": pire_label, "pct": pire_valeur}

    if granularite != "heure" or len(labels) < 3:
        meilleur_label, meilleur_valeur = min(fiables, key=lambda c: c[1])
        return {"label": meilleur_label, "pct": meilleur_valeur}, pire

    fiables_par_label = dict(fiables)
    meilleure_fenetre = None
    for i in range(len(labels) - 2):
        trio = labels[i:i + 3]
        valeurs = [fiables_par_label.get(l) for l in trio]
        if any(v is None for v in valeurs):
            continue
        moyenne = sum(valeurs) / 3
        if meilleure_fenetre is None or moyenne < meilleure_fenetre[1]:
            debut_h = int(trio[0].rstrip("h"))
            meilleure_fenetre = (f"{debut_h}h-{(debut_h + 3) % 24}h", moyenne)
    if meilleure_fenetre is None:
        meilleur_label, meilleur_valeur = min(fiables, key=lambda c: c[1])
        return {"label": meilleur_label, "pct": meilleur_valeur}, pire
    return {"label": meilleure_fenetre[0], "pct": meilleure_fenetre[1]}, pire


def calculer_contexte_tendances_mobile(connexion, request: Request, gare: str):
    """Écran 'Tendances' mobile — mêmes données que l'onglet desktop 'Par
    jour/heure' (_materialiser_jour_heure/_stats_par_categorie_sql), mais
    gare seule (train="Tous", sens="Tous", limiter_ligne toujours True — pas
    de case à cocher sur mobile) et SANS passer par json_pour_script
    (réservé à Plotly) : les barres sont consommées directement par Jinja/
    CSS (pas de graphique Plotly côté mobile, plus léger)."""
    granularite = request.query_params.get("granularite") or "heure"
    connexion.execute("DROP TABLE IF EXISTS temp.jour_heure_filtre")
    try:
        _materialiser_jour_heure(connexion, gare, "Tous", "Tous", True)
        if granularite == "jour":
            stats = _stats_par_categorie_sql(connexion, "jour_semaine", JOURS_ORDRE)
            labels = [j[:3] for j in JOURS_ORDRE]
        else:
            stats = _stats_par_categorie_sql(connexion, "heure_locale", list(range(24)))
            labels = [f"{h}h" for h in range(24)]
    finally:
        connexion.execute("DROP TABLE IF EXISTS temp.jour_heure_filtre")

    barres_pct = _construire_barre(stats, "pct", labels, " %", avec_moyenne=False)
    # hauteur_pct (0-100, relative au max de la série, pas à une échelle
    # 0-100% absolue) + classe_couleur (mêmes seuils que le mockup validé
    # par l'utilisateur, 8/15 % — pas de seuil équivalent déjà défini
    # ailleurs dans l'appli : SEUIL_RETARD_FORT/MOYEN portent sur un retard
    # en minutes par relevé, pas sur une proportion de circulations) :
    # calculés ici plutôt qu'en Jinja, pour garder le template simple/muet.
    valeurs = [b["valeur"] for b in barres_pct["barres"] if b["valeur"] is not None]
    valeur_max = max(valeurs) if valeurs else 0
    for b in barres_pct["barres"]:
        if b["valeur"] is None:
            b["hauteur_pct"] = 0
            b["classe_couleur"] = ""
        else:
            b["hauteur_pct"] = round(100 * b["valeur"] / valeur_max) if valeur_max else 0
            b["classe_couleur"] = "rouge" if b["valeur"] >= 15 else "orange" if b["valeur"] >= 8 else "verte"
    meilleur, pire = _meilleur_et_pire_creneau(stats, labels, granularite)
    return {
        "granularite": granularite,
        "barres_pct": barres_pct,
        "meilleur_creneau": meilleur,
        "pire_creneau": pire,
        "gare_options": GARES_LIGNE_ORDRE,
    }


def _construire_lignes_annulations_mobile(evenements_df, maintenant, jours=7):
    """Annulations récentes (écran 'Alertes' mobile) : mêmes événements
    'trajet_annule' que calculer_contexte_travaux (Perturbations desktop),
    mais bornés aux `jours` derniers jours et dédoublonnés par circulation
    (train, start_date) — le flux peut enregistrer plusieurs événements pour
    la même annulation au fil de la collecte."""
    annules = evenements_df[
        (evenements_df["type"] == "trajet_annule")
        & (evenements_df["poll_time"] >= maintenant - pd.Timedelta(days=jours))
    ].drop_duplicates(subset=["train", "start_date"]).sort_values("poll_time", ascending=False)
    lignes = []
    for _, ligne in annules.iterrows():
        lignes.append({
            "train": format_numero_train(ligne["train"]),
            "trajet": trajet_origine_destination(ligne["trip_id"], reference_donnees["variantes"]),
            "date": _format_start_date(ligne["start_date"]),
        })
    return lignes


def calculer_contexte_alertes_mobile():
    """Écran 'Alertes' mobile — pure réutilisation de charger_alertes/
    calculer_alertes_actives (travaux actifs) et des événements 'trajet_
    annule' (annulations récentes), déjà utilisés par l'onglet desktop
    Perturbations/Rapports. Aucune nouvelle requête SQL."""
    alertes_df = charger_alertes(LOCAL_ALERTES)
    actif, _ = calculer_alertes_actives(alertes_df)
    travaux = _construire_lignes_alertes(alertes_df[actif].sort_values("fin"))

    maintenant = pd.Timestamp.now(tz="UTC")
    evenements_df = charger_evenements(PERTURBATIONS_FILE)
    annulations_recentes = _construire_lignes_annulations_mobile(evenements_df, maintenant)
    return {
        "n_alertes_actives": len(travaux),
        "travaux": travaux,
        "annulations_recentes": annulations_recentes,
    }


def construire_contexte_mobile(request: Request, ecran: str, gare: str):
    """Point d'entrée des routes /mobile — dispatch sur `ecran`, même
    principe que construire_contexte (desktop) sur `vue`, mais un contexte
    totalement séparé : pas de filtres Gare/Train/Sens/Limiter partagés
    avec le desktop (preparer_contexte_commun), l'écran mobile Accueil/
    Favoris n'a même pas la notion de 'Train'/'Sens' avant résolution."""
    contexte = {
        "titre_app": TITRE_APP, "ecran": ecran, "gare": gare,
        "gare_options": gares_options_mobile(reference_donnees["index_gare_heure"]),
    }
    if ecran == "tendances":
        connexion = sqlite3.connect(OBSERVATIONS_DB)
        try:
            contexte.update(calculer_contexte_tendances_mobile(connexion, request, gare))
        finally:
            connexion.close()
    elif ecran == "alertes":
        contexte.update(calculer_contexte_alertes_mobile())
    # "favoris" : rendu entièrement côté client (localStorage), voir mobile.js — Phase 3
    # par défaut ("accueil") : rien à calculer avant que l'utilisateur choisisse gare+heure — Phase 2
    return contexte


def _materialiser_rapport_filtre(connexion, debut_iso, fin_iso):
    """Construit la table temporaire `rapport_filtre` — un seul scan
    d'observations.db pour le périmètre commun à plusieurs morceaux de
    l'onglet Rapports (météo, graphiques "retard moyen par gare"/"par jour
    de semaine"/"par heure") : période + circulations arrivées + restreint
    aux 11 gares de la ligne (limiter_ligne=True, comme df_periode dans
    generer_rapport.py — PAS df_periode_complet, ces usages portent bien
    sur le périmètre restreint à la ligne). L'appelant DOIT avoir déjà
    matérialisé circulations_arrivees_periode (voir _materialiser_
    circulations_arrivees_periode) avant cet appel. ligne_id (= rowid
    d'observations) : sert de départage déterministe à _meteo_periode_sql
    (voir son docstring) pour reproduire exactement l'ordre de lignes que
    pandas trouve "en premier". Appelant responsable du DROP TABLE
    ensuite."""
    where, params = _construire_where_sql(
        "Toutes", "Tous", "Tous", limiter_ligne=True, debut_iso=debut_iso, fin_iso=fin_iso,
    )
    connexion.execute("DROP TABLE IF EXISTS temp.rapport_filtre")
    connexion.execute(
        f"""
        CREATE TEMP TABLE rapport_filtre AS
        SELECT rowid AS ligne_id, poll_time, gare, trip_id, start_date,
               ROUND(COALESCE(arrival_delay_s, departure_delay_s) / 60.0, 1) AS retard_min,
               temperature_c, precipitation_mm, wind_speed_kmh,
               {_EXPR_JOUR_SEMAINE} AS jour_semaine, heure_locale
        FROM observations WHERE {where}
        """,
        params,
    )


def _meteo_periode_sql(connexion):
    """Moyennes température/vent + pluie cumulée sur la période — porte le
    calcul météo de generer_rapport.py (voir son docstring, 2026-07-31) :
    dédoublonnage à 2 niveaux, car la météo (une requête Open-Meteo par
    gare, mise à jour ~1x/heure côté serveur, voir collect_realtime.py)
    est répétée sur chaque ligne de rapport_filtre où elle apparaît (un
    même poll_time+gare peut avoir plusieurs trains, ET un même relevé
    météo horaire peut couvrir plusieurs polls consécutifs à 5 min
    d'intervalle) :
    1) Une seule ligne gardée par (poll_time, gare) — retire les doublons
       dus aux trains, comme drop_duplicates(["poll_time", "gare"]) côté
       pandas. IMPORTANT : ce dédoublonnage doit porter UNIQUEMENT sur
       (poll_time, gare), pas sur le reste de la ligne (ex: DISTINCT sur
       toutes les colonnes) — deux lignes peuvent partager un même
       (poll_time, gare) mais différer sur les colonnes météo elles-mêmes
       (une valeur NULL selon la ligne d'origine), auquel cas DISTINCT
       sur toutes les colonnes les garderait toutes deux, contrairement à
       pandas qui n'en garde qu'une (la première rencontrée). Départagé
       par ligne_id (= rowid, l'ordre naturel d'insertion) ASC, comme le
       fait pandas implicitement (keep="first" sur un DataFrame construit
       via "ORDER BY poll_time" seul, sans second critère de tri — l'ordre
       naturel des lignes à poll_time égal est donc l'ordre d'insertion).
    2) Un seul relevé gardé par (gare, heure civile) : le plus récent de
       cette heure (ROW_NUMBER, même technique que _cte_dernier_par_
       passage) — sinon la pluie (cumul glissant sur la dernière heure
       côté Open-Meteo) serait comptée en double à chaque poll partageant
       la même heure civile (jusqu'à ×11 mesuré en pratique). Aucune
       ambiguïté possible à cette étape : après l'étape 1, poll_time est
       déjà unique par gare, donc unique aussi au sein d'un même (gare,
       heure).
    PIÈGE (trouvé en comparant à generer_rapport.py) : .groupby(["gare",
    "heure"]).last() côté pandas ne prend PAS littéralement la dernière
    ligne du groupe — par défaut, pandas y ignore les NaN et renvoie la
    dernière valeur NON NULLE, colonne par colonne indépendamment (ex: la
    température peut venir d'un poll different de celui qui fournit le
    vent, si le poll le plus récent de l'heure a une valeur météo
    manquante sur une seule des 3 colonnes). D'où les 3 CTE séparées
    ci-dessous plutôt qu'une seule "dernière ligne de l'heure" partagée.
    Suppose rapport_filtre déjà matérialisée (_materialiser_rapport_filtre).
    """
    lignes = connexion.execute(
        """
        WITH dedup_poll AS (
            SELECT poll_time, gare, temperature_c, precipitation_mm, wind_speed_kmh,
                   ROW_NUMBER() OVER (
                       PARTITION BY poll_time, gare ORDER BY ligne_id ASC
                   ) AS rn
            FROM rapport_filtre
        ),
        distinct_poll AS (
            SELECT poll_time, gare, temperature_c, precipitation_mm, wind_speed_kmh
            FROM dedup_poll WHERE rn = 1
        ),
        temp_par_heure AS (
            SELECT temperature_c,
                   ROW_NUMBER() OVER (
                       PARTITION BY gare, strftime('%Y-%m-%d %H', poll_time) ORDER BY poll_time DESC
                   ) AS rn
            FROM distinct_poll WHERE temperature_c IS NOT NULL
        ),
        vent_par_heure AS (
            SELECT wind_speed_kmh,
                   ROW_NUMBER() OVER (
                       PARTITION BY gare, strftime('%Y-%m-%d %H', poll_time) ORDER BY poll_time DESC
                   ) AS rn
            FROM distinct_poll WHERE wind_speed_kmh IS NOT NULL
        ),
        pluie_par_heure AS (
            SELECT precipitation_mm,
                   ROW_NUMBER() OVER (
                       PARTITION BY gare, strftime('%Y-%m-%d %H', poll_time) ORDER BY poll_time DESC
                   ) AS rn
            FROM distinct_poll WHERE precipitation_mm IS NOT NULL
        )
        SELECT
            (SELECT AVG(temperature_c) FROM temp_par_heure WHERE rn = 1),
            (SELECT AVG(wind_speed_kmh) FROM vent_par_heure WHERE rn = 1),
            (SELECT SUM(precipitation_mm) FROM pluie_par_heure WHERE rn = 1)
        """,
    ).fetchone()
    return lignes[0], lignes[1], lignes[2] or 0


def _moyenne_retard_par_categorie_sql(connexion, colonne_categorie, ordre):
    """Moyenne de retard_min + n/n_circulations par catégorie, sur
    rapport_filtre — porte df_periode.groupby(colonne)["retard_min"].mean()
    de generer_rapport.py (graphiques mensuels "retard moyen par gare"/
    "par jour de semaine", colonne_categorie = "gare" ou "jour_semaine").
    Plus simple que _stats_par_categorie_sql (onglet Par jour/heure) : pas
    de calcul de pourcentage en retard ici, generer_rapport.py n'affiche
    que la moyenne pour ces 2 graphiques. n_circulations (circulations
    distinctes, PAS relevés bruts) : utilisé par _construire_barre pour la
    fiabilité, voir SEUIL_FIABLE. Suppose rapport_filtre déjà matérialisée
    (_materialiser_rapport_filtre)."""
    lignes = connexion.execute(
        f"SELECT {colonne_categorie} AS categorie, AVG(retard_min) AS moyenne, COUNT(*) AS n, "
        f"COUNT(DISTINCT trip_id || '|' || start_date) AS n_circulations "
        f"FROM rapport_filtre GROUP BY categorie",
    ).fetchall()
    stats = pd.DataFrame(
        lignes, columns=["categorie", "moyenne", "n", "n_circulations"],
    ).set_index("categorie")
    return stats.reindex(ordre) if ordre is not None else stats


def _construire_jours_periode(debut_local, fin_local):
    """Un jour par entrée, de minuit local (Paris) à minuit local — PAS
    aligné sur la borne 2h de la période elle-même (calculer_periode) :
    repère purement calendaire ("jour par jour"), différent du repère
    "cycle 2h→2h" utilisé pour découper la période globale. Même
    normalize() que generer_rapport.py — inclusive="left" car fin_local
    (minuit du jour suivant le dernier jour complet) ne doit pas devenir
    un jour à part entière."""
    return pd.date_range(debut_local.normalize(), fin_local.normalize(), freq="D", inclusive="left")


def _pct_et_cumule_par_jour_sql(connexion, debut_local, fin_local):
    """(% perturbées, retard cumulé en heures) jour par jour calendaire
    (heure locale Paris) — porte les 2 graphiques mensuels "% perturbées
    par jour"/"retard cumulé croissant" de generer_rapport.py.

    Le bucketing par jour local est DST-sensible (un jour de changement
    d'heure ne fait pas 24h) : SQLite n'a pas de vraie base de fuseaux
    horaires pour le faire correctement en SQL pur. Plutôt que de
    réimplémenter la logique pandas de generer_rapport.py (cle_circulation
    + derniers_par_passage_avec_date, déjà présentes et testées), les
    lignes de rapport_filtre — déjà bornées à la période et aux 11 gares
    de la ligne, jamais la table observations complète — sont rechargées
    dans un petit DataFrame et passées telles quelles à ces mêmes
    fonctions : garantit un résultat identique par construction plutôt que
    par vérification a posteriori d'une 2e implémentation parallèle.
    Suppose rapport_filtre déjà matérialisée (_materialiser_rapport_filtre).
    """
    jours_periode = _construire_jours_periode(debut_local, fin_local)
    lignes = connexion.execute(
        "SELECT poll_time, gare, trip_id, start_date, retard_min FROM rapport_filtre",
    ).fetchall()
    if not lignes:
        pct_par_jour = pd.Series(float("nan"), index=jours_periode, dtype=float)
        cumule_par_jour_h = pd.Series(0.0, index=jours_periode, dtype=float)
        return pct_par_jour, cumule_par_jour_h

    df = pd.DataFrame(lignes, columns=["poll_time", "gare", "trip_id", "start_date", "retard_min"])
    df["poll_time"] = pd.to_datetime(df["poll_time"])

    def _pct_perturbees(g):
        circ = cle_circulation(g)
        tot = circ.nunique()
        return 100 * circ[g["retard_min"] > 0].nunique() / tot if tot else float("nan")

    jour = df["poll_time"].dt.tz_convert(PARIS_TZ).dt.normalize()
    pct_par_jour = df.groupby(jour).apply(_pct_perturbees, include_groups=False)
    pct_par_jour = pct_par_jour.astype(float).reindex(jours_periode)

    derniers_avec_date = derniers_par_passage_avec_date(df)
    derniers_avec_date = derniers_avec_date[derniers_avec_date["retard_min"] > 0]
    jour_dernier = derniers_avec_date["poll_time"].dt.tz_convert(PARIS_TZ).dt.normalize()
    cumule_par_jour_min = derniers_avec_date.groupby(jour_dernier)["retard_min"].sum()
    cumule_par_jour_h = cumule_par_jour_min.reindex(jours_periode, fill_value=0.0).astype(float).cumsum() / 60

    return pct_par_jour, cumule_par_jour_h


def _construire_donnees_top5_sql(connexion, variantes, calendrier):
    """Les 5 circulations les plus retardées de la période (quotidien/
    hebdomadaire, jamais mensuel — voir generer_rapport.py) + leur
    mini-graphique "dernier relevé" (voir calculer_dernier_releve, PAS
    l'Escalier de "Suivi d'un train") — porte la sélection et le rendu du
    Top 5 de generer_rapport.py.

    Sélection : réutilise derniers_complet_periode, déjà matérialisée par
    _materialiser_circulations_arrivees_periode (dernier retard connu par
    (trip_id, start_date, gare), TOUTES gares confondues) — aucun nouveau
    scan d'observations. Restreint à circulations_arrivees_periode comme le
    reste de l'onglet. Classée sur retard_max_ligne (restreint aux 11 gares
    de la ligne), PAS retard_max (toutes gares, gardé pour le titre affiché
    plus bas) : un retard survenu uniquement hors ligne (ex: Coutances,
    Granville) n'a été ressenti par aucun voyageur Paris-Cherbourg, il ne
    doit donc pas, à lui seul, faire entrer une circulation dans le
    classement — même principe que generer_rapport.py, demande explicite de
    l'utilisateur, 2026-08-18.

    Par circulation retenue (au plus 5) : calculer_dernier_releve a besoin
    du trajet complet de CETTE circulation — une petite requête SQL dédiée
    par circulation (quelques dizaines de lignes chacune), plutôt qu'un
    chargement pandas de tout
    l'historique, pour rester dans la même discipline RAM que le reste de
    cet onglet. Les deux tables temporaires ci-dessus DOIVENT déjà être
    matérialisées avant cet appel.

    ORDER BY ... trip_id ASC : 3e clé nécessaire pour reproduire EXACTEMENT
    le tri de generer_rapport.py sur une égalité (retard_max_ligne,
    start_date) — infos.sort_values(["retard_max_ligne","start_date"],
    ascending=[False,False]) est un tri stable sur un DataFrame déjà groupé
    par (trip_id, start_date) croissant (groupby(sort=True), le défaut) :
    pour une égalité totale, le résidu d'ordre préservé est trip_id
    croissant. Sans cette 3e clé, l'ordre SQL des égalités est indéterminé
    et peut differer du rapport PDF sur QUEL train exact occupe le 5e rang —
    repéré en comparant aux vrais résultats sur la période hebdomadaire
    (2 trains à 70 min à la frontière du top 5)."""
    # Classement (ORDER BY) sur retard_max_ligne, restreint aux 11 gares de
    # la ligne — pas sur retard_max (toutes gares, gardé pour l'affichage
    # ci-dessous, titre inclus) : un retard survenu uniquement hors ligne
    # (Coutances, Granville...) n'a été ressenti par aucun voyageur
    # Paris-Cherbourg, il ne doit donc pas, à lui seul, faire entrer une
    # circulation dans le top 5 — même principe que generer_rapport.py
    # (retard_max_ligne_par_circulation), demande explicite de
    # l'utilisateur, 2026-08-18, vérifié sur le rapport hebdomadaire réel :
    # 3 circulations sur 5 changeaient avec ce critère. NULLs (circulation
    # sans aucun retard connu sur la ligne) triés en dernier par SQLite en
    # ORDER BY DESC, même comportement que le sort_values pandas côté PDF.
    gares_placeholders = ",".join("?" * len(GARES_LIGNE_ORDRE))
    lignes = connexion.execute(
        f"""
        SELECT trip_id, start_date, MAX(retard_min) AS retard_max,
               MAX(CASE WHEN gare IN ({gares_placeholders}) THEN retard_min END) AS retard_max_ligne
        FROM derniers_complet_periode
        WHERE (trip_id || '|' || start_date) IN (SELECT cle FROM circulations_arrivees_periode)
        GROUP BY trip_id, start_date
        ORDER BY retard_max_ligne DESC, start_date DESC, trip_id ASC
        LIMIT 5
        """,
        list(GARES_LIGNE_ORDRE),
    ).fetchall()

    top5 = []
    for trip_id, start_date, retard_max, retard_max_ligne in lignes:
        variante = choisir_variante(variantes, calendrier, trip_id, start_date)
        ordre_gares = variante["gares"] if variante else []
        train = trip_id.split(":", 1)[0]
        # Noms complets ("Paris Saint-Lazare → Trouville - Deauville"), pas
        # les codes abrégés de formatting.trajet_sens (menus déroulants) —
        # même trajet_sens que generer_rapport.py (PDF), demande explicite
        # de l'utilisateur, 2026-08-17 : reproduire le même titre que le
        # rapport PDF pour cette section.
        sens = f"{ordre_gares[0]} → {ordre_gares[-1]}" if len(ordre_gares) >= 2 else ""
        entree = {
            "trip_id": trip_id, "start_date": start_date,
            "train": format_numero_train(train), "sens": sens,
            "retard_max": round(float(retard_max), 1) if retard_max is not None else None,
            "retard_max_ligne": round(float(retard_max_ligne), 1) if retard_max_ligne is not None else None,
            "releve": None, "labels": [format_gare(g) for g in ordre_gares],
            # Gares hors des 11 de la ligne (trains de jonction, ex: Rennes,
            # Granville, Rouen) grisées côté JS — même traitement que Suivi
            # d'un train (hors_ligne, calculer_contexte_train) et le rapport
            # PDF (generer_rapport.py, tick.set_color("#999999")), demande
            # explicite de l'utilisateur, 2026-08-17.
            "hors_ligne": [g not in GARES_LIGNE for g in ordre_gares],
        }
        if ordre_gares:
            position_gare = {g: i for i, g in enumerate(ordre_gares)}
            horaires_par_gare = dict(zip(ordre_gares, variante["horaires"]))
            trajet = pd.read_sql_query(
                "SELECT poll_time, gare, "
                "ROUND(COALESCE(arrival_delay_s, departure_delay_s) / 60.0, 1) AS retard_min "
                "FROM observations WHERE trip_id = ? AND start_date = ?",
                connexion, params=(trip_id, start_date),
            )
            entree["releve"] = calculer_dernier_releve(
                trajet, ordre_gares, position_gare, start_date, horaires_par_gare,
            )
        top5.append(entree)
    return top5


def _pct_perturbees_sql(connexion, debut_iso, fin_iso):
    """% de circulations perturbées sur [debut_iso, fin_iso), restreint aux
    11 gares de la ligne et aux circulations arrivées — porte le calcul
    partagé par generer_rapport.stats_perturbees_periode (comparaison au
    mois précédent) ET par le total/en_retard du corps de generer()
    (calculer_stats_globales_sql fait déjà ce calcul pour la période
    COURANTE, mais avec exiger_retard_connu=False comme le reste de la
    barre de stats — ce n'est PAS le même périmètre que df_periode ici,
    qui exige un retard connu via le dropna de filtrer_periode_arrivees,
    d'où cette requête dédiée plutôt qu'une réutilisation directe).
    Suppose circulations_arrivees_periode déjà matérialisée pour la bonne
    fenêtre (voir _materialiser_circulations_arrivees_periode)."""
    where, params = _construire_where_sql(
        "Toutes", "Tous", "Tous", limiter_ligne=True, debut_iso=debut_iso, fin_iso=fin_iso,
    )
    total, en_retard = connexion.execute(
        f"""
        SELECT COUNT(DISTINCT trip_id || '|' || start_date),
               COUNT(DISTINCT CASE WHEN retard_min > 0 THEN trip_id || '|' || start_date END)
        FROM (
            SELECT trip_id, start_date,
                   ROUND(COALESCE(arrival_delay_s, departure_delay_s) / 60.0, 1) AS retard_min
            FROM observations WHERE {where}
        )
        """,
        params,
    ).fetchone()
    return (100 * en_retard / total) if total else None


def _moyenne_mois_precedent_sql(connexion, debut_local):
    """% de circulations perturbées sur le mois précédent, pour la
    comparaison affichée dans le rapport mensuel — None si ce mois n'est
    pas entièrement couvert par la collecte (ex: tout premier rapport
    mensuel, données commencées en cours de mois précédent), comme
    generer_rapport.py. Matérialise circulations_arrivees_periode pour
    cette fenêtre PRÉCÉDENTE — écrase temporairement le contenu de la
    période courante, l'appelant DOIT re-matérialiser pour la période
    courante après cet appel s'il en a encore besoin."""
    debut_mois_precedent = debut_local - pd.DateOffset(months=1)
    debut_mois_precedent_utc = debut_mois_precedent.tz_convert("UTC")
    premiere_donnee = connexion.execute("SELECT MIN(poll_time) FROM observations").fetchone()[0]
    if premiere_donnee is None or pd.Timestamp(premiere_donnee) > debut_mois_precedent_utc:
        return None
    debut_prec_iso = debut_mois_precedent_utc.isoformat()
    fin_prec_iso = debut_local.tz_convert("UTC").isoformat()
    _materialiser_circulations_arrivees_periode(connexion, debut_prec_iso, fin_prec_iso)
    return _pct_perturbees_sql(connexion, debut_prec_iso, fin_prec_iso)


def calculer_contexte_rapport_sql(connexion, nom_periode, maintenant_utc=None):
    """Assemble tout le contenu de l'onglet Rapports pour une période
    donnée ("quotidien"/"hebdomadaire"/"mensuel") — porte generer_rapport.
    generer(), en réutilisant chaque brique déjà construite et vérifiée
    séparément ci-dessus plutôt que de tout recalculer ici. maintenant_utc
    (par défaut l'heure réelle) : paramétrable pour les tests, comme les
    autres fonctions calculer_periode-dépendantes.

    Purge ses 4 tables temporaires dans un bloc finally, y compris en cas
    d'erreur en cours de route — jamais laissées sales pour l'appel
    suivant sur cette même connexion."""
    maintenant_utc = maintenant_utc if maintenant_utc is not None else pd.Timestamp.now(tz="UTC")
    debut_local, fin_local = calculer_periode(nom_periode, maintenant_utc)
    debut_iso = debut_local.tz_convert("UTC").isoformat()
    fin_iso = fin_local.tz_convert("UTC").isoformat()

    contexte = {"nom_periode": nom_periode, "debut_local": debut_local, "fin_local": fin_local}
    try:
        # AVANT tout appel à _materialiser_circulations_arrivees_periode
        # (mois précédent inclus ci-dessous) : cette table ne dépend ni de
        # la période ni du mois précédent/courant, une seule matérialisation
        # suffit pour tout calculer_contexte_rapport_sql.
        _materialiser_circulations_annulees(connexion)

        if nom_periode == "mensuel":
            # AVANT la matérialisation de la période courante ci-dessous
            # (_moyenne_mois_precedent_sql matérialise circulations_
            # arrivees_periode pour le mois PRÉCÉDENT, un contenu qui doit
            # être remplacé par celui de la période courante avant tout
            # calcul dépendant de cette table).
            contexte["moyenne_mois_precedent"] = _moyenne_mois_precedent_sql(connexion, debut_local)

        _materialiser_circulations_arrivees_periode(connexion, debut_iso, fin_iso)
        _materialiser_rapport_filtre(connexion, debut_iso, fin_iso)

        # .update (pas contexte["stats"] = ...) : calculer_stats_globales_sql
        # renvoie déjà un dict avec les clés "stats"/"stats_ratio"/
        # "pct_perturbe"/tooltips au niveau racine — exactement ce
        # qu'attend templates/_stats.html (réutilisé tel quel par l'onglet
        # Rapports, voir le plan) — les nicher sous une seule clé "stats"
        # créerait une collision de nom avec la sous-clé "stats" qu'il
        # contient déjà (heures/minutes/retard_max_texte...).
        contexte.update(calculer_stats_globales_sql(
            connexion, "Toutes", "Tous", "Tous", True, False, debut_iso=debut_iso, fin_iso=fin_iso,
        ))
        temp_moy, vent_moy, pluie_totale = _meteo_periode_sql(connexion)
        contexte["meteo"] = {"temp_moy": temp_moy, "vent_moy": vent_moy, "pluie_totale": pluie_totale}
        contexte["alertes"] = alertes_periode(charger_alertes(LOCAL_ALERTES), debut_local, fin_local)
        contexte["annulations"] = annulations_periode(
            charger_evenements(PERTURBATIONS_FILE), debut_local, fin_local,
            reference_donnees["variantes"], reference_donnees["calendrier"],
        )

        if nom_periode == "mensuel":
            # _pct_perturbees_sql (PAS contexte["pct_perturbe"] ci-dessus,
            # qui vient de calculer_stats_globales_sql avec exiger_retard_
            # connu=False comme le reste de la barre de stats) : même
            # périmètre exact que moyenne_mois_precedent juste en dessous
            # et que generer_rapport.py (dropna implicite de filtrer_
            # periode_arrivees) — audit de nettoyage, 2026-08-17 : les deux
            # valeurs comparées dans le texte de comparaison doivent
            # utiliser exactement le même filtre, sans quoi une différence
            # de couverture (relevé sans aucun retard connu) pourrait un
            # jour rendre la comparaison incohérente.
            contexte["moyenne_mois"] = _pct_perturbees_sql(connexion, debut_iso, fin_iso)
            pct_par_jour, cumule_par_jour_h = _pct_et_cumule_par_jour_sql(connexion, debut_local, fin_local)
            contexte["graph_pct_jour"] = pct_par_jour
            contexte["graph_cumule_jour"] = cumule_par_jour_h
            stats_gare_mois = _moyenne_retard_par_categorie_sql(connexion, "gare", GARES_LIGNE_ORDRE)
            contexte["graph_gare"] = _construire_barre(
                stats_gare_mois, "moyenne", GARES_LIGNE_ORDRE, " min", avec_moyenne=True,
            )
            # Titres dynamiques ("— max : ..."), même principe que l'onglet
            # Par jour/heure (titre_dynamique_jour_heure ci-dessus) —
            # demande explicite de l'utilisateur, 2026-08-20, pour que le
            # rapport mensuel donne la même précision.
            contexte["graph_gare"]["titre"] = titre_dynamique_jour_heure(
                "Retard moyen par gare", stats_gare_mois, "moyenne", GARES_LIGNE_ORDRE, str,
                lambda v: f"{v:.1f} min", SEUIL_FIABLE,
            )
            stats_jour_mois = _moyenne_retard_par_categorie_sql(connexion, "jour_semaine", JOURS_ORDRE)
            contexte["graph_jour_semaine"] = _construire_barre(
                stats_jour_mois, "moyenne", JOURS_ORDRE, " min", avec_moyenne=True,
            )
            contexte["graph_jour_semaine"]["titre"] = titre_dynamique_jour_heure(
                "Retard moyen par jour", stats_jour_mois, "moyenne", JOURS_ORDRE, str.lower,
                lambda v: f"{v:.1f} min", SEUIL_FIABLE,
            )
            # 3e graphique "vue d'ensemble du mois" — même donnée que l'onglet
            # Par jour/heure, aligné sur le rapport mensuel PDF (generer_
            # rapport.py) — demande explicite de l'utilisateur, 2026-08-23.
            labels_heure = [f"{h}h" for h in range(24)]
            stats_heure_mois = _moyenne_retard_par_categorie_sql(connexion, "heure_locale", list(range(24)))
            contexte["graph_heure"] = _construire_barre(
                stats_heure_mois, "moyenne", labels_heure, " min", avec_moyenne=True,
            )
            contexte["graph_heure"]["titre"] = titre_dynamique_jour_heure(
                "Retard moyen par heure", stats_heure_mois, "moyenne", labels_heure, lambda l: f"à {l}",
                lambda v: f"{v:.1f} min", SEUIL_FIABLE,
            )
        else:
            contexte["top5"] = _construire_donnees_top5_sql(
                connexion, reference_donnees["variantes"], reference_donnees["calendrier"],
            )
    finally:
        connexion.execute("DROP TABLE IF EXISTS temp.circulations_arrivees_periode")
        connexion.execute("DROP TABLE IF EXISTS temp.derniers_complet_periode")
        connexion.execute("DROP TABLE IF EXISTS temp.rapport_filtre")
        connexion.execute("DROP TABLE IF EXISTS temp.circulations_annulees")

    return contexte


# Cache des RÉSULTATS déjà calculés (même motif que _cache_resultats_stats_
# globales/_cache_resultats_jour_heure) — clé sur (nom_periode, fin_local)
# EN PLUS de MAX(poll_time) : une période qui vient de "rouler" (ex: passage
# de 2h du matin) doit invalider le cache même si la collecte est à l'arrêt
# et que poll_time n'a pas changé entre-temps.
_cache_resultats_rapport = {"dernier_poll": None, "resultats": {}}


def calculer_contexte_rapport_sql_avec_cache(connexion, nom_periode):
    """Enrobe calculer_contexte_rapport_sql d'un cache mémoire — voir
    _cache_resultats_rapport ci-dessus."""
    maintenant_utc = pd.Timestamp.now(tz="UTC")
    debut_local, fin_local = calculer_periode(nom_periode, maintenant_utc)
    dernier_poll = connexion.execute("SELECT MAX(poll_time) FROM observations").fetchone()[0]
    cache = _cache_resultats_rapport
    if cache["dernier_poll"] != dernier_poll:
        cache["dernier_poll"] = dernier_poll
        cache["resultats"] = {}
    cle = (nom_periode, fin_local)
    if cle not in cache["resultats"]:
        cache["resultats"][cle] = calculer_contexte_rapport_sql(connexion, nom_periode, maintenant_utc)
    return cache["resultats"][cle]


def _construire_lignes_alertes(df, avec_actif=False):
    """Construit les lignes du tableau d'alertes (depuis/jusqu'à formatés,
    repli description -> texte) — partagé entre calculer_contexte_travaux et
    calculer_contexte_rapport_pour_affichage, qui alimentent tous deux
    templates/_table_alertes.html (déjà factorisé côté template lors d'un
    audit de nettoyage, 2026-08-18 ; ce code Python qui construisait chaque
    ligne restait dupliqué à l'identique aux deux endroits, repéré lors d'un
    2e audit, 2026-08-19). avec_actif : ajoute la clé "actif" (Perturbations
    uniquement — le DataFrame doit alors avoir une colonne "_actif")."""
    lignes = []
    for _, ligne in df.iterrows():
        depuis = ligne["debut"].tz_convert(PARIS_TZ).strftime("%d/%m %Hh%M") if pd.notna(ligne["debut"]) else "-"
        jusqua = ligne["fin"].tz_convert(PARIS_TZ).strftime("%d/%m %Hh%M") if pd.notna(ligne["fin"]) else "-"
        entree = {
            "gares": ligne["gares"], "depuis": depuis, "jusqua": jusqua,
            "texte": ligne["texte"], "description": ligne["description"] or ligne["texte"],
        }
        if avec_actif:
            entree["actif"] = bool(ligne["_actif"])
        lignes.append(entree)
    return lignes


def calculer_contexte_rapport_pour_affichage(connexion, nom_periode):
    """Reformate calculer_contexte_rapport_sql_avec_cache pour l'affichage
    (texte, JSON Plotly) — séparé du calcul lui-même pour garder celui-ci
    indépendant de la présentation, même découpage que calculer_escalier/
    calculer_detail (données brutes) vs calculer_contexte_train (labels,
    JSON, texte) juste au-dessus.

    stats/stats_ratio/pct_perturbe/tooltips/format_min_sans_zero (déjà au
    niveau racine de ctx, voir calculer_contexte_rapport_sql) sont
    reportées telles quelles dans le résultat — directement consommables
    par _stats.html. PAS resume_collecte/tooltip_resume_collecte : ces deux
    clés existent aussi dans ctx (période courante), mais préparer_
    contexte_commun a déjà placé dans le contexte de la requête la version
    GLOBALE (depuis le début de la collecte) affichée dans l'en-tête
    (#resume-collecte, base.html, hors de #stats donc jamais masquée sur
    cet onglet) — les reporter ici écraserait ce texte d'en-tête par la
    version bornée à la période, pour toute la durée d'affichage de cet
    onglet. Bug réel trouvé en testant en direct : sans cette exclusion
    explicite, aucune des deux clés n'étant reportée du tout au départ,
    _stats.html affichait silencieusement les stats GLOBALES (celles de
    préparer_contexte_commun, jamais écrasées) au lieu des stats de la
    période choisie."""
    ctx = calculer_contexte_rapport_sql_avec_cache(connexion, nom_periode)

    resultat = {
        cle: valeur for cle, valeur in ctx.items()
        if cle in (
            "stats", "stats_ratio", "pct_perturbe", "format_min_sans_zero",
            "tooltip_ratio_retard", "tooltip_cumule", "tooltip_moyen",
            "tooltip_retard_max", "tooltip_pire_gare", "annulations",
        )
    }
    resultat["rapport_periode_texte"] = texte_periode_rapport(nom_periode, ctx["debut_local"], ctx["fin_local"])

    temp_moy = ctx["meteo"]["temp_moy"]
    if pd.notna(temp_moy):
        resultat["rapport_texte_meteo"] = (
            f"Météo sur la période : {temp_moy:.1f}°C en moyenne · "
            f"{ctx['meteo']['pluie_totale']:.1f} mm de pluie cumulée · "
            f"{ctx['meteo']['vent_moy']:.0f} km/h de vent moyen"
        )
    else:
        resultat["rapport_texte_meteo"] = "Météo : non disponible sur cette période."

    lignes_alertes = _construire_lignes_alertes(ctx["alertes"])
    # "lignes_alertes" (pas "rapport_lignes_alertes") : même nom de clé que
    # calculer_contexte_travaux, pour partager templates/_table_alertes.html
    # (audit de nettoyage, 2026-08-18 — les deux onglets avaient un tableau
    # d'alertes quasi identique, dupliqué). Aucun risque de collision : les
    # deux vues sont mutuellement exclusives, jamais mergées dans le même
    # contexte de requête.
    resultat["lignes_alertes"] = lignes_alertes
    resultat["rapport_texte_alertes"] = (
        f"Travaux / alertes sur la période : {len(lignes_alertes)} alerte(s) active(s) "
        "(détail ci-dessous)." if lignes_alertes else
        "Travaux / alertes sur la période : aucune alerte connue."
    )

    if nom_periode == "mensuel":
        pct_par_jour, cumule_par_jour_h = ctx["graph_pct_jour"], ctx["graph_cumule_jour"]
        resultat.update({
            "rapport_mensuel": True,
            "rapport_moyenne_mois": ctx["moyenne_mois"],
            "rapport_moyenne_mois_precedent": ctx["moyenne_mois_precedent"],
            "rapport_graph_pct_jour_json": json_pour_script({
                "x": [d.strftime("%Y-%m-%d") for d in pct_par_jour.index],
                "y": [None if pd.isna(v) else round(float(v), 1) for v in pct_par_jour],
            }),
            "rapport_graph_cumule_jour_json": json_pour_script({
                "x": [d.strftime("%Y-%m-%d") for d in cumule_par_jour_h.index],
                "y": [round(float(v), 2) for v in cumule_par_jour_h],
            }),
            "rapport_graph_gare_json": json_pour_script(ctx["graph_gare"]),
            "rapport_graph_jour_semaine_json": json_pour_script(ctx["graph_jour_semaine"]),
            "rapport_graph_heure_json": json_pour_script(ctx["graph_heure"]),
        })
    else:
        resultat["rapport_mensuel"] = False
        # Même format de titre que le rapport PDF (generer_rapport.py) —
        # demande explicite de l'utilisateur, 2026-08-17 : "train 3379
        # (Paris Saint-Lazare → Trouville - Deauville) — 16/08/2026 — max
        # 120 min".
        resultat["rapport_top5"] = [
            {
                # Suffixe "(X sur la ligne)" quand le pic toutes gares
                # confondues (retard_max) et celui restreint aux 11 gares de
                # la ligne (retard_max_ligne, même chiffre que le "Retard
                # max" affiché dans la barre de stats) diffèrent une fois
                # arrondis — évite qu'un pic hors ligne (ex: St-Lô) semble
                # contredire le "Retard max" de l'en-tête sans explication,
                # repéré par l'utilisateur sur le rapport hebdomadaire,
                # 2026-08-19.
                "titre": (
                    f"train {e['train']} ({e['sens']}) — {_format_start_date(e['start_date'])} — "
                    f"max {round(e['retard_max'])} min"
                    + (
                        f" ({round(e['retard_max_ligne'])} min sur la ligne)"
                        if e["retard_max_ligne"] is not None
                        and round(e["retard_max_ligne"]) != round(e["retard_max"])
                        else ""
                    )
                ),
                "releve_json": (
                    json_pour_script({"labels": e["labels"], "hors_ligne": e["hors_ligne"], **e["releve"]})
                    if e["releve"] else None
                ),
            }
            for e in ctx["top5"]
        ]
    return resultat


def calculer_contexte_travaux(alertes_df, alertes_actif):
    """Porte _render_travaux_tab (viewer.py: 673-724) : les deux tables de
    l'onglet Perturbations. alertes_df/alertes_actif proviennent déjà de
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
    lignes_alertes = _construire_lignes_alertes(ordre, avec_actif=True)

    evenements_df = charger_evenements(PERTURBATIONS_FILE)
    lignes_evenements = []
    for _, ligne in evenements_df.sort_values("poll_time", ascending=False).iterrows():
        date_str = _format_start_date(ligne["start_date"])
        if ligne["type"] == "trajet_annule":
            # Un trajet annulé n'a pas de gare renseignée (voir
            # perturbations.detecter_evenements : un trip.schedule_relationship
            # = CANCELED ne liste aucun arrêt un par un dans le flux) — la
            # liaison (origine → destination) est dérivée du référentiel via
            # trajet_origine_destination (noms complets, pas trajet_sens et
            # ses codes abrégés type "COUTA" — incohérent avec "Arrêt
            # supprimé : Granville" juste en dessous, repéré par
            # l'utilisateur, 2026-08-19) plutôt que d'un reparsing manuel du
            # trip_id, comme pour la colonne "Sens" du Tableau. Vide si le
            # trajet théorique n'est plus dans le référentiel actuel (repli
            # silencieux déjà géré par trajet_origine_destination elle-même).
            sens = trajet_origine_destination(ligne["trip_id"], reference_donnees["variantes"])
            evenement = f"Trajet annulé (entier) : {sens}" if sens else "Trajet annulé (entier)"
        else:
            evenement = f"Arrêt supprimé : {ligne['gare']}"
        lignes_evenements.append({
            "train": format_numero_train(ligne["train"]), "date": date_str, "evenement": evenement,
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

# Version non ancrée de formatting.RE_NUMERO_TRAIN : ici le numéro apparaît
# au début d'un trip_id complet (ex: "OCESN13100F1187_F:TER:FR:Line::..."),
# pas seul comme le champ "train" que RE_NUMERO_TRAIN attend en entrée
# entière — met juste le numéro en gras, garde le reste de l'identifiant
# intact (utile pour du diagnostic GTFS), plutôt que de le raccourcir comme
# format_numero_train (jugé redondant ici par l'utilisateur, 2026-08-15).
RE_NUMERO_TRAIN_INLINE = re.compile(r"OCESN(\d+)([A-Z]1187_[A-Z])")


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
        echappee = RE_NUMERO_TRAIN_INLINE.sub(r"OCESN<b>\1</b>\2", html.escape(ligne))
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


def construire_options_trajet(df_pour_trajets, df_complet, filtre_jour_retard, circulations_annulees):
    """Porte _update_trajet_list (viewer.py:1089-1150). df_pour_trajets :
    Gare/Train/Sens seulement, SANS "Limiter aux gares de la ligne" (voir
    filtrer_df(..., appliquer_limite_ligne=False), même règle que
    _filtered_df_pour_trajets) — établit quelles circulations proposer.
    df_complet : non filtré du tout — sert à calculer le vrai retard max de
    chaque circulation, y compris sur une gare hors ligne exclue de
    df_pour_trajets. circulations_annulees : ensemble de (trip_id,
    start_date), même construction que construire_lignes_tableau — ajoute
    "[ANNULÉ]" au libellé pour éviter de sélectionner un trajet dont le
    graphique s'arrête net sans explication, demande explicite de
    l'utilisateur, 2026-08-19. Renvoie une liste de (valeur, libellé),
    valeur = "trip_id|start_date" (aucun des deux ne contient jamais "|")."""
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
        label = f"{format_numero_train(row['train'])} du {date_str} (retard max {row['retard_max']:.0f} min)"
        if (trip_id, str(start_date)) in circulations_annulees:
            label += " [ANNULÉ]"
        options.append((f"{trip_id}|{start_date}", label))
    return options


def _construire_points_figes(ordre_gares, position_gare, start_date, horaires_par_gare, dernier_poll, valeur_gare):
    """Factorise la partie commune à calculer_escalier/calculer_dernier_releve
    (audit de nettoyage, 2026-08-17 : ces deux fonctions ne différaient que
    par la source du retard par gare) : pour chaque gare de l'ordre donné,
    récupère son retard via valeur_gare(gare) (None/NaN si non disponible —
    la gare est alors simplement absente du tracé), calcule "figé" par
    comparaison au dernier poll du trajet, puis wrap en {points, runs}
    (voir _runs_json)."""
    points = []
    for gare in ordre_gares:
        retard = valeur_gare(gare)
        if retard is None or pd.isna(retard):
            continue
        passage_estime = estimer_passage_reel(horaires_par_gare.get(gare), start_date, retard)
        fige = passage_estime is not None and passage_estime <= dernier_poll
        points.append((position_gare[gare], round(float(retard), 1), bool(fige)))

    return {
        "points": [{"x": x, "y": y, "fige": f} for x, y, f in points],
        "runs": _runs_json(points),
    }


def calculer_escalier(trajet, ordre_gares, position_gare, start_date, horaires_par_gare):
    """Porte _render_train_escalier (viewer.py:2113-2158) : dernière valeur
    connue par gare, jugée "figée" (déjà passée) ou non par rapport au tout
    dernier relevé du trajet."""
    dernier_poll_trajet = pd.to_datetime(trajet["poll_time"]).max()
    dernieres = trajet.sort_values("poll_time").groupby("gare").last()
    return _construire_points_figes(
        ordre_gares, position_gare, start_date, horaires_par_gare, dernier_poll_trajet,
        lambda gare: dernieres.loc[gare, "retard_min"] if gare in dernieres.index else None,
    )


def calculer_dernier_releve(trajet, ordre_gares, position_gare, start_date, horaires_par_gare):
    """Porte le tracé du Top 5 de generer_rapport.py (rapport PDF) : PAS
    l'Escalier ci-dessus (valeur tenue jusqu'à la gare suivante), mais
    uniquement le TOUT DERNIER relevé de la circulation — un point par gare
    réellement observée à ce relevé précis, reliées par de vraies
    diagonales (pas de palier) ; une gare non observée à ce relevé (poll
    manqué, gare hors ligne jamais interrogée...) n'apparaît simplement
    pas, plutôt que d'être comblée par une valeur d'un relevé plus ancien
    comme le ferait l'Escalier. Demande explicite de l'utilisateur,
    2026-08-17 : le Top 5 web doit reproduire ce rendu, pas celui de
    "Suivi d'un train"."""
    trajet = trajet.copy()
    trajet["poll_time"] = pd.to_datetime(trajet["poll_time"])
    dernier_poll = trajet["poll_time"].max()
    snapshot = trajet[trajet["poll_time"] == dernier_poll]

    def valeur_gare(gare):
        lignes_gare = snapshot[snapshot["gare"] == gare]
        return lignes_gare["retard_min"].iloc[0] if not lignes_gare.empty else None

    return _construire_points_figes(
        ordre_gares, position_gare, start_date, horaires_par_gare, dernier_poll, valeur_gare,
    )


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
    # Même ensemble que le Tableau (construire_lignes_tableau) : sans
    # marqueur, un trajet annulé sélectionné ici affiche un graphique qui
    # s'arrête net sans aucune explication — repéré par l'utilisateur,
    # 2026-08-19.
    evenements_df = charger_evenements(PERTURBATIONS_FILE)
    annules = evenements_df[evenements_df["type"] == "trajet_annule"]
    circulations_annulees = set(zip(annules["trip_id"], annules["start_date"].astype(str)))
    options_trajet = construire_options_trajet(df_pour_trajets, df, filtre_jour_retard, circulations_annulees)

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
        "fenetre_jours": FENETRE_CACHE_JOURS,
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
        "titre": f"Évolution du retard gare par gare — train {dict(options_trajet).get(trajet_choisi, '')}",
        **donnees_vue,
    }
    contexte["donnees_json"] = json_pour_script(donnees)
    return contexte


def construire_lignes_tableau(df_filtre, circulations_annulees, arrets_supprimes):
    """Même construction que app_streamlit.py (300 relevés les plus
    récents, tri stable, ligne vide entre groupes (train, poll_time)
    différents, couleur calculée AVANT le formatage d'affichage).

    circulations_annulees : ensemble de (trip_id, start_date) — un trajet
    entièrement annulé n'a plus aucune ligne fraîche dans observations
    après l'annulation (voir calculer_contexte_travaux), donc son dernier
    relevé connu reste affiché ici tel quel, sans rien qui indique qu'il ne
    circulera pas — juste une prédiction figée, indiscernable d'un train
    "normal" pour qui consulte seulement cet onglet. Badge "ANNULÉ" ajouté
    pour lever l'ambiguïté, sans toucher au code couleur retard existant
    (badge à côté, pas à la place) — repéré par l'utilisateur sur le train
    852610 (Coutances → Caen), 2026-08-19.

    arrets_supprimes : ensemble de (trip_id, start_date, gare) — même
    principe, mais un arrêt supprimé isolé (contrairement à un trajet
    annulé) n'empêche pas les AUTRES gares de la même circulation de
    continuer à être suivies : le badge "Arrêt supprimé" cible donc la
    ligne précise (cette gare-là uniquement), pas tout le train — repéré
    par l'utilisateur sur le train 852820 (Granville), 2026-08-19."""
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
    annule_flags = [
        (tid, str(sd)) in circulations_annulees for tid, sd in zip(recent["trip_id"], recent["start_date"])
    ]
    # AVANT recent["gare"] = recent["gare"].map(format_gare) plus bas :
    # arrets_supprimes est construit sur les noms de gare bruts (stop_names,
    # voir perturbations.detecter_evenements), pas encore abrégés ("Saint-"
    # -> "St-").
    supprime_flags = [
        (tid, str(sd), gare) in arrets_supprimes
        for tid, sd, gare in zip(recent["trip_id"], recent["start_date"], recent["gare"])
    ]
    # arret_ajoute : déjà calculé par préparer_donnees (df["arret_ajoute"]) —
    # pas besoin d'un ensemble séparé, contrairement à annule/supprime (qui
    # viennent de perturbations_detectees.csv, une source externe).
    ajoute_flags = list(recent["arret_ajoute"])

    recent["poll_time"] = recent["poll_time"].map(format_poll_time)
    recent["train"] = recent["train"].map(format_numero_train)
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
            "annule": annule_flags[i],
            "arret_supprime": supprime_flags[i],
            "arret_ajoute": ajoute_flags[i],
        })
    return lignes


MARQUEURS_USER_AGENT_MOBILE = ("iphone", "ipod", "android", "windows phone", "blackberry")


def est_navigateur_mobile(request: Request) -> bool:
    """Détection volontairement simple (sous-chaînes de User-Agent) : usage
    perso/familial, pas de produit public à couvrir exhaustivement — vise les
    téléphones (pas les tablettes, ex: iPad exclu, qui garde le desktop)."""
    ua = request.headers.get("user-agent", "").lower()
    return any(marqueur in ua for marqueur in MARQUEURS_USER_AGENT_MOBILE)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, gare: str = "Toutes", train: str = "Tous", sens: str = "Tous"):
    if est_navigateur_mobile(request):
        return RedirectResponse("/mobile")
    contexte = construire_contexte(request, gare, train, sens)
    return templates.TemplateResponse(request, "base.html", contexte)


@app.get("/contenu", response_class=HTMLResponse)
def contenu(request: Request, gare: str = "Toutes", train: str = "Tous", sens: str = "Tous"):
    contexte = construire_contexte(request, gare, train, sens)
    return templates.TemplateResponse(request, "_contenu_reponse.html", contexte)


@app.get("/mobile", response_class=HTMLResponse)
def mobile_index(request: Request, ecran: str = "accueil", gare: str = GARES_LIGNE_ORDRE[0]):
    contexte = construire_contexte_mobile(request, ecran, gare)
    return templates.TemplateResponse(request, "mobile_base.html", contexte)


@app.get("/mobile/contenu", response_class=HTMLResponse)
def mobile_contenu(request: Request, ecran: str = "accueil", gare: str = GARES_LIGNE_ORDRE[0]):
    contexte = construire_contexte_mobile(request, ecran, gare)
    return templates.TemplateResponse(request, "_mobile_contenu_reponse.html", contexte)


@app.get("/mobile/destinations_gare", response_class=HTMLResponse)
def mobile_destinations_gare(request: Request, gare: str):
    """Fragment <option> pour le <select> 'Gare d'arrivée' de l'écran
    Accueil/Favoris mobile — rechargé à chaque changement de Gare de
    départ."""
    destinations = destinations_disponibles_pour_gare(gare, reference_donnees["index_gare_heure"])
    return templates.TemplateResponse(
        request, "_mobile_options_destination.html", {"destinations": _regrouper_gares_ligne(destinations)},
    )


@app.get("/mobile/heures_trajet", response_class=HTMLResponse)
def mobile_heures_trajet(request: Request, gare: str, destination: str, jour: str):
    """Fragment <option> pour le <select> 'Heure théo.' de l'écran "Mon
    train"/Favoris mobile — rechargé à chaque changement de Gare d'arrivée
    OU de Jour, scopé au trajet (gare, destination) ET au jour de la
    semaine choisis (donc ne mélange plus les horaires de trains partant
    vers d'autres destinations, ni ceux qui ne circulent pas ce jour-là).
    jour : nom français ("Lundi".."Dimanche", voir JOURS_ORDRE), converti
    ici en entier 0-6 pour l'index (voir _jours_semaine_actifs)."""
    heures = heures_disponibles_pour_trajet(
        gare, destination, JOURS_ORDRE.index(jour), reference_donnees["index_gare_heure"],
    )
    return templates.TemplateResponse(request, "_mobile_options_heure.html", {"heures": heures})


@app.get("/mobile/resoudre_train", response_class=HTMLResponse)
def mobile_resoudre_train(
    request: Request, gare: str, heure: str, jour: str, destination: str = "", mode: str = "accueil",
):
    """Fragment de désambiguïsation : liste de candidats (voir
    resoudre_trains_pour_gare_heure) que l'utilisateur clique pour
    confirmer — le clic déclenche ensuite le chargement de la carte stats
    (/mobile/carte_train). jour : voir mobile_heures_trajet ci-dessus."""
    candidats = resoudre_trains_pour_gare_heure(
        gare, heure, JOURS_ORDRE.index(jour), reference_donnees["index_gare_heure"], destination=destination or None,
    )
    return templates.TemplateResponse(
        request, "_mobile_candidats_train.html",
        {"candidats": candidats, "gare": gare, "heure": heure, "jour": jour, "mode": mode},
    )


@app.get("/mobile/carte_train", response_class=HTMLResponse)
def mobile_carte_train(
    request: Request, train: str, gare: str = "Toutes", heure: str = "",
    destination: str = "", mode: str = "accueil", gare_depart: str = "",
):
    """Carte stats réutilisée par "Mon train" (après résolution) ET par
    Favoris (Phase 3, un favori stocke déjà train/gare/heure/destination,
    appelle directement cette route sans repasser par le résolveur).
    mode="favoris" fait apparaître le bouton "Ajouter aux favoris" dans le
    template.

    gare_depart : gare de départ FIXE du trajet (les 2 extrémités,
    gare_depart/destination, ne changent jamais), distincte de `gare`
    (la gare actuellement AFFICHÉE, qui bascule entre les deux via le
    sélecteur "Voir les stats à :") — sans cette distinction, une fois
    basculé sur l'arrivée, plus aucun champ ne garderait le nom de la gare
    de départ pour revenir en arrière. Repli sur `gare` si absent
    (favoris enregistrés avant l'ajout de ce champ, ou tout appelant qui
    ne le connaît pas encore) : gare_depart == gare == la gare affichée à
    ce moment-là, comportement inchangé."""
    connexion = sqlite3.connect(OBSERVATIONS_DB)
    try:
        contexte = calculer_carte_stats_train_sql(connexion, train, gare)
    finally:
        connexion.close()
    contexte.update({
        "train": train, "train_affiche": format_numero_train(train), "gare": gare,
        "heure": heure, "destination": destination, "mode": mode,
        "gare_depart": gare_depart or gare,
    })
    return templates.TemplateResponse(request, "_mobile_carte_stats.html", contexte)


@app.get("/rapports/{periode}/telecharger")
def telecharger_rapport(periode: str):
    """Sert la copie à nom fixe poussée par le Pi (envoyer_rapport_vps_pi.sh)
    — cette appli ne génère jamais elle-même de PDF, voir RAPPORTS_PDF_DIR."""
    if periode not in ("quotidien", "hebdomadaire", "mensuel"):
        raise HTTPException(status_code=404)
    chemin = os.path.join(RAPPORTS_PDF_DIR, f"{periode}.pdf")
    if not os.path.isfile(chemin):
        raise HTTPException(status_code=404)
    return FileResponse(chemin, media_type="application/pdf", filename=f"rapport_{periode}.pdf")


