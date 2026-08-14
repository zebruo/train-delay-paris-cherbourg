# train-delay-paris-cherbourg

Suivi en temps réel des retards sur la ligne SNCF Paris ↔ Cherbourg : collecte
GTFS-RT sur une VPS, interface web (FastAPI + htmx, en ligne sur
[paris-cherbourg.generalbol.fr](https://paris-cherbourg.generalbol.fr)) et
desktop (Tkinter), rapports PDF automatiques et détection des perturbations.

## Architecture

Migration d'un Raspberry Pi vers une VPS (IONOS) : la VPS est désormais la
source active (collecte + appli web en production). Le Pi continue de
tourner en parallèle pour l'instant, en filet de sécurité le temps de
confirmer que tout est stable, avant son retrait définitif.

- **VPS** — collecte en continu et héberge l'appli web en production. Cron :
  - `collect_realtime.py` (toutes les 5 min) : interroge le flux GTFS-RT SNCF, écrit dans `observations.db` (SQLite, écritures atomiques, colonnes typées).
  - `collect_alertes.py` (toutes les heures) : perturbations/travaux signalés, écrit `alertes.csv`.
  - `verifier_gtfs.py` (03:15) : compare le référentiel local aux horaires théoriques publiés par la SNCF, détecte quand `reference_paris_cherbourg.csv` devient obsolète.
  - `app_fastapi.py` tourne en continu (`systemd`, `train-delay.service`) derrière nginx + HTTPS (Let's Encrypt), lit directement `observations.db` — pas de rapatriement réseau, la collecte et l'appli sont sur la même machine.
- **Raspberry Pi** — mêmes crons de collecte, encore en CSV (`observations.csv`) — copie du dépôt volontairement pas migrée vers SQLite tant qu'il tourne en parallèle. Sert aussi, pour l'instant, aux rapports PDF et aux sauvegardes NAS (voir plus bas) — pas encore repointés vers la VPS.
  - `backup_local.sh` (03:10) puis `backup_to_nas.sh` (03:20) : sauvegarde locale puis vers le NAS.
  - Rapports PDF quotidien (03:30), hebdomadaire (03:35 le lundi) et mensuel (03:40 le 1er du mois), envoyés au NAS.
- **PC** — `viewer.py` (Tkinter) : rapatrie `observations.db` depuis la VPS par rsync (bouton "Rafraîchir depuis la VPS"), même fonctionnalités que l'appli web plus l'onglet "Guide statistiques" et les actions qui écrivent sur la VPS (boutons de l'onglet "Vérification GTFS"). `app_fastapi.py` peut aussi tourner en local sur le PC (utile pour tester une modification avant de la déployer, ou en secours hors-ligne) — il lit alors la copie locale d'`observations.db`, elle aussi rapatriée par `viewer.py`, sans se rafraîchir tout seul.
- **NAS** — destination des sauvegardes et des rapports PDF (toujours générés depuis le Pi pour l'instant).

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

Les rapports PDF (`generer_rapport.py`) et le référentiel des trajets
(`build_reference.py`, à partir de l'export GTFS statique national SNCF)
tournent indépendamment des deux interfaces.

## Installation

```bash
git clone git@github.com:zebruo/train-delay-paris-cherbourg.git
cd train-delay-paris-cherbourg
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # PC (interface complète)
# ou : pip install -r requirements-pi.txt   # Pi (collecte seule, sans Tkinter/GUI)
```

### Configuration

Deux fichiers de configuration locale, non versionnés (contiennent des IP
privées) — à créer à partir des modèles fournis :

```bash
cp config.example.py config.py
cp config.sh.example config.sh
```

Puis renseigner `PI_HOST`, `VPS_HOST`, `NAS_HOST`, `CHEMIN_DISTANT_PI`,
`CHEMIN_DISTANT_VPS` et `SSH_KEY_NAS` avec tes propres valeurs.

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
