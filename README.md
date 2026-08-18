# train-delay-paris-cherbourg

Suivi en temps réel des retards sur la ligne SNCF Paris ↔ Cherbourg : collecte
GTFS-RT sur une VPS, interface web (FastAPI + htmx, en ligne sur
[paris-cherbourg.generalbol.fr](https://paris-cherbourg.generalbol.fr)) et
desktop (Tkinter), rapports PDF automatiques et détection des perturbations.

## Architecture

Migration d'un Raspberry Pi vers une VPS (IONOS) terminée : la VPS est la
seule source de collecte depuis le 2026-08-14. Le Pi reste allumé, mais
change de rôle — il ne collecte plus rien lui-même, il sert uniquement de
relais vers le NAS (rapports PDF et sauvegarde d'`observations.db`, voir
plus bas), parce qu'il est sur le même réseau local que le NAS,
contrairement à la VPS (serveur public).

- **VPS** — collecte en continu et héberge l'appli web en production. Cron :
  - `collect_realtime.py` (toutes les 5 min) : interroge le flux GTFS-RT SNCF, écrit dans `observations.db` (SQLite, écritures atomiques, colonnes typées).
  - `collect_alertes.py` (toutes les heures) : perturbations/travaux signalés, écrit `alertes.csv`.
  - `verifier_gtfs.py` (03:15) : compare le référentiel local aux horaires théoriques publiés par la SNCF, détecte quand `reference_paris_cherbourg.csv` devient obsolète.
  - `app_fastapi.py` tourne en continu (`systemd`, `train-delay.service`) derrière nginx + HTTPS (Let's Encrypt), lit directement `observations.db` — pas de rapatriement réseau, la collecte et l'appli sont sur la même machine.
- **Raspberry Pi** — ne collecte plus (`collect_realtime.py`/`collect_alertes.py` retirés de son cron, plus présents du tout sur le Pi depuis le nettoyage du 2026-08-16) ; reste allumé uniquement comme relais vers le NAS (même IP privée que lui, contrairement à la VPS, serveur public) :
  - `executer_rapport_pi.sh` (quotidien 03:30, hebdomadaire 03:35 le lundi, mensuel 03:40 le 1er du mois) rapatrie d'abord `observations.db`/`alertes.csv` depuis la VPS par rsync, génère le rapport (`generer_rapport.py`), puis l'envoie au NAS (`envoyer_rapport_nas_pi.sh`) et pousse une copie à nom fixe (toujours écrasée, pas d'historique) vers la VPS (`envoyer_rapport_vps_pi.sh`) pour le bouton de téléchargement de l'onglet "Rapports" — seule exception au sens de circulation habituel (VPS -> Pi), la VPS ne pouvant pas atteindre le Pi/NAS elle-même.
  - `sauvegarder_observations_nas.sh` (quotidien 03:45, indépendant de la génération de rapport) : sauvegarde datée d'`observations.db` (rapatrié depuis la VPS) vers le NAS, rotation 14 jours.
- **PC** — `viewer.py` (Tkinter) : rapatrie `observations.db` depuis la VPS par rsync (bouton "Rafraîchir depuis la VPS"), même fonctionnalités que l'appli web plus l'onglet "Guide statistiques" et les actions qui écrivent sur la VPS (boutons de l'onglet "Vérification GTFS"). `app_fastapi.py` peut aussi tourner en local sur le PC (utile pour tester une modification avant de la déployer, ou en secours hors-ligne) — il lit alors la copie locale d'`observations.db`, elle aussi rapatriée par `viewer.py`, sans se rafraîchir tout seul.
- **NAS** — destination des rapports PDF, et depuis le 2026-08-15 d'une sauvegarde quotidienne d'`observations.db` (voir `sauvegarder_observations_nas.sh` ci-dessus, 14 jours d'historique glissant). L'ancien historique CSV collecté par le Pi jusqu'au 2026-08-14 (`observations.csv`/`alertes.csv`/`backups/`) reste archivé séparément, figé à cette date (`backup_local.sh`/`backup_to_nas.sh`, retirés du cron, supprimés depuis).

## Fonctionnalités

Communes aux deux interfaces :

- **Tableau** — dernier relevé par gare/train, code couleur (rouge/orange/doré) selon le retard à l'arrivée et au départ.
- **Graphique / Par jour-heure** — vue d'ensemble des retards sur la période.
- **Suivi d'un train** — évolution du retard relevé par relevé pour une circulation donnée.
- **Travaux / Alertes** — perturbations SNCF en cours et passées (annulations, arrêts supprimés).
- **Vérification GTFS** — écart entre le référentiel utilisé par l'appli et les horaires SNCF actuellement publiés (disparus/modifiés/nouveaux/renommés).

Propre à `viewer.py` :

- **Guide statistiques** — explication de chaque statistique et code couleur (le même contenu alimente aussi `guide_statistiques.pdf`, via `generer_guide_statistiques.py`).
- Boutons "Lancer la vérification maintenant", "Régénérer" et "Déployer vers la VPS" sur l'onglet "Vérification GTFS" — absents de la version web, qui reste volontairement en lecture seule tant qu'il n'y a pas d'authentification. "Déployer vers la VPS" envoie le référentiel régénéré (+ `gtfs/stops.txt`) et redémarre le service à distance pour qu'il en tienne compte immédiatement.

Propre à l'appli web :

- **Rapports** — même contenu que les rapports PDF ci-dessous (quotidien/hebdomadaire/mensuel : stats, météo, alertes, Top 5 des circulations les plus perturbées ou graphiques mensuels selon la période), recalculé en direct en SQL plutôt que généré à l'avance — périmètre fixe (11 gares de la ligne, comme le PDF), pas de filtre Gare/Train/Sens. Un bouton télécharge le vrai PDF de la période affichée (dernière version poussée par le Pi, voir plus haut).

Les rapports PDF (`generer_rapport.py`, envoyés au NAS via le Pi) et le
référentiel des trajets (`build_reference.py`, à partir de l'export GTFS
statique national SNCF) tournent indépendamment des deux interfaces.
L'onglet "Rapports" ci-dessus reproduit le même contenu en direct, mais
reste une implémentation SQL séparée (pas de lecture du PDF) — période et
libellés partagés avec `generer_rapport.py` via `formatting.py`
(`calculer_periode`, `texte_periode_rapport`) pour ne pas diverger.

## Installation

```bash
git clone git@github.com:zebruo/train-delay-paris-cherbourg.git
cd train-delay-paris-cherbourg
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # PC (interface complète)
# ou : pip install -r requirements-pi.txt   # Pi (rapports PDF, sans Tkinter/GUI)
```

### Configuration

Deux fichiers de configuration locale, non versionnés (contiennent des IP
privées) — à créer à partir des modèles fournis :

```bash
cp config.example.py config.py
cp config.sh.example config.sh
```

Puis renseigner `PI_HOST`, `VPS_HOST`, `NAS_HOST`, `CHEMIN_DISTANT_PI` et
`CHEMIN_DISTANT_VPS` dans `config.py`, et `NAS_HOST`/`SSH_KEY_NAS`/
`VPS_HOST`/`SSH_KEY_VPS` dans `config.sh` (utilisé par les scripts bash
tournant sur le Pi, notamment `executer_rapport_pi.sh`), avec tes propres
valeurs.

### Référentiel initial

`reference_paris_cherbourg.csv` n'est pas versionné (régénérable). Première
génération :

```bash
python3 build_reference.py
```

## Utilisation

```bash
uvicorn app_fastapi:app        # interface web (VPS en production ; ou PC, en local/test) — http://127.0.0.1:8000
python3 viewer.py              # interface desktop Tkinter (PC)
python3 collect_realtime.py    # collecte (VPS, cron) — écrit observations.db (SQLite)
python3 generer_rapport.py quotidien|hebdomadaire|mensuel
```
