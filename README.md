# train-delay-paris-cherbourg

Suivi en temps réel des retards sur la ligne SNCF Paris ↔ Cherbourg : collecte
GTFS-RT sur Raspberry Pi, interface web (FastAPI + htmx) et desktop (Tkinter),
rapports PDF automatiques et détection des perturbations.

## Architecture

- **Raspberry Pi** — collecte en continu, sans interface graphique. Cron :
  - `collect_realtime.py` (toutes les 5 min) : interroge le flux GTFS-RT SNCF, écrit `observations.csv`.
  - `collect_alertes.py` (toutes les heures) : perturbations/travaux signalés, écrit `alertes.csv`.
  - `verifier_gtfs.py` (03:15) : compare le référentiel local aux horaires théoriques publiés par la SNCF, détecte quand `reference_paris_cherbourg.csv` devient obsolète.
  - `backup_local.sh` (03:10) puis `backup_to_nas.sh` (03:20) : sauvegarde locale puis vers le NAS.
  - Rapports PDF quotidien (03:30), hebdomadaire (03:35 le lundi) et mensuel (03:40 le 1er du mois), envoyés au NAS.
- **PC** — deux interfaces de consultation, au choix, qui partagent le même code (`formatting.py`) et rapatrient les données du Pi par SSH/rsync :
  - `app_fastapi.py` — appli web (FastAPI + htmx + Plotly.js), lecture seule.
  - `viewer.py` (Tkinter) — mêmes fonctionnalités, plus l'onglet "Guide statistiques" et les actions qui écrivent sur le Pi (bouton "Déployer vers le Pi" de l'onglet "Vérification GTFS").
- **NAS** — destination des sauvegardes et des rapports PDF.

## Fonctionnalités

Communes aux deux interfaces :

- **Tableau** — dernier relevé par gare/train, code couleur (rouge/orange/doré) selon le retard à l'arrivée et au départ.
- **Graphique / Par jour-heure** — vue d'ensemble des retards sur la période.
- **Suivi d'un train** — évolution du retard relevé par relevé pour une circulation donnée.
- **Travaux / Alertes** — perturbations SNCF en cours et passées (annulations, arrêts supprimés).
- **Vérification GTFS** — écart entre le référentiel utilisé par l'appli et les horaires SNCF actuellement publiés (disparus/modifiés/nouveaux/renommés).

Propre à `viewer.py` :

- **Guide statistiques** — explication de chaque statistique et code couleur (le même contenu alimente aussi `guide_statistiques.pdf`, via `generer_guide_statistiques.py`).
- Boutons "Lancer la vérification maintenant", "Régénérer" et "Déployer vers le Pi" sur l'onglet "Vérification GTFS" — absents de la version web, qui reste volontairement en lecture seule tant qu'il n'y a pas d'authentification.

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

Puis renseigner `PI_HOST`, `NAS_HOST`, `CHEMIN_DISTANT_PI` et
`SSH_KEY_NAS` avec tes propres valeurs.

### Référentiel initial

`reference_paris_cherbourg.csv` n'est pas versionné (régénérable). Première
génération :

```bash
python3 build_reference.py
```

## Utilisation

```bash
uvicorn app_fastapi:app        # interface web (PC), sur http://127.0.0.1:8000
python3 viewer.py              # interface desktop Tkinter (PC)
python3 collect_realtime.py    # collecte (Pi, prévu pour cron)
python3 generer_rapport.py quotidien|hebdomadaire|mensuel
```
