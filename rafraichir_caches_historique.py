"""
Précalcule périodiquement les résultats des onglets desktop les plus
coûteux à calculer en direct : Graphique (période "tout l'historique",
~22-24s mesurés le 2026-08-31) et Par jour/heure (~3,6s mesurés le
2026-08-16), tous deux pour leur combinaison de filtres par défaut
(Gare=Toutes, Train=Tous, Sens=Tous, "Limiter aux gares de la ligne" coché
— celle à l'arrivée sur l'onglet, la version filtrée par gare restant déjà
rapide, ~3,85s) ; et Rapports hebdomadaire/mensuel (~3,7s / 7-19s à froid
mesurés le 2026-08-31 — son cache mémoire existant, _cache_resultats_
rapport, est invalidé à chaque nouveau relevé, donc repasse à froid très
régulièrement ; quotidien reste rapide, ~0,2s, pas concerné ; pas de filtre
Gare/Train/Sens sur cet onglet, un seul cache par période suffit). Écrit
chaque résultat dans sa propre table (une ligne pour Graphique/Jour-heure,
une ligne par période pour Rapports) pour que la route web n'ait plus qu'à
lire au lieu de recalculer.

Réutilise SANS LES MODIFIER calculer_donnees_graphique_historique_sql,
_construire_reponse_graphique, calculer_contexte_jour_heure_sql et
calculer_contexte_rapport_pour_affichage (app_fastapi.py) plutôt que de
réimplémenter ces agrégations ici, pour ne jamais diverger du calcul live
(voir _construire_where_sql/exiger_retard_connu, bug du 2026-08-16).
Importer app_fastapi.py ainsi est sûr : ça ne crée qu'un objet FastAPI
inerte + un montage static/templates (aucun serveur ne démarre) — mais ces
fonctions lisent reference_donnees["variantes"]/["calendrier"], que seul le
lifespan async de l'appli (jamais déclenché par un simple import) peuple
normalement : ce script reproduit ici le même chargement, en n'appelant que
les fonctions synchrones de formatting.py qu'utilise lifespan
(load_reference/build_trip_data/load_calendrier), sans le reste
(stop_names/index_gare_heure, inutiles ici). StaticFiles(directory=
"static") exige par ailleurs que ce dossier soit résolu depuis le cwd
courant : lancer ce script depuis la racine du projet (cron :
`cd ~/train-delay-paris-cherbourg && ...`), comme collect_realtime.py.

À lancer toutes les ~15 min via crontab (VPS), comme les 3 autres scripts
de collecte — cadence ajustable, le coût d'un run (~30-45s pour les 4
caches) reste négligeable à cette fréquence. Les rafraîchissements sont
indépendants : l'échec de l'un n'empêche pas les autres de s'exécuter, mais
le script sort en erreur si au moins un a échoué (visible dans le fichier
de log cron) — la ligne de cache correspondante reste alors inchangée,
sans danger pour l'utilisateur puisque la route retombe sur le calcul live
si elle la trouve trop ancienne (ou périmée par un changement de semaine/
mois, pour Rapports).
"""
import json
import sqlite3
import time

import pandas as pd

from app_fastapi import (
    _construire_reponse_graphique,
    calculer_contexte_jour_heure_sql,
    calculer_contexte_rapport_pour_affichage,
    calculer_donnees_graphique_historique_sql,
    reference_donnees,
)
from formatting import build_trip_data, calculer_periode, load_calendrier, load_reference

OBSERVATIONS_DB = "observations.db"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cache_graphique_historique (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    derniere_maj_iso TEXT NOT NULL,
    contexte_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache_jour_heure_historique (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    derniere_maj_iso TEXT NOT NULL,
    contexte_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache_rapport_historique (
    nom_periode TEXT PRIMARY KEY,
    fin_local_iso TEXT NOT NULL,
    derniere_maj_iso TEXT NOT NULL,
    contexte_json TEXT NOT NULL
);
"""
GARE, TRAIN, SENS, LIMITER_LIGNE = "Toutes", "Tous", "Tous", True


def _ecrire_cache(connexion, table, contexte):
    with connexion:
        connexion.execute(
            f"INSERT OR REPLACE INTO {table} (id, derniere_maj_iso, contexte_json) VALUES (1, ?, ?)",
            (pd.Timestamp.now(tz="UTC").isoformat(), json.dumps(contexte)),
        )


def _ecrire_cache_rapport(connexion, nom_periode, fin_local, contexte):
    with connexion:
        connexion.execute(
            "INSERT OR REPLACE INTO cache_rapport_historique "
            "(nom_periode, fin_local_iso, derniere_maj_iso, contexte_json) VALUES (?, ?, ?, ?)",
            (nom_periode, fin_local.isoformat(), pd.Timestamp.now(tz="UTC").isoformat(), json.dumps(contexte)),
        )


def rafraichir_graphique(connexion):
    resultat = calculer_donnees_graphique_historique_sql(connexion, GARE, TRAIN, SENS, LIMITER_LIGNE)
    if resultat is None:
        contexte = {"graphique_vide": True}
    else:
        moyenne_par_releve, pct_par_releve, nb_releves, stats_periode = resultat
        contexte = _construire_reponse_graphique(
            moyenne_par_releve, pct_par_releve, nb_releves, stats_periode,
            "tout l'historique", GARE, TRAIN, SENS,
        )
    _ecrire_cache(connexion, "cache_graphique_historique", contexte)
    return "vide" if contexte["graphique_vide"] else f"{contexte['nb_releves']} relevés"


def rafraichir_jour_heure(connexion):
    contexte = calculer_contexte_jour_heure_sql(connexion, GARE, TRAIN, SENS, LIMITER_LIGNE)
    _ecrire_cache(connexion, "cache_jour_heure_historique", contexte)
    return "vide" if contexte["jour_heure_vide"] else "ok"


def _rafraichir_rapport(connexion, nom_periode):
    fin_local = calculer_periode(nom_periode, pd.Timestamp.now(tz="UTC"))[1]
    contexte = calculer_contexte_rapport_pour_affichage(connexion, nom_periode)
    # format_min_sans_zero : la fonction elle-même (pas une donnée), non
    # sérialisable en JSON — ré-attachée après lecture du cache, voir
    # _lire_cache_rapport_historique côté app_fastapi.py.
    contexte = {cle: valeur for cle, valeur in contexte.items() if cle != "format_min_sans_zero"}
    _ecrire_cache_rapport(connexion, nom_periode, fin_local, contexte)
    return "ok"


def rafraichir_rapport_hebdomadaire(connexion):
    return _rafraichir_rapport(connexion, "hebdomadaire")


def rafraichir_rapport_mensuel(connexion):
    return _rafraichir_rapport(connexion, "mensuel")


def main():
    reference = load_reference()
    reference_donnees["variantes"] = build_trip_data(reference)
    reference_donnees["calendrier"] = load_calendrier()

    connexion = sqlite3.connect(OBSERVATIONS_DB)
    # collect_realtime.py écrit dans observations.db toutes les 5 min : sans
    # ce délai (défaut sqlite3 : 5s), un INSERT OR REPLACE ci-dessous tombant
    # pile sur son écriture échoue en "database is locked" (constaté au tout
    # premier run cron, 2026-08-31, malgré le décalage d'horaire ci-dessous
    # — les deux cadences peuvent quand même se chevaucher si l'une déborde).
    connexion.execute("PRAGMA busy_timeout = 10000")
    echecs = []
    try:
        connexion.executescript(SCHEMA_SQL)
        taches = (
            ("graphique", rafraichir_graphique),
            ("jour_heure", rafraichir_jour_heure),
            ("rapport_hebdomadaire", rafraichir_rapport_hebdomadaire),
            ("rapport_mensuel", rafraichir_rapport_mensuel),
        )
        for nom, rafraichir in taches:
            debut = time.monotonic()
            try:
                detail = rafraichir(connexion)
            except Exception as exc:
                echecs.append(nom)
                print(f"cache_{nom}_historique : ÉCHEC ({exc}).")
            else:
                duree = time.monotonic() - debut
                horodatage = pd.Timestamp.now(tz="UTC").isoformat()
                print(f"{horodatage} : cache_{nom}_historique mis à jour ({detail}, {duree:.1f}s).")
    finally:
        connexion.close()

    if echecs:
        raise SystemExit(f"Échec du rafraîchissement : {', '.join(echecs)}")


if __name__ == "__main__":
    main()
