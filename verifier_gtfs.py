"""
Compare quotidiennement la desserte Paris-Cherbourg du GTFS statique
national SNCF actuellement en ligne à celle de reference_paris_cherbourg.csv
(voir build_reference.py), pour repérer quand cette dernière devient
obsolète.

Le Last-Modified du zip n'est pas exploitable comme raccourci (la SNCF
régénère l'export chaque jour, même sans changement substantiel — la fenêtre
glissante de 151 jours avance d'un jour, testé le 2026-08-03) : la
comparaison complète (téléchargement ~5 Mo + lecture de stop_times.txt,
~9 s mesurées) tourne donc tous les jours, ce qui reste largement
acceptable pour un cron quotidien.

Pour éviter de réafficher le même rapport détaillé chaque jour tant que rien
n'a empiré, seule une ligne de suivi courte est loggée si l'écart par
rapport à reference_paris_cherbourg.csv (services disparus + modifiés +
nouveaux + renommés, voir comparer()) n'a pas augmenté depuis la dernière
alerte détaillée — l'écart ne peut que croître tant que la référence n'est
pas régénérée, donc ce seuil mobile suffit à ne réalerter qu'en cas
d'aggravation réelle, et se réinitialise naturellement après une
régénération (l'écart retombe près de zéro, en dessous de tout seuil
précédent).

Ne régénère JAMAIS reference_paris_cherbourg.csv automatiquement : vu
l'ampleur possible des changements (135 services disparus, 43 modifiés
observés le 2026-08-03 après seulement 3 semaines), la régénération doit
rester une décision consciente de l'utilisateur (python3 build_reference.py,
après avoir téléchargé/extrait le nouveau zip dans gtfs/), pas un
remplacement silencieux d'une donnée utilisée dans tout le projet
(collect_realtime.py, viewer.py, generer_rapport.py).

Usage : python verifier_gtfs.py >> verification_gtfs.log 2>&1
Cron quotidien sur la VPS (3h15, heure de Paris depuis le passage du
fuseau système à Europe/Paris) — verification_gtfs.log n'est plus remonté
vers le NAS (backup_local.sh/backup_to_nas.sh, qui faisaient ça à l'époque
du Pi collecteur, retirés le 2026-08-14) ; viewer.py le rapatrie
directement depuis la VPS (bouton "Rafraîchir"). charger_journal() et
lancer_a_distance() sont aussi importés par viewer.py (onglet "Vérification
GTFS") : ce module fait office d'annexe backend pour cet onglet, comme
perturbations.py pour Perturbations — l'UI Tkinter elle-même reste dans
viewer.py.
"""
import csv
import io
import json
import re
import subprocess
import urllib.request
import zipfile
from datetime import datetime

from build_reference import GARES_LIGNE, GTFS_URL
from config import CHEMIN_DISTANT_VPS
from formatting import PARIS_TZ, sans_date_trip_id

META_FILE = "reference_paris_cherbourg.meta.json"
ETAT_FILE = "verification_gtfs_etat.json"
REFERENCE_FILE = "reference_paris_cherbourg.csv"
LOG_FILE = "verification_gtfs.log"


def horodatage():
    # datetime.now(PARIS_TZ) plutôt que datetime.now() (naïf) : ce dernier
    # dépend du fuseau système de la machine qui exécute le cron — correct
    # tant que ça tournait sur le Pi (Europe/Paris), mais silencieusement
    # décalé de 2h (été) depuis le passage à la VPS (Etc/UTC, vérifié) —
    # repéré par l'utilisateur dans l'onglet "Vérification GTFS", 2026-08-14.
    # Les entrées déjà écrites par le Pi restent correctes ; celles déjà
    # écrites par la VPS avant ce correctif restent décalées dans le fichier
    # existant (non retouchées rétroactivement, ambigu de départager les
    # deux sans le savoir déjà).
    return datetime.now(PARIS_TZ).strftime("%Y-%m-%d %H:%M:%S")


def charger_json(chemin):
    try:
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def routes_depuis_zip(zip_bytes):
    """Même logique que build_reference.trips_sur_la_ligne() + le second
    passage de main(), mais lue directement depuis le zip en mémoire (pas de
    dossier temporaire à gérer pour une vérification ponctuelle) — deux
    passages sur stop_times.txt, comme dans build_reference.py, plutôt qu'un
    seul en gardant tout en mémoire (le fichier national fait plusieurs
    dizaines de Mo décompressé). Retourne aussi feed_version (feed_info.txt
    du zip), pour l'afficher dans le log sans requête réseau séparée."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        with z.open("feed_info.txt") as f:
            feed_version = next(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")))["feed_version"]

        with z.open("stop_times.txt") as f:
            gares_par_trajet = {}
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
                code = row["stop_id"].rsplit("-", 1)[-1]
                gare = GARES_LIGNE.get(code)
                if gare:
                    gares_par_trajet.setdefault(row["trip_id"], set()).add(gare)
        trip_ids_retenus = {t for t, g in gares_par_trajet.items() if len(g) >= 2}

        routes = {}
        with z.open("stop_times.txt") as f:
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
                if row["trip_id"] in trip_ids_retenus:
                    routes.setdefault(row["trip_id"], []).append(
                        (int(row["stop_sequence"]), row["stop_id"], row["arrival_time"], row["departure_time"])
                    )
    for tid in routes:
        routes[tid].sort()
    return routes, feed_version


def routes_depuis_reference():
    """Reconstruit la même structure {trip_id: [(seq, stop_id, arr, dep)]}
    que routes_depuis_zip(), à partir de reference_paris_cherbourg.csv déjà
    généré, pour une comparaison homogène."""
    routes = {}
    with open(REFERENCE_FILE, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            routes.setdefault(row["trip_id"], []).append((
                int(row["stop_sequence"]), row["stop_id"],
                row["scheduled_arrival"], row["scheduled_departure"],
            ))
    for tid in routes:
        routes[tid].sort()
    return routes


def comparer(routes_actuelles, routes_nouvelles):
    def grouper(routes):
        g = {}
        for tid, r in routes.items():
            g.setdefault(sans_date_trip_id(tid), []).append((tid, r))
        return g

    def signature(g, cle):
        return tuple((a[1], a[2], a[3]) for a in g[cle][0][1])

    g_anc, g_nouv = grouper(routes_actuelles), grouper(routes_nouvelles)
    disparus = set(g_anc) - set(g_nouv)
    nouveaux = set(g_nouv) - set(g_anc)
    communs = set(g_anc) & set(g_nouv)

    # Rapprochement des "renommés" : la SNCF réattribue parfois le segment
    # ligne/route d'un trip_id à un service par ailleurs inchangé (ex: un
    # car de substitution détaché de la ligne principale vers une route ad
    # hoc, cas OCESN446805R1187_R constaté le 2026-08-12, mêmes arrêts et
    # horaires des deux côtés) — sans_date_trip_id() ne retire que la date
    # finale, donc ce genre de changement fait apparaître le même service
    # comme disparu ET nouveau. Rapprochement strict (mêmes arrêts + mêmes
    # horaires exacts), pas approximatif : un service réellement remplacé
    # par un autre très proche (quelques minutes d'écart, ex: 13111/3344)
    # doit rester compté en disparu+nouveau, pas être noyé ici.
    nouveaux_par_signature = {}
    for cle in nouveaux:
        nouveaux_par_signature.setdefault(signature(g_nouv, cle), []).append(cle)
    renommes = []
    for cle_disp in list(disparus):
        candidats = nouveaux_par_signature.get(signature(g_anc, cle_disp))
        if candidats:
            renommes.append((cle_disp, candidats.pop()))
    for cle_disp, cle_nouv in renommes:
        disparus.discard(cle_disp)
        nouveaux.discard(cle_nouv)

    identiques, exemples_modifies = 0, []
    for s in communs:
        sig_anc = tuple((a[1], a[2], a[3]) for a in g_anc[s][0][1])
        sig_nouv = tuple((a[1], a[2], a[3]) for a in g_nouv[s][0][1])
        if sig_anc == sig_nouv:
            identiques += 1
        elif len(exemples_modifies) < 3:
            exemples_modifies.append(s)

    return {
        "communs": len(communs),
        "identiques": identiques,
        "modifies": len(communs) - identiques,
        "disparus": len(disparus),
        "nouveaux": len(nouveaux),
        "renommes": len(renommes),
        "exemples_modifies": exemples_modifies,
        "exemples_disparus": sorted(disparus)[:3],
        "exemples_nouveaux": sorted(nouveaux)[:3],
        "exemples_renommes": sorted(renommes)[:3],
    }


RE_RESUME = re.compile(
    r"Vérification GTFS \(export du jour : (?P<export>\S+)\) — "
    r"référence datée du (?P<reference>\S+) : (?P<communs>\d+) communs "
    r"\((?P<identiques>\d+) identiques, (?P<modifies>\d+) modifiés\), "
    r"(?P<disparus>\d+) disparus, (?P<nouveaux>\d+) nouveaux"
    # Groupe "renommés" optionnel : absent des entrées de journal écrites
    # avant son introduction (2026-08-12) — sans ce ?, charger_journal()
    # perdrait les champs structurés (communs/identiques/...) de tout
    # l'historique existant, pas seulement des nouvelles entrées.
    r"(?:, (?P<renommes>\d+) renommés)?\."
)


def charger_journal(chemin=LOG_FILE):
    """Parse verification_gtfs.log en une liste d'entrées — une par
    exécution de main(), délimitées par les lignes "[AAAA-MM-JJ HH:MM:SS]
    ...". Le fichier lui-même est écrit en ajout donc chronologique
    croissant ; c'est à l'appelant (viewer.py) d'inverser l'ordre s'il veut
    le plus récent en premier.

    Chaque entrée contient toujours "horodatage" et "texte" (le bloc brut,
    pour affichage détaillé). Si la ligne de résumé a le format attendu
    (RE_RESUME), les champs sont aussi extraits individuellement (communs,
    identiques, modifies, disparus, nouveaux, renommes, export, reference,
    aggrave) — utilisé par viewer.py pour un affichage en tableau. Une
    entrée d'échec (serveur SNCF injoignable) n'a pas ces champs : le
    tableau doit gérer leur absence. "renommes" vaut 0 pour les entrées
    écrites avant son introduction (voir RE_RESUME)."""
    try:
        with open(chemin, encoding="utf-8") as f:
            contenu = f.read()
    except FileNotFoundError:
        return []

    entrees = []
    bloc_courant, horodatage_courant = [], None
    for ligne in contenu.splitlines():
        m = re.match(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", ligne)
        if m:
            if horodatage_courant is not None:
                entrees.append(_construire_entree(horodatage_courant, bloc_courant))
            horodatage_courant, bloc_courant = m.group(1), [ligne]
        elif horodatage_courant is not None:
            bloc_courant.append(ligne)
    if horodatage_courant is not None:
        entrees.append(_construire_entree(horodatage_courant, bloc_courant))
    return entrees


def _construire_entree(horodatage, bloc):
    texte = "\n".join(bloc).strip()
    entree = {"horodatage": horodatage, "texte": texte}
    m = RE_RESUME.search(texte)
    if m:
        entree.update({
            "export": m["export"],
            "reference": m["reference"],
            "communs": int(m["communs"]),
            "identiques": int(m["identiques"]),
            "modifies": int(m["modifies"]),
            "disparus": int(m["disparus"]),
            "nouveaux": int(m["nouveaux"]),
            "renommes": int(m["renommes"]) if m["renommes"] else 0,
            "aggrave": "Exemples de services" in texte,
        })
    return entree


def lancer_a_distance(hote, chemin_distant=CHEMIN_DISTANT_VPS, timeout=30):
    """Exécute verifier_gtfs.py sur le serveur distant par SSH (même
    commande que le cron quotidien, y compris l'ajout au log distant) —
    pour le bouton "Lancer la vérification maintenant" de viewer.py. Ce
    serveur reste la seule source de vérité pour verification_gtfs.log
    (comme pour observations.db) : viewer.py doit re-rapatrier le fichier
    par rsync après cet appel plutôt que d'écrire dans sa propre copie
    locale. hote générique (pas "pi_host") : appelée avec VPS_HOST depuis
    le 2026-08-13 (la VPS remplace le Pi). Retourne True/False."""
    commande = f"cd {chemin_distant} && .venv/bin/python verifier_gtfs.py >> {LOG_FILE} 2>&1"
    try:
        subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", hote, commande],
            capture_output=True, timeout=timeout, check=True,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


def main():
    ancien_version = charger_json(META_FILE).get("feed_version", "inconnue")

    try:
        with urllib.request.urlopen(GTFS_URL, timeout=120) as r:
            zip_bytes = r.read()
    except OSError as e:
        print(f"[{horodatage()}] Vérification GTFS — échec (impossible de contacter le serveur SNCF : {e}).")
        return

    routes_nouvelles, nouvelle_version = routes_depuis_zip(zip_bytes)
    resultat = comparer(routes_depuis_reference(), routes_nouvelles)
    # renommés compte pour 1 (pas 2) dans l'écart : un service juste
    # renommé (mêmes arrêts/horaires, voir comparer()) reste un signal que
    # la référence date un peu, mais un signal plus faible qu'un vrai
    # disparu+nouveau distinct — double-compter gonflerait l'écart pour un
    # seul changement réel.
    ecart = resultat["disparus"] + resultat["modifies"] + resultat["nouveaux"] + resultat["renommes"]

    etat = charger_json(ETAT_FILE)
    dernier_ecart_signale = etat.get("dernier_ecart_signale", -1)
    if etat.get("reference") != ancien_version:
        # La référence a changé depuis la dernière alerte mémorisée (une
        # régénération a eu lieu) : le seuil ne correspond plus à rien de
        # pertinent — sans ce reset, une régénération qui ramène l'écart à 0
        # laisserait quand même le seuil à son ancienne valeur élevée (ex.
        # 207), et il faudrait alors dépasser à nouveau ce chiffre avant la
        # moindre nouvelle alerte, potentiellement des semaines même si
        # "Nouveaux" recommence à monter — repéré par l'utilisateur,
        # 2026-08-03.
        dernier_ecart_signale = -1

    ligne_resume = (
        f"[{horodatage()}] Vérification GTFS (export du jour : {nouvelle_version}) — "
        f"référence datée du {ancien_version} : {resultat['communs']} communs "
        f"({resultat['identiques']} identiques, {resultat['modifies']} modifiés), "
        f"{resultat['disparus']} disparus, {resultat['nouveaux']} nouveaux, "
        f"{resultat['renommes']} renommés."
    )

    if ecart <= dernier_ecart_signale:
        print(f"{ligne_resume} Pas d'aggravation depuis la dernière alerte, "
              f"régénération toujours en attente si tu veux la faire.")
        return

    print(ligne_resume)
    if resultat["exemples_modifies"]:
        print("  Exemples de services modifiés :")
        for s in resultat["exemples_modifies"]:
            print(f"    {s}")
    if resultat["exemples_renommes"]:
        print("  Exemples de services renommés (mêmes arrêts/horaires, identifiant changé) :")
        for ancien, nouveau in resultat["exemples_renommes"]:
            print(f"    {ancien}")
            print(f"    -> {nouveau}")
    if resultat["exemples_disparus"]:
        print("  Exemples de services disparus :")
        for s in resultat["exemples_disparus"]:
            print(f"    {s}")
    if resultat["exemples_nouveaux"]:
        print("  Exemples de services nouveaux :")
        for s in resultat["exemples_nouveaux"]:
            print(f"    {s}")
    print("  Vérifier si une régénération est nécessaire (voir comparaison ci-dessus) : "
          "télécharger le nouveau GTFS puis python3 build_reference.py")
    print()

    with open(ETAT_FILE, "w", encoding="utf-8") as f:
        json.dump({"dernier_ecart_signale": ecart, "reference": ancien_version}, f, indent=2)


if __name__ == "__main__":
    main()
