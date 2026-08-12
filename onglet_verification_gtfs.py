"""
Onglet "Vérification GTFS" de viewer.py : tableau des exécutions de
verifier_gtfs.py (backend, voir ce module — la logique de comparaison
elle-même n'est pas dupliquée ici) + bouton pour en déclencher une sur le
Pi.

Extrait de viewer.py dans son propre fichier — comme perturbations.py
sépare déjà la logique métier de Travaux/Alertes, mais ici c'est la
construction/le rendu des widgets Tkinter eux-mêmes qui est extrait, pas
seulement les données. Contrairement à une séparation complète du fichier en
mixins (mise de côté par l'utilisateur en attendant un dépôt git, vu le
risque sur les onglets existants déjà éprouvés), cet onglet est du code
neuf : le placer directement dans son propre fichier ne touche à rien
d'existant — demande explicite de l'utilisateur, 2026-08-03, viewer.py ayant
atteint 2644 lignes.

OngletVerificationGTFSMixin est hérité par App (voir viewer.py :
class App(tk.Tk, OngletVerificationGTFSMixin)) et suppose que App fournit
self.status_var, self._rsync_depuis_pi (méthode statique), self.notebook —
mêmes attributs que ceux utilisés par les onglets définis directement dans
viewer.py.
"""
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox

from build_reference import deployer_vers_pi, lire_feed_version_distante, META_FILE, telecharger_et_regenerer
from tooltips import SimpleTooltip, TreeviewHeaderTooltips
from verifier_gtfs import charger_journal, charger_json, lancer_a_distance, LOG_FILE
from config import PI_HOST

PI_GTFS_LOG_PATH = f"~/train-delay-paris-cherbourg/{LOG_FILE}"


def _format_date_fr(valeur, avec_heure=False):
    """Convertit AAAA-MM-JJ (ou AAAA-MM-JJ HH:MM:SS) en JJ/MM/AAAA (ou
    JJ/MM/AAAA à HH:MM) — même format que le reste de l'application (voir
    formatting.format_poll_time), pour rester cohérent avec les autres
    onglets. Retourne la valeur telle quelle si elle ne correspond pas au
    format attendu, plutôt que de lever une exception pour un simple
    affichage."""
    try:
        if avec_heure:
            return datetime.strptime(valeur, "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y à %H:%M")
        return datetime.strptime(valeur, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return valeur


class OngletVerificationGTFSMixin:
    def _build_verification_gtfs_tab(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=10, pady=10)
        self.bouton_verification_gtfs = ttk.Button(
            top, text="Lancer la vérification maintenant", command=self._lancer_verification_gtfs,
        )
        self.bouton_verification_gtfs.pack(side="left")
        SimpleTooltip(
            self.bouton_verification_gtfs,
            "Déclenche immédiatement une comparaison sur le Pi (au lieu d'attendre le prochain "
            "passage cron, 3h15) — lecture seule, ne modifie rien.",
        )
        # Séparé de "Lancer la vérification" : celui-ci ne fait que comparer
        # (lecture seule), "Régénérer" modifie des fichiers locaux — mieux
        # vaut deux actions distinctes que combiner comparaison et écriture
        # dans un seul bouton.
        self.bouton_regenerer = ttk.Button(
            top, text="Régénérer", command=self._regenerer_reference,
        )
        self.bouton_regenerer.pack(side="left", padx=(10, 0))
        SimpleTooltip(
            self.bouton_regenerer,
            "Télécharge le GTFS national SNCF et reconstruit reference_paris_cherbourg.csv à "
            "partir de cet export — sur ce PC uniquement, sans toucher au Pi.",
        )

        # Premier bouton de l'appli qui écrit vers le Pi (tout le reste ne
        # fait que rapatrier) — d'où la vérification + confirmation avant
        # d'agir, contrairement aux rapatriements silencieux habituels.
        self.bouton_deployer = ttk.Button(
            top, text="Déployer vers le Pi", command=self._deployer_vers_pi,
        )
        self.bouton_deployer.pack(side="left", padx=(10, 0))
        SimpleTooltip(
            self.bouton_deployer,
            "Envoie le référentiel régénéré (reference_paris_cherbourg.csv + .meta.json) vers le "
            "Pi par rsync, après confirmation — la collecte en cours (cron toutes les 5 min) "
            "l'utilisera dès son prochain passage.",
        )

        ttk.Label(
            parent,
            text="Compare quotidiennement (cron sur le Pi, 3h15) la desserte Paris-Cherbourg du GTFS "
                 "SNCF en ligne à reference_paris_cherbourg.csv — n'alerte en détail qu'en cas "
                 "d'aggravation depuis la dernière alerte (voir verifier_gtfs.py). \"Régénérer\" "
                 "télécharge le GTFS national et reconstruit reference_paris_cherbourg.csv "
                 "localement (PC uniquement) — le déploiement vers le Pi (rsync) reste une étape "
                 "manuelle séparée, pour garder la régénération comme une décision consciente.",
            foreground="#555", wraplength=1000, justify="left",
        ).pack(anchor="w", padx=10, pady=(0, 5))

        self.filtre_aggravations_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            parent, text="Afficher seulement les aggravations", variable=self.filtre_aggravations_var,
            command=self._render_verification_gtfs_tab,
        ).pack(anchor="w", padx=10, pady=(0, 10))

        # Treeview (une ligne = une exécution) plutôt qu'un bloc de texte
        # brut : les chiffres (communs/identiques/modifiés/disparus/nouveaux)
        # se comparent bien plus facilement en colonnes qu'en relisant chaque
        # ligne de résumé — demande explicite de l'utilisateur, 2026-08-03.
        colonnes = ("date", "reference", "export", "communs", "identiques", "modifies", "disparus", "nouveaux", "renommes", "statut")
        titres = {
            "date": "Date", "reference": "Référence datée du", "export": "Export SNCF du jour",
            "communs": "Communs", "identiques": "Identiques", "modifies": "Modifiés",
            "disparus": "Disparus", "nouveaux": "Nouveaux", "renommes": "Renommés", "statut": "Statut",
        }
        largeurs = {
            "date": 140, "reference": 130, "export": 130, "communs": 80, "identiques": 80,
            "modifies": 80, "disparus": 80, "nouveaux": 80, "renommes": 80, "statut": 160,
        }
        self.verification_gtfs_tree = ttk.Treeview(parent, columns=colonnes, show="headings")
        for c in colonnes:
            self.verification_gtfs_tree.heading(c, text=titres[c])
            self.verification_gtfs_tree.column(c, width=largeurs[c], anchor="center" if c != "date" else "w")
        self.verification_gtfs_tree.pack(fill="both", expand=True, padx=10, pady=(0, 5))
        # "aggrave" distingue visuellement les lignes avec rapport détaillé
        # (une aggravation a été détectée) des lignes de suivi silencieuses.
        self.verification_gtfs_tree.tag_configure("aggrave", foreground="#c2410c", font=("", 9, "bold"))
        self.verification_gtfs_tree.tag_configure("echec", foreground="#888")

        TreeviewHeaderTooltips(self.verification_gtfs_tree, {
            "date": "Date et heure de cette exécution (tâche quotidienne à 3h15 sur le Pi, ou "
                    "déclenchée manuellement).",
            "reference": "Date de génération du GTFS utilisé pour construire "
                         "reference_paris_cherbourg.csv — le fichier actuellement utilisé par "
                         "l'application.",
            "export": "Date de génération de l'export GTFS SNCF en ligne au moment de cette "
                      "vérification.",
            "communs": "Nombre de trains (desserte Paris-Cherbourg) présents à la fois dans le "
                       "référentiel de l'application et dans l'export SNCF du jour.",
            "identiques": "Parmi les trains communs, ceux dont l'horaire et les arrêts n'ont pas "
                          "changé depuis le référentiel.",
            "modifies": "Parmi les trains communs, ceux dont l'horaire ou la liste d'arrêts a "
                        "changé — le trajet théorique affiché dans l'application peut être "
                        "légèrement faux pour ces trains-là.",
            "disparus": "Trains présents dans le référentiel mais absents de l'export SNCF du jour, "
                        "et pas rattachés à un Renommé (voir cette colonne) — sans impact en soi, "
                        "sauf s'ils sont en réalité toujours en service sous une autre forme non "
                        "détectée automatiquement.",
            "nouveaux": "Trains présents dans l'export SNCF du jour mais absents du référentiel, et "
                        "pas rattachés à un Renommé (voir cette colonne) — ces trains-là ne sont "
                        "actuellement suivis par aucun relevé de l'application. Le chiffre le plus "
                        "important à surveiller.",
            "renommes": "Trains dont seul l'identifiant technique a changé entre le référentiel et "
                        "l'export du jour — mêmes gares, mêmes horaires, même train en réalité "
                        "(ex: un service reclassé par la SNCF sous une autre ligne). Compté à part "
                        "des Disparus/Nouveaux pour ne pas gonfler artificiellement leur nombre — "
                        "l'appli continuera néanmoins d'ignorer ce train tant que le référentiel "
                        "n'est pas régénéré, faute de reconnaître son nouvel identifiant.",
            "statut": "« ⚠ Aggravation » si l'écart (disparus + modifiés + nouveaux + renommés) a "
                      "augmenté depuis la dernière alerte détaillée, « Stable » sinon, « Échec » si "
                      "le serveur SNCF était injoignable ce jour-là.",
        })

        self._verification_gtfs_details = {}
        self.verification_gtfs_tree.bind("<<TreeviewSelect>>", self._on_select_verification_gtfs)

        ttk.Label(parent, text="Détail (sélectionner une ligne) :", font=("", 8, "bold")).pack(
            anchor="w", padx=10, pady=(5, 0),
        )
        self.detail_verification_gtfs_var = tk.StringVar(
            value="Clique sur une ligne pour voir le détail (exemples de services modifiés/disparus).",
        )
        ttk.Label(
            parent, textvariable=self.detail_verification_gtfs_var, foreground="#555",
            wraplength=1000, justify="left", font=("Courier", 8),
        ).pack(anchor="w", padx=10, pady=(0, 10))

    def _on_select_verification_gtfs(self, event=None):
        selection = self.verification_gtfs_tree.selection()
        if not selection:
            return
        self.detail_verification_gtfs_var.set(self._verification_gtfs_details.get(selection[0], ""))

    def _render_verification_gtfs_tab(self):
        self.verification_gtfs_tree.delete(*self.verification_gtfs_tree.get_children())
        self._verification_gtfs_details = {}
        entrees = charger_journal(LOG_FILE)

        if self.filtre_aggravations_var.get():
            # Une entrée d'échec (pas de champ "communs") reste affichée même
            # filtrée : ce n'est pas une ligne "Stable" silencieuse, c'est un
            # problème (serveur SNCF injoignable) tout aussi digne d'attention
            # qu'une aggravation.
            entrees = [e for e in entrees if e.get("aggrave") or "communs" not in e]

        if not entrees:
            message = (
                "Aucune aggravation dans l'historique — décoche le filtre pour voir toutes les "
                "vérifications." if self.filtre_aggravations_var.get() else
                "Aucune vérification enregistrée pour l'instant — clique sur "
                "\"Lancer la vérification maintenant\" ou attends le prochain passage cron (3h15)."
            )
            self.detail_verification_gtfs_var.set(message)
            return

        # Plus récent en premier — le fichier est écrit en ajout donc
        # chronologique croissant, même logique que pour les alertes/
        # événements des autres onglets.
        for e in reversed(entrees):
            date_affichee = _format_date_fr(e["horodatage"], avec_heure=True)
            if "communs" not in e:
                # Entrée d'échec (serveur SNCF injoignable) : pas de chiffres
                # à afficher, juste le texte brut en détail.
                item = self.verification_gtfs_tree.insert(
                    "", "end", values=(date_affichee, "-", "-", "-", "-", "-", "-", "-", "-", "Échec"),
                    tags=("echec",),
                )
                self._verification_gtfs_details[item] = e["texte"]
                continue
            statut = "⚠ Aggravation" if e["aggrave"] else "Stable"
            item = self.verification_gtfs_tree.insert(
                "", "end",
                values=(
                    date_affichee, _format_date_fr(e["reference"]), _format_date_fr(e["export"]),
                    e["communs"], e["identiques"], e["modifies"], e["disparus"], e["nouveaux"],
                    e["renommes"], statut,
                ),
                tags=("aggrave",) if e["aggrave"] else (),
            )
            self._verification_gtfs_details[item] = e["texte"]

        # Ligne la plus récente sélectionnée par défaut, pour ne pas laisser
        # le panneau de détail vide au premier affichage de l'onglet.
        premiere = self.verification_gtfs_tree.get_children()[0]
        self.verification_gtfs_tree.selection_set(premiere)
        self.detail_verification_gtfs_var.set(self._verification_gtfs_details[premiere])

    def _lancer_verification_gtfs(self):
        self.bouton_verification_gtfs.config(state="disabled")
        self.status_var.set("Vérification GTFS en cours sur le Pi (une quinzaine de secondes)...")
        self.update_idletasks()
        try:
            ok = lancer_a_distance(PI_HOST)
            if ok:
                try:
                    self._rsync_depuis_pi(PI_GTFS_LOG_PATH, LOG_FILE)
                except Exception:
                    pass  # Le script a tourné sur le Pi même si le rapatriement échoue ; on affichera l'ancien contenu.
                self._render_verification_gtfs_tab()
                self.status_var.set("Vérification GTFS terminée.")
            else:
                messagebox.showerror(
                    "Erreur",
                    "Impossible de lancer la vérification sur le Pi (connexion SSH, ou verifier_gtfs.py en échec).",
                )
                self.status_var.set("Échec de la vérification GTFS.")
        finally:
            self.bouton_verification_gtfs.config(state="normal")

    def _regenerer_reference(self):
        # Confirmation avant d'agir : contrairement à "Lancer la
        # vérification" (lecture seule), ce bouton remplace gtfs/ et
        # reference_paris_cherbourg.csv sur le PC — pas destructeur pour la
        # collecte en cours (le Pi garde l'ancienne référence tant qu'elle
        # n'est pas déployée à la main), mais irréversible localement.
        if not messagebox.askyesno(
            "Régénérer la référence",
            "Télécharge le GTFS national SNCF (~5 Mo) et reconstruit reference_paris_cherbourg.csv "
            "à partir de ce nouvel export — sur ce PC uniquement.\n\n"
            "Le Pi (collecte en cours) continuera d'utiliser l'ancienne référence tant que tu ne "
            "l'auras pas déployée toi-même (rsync).\n\nContinuer ?",
        ):
            return

        self.bouton_regenerer.config(state="disabled")
        self.status_var.set("Régénération en cours (téléchargement + reconstruction, ~15 s)...")
        self.update_idletasks()
        try:
            telecharger_et_regenerer()
            messagebox.showinfo(
                "Régénération terminée",
                "reference_paris_cherbourg.csv et reference_paris_cherbourg.meta.json ont été "
                "régénérés sur ce PC.\n\nPas encore déployé sur le Pi — la collecte en cours utilise "
                "toujours l'ancienne référence tant que tu ne cliques pas sur \"Déployer vers le Pi\".",
            )
            self.status_var.set("Référence régénérée localement (PC). Déploiement vers le Pi non fait.")
        except Exception as exc:
            messagebox.showerror("Erreur", f"Échec de la régénération :\n{exc}")
            self.status_var.set("Échec de la régénération.")
        finally:
            self.bouton_regenerer.config(state="normal")

    def _deployer_vers_pi(self):
        version_locale = charger_json(META_FILE).get("feed_version", "inconnue")

        self.status_var.set("Vérification de la version actuellement sur le Pi...")
        self.update_idletasks()
        version_pi = lire_feed_version_distante(PI_HOST)

        if version_pi == version_locale:
            comparaison = (
                f"Le Pi est déjà sur la même version ({version_pi}) — rien de nouveau à envoyer."
            )
        else:
            comparaison = (
                f"Référentiel actuellement sur le Pi : {version_pi or 'inconnu (jamais déployé ?)'}\n"
                f"Référentiel local à déployer : {version_locale}"
            )
        if not messagebox.askyesno(
            "Déployer vers le Pi",
            f"{comparaison}\n\nEnvoyer reference_paris_cherbourg.csv et "
            f"reference_paris_cherbourg.meta.json vers le Pi ? La collecte en cours (cron toutes "
            f"les 5 min) utilisera ce nouveau référentiel dès son prochain passage.",
        ):
            self.status_var.set("Déploiement annulé.")
            return

        self.bouton_deployer.config(state="disabled")
        self.status_var.set("Déploiement vers le Pi en cours...")
        self.update_idletasks()
        try:
            if not deployer_vers_pi(PI_HOST):
                messagebox.showerror("Erreur", "Échec de l'envoi vers le Pi (rsync).")
                self.status_var.set("Échec du déploiement vers le Pi.")
                return

            # Relance verifier_gtfs.py sur le Pi dans la foulée : sans ça, le
            # seuil d'alerte du Pi (verification_gtfs_etat.json) resterait
            # basé sur l'ancien référentiel jusqu'au prochain cron (3h15) —
            # même geste que fait "Lancer la vérification maintenant".
            lancer_a_distance(PI_HOST)
            try:
                self._rsync_depuis_pi(PI_GTFS_LOG_PATH, LOG_FILE)
            except Exception:
                pass
            self._render_verification_gtfs_tab()
            self.status_var.set(f"Référentiel déployé sur le Pi ({version_locale}).")
        finally:
            self.bouton_deployer.config(state="normal")
