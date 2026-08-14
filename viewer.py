"""
Interface graphique pour suivre les données collectées sur la VPS
(observations.db, SQLite — Pi jusqu'au 2026-08-13, remplacé depuis par la
VPS qui héberge la collecte, voir mémoire du projet) sans avoir à se
connecter en SSH.

Le bouton "Rafraîchir" rapatrie le fichier le plus récent depuis la VPS
(via rsync) puis met à jour le tableau, les statistiques et le graphique.
"""
import math
import os
import re
import sqlite3
import subprocess
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont
from tkinter import ttk, messagebox

import matplotlib
import matplotlib.colors
import matplotlib.dates as mdates
import matplotlib.patches
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from graphiques import finalize_axes, marquer_maximum, marquer_moyenne, tracer_serie_temporelle
from generer_guide_statistiques import (
    COULEUR_ACCENT as GUIDE_COULEUR_ACCENT,
    COULEUR_EXEMPLE_FOND as GUIDE_COULEUR_EXEMPLE_FOND,
    COULEUR_GRIS as GUIDE_COULEUR_GRIS,
    GUIDE_PAGES,
)
from generer_guide_statistiques import generer as generer_guide_statistiques_pdf
from formatting import (
    PARIS_TZ,
    build_stop_names,
    build_trip_data,
    calculer_stats_bloc,
    choisir_variante,
    cle_circulation,
    derniers_par_passage,
    duree_theorique,
    format_bool_oui_non,
    format_entier,
    format_gare,
    format_gare_frise,
    format_heure_avec_arret,
    format_min_sans_zero,
    format_poll_time,
    format_retard,
    format_valeur,
    estimer_passage_reel,
    load_calendrier,
    load_reference,
    sans_date_trip_id,
    trajet_sens,
)
from tooltips import SimpleTooltip, SurvolArtistes, TreeviewHeaderTooltips
from vps_status import recuperer_etat_vps
from perturbations import charger_alertes, charger_evenements, PERTURBATIONS_FILE
from verifier_gtfs import LOG_FILE as GTFS_LOG_FILE
from onglet_verification_gtfs import OngletVerificationGTFSMixin
from config import VPS_HOST

matplotlib.rcParams["font.size"] = 9  # même taille que le tableau

# observations.db (SQLite) plutôt que observations.csv, et VPS plutôt que Pi
# depuis le 2026-08-13 (la VPS remplace le Pi comme source des données) — le
# voyant d'état dans la barre du haut a suivi le même mouvement le
# 2026-08-14 : il affichait la santé matérielle du Pi (via pi_status.py,
# resté disponible en outil autonome), il affiche maintenant celle de la
# VPS (voir vps_status.py) — PI_HOST n'est donc plus importé du tout ici.
VPS_OBSERVATIONS_DB_PATH = "~/train-delay-paris-cherbourg/observations.db"
LOCAL_OBSERVATIONS_DB = "observations.db"
VPS_ALERTES_PATH = "~/train-delay-paris-cherbourg/alertes.csv"
LOCAL_ALERTES = "alertes.csv"
VPS_PERTURBATIONS_PATH = "~/train-delay-paris-cherbourg/perturbations_detectees.csv"
VPS_GTFS_LOG_PATH = f"~/train-delay-paris-cherbourg/{GTFS_LOG_FILE}"
ICON_FILE = "train-logo.png"

AUTO_REFRESH_MS = 5 * 60 * 1000  # 5 minutes, au même rythme que la collecte sur le Pi
SEUIL_RETARD_FORT = 10  # minutes
SEUIL_RETARD_MOYEN = 5  # minutes

# Dégradé ancien -> récent pour "Suivi d'un train" (bleu -> orange). Remplace
# viridis dont le violet foncé de départ se confondait avec le gris des axes.
TRAJET_COLORMAP = matplotlib.colors.LinearSegmentedColormap.from_list(
    "bleu_orange", ["#2c6ea5", "#f2a53d"]
)

# Les 11 gares réellement sur la ligne Paris-Cherbourg (voir GARES_LIGNE dans
# build_reference.py). Sert à distinguer les gares "de passage" qu'un train
# de jonction traverse (ex: vers Rouen, Rennes, Granville...) mais qui ne
# font pas partie de cette ligne, depuis que le référentiel a été élargi
# (voir mémoire du projet, 2026-07-20).
GARES_LIGNE = {
    "Paris Saint-Lazare", "Mantes-la-Jolie", "Évreux Normandie", "Bernay",
    "Lisieux", "Caen", "Bayeux", "Lison", "Carentan", "Valognes", "Cherbourg",
}

# Mêmes 11 gares que GARES_LIGNE, mais ordonnées Paris -> Cherbourg (un set
# n'a pas d'ordre) — pour la frise en bas de l'appli.
GARES_LIGNE_ORDRE = (
    "Paris Saint-Lazare", "Mantes-la-Jolie", "Évreux Normandie", "Bernay",
    "Lisieux", "Caen", "Bayeux", "Lison", "Carentan", "Valognes", "Cherbourg",
)


class App(tk.Tk, OngletVerificationGTFSMixin):
    # px — voir _empaqueter_segments. Relevé à 1450 (était 1100) en même
    # temps que la police de cette ligne passée à 8pt (était 9, 7pt essayé
    # d'abord mais jugé trop petit) — mesuré sur des segments réels :
    # ~1535px à 9pt, ~1363px à 8pt (tient dans 1450px, marge d'environ
    # 90px) — demande explicite de l'utilisateur, 2026-08-14.
    LARGEUR_STATS_PERIODE = 1450

    def __init__(self):
        super().__init__()
        self.title("Suivi des circulations sur l'axe Paris ↔ Cherbourg")
        self.geometry("1100x700")
        try:
            # Référence gardée sur self, sinon l'image est garbage-collectée et
            # l'icône disparaît silencieusement.
            self._icon_image = tk.PhotoImage(file=ICON_FILE)
            self.iconphoto(True, self._icon_image)
        except tk.TclError:
            pass  # icône absente/illisible : pas bloquant pour l'application
        style = ttk.Style()
        style.theme_use("clam")  # affiche une vraie coche plutôt qu'un carré plein
        style.configure("Treeview", font=("", 9), rowheight=22)
        style.configure("Treeview.Heading", font=("", 9, "bold"))
        style.configure("TCombobox", font=("", 9))
        self.option_add("*TCombobox*Listbox.font", ("", 9))  # police du menu déroulant ouvert

        reference = load_reference()
        self.stop_names = build_stop_names(reference)
        # self.variantes/self.calendrier remplacent les anciens
        # trajet_gares/trajet_horaires/scheduled_times/trajet_arrets/
        # temps_arret séparés — un même train peut avoir plusieurs
        # variantes d'horaire selon la période, choisir_variante() choisit
        # la bonne selon la date réellement demandée (voir formatting.py,
        # correctif du 2026-08-12).
        self.variantes = build_trip_data(reference)
        self.calendrier = load_calendrier()
        self.trajet_labels = {}
        self.df = None
        self.alertes = pd.DataFrame(columns=["id", "gares", "cause", "effet", "debut", "fin", "texte", "description", "poll_time"])
        self.evenements = pd.DataFrame(columns=["poll_time", "type", "trip_id", "start_date", "train", "gare"])
        self.auto_refresh_job = None

        # État de la légende interactive de "Suivi d'un train" (vue Détail) :
        # quels relevés sont affichés/masqués et si la légende est dépliée,
        # par trajet — pour que ça survive aux re-rendus déclenchés par un clic.
        self._detail_visibilite = {}
        self._detail_legende_etendue = {}
        self._train_legend_map = {}
        self._train_detail_polls = {}

        self._build_top_bar()
        self._build_filters()
        # Filtres avant les stats : les chiffres affichés en dessous sont
        # déjà filtrés (Gare/Train/Sens...), les lire avant de savoir sur
        # quoi ils portent obligeait à lire dans le mauvais sens (l'effet
        # avant la cause) — repéré par l'utilisateur, 2026-07-27.
        self._build_stats()

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=(0, 5))
        self.notebook = notebook

        self.table_tab = ttk.Frame(notebook)
        notebook.add(self.table_tab, text="Tableau")
        self._build_table(self.table_tab)

        self.chart_tab = ttk.Frame(notebook)
        notebook.add(self.chart_tab, text="Graphique")
        self._build_chart(self.chart_tab)

        self.train_tab = ttk.Frame(notebook)
        notebook.add(self.train_tab, text="Suivi d'un train")
        self._build_train_tab(self.train_tab)

        self.jour_heure_tab = ttk.Frame(notebook)
        notebook.add(self.jour_heure_tab, text="Par jour / heure")
        self._build_jour_heure_tab(self.jour_heure_tab)

        self.travaux_tab = ttk.Frame(notebook)
        notebook.add(self.travaux_tab, text="Travaux / Alertes")
        self._build_travaux_tab(self.travaux_tab)

        self.verification_gtfs_tab = ttk.Frame(notebook)
        notebook.add(self.verification_gtfs_tab, text="Vérification GTFS")
        self._build_verification_gtfs_tab(self.verification_gtfs_tab)

        self.guide_tab = ttk.Frame(notebook)
        notebook.add(self.guide_tab, text="Guide statistiques")
        self._build_guide_tab(self.guide_tab)

        notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_frise_ligne()

        self.status_var = tk.StringVar(value="Prêt.")
        ttk.Label(self, textvariable=self.status_var, foreground="#555").pack(anchor="w", padx=10, pady=(0, 5))

        self.load_local_data()
        self._toggle_auto_refresh()

    # --- Construction de l'interface ---
    def _build_top_bar(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=10)

        self.refresh_button = ttk.Button(top, text="Rafraîchir depuis la VPS", command=self.refresh)
        self.refresh_button.pack(side="left")

        # Espace disque/température, pas la fraîcheur des données (déjà
        # couverte par "dernier relevé" plus loin sur cette même ligne, qui
        # se met à jour tout seul via le rafraîchissement automatique) —
        # l'intérêt ici est de voir venir une panne qui couve (carte SD
        # pleine, surchauffe) avant qu'elle n'interrompe la collecte, pas de
        # dater les données — demande explicite de l'utilisateur, 2026-07-31.
        # Pastille dessinée sur un petit Canvas plutôt qu'un caractère "●"/"•"
        # dans le texte : aucun glyphe Unicode ne donnait à la fois un bon
        # alignement vertical avec le texte ET une taille assez visible
        # (repéré par l'utilisateur, 2026-07-31) — un cercle dessiné donne un
        # contrôle exact sur les deux.
        fond = ttk.Style().lookup("TFrame", "background")
        self.canvas_etat_vps = tk.Canvas(top, width=14, height=16, highlightthickness=0, background=fond)
        self._point_etat_vps = self.canvas_etat_vps.create_oval(1, 2, 13, 14, fill="#888888", outline="")
        self.canvas_etat_vps.pack(side="left", padx=(15, 4))
        self.etat_vps_var = tk.StringVar(value="VPS : état inconnu (pas encore interrogée)")
        self.label_etat_vps = ttk.Label(top, textvariable=self.etat_vps_var, foreground="#888", font=("", 9))
        self.label_etat_vps.pack(side="left")
        # Dernier taux de mémoire utilisée connu, pour afficher une tendance
        # (→/↗/↘) au rafraîchissement suivant — None tant qu'on n'a pas
        # encore de valeur à comparer (premier rafraîchissement). Mémoire
        # plutôt que température CPU (utilisée ici avant le 2026-08-14, du
        # temps où ce voyant suivait le Pi, voir pi_status.py) : la VPS n'a
        # pas de capteur thermique exposé, et c'est justement la mémoire qui
        # a causé l'incident du 2026-08-13 (mémoire du projet) — la
        # grandeur la plus utile à surveiller ici.
        self._dernier_mem_pct = None

        self.auto_refresh_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            top, text="↻ auto (5 min)",
            variable=self.auto_refresh_var, command=self._toggle_auto_refresh,
        ).pack(side="left", padx=15)

        self.summary_var = tk.StringVar(value="Aucune donnée chargée.")
        ttk.Label(top, textvariable=self.summary_var).pack(side="left", padx=15)

    def _rafraichir_etat_vps(self):
        etat = recuperer_etat_vps(VPS_HOST)
        if etat is None:
            self.etat_vps_var.set("VPS : injoignable")
            self.label_etat_vps.configure(foreground="#ef4444")
            self.canvas_etat_vps.itemconfigure(self._point_etat_vps, fill="#ef4444")
        elif not etat["service_actif"]:
            # Joignable par SSH mais train-delay.service arrêté : distinct
            # d'une VPS injoignable (même couleur d'alerte, message différent)
            # — c'est justement le genre de panne partielle qu'un simple
            # ping/SSH ne détecterait pas, alors que le site public est bel
            # et bien indisponible pour les visiteurs.
            self.etat_vps_var.set(
                f"VPS : en ligne mais train-delay arrêté !  ·  disque "
                f"{etat['disque_libre_pct']} % libre ({etat['disque_libre_texte']})"
            )
            self.label_etat_vps.configure(foreground="#ef4444")
            self.canvas_etat_vps.itemconfigure(self._point_etat_vps, fill="#ef4444")
        else:
            mem_pct = etat["mem_utilisee_pct"]
            SEUIL_TENDANCE = 3  # points de % — en dessous, on considère la mémoire stable
            if self._dernier_mem_pct is None:
                fleche = ""
            elif mem_pct - self._dernier_mem_pct > SEUIL_TENDANCE:
                fleche = "↗ "
            elif self._dernier_mem_pct - mem_pct > SEUIL_TENDANCE:
                fleche = "↘ "
            else:
                fleche = "→ "
            self._dernier_mem_pct = mem_pct

            self.etat_vps_var.set(
                f"VPS : en ligne  ·  disque {etat['disque_libre_pct']} % libre "
                f"({etat['disque_libre_texte']})  ·  RAM {fleche}{mem_pct:.0f} % "
                f"({etat['mem_utilisee_mo']}/{etat['mem_totale_mo']} Mo)"
            )
            self.label_etat_vps.configure(foreground="#1a7d3c")
            self.canvas_etat_vps.itemconfigure(self._point_etat_vps, fill="#1a7d3c")

    def _build_stats(self):
        stats = ttk.Frame(self)
        stats.pack(fill="x", padx=10)
        self.stats_frame = stats
        self.stat_ratio_retard_var = tk.StringVar(value="circulations perturbées : -")
        self.stat_cumule_var = tk.StringVar(value="Retard cumulé : -")
        self.stat_moyen_var = tk.StringVar(value="Retard moyen / relevé : -")
        self.stat_max_var = tk.StringVar(value="Retard max : -")
        self.stat_pire_gare_var = tk.StringVar(value="Gare la + touchée : -")

        self.label_ratio_retard = ttk.Label(
            stats, textvariable=self.stat_ratio_retard_var, font=("", 9, "bold"), foreground="#c2410c",
        )
        self.label_ratio_retard.pack(side="left", padx=15, pady=(0, 5))
        # Texte mis à jour à chaque rendu (voir _render_stats) : les chiffres
        # évoluent au fil de la collecte, contrairement aux autres tooltips
        # statiques de cette barre.
        self._tooltip_ratio_retard = SimpleTooltip(self.label_ratio_retard, "")

        self.label_cumule = ttk.Label(stats, textvariable=self.stat_cumule_var, font=("", 9, "bold"))
        self.label_cumule.pack(side="left", padx=15, pady=(0, 5))
        SimpleTooltip(
            self.label_cumule,
            "Somme des retards, une seule fois par passage réel (dernière valeur connue), "
            "pas par relevé — contrairement à \"Retard moyen / relevé\", chaque perturbation "
            "n'est comptée qu'une fois même si elle a été vue à plusieurs relevés successifs.",
        )

        self.label_moyen = ttk.Label(stats, textvariable=self.stat_moyen_var, font=("", 9, "bold"))
        self.label_moyen.pack(side="left", padx=15, pady=(0, 5))
        # Texte mis à jour à chaque rendu (voir _render_stats) : le nombre de
        # relevés dépend des filtres actifs (Gare/Train/Sens...).
        self._tooltip_moyen = SimpleTooltip(self.label_moyen, "")

        self.label_max = ttk.Label(stats, textvariable=self.stat_max_var, font=("", 9, "bold"))
        self.label_max.pack(side="left", padx=15, pady=(0, 5))

        self.label_pire_gare = ttk.Label(stats, textvariable=self.stat_pire_gare_var, font=("", 9, "bold"))
        self.label_pire_gare.pack(side="left", padx=15, pady=(0, 5))
        # Texte mis à jour à chaque rendu (voir _render_stats) : le nombre de
        # relevés dépend des filtres actifs.
        self._tooltip_pire_gare = SimpleTooltip(self.label_pire_gare, "")

    def _build_filters(self):
        filters = ttk.Frame(self)
        filters.pack(fill="x", padx=10, pady=(0, 5))
        self.filters_frame = filters

        ttk.Label(filters, text="Gare :").pack(side="left")
        self.filtre_gare_var = tk.StringVar(value="Toutes")
        self.filtre_gare_combo = ttk.Combobox(filters, textvariable=self.filtre_gare_var, state="readonly", width=23)
        self.filtre_gare_combo.pack(side="left", padx=(5, 15))
        self.filtre_gare_combo.bind("<<ComboboxSelected>>", lambda e: self.render())

        ttk.Label(filters, text="Train :").pack(side="left")
        self.filtre_train_var = tk.StringVar(value="Tous")
        self.filtre_train_combo = ttk.Combobox(filters, textvariable=self.filtre_train_var, state="readonly", width=19)
        self.filtre_train_combo.pack(side="left", padx=(5, 15))
        self.filtre_train_combo.bind("<<ComboboxSelected>>", lambda e: self.render())

        ttk.Label(filters, text="Sens :").pack(side="left")
        self.filtre_sens_var = tk.StringVar(value="Tous")
        self.filtre_sens_combo = ttk.Combobox(filters, textvariable=self.filtre_sens_var, state="readonly", width=14)
        self.filtre_sens_combo.pack(side="left", padx=5)
        self.filtre_sens_combo.bind("<<ComboboxSelected>>", lambda e: self.render())

        self.limiter_ligne_var = tk.BooleanVar(value=True)
        self.case_limiter_ligne = ttk.Checkbutton(
            filters, text=f"Limiter aux {len(GARES_LIGNE)} gares de la ligne", variable=self.limiter_ligne_var,
            command=self._on_toggle_limiter_ligne,
        )
        self.case_limiter_ligne.pack(side="left", padx=15)
        SimpleTooltip(
            self.case_limiter_ligne,
            "Coché (par défaut) : les gares hors de l'axe Paris ↔ Cherbourg (ex: Rouen, Le "
            "Havre, Rennes, Granville, Coutances) sont exclues de toutes les statistiques "
            "générales. Décoché : ces gares comptent aussi — un retard survenu uniquement sur "
            "une branche éloignée peut alors faire remonter une circulation dans « Circulations "
            "perturbées » ou peser dans « Retard cumulé », même sans aucun retard réel sur "
            "l'axe Paris-Cherbourg lui-même.",
        )
        # Remplace la case à cocher (plutôt que de la griser, voir le
        # commentaire équivalent pour label_limiter_retard_ignore un peu plus
        # bas) sur Suivi d'un train, qui affiche toujours le trajet réel
        # complet d'une circulation, gares hors ligne comprises (voir
        # _render_train_tab) — sans quoi une case cochée + grisée aurait
        # laissé croire que le filtre restait actif sur cet onglet, ce qui
        # est l'inverse de la réalité (repéré par l'utilisateur, 2026-08-03,
        # même incohérence que pour "Limiter aux trains avec retard"
        # ci-dessous).
        self.label_limiter_ligne_ignore = ttk.Label(
            filters, text="Limiter aux gares de la ligne : off (voir info)",
            foreground="#555",
        )
        SimpleTooltip(
            self.label_limiter_ligne_ignore,
            "Sans effet sur cet onglet : Suivi d'un train affiche toujours le trajet réel "
            "complet d'une circulation, gares hors ligne comprises — s'applique toujours "
            "dans les autres onglets, si coché.",
        )

        self.limiter_retard_var = tk.BooleanVar(value=True)
        self.case_limiter_retard = ttk.Checkbutton(
            filters, text="Limiter aux trains avec retard", variable=self.limiter_retard_var,
            command=self.render,
        )
        self.case_limiter_retard.pack(side="left", padx=15)
        SimpleTooltip(
            self.case_limiter_retard,
            "Un train reste inclus tant qu'il a eu du retard à un moment de son "
            "trajet, même si ses relevés les plus récents sont revenus à 0 min — "
            "le tableau n'affichant que les 300 dernières lignes, ce retard peut "
            "ne plus être visible dans les lignes affichées.",
        )
        # Remplace la case à cocher sur les onglets où elle est ignorée
        # (Graphique, Par jour/heure — voir _render_chart et
        # _filtered_df_avant_retard — et désormais aussi Suivi d'un train,
        # voir _filtered_df_pour_trajets) plutôt que de la griser : une case
        # cochée + grisée laissait croire que le filtre restait actif, ce qui
        # est l'inverse de la réalité — repéré par l'utilisateur, 2026-07-29.
        # Pas de widget affiché en la remplaçant, activé/désactivé dans
        # _on_tab_changed.
        self.label_limiter_retard_ignore = ttk.Label(
            filters, text="Limiter aux trains avec retard : off (voir info)",
            foreground="#555",
        )
        SimpleTooltip(
            self.label_limiter_retard_ignore,
            "Sans effet sur cet onglet — gonflerait artificiellement les tendances de "
            "Graphique/Par jour-heure en excluant les trains ponctuels, ou ferait "
            "disparaître à tort des circulations réellement en retard de la liste de "
            "Suivi d'un train, qui a sa propre case \"Trains du jour en retard "
            "uniquement\" pour ça — s'applique toujours dans les autres onglets, si coché.",
        )

        self.bouton_reinitialiser = ttk.Button(filters, text="Réinitialiser", command=self.reset_filters)
        self.bouton_reinitialiser.pack(side="left", padx=10)

        # Déplacé ici depuis la barre du haut, pour laisser la place à la
        # ligne "état du Pi" à côté du bouton Rafraîchir — demande explicite
        # de l'utilisateur, 2026-07-31.
        ttk.Button(filters, text="Sources des données", command=self._show_sources).pack(side="left", padx=10)

    def _on_tab_changed(self, event=None):
        onglet_actif = self.notebook.select()
        sur_graphique = onglet_actif == str(self.chart_tab)
        sur_graphique_ou_jour_heure = sur_graphique or onglet_actif == str(self.jour_heure_tab)
        sur_suivi_train = onglet_actif == str(self.train_tab)

        # Ordre d'insertion important : les deux blocs ci-dessous s'ancrent
        # sur le même repère fixe (bouton_reinitialiser, toujours affiché) —
        # traiter d'abord "Limiter aux gares de la ligne" puis "Limiter aux
        # trains avec retard" les insère chacun juste avant ce repère dans
        # cet ordre, reproduisant l'ordre visuel voulu (ligne avant retard)
        # sans dépendre de l'état (case ou label) de l'autre widget.
        self.case_limiter_ligne.pack_forget()
        self.label_limiter_ligne_ignore.pack_forget()
        if sur_suivi_train:
            self.label_limiter_ligne_ignore.pack(side="left", padx=15, before=self.bouton_reinitialiser)
        else:
            self.case_limiter_ligne.pack(side="left", padx=15, before=self.bouton_reinitialiser)

        self.case_limiter_retard.pack_forget()
        self.label_limiter_retard_ignore.pack_forget()
        if sur_graphique_ou_jour_heure or sur_suivi_train:
            self.label_limiter_retard_ignore.pack(side="left", padx=15, before=self.bouton_reinitialiser)
        else:
            self.case_limiter_retard.pack(side="left", padx=15, before=self.bouton_reinitialiser)

        # Reconstruit systématiquement l'ordre des 5 stats principales
        # plutôt que de raccrocher chaque widget à un voisin qui peut
        # lui-même être masqué — plus simple et sans risque d'ordre
        # incohérent que des before= en cascade.
        for widget in (self.label_ratio_retard, self.label_cumule, self.label_moyen,
                       self.label_max, self.label_pire_gare):
            widget.pack_forget()
        if sur_graphique:
            # Les 5 stats principales ont toutes un équivalent dans la ligne
            # de stats propre à cet onglet (scopée à la période choisie) —
            # les garder ici ferait doublon, avec des chiffres différents
            # pour le même libellé (l'historique filtré complet ici, contre
            # la période choisie juste en dessous) — repéré par
            # l'utilisateur, 2026-07-30.
            widgets_a_montrer = []
        elif sur_graphique_ou_jour_heure:
            # Par jour/heure n'a pas de ligne équivalente pour circulations
            # perturbées/Retard cumulé/Retard max (contrairement à
            # Graphique) — seuls Retard moyen/relevé et Gare la + touchée
            # sont masqués ici, car sensibles à "Limiter aux trains avec
            # retard" (voir _tracer_barres_fiabilite/label_limiter_retard_ignore),
            # pas les 3 autres, invariants à cette case.
            widgets_a_montrer = [self.label_ratio_retard, self.label_cumule, self.label_max]
        else:
            widgets_a_montrer = [
                self.label_ratio_retard, self.label_cumule, self.label_moyen,
                self.label_max, self.label_pire_gare,
            ]
        for widget in widgets_a_montrer:
            widget.pack(side="left", padx=15, pady=(0, 5))

        # La largeur des barres filtres/stats change avec ce qui vient d'être
        # affiché/masqué ci-dessus — sans ce recalcul, la fenêtre garde la
        # largeur de l'onglet précédent et le dernier libellé peut se
        # retrouver tronqué (voir _ajuster_largeur_fenetre, jusqu'ici
        # seulement appelée après le rendu du tableau) — repéré par
        # l'utilisateur, 2026-07-29.
        self._ajuster_largeur_fenetre()

    def _build_table(self, parent):
        columns = ("poll_time", "train", "sens", "gare", "heure_theorique", "retard_arrivee", "retard_depart",
                   "temperature", "precipitation", "vent",
                   "type_jour", "vacances", "arrets_restants")
        self.tree = ttk.Treeview(parent, columns=columns, show="headings")
        headers = {
            "poll_time": "Relevé",
            "gare": "Gare",
            "heure_theorique": "Heure théo.",
            "train": "Train",
            "sens": "Sens",
            "retard_arrivee": "Arr. (min)",
            "retard_depart": "Dép. (min)",
            "temperature": "Temp. (°C)",
            "precipitation": "Pluie (mm)",
            "vent": "Vent (km/h)",
            "type_jour": "Jour",
            "vacances": "Vacances",
            "arrets_restants": "Arrêts",
        }
        for col in columns:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, width=100, anchor="center")  # largeur provisoire, ajustée au contenu ensuite
        self.tree.pack(fill="both", expand=True, side="left")
        self.table_font = tkfont.Font(font=("", 9))

        # Couleur de texte pour les retards importants (la teinte pastel par
        # train et la palette associée ont été retirées : le séparateur gris
        # entre trains suffit à distinguer les groupes).
        self.tree.tag_configure("retard_fort", foreground="#ef4444")
        self.tree.tag_configure("retard_moyen", foreground="#f97316")

        # Arrivée correcte mais départ en retard notable (arrêt rallongé) :
        # signalé à part, distinct de retard_moyen/fort qui reflètent un
        # retard d'arrivée — sinon ce genre de ligne restait invisible en
        # noir alors qu'un vrai retard (de départ) est en train de s'accumuler.
        self.tree.tag_configure("depart_retard", foreground="#ccb531")

        # Gares hors de la ligne Paris-Cherbourg (trains de jonction) : texte
        # grisé, toujours prioritaire sur les couleurs de retard ci-dessus.
        self.tree.tag_configure("hors_ligne", foreground="#999999")

        # Ligne vide grise entre deux trains différents, pour séparer
        # visuellement les groupes (voir apercu_separateur.png).
        self.tree.tag_configure("separateur", background="#f1f2f3")

        TreeviewHeaderTooltips(self.tree, {
            "arrets_restants": "Nombre de gares restantes avant le terminus du trajet, "
                                "au moment de cette observation.",
            "retard_arrivee": "Retard prévu à l'arrivée dans cette gare, au moment de "
                               "ce relevé. Vide pour la toute première gare du trajet "
                               "(un train n'y a pas d'heure d'arrivée, il y part).",
            "retard_depart": "Retard prévu au départ de cette gare, au moment de ce "
                              "relevé. Vide pour la dernière gare du trajet (un train "
                              "n'y repart pas, il y arrive).",
            "heure_theorique": "Heure théorique (horaires prévus) de passage du train "
                                "à cette gare précise, indépendamment du retard observé.",
        })

        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

    def _build_chart(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=10, pady=(10, 0))
        ttk.Label(top, text="Période :").pack(side="left")
        self.periode_graphique_var = tk.StringVar(value="dernières 24h")
        periode_combo = ttk.Combobox(
            top, textvariable=self.periode_graphique_var, state="readonly",
            values=["dernières 24h", "3 derniers jours", "7 derniers jours", "tout l'historique"],
            width=18,
        )
        periode_combo.pack(side="left", padx=5)
        periode_combo.bind("<<ComboboxSelected>>", lambda e: self.render())

        # Nombre de relevés directement à côté du sélecteur de période
        # (plutôt qu'en premier segment de la ligne de stats juste en
        # dessous) : répond directement à "sur combien de données porte
        # cette période", au moment même où on la choisit — demande
        # explicite de l'utilisateur, 2026-07-30. Pas besoin de répéter le
        # nom de la période, déjà visible dans la combobox elle-même.
        self.nb_releves_periode_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.nb_releves_periode_var, foreground="#555").pack(side="left", padx=(5, 0))

        ttk.Label(
            top,
            text="Courbe du haut : chaque point = retard moyen de tous les relevés à cet instant (selon les\n"
                 "filtres actifs). Courbe du bas : chaque point = % des circulations en retard au même\n"
                 "instant. Pointillé « moy. » = moyenne de la courbe sur la période affichée. ★ = point le\n"
                 "plus haut de la période — survole-le pour le détail.\n",
            foreground="#555", justify="left", font=("", 9),
        ).pack(side="left", padx=10)

        # Mêmes stats que la barre du haut (Trains en retard/Retard moyen/
        # Retard max/Gare la + touchée), mais limitées à la période choisie
        # ci-dessus — celles du haut portent toujours sur tout l'historique
        # filtré, ce qui ne permet pas de vérifier un chiffre "sur les
        # dernières 24h" par exemple (voir mémoire du projet, 2026-07-24).
        # Police utilisée pour mesurer la largeur réelle des segments dans
        # _empaqueter_segments — même taille que le texte affiché.
        self._police_stats_periode = tkfont.Font(font=("", 8, "bold"))
        # Widget Text plutôt qu'un ttk.Label : un Label ne peut afficher
        # qu'une seule couleur, alors qu'on veut le premier segment
        # ("circulations perturbées") dans la couleur accent de la stat
        # équivalente du haut, le reste en couleur normale — voir
        # _afficher_stats_periode. Stylé pour se fondre comme un label :
        # lecture seule, sans bordure/curseur, fond identique au thème.
        # Hauteur ajustée dynamiquement au nombre de lignes réel (voir
        # _afficher_stats_periode) — ce texte grandit avec les données,
        # jamais borné dans le temps, sans limite il finirait par forcer la
        # fenêtre à s'élargir indéfiniment jusqu'à buter sur le bord de
        # l'écran (repéré par l'utilisateur, 2026-07-30, sur "tout
        # l'historique" après plusieurs semaines de collecte).
        fond = ttk.Style().lookup("TFrame", "background")
        self.texte_stats_periode = tk.Text(
            parent, font=("", 8, "bold"), height=1, wrap="none",
            relief="flat", borderwidth=0, highlightthickness=0,
            background=fond, cursor="arrow", takefocus=0, padx=0, pady=0,
        )
        self.texte_stats_periode.tag_configure("accent", foreground="#c2410c")
        self.texte_stats_periode.config(state="disabled")
        self.texte_stats_periode.pack(anchor="w", fill="x", padx=10, pady=(2, 5))

        self.figure = Figure(figsize=(8, 6.2), dpi=100)
        # Deux graphiques empilés, même axe des temps (sharex) : le retard
        # moyen ne dit pas si un pic vient d'un seul train très en retard ou
        # de beaucoup de trains légèrement en retard — la proportion de
        # trains en retard en dessous répond à cette question complémentaire
        # (voir mémoire du projet, 2026-07-23).
        self.ax = self.figure.add_subplot(211)
        self.ax2 = self.figure.add_subplot(212, sharex=self.ax)
        self.ax.tick_params(axis="x", labelbottom=False)
        self.canvas = FigureCanvasTkAgg(self.figure, master=parent)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self._survol_graphique = SurvolArtistes(self, self.canvas)

    def _build_jour_heure_tab(self, parent):
        ttk.Label(
            parent, text="Retard moyen et proportion de trains en retard selon le jour de la "
                          "semaine (date réelle du trajet), l'heure du relevé, le type de jour et "
                          "les vacances scolaires — statistique simple, pas un modèle.",
            foreground="#555",
        ).pack(anchor="w", padx=10, pady=(10, 0))
        self.jour_heure_figure = Figure(figsize=(11, 5.5), dpi=100)
        self.jour_ax = self.jour_heure_figure.add_subplot(321)
        self.heure_ax = self.jour_heure_figure.add_subplot(322)
        self.jour_pct_ax = self.jour_heure_figure.add_subplot(323)
        self.heure_pct_ax = self.jour_heure_figure.add_subplot(324)
        self.type_jour_ax = self.jour_heure_figure.add_subplot(325)
        self.vacances_ax = self.jour_heure_figure.add_subplot(326)
        self.jour_heure_canvas = FigureCanvasTkAgg(self.jour_heure_figure, master=parent)
        self.jour_heure_canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=10)

        self._survol_jour_heure = SurvolArtistes(self, self.jour_heure_canvas)

    def _build_travaux_tab(self, parent):
        ttk.Label(
            parent, text="Perturbations SNCF (travaux, incidents...) concernant une gare de la ligne",
            font=("", 9, "bold"),
        ).pack(anchor="w", padx=10, pady=(10, 0))
        ttk.Label(
            parent,
            text="Source : flux SNCF sncf-gtfs-rt-service-alerts, recoupé par gare — pas de filtrage "
                 "par gravité (beaucoup d'alertes SNCF n'indiquent ni cause ni effet exploitables).",
            foreground="#555", font=("", 8),
        ).pack(anchor="w", padx=10, pady=(0, 10))

        self.resume_travaux_var = tk.StringVar(value="Aucune alerte connue pour l'instant.")
        ttk.Label(
            parent, textvariable=self.resume_travaux_var, font=("", 10, "bold"), foreground="#c2410c",
        ).pack(anchor="w", padx=10, pady=(0, 8))

        colonnes = ("gares", "depuis", "jusqua", "texte")
        self.travaux_tree = ttk.Treeview(parent, columns=colonnes, show="headings")
        largeurs = {"gares": 180, "depuis": 130, "jusqua": 130, "texte": 500}
        titres = {"gares": "Gare(s) concernée(s)", "depuis": "Depuis", "jusqua": "Jusqu'à", "texte": "Alerte"}
        for c in colonnes:
            self.travaux_tree.heading(c, text=titres[c])
            self.travaux_tree.column(c, width=largeurs[c], anchor="w")
        self.travaux_tree.pack(fill="both", expand=True, padx=10, pady=(0, 5))
        self.travaux_tree.tag_configure("active", font=("", 9, "bold"))
        self.travaux_tree.tag_configure("passee", foreground="#888")

        self._travaux_descriptions = {}
        self.travaux_tree.bind("<<TreeviewSelect>>", self._on_select_alerte)

        self.description_alerte_var = tk.StringVar(value="")
        ttk.Label(
            parent, textvariable=self.description_alerte_var, foreground="#555",
            wraplength=1000, justify="left",
        ).pack(anchor="w", padx=10, pady=(5, 10))

        ttk.Label(
            parent, text="Arrêts supprimés / trajets annulés détectés",
            font=("", 9, "bold"),
        ).pack(anchor="w", padx=10, pady=(10, 0))
        ttk.Label(
            parent,
            text="Source : flux SNCF sncf-gtfs-rt-trip-updates (schedule_relationship) — événements "
                 "ponctuels détectés en temps réel, distincts des alertes officielles ci-dessus.",
            foreground="#555", font=("", 8),
        ).pack(anchor="w", padx=10, pady=(0, 10))

        colonnes_evt = ("train", "date", "evenement", "detecte")
        self.evenements_tree = ttk.Treeview(parent, columns=colonnes_evt, show="headings")
        largeurs_evt = {"train": 220, "date": 100, "evenement": 260, "detecte": 150}
        titres_evt = {"train": "Train", "date": "Date", "evenement": "Événement", "detecte": "Détecté le"}
        for c in colonnes_evt:
            self.evenements_tree.heading(c, text=titres_evt[c])
            self.evenements_tree.column(c, width=largeurs_evt[c], anchor="w")
        self.evenements_tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _on_select_alerte(self, event=None):
        selection = self.travaux_tree.selection()
        if not selection:
            self.description_alerte_var.set("")
            return
        self.description_alerte_var.set(self._travaux_descriptions.get(selection[0], ""))

    def _render_travaux_tab(self):
        self.travaux_tree.delete(*self.travaux_tree.get_children())
        self._travaux_descriptions = {}
        self.description_alerte_var.set("")

        alertes = self.alertes
        maintenant = pd.Timestamp.now(tz="UTC")
        # Une alerte sans début/fin connu (champ vide dans le flux SNCF) est
        # traitée comme active plutôt qu'ignorée silencieusement — mieux vaut
        # la montrer avec une date manquante que la faire disparaître.
        actif = ((alertes["debut"].isna()) | (alertes["debut"] <= maintenant)) & \
                ((alertes["fin"].isna()) | (maintenant <= alertes["fin"]))

        n_actives = int(actif.sum())
        if alertes.empty:
            self.resume_travaux_var.set("Aucune alerte connue pour l'instant.")
        elif n_actives == 0:
            self.resume_travaux_var.set(f"Aucune alerte active en ce moment ({len(alertes)} archivée(s)).")
        else:
            self.resume_travaux_var.set(f"⚠ {n_actives} alerte(s) active(s) en ce moment")

        # Actives d'abord (les plus proches de leur fin en premier, plus
        # urgentes à consulter), puis les passées (les plus récentes en tête).
        ordre = alertes.assign(_actif=actif).sort_values(
            ["_actif", "fin"], ascending=[False, True],
        )
        for _, ligne in ordre.iterrows():
            depuis = ligne["debut"].tz_convert(PARIS_TZ).strftime("%d/%m %Hh%M") if pd.notna(ligne["debut"]) else "-"
            jusqua = ligne["fin"].tz_convert(PARIS_TZ).strftime("%d/%m %Hh%M") if pd.notna(ligne["fin"]) else "-"
            tag = "active" if ligne["_actif"] else "passee"
            item = self.travaux_tree.insert(
                "", "end", values=(ligne["gares"], depuis, jusqua, ligne["texte"]), tags=(tag,),
            )
            self._travaux_descriptions[item] = ligne["description"] or ligne["texte"]

        # Badge sur l'onglet lui-même : visible même sans être dessus.
        libelle = f"Travaux / Alertes ⚠ ({n_actives})" if n_actives else "Travaux / Alertes"
        self.notebook.tab(self.travaux_tab, text=libelle)

        self.evenements_tree.delete(*self.evenements_tree.get_children())
        # Plus récemment détecté en premier — comme pour les alertes actives,
        # les cas les plus susceptibles d'être encore pertinents en tête.
        for _, ligne in self.evenements.sort_values("poll_time", ascending=False).iterrows():
            date_str = datetime.strptime(str(ligne["start_date"]), "%Y%m%d").strftime("%d/%m/%Y")
            if ligne["type"] == "trajet_annule":
                evenement = "Trajet annulé (entier)"
            else:
                evenement = f"Arrêt supprimé : {ligne['gare']}"
            self.evenements_tree.insert(
                "", "end",
                values=(ligne["train"], date_str, evenement, format_poll_time(ligne["poll_time"].isoformat())),
            )

    def _build_guide_tab(self, parent):
        # Contenu (GUIDE_PAGES) partagé avec generer_guide_statistiques.py :
        # cet onglet et le PDF affichent exactement le même texte, une seule
        # source à tenir à jour — demande explicite de l'utilisateur,
        # 2026-07-28.
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=10, pady=10)
        ttk.Button(
            top, text="Régénérer le PDF (guide_statistiques.pdf)", command=self._regenerer_guide_pdf,
        ).pack(side="left")
        ttk.Label(
            top,
            text="Explique, avec des exemples chiffrés, ce que veut dire chaque statistique de l'application "
                 "et des rapports PDF.",
            foreground="#555",
        ).pack(side="left", padx=15)

        conteneur = ttk.Frame(parent)
        conteneur.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        canvas = tk.Canvas(conteneur, highlightthickness=0)
        scrollbar = ttk.Scrollbar(conteneur, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        interieur = ttk.Frame(canvas)
        fenetre_id = canvas.create_window((0, 0), window=interieur, anchor="nw")
        interieur.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(fenetre_id, width=e.width))

        # Molette active seulement quand le pointeur survole cet onglet, pour
        # ne pas interférer avec le défilement des autres (ex: Treeview du
        # tableau) — bind_all serait global à toute la fenêtre.
        def _molette(event):
            pas = -1 if (getattr(event, "delta", 0) > 0 or event.num == 4) else 1
            canvas.yview_scroll(pas, "units")

        def _activer_molette(_event):
            canvas.bind_all("<MouseWheel>", _molette)
            canvas.bind_all("<Button-4>", _molette)
            canvas.bind_all("<Button-5>", _molette)

        def _desactiver_molette(_event):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        canvas.bind("<Enter>", _activer_molette)
        canvas.bind("<Leave>", _desactiver_molette)

        # "(suite)"/"(suite (2))" a un sens dans le PDF (signale un saut de
        # page) mais pas ici, où le contenu défile en continu sans coupure —
        # on ne veut qu'un seul en-tête par section, quel que soit le nombre
        # de pages PDF qui la composent (repéré par l'utilisateur,
        # 2026-07-30, puis à nouveau 2026-08-04 : les pages "(suite)" de la
        # section 4 utilisent un titre abrégé, différent du titre complet de
        # la première page, donc une comparaison texte-à-texte après
        # suppression du seul suffixe "(suite...)" ne suffit pas). On
        # regroupe donc par numéro de section ("4." par exemple) plutôt que
        # par texte, et on affiche le titre (nettoyé de son suffixe
        # "(suite...)") de la première page rencontrée pour ce numéro.
        titre_page_precedent = None
        for page in GUIDE_PAGES:
            numero_section_match = re.match(r"\s*(\d+)\.", page["titre_page"])
            numero_section = (
                numero_section_match.group(1) if numero_section_match else page["titre_page"]
            )
            if numero_section != titre_page_precedent:
                titre_affiche = re.sub(
                    r"\s*[,(]\s*suite(\s*\(\d+\))?\)?$", "", page["titre_page"]
                )
                ttk.Label(
                    interieur, text=titre_affiche, font=("", 12, "bold"),
                ).pack(anchor="w", pady=(18, 6))
                titre_page_precedent = numero_section
            for bloc in page["blocs"]:
                self._ajouter_bloc_guide(interieur, bloc)

    def _ajouter_bloc_guide(self, parent, bloc):
        largeur_texte = 950

        ligne_titre = ttk.Frame(parent)
        ligne_titre.pack(anchor="w", pady=(8, 2))
        ttk.Label(
            ligne_titre, text=bloc["titre"], font=("", 11, "bold"), foreground=GUIDE_COULEUR_ACCENT,
        ).pack(side="left")
        sous_titre = bloc.get("sous_titre")
        if sous_titre:
            ttk.Label(
                ligne_titre, text=f" {sous_titre}", font=("", 9), foreground=GUIDE_COULEUR_GRIS,
            ).pack(side="left")

        ttk.Label(
            parent, text=bloc["definition"], justify="left", wraplength=largeur_texte,
        ).pack(anchor="w", pady=(0, 6))

        encart = tk.Frame(parent, background=GUIDE_COULEUR_EXEMPLE_FOND)
        encart.pack(fill="x", pady=(0, 6))
        tk.Label(
            encart, text="\n".join(bloc["exemple_lignes"]), justify="left", font=("Courier New", 9),
            background=GUIDE_COULEUR_EXEMPLE_FOND, anchor="w",
        ).pack(anchor="w", padx=10, pady=8, fill="x")

        ttk.Label(
            parent, text=f"Utile pour : {bloc['utilite']}", font=("", 9, "bold"),
            justify="left", wraplength=largeur_texte,
        ).pack(anchor="w", pady=(0, 4))

        pourquoi = bloc.get("pourquoi")
        if pourquoi:
            ttk.Label(
                parent, text=pourquoi, font=("", 9, "italic"), foreground=GUIDE_COULEUR_GRIS,
                justify="left", wraplength=largeur_texte,
            ).pack(anchor="w", pady=(0, 4))

    def _regenerer_guide_pdf(self):
        try:
            generer_guide_statistiques_pdf()
        except Exception as exc:
            messagebox.showerror("Erreur", f"Impossible de régénérer le guide :\n{exc}")
            return
        heure = datetime.now(PARIS_TZ).strftime("%H:%M:%S")
        self.status_var.set(f"Guide régénéré (guide_statistiques.pdf) à {heure}.")

    def _build_frise_ligne(self):
        label_frise = ttk.Label(
            self, text="État de la ligne, gare par gare", font=("", 9, "bold"),
        )
        label_frise.pack(anchor="w", padx=15, pady=(4, 0))
        # Texte mis à jour à chaque rendu (voir _render_frise) : le nombre de
        # relevés dépend des filtres actifs.
        self._tooltip_frise = SimpleTooltip(label_frise, "", au_dessus=True)
        fond = ttk.Style().lookup("TFrame", "background")
        # Un peu plus haut que le minimum requis par les points/étiquettes
        # (80px) : laisse la place à la légende "Trajet : ..." et au
        # connecteur en pointillé vers une gare hors ligne (voir
        # _infos_trajet_depuis_route), affichés sur Suivi d'un train quand
        # une circulation précise est sélectionnée.
        self.frise_canvas = tk.Canvas(self, height=95, bg=fond, highlightthickness=0)
        self.frise_canvas.pack(fill="x", padx=10, pady=(0, 5))
        # Redessiner au redimensionnement : les positions des gares sont
        # calculées en fonction de la largeur du canvas (voir _render_frise).
        self.frise_canvas.bind("<Configure>", lambda e: self._render_frise())

    def _build_train_tab(self, parent):
        top0 = ttk.Frame(parent)
        top0.pack(fill="x", padx=10, pady=(10, 0))
        # Coché par défaut : le menu "Trajet" liste sinon des centaines de
        # circulations depuis le 12/07, la plupart déjà consultées — ce
        # filtre ramène la liste à ce qui est probablement intéressant
        # *aujourd'hui*, sans devoir manuellement chercher dans tout
        # l'historique. Ne déclenche que _update_trajet_list (pas un
        # render() complet) : les autres onglets ne dépendent pas de ce
        # filtre, propre à ce menu. Le texte (nombre affiché / total) est mis
        # à jour par _update_trajet_list à chaque rendu.
        self.filtre_jour_retard_var = tk.BooleanVar(value=True)
        self.texte_filtre_jour_retard_var = tk.StringVar(value="Trains du jour en retard uniquement")
        ttk.Checkbutton(
            top0, textvariable=self.texte_filtre_jour_retard_var, variable=self.filtre_jour_retard_var,
            command=lambda: self._update_trajet_list(self._filtered_df_pour_trajets()),
        ).pack(side="left")

        top = ttk.Frame(parent)
        top.pack(fill="x", padx=10, pady=10)

        ttk.Label(top, text="Trajet :").pack(side="left")
        self.select_trajet_var = tk.StringVar()
        self.select_trajet_combo = ttk.Combobox(top, textvariable=self.select_trajet_var, state="readonly", width=47)
        self.select_trajet_combo.pack(side="left", padx=5)
        self.select_trajet_combo.bind("<<ComboboxSelected>>", lambda e: self._render_train_tab())

        ttk.Label(top, text="Vue :").pack(side="left", padx=(15, 0))
        self.vue_train_var = tk.StringVar(value="Escalier")
        vue_combo = ttk.Combobox(
            top, textvariable=self.vue_train_var, state="readonly",
            values=["Escalier", "Détail des relevés"], width=18,
        )
        vue_combo.pack(side="left", padx=5)
        vue_combo.bind("<<ComboboxSelected>>", lambda e: self._render_train_tab())

        self.aide_vue_train_var = tk.StringVar()
        ttk.Label(
            top, textvariable=self.aide_vue_train_var,
            foreground="#555", justify="left", font=("", 9),
        ).pack(side="left", padx=10)

        self.depart_arrivee_var = tk.StringVar(value="")
        ttk.Label(
            parent, textvariable=self.depart_arrivee_var, font=("", 8, "bold"),
        ).pack(anchor="w", padx=10, pady=(0, 5))

        self.train_figure = Figure(figsize=(8, 5), dpi=100)
        self.train_ax = self.train_figure.add_subplot(111)
        self.train_canvas = FigureCanvasTkAgg(self.train_figure, master=parent)
        self.train_canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.train_canvas.mpl_connect("pick_event", self._on_train_legend_pick)

    def _show_sources(self):
        messagebox.showinfo(
            "Sources des données",
            "Retards en temps réel :\n"
            "  proxy.transport.data.gouv.fr (flux GTFS-RT SNCF, public, sans clé)\n\n"
            "Météo actuelle :\n"
            "  api.open-meteo.com (gratuit, sans clé)\n\n"
            "Horaires théoriques (référence des trajets/arrêts) :\n"
            "  eu.ftp.opendatasoft.com/sncf/plandata (GTFS statique SNCF)\n\n"
            "Jours fériés :\n"
            "  calendrier.api.gouv.fr\n\n"
            "Vacances scolaires (zone B) :\n"
            "  data.education.gouv.fr\n\n"
            "Toutes ces sources sont publiques et gratuites, sans authentification.",
        )

    # --- Rafraîchissement automatique ---
    def _toggle_auto_refresh(self):
        if self.auto_refresh_job is not None:
            self.after_cancel(self.auto_refresh_job)
            self.auto_refresh_job = None
        if self.auto_refresh_var.get():
            self.auto_refresh_job = self.after(AUTO_REFRESH_MS, self._auto_refresh_tick)

    def _auto_refresh_tick(self):
        self.refresh(manuel=False)
        if self.auto_refresh_var.get():
            self.auto_refresh_job = self.after(AUTO_REFRESH_MS, self._auto_refresh_tick)

    # --- Données ---
    @staticmethod
    def _rsync_depuis_vps(chemin_distant, fichier_local):
        # rsync plutôt que scp : ces fichiers ne grandissent que par ajout en
        # fin de fichier (jamais modifiés au milieu), donc rsync ne
        # retransfère que les nouvelles lignes plutôt que le fichier entier à
        # chaque rafraîchissement (même mécanisme déjà utilisé pour la
        # synchro Pi -> NAS, backup_to_nas.sh). observations.db (SQLite) se
        # comporte différemment (pas un simple append, voir
        # collect_realtime.py) mais reste rsync-safe : ce script ferme sa
        # connexion à chaque exécution (checkpoint WAL automatique), donc le
        # fichier .db lu ici est toujours dans un état stable entre deux
        # cycles de collecte.
        subprocess.run(
            ["rsync", "-az", f"{VPS_HOST}:{chemin_distant}", fichier_local],
            check=True, capture_output=True, timeout=60,
        )

    def refresh(self, manuel=True):
        self.status_var.set("Récupération des données depuis la VPS...")
        self.update_idletasks()
        try:
            self._rsync_depuis_vps(VPS_OBSERVATIONS_DB_PATH, LOCAL_OBSERVATIONS_DB)
        except Exception as exc:
            # En automatique (toutes les 5 min), un souci passager de la VPS ne
            # doit pas interrompre l'utilisateur avec une fenêtre à chaque fois —
            # on se contente de la barre de statut. La fenêtre reste affichée
            # pour un clic manuel, où l'utilisateur attend un vrai retour.
            if manuel:
                messagebox.showerror("Erreur", f"Impossible de récupérer les données :\n{exc}")
            heure = datetime.now(PARIS_TZ).strftime("%H:%M:%S")
            self.status_var.set(f"Échec de la récupération à {heure} — nouvelle tentative dans 5 min.")
            # Interrogé même si le rsync a échoué : c'est justement le cas où
            # savoir si la VPS est en ligne (et dans quel état) aide le plus à
            # comprendre l'échec — ex: SSH répond mais train-delay.service est
            # arrêté, ou disque/mémoire pleins.
            self._rafraichir_etat_vps()
            return

        try:
            # Best-effort : contrairement à observations.db, l'absence
            # d'alertes.csv (ex: pas encore déployé sur la VPS, ou collecte pas
            # encore passée) ne doit jamais faire échouer tout le
            # rafraîchissement — ces alertes ne sont qu'un complément.
            self._rsync_depuis_vps(VPS_ALERTES_PATH, LOCAL_ALERTES)
        except Exception:
            pass

        try:
            # Même logique best-effort : peut ne pas encore exister sur la VPS
            # si aucun arrêt supprimé/trajet annulé n'a jamais été détecté
            # (voir perturbations.py — enregistrer_evenements() ne crée le
            # fichier qu'au premier événement réel).
            self._rsync_depuis_vps(VPS_PERTURBATIONS_PATH, PERTURBATIONS_FILE)
        except Exception:
            pass

        try:
            # Même logique best-effort : verifier_gtfs.py tourne sur la VPS
            # (cron 3h15, ou déclenché manuellement depuis l'onglet
            # "Vérification GTFS") — ce rapatriement garde l'onglet à jour
            # même sans clic sur "Lancer la vérification maintenant".
            self._rsync_depuis_vps(VPS_GTFS_LOG_PATH, GTFS_LOG_FILE)
        except Exception:
            pass

        self._rafraichir_etat_vps()
        self.load_local_data()
        self.status_var.set("Données à jour.")

    def load_local_data(self):
        # os.path.isfile AVANT de se connecter : sqlite3.connect() crée
        # silencieusement un fichier vide s'il n'existe pas, contrairement à
        # pd.read_csv qui levait FileNotFoundError — sans cette vérification,
        # une première ouverture avant tout rafraîchissement créerait un
        # observations.db vide par erreur plutôt que d'afficher le message
        # "Cliquez sur Rafraîchir" ci-dessous.
        if not os.path.isfile(LOCAL_OBSERVATIONS_DB):
            self.summary_var.set("Aucune donnée locale. Cliquez sur Rafraîchir.")
            return
        connexion = sqlite3.connect(LOCAL_OBSERVATIONS_DB)
        try:
            df = pd.read_sql_query("SELECT * FROM observations ORDER BY poll_time", connexion)
        except (sqlite3.DatabaseError, pd.errors.DatabaseError) as exc:
            # observations.db corrompu (ex: écriture interrompue sur la VPS) —
            # ne doit pas planter toute l'application, un nouveau rafraîchissement
            # depuis la VPS suffit en général à récupérer une copie saine.
            self.summary_var.set(
                f"observations.db semble corrompu ({type(exc).__name__}) — "
                "cliquez sur Rafraîchir pour récupérer une copie saine depuis la VPS."
            )
            return
        finally:
            connexion.close()

        df["gare"] = df["stop_id"].map(self.stop_names).fillna(df["stop_id"])
        # trip_id doit encore être en object/str ici, pas category (conversion
        # plus bas) : .str.split() sur un CategoricalIndex/une Series category
        # ne renvoie pas de vraies listes mais leur représentation texte (bug
        # rencontré dans formatting.calculer_stats_bloc, corrigé le 2026-08-14)
        # — ce .str.split()-ci reste sûr tant que cet ordre n'est pas inversé.
        df["train"] = df["trip_id"].str.split(":").str[0]
        df["sens"] = df["trip_id"].map(lambda t: trajet_sens(t, self.variantes))

        # Mémoïse choisir_variante par (trip_id, start_date) : beaucoup de
        # lignes (une par gare, répétées à chaque relevé) partagent la même
        # circulation réelle — pas la peine de refaire la recherche parmi
        # les variantes/le calendrier à chaque ligne individuellement.
        cache_variante = {}

        def variante_pour_ligne(trip_id, start_date):
            cle = (trip_id, start_date)
            if cle not in cache_variante:
                cache_variante[cle] = choisir_variante(self.variantes, self.calendrier, trip_id, start_date)
            return cache_variante[cle]

        def heure_theorique_ligne(r):
            variante = variante_pour_ligne(r["trip_id"], r["start_date"])
            if variante is None:
                return format_heure_avec_arret(None, r["start_date"], None)
            return format_heure_avec_arret(
                variante["horaires_par_stop"].get(r["stop_id"]), r["start_date"],
                variante["arrets_par_stop"].get(r["stop_id"]),
            )

        df["heure_theorique"] = df.apply(heure_theorique_ligne, axis=1)
        df["retard_arrivee_min"] = (df["arrival_delay_s"] / 60).round(1)
        df["retard_depart_min"] = (df["departure_delay_s"] / 60).round(1)
        df["retard_min"] = df["retard_arrivee_min"].fillna(df["retard_depart_min"])

        # category plutôt que object (chaîne Python "normale") pour les
        # colonnes à faible cardinalité — même optimisation que
        # app_fastapi.preparer_donnees (VPS), ajoutée le même jour après
        # l'incident mémoire là-bas : ~20 gares/quelques centaines de trains
        # répétés sur des centaines de milliers de lignes coûtent bien moins
        # cher stockés une fois chacun + un code entier par ligne. Vérifié
        # avant d'ajouter start_date à la liste : .astype("category") trie
        # ses catégories par valeur par défaut, donc le tri existant plus
        # bas (sort_values(["retard_max", "start_date"])) reste correct.
        for colonne in ("gare", "train", "sens", "type_jour", "trip_id", "stop_id", "heure_theorique", "start_date"):
            df[colonne] = df[colonne].astype("category")

        self.df = df

        gares_vues = df["gare"].dropna().unique().tolist()
        gares_de_la_ligne = sorted(g for g in gares_vues if g in GARES_LIGNE)
        gares_hors_ligne = sorted(g for g in gares_vues if g not in GARES_LIGNE)
        gares = ["Toutes"]
        if gares_de_la_ligne:
            gares += ["— Gares de la ligne —"] + gares_de_la_ligne
        if gares_hors_ligne:
            gares += ["— Autres gares (jonction) —"] + gares_hors_ligne
        self.filtre_gare_combo["values"] = gares
        if self.filtre_gare_var.get() not in gares:
            self.filtre_gare_var.set("Toutes")

        trains = ["Tous"] + sorted(df["train"].dropna().unique().tolist())
        self.filtre_train_combo["values"] = trains
        if self.filtre_train_var.get() not in trains:
            self.filtre_train_var.set("Tous")

        # "" (pas seulement NaN) exclu : valeur renvoyée par trajet_sens
        # quand le trajet théorique du train n'est plus dans le référentiel
        # actuel (voir sans_date_trip_id) — sans ce filtre, elle apparaissait
        # comme une entrée vide dans la combobox, triée juste après "Tous"
        # (chaîne vide < tout le reste), repéré par l'utilisateur 2026-08-04
        # après la régénération du référentiel qui a réduit sa couverture.
        sens_valeurs = ["Tous"] + sorted(v for v in df["sens"].dropna().unique() if v)
        self.filtre_sens_combo["values"] = sens_valeurs
        if self.filtre_sens_var.get() not in sens_valeurs:
            self.filtre_sens_var.set("Tous")

        self.load_local_perturbations()
        self.render()

    def load_local_perturbations(self):
        """Charge les deux sources de l'onglet Travaux/Alertes (voir
        perturbations.py) : alertes.csv (perturbations SNCF officielles,
        collect_alertes.py) et perturbations_detectees.csv (arrêts
        supprimés/trajets annulés détectés dans le flux temps réel,
        collect_realtime.py) — logique de chargement factorisée dans ce
        module, partagée avec le côté collecteur."""
        self.alertes = charger_alertes(LOCAL_ALERTES)
        self.evenements = charger_evenements()

    def _update_trajet_list(self, df):
        # df attendu : _filtered_df_pour_trajets() (Gare/Train/Sens
        # seulement, ni "Limiter aux gares de la ligne" ni "Limiter aux
        # trains avec retard" — voir sa docstring) plutôt que filtered_df().
        # start_date (rapporté par le flux temps réel) donne la vraie date de
        # circulation — la date encodée dans le trip_id est un identifiant
        # technique qui ne correspond pas forcément au jour réel du trajet.
        # Un même trip_id peut être réutilisé pour plusieurs circulations
        # réelles (ex: le même service un jour puis un autre) : grouper par
        # trip_id seul mélangerait leurs relevés — d'où le groupby sur les
        # deux colonnes ensemble.
        infos = df.groupby(["trip_id", "start_date"]).agg(train=("train", "first")).reset_index()
        # Retard max calculé sur self.df (le trajet complet, toutes gares),
        # pas sur df : sinon ce chiffre peut être inférieur à ce que montre
        # réellement le graphique "Suivi d'un train" pour ce même trajet, qui
        # lui ignore volontairement ces filtres pour afficher le trajet
        # complet (voir _render_train_tab) — un retard survenu à une gare
        # hors ligne resterait sinon invisible dans ce menu alors qu'il est
        # bien visible une fois le trajet ouvert. Basé sur la dernière valeur
        # connue par passage (derniers_par_passage), pas le maximum brut sur
        # tous les relevés — même correctif que calculer_stats_bloc, sinon
        # ce libellé afficherait une prédiction ponctuelle depuis corrigée
        # (repéré par l'utilisateur, 2026-08-03).
        retard_max_reel = derniers_par_passage(self.df).groupby(
            level=["trip_id", "start_date"]
        ).max().rename("retard_max")
        infos = infos.merge(retard_max_reel, on=["trip_id", "start_date"], how="left")

        if self.filtre_jour_retard_var.get():
            aujourdhui = datetime.now(PARIS_TZ).strftime("%Y%m%d")
            infos = infos[(infos["start_date"].astype(str) == aujourdhui) & (infos["retard_max"] > 0)]
        # Pas de total ici (ex: "12 sur 315") : ce total sans le filtre
        # "jour + retard" est déjà visible dans "Trains en retard" en haut de
        # l'appli, l'afficher à nouveau ici prêtait à confusion (315 n'est
        # pas "du jour", contrairement à ce que "sur" laissait penser).
        self.texte_filtre_jour_retard_var.set(f"Trains du jour en retard uniquement ({len(infos)} trajets)")

        self.trajet_labels = {}
        # D'abord par retard max (les cas les plus intéressants en premier),
        # puis par date la plus récente pour départager les trajets à retard
        # max égal (fréquent avec les paliers de 5 min, voir mémoire du
        # projet) — sans ce deuxième critère, l'ordre entre eux dépendait
        # arbitrairement de l'ordre de tri interne de pandas.
        ordre = infos.sort_values(["retard_max", "start_date"], ascending=[False, False])
        for _, row in ordre.iterrows():
            trip_id, start_date = row["trip_id"], row["start_date"]
            date_str = datetime.strptime(str(start_date), "%Y%m%d").strftime("%d/%m/%Y")
            label = f"{row['train']} du {date_str} (retard max {row['retard_max']:.0f} min)"
            self.trajet_labels[label] = (trip_id, start_date)
        options = list(self.trajet_labels.keys())
        self.select_trajet_combo["values"] = options
        if options and self.select_trajet_var.get() not in options:
            self.select_trajet_var.set(options[0])
            self._render_train_tab()
        elif not options and self.select_trajet_var.get():
            # Plus aucun trajet ne correspond aux filtres actuels (ex:
            # "Trains du jour en retard uniquement" retombe à 0) — sans ça,
            # la combobox et le graphique continuaient d'afficher le
            # dernier trajet sélectionné, comme s'il correspondait encore
            # aux filtres actuels (repéré par l'utilisateur, 2026-07-31).
            self.select_trajet_var.set("")
            self._render_train_tab()

    def _infos_trajet_depuis_route(self, route):
        """À partir de la liste réelle des gares parcourues par UNE
        circulation précise (route, déjà dans l'ordre réel de circulation —
        voir trajet_gares), repère si elle entre/sort de la ligne par une
        gare hors des 11 (ex: Saint-Lô via Lison) — pour que la frise mette
        en évidence le tronçon réellement emprunté plutôt que de montrer les
        gares non desservies comme un simple "pas de donnée" (voir mémoire
        du projet, 2026-07-24).

        Ancienne version (repéré par l'utilisateur, 2026-08-11 — même
        session que le port FastAPI, voir app_fastapi.py) : cette méthode
        s'appelait _infos_trajet_sens et essayait de deviner une circulation
        "représentative" pour tout le Sens sélectionné (ex: "PARIS →
        CHERB"), en prenant juste la première trouvée. Mais un Sens recouvre
        plusieurs trajets physiques réellement différents (certains
        Paris-Cherbourg sautent Évreux Normandie/Bernay/Lisieux, d'autres
        s'y arrêtent) — aucun représentant unique n'est correct pour tous.
        La frise n'estompe donc plus jamais selon le Sens seul : seulement
        quand une circulation précise est réellement sélectionnée dans
        Suivi d'un train (voir _render_frise, qui construit `route` à partir
        de select_trajet_var dans ce cas, et passe None sinon).

        Retourne None si route est vide/absente. Sinon :
        (gares_sur_trajet, ordre_reel, connecteur_avant, connecteur_apres) :
        - gares_sur_trajet : sous-ensemble de GARES_LIGNE_ORDRE réellement
          desservi, dans l'ordre GARES_LIGNE_ORDRE (pour le positionnement) ;
        - ordre_reel : les mêmes gares, mais dans l'ordre réel de
          circulation (pour la légende texte, qui doit refléter le sens de
          circulation, pas la position sur la ligne) ;
        - connecteur_avant / connecteur_apres : (index dans
          GARES_LIGNE_ORDRE, nom de la gare hors ligne) si le trajet
          entre/sort de la ligne par cette extrémité, sinon None. Peuvent
          être tous les deux renseignés (une seule gare de la ligne
          traversée, hors ligne des deux côtés) ou tous les deux None (le
          trajet ne quitte jamais les 11 gares)."""
        if not route:
            return None
        gares_set = set(route)
        gares_sur_trajet = [g for g in GARES_LIGNE_ORDRE if g in gares_set]
        if not gares_sur_trajet:
            return None
        pos = {g: i for i, g in enumerate(route)}
        i_debut, i_fin = gares_sur_trajet[0], gares_sur_trajet[-1]
        premier, dernier = (i_debut, i_fin) if pos[i_debut] <= pos[i_fin] else (i_fin, i_debut)
        connecteur_avant = (GARES_LIGNE_ORDRE.index(premier), route[0]) if pos[premier] > 0 else None
        connecteur_apres = (
            (GARES_LIGNE_ORDRE.index(dernier), route[-1]) if pos[dernier] < len(route) - 1 else None
        )
        ordre_reel = sorted(gares_sur_trajet, key=lambda g: pos[g])
        return gares_sur_trajet, ordre_reel, connecteur_avant, connecteur_apres

    def _dessiner_connecteur_hors_ligne(self, xs, y_ligne, connecteur, entrant, vers_la_gauche):
        """Flèche pointillée en angle (10°, vers le haut) reliant une gare de
        la ligne à une gare hors ligne (ex: Lison -> Saint-Lô) — utilisée par
        _render_frise quand le Sens sélectionné entre/sort de la ligne par
        cette extrémité. entrant=True : la pointe touche la gare de la ligne
        (le trajet y arrive depuis l'extérieur) ; entrant=False : la pointe
        s'en éloigne (le trajet la quitte). vers_la_gauche : sens horizontal
        de la flèche — True quand cette extrémité est la gauche du tronçon
        mis en évidence (ex: Bernay -> Rouen), False quand c'est la droite
        (ex: Lison -> Saint-Lô) ; sans ça, une flèche toujours dessinée vers
        la droite pointerait par-dessus le tronçon lui-même quand le
        connecteur est sur son côté gauche. Le trait s'arrête au bord du
        point de la gare plutôt qu'à son centre, sinon la pointe se
        retrouverait cachée sous le point plein dessiné par-dessus ensuite."""
        if connecteur is None:
            return
        index_gare, nom_hors_ligne = connecteur
        x_gare = xs[index_gare]
        angle = math.radians(10)
        signe = -1 if vers_la_gauche else 1
        rayon_point, longueur = 13, 65
        x_bord = x_gare + signe * rayon_point * math.cos(angle)
        y_bord = y_ligne - rayon_point * math.sin(angle)
        x_bout = x_gare + signe * longueur * math.cos(angle)
        y_bout = y_ligne - longueur * math.sin(angle)
        depart, arrivee = ((x_bout, y_bout), (x_bord, y_bord)) if entrant else ((x_bord, y_bord), (x_bout, y_bout))
        self.frise_canvas.create_line(
            *depart, *arrivee, fill="#555555", width=2, dash=(4, 2), arrow="last", arrowshape=(8, 10, 3),
        )
        self.frise_canvas.create_text(
            x_bout + 4 * signe, y_bout, text=format_gare_frise(nom_hors_ligne),
            font=("", 7, "bold"), fill="#333333", anchor="se" if vers_la_gauche else "sw",
        )

    def _render_frise(self):
        """Vue d'ensemble des 11 gares de la ligne (toujours ces 11-là, même
        si "Limiter aux gares de la ligne" est décochée) : un point par gare,
        coloré selon le retard moyen (mêmes seuils que le tableau), pour un
        état de la ligne visible d'un coup d'œil sans changer d'onglet ni
        de filtre. Calculé sur _filtered_df_avant_retard() plutôt que
        filtered_df() : comme pour le ratio "Trains en retard" (voir cette
        méthode), inclure "Limiter aux trains avec retard" ici exclurait les
        trains ponctuels du calcul et gonflerait artificiellement la moyenne
        affichée — ça reste un vrai "état de la ligne" quel que soit l'état
        de cette case, alors que Gare/Train/Sens/gares de la ligne restent
        pris en compte (un vrai choix de périmètre, pas une exclusion qui
        fausse la moyenne). Rappelée aussi au redimensionnement (voir
        _build_frise_ligne), d'où le recalcul ici plutôt que de dépendre
        d'un df passé en paramètre par render().

        Sur l'onglet Suivi d'un train, quand une circulation précise est
        sélectionnée (voir _infos_trajet_depuis_route), les gares non
        desservies par ce trajet réel sont estompées (point creux, nom
        grisé) plutôt que traitées comme un simple "pas de donnée" (point
        gris plein, comme pour une vraie absence de relevé) — les deux
        situations ne veulent pas dire la même chose. Sur les autres
        onglets, jamais d'estompage par trajet : un Sens (ex: "PARIS →
        CHERB") recouvre plusieurs trajets physiques réellement différents
        (repéré par l'utilisateur, 2026-08-11), donc il n'existe pas de
        "trajet" unique et correct à représenter tant qu'aucune circulation
        précise n'est choisie."""
        self.frise_canvas.delete("all")
        if self.df is None:
            return
        largeur = self.frise_canvas.winfo_width()
        if largeur <= 1:
            return

        df = self._filtered_df_avant_retard()
        moyennes = df.groupby("gare")["retard_min"].mean()

        infos_trajet = None
        if self.notebook.select() == str(self.train_tab):
            selection = self.select_trajet_var.get()
            cle_trajet = self.trajet_labels.get(selection) if selection else None
            if cle_trajet is not None:
                trip_id, start_date = cle_trajet
                variante = choisir_variante(self.variantes, self.calendrier, trip_id, start_date)
                if variante:
                    infos_trajet = self._infos_trajet_depuis_route(variante["gares"])

        nb_releves_frise = int(df["retard_min"].count())
        self._tooltip_frise.texte = (
            "Retard moyen par relevé propre à chaque gare ≠ "
            "du \"Retard moyen par relevé\" affiché en haut qui est issu des filtres actifs "
            f"alors que cette frise (calculée sur {nb_releves_frise} relevés) reste "
            "toujours limitée aux 11 gares de la ligne et ignore \"Limiter aux trains avec "
            "retard\" précisément (pour donner un vrai état de la ligne). Point gris plein : "
            "aucune donnée pour cette gare sous les filtres actuels. Point creux (Suivi "
            "d'un train uniquement) : gare que le train sélectionné ne dessert pas du tout."
        )

        marge = 90
        y_ligne = 42
        n = len(GARES_LIGNE_ORDRE)
        largeur_utile = largeur - 2 * marge
        if largeur_utile <= 0:
            return
        xs = [marge + i * (largeur_utile / (n - 1)) for i in range(n)]

        if infos_trajet is None:
            self.frise_canvas.create_line(xs[0], y_ligne, xs[-1], y_ligne, fill="#888888", width=2)
            gares_sur_trajet = set(GARES_LIGNE_ORDRE)
        else:
            gares_sur_trajet_liste, ordre_reel, connecteur_avant, connecteur_apres = infos_trajet
            gares_sur_trajet = set(gares_sur_trajet_liste)
            # Ligne complète estompée, tronçon réellement emprunté mis en
            # avant par-dessus (plus épais, couleur neutre).
            self.frise_canvas.create_line(xs[0], y_ligne, xs[-1], y_ligne, fill="#dddddd", width=2)
            i_debut = GARES_LIGNE_ORDRE.index(gares_sur_trajet_liste[0])
            i_fin = GARES_LIGNE_ORDRE.index(gares_sur_trajet_liste[-1])
            if i_fin > i_debut:
                self.frise_canvas.create_line(xs[i_debut], y_ligne, xs[i_fin], y_ligne, fill="#555555", width=3)
            # Le côté (gauche/droite) dépend de quelle extrémité du tronçon
            # mis en évidence porte le connecteur — pas de "avant"/"après",
            # qui décrivent l'ordre de circulation et peuvent tomber sur
            # n'importe quel côté selon le sens du trajet (voir
            # _dessiner_connecteur_hors_ligne).
            self._dessiner_connecteur_hors_ligne(
                xs, y_ligne, connecteur_avant, entrant=True,
                vers_la_gauche=connecteur_avant is not None and connecteur_avant[0] == i_debut,
            )
            self._dessiner_connecteur_hors_ligne(
                xs, y_ligne, connecteur_apres, entrant=False,
                vers_la_gauche=connecteur_apres is not None and connecteur_apres[0] == i_debut,
            )

            segments = [format_gare_frise(g) for g in ordre_reel]
            if connecteur_avant is not None:
                segments.insert(0, f"{format_gare_frise(connecteur_avant[1])} (hors ligne)")
            if connecteur_apres is not None:
                segments.append(f"{format_gare_frise(connecteur_apres[1])} (hors ligne)")
            # x=5 plutôt que marge : aligné avec le texte du label "Retard
            # moyen des relevés par gare" au-dessus (padx=15 sur le label,
            # padx=10 sur ce canvas, donc 15-10=5 en coordonnées locales du
            # canvas) — pas avec le début de la ligne des gares.
            self.frise_canvas.create_text(
                5, 2, anchor="nw", font=("", 8, "italic"), fill="#555555",
                text="Trajet : " + " → ".join(segments),
            )

        for x, gare in zip(xs, GARES_LIGNE_ORDRE):
            retard = moyennes.get(gare)
            if pd.isna(retard):
                couleur, texte_retard = "#bbbbbb", None
            elif retard >= SEUIL_RETARD_FORT:
                couleur, texte_retard = "#ef4444", f"{format_min_sans_zero(retard)} min"
            elif retard >= SEUIL_RETARD_MOYEN:
                couleur, texte_retard = "#f97316", f"{format_min_sans_zero(retard)} min"
            else:
                couleur, texte_retard = "#22c55e", f"{format_min_sans_zero(retard)} min"

            if gare not in gares_sur_trajet:
                # Hors du trajet du Sens sélectionné : distinct d'un simple
                # "pas de donnée" (point plein gris) — point creux et nom
                # estompé, pour ne pas laisser croire que ce train pourrait
                # y passer.
                self.frise_canvas.create_oval(
                    x - 5, y_ligne - 5, x + 5, y_ligne + 5, fill="#f3f3f3", outline="#bbbbbb", width=1,
                )
                self.frise_canvas.create_text(
                    x, y_ligne + 22, text=format_gare_frise(gare), font=("", 8), fill="#aaaaaa",
                )
                continue

            self.frise_canvas.create_oval(
                x - 9, y_ligne - 9, x + 9, y_ligne + 9, fill=couleur, outline="#555555", width=1,
            )
            # Pas de "-" au-dessus des gares sans donnée : à cette taille de
            # police, un simple tiret rendait comme un petit point disgracieux
            # — le point gris suffit déjà à signaler l'absence de donnée.
            if texte_retard is not None:
                self.frise_canvas.create_text(
                    x, y_ligne - 16, text=texte_retard, font=("", 8, "bold"), fill="#333333",
                )
            # Horizontal plutôt qu'incliné : le rendu du texte pivoté
            # (angle=...) sur le canvas Tk tronquait les caractères accentués
            # (ex: "Évreux" perdait son É) — non-accentué maintenant, mais le
            # nom complet horizontal tient déjà sans chevaucher, donc pas
            # besoin de revenir à l'inclinaison.
            self.frise_canvas.create_text(
                x, y_ligne + 22, text=format_gare_frise(gare), font=("", 8), fill="#333333",
            )

    def _on_toggle_limiter_ligne(self):
        if self.limiter_ligne_var.get():
            self.status_var.set(f"Gares hors ligne masquées (limité aux {len(GARES_LIGNE)} gares de la ligne).")
        else:
            self.status_var.set(
                "Gares hors ligne incluses : les gares grisées (trains de jonction, "
                "ex: vers Rouen, Rennes, Granville...) apparaissent maintenant partout dans l'appli."
            )
        self.render()

    def reset_filters(self):
        self.filtre_gare_var.set("Toutes")
        self.filtre_train_var.set("Tous")
        self.filtre_sens_var.set("Tous")
        self.limiter_ligne_var.set(True)
        self.limiter_retard_var.set(True)
        self.filtre_jour_retard_var.set(True)
        self.status_var.set("Filtres réinitialisés.")
        self.render()

    def _filtered_df_avant_retard(self, appliquer_limite_ligne=True):
        """Comme filtered_df(), mais sans appliquer "Limiter aux trains avec
        retard" — sert de base au calcul du ratio de trains en retard, qui
        deviendrait trivialement 100 % s'il incluait ce filtre-là.
        appliquer_limite_ligne=False : sans "Limiter aux gares de la ligne"
        non plus — utilisé par _filtered_df_pour_trajets (voir plus bas)."""
        if self.df is None:
            return None
        df = self.df
        gare = self.filtre_gare_var.get()
        # Les entrées "— ... —" ne sont que des séparateurs visuels dans la
        # liste, pas un vrai filtre — les traiter comme "Toutes" si jamais
        # sélectionnées (ex: navigation au clavier dans la liste déroulante).
        if gare and gare != "Toutes" and not gare.startswith("—"):
            df = df[df["gare"] == gare]
        train = self.filtre_train_var.get()
        if train and train != "Tous":
            df = df[df["train"] == train]
        sens = self.filtre_sens_var.get()
        if sens and sens != "Tous":
            df = df[df["sens"] == sens]
        if appliquer_limite_ligne and self.limiter_ligne_var.get():
            df = df[df["gare"].isin(GARES_LIGNE)]
        return df

    def _filtered_df_pour_trajets(self):
        """Base df pour _update_trajet_list (menu "Trajet" de Suivi d'un
        train) : Gare/Train/Sens seulement, sans "Limiter aux gares de la
        ligne" ni "Limiter aux trains avec retard". Ces deux cases pilotent
        les vues agrégées (Tableau/Graphique/Par jour-heure) ; Suivi d'un
        train affiche déjà le trajet réel complet d'une circulation quel que
        soit leur état (voir _render_train_tab), et a sa propre case dédiée
        "Trains du jour en retard uniquement" pour filtrer sur le retard —
        sans ce contournement, une circulation avec un vrai retard (visible
        dans son "retard max", recalculé sur self.df) pouvait disparaître de
        la liste simplement parce qu'aucun de ses relevés en retard ne
        tombait sur une gare de la ligne, ou parce que "Limiter aux trains
        avec retard" était coché — deux incohérences repérées par
        l'utilisateur, 2026-08-03."""
        return self._filtered_df_avant_retard(appliquer_limite_ligne=False)

    @staticmethod
    def _restreindre_aux_trains_en_retard(df):
        """Ne garde que les circulations ayant eu au moins un retard > 0
        quelque part dans df. Un même trip_id peut être réutilisé pour
        plusieurs circulations réelles (voir _update_trajet_list) — se
        limiter au trip_id inclurait à tort une circulation ponctuelle d'un
        jour donné simplement parce que le même trip_id a eu du retard un
        autre jour. D'où la clé combinée trip_id + start_date. Factorisé pour
        être appelable indépendamment de "Limiter aux trains avec retard"
        (voir _render_chart : la comparaison "trains en retard uniquement"
        doit rester fixe, peu importe l'état de cette case)."""
        circulation = cle_circulation(df)
        circulations_en_retard = circulation[df["retard_min"] > 0].unique()
        return df[circulation.isin(circulations_en_retard)]

    def filtered_df(self):
        df = self._filtered_df_avant_retard()
        if df is None:
            return None
        if self.limiter_retard_var.get():
            df = self._restreindre_aux_trains_en_retard(df)
        return df

    # --- Affichage ---
    def render(self):
        df = self.filtered_df()
        if df is None:
            return

        date_debut_collecte = format_poll_time(self.df["poll_time"].iloc[0]).split(" à ")[0]
        self.summary_var.set(
            f"{len(df)} relevés (sur {len(self.df)} au total, depuis le "
            f"{date_debut_collecte}) — "
            f"dernier relevé : {format_poll_time(self.df['poll_time'].iloc[-1])}"
        )
        # Le Graphique et Par jour/heure montrent une tendance/moyenne dans le
        # temps : comme pour la frise, les calculer sur df (qui applique
        # "Limiter aux trains avec retard") gonflerait artificiellement la
        # courbe en excluant les trains ponctuels — on utilise donc la même
        # base que la frise, sans ce filtre-là précisément.
        df_pour_tendances = self._filtered_df_avant_retard()

        self._render_stats(df)
        self._render_table(df)
        self._render_chart(df_pour_tendances)
        self._render_jour_heure_tab(df_pour_tendances)
        self._update_trajet_list(self._filtered_df_pour_trajets())
        self._render_frise()
        self._render_travaux_tab()
        self._render_verification_gtfs_tab()

    def _render_stats(self, df):
        # Calculé à part sur les données d'avant "Limiter aux trains avec
        # retard" (voir _filtered_df_avant_retard) : sinon, dès que cette
        # case est cochée, le ratio afficherait toujours 100 %.
        df_avant_retard = self._filtered_df_avant_retard()
        stats_ratio = calculer_stats_bloc(df_avant_retard)
        total_trains = stats_ratio["total"]
        if total_trains == 0:
            self.stat_ratio_retard_var.set("Circulations perturbées : -")
            self._tooltip_ratio_retard.texte = ""
        else:
            circulations_perturbees = stats_ratio["en_retard"]
            self.stat_ratio_retard_var.set(
                f"{circulations_perturbees} circulations perturbées / {total_trains} "
                f"({100 * circulations_perturbees / total_trains:.0f} %)"
            )
            # sans_date_trip_id : le trip_id brut se termine par un suffixe
            # de date qui change chaque jour pour un même train réel — un
            # .nunique() direct comptait (motifs de train × jours observés),
            # incomparable à len(self.variantes) (indexé sans cette
            # date) — bug réel corrigé ici (et côté app_fastapi.py),
            # 2026-08-10 : affichait 562/562 alors que le vrai chiffre est
            # 478/562.
            nb_trains_observes = df_avant_retard["trip_id"].map(sans_date_trip_id).nunique()
            self._tooltip_ratio_retard.texte = (
                f"{circulations_perturbees} circulations perturbées (retard à un moment de "
                f"leur trajet, même rattrapé ensuite) sur {total_trains} déjà observées "
                f"depuis le début de la collecte (issues de {nb_trains_observes} trains "
                f"différents parmi les {len(self.variantes)} du référentiel), soit "
                f"{100 * circulations_perturbees / total_trains:.0f} %."
            )

        if df["retard_min"].dropna().empty:
            self.stat_cumule_var.set("Retard cumulé : -")
            self.stat_moyen_var.set("Retard moyen / relevé : -")
            self.stat_max_var.set("Retard max : -")
            self.stat_pire_gare_var.set("Gare la + touchée : -")
            self._tooltip_moyen.texte = ""
            self._tooltip_pire_gare.texte = ""
            return

        stats = calculer_stats_bloc(df)
        self.stat_cumule_var.set(
            f"Retard cumulé : {stats['heures']} h {stats['minutes']:02d} min "
            f"({stats['nb_passages_impactes']} passages impactés)"
        )
        self.stat_moyen_var.set(f"Retard moyen / relevé : {stats['moyen']:.1f} min")
        self.stat_max_var.set(f"Retard max : {stats['retard_max_texte']}")
        self.stat_pire_gare_var.set(f"Gare la + touchée : {stats['pire_gare_texte']}")

        nb_releves = int(df["retard_min"].count())
        self._tooltip_moyen.texte = (
            f"Moyenne brute sur les {nb_releves} relevés issus des filtres actifs "
            "ci-dessus, pas seulement sur les 300 dernières lignes affichées dans le "
            "tableau — un même passage réel est vu à plusieurs relevés tant qu'il reste "
            "dans la fenêtre du flux temps réel, d'où une moyenne \"par relevé\" très "
            "diluée par rapport au retard cumulé réel."
        )
        self._tooltip_pire_gare.texte = (
            f"Gare avec le retard moyen / relevé le plus élevé, sur les {nb_releves} "
            "relevés issus des filtres actifs ci-dessus (pas seulement les 300 dernières lignes "
            "affichées dans le tableau)."
        )

    def _render_table(self, df):
        self.tree.delete(*self.tree.get_children())
        # Tri stable par relevé décroissant (plus récent en premier) : contrairement
        # à un simple .iloc[::-1], ça préserve l'ordre d'origine (séquence des
        # gares) entre lignes qui partagent le même horodatage — sinon un train
        # capté d'un coup affichait sa dernière gare avant sa première.
        recent = df.tail(300).sort_values("poll_time", ascending=False, kind="stable")
        dernier_groupe = None
        for _, row in recent.iterrows():
            groupe = (row["train"], row["poll_time"])
            if dernier_groupe is not None and groupe != dernier_groupe:
                self.tree.insert("", "end", tags=("separateur",), values=[""] * len(self.tree["columns"]))
            dernier_groupe = groupe

            retard = row["retard_min"]
            retard_depart = row["retard_depart_min"]
            tags = []
            if row["gare"] not in GARES_LIGNE:
                tags.append("hors_ligne")
            elif pd.notna(retard) and retard >= SEUIL_RETARD_FORT:
                tags.append("retard_fort")
            elif pd.notna(retard) and retard >= SEUIL_RETARD_MOYEN:
                tags.append("retard_moyen")
            elif pd.notna(retard_depart) and retard_depart >= SEUIL_RETARD_MOYEN:
                tags.append("depart_retard")
            self.tree.insert("", "end", tags=tuple(tags), values=(
                format_poll_time(row["poll_time"]),
                row["train"],
                row["sens"],
                format_gare(row["gare"]),
                row.get("heure_theorique", ""),
                format_retard(row["retard_arrivee_min"]),
                format_retard(row["retard_depart_min"]),
                format_valeur(row.get("temperature_c")),
                format_valeur(row.get("precipitation_mm")),
                format_valeur(row.get("wind_speed_kmh")),
                format_valeur(row.get("type_jour")),
                format_bool_oui_non(row.get("vacances_scolaires")),
                format_entier(row.get("arrets_restants")),
            ))
        self._autofit_columns()

    def _autofit_columns(self, padding=20, largeur_max=400):
        """Redimensionne chaque colonne à la largeur de son contenu le plus
        large (entête compris), plutôt que des largeurs fixées à l'avance."""
        for col in self.tree["columns"]:
            largeur = self.table_font.measure(self.tree.heading(col)["text"])
            for item in self.tree.get_children():
                valeur = str(self.tree.set(item, col))
                largeur = max(largeur, self.table_font.measure(valeur))
            self.tree.column(col, width=min(largeur + padding, largeur_max))
        self._ajuster_largeur_fenetre()

    def _ajuster_largeur_fenetre(self):
        """Ajuste la largeur de la fenêtre à celle du tableau (somme des
        colonnes + barre de défilement) et, si besoin, à celle des barres de
        stats/filtres ou de l'onglet Graphique (dont la largeur ne dépend
        pas des colonnes du tableau — la ligne de stats de cet onglet
        grandit avec la période choisie, ex: "tout l'historique" a des
        chiffres bien plus longs que "dernières 24h", repéré par
        l'utilisateur, 2026-07-30) — sinon leur dernier libellé reste
        tronqué malgré une fenêtre "assez large" pour le tableau. La hauteur n'est qu'agrandie
        si le contenu (ex: la frise en bas) ne tient plus, jamais réduite
        en dessous de ce que l'utilisateur a déjà choisi — mais plafonnée à
        la hauteur de l'écran, sinon le bas de la fenêtre (frise, statut)
        sort de l'écran plutôt que de servir à rien (ex: l'onglet 'Par jour
        / heure', assez grand avec ses 6 graphiques pour dépasser un écran
        1080p, voir mémoire du projet 2026-07-23)."""
        largeur_colonnes = sum(self.tree.column(col, "width") for col in self.tree["columns"])
        largeur_scrollbar = 20
        marges = 30  # padding de part et d'autre de la fenêtre
        largeur_totale = largeur_colonnes + largeur_scrollbar + marges
        self.update_idletasks()
        # +20 : marge padx=10 de chaque côté des frames stats/filtres
        # elles-mêmes (non comptée dans leur reqwidth, qui ne mesure que
        # leur contenu) — sans ça, le dernier libellé de chaque ligne reste
        # tronqué de quelques caractères malgré une fenêtre "assez large".
        largeur_totale = max(
            largeur_totale,
            self.stats_frame.winfo_reqwidth() + 20,
            self.filters_frame.winfo_reqwidth() + 20,
            self.chart_tab.winfo_reqwidth() + 20,
        )
        largeur_totale = min(largeur_totale, self.winfo_screenwidth() - 100)
        hauteur = max(self.winfo_height(), self.winfo_reqheight())
        hauteur = min(hauteur, self.winfo_screenheight() - 50)
        self.geometry(f"{largeur_totale}x{hauteur}")

    def _empaqueter_segments(self, segments, largeur_max_px):
        """Assemble des segments de texte déjà unitaires (ex: "Retard max :
        train X → Y min") en lignes séparées par " · ", en repoussant un
        segment entier à la ligne suivante dès que la ligne dépasserait
        largeur_max_px — contrairement au wraplength natif de Tkinter, qui
        coupe au mot le plus proche sans notion de segment, quitte à couper
        un segment en plein milieu (ex: "Retard max : train" sur une ligne,
        l'identifiant du train sur la suivante — repéré par l'utilisateur,
        2026-07-30). Renvoie la liste des lignes (pas une chaîne déjà
        jointe) : l'appelant en a besoin pour styler différemment le premier
        segment (voir _afficher_stats_periode)."""
        lignes = []
        ligne_courante = []
        for segment in segments:
            essai = " · ".join(ligne_courante + [segment])
            if ligne_courante and self._police_stats_periode.measure(essai) > largeur_max_px:
                lignes.append(" · ".join(ligne_courante))
                ligne_courante = [segment]
            else:
                ligne_courante.append(segment)
        if ligne_courante:
            lignes.append(" · ".join(ligne_courante))
        return lignes

    def _afficher_stats_periode(self, segments):
        """Affiche `segments` dans le widget Text de la ligne de stats de
        l'onglet Graphique, avec le premier segment ("circulations
        perturbées") en couleur accent — même couleur que la stat
        équivalente de la barre du haut. Un ttk.Label ne sait pas mélanger
        deux couleurs dans un même texte (contrairement à un widget Text),
        d'où ce widget dédié plutôt qu'un simple textvariable — demande
        explicite de l'utilisateur, 2026-07-30."""
        texte = self.texte_stats_periode
        texte.config(state="normal")
        texte.delete("1.0", "end")
        lignes = self._empaqueter_segments(segments, self.LARGEUR_STATS_PERIODE)
        if lignes:
            premiere_ligne = lignes[0]
            longueur_premier_segment = len(segments[0]) if segments else 0
            texte.insert("end", premiere_ligne[:longueur_premier_segment], "accent")
            texte.insert("end", premiere_ligne[longueur_premier_segment:])
            for ligne in lignes[1:]:
                texte.insert("end", "\n" + ligne)
        texte.config(state="disabled", height=max(len(lignes), 1))

    def _incruster_travaux(self, ax, debut_visible, fin_visible, gares_visibles):
        """Grise en fond les périodes où une alerte SNCF (travaux, incident...
        voir alertes.csv/collect_alertes.py) concernant une gare de la ligne
        était active, pour repérer d'un coup d'œil si un pic de retard
        coïncide avec une perturbation connue. Deux restrictions par rapport
        à "toutes les alertes connues" :
        - gares_visibles (les gares du df déjà filtré par Gare/Train/Sens,
          voir _render_chart) : une alerte sur une gare hors de la sélection
          actuelle n'a rien à faire sur ce graphique précis (ex: une alerte
          "Paris St-Lazare" ne concerne pas un Sens "Lisieux -> Caen").
        - bornes recadrées sur [debut_visible, fin_visible] (la période
          affichée) : sans ça, une alerte plus longue que la période choisie
          élargirait l'axe des x au-delà des données réellement tracées.
        Retourne True si au moins une période a été dessinée, pour que
        l'appelant n'ajoute l'entrée de légende correspondante qu'une fois."""
        dessine = False
        for _, alerte in self.alertes.iterrows():
            gares_alerte = {g.strip() for g in alerte["gares"].split(",")} if alerte["gares"] else set()
            if not gares_alerte & gares_visibles:
                continue
            debut = alerte["debut"] if pd.notna(alerte["debut"]) else debut_visible
            fin = alerte["fin"] if pd.notna(alerte["fin"]) else fin_visible
            debut_clip, fin_clip = max(debut, debut_visible), min(fin, fin_visible)
            if debut_clip >= fin_clip:
                continue  # ne recoupe pas la période affichée
            patch = ax.axvspan(debut_clip, fin_clip, color="#a78bfa", alpha=0.15, zorder=0)
            self._survol_graphique.enregistrer(patch, f"{alerte['gares']} — {alerte['texte']}")
            dessine = True
        return dessine

    def _render_chart(self, df):
        self.ax.clear()
        self.ax2.clear()
        # Les artistes de l'ancien rendu sont détruits par ax.clear() —
        # invalide toute référence gardée pour les info-bulles au survol.
        self._survol_graphique.vider()
        # ax.clear() réinitialise aussi les réglages de graduations : sans
        # ça, les heures réapparaîtraient sur l'axe du haut, en double avec
        # celles déjà affichées sous le graphique du bas (axe X partagé).
        self.ax.tick_params(axis="x", labelbottom=False)
        # figure.suptitle() n'est pas un artiste de l'axe : contrairement au
        # titre (effacé par ax.clear() ci-dessus), il resterait affiché tout
        # seul si plot_df s'avère vide plus bas — on le vide par défaut, et
        # on ne le renseigne à nouveau que si on a bien quelque chose à tracer.
        self.figure.suptitle("")
        self._afficher_stats_periode([])
        self.nb_releves_periode_var.set("")
        # Gares réellement présentes dans la sélection actuelle (df est déjà
        # filtré par Gare/Train/Sens) : sert à ne montrer que les alertes qui
        # concernent cette sélection précise, voir _incruster_travaux.
        gares_visibles = set(df["gare"].dropna().unique())
        plot_df = df.dropna(subset=["retard_min"]).copy()
        if not plot_df.empty:
            plot_df["poll_time"] = pd.to_datetime(plot_df["poll_time"])

            # Afficher tout l'historique depuis le tout premier relevé devient
            # vite illisible/lourd au fil des jours — on se limite par défaut à
            # une période récente, choisie par l'utilisateur.
            duree_par_periode = {
                "dernières 24h": pd.Timedelta(hours=24),
                "3 derniers jours": pd.Timedelta(days=3),
                "7 derniers jours": pd.Timedelta(days=7),
            }
            duree = duree_par_periode.get(self.periode_graphique_var.get())
            if duree is not None:
                plot_df = plot_df[plot_df["poll_time"] >= plot_df["poll_time"].max() - duree]

            debut_visible, fin_visible = plot_df["poll_time"].min(), plot_df["poll_time"].max()

            # Une ligne brute par observation mélangerait des trains/gares
            # différents sur la même courbe (des sauts sans rapport les uns
            # avec les autres) — on agrège plutôt par relevé (retard moyen de
            # tout ce qui est sélectionné à cet instant), une vraie tendance.
            moyenne_par_releve = plot_df.groupby("poll_time")["retard_min"].mean().sort_index()
            tracer_serie_temporelle(self.ax, moyenne_par_releve, "#1f77b4")
            travaux_incrustes = self._incruster_travaux(self.ax, debut_visible, fin_visible, gares_visibles)

            legende = [
                Line2D([0], [0], color="#1f77b4", linewidth=1, marker="o", markersize=1.5, label="Relevés"),
                Line2D([0], [0], color="#bbbbbb", linewidth=1, linestyle="--", label="Absence de relevés"),
            ]
            if travaux_incrustes:
                legende.append(matplotlib.patches.Patch(color="#a78bfa", alpha=0.3, label="Travaux / alerte SNCF"))
            self.ax.legend(handles=legende, loc="upper left", fontsize=8)
            self.ax.set_ylabel("Retard moyen (min)")

            # Deuxième graphique : proportion de circulations en retard à
            # chaque relevé (pas la même notion que "Limiter aux trains avec
            # retard", qui regarde tout le trajet — ici c'est un vrai
            # instantané par relevé). Le retard moyen seul ne dit pas si un
            # pic vient d'un seul train très en retard ou de beaucoup de
            # trains légèrement en retard ; cette proportion répond à cette
            # question complémentaire (voir mémoire du projet, 2026-07-23).
            plot_df["circulation"] = cle_circulation(plot_df)

            # Mêmes stats (et mêmes libellés, voir mémoire du projet
            # 2026-07-28) que la barre du haut, mais limitées à cette période
            # (voir _build_chart) — recalculées à chaque rendu pour suivre le
            # sélecteur de période (24h/3j/7j/tout l'historique). Même calcul
            # que _render_stats, voir calculer_stats_bloc.
            stats_periode = calculer_stats_bloc(plot_df)

            self.nb_releves_periode_var.set(f"soit {len(plot_df)} relevés")
            segments_periode = [
                f"{stats_periode['en_retard']}/{stats_periode['total']} circulations perturbées "
                f"({100 * stats_periode['en_retard'] / stats_periode['total']:.0f} %)",
                f"Retard cumulé : {stats_periode['heures']} h {stats_periode['minutes']:02d} min "
                f"({stats_periode['nb_passages_impactes']} passages impactés)",
                f"Retard moyen / relevé : {stats_periode['moyen']:.1f} min",
                f"Retard max : {stats_periode['retard_max_texte']}",
                f"Gare la + touchée : {stats_periode['pire_gare_texte']}",
            ]
            self._afficher_stats_periode(segments_periode)

            def _pct_en_retard(groupe):
                total = groupe["circulation"].nunique()
                en_retard = groupe.loc[groupe["retard_min"] > 0, "circulation"].nunique()
                return 100 * en_retard / total if total else float("nan")

            pct_par_releve = plot_df.groupby("poll_time").apply(_pct_en_retard, include_groups=False).sort_index()
            tracer_serie_temporelle(self.ax2, pct_par_releve, "#c2410c")
            self._incruster_travaux(self.ax2, debut_visible, fin_visible, gares_visibles)
            self.ax2.set_ylabel("% trains en retard")
            self.ax2.set_xlabel("Heure du relevé")
            # Rappelle les filtres Gare/Train/Sens actifs dans les titres,
            # comme déjà fait pour Sens seul — sans ça, un graphique filtré
            # sur une seule gare/un seul train se lit comme s'il portait sur
            # toute la ligne (demande explicite de l'utilisateur, 2026-07-29).
            gare = self.filtre_gare_var.get()
            train = self.filtre_train_var.get()
            sens = self.filtre_sens_var.get()
            elements_filtres = []
            if gare and gare != "Toutes" and not gare.startswith("—"):
                elements_filtres.append(f"Gare {gare}")
            if train and train != "Tous":
                elements_filtres.append(f"Train {train}")
            if sens and sens != "Tous":
                elements_filtres.append(sens)
            suffixe_filtres = f" — {' · '.join(elements_filtres)}" if elements_filtres else ""
            self.ax2.set_title("Évolution de la proportion de trains en retard" + suffixe_filtres, fontsize=10)

            periode_texte = self.periode_graphique_var.get()
            self.figure.suptitle("Évolution du retard moyen dans le temps" + suffixe_filtres, fontsize=11, y=0.97)
            self.ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %Hh", tz=PARIS_TZ))
            if periode_texte == "dernières 24h":
                # Une graduation par heure plutôt que l'espacement automatique
                # (~3h) : reste lisible sur 24h, mais deviendrait un magma de
                # texte illisible sur 3j/7j/tout l'historique (72/168+
                # graduations) — donc limité à cette période (voir mémoire du
                # projet, 2026-07-24). Date répétée à chaque heure ("23/07
                # 17h", "23/07 18h"...) serait redondante sur 24 graduations
                # — la date n'est donc affichée qu'au passage à minuit
                # ("24/07 00h"), les autres heures restant juste "17h", "18h".
                self.ax2.xaxis.set_major_locator(mdates.HourLocator(tz=PARIS_TZ))
                self.ax2.xaxis.set_major_formatter(FuncFormatter(
                    lambda x, pos: mdates.num2date(x, tz=PARIS_TZ).strftime(
                        "%d/%m %Hh" if mdates.num2date(x, tz=PARIS_TZ).hour == 0 else "%Hh"
                    )
                ))
            # 10 min plutôt que le plancher par défaut (pensé pour un axe par
            # gares, voir _finalize_axes) : sur un axe temporel, une marge de
            # 10 min reste imperceptible même sur "dernières 24h", tout en
            # empêchant une marge nulle si jamais un seul point était tracé.
            MARGE_X_MIN_TEMPS = 10 / (24 * 60)  # 10 min, en jours (unité de l'axe date de matplotlib)
            finalize_axes(self.ax, marge_bas=True, marge_x_min=MARGE_X_MIN_TEMPS)
            finalize_axes(self.ax2, marge_bas=True, marge_x_min=MARGE_X_MIN_TEMPS)
            marquer_maximum(
                self.ax, moyenne_par_releve, "#1f77b4", " min",
                "Indique à quel moment la moyenne, tous trains confondus, a été la plus haute. "
                "C'est différent du \"Retard max\" affiché en haut de l'appli, qui est le "
                "pire retard d'un seul train à un instant donné, pas une moyenne. Les deux "
                "se complètent : l'un dit \"le pire cas isolé jamais vu\", l'autre dit "
                "\"le pire moment pour la ligne dans son ensemble\".",
                survol=self._survol_graphique,
            )
            marquer_maximum(
                self.ax2, pct_par_releve, "#c2410c", " %",
                "Indique le pic de trains simultanément en retard.",
                survol=self._survol_graphique,
            )
            marquer_moyenne(
                self.ax, moyenne_par_releve, "#1f77b4", " min",
                "Moyenne sur la période actuellement affichée (24h/3j/7j/tout l'historique, "
                "selon le sélecteur ci-dessus) — sert de repère pour juger si un point de la "
                "courbe est au-dessus ou en dessous de la tendance générale de cette période.",
                survol=self._survol_graphique,
            )
            marquer_moyenne(
                self.ax2, pct_par_releve, "#c2410c", " %",
                "Moyenne sur la période actuellement affichée (24h/3j/7j/tout l'historique, "
                "selon le sélecteur ci-dessus) — sert de repère pour juger si un point de la "
                "courbe est au-dessus ou en dessous de la tendance générale de cette période.",
                survol=self._survol_graphique,
            )
            self.figure.autofmt_xdate()
        self.canvas.draw()

    SEUIL_FIABLE = 30  # nb minimal de relevés pour considérer une barre fiable

    @staticmethod
    def _stats_par_categorie(df, colonne, ordre=None):
        """Pour chaque valeur de `colonne` : retard moyen, nombre de relevés
        (n) et % de circulations en retard — la base commune aux 6 graphiques
        de l'onglet 'Par jour / heure'."""
        groupes = df.groupby(colonne)
        moyenne = groupes["retard_min"].mean()
        n = groupes.size()
        pct = groupes.apply(
            lambda g: 100 * g.loc[g["retard_min"] > 0, "circulation"].nunique() / g["circulation"].nunique(),
            include_groups=False,
        )
        stats = pd.DataFrame({"moyenne": moyenne, "n": n, "pct": pct})
        return stats.reindex(ordre) if ordre is not None else stats

    def _tracer_barres_fiabilite(self, ax, stats, labels, colonne, couleur, ylabel, titre, xlabel=None,
                                  unite="", afficher_moyenne=False):
        """Barres colorées, hachurées en gris pour celles reposant sur moins
        de SEUIL_FIABLE relevés (ex: une seule circulation matinale peut
        donner un % en retard de 0 ou 100 %, pas représentatif). Le détail
        (valeur + nombre de relevés) est en info-bulle au survol de chaque
        barre (voir SurvolArtistes) plutôt qu'annoté en permanence, pour ne
        pas surcharger visuellement (voir mémoire du projet, 2026-07-23).
        afficher_moyenne : ligne pointillée de référence (voir
        _marquer_moyenne) — réservée aux graphiques à plusieurs catégories
        (jour de semaine, heure) où elle aide à repérer les barres au-dessus/
        en dessous de la tendance ; pas utile sur une comparaison à 2 barres
        seulement (ouvré/weekend, vacances/hors vacances), où elle
        n'ajouterait rien à la comparaison directe des deux barres entre
        elles — demande explicite de l'utilisateur, 2026-07-30."""
        x = range(len(stats))
        valeurs = stats[colonne].values
        ns = stats["n"].fillna(0).values
        couleurs = [couleur if n >= self.SEUIL_FIABLE else "#dddddd" for n in ns]
        barres = ax.bar(x, valeurs, color=couleurs)
        for barre, n in zip(barres, ns):
            if n < self.SEUIL_FIABLE:
                barre.set_hatch("//")
                barre.set_edgecolor("#999999")
        for barre, label, valeur, n in zip(barres, labels, valeurs, ns):
            if pd.notna(valeur):
                texte = f"{label} : {valeur:.1f}{unite} (n={int(n)} relevés)"
                self._survol_jour_heure.enregistrer(barre, texte)
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, fontsize=8)
        ax.set_ylabel(ylabel, fontsize=9)
        ax.set_title(titre, fontsize=9)
        if xlabel:
            ax.set_xlabel(xlabel)
        ax.axhline(0, color="gray", linewidth=0.6)
        finalize_axes(ax)
        if afficher_moyenne:
            marquer_moyenne(
                ax, pd.Series(valeurs), couleur, unite,
                "Moyenne de toutes les barres de ce graphique — sert de repère pour comparer "
                "chaque catégorie à la tendance générale.",
                survol=self._survol_jour_heure,
            )

    def _render_jour_heure_tab(self, df):
        axes = (self.jour_ax, self.heure_ax, self.jour_pct_ax, self.heure_pct_ax,
                self.type_jour_ax, self.vacances_ax)
        for ax in axes:
            ax.clear()
        # Les barres de l'ancien rendu sont détruites par ax.clear() —
        # invalide toute référence gardée pour les info-bulles au survol.
        self._survol_jour_heure.vider()
        plot_df = df.dropna(subset=["retard_min"]).copy()
        if plot_df.empty:
            self.jour_heure_canvas.draw()
            return
        plot_df["circulation"] = cle_circulation(plot_df)

        jours_ordre = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
        plot_df["jour_semaine"] = pd.to_datetime(
            plot_df["start_date"], format="%Y%m%d"
        ).dt.dayofweek.map(dict(enumerate(jours_ordre)))
        stats_jour = self._stats_par_categorie(plot_df, "jour_semaine", jours_ordre)
        self._tracer_barres_fiabilite(
            self.jour_ax, stats_jour, [j[:3] for j in jours_ordre], "moyenne",
            "#4a7fb5", "Retard moyen (min)", "Retard moyen par jour de semaine", unite=" min",
            afficher_moyenne=True,
        )
        self._tracer_barres_fiabilite(
            self.jour_pct_ax, stats_jour, [j[:3] for j in jours_ordre], "pct",
            "#c2410c", "% trains en retard", "% en retard par jour de semaine", unite=" %",
            afficher_moyenne=True,
        )

        plot_df["heure"] = pd.to_datetime(plot_df["poll_time"]).dt.tz_convert(PARIS_TZ).dt.hour
        stats_heure = self._stats_par_categorie(plot_df, "heure", list(range(24)))
        self._tracer_barres_fiabilite(
            self.heure_ax, stats_heure, [str(h) for h in range(24)], "moyenne",
            "#5ba58c", "Retard moyen (min)", "Retard moyen par heure", unite=" min",
            afficher_moyenne=True,
        )
        self._tracer_barres_fiabilite(
            self.heure_pct_ax, stats_heure, [str(h) for h in range(24)], "pct",
            "#c2410c", "% trains en retard", "% en retard par heure", xlabel="Heure (locale)", unite=" %",
            afficher_moyenne=True,
        )

        # "ouvre" (sans accent) est une ancienne graphie du même statut que
        # "ouvré", voir mémoire du projet (glitch d'encodage corrigé depuis
        # côté collecteur, mais présent dans les vieilles lignes).
        type_jour_map = {"ouvré": "Ouvré", "ouvre": "Ouvré", "weekend": "Weekend/Férié", "férié": "Weekend/Férié"}
        plot_df["type_jour_simple"] = plot_df["type_jour"].map(type_jour_map)
        stats_type_jour = self._stats_par_categorie(
            plot_df.dropna(subset=["type_jour_simple"]), "type_jour_simple", ["Ouvré", "Weekend/Férié"],
        )
        self._tracer_barres_fiabilite(
            self.type_jour_ax, stats_type_jour, list(stats_type_jour.index), "moyenne",
            "#8a5cb5", "Retard moyen (min)", "Jour ouvré vs Weekend/Férié", unite=" min",
        )

        # Comparaison par égalité (==), pas par identité (is True) : selon la
        # présence ou non de NaN dans la colonne, pandas peut charger ces
        # valeurs en bool Python natif ou en numpy.bool_, pour lesquels
        # `numpy.bool_(True) is True` vaut False (même piège que
        # format_bool_oui_non, voir l'audit de code du 2026-07-18).
        plot_df["vacances_texte"] = plot_df["vacances_scolaires"].apply(
            lambda v: "Vacances" if v == True else ("Hors vacances" if v == False else None)  # noqa: E712
        )
        stats_vacances = self._stats_par_categorie(
            plot_df.dropna(subset=["vacances_texte"]), "vacances_texte", ["Hors vacances", "Vacances"],
        )
        self._tracer_barres_fiabilite(
            self.vacances_ax, stats_vacances, list(stats_vacances.index), "moyenne",
            "#b58a2c", "Retard moyen (min)", "Vacances vs Hors vacances", unite=" min",
        )

        # rect=(...,0.985,...) : réserve une petite marge à droite — sans
        # elle, l'étiquette "moy." des graphiques de la colonne de droite
        # (positionnée au bord droit de leur propre axe, donc tout près du
        # bord de la figure) pouvait se faire tronquer par tight_layout, qui
        # ne tient pas toujours bien compte de l'encombrement réel d'une
        # annotation en coordonnées mixtes ("axes fraction", "data") —
        # repéré par l'utilisateur, 2026-07-30.
        self.jour_heure_figure.tight_layout(rect=(0, 0, 0.985, 1))
        self.jour_heure_canvas.draw()

    def _render_train_tab(self):
        self.train_ax.clear()
        selection = self.select_trajet_var.get()
        if not selection or self.df is None:
            self.depart_arrivee_var.set("")
            self.train_canvas.draw()
            return

        cle_trajet = self.trajet_labels.get(selection)
        if cle_trajet is None:
            self.depart_arrivee_var.set("")
            self.train_canvas.draw()
            return
        trip_id, start_date = cle_trajet
        # Filtré sur trip_id ET start_date : un même trip_id peut être
        # réutilisé pour plusieurs circulations réelles (voir
        # _update_trajet_list) — se limiter au trip_id mélangerait leurs
        # relevés, faussant tout le reste (retards, figé/pas encore atteint).
        trajet = self.df[(self.df["trip_id"] == trip_id) & (self.df["start_date"] == start_date)].copy()

        # Toutes les gares théoriques du trajet (horaires), même celles jamais
        # observées en temps réel (ex: Paris Saint-Lazare, voir README/mémoire) —
        # sinon la dernière gare *observée* donne l'illusion d'être le terminus.
        variante = choisir_variante(self.variantes, self.calendrier, trip_id, start_date)
        ordre_gares = variante["gares"] if variante else []
        if not ordre_gares:
            # Le référentiel (reference_paris_cherbourg.csv) a pu être
            # régénéré depuis que cette circulation a été observée — un train
            # ancien dont le motif d'horaire n'est plus publié par la SNCF
            # disparaît alors de trajet_gares, même si ses relevés bruts
            # restent dans observations.db. Message explicite plutôt qu'un
            # graphique vide avec le texte départ/arrivée resté périmé de la
            # sélection précédente — repéré par l'utilisateur, 2026-08-04.
            self.depart_arrivee_var.set(
                "Trajet théorique introuvable dans le référentiel actuel — ce train a peut-être "
                "été retiré de la desserte SNCF depuis (voir l'onglet \"Vérification GTFS\"). "
                "Il reste néanmoins comptabilisé dans les statistiques (Tableau, Graphique...), "
                "qui ne dépendent pas du référentiel."
            )
            self.train_ax.text(
                0.5, 0.5,
                "Trajet théorique introuvable dans le référentiel actuel.\n"
                "Ce train reste comptabilisé dans les statistiques.",
                ha="center", va="center", fontsize=10, color="#888", transform=self.train_ax.transAxes,
            )
            self.train_canvas.draw()
            return
        if trajet.empty:
            self.depart_arrivee_var.set("")
            self.train_canvas.draw()
            return
        position_gare = {gare: i for i, gare in enumerate(ordre_gares)}

        horaires_bruts = variante["horaires"]
        horaires = [
            format_heure_avec_arret(h, start_date, arret)
            for h, arret in zip(horaires_bruts, variante["arrets"])
        ]
        if horaires and horaires[0] and horaires[-1]:
            # Icône horloge + durée théorique en préfixe, comme côté web
            # (app_fastapi.py, demandé par l'utilisateur pour cette version-là
            # le 2026-08-10, ajouté ici le 2026-08-11) — sans la mise en forme
            # CSS (icône légèrement agrandie) de la version web, pas
            # transposable à un ttk.Label unique. ◷ (U+25F7) plutôt que ⏱ :
            # ce dernier rendait comme un rectangle vide (glyphe manquant,
            # absent de DejaVu Sans — vérifié dans sa table cmap) sur ce
            # bureau Linux, contrairement à ◷, couvert par cette police.
            duree = duree_theorique(horaires_bruts[0], horaires_bruts[-1], start_date)
            self.depart_arrivee_var.set(
                (f"◷ {duree}  —  " if duree else "")
                + f"Départ {format_gare(ordre_gares[0])} à {horaires[0]}  →  "
                f"Arrivée {format_gare(ordre_gares[-1])} à {horaires[-1]}"
            )
        else:
            self.depart_arrivee_var.set("")

        if self.vue_train_var.get() == "Escalier":
            self.aide_vue_train_var.set(
                "Une seule ligne : dernière valeur connue par gare.\n"
                "Trait plein = gare déjà passée (figé) — pointillé = pas encore atteinte (peut encore changer),\n"
                "ou jamais confirmée si le train est sorti du flux avant son heure d'arrivée prédite (plus fréquent en cas de fort retard)."
            )
            self._render_train_escalier(trajet, ordre_gares, position_gare, start_date, horaires_bruts)
        else:
            self.aide_vue_train_var.set(
                "Chaque ligne = un relevé (bleu = ancien, orange = récent) — montre\n"
                "l'historique complet des révisions de prévision, gare par gare. Cliquer\n"
                "un relevé dans la légende l'affiche/le masque."
            )
            self._render_train_detail(trajet, ordre_gares, position_gare, start_date, horaires_bruts, cle_trajet)

        labels = [
            f"{format_gare(g)}\n{h}" if h else format_gare(g)
            for g, h in zip(ordre_gares, horaires + [""] * (len(ordre_gares) - len(horaires)))
        ]
        self.train_ax.set_xticks(range(len(ordre_gares)))
        self.train_ax.set_xticklabels(labels, rotation=45, ha="right")
        # Gares hors de la ligne Paris-Cherbourg (trains de jonction, voir
        # GARES_LIGNE) grisées, pour les distinguer des vraies gares de la ligne.
        for tick, gare in zip(self.train_ax.get_xticklabels(), ordre_gares):
            if gare not in GARES_LIGNE:
                tick.set_color("#999999")
        self.train_ax.set_ylabel("Retard (min)")
        self.train_ax.set_title(f"Évolution du retard gare par gare — {selection}")
        # En arrière-plan (zorder) et en pointillés : à 0 min de retard, une
        # ligne de référence pleine et au premier plan masquerait les courbes.
        self.train_ax.axhline(0, color="gray", linewidth=0.8, linestyle=(0, (4, 3)), zorder=1)
        finalize_axes(self.train_ax, marge_bas=True)
        self.train_figure.tight_layout()
        self.train_canvas.draw()

    def _render_train_escalier(self, trajet, ordre_gares, position_gare, start_date, horaires_bruts):
        """Une seule ligne 'escalier' : la dernière valeur connue par gare,
        tracée dans l'ordre du trajet — un palier plat entre deux gares où le
        retard n'a pas changé, une marche vers le haut/bas sinon (voir
        mémoire du projet : un retard révisé s'applique d'un coup à toutes
        les gares pas encore atteintes, d'où ces paliers). Trait plein pour
        les gares déjà dépassées (figées, voir estimer_passage_reel),
        pointillé pour celles pas encore atteintes au moment du dernier
        relevé (valeur encore susceptible de changer)."""
        # poll_time est encore une chaîne ISO à ce stade (self.df ne la
        # convertit pas globalement) — nécessaire ici en datetime pour la
        # comparer aux horaires théoriques calculés par estimer_passage_reel.
        dernier_poll_trajet = pd.to_datetime(trajet["poll_time"]).max()
        dernieres = trajet.sort_values("poll_time").groupby("gare").last()
        # horaires_bruts (variante déjà choisie par l'appelant, _render_train_tab)
        # plutôt que refaire un choisir_variante ici : même trip_id/start_date,
        # pas la peine de repayer la recherche parmi les variantes/le calendrier.
        heure_par_gare = dict(zip(ordre_gares, horaires_bruts))

        points = []
        for gare in ordre_gares:
            if gare not in dernieres.index:
                continue
            retard = dernieres.loc[gare, "retard_min"]
            if pd.isna(retard):
                continue
            passage_estime = estimer_passage_reel(heure_par_gare.get(gare), start_date, retard)
            fige = passage_estime is not None and passage_estime <= dernier_poll_trajet
            points.append((position_gare[gare], retard, fige))

        couleur_figee, couleur_encore = "#2c6ea5", "#f2a53d"
        for (p1, v1, f1), (p2, v2, f2) in zip(points, points[1:]):
            style = "-" if f1 and f2 else (0, (4, 2))
            couleur = couleur_figee if f1 and f2 else couleur_encore
            self.train_ax.step([p1, p2], [v1, v1], where="post",
                                linewidth=1.8, linestyle=style, color=couleur, zorder=2)
            self.train_ax.plot([p2, p2], [v1, v2],
                                linewidth=1.8, linestyle=style, color=couleur, zorder=2)
        for p, v, f in points:
            self.train_ax.plot(p, v, marker="o", markersize=5,
                                color=couleur_figee if f else couleur_encore, zorder=3)

        if points:
            legende = [
                Line2D([0], [0], color=couleur_figee, linewidth=1.8, label="Gare déjà passée (figé)"),
                Line2D([0], [0], color=couleur_encore, linewidth=1.8, linestyle=(0, (4, 2)),
                       label="Gare pas encore atteinte (peut encore changer)"),
            ]
            self.train_ax.legend(handles=legende, loc="upper left")

    def _render_train_detail(self, trajet, ordre_gares, position_gare, start_date, horaires_bruts, cle_trajet):
        """Contrairement à l'Escalier, garde une ligne par relevé (légende
        interactive, voir _construire_legende_detail — repliée par défaut
        pour rester lisible sur un train suivi longtemps)."""
        # Ne garder que les relevés où le retard a réellement changé par rapport
        # au précédent — sinon un train suivi plusieurs heures affiche des
        # dizaines de lignes quasi identiques superposées (le retard reste
        # souvent figé un long moment avant de sauter, voir mémoire du projet).
        tous_les_polls = sorted(trajet["poll_time"].unique())
        polls = []
        signature_precedente = None
        for poll_time in tous_les_polls:
            snapshot_poll = trajet[trajet["poll_time"] == poll_time].sort_values(
                by="gare", key=lambda s: s.map(position_gare)
            )
            signature = tuple(snapshot_poll["retard_min"].fillna(-1))
            if signature != signature_precedente or poll_time == tous_les_polls[-1]:
                polls.append(poll_time)
                signature_precedente = signature

        n = len(polls)
        colormap = TRAJET_COLORMAP
        # horaires_bruts (variante déjà choisie par l'appelant, _render_train_tab)
        # plutôt que refaire un choisir_variante ici : même trip_id/start_date,
        # pas la peine de repayer la recherche parmi les variantes/le calendrier.
        heure_par_gare = dict(zip(ordre_gares, horaires_bruts))
        visibilite = self._detail_visibilite.setdefault(cle_trajet, {})
        # Repliée (par défaut) : seuls le premier et le dernier relevé sont
        # affichés, comme avant — dépliée : chaque relevé suit son propre
        # état (coché/décoché), tous visibles par défaut à ce moment-là.
        etendue = self._detail_legende_etendue.get(cle_trajet, False)
        self._train_detail_polls[cle_trajet] = polls

        lignes_pour_legende = []
        for i, poll_time in enumerate(polls):
            snapshot = trajet[trajet["poll_time"] == poll_time].copy()
            snapshot["position"] = snapshot["gare"].map(position_gare)
            snapshot = snapshot.dropna(subset=["position"]).sort_values("position")
            couleur = colormap(i / max(n - 1, 1))
            visible = visibilite.get(poll_time, True) if (etendue or n <= 2) else i in (0, n - 1)
            poll_dt = pd.Timestamp(poll_time)

            # Segment diagonal (pas en escalier, contrairement à la vue
            # Escalier) : chaque ligne de la vue Détail est un relevé unique
            # figeant les prévisions de toutes les gares à un instant donné,
            # pas une valeur "tenue" jusqu'à la gare suivante.
            artistes = []
            pts = list(zip(snapshot["position"], snapshot["retard_min"], snapshot["gare"]))
            for (p1, v1, g1), (p2, v2, g2) in zip(pts, pts[1:]):
                h1 = estimer_passage_reel(heure_par_gare.get(g1), start_date, v1)
                h2 = estimer_passage_reel(heure_par_gare.get(g2), start_date, v2)
                fige = h1 is not None and h1 <= poll_dt and h2 is not None and h2 <= poll_dt
                style = "-" if fige else (0, (4, 2))
                ligne, = self.train_ax.plot([p1, p2], [v1, v2], linewidth=1.3,
                                             linestyle=style, color=couleur, zorder=2, visible=visible)
                artistes.append(ligne)
            for p, v, _ in pts:
                point, = self.train_ax.plot(p, v, marker="o", markersize=3, color=couleur,
                                             zorder=3, visible=visible)
                artistes.append(point)

            lignes_pour_legende.append((poll_time, artistes, couleur, visible))

        if n > 1:
            self._construire_legende_detail(cle_trajet, lignes_pour_legende)

    def _construire_legende_detail(self, cle_trajet, lignes):
        """Légende interactive de la vue Détail : repliée par défaut
        (premier + dernier relevé, avec un lien pour tout déplier), ou
        dépliée si l'utilisateur l'a demandé pour ce trajet précis (état
        gardé dans self._detail_legende_etendue, indexé par (trip_id,
        start_date) pour ne pas mélanger deux circulations réelles
        différentes qui partageraient le même trip_id technique). Chaque
        entrée est cliquable pour afficher/masquer sa ligne (☑/☐, voir
        _on_train_legend_pick), ce qui permet de nettoyer les relevés
        redondants qui se superposent exactement."""
        n = len(lignes)
        etendue = self._detail_legende_etendue.get(cle_trajet, False)
        indices = list(range(n)) if (etendue or n <= 2) else [0, n - 1]

        handles, labels, entrees = [], [], []
        for i in indices:
            poll_time, _, couleur, visible = lignes[i]
            coche = "☑" if visible else "☐"
            handles.append(Line2D([0], [0], color=couleur if visible else "#bbbbbb",
                                   linewidth=1.8, linestyle="-" if visible else (0, (2, 2))))
            labels.append(f"{coche} {format_poll_time(poll_time)}")
            entrees.append(("bascule", cle_trajet, poll_time))

        if n > 2:
            if etendue:
                tout_visible = all(v for _, _, _, v in lignes)
                coche_tout = "☐" if tout_visible else "☑"
                handles.append(Line2D([0], [0], color="none"))
                labels.append(f"{coche_tout} Tout décocher / cocher")
                entrees.append(("tout_bascule", cle_trajet, None))

                handles.append(Line2D([0], [0], color="none"))
                labels.append("− réduire (afficher seulement 1er/dernier)")
                entrees.append(("repli", cle_trajet, None))
            else:
                handles.append(Line2D([0], [0], color="none"))
                labels.append(f"+ afficher les {n - 2} autres relevés")
                entrees.append(("depli", cle_trajet, None))

        legende = self.train_ax.legend(
            handles, labels, loc="upper left", fontsize=9,
            title="Cliquer un relevé pour l'afficher/masquer",
        )
        self._train_legend_map = {}
        for leg_ligne, leg_texte, entree in zip(legende.get_lines(), legende.get_texts(), entrees):
            leg_ligne.set_picker(8)
            leg_texte.set_picker(8)
            self._train_legend_map[leg_ligne] = entree
            self._train_legend_map[leg_texte] = entree

    def _on_train_legend_pick(self, event):
        entree = self._train_legend_map.get(event.artist)
        if entree is None:
            return
        action, cle_trajet, poll_time = entree
        if action == "bascule":
            visibilite = self._detail_visibilite.setdefault(cle_trajet, {})
            visibilite[poll_time] = not visibilite.get(poll_time, True)
        elif action == "tout_bascule":
            polls = self._train_detail_polls.get(cle_trajet, [])
            visibilite = self._detail_visibilite.setdefault(cle_trajet, {})
            tout_visible = all(visibilite.get(p, True) for p in polls)
            for p in polls:
                visibilite[p] = not tout_visible
        elif action == "depli":
            self._detail_legende_etendue[cle_trajet] = True
        elif action == "repli":
            self._detail_legende_etendue[cle_trajet] = False
        self._render_train_tab()


if __name__ == "__main__":
    App().mainloop()
