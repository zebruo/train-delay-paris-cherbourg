"""
Récupère un état de santé sommaire du Raspberry Pi (espace disque restant,
température CPU) par SSH — aucune dépendance à Tkinter, testable seul en
ligne de commande, importé par viewer.py pour l'afficher dans la barre du
haut.
"""
import re
import subprocess

COMMANDE_DISTANTE = "df -h / | tail -1 | awk '{print $5, $4}'; vcgencmd measure_temp"


def recuperer_etat_pi(pi_host, timeout=8):
    """Retourne {"disque_libre_pct", "disque_libre_texte", "cpu_temp"} en cas
    de succès, ou None si le Pi est injoignable ou la commande échoue —
    timeout volontairement court (par défaut 8s) : ne doit pas bloquer l'UI
    plus que nécessaire à chaque rafraîchissement (voir viewer.py:refresh()),
    contrairement au rsync des données (60s, un vrai transfert de fichier)."""
    try:
        resultat = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", pi_host, COMMANDE_DISTANTE],
            capture_output=True, text=True, timeout=timeout, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None

    lignes = resultat.stdout.strip().splitlines()
    if len(lignes) < 2:
        return None

    # "32% 24G" (usage % puis espace disponible, voir COMMANDE_DISTANTE) —
    # df donne l'usage, pas le libre directement, d'où le "100 -" ci-dessous.
    match_disque = re.match(r"(\d+)%\s+(\S+)", lignes[0])
    # "temp=42.3'C" (vcgencmd) — on ne garde que le nombre.
    match_temp = re.search(r"temp=([\d.]+)", lignes[1])
    if not match_disque or not match_temp:
        return None

    return {
        "disque_libre_pct": 100 - int(match_disque.group(1)),
        "disque_libre_texte": match_disque.group(2),
        "cpu_temp": float(match_temp.group(1)),
    }


if __name__ == "__main__":
    import sys
    from config import PI_HOST
    hote = sys.argv[1] if len(sys.argv) > 1 else PI_HOST
    print(recuperer_etat_pi(hote))
