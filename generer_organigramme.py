"""
Génère un schéma (PNG) de l'architecture du projet : les 4 machines
impliquées (VPS, Raspberry Pi, PC/WSL, NAS), les sources externes, et les
scripts/fichiers de données de ce dossier avec leurs relations réelles
(imports, lecture/écriture de fichiers, déclenchement cron/planificateur) —
à mettre à jour à la main si l'architecture change (nouveau script, nouveau
fichier de données, nouvelle machine...).

Réécrit le 2026-08-16 pour la bascule VPS (2026-08-13/14) : la VPS est
maintenant la seule collectrice (observations.db/alertes.csv/
verification_gtfs.log) et héberge le site web public ; le Pi ne fait plus
que relayer (rapports PDF + sauvegarde de la base vers le NAS, la VPS ne
pouvant pas atteindre le NAS, adresse privée) ; le PC/WSL reste l'interface
de suivi (viewer.py) et le point de préparation/déploiement du référentiel.

Usage : python generer_organigramme.py
Écrit : organigramme_application.png (racine du projet, écrasé à chaque
génération).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.lines import Line2D

COULEUR_VPS = "#e0f2e9"
COULEUR_VPS_BORD = "#2f855a"
COULEUR_PI = "#e6f7f5"
COULEUR_PI_BORD = "#0f9488"
COULEUR_PC = "#e0edf7"
COULEUR_PC_BORD = "#2c6ea5"
COULEUR_NAS = "#f3e8ff"
COULEUR_NAS_BORD = "#7c3aed"
COULEUR_EXT = "#fdf1e0"
COULEUR_EXT_BORD = "#c2410c"
COULEUR_DONNEE = "#f5f5f5"
COULEUR_DONNEE_BORD = "#555555"
COULEUR_DONNEE_FIGEE = "#e8e8e8"
COULEUR_MANUEL = "#fff8e0"
COULEUR_MANUEL_BORD = "#b7791f"


def generer():
    fig, ax = plt.subplots(figsize=(26, 18))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    boxes = {}

    def boite(nom, x, y, w, h, texte, fond, bord_couleur, fontsize=8.5, fontweight="normal",
              style="round,pad=0.25"):
        patch = FancyBboxPatch(
            (x, y), w, h, boxstyle=style, facecolor=fond, edgecolor=bord_couleur, linewidth=1.3, zorder=2,
        )
        ax.add_patch(patch)
        ax.text(
            x + w / 2, y + h / 2, texte, ha="center", va="center", fontsize=fontsize,
            fontweight=fontweight, zorder=3,
        )
        boxes[nom] = (x, y, w, h)

    def bord(nom, cote):
        x, y, w, h = boxes[nom]
        return {
            "haut": (x + w / 2, y + h), "bas": (x + w / 2, y),
            "gauche": (x, y + h / 2), "droite": (x + w, y + h / 2),
        }[cote]

    def fleche(depart, arrivee, texte=None, couleur="#333333", style="-|>", rad=0.0,
               decal_texte=(0, 1.1), fontsize=7, linestyle="-"):
        p = FancyArrowPatch(
            depart, arrivee, arrowstyle=style, mutation_scale=12, color=couleur,
            linewidth=1.2, linestyle=linestyle, connectionstyle=f"arc3,rad={rad}", zorder=1,
        )
        ax.add_patch(p)
        if texte:
            mx, my = (depart[0] + arrivee[0]) / 2, (depart[1] + arrivee[1]) / 2
            ax.text(mx + decal_texte[0], my + decal_texte[1], texte, ha="center", va="center",
                    fontsize=fontsize, color=couleur, style="italic", zorder=4,
                    bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.9))

    def fleche_coude(points, texte=None, couleur="#333333", linewidth=1.2, linestyle="-",
                      texte_pos=None, fontsize=7, arrowhead=True):
        """Flèche en plusieurs segments droits (coudes), pour router
        proprement autour d'une boîte plutôt qu'une courbe arc3 qui la
        traverserait — la flèche n'apparaît que sur le dernier segment."""
        for i in range(len(points) - 1):
            dernier = i == len(points) - 2
            p = FancyArrowPatch(
                points[i], points[i + 1], arrowstyle="-|>" if (dernier and arrowhead) else "-",
                mutation_scale=12, color=couleur, linewidth=linewidth, linestyle=linestyle,
                connectionstyle="arc3,rad=0", zorder=1,
            )
            ax.add_patch(p)
        if texte:
            tx, ty = texte_pos if texte_pos else points[len(points) // 2]
            ax.text(tx, ty, texte, ha="center", va="center", fontsize=fontsize, color=couleur,
                    style="italic", zorder=4,
                    bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.9))

    # ---------- Titre ----------
    ax.text(50, 97.3, "train-delay-paris-cherbourg — Organigramme de l'application", ha="center",
            fontsize=17, fontweight="bold")
    ax.text(50, 94.5,
            "Suivi des retards SNCF sur l'axe Paris ↔ Cherbourg — collecte continue sur VPS, "
            "site web public, interface de suivi, rapports PDF",
            ha="center", fontsize=10.5, color="#555555")

    # ---------- Zones (fond) ----------
    boite("zone_ext", 1, 79, 98, 10, "", COULEUR_EXT, COULEUR_EXT_BORD, style="round,pad=0.1")
    ax.text(2.5, 87, "Sources externes", fontsize=10, fontweight="bold", color=COULEUR_EXT_BORD, va="center")

    boite("zone_vps", 1, 3, 38, 74, "", COULEUR_VPS, COULEUR_VPS_BORD, style="round,pad=0.1")
    ax.text(2.5, 75, "VPS (IONOS) — collecte continue + site web public", fontsize=10.5, fontweight="bold",
            color=COULEUR_VPS_BORD, va="center")

    boite("zone_pi", 41, 3, 20, 74, "", COULEUR_PI, COULEUR_PI_BORD, style="round,pad=0.1")
    ax.text(42.5, 75, "Raspberry Pi — relais rapports + sauvegarde", fontsize=9.5, fontweight="bold",
            color=COULEUR_PI_BORD, va="center")

    boite("zone_pc", 63, 3, 20, 74, "", COULEUR_PC, COULEUR_PC_BORD, style="round,pad=0.1")
    ax.text(64.5, 75, "PC / WSL — suivi + référentiel", fontsize=9.5, fontweight="bold",
            color=COULEUR_PC_BORD, va="center")

    boite("zone_nas", 85, 3, 14, 74, "", COULEUR_NAS, COULEUR_NAS_BORD, style="round,pad=0.1")
    ax.text(86, 75, "NAS", fontsize=10.5, fontweight="bold", color=COULEUR_NAS_BORD, va="center")

    # ---------- Sources externes ----------
    boite("gtfs_rt", 2.5, 80, 8, 6.5, "Flux GTFS-RT\nSNCF (retards\ntemps réel)", COULEUR_EXT, COULEUR_EXT_BORD, fontsize=7.3)
    boite("meteo", 11, 80, 8, 6.5, "API\nOpen-Meteo\n(météo)", COULEUR_EXT, COULEUR_EXT_BORD, fontsize=7.3)
    boite("vacances_api", 19.5, 80, 8, 6.5, "Calendrier\nscolaire /\njours fériés", COULEUR_EXT, COULEUR_EXT_BORD, fontsize=7.3)
    boite("alertes_flux", 28, 80, 8, 6.5, "Flux alertes\nSNCF", COULEUR_EXT, COULEUR_EXT_BORD, fontsize=7.3)
    boite("gtfs_static", 45, 80, 20, 6.5, "GTFS statique SNCF\n(horaires théoriques)", COULEUR_EXT, COULEUR_EXT_BORD, fontsize=8)
    boite("visiteurs", 68, 80, 15, 6.5, "Visiteurs du site\n(navigateur web)", COULEUR_EXT, COULEUR_EXT_BORD, fontsize=8)

    # ================= VPS =================
    boite("ref_csv_vps", 2.5, 63, 10, 8, "reference_\nparis_\ncherbourg.csv\n(déployé\ndepuis le PC)", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=6.5)
    boite("calendar_data_vps", 2.5, 53, 10, 8, "calendar_\ndata.py\n(module)", "#ffffff", COULEUR_VPS_BORD, fontsize=7)

    boite("collect_realtime", 14, 58, 11, 13, "collect_\nrealtime.py\n(cron\n5 min)", COULEUR_VPS, COULEUR_VPS_BORD, fontsize=7.4, fontweight="bold")
    boite("observations_db", 27, 60, 10, 9, "observations.db\n(SQLite,\nWAL)", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=7)

    boite("collect_alertes", 14, 44, 11, 11, "collect_\nalertes.py\n(cron\nhoraire)", COULEUR_VPS, COULEUR_VPS_BORD, fontsize=7.4, fontweight="bold")
    boite("alertes_csv_vps", 27, 46, 10, 7, "alertes.csv", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=7)

    boite("verifier_gtfs", 14, 30, 11, 11, "verifier_\ngtfs.py\n(cron\n3h15)", COULEUR_VPS, COULEUR_VPS_BORD, fontsize=7.4, fontweight="bold")
    boite("verif_log", 27, 32, 10, 7, "verification_\ngtfs.log", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=7)

    boite("app_fastapi", 2.5, 5, 34.5, 21, "app_fastapi.py\n(service systemd train-delay,\nderrière nginx + HTTPS)\nsite web public : Tableau, Graphique,\nSuivi d'un train, Par jour/heure,\nPerturbations, Vérification GTFS",
          COULEUR_VPS, COULEUR_VPS_BORD, fontsize=8.3, fontweight="bold")

    # ================= RASPBERRY PI =================
    boite("executer_rapport_pi", 42, 55, 18, 14, "executer_rapport_pi.sh\n(cron 3h30/3h35 lundi/\n3h40 le 1er du mois)\nrapatrie observations.db +\nalertes.csv depuis la VPS",
          COULEUR_PI, COULEUR_PI_BORD, fontsize=7.6, fontweight="bold")
    boite("generer_rapport_pi", 42, 42, 18, 10, "generer_rapport.py\n(quotidien/hebdo/mensuel)", COULEUR_PI, COULEUR_PI_BORD, fontsize=7.8)
    boite("rapports_pi", 42, 33, 18, 6, "rapports/quotidien/*.pdf\netc. (copie locale Pi)", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=6.8)
    boite("envoyer_nas_pi", 42, 27, 18, 4, "envoyer_rapport_nas_pi.sh", COULEUR_MANUEL, COULEUR_MANUEL_BORD, fontsize=7.2)

    boite("sauvegarder_obs_nas", 42, 12, 18, 12, "sauvegarder_observations_\nnas.sh\n(cron 3h45, quotidien)\nrapatrie sa propre copie\nd'observations.db depuis\nla VPS, rotation 14 jours",
          COULEUR_PI, COULEUR_PI_BORD, fontsize=7.2, fontweight="bold")

    # ================= PC / WSL =================
    boite("build_reference", 64, 65, 18, 9, "build_reference.py\n(manuel, ponctuel)\nRégénérer / Déployer vers la VPS\n(boutons \"Vérification GTFS\")", COULEUR_MANUEL, COULEUR_MANUEL_BORD, fontsize=7.3)
    boite("ref_csv_pc", 64, 55, 18, 7, "reference_paris_cherbourg.csv\n(copie locale PC)", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=7.3)

    boite("formatting", 64, 45, 18, 7, "formatting.py\n(module partagé : formatage,\ncalculs communs)", "#ffffff", COULEUR_PC_BORD, fontsize=7.3)

    boite("viewer", 64, 28, 18, 14, "viewer.py\nInterface de suivi (Tkinter)\nrapatrie observations.db\ndepuis la VPS (bouton\n\"Rafraîchir depuis la VPS\")",
          COULEUR_PC, COULEUR_PC_BORD, fontsize=8.3, fontweight="bold")

    boite("guide_py", 64, 15, 18, 9, "generer_guide_statistiques.py\n(régénérable depuis l'onglet\n\"Guide statistiques\")", COULEUR_PC, COULEUR_PC_BORD, fontsize=7.3)
    boite("guide_pdf", 64, 6, 18, 6, "guide_statistiques.pdf", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=7.6)

    # ================= NAS =================
    boite("nas_obs_db", 86, 55, 12, 12, "observations_db/\n(sauvegardes datées,\nrotation 14 jours)", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=7)
    boite("nas_rapports", 86, 40, 12, 10, "rapports/\nquotidien/hebdo/mensuel", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=7.3)
    boite("nas_csv_fige", 86, 26, 12, 9, "observations.csv\n(archive figée,\nfin de collecte Pi,\n14/08/2026)", COULEUR_DONNEE_FIGEE, COULEUR_DONNEE_BORD, fontsize=6.8)

    # ================= FLECHES =================
    # --- Sources -> VPS ---
    fleche(bord("gtfs_rt", "bas"), bord("collect_realtime", "haut"), rad=-0.1)
    fleche(bord("meteo", "bas"), bord("collect_realtime", "haut"), rad=0.0)
    fleche(bord("vacances_api", "bas"), bord("collect_realtime", "haut"), rad=0.15)
    fleche(bord("alertes_flux", "bas"), bord("collect_alertes", "haut"), rad=0.1)
    fleche_coude([bord("gtfs_static", "gauche"), (48, 76), (19, 76), bord("verifier_gtfs", "droite")],
                 texte="build_reference/verifier_gtfs", texte_pos=(35, 77.5), fontsize=6.6)
    fleche(bord("visiteurs", "bas"), bord("app_fastapi", "droite"), rad=-0.25, couleur=COULEUR_VPS_BORD)
    ax.text(bord("visiteurs", "bas")[0] + 2, bord("visiteurs", "bas")[1] - 2, "HTTPS", ha="left", va="center",
            fontsize=7, color=COULEUR_VPS_BORD, style="italic")

    # --- VPS : pipeline temps réel ---
    fleche(bord("ref_csv_vps", "droite"), bord("collect_realtime", "gauche"), rad=0.1)
    fleche(bord("calendar_data_vps", "droite"), bord("collect_realtime", "gauche"), rad=-0.1, texte="type_jour / vacances", decal_texte=(4, -2.5), fontsize=6.3)
    fleche(bord("collect_realtime", "droite"), bord("observations_db", "gauche"))

    # --- VPS : pipeline alertes ---
    fleche(bord("collect_alertes", "droite"), bord("alertes_csv_vps", "gauche"))

    # --- VPS : vérification GTFS ---
    fleche(bord("ref_csv_vps", "bas"), bord("verifier_gtfs", "haut"), rad=-0.1, texte="référence à jour ?", fontsize=6.3)
    fleche(bord("verifier_gtfs", "droite"), bord("verif_log", "gauche"))

    # --- VPS : données -> service web ---
    fleche(bord("observations_db", "bas"), bord("app_fastapi", "haut"), rad=0.1)
    fleche(bord("alertes_csv_vps", "bas"), bord("app_fastapi", "haut"), rad=0.0)
    fleche(bord("verif_log", "bas"), bord("app_fastapi", "haut"), rad=-0.15)

    # --- VPS -> Pi (rsync, 2 chemins distincts) ---
    fleche_coude(
        [bord("observations_db", "droite"), (39.5, 64), bord("executer_rapport_pi", "gauche")],
        texte="rsync (cron Pi)", texte_pos=(40, 66.5), fontsize=6.8, couleur=COULEUR_PI_BORD,
    )
    fleche_coude(
        [bord("app_fastapi", "droite"), (39.5, 18), bord("sauvegarder_obs_nas", "gauche")],
        texte="rsync (cron Pi, observations.db)", texte_pos=(40, 20.5), fontsize=6.5, couleur=COULEUR_PI_BORD,
    )
    ax.text(51, 71, "la VPS ne peut pas atteindre le NAS\ndirectement (IP privée) — le Pi relaie",
            ha="center", fontsize=6.8, color="#888888", style="italic")

    # --- VPS -> PC (rsync à la demande, viewer.py) ---
    fleche_coude(
        [bord("app_fastapi", "haut"), (19.25, 27), (60, 27), (60, 35), bord("viewer", "gauche")],
        texte="rsync (bouton \"Rafraîchir depuis la VPS\")", texte_pos=(50, 24.5), fontsize=7, couleur=COULEUR_PC_BORD,
    )

    # --- Pi : rapport planifié ---
    fleche(bord("executer_rapport_pi", "bas"), bord("generer_rapport_pi", "haut"))
    fleche(bord("generer_rapport_pi", "bas"), bord("rapports_pi", "haut"))
    fleche(bord("rapports_pi", "bas"), bord("envoyer_nas_pi", "haut"))

    # --- Pi -> NAS ---
    fleche_coude(
        [bord("envoyer_nas_pi", "droite"), (83, 29), bord("nas_rapports", "gauche")],
        texte="envoyer_rapport_nas_pi.sh", texte_pos=(83, 33), fontsize=7, couleur=COULEUR_NAS_BORD,
    )
    fleche_coude(
        [bord("sauvegarder_obs_nas", "droite"), (83, 60), bord("nas_obs_db", "gauche")],
        texte="rsync", texte_pos=(83, 62.5), fontsize=7, couleur=COULEUR_NAS_BORD,
    )

    # --- PC : préparation/déploiement référentiel ---
    fleche(bord("build_reference", "bas"), bord("ref_csv_pc", "haut"))
    fleche_coude(
        [bord("ref_csv_pc", "gauche"), (58, 58.5), (19, 58.5), bord("ref_csv_vps", "droite")],
        texte="bouton \"Déployer vers la VPS\" (rsync + redémarrage du service)",
        texte_pos=(40, 60.5), fontsize=6.8, couleur=COULEUR_VPS_BORD,
    )

    # --- PC : modules partagés -> viewer ---
    fleche(bord("ref_csv_pc", "bas"), bord("formatting", "haut"), texte="load_reference()", fontsize=6.8)
    fleche(bord("formatting", "bas"), bord("viewer", "haut"))

    # --- viewer <-> guide ---
    fleche(bord("viewer", "bas"), bord("guide_py", "haut"), texte="import direct + bouton \"Régénérer\"", fontsize=6.8)
    fleche(bord("guide_py", "bas"), bord("guide_pdf", "haut"))

    # --- Archive figée NAS (pas de flèche entrante active, juste une note) ---
    ax.text(92, 22, "plus mis à jour depuis\nl'arrêt de la collecte\nsur le Pi (14/08/2026)",
            ha="center", fontsize=6.3, color="#888888", style="italic")

    # ---------- Légende ----------
    legende_elements = [
        Line2D([0], [0], marker="s", color="none", markerfacecolor=COULEUR_EXT, markeredgecolor=COULEUR_EXT_BORD,
               markersize=13, label="Source externe / visiteur"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor=COULEUR_VPS, markeredgecolor=COULEUR_VPS_BORD,
               markersize=13, label="Script/service automatisé — VPS"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor=COULEUR_PI, markeredgecolor=COULEUR_PI_BORD,
               markersize=13, label="Script automatisé (cron) — Pi"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor=COULEUR_PC, markeredgecolor=COULEUR_PC_BORD,
               markersize=13, label="Application / script principal — PC"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor=COULEUR_MANUEL, markeredgecolor=COULEUR_MANUEL_BORD,
               markersize=13, label="Lancé manuellement / planifié"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor="#ffffff", markeredgecolor=COULEUR_PC_BORD,
               markersize=13, label="Module partagé (pas un script autonome)"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor=COULEUR_DONNEE, markeredgecolor=COULEUR_DONNEE_BORD,
               markersize=13, label="Fichier de données"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor=COULEUR_DONNEE_FIGEE, markeredgecolor=COULEUR_DONNEE_BORD,
               markersize=13, label="Fichier de données figé (archive)"),
    ]
    ax.legend(handles=legende_elements, loc="lower left", bbox_to_anchor=(0.002, 0.001), fontsize=8.3,
              frameon=True, facecolor="white", edgecolor="#cccccc", ncol=1, title="Légende", title_fontsize=9)

    plt.tight_layout()
    plt.savefig("organigramme_application.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("Organigramme généré : organigramme_application.png")


if __name__ == "__main__":
    generer()
