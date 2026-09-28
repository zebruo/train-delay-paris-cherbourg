"""Helper SSH partagé par les outils d'administration à distance du PC
(pi_status.py, vps_status.py, vps_control.py, verifier_gtfs.py,
build_reference.py) — factorise UNIQUEMENT la construction de la commande
SSH + l'appel subprocess.run, jamais la gestion d'erreur ni le contrat de
retour (audit de nettoyage, 2026-09-28 : le motif subprocess.run(["ssh",
"-o", "BatchMode=yes", "-o", "ConnectTimeout=5", ...]) était recopié à la
main dans 8 fonctions, avec des contrats de retour trop hétérogènes pour
être unifiés sans changer le comportement observable de chacune — None
dans 4 cas, False booléen dans 5 cas, message texte formaté dans 1 cas
unique (vps_control.recuperer_logs_vps, seul endroit qui inspecte
returncode/stderr)."""
import subprocess

SSH_BASE_ARGS = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]


def executer_ssh(hote, commande, timeout, *, check=False, text=True, capture_output=True):
    """Exécute `commande` sur `hote` par SSH avec les options communes aux
    appelants existants. Ne catche rien : CalledProcessError (si
    check=True), TimeoutExpired, OSError remontent tels quels — chaque
    appelant garde son propre try/except et son interprétation du
    résultat (stdout/stderr/returncode) inchangés."""
    return subprocess.run(
        [*SSH_BASE_ARGS, hote, commande],
        capture_output=capture_output, text=text, timeout=timeout, check=check,
    )
