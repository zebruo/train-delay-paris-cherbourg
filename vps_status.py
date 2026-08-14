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


if __name__ == "__main__":
    import sys
    from config import VPS_HOST
    hote = sys.argv[1] if len(sys.argv) > 1 else VPS_HOST
    print(recuperer_etat_vps(hote))