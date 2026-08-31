"""
Précalcule périodiquement les résultats des onglets desktop dont la
combinaison de filtres par défaut (Gare=Toutes, Train=Tous, Sens=Tous,
"Limiter aux gares de la ligne" coché — celle à l'arrivée sur l'onglet)
nécessite de scanner la quasi-totalité d'observations.db : Graphique
(période "tout l'historique", ~22-24s mesurés le 2026-08-31) et Par
jour/heure (~3,6s mesurés le 2026-08-16, après un premier passage en SQL —
déjà rapide en absolu, mais encore trop lent pour chaque changement
d'onglet). Écrit chaque résultat dans sa propre table à une seule ligne
(cache_graphique_historique, cache_jour_heure_historique) pour que la route
web n'ait plus qu'à lire au lieu de recalculer. La version filtrée par gare
de ces deux onglets reste rapide (~3,85s pour Graphique) — pas concernée
ici.

Réutilise SANS LES MODIFIER calculer_donnees_graphique_historique_sql,
_construire_reponse_graphique et calculer_contexte_jour_heure_sql
(app_fastapi.py) plutôt que de réimplémenter ces agrégations ici, pour ne
jamais diverger du calcul live (voir _construire_where_sql/
exiger_retard_connu, bug du 2026-08-16). Importer app_fastapi.py ainsi est
sûr : ça ne crée qu'un objet FastAPI inerte + un montage static/templates
(aucun serveur ne démarre) — mais ces deux fonctions lisent
reference_donnees["variantes"]/["calendrier"], que seul le lifespan async
de l'appli (jamais déclenché par un simple import) peuple normalement : ce
script reproduit ici le même chargement, en n'appelant que les fonctions
synchrones de formatting.py qu'utilise lifespan (load_reference/
build_trip_data/load_calendrier), sans le reste (stop_names/
index_gare_heure, inutiles ici). StaticFiles(directory="static") exige par
ailleurs que ce dossier soit résolu depuis le cwd courant : lancer ce
script depuis la racine du projet (cron : `cd ~/train-delay-paris-cherbourg
&& ...`), comme collect_realtime.py.

À lancer toutes les ~15 min via crontab (VPS), comme les 3 autres scripts
de collecte — cadence ajustable, le coût d'un run (~25-30s pour les deux
caches) reste négligeable à cette fréquence. Les deux rafraîchissements
sont indépendants : l'échec de l'un n'empêche pas l'autre de s'exécuter,
mais le script sort en erreur si au moins un des deux a échoué (visible
dans le fichier de log cron) — la ligne de cache correspondante reste alors
inchangée, sans danger pour l'utilisateur puisque la route retombe sur le
calcul live si elle la trouve trop ancienne.
"""
import json
import sqlite3
import time

import pandas as pd

from app_fastapi import (
    _construire_reponse_graphique,
    calculer_contexte_jour_heure_sql,
    calculer_donnees_graphique_historique_sql,
    reference_donnees,
)
from formatting import build_trip_data, load_calendrier, load_reference

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
"""
GARE, TRAIN, SENS, LIMITER_LIGNE = "Toutes", "Tous", "Tous", True


def _ecrire_cache(connexion, table, contexte):
    with connexion:
        connexion.execute(
            f"INSERT OR REPLACE INTO {table} (id, derniere_maj_iso, contexte_json) VALUES (1, ?, ?)",
            (pd.Timestamp.now(tz="UTC").isoformat(), json.dumps(contexte)),
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
        for nom, rafraichir in (("graphique", rafraichir_graphique), ("jour_heure", rafraichir_jour_heure)):
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
