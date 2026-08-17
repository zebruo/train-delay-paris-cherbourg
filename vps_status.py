"""
Récupère un état de santé sommaire de la VPS (espace disque restant,
mémoire utilisée, service train-delay actif ou non) par SSH — aucune
dépendance à Tkinter, testable seul en ligne de commande, importé par
viewer.py pour l'afficher dans la barre du haut (voyant "VPS : ...",
qui affichait l'état matériel du Pi jusqu'au 2026-08-14 — voir
pi_status.py, resté disponible en outil autonome mais plus appelé par
l'appli). Pas de température CPU ici, contrairement à pi_status.py :
une VPS est une VM sans capteur thermique exposé (vcgencmd est propre
au firmware Raspberry Pi, absent), et le vrai point de vigilance de ce
projet côté VPS est la mémoire, pas la chaleur — voir l'incident OOM du
2026-08-13 (mémoire du projet).
"""
import datetime
import re
import subprocess

COMMANDE_DISTANTE = (
    "df -h / | tail -1 | awk '{print $5, $4}'; "
    "free -m | awk '/^Mem:/ {print $3, $2}'; "
    "systemctl is-active train-delay"
)


def recuperer_etat_vps(vps_host, timeout=8):
    """Retourne {"disque_libre_pct", "disque_libre_texte", "mem_utilisee_pct",
    "mem_utilisee_mo", "mem_totale_mo", "service_actif"} en cas de succès, ou
    None si la VPS est injoignable ou la commande échoue — timeout
    volontairement court (par défaut 8s), même logique que pi_status.py :
    ne doit pas bloquer l'UI plus que nécessaire à chaque rafraîchissement
    (voir viewer.py:refresh()), contrairement au rsync des données (60s, un
    vrai transfert de fichier). Pas de "check=True" : "systemctl is-active"
    sort en code 3 quand le service est arrêté (4 si le nom n'existe pas du
    tout — vérifié empiriquement), ce qui ferait lever CalledProcessError
    sur un cas pourtant normal (service arrêté, précisément ce que ce
    voyant doit pouvoir signaler) — la validité de la sortie est vérifiée
    directement ci-dessous plutôt que via le code de retour."""
    try:
        resultat = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", vps_host, COMMANDE_DISTANTE],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    lignes = resultat.stdout.strip().splitlines()
    if len(lignes) < 3:
        return None

    # "2% 112G" (usage % puis espace disponible, voir COMMANDE_DISTANTE) —
    # df donne l'usage, pas le libre directement, d'où le "100 -" ci-dessous.
    match_disque = re.match(r"(\d+)%\s+(\S+)", lignes[0])
    # "2022 3854" (mémoire utilisée puis totale, en Mo, voir free -m).
    match_mem = re.match(r"(\d+)\s+(\d+)", lignes[1])
    if not match_disque or not match_mem:
        return None

    mem_utilisee_mo = int(match_mem.group(1))
    mem_totale_mo = int(match_mem.group(2))

    return {
        "disque_libre_pct": 100 - int(match_disque.group(1)),
        "disque_libre_texte": match_disque.group(2),
        "mem_utilisee_pct": round(100 * mem_utilisee_mo / mem_totale_mo),
        "mem_utilisee_mo": mem_utilisee_mo,
        "mem_totale_mo": mem_totale_mo,
        "service_actif": lignes[2].strip() == "active",
    }


# Bilan complet, à la demande (pas un rafraîchissement UI comme
# recuperer_etat_vps ci-dessus) — une seule connexion SSH, commandes
# regroupées par point-virgule, chaque section précédée d'un marqueur "@nom"
# pour un découpage fiable en sortie (plus robuste qu'un découpage par
# numéro de ligne vu le nombre de sections). journalctl ne nécessite pas
# sudo ici (accès lecture déjà ouvert sur cette VPS) ; ufw/fail2ban/certbot
# si (via NOPASSWD:ALL, voir mémoire du projet — accepté par l'utilisateur).
COMMANDE_BILAN_VPS = r"""
echo @SERVICE
systemctl status train-delay --no-pager | grep 'Active:'
systemctl show train-delay -p MemoryCurrent --value
systemctl show train-delay -p MemoryPeak --value
journalctl -u train-delay --since '24 hours ago' --no-pager | grep -icE 'error|traceback'
echo @RAM
free -m | awk '/^Mem:/ {print $3, $2}'
echo @DISQUE
df -h / | tail -1 | awk '{print $3, $2, $5}'
echo @COLLECTE
cd ~/train-delay-paris-cherbourg && .venv/bin/python3 -c 'import sqlite3; c=sqlite3.connect("observations.db"); print(c.execute("SELECT MAX(poll_time) FROM observations").fetchone()[0])'
echo @DB
du -h ~/train-delay-paris-cherbourg/observations.db 2>/dev/null | awk '{print $1}'
echo @CRON
crontab -l | grep -c '^[^#].*[a-zA-Z]'
echo @SECURITE
sudo ufw status | head -1
sudo fail2ban-client status sshd | grep -E 'Currently banned|Total banned'
echo @HTTPS
sudo certbot certificates 2>&1 | grep 'Expiry Date'
echo @GTFS
cat ~/train-delay-paris-cherbourg/verification_gtfs_etat.json 2>/dev/null | tr '\n' ' '
echo
tail -1 ~/train-delay-paris-cherbourg/verification_gtfs.log 2>/dev/null
"""

# Le Pi n'est plus qu'un relais rapports/NAS depuis le 2026-08-14 (voir
# README.md) — ces 2 fichiers .log suffisent à savoir si ce relais tourne
# toujours normalement, pas besoin d'y répliquer tout ce que fait
# COMMANDE_BILAN_VPS ci-dessus. rapports.log est PARTAGÉ par les cron
# quotidien/hebdomadaire (voir crontab du Pi) : un simple "tail -1" ne
# montre que le dernier écrit, masquant l'autre du jour (ex: hebdomadaire
# écrit après quotidien le lundi) — un grep+préfixe par type, avec un
# "${L:-(aucun)}" pour ne jamais perdre l'alignement même si l'un des 2
# n'a encore aucune correspondance. Mensuel volontairement absent d'un
# bilan pensé pour être consulté au jour le jour — sa cadence (1 fois par
# mois) n'en fait pas un point pertinent à vérifier quotidiennement.
COMMANDE_BILAN_PI = r"""
echo @RAPPORTS
L=$(grep 'Envoyé vers le NAS.*quotidien' ~/train-delay-paris-cherbourg/rapports.log 2>/dev/null | tail -1); echo "quotidien: ${L:-(aucun)}"
L=$(grep 'Envoyé vers le NAS.*hebdomadaire' ~/train-delay-paris-cherbourg/rapports.log 2>/dev/null | tail -1); echo "hebdomadaire: ${L:-(aucun)}"
echo @BACKUP_NAS
tail -1 ~/train-delay-paris-cherbourg/backup_observations.log 2>/dev/null
"""


def _decouper_sections(sortie):
    """{"NOM": ["ligne1", "ligne2", ...]} à partir des marqueurs "@NOM" de
    COMMANDE_BILAN_VPS/COMMANDE_BILAN_PI ci-dessus."""
    sections = {}
    section_courante = None
    for ligne in sortie.splitlines():
        if ligne.startswith("@"):
            section_courante = ligne[1:].strip()
            sections[section_courante] = []
        elif section_courante is not None:
            sections[section_courante].append(ligne)
    return sections


def _executer(hote, commande, timeout):
    try:
        resultat = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", hote, commande],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return resultat.stdout


def generer_bilan(vps_host, pi_host=None, timeout=20):
    """Bilan complet de la VPS (et du relais Pi si pi_host est fourni) —
    reprend les points vérifiés manuellement le 2026-08-17 (service, RAM,
    disque, fraîcheur de la collecte, cron, sécurité, certificat HTTPS,
    fraîcheur du référentiel GTFS, rapports/backup NAS). Renvoie un dict
    brut (pas encore mis en forme, voir formater_bilan) ; une section
    manquante (VPS injoignable, Pi non fourni ou injoignable) donne un dict
    vide pour cette section plutôt qu'une exception — un bilan partiel reste
    utile, mieux qu'aucun bilan du tout."""
    sortie_vps = _executer(vps_host, COMMANDE_BILAN_VPS, timeout)
    sections = _decouper_sections(sortie_vps) if sortie_vps is not None else {}

    if pi_host:
        sortie_pi = _executer(pi_host, COMMANDE_BILAN_PI, timeout)
        sections.update(_decouper_sections(sortie_pi) if sortie_pi is not None else {})

    return sections


def _ligne(section, index=0, defaut=""):
    lignes = [l for l in section if l.strip()] if section else []
    return lignes[index].strip() if index < len(lignes) else defaut


def _ligne_prefixee(section, prefixe, defaut=""):
    """Cherche une ligne "prefixe: valeur" (voir COMMANDE_BILAN_PI, section
    RAPPORTS) plutôt qu'une position fixe — robuste même si l'ordre change
    ou qu'une des 2 lignes (quotidien/hebdomadaire) est absente."""
    for ligne in (section or []):
        if ligne.strip().startswith(f"{prefixe}:"):
            return ligne.split(":", 1)[1].strip()
    return defaut


# Voyants repris du même principe que le bandeau du haut de vps_control.py
# (service actif/arrêté) — appliqués à chaque ligne du bilan, pas seulement
# au service, pour repérer d'un coup d'œil ce qui mérite attention plutôt
# que de devoir lire chaque valeur en détail. "?" (donnée absente/hôte
# injoignable) : volontairement traité comme "à vérifier" (attention), pas
# comme "en échec". "✓" pour OK (demande explicite, 2026-08-17) — "!!"/"XX"
# restent en ASCII pour attention/échec : ✓/⚠/✕ ne s'affichaient pas tous
# dans la zone de texte Tkinter (ScrolledText, Consolas) lors d'un premier
# essai le même jour, alors que ✓ seul s'affiche correctement dans le label
# du bandeau du haut (police/rendu Tkinter différent d'un widget à l'autre,
# et le tag coloré+gras ajouté depuis dans zone_texte peut aussi changer la
# donne) — à surveiller si le même souci réapparaît pour ✓ spécifiquement.
VOYANT_OK, VOYANT_ATTENTION, VOYANT_ECHEC = "✓", "!!", "XX"

# Seuil "attention" pour RAM/disque utilisés — repris tel quel par
# vps_control.py (bandeau du haut, voyant LED) pour rester cohérent avec
# les voyants du bilan complet ci-dessous, plutôt que de dupliquer "80" en
# dur à un 3e endroit.
SEUIL_ATTENTION_PCT = 80


def _service_etat(section):
    # "Active: active (running) since Mon 2026-08-17 00:37:18 CEST; 8h ago"
    # -> "actif — depuis 8h" ; repli sur la ligne brute si le motif attendu
    # de systemctl change un jour.
    ligne = _ligne(section, 0)
    actif = "active (running)" in ligne
    match = re.search(r"since .*?;\s*(.+?)\s*ago\s*$", ligne)
    etat = "actif" if actif else (ligne or "?")
    depuis = f" — depuis {match.group(1)}" if match else ""
    mem_courante = section[1].strip() if len(section) > 1 and section[1].strip().isdigit() else None
    mem_pic = section[2].strip() if len(section) > 2 and section[2].strip().isdigit() else None
    nb_erreurs_brut = section[3].strip() if len(section) > 3 and section[3].strip().isdigit() else None
    ram_texte = (
        f", RAM {round(int(mem_courante) / 1_000_000)}/{round(int(mem_pic) / 1_000_000)} Mo (pic)"
        if mem_courante and mem_pic else ""
    )
    nb_erreurs = int(nb_erreurs_brut) if nb_erreurs_brut is not None else None
    texte = f"{etat}{depuis}{ram_texte}, {nb_erreurs if nb_erreurs is not None else '?'} erreur(s)/24h"
    voyant = VOYANT_OK if actif and nb_erreurs == 0 else (VOYANT_ATTENTION if actif else VOYANT_ECHEC)
    return voyant, texte


def _ram_etat(section):
    valeurs = _ligne(section, 0).split()
    if len(valeurs) != 2:
        return VOYANT_ATTENTION, "?"
    utilisee_mo, totale_mo = int(valeurs[0]), int(valeurs[1])
    pct = round(100 * utilisee_mo / totale_mo)
    voyant = VOYANT_OK if pct < SEUIL_ATTENTION_PCT else VOYANT_ATTENTION
    return voyant, f"{utilisee_mo} Mo / {round(totale_mo / 1000, 1)} Go ({pct} %)"


def _disque_etat(section):
    valeurs = _ligne(section, 0).split()
    if len(valeurs) != 3:
        return VOYANT_ATTENTION, "?"
    pct = int(valeurs[2].rstrip("%"))
    voyant = VOYANT_OK if pct < SEUIL_ATTENTION_PCT else VOYANT_ATTENTION
    return voyant, f"{valeurs[0]} / {valeurs[1]} ({valeurs[2]})"


def _db_etat(section):
    taille = _ligne(section, 0, "?")
    return (VOYANT_OK if taille != "?" else VOYANT_ATTENTION), taille


def _collecte_etat(section):
    # ISO poll_time -> "il y a Xmin"/"il y a Xh Ymin", plus lisible que
    # l'horodatage brut dans un bilan pensé pour être lu d'un coup d'œil.
    # Seuil ⚠ à 15 min : le cron collecte toutes les 5 min, 3 cycles ratés
    # d'affilée mérite d'être signalé plutôt qu'un simple retard ponctuel.
    brut = _ligne(section, 0, "?")
    try:
        dernier = datetime.datetime.fromisoformat(brut)
        ecart = datetime.datetime.now(datetime.timezone.utc) - dernier
        minutes = round(ecart.total_seconds() / 60)
        texte = f"il y a {minutes} min" if minutes < 60 else f"il y a {minutes // 60} h {minutes % 60:02d} min"
        return (VOYANT_OK if minutes < 15 else VOYANT_ATTENTION), texte
    except ValueError:
        return VOYANT_ATTENTION, brut


def _ufw_etat(section):
    # \bactive\b, pas "in": : "Status: inactive" contient la sous-chaîne
    # "active" (dans "in-active") — un simple "in ..." donnerait un faux ✓.
    ufw_ok = bool(re.search(r"\bactive\b", _ligne(section, 0, "").lower()))
    return (VOYANT_OK if ufw_ok else VOYANT_ECHEC), ("actif" if ufw_ok else "INACTIF")


def _fail2ban_etat(section):
    bannis_actuels = re.search(r"Currently banned:\s*(\d+)", _ligne(section, 1))
    bannis_total = re.search(r"Total banned:\s*(\d+)", _ligne(section, 2))
    if not (bannis_actuels and bannis_total):
        return VOYANT_ATTENTION, "?"
    return VOYANT_OK, f"{bannis_actuels.group(1)} banni(s) actuellement ({bannis_total.group(1)} au total)"


def _https_etat(section):
    # "Expiry Date: 2026-11-11 18:53:02+00:00 (VALID: 86 days)"
    match = re.search(r"(\d{4}-\d{2}-\d{2}).*\((VALID|INVALID):\s*(\d+) days?\)", _ligne(section, 0))
    if not match:
        return VOYANT_ATTENTION, _ligne(section, 0, "?")
    date_fr = "/".join(reversed(match.group(1).split("-")))
    jours = int(match.group(3))
    voyant = VOYANT_OK if jours > 14 else VOYANT_ATTENTION
    return voyant, f"valide jusqu'au {date_fr} ({jours} jours)"


def _gtfs_etat(section):
    etat_ligne, log_ligne = _ligne(section, 0), _ligne(section, 1)
    match_ref = re.search(r'"reference":\s*"([\d-]+)"', etat_ligne)
    reference = match_ref.group(1) if match_ref else "?"
    match_ecarts = re.search(r"(\d+) disparus, (\d+) nouveaux", log_ligne)
    if match_ecarts and match_ecarts.group(1) == "0" and match_ecarts.group(2) == "0":
        return VOYANT_OK, f"régularisé — référentiel du {reference}, 0 disparu/nouveau"
    if match_ecarts:
        return (
            VOYANT_ATTENTION,
            f"référentiel du {reference} — {match_ecarts.group(1)} disparu(s), {match_ecarts.group(2)} nouveau(x)",
        )
    return VOYANT_ATTENTION, f"référentiel du {reference}"


def _rapport_etat(section, type_rapport):
    valeur = _ligne_prefixee(section, type_rapport, "(Pi non interrogé)")
    voyant = VOYANT_OK if valeur.startswith("Envoyé vers le NAS") else VOYANT_ATTENTION
    return voyant, valeur


def _backup_etat(section):
    valeur = _ligne(section, 0, "(Pi non interrogé)")
    voyant = VOYANT_OK if valeur.startswith("Sauvegarde envoyée") else VOYANT_ATTENTION
    return voyant, valeur


def lignes_bilan(sections):
    """Sections brutes (generer_bilan) -> [(label, voyant, texte), ...],
    voyant valant VOYANT_OK/VOYANT_ATTENTION/VOYANT_ECHEC — structuré plutôt
    que déjà mis en texte, pour que vps_control.py puisse colorer chaque
    voyant dans le widget Tkinter (voir sa fonction voir_bilan) au lieu de
    n'avoir que le code ASCII "OK"/"!!"/"XX" en texte plat. formater_bilan
    ci-dessous fait le même calcul pour un affichage en ligne de commande,
    sans dépendance à Tkinter. Renvoie [] si le VPS est injoignable."""
    if not sections.get("SERVICE"):
        return []
    rapports = sections.get("RAPPORTS", [])
    return [
        ("Service train-delay", *_service_etat(sections.get("SERVICE", []))),
        ("RAM système", *_ram_etat(sections.get("RAM", []))),
        ("Disque", *_disque_etat(sections.get("DISQUE", []))),
        ("observations.db", *_db_etat(sections.get("DB", []))),
        ("Collecte", *_collecte_etat(sections.get("COLLECTE", []))),
        ("Cron VPS", VOYANT_OK, f"{_ligne(sections.get('CRON', []), 0, '?')} tâche(s) active(s)"),
        ("ufw (pare-feu de la VPS)", *_ufw_etat(sections.get("SECURITE", []))),
        ("fail2ban (surveille les tentatives de connexion SSH)", *_fail2ban_etat(sections.get("SECURITE", []))),
        ("HTTPS", *_https_etat(sections.get("HTTPS", []))),
        ("Référentiel GTFS", *_gtfs_etat(sections.get("GTFS", []))),
        ("Rapport quotidien (Pi)", *_rapport_etat(rapports, "quotidien")),
        ("Rapport hebdomadaire (Pi)", *_rapport_etat(rapports, "hebdomadaire")),
        ("Backup NAS (Pi)", *_backup_etat(sections.get("BACKUP_NAS", []))),
    ]


MESSAGE_BILAN_INJOIGNABLE = "VPS injoignable ou commande de bilan échouée."


def entete_bilan(lignes):
    """(largeur_label, ligne_entete, ligne_separateur) pour le tableau aligné
    (voyant/point/état) — calcul partagé entre formater_bilan ci-dessous
    (texte brut, CLI) et vps_control.py._ecrire_bilan (Tkinter, même
    largeur/en-tête mais insertion ligne par ligne pour colorer chaque
    voyant) : les deux affichaient un en-tête et un séparateur strictement
    identiques, dupliqués mot pour mot avant cette factorisation.
    4 espaces d'alignement en tête = largeur d'un voyant ("✓"/"!!"/"XX",
    complété à 2 caractères via ljust côté appelant même si "✓" seul n'en
    fait qu'un) + les 2 espaces de séparation avant la colonne Point."""
    largeur_label = max(len(label) for label, _, _ in lignes)
    entete = f"    {'Point'.ljust(largeur_label)}  État"
    separateur = f"    {'-' * largeur_label}  {'-' * 60}"
    return largeur_label, entete, separateur


def formater_bilan(sections):
    """lignes_bilan(sections) -> tableau texte aligné (3 colonnes :
    voyant/point/état, police à chasse fixe — voir vps_control.py,
    zone_texte en Consolas), pour un affichage en ligne de commande
    (`python3 vps_status.py bilan`) sans coloration (vps_control.py colore
    lui-même les voyants dans son widget, voir lignes_bilan)."""
    lignes = lignes_bilan(sections)
    if not lignes:
        return MESSAGE_BILAN_INJOIGNABLE

    largeur_label, entete, separateur = entete_bilan(lignes)
    corps = "\n".join(
        f"{voyant.ljust(2)}  {label.ljust(largeur_label)}  {texte}" for label, voyant, texte in lignes
    )
    return f"{entete}\n{separateur}\n{corps}"


if __name__ == "__main__":
    import sys
    from config import PI_HOST, VPS_HOST

    if len(sys.argv) > 1 and sys.argv[1] == "bilan":
        print(formater_bilan(generer_bilan(VPS_HOST, PI_HOST)))
    else:
        hote = sys.argv[1] if len(sys.argv) > 1 else VPS_HOST
        print(recuperer_etat_vps(hote))