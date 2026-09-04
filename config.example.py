"""Modèle de config.py : copier ce fichier en config.py puis renseigner tes propres
valeurs (non versionné, voir .gitignore)."""

PI4_HOST = "utilisateur@ip_du_pi"
# Optionnel — Pi de secours (voir vps_control.py), n'a de sens que si un 2e
# Raspberry Pi est effectivement présent sur le réseau local.
PI2_HOST = "utilisateur@ip_du_pi_2"
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

# Utilisé par collect_realtime.py pour les données d'observation Météo-
# France (précipitations réelles, réseau RADOME/ETENDU) — repli automatique
# sur Open-Meteo si absent ou en cas d'échec (voir fetch_weather_
# meteofrance). Portail : portail-api.meteofrance.fr -> compte -> "Mes
# APIs" -> souscrire à "Données d'observation" -> "Générer token" (type
# "API Key", durée longue conseillée, ex. 315360000 = 10 ans, le token ne
# se régénère pas tout seul).
METEOFRANCE_API_KEY = ""
