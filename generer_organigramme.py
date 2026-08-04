"""
Génère un schéma (PNG) de l'architecture du projet : les 3 machines
impliquées (Raspberry Pi, PC/WSL, NAS), les sources externes, et les
scripts/fichiers de données de ce dossier avec leurs relations réelles
(imports, lecture/écriture de fichiers, déclenchement cron/planificateur) —
à mettre à jour à la main si l'architecture change (nouveau script, nouveau
fichier de données, nouvelle machine...).

Usage : python generer_organigramme.py
Écrit : organigramme_application.png (racine du projet, écrasé à chaque
génération).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.lines import Line2D

COULEUR_PI = "#e0f2e9"
COULEUR_PI_BORD = "#2f855a"
COULEUR_PC = "#e0edf7"
COULEUR_PC_BORD = "#2c6ea5"
COULEUR_NAS = "#f3e8ff"
COULEUR_NAS_BORD = "#7c3aed"
COULEUR_EXT = "#fdf1e0"
COULEUR_EXT_BORD = "#c2410c"
COULEUR_DONNEE = "#f5f5f5"
COULEUR_DONNEE_BORD = "#555555"
COULEUR_MANUEL = "#fff8e0"
COULEUR_MANUEL_BORD = "#b7791f"


def generer():
    fig, ax = plt.subplots(figsize=(24, 18))
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
            "Suivi des retards SNCF sur l'axe Paris ↔ Cherbourg — collecte continue, interface de suivi, rapports PDF",
            ha="center", fontsize=10.5, color="#555555")

    # ---------- Zones (fond) ----------
    boite("zone_ext", 1, 79, 98, 10, "", COULEUR_EXT, COULEUR_EXT_BORD, style="round,pad=0.1")
    ax.text(2.5, 87, "Sources externes", fontsize=10, fontweight="bold", color=COULEUR_EXT_BORD, va="center")

    boite("zone_pi", 1, 3, 35, 74, "", COULEUR_PI, COULEUR_PI_BORD, style="round,pad=0.1")
    ax.text(2.5, 75, "Raspberry Pi — collecte continue (cron)", fontsize=10.5, fontweight="bold",
            color=COULEUR_PI_BORD, va="center")

    boite("zone_pc", 38, 3, 40, 74, "", COULEUR_PC, COULEUR_PC_BORD, style="round,pad=0.1")
    ax.text(39.5, 75, "PC / WSL — interface de suivi + rapports", fontsize=10.5, fontweight="bold",
            color=COULEUR_PC_BORD, va="center")

    boite("zone_nas", 80, 3, 19, 74, "", COULEUR_NAS, COULEUR_NAS_BORD, style="round,pad=0.1")
    ax.text(81.5, 75, "NAS — sauvegarde", fontsize=10.5, fontweight="bold", color=COULEUR_NAS_BORD, va="center")

    # ---------- Sources externes ----------
    # Groupées au-dessus de la zone qui les consomme : évite les longues
    # diagonales traversant les autres boîtes.
    boite("gtfs_rt", 2.5, 80, 8, 6.5, "Flux GTFS-RT\nSNCF (retards\ntemps réel)", COULEUR_EXT, COULEUR_EXT_BORD, fontsize=7.3)
    boite("meteo", 11, 80, 8, 6.5, "API\nOpen-Meteo\n(météo)", COULEUR_EXT, COULEUR_EXT_BORD, fontsize=7.3)
    boite("vacances_api", 19.5, 80, 8, 6.5, "Calendrier\nscolaire /\njours fériés", COULEUR_EXT, COULEUR_EXT_BORD, fontsize=7.3)
    boite("alertes_flux", 28, 80, 7, 6.5, "Flux alertes\nSNCF", COULEUR_EXT, COULEUR_EXT_BORD, fontsize=7.3)
    boite("gtfs_static", 48, 80, 22, 6.5, "GTFS statique SNCF\n(horaires théoriques, dossier gtfs/)", COULEUR_EXT, COULEUR_EXT_BORD, fontsize=8)

    # ================= RASPBERRY PI =================
    # Pipeline temps réel : entrées à gauche, script au centre, sortie à droite.
    boite("ref_csv_pi", 2.5, 62, 9.5, 8, "reference_\nparis_\ncherbourg.csv", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=6.8)
    boite("calendar_data", 2.5, 52, 9.5, 8, "calendar_\ndata.py\n(module)", "#ffffff", COULEUR_PI_BORD, fontsize=7)
    boite("cal_cache", 2.5, 42, 9.5, 7, "calendar_\ncache.json", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=6.8)

    boite("collect_realtime", 13.5, 55, 9.5, 15, "collect_\nrealtime.py\n(cron\ntoutes les\n5 min)", COULEUR_PI, COULEUR_PI_BORD, fontsize=7.6, fontweight="bold")

    boite("observations_csv", 24.5, 58, 8, 9, "observations.csv\n(ajouté en\ncontinu)", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=6.8)

    # Pipeline alertes, même schéma, ligne du dessous.
    boite("collect_alertes", 13.5, 39, 9.5, 12, "collect_\nalertes.py\n(cron)", COULEUR_PI, COULEUR_PI_BORD, fontsize=7.6, fontweight="bold")
    boite("alertes_csv", 24.5, 41, 8, 8, "alertes.csv\n(ajouté en\ncontinu)", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=6.8)

    # Sauvegardes (bas de la zone), reçoivent les deux fichiers ci-dessus.
    boite("backup_scripts", 2.5, 21, 30, 8, "backup_local.sh  +  backup_to_nas.sh\n(cron quotidien / horaire)", "#ffffff", COULEUR_PI_BORD, fontsize=8)
    boite("backups_pi", 2.5, 8, 30, 8, "backups/ (copies datées locales,\nrotation 14 jours)", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=8)

    # ================= PC / WSL =================
    # Colonne gauche (x39-56) : préparation référentiel + guide + rapport.
    # Colonne droite (x59-76) : jumelles (données / manuel).
    boite("build_reference", 39.5, 65, 17, 7, "build_reference.py\n(manuel, ponctuel)", COULEUR_MANUEL, COULEUR_MANUEL_BORD, fontsize=8)
    boite("fetch_data", 58.5, 65, 17, 7, "fetch_data.py\n(manuel, ponctuel)", COULEUR_MANUEL, COULEUR_MANUEL_BORD, fontsize=8)

    boite("ref_csv_pc", 39.5, 56, 17, 6.5, "reference_paris_cherbourg.csv", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=7.6)
    boite("csv_lignes", 58.5, 56, 17, 6.5, "intercites_paris_cherbourg.csv\nter_normandie.csv\n(lus aussi par analyze.py)", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=6.8)

    boite("formatting", 39.5, 47, 17, 6.5, "formatting.py\n(module partagé : formatage,\ncalculs communs)", "#ffffff", COULEUR_PC_BORD, fontsize=7.3)
    boite("tooltips", 58.5, 47, 17, 6.5, "tooltips.py\n(module : bulles d'aide)", "#ffffff", COULEUR_PC_BORD, fontsize=8)

    boite("viewer", 39.5, 34, 36, 10.5, "viewer.py\nInterface de suivi (Tkinter)\nrapatrie observations.csv / alertes.csv\ndepuis le Pi (rsync, bouton \"Rafraîchir\")",
          COULEUR_PC, COULEUR_PC_BORD, fontsize=9, fontweight="bold")

    boite("guide_py", 39.5, 24, 17, 7, "generer_guide_\nstatistiques.py\n(régénérable depuis l'onglet\n\"Guide statistiques\")", COULEUR_PC, COULEUR_PC_BORD, fontsize=7.3)
    boite("guide_pdf", 58.5, 25.5, 17, 4.5, "guide_statistiques.pdf", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=8)

    boite("obs_local_pc", 39.5, 15, 17, 6, "observations.csv\nalertes.csv (copie locale PC)", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=7.6)
    boite("executer_rapport", 58.5, 15, 17, 6, "executer_rapport.sh\n(Planificateur de tâches\nWindows)", COULEUR_MANUEL, COULEUR_MANUEL_BORD, fontsize=7.6)

    boite("generer_rapport", 39.5, 6, 17, 6.5, "generer_rapport.py\n(quotidien / hebdomadaire)", COULEUR_PC, COULEUR_PC_BORD, fontsize=8, fontweight="bold")
    boite("rapports_pdf", 58.5, 6, 17, 6.5, "rapports/quotidien/*.pdf\nrapports/hebdomadaire/*.pdf", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=7.6)

    boite("envoyer_nas", 39.5, 3.5, 36, 1.8, "envoyer_rapport_nas.sh", COULEUR_MANUEL, COULEUR_MANUEL_BORD, fontsize=7.5)

    # ================= NAS =================
    boite("nas_obs", 81.5, 55, 16, 9, "observations.csv\nalertes.csv\n(sauvegarde horaire +\ndatée 14 jours)", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=7.6)
    boite("nas_rapports", 81.5, 40, 16, 9, "rapports/quotidien/*.pdf\nrapports/hebdomadaire/*.pdf", COULEUR_DONNEE, COULEUR_DONNEE_BORD, fontsize=7.6)

    # ================= FLECHES =================
    # --- Sources -> Pi ---
    fleche(bord("gtfs_rt", "bas"), bord("collect_realtime", "haut"), rad=-0.15)
    fleche(bord("meteo", "bas"), bord("collect_realtime", "haut"), rad=-0.05)
    fleche(bord("vacances_api", "bas"), bord("collect_realtime", "haut"), rad=0.1)
    fleche(bord("alertes_flux", "bas"), bord("collect_alertes", "haut"), rad=0.05)
    fleche(bord("gtfs_static", "bas"), bord("build_reference", "haut"), rad=0.0)

    # --- Pi : pipeline temps réel (entrées gauche -> script -> sortie droite) ---
    fleche(bord("ref_csv_pi", "droite"), bord("collect_realtime", "gauche"), rad=0.15)
    fleche(bord("calendar_data", "droite"), bord("collect_realtime", "gauche"), rad=-0.12, texte="type_jour / vacances", decal_texte=(4, -2.5), fontsize=6.3)
    fleche(bord("calendar_data", "bas"), bord("cal_cache", "haut"))
    fleche(bord("collect_realtime", "droite"), bord("observations_csv", "gauche"))

    # --- Pi : pipeline alertes --- (coude par la marge de gauche : un tracé
    # courbe direct passait en plein sur la boîte collect_realtime.py au-dessus)
    fleche_coude(
        [bord("ref_csv_pi", "bas"), (7.25, 53), bord("collect_alertes", "haut")],
        texte="reference_...csv (partagé)", texte_pos=(10.5, 54), fontsize=6.3,
    )
    fleche(bord("collect_alertes", "droite"), bord("alertes_csv", "gauche"))

    # --- Pi : sauvegardes ---
    fleche(bord("observations_csv", "bas"), bord("backup_scripts", "haut"), rad=0.15)
    fleche(bord("alertes_csv", "bas"), bord("backup_scripts", "haut"), rad=-0.1)
    fleche(bord("backup_scripts", "bas"), bord("backups_pi", "haut"), texte="backup_local.sh")

    # --- Pi -> NAS (sauvegarde horaire), corridor du haut ---
    fleche_coude(
        [bord("backup_scripts", "droite"), (36, 25), (36, 92), (89.5, 92), (89.5, 64)],
        texte="backup_to_nas.sh — copie horaire", texte_pos=(63, 93), fontsize=8, couleur=COULEUR_NAS_BORD,
    )

    # --- Pi -> PC (rsync à la demande) : un seul trajet, les deux fichiers
    # partent du même point de sortie de la zone Pi plutôt que deux tracés
    # séparés qui se croisaient visuellement dans l'espace étroit entre les
    # deux zones.
    point_sortie_pi = (36, 52)
    fleche_coude([bord("observations_csv", "droite"), point_sortie_pi], couleur=COULEUR_PC_BORD, arrowhead=False)
    fleche_coude([bord("alertes_csv", "droite"), point_sortie_pi], couleur=COULEUR_PC_BORD, arrowhead=False)
    fleche_coude(
        [point_sortie_pi, (37.5, 45), bord("viewer", "gauche")],
        texte="rsync (bouton \"Rafraîchir\")", texte_pos=(37, 50), fontsize=7.3, couleur=COULEUR_PC_BORD,
    )

    # --- PC : préparation référentiel (manuel) ---
    fleche(bord("build_reference", "bas"), bord("ref_csv_pc", "haut"))
    fleche(bord("fetch_data", "bas"), bord("csv_lignes", "haut"))
    ax.text(48, 53.3, "copié manuellement sur le Pi après génération", ha="center", fontsize=6.6,
            color="#888888", style="italic")

    # --- PC : modules partagés -> viewer ---
    fleche(bord("ref_csv_pc", "bas"), bord("formatting", "haut"), texte="load_reference()", fontsize=6.8)
    fleche(bord("formatting", "bas"), bord("viewer", "haut"), rad=0.08)
    fleche(bord("tooltips", "bas"), bord("viewer", "haut"), rad=-0.08)

    # --- viewer <-> guide ---
    fleche(bord("viewer", "bas"), bord("guide_py", "haut"), rad=0.1, texte="import direct + bouton \"Régénérer\"", fontsize=6.8)
    fleche(bord("guide_py", "droite"), bord("guide_pdf", "gauche"))

    # --- viewer <-> observations locales PC ---
    fleche(bord("obs_local_pc", "haut"), bord("viewer", "bas"), rad=-0.1, texte="lecture locale", fontsize=6.8)

    # --- rapport planifié ---
    fleche(bord("executer_rapport", "gauche"), bord("generer_rapport", "droite"), rad=0.12)
    fleche(bord("obs_local_pc", "bas"), bord("generer_rapport", "haut"), rad=0.1)
    fleche(bord("formatting", "gauche"), (37, 50), rad=0)
    fleche_coude([(37, 50), (37, 9.3), bord("generer_rapport", "gauche")], couleur=COULEUR_PC_BORD)
    fleche(bord("generer_rapport", "droite"), bord("rapports_pdf", "gauche"))
    fleche(bord("rapports_pdf", "bas"), bord("envoyer_nas", "haut"), rad=0.08)
    fleche(bord("executer_rapport", "bas"), bord("envoyer_nas", "haut"), rad=-0.15)

    # --- PC -> NAS (envoi rapport) ---
    fleche_coude(
        [bord("envoyer_nas", "droite"), (78.5, 4.4), (78.5, 44.5), bord("nas_rapports", "gauche")],
        texte="envoyer_rapport_nas.sh", texte_pos=(78.5, 22), fontsize=7.3, couleur=COULEUR_NAS_BORD,
    )

    ax.text(89.5, 51.5, "même sous-dossier\nquotidien/hebdomadaire\nque localement", ha="center", fontsize=6.6,
            color="#888888", style="italic")

    # ---------- Légende ----------
    legende_elements = [
        Line2D([0], [0], marker="s", color="none", markerfacecolor=COULEUR_EXT, markeredgecolor=COULEUR_EXT_BORD,
               markersize=13, label="Source externe"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor=COULEUR_PI, markeredgecolor=COULEUR_PI_BORD,
               markersize=13, label="Script automatisé (cron)"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor=COULEUR_PC, markeredgecolor=COULEUR_PC_BORD,
               markersize=13, label="Application / script principal"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor=COULEUR_MANUEL, markeredgecolor=COULEUR_MANUEL_BORD,
               markersize=13, label="Lancé manuellement / planifié"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor="#ffffff", markeredgecolor=COULEUR_PC_BORD,
               markersize=13, label="Module partagé (pas un script autonome)"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor=COULEUR_DONNEE, markeredgecolor=COULEUR_DONNEE_BORD,
               markersize=13, label="Fichier de données"),
    ]
    ax.legend(handles=legende_elements, loc="lower left", bbox_to_anchor=(0.002, 0.001), fontsize=8.3,
              frameon=True, facecolor="white", edgecolor="#cccccc", ncol=1, title="Légende", title_fontsize=9)

    plt.tight_layout()
    plt.savefig("organigramme_application.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("Organigramme généré : organigramme_application.png")


if __name__ == "__main__":
    generer()
