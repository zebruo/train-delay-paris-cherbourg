"""Modèle de config.py : copier ce fichier en config.py puis renseigner tes propres
valeurs (non versionné, voir .gitignore)."""

PI_HOST = "utilisateur@ip_du_pi"
NAS_HOST = "utilisateur@ip_du_nas"
VPS_HOST = "utilisateur@ip_de_la_vps"
CHEMIN_DISTANT_PI = "~/train-delay-paris-cherbourg"
CHEMIN_DISTANT_VPS = "~/train-delay-paris-cherbourg"

# Optionnel — utilisé par verifier_gtfs.py (VPS uniquement) pour l'alerte
# SMS quand "Nouveaux" reste > 0 plusieurs jours de suite. Espace Abonné
# Free -> Options -> Notifications par SMS : les deux valeurs (identifiant
# + clé API) sont affichées ensemble sur cette même page.
FREE_MOBILE_USER = ""
FREE_MOBILE_PASS = ""
