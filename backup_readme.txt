Sauvegardes du projet "Suivi retards Paris-Cherbourg"
======================================================

Ce dossier contient une copie de sécurité de observations.csv et alertes.csv,
les fichiers qui accumulent les relevés de retards de trains et les alertes
SNCF (travaux, incidents...) collectés sur le Raspberry Pi (192.168.1.30)
depuis le 12/07/2026. Ces données ne peuvent pas être retéléchargées après
coup (la SNCF ne fournit aucune archive historique par trajet) : leur perte
serait définitive, d'où cette sauvegarde. Il contient aussi les rapports PDF
générés automatiquement (voir plus bas).

Contenu de ce dossier
----------------------

- observations.csv
    Miroir à jour du fichier actif sur le Pi. Rafraîchi automatiquement
    2 fois par jour (cron sur le Pi, 06h20 et 18h20) — passé d'un rythme
    horaire à ça pour laisser le NAS rester en veille plus longtemps.

- alertes.csv
    Miroir à jour des alertes SNCF (travaux, incidents...) concernant une
    gare de la ligne, collectées sur le Pi toutes les heures. Même rythme de
    synchronisation que observations.csv (06h20 et 18h20).

- backups/observations_AAAAMMJJ.csv, backups/alertes_AAAAMMJJ.csv
    Une copie datée par jour de chaque fichier, créée sur le Pi chaque nuit
    à 03h10, puis envoyée ici dans la même foulée que les miroirs ci-dessus.
    Permet de revenir à un état antérieur si un fichier actif venait à être
    corrompu un jour donné.
    Conservation : 14 jours glissants. Les fichiers plus anciens sont
    supprimés automatiquement sur le Pi (et donc plus renvoyés ici) — si une
    conservation plus longue est un jour nécessaire, il faudra l'organiser
    séparément sur le NAS, rien ne l'empêche ici.

- rapports/rapport_quotidien_AAAAMMJJ.pdf, rapports/rapport_hebdomadaire_AAAAMMJJ.pdf
    Rapport PDF résumant l'état de la ligne (stats clés, évolution du retard
    moyen, état par gare, pires trajets, alertes actives) sur les dernières
    24h ou les derniers 7 jours. Un fichier par génération, jamais écrasé
    (historique conservé, pas de purge automatique ici contrairement à
    backups/).

Origine et automatisation
--------------------------

Deux mécanismes différents alimentent ce dossier :

1) observations.csv / alertes.csv / backups/ : pilotés depuis le Raspberry
   Pi (192.168.1.30), par deux scripts et deux tâches cron :

     backup_local.sh   -> crée les copies datées du jour (03h10, quotidien)
     backup_to_nas.sh  -> envoie tout ici par rsync (06h20 et 18h20, quotidien)

   Les deux scripts se trouvent dans ~/train-delay-paris-cherbourg/ sur le Pi
   (et une copie identique dans le dossier du projet côté PC : voir
   train-delay-paris-cherbourg/backup_local.sh et backup_to_nas.sh).
   Le journal des exécutions est dans backup.log au même endroit sur le Pi.

2) rapports/ : piloté depuis le PC (pas le Pi — matplotlib n'a pas pu être
   installé sur le Pi, la compilation a fait planter la carte faute de RAM),
   par le Planificateur de tâches Windows :

     TrainDelay_RapportQuotidien       -> tous les jours à 07h00
     TrainDelay_RapportHebdomadaire    -> tous les lundis à 07h05

   Chaque tâche lance, via WSL, executer_rapport.sh (dans le dossier du
   projet côté PC), qui génère le PDF avec generer_rapport.py puis l'envoie
   ici avec envoyer_rapport_nas.sh (copie directe vers ce partage réseau,
   déjà accessible depuis Windows, sans passer par le Pi).
   Contrairement aux backups, ce mécanisme suppose que le PC est allumé à
   l'heure prévue — pas de garantie 24/7 comme sur le Pi.

En cas de perte de données sur le Pi
--------------------------------------

1. Récupérer le(s) fichier(s) le(s) plus récent(s) utilisable(s) ici
   (observations.csv/alertes.csv, ou à défaut la dernière sauvegarde datée
   dans backups/).
2. Le(s) replacer sur le Pi dans ~/train-delay-paris-cherbourg/.
3. Relancer la collecte normalement (les cron */5 min et horaire reprendront
   tout seuls).

Au pire, jusqu'à 12h de données sont perdues entre deux passages de
backup_to_nas.sh — le reste de l'historique est protégé. Les rapports PDF ne
sont pas concernés par cette procédure : ils sont recalculés depuis
observations.csv/alertes.csv à chaque génération, rien à restaurer pour eux.
