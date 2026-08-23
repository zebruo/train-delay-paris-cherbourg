"""
Génère un PDF de référence expliquant, en langage simple avec des exemples
concrets, les statistiques affichées dans viewer.py et dans les rapports
PDF (generer_rapport.py) — pour un lecteur non technique (usager de la
ligne, association UDUPC...), pas pour un public data/ingénierie.

Document statique, pas de données live : contrairement aux autres scripts
de ce projet, ne lit ni observations.csv ni le référentiel — uniquement du
texte pédagogique avec des chiffres d'exemple choisis pour être ronds et
faciles à suivre (pas des vraies données du jour).

Usage : python generer_guide_statistiques.py
Écrit : guide_statistiques.pdf (racine du projet, écrasé à chaque
génération — ce n'est pas un historique daté comme rapports/, juste la
version courante du guide).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.font_manager import FontProperties

COULEUR_ACCENT = "#c2410c"
COULEUR_TITRE = "#111"
COULEUR_GRIS = "#555"
COULEUR_EXEMPLE_FOND = "#f5f5f5"


def page_titre(fig):
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.text(0.5, 0.62, "Suivi des circulations sur l'axe Paris ↔ Cherbourg", fontsize=18,
             fontweight="bold", ha="center", va="center", color=COULEUR_TITRE)
    ax.text(0.5, 0.55, "Comment lire les statistiques", fontsize=16,
             ha="center", va="center", color=COULEUR_TITRE)
    ax.text(
        0.5, 0.44,
        "Guide de référence — ce que veut dire chaque chiffre, avec un exemple concret,\n"
        "que ce soit dans l'application de suivi ou dans les rapports quotidien/hebdomadaire/mensuelle.",
        fontsize=10.5, ha="center", va="center", color=COULEUR_GRIS,
    )


def dessiner_paragraphe_justifie(ax, x0, y0, texte, largeur_data, fontsize, color,
                                  fontweight="normal", fontstyle="normal", interligne=None):
    """Affiche un paragraphe "justifié" (lignes étirées pour occuper toute la
    largeur disponible, comme dans un traitement de texte) — matplotlib ne
    sait pas faire ça nativement (seul ha="left"/"center"/"right" existe).
    On mesure la largeur réelle de chaque mot avec le renderer de la figure,
    on compose les lignes mot par mot (comme un retour à la ligne
    automatique), puis pour chaque ligne complète (pas la dernière du
    paragraphe, ni une ligne d'un seul mot) on répartit l'espace en trop
    entre les mots en repositionnant chaque mot individuellement — demande
    explicite de l'utilisateur, 2026-07-28. Retourne la coordonnée y
    disponible juste après le paragraphe."""
    fig = ax.figure
    renderer = fig.canvas.get_renderer()
    fp = FontProperties(size=fontsize, weight=fontweight, style=fontstyle)

    def largeur_px(s):
        return renderer.get_text_width_height_descent(s, fp, False)[0]

    x0_px, x1_px = ax.transData.transform([(x0, 0), (x0 + largeur_data, 0)])[:, 0]
    largeur_ligne_px = x1_px - x0_px
    espace_px = largeur_px(" ")

    if interligne is None:
        # Même interligne relatif (1.5x) que le paragraphe "definition" plus
        # haut (ax.text(..., linespacing=1.5)) — converti en unités data via
        # la hauteur réelle des axes, pour rester cohérent quel que soit le
        # fontsize passé (fixer une valeur absolue, comme avant, donnait un
        # interligne bien trop grand pour les petites tailles de police —
        # repéré par l'utilisateur, 2026-07-28).
        _, y0_px_axe = ax.transData.transform((x0, 0))
        _, y1_px_axe = ax.transData.transform((x0, 1))
        px_par_unite_y = y1_px_axe - y0_px_axe
        hauteur_ligne_px = fontsize * (fig.dpi / 72) * 1.2 * 1.5
        interligne = hauteur_ligne_px / px_par_unite_y

    mots = texte.split()
    lignes = []
    ligne = []
    largeur_ligne_actuelle = 0
    for mot in mots:
        lm = largeur_px(mot)
        ajout = lm if not ligne else espace_px + lm
        if ligne and largeur_ligne_actuelle + ajout > largeur_ligne_px:
            lignes.append(ligne)
            ligne, largeur_ligne_actuelle = [mot], lm
        else:
            ligne.append(mot)
            largeur_ligne_actuelle += ajout
    if ligne:
        lignes.append(ligne)

    y = y0
    for i, mots_ligne in enumerate(lignes):
        derniere = i == len(lignes) - 1
        if derniere or len(mots_ligne) == 1:
            ax.text(x0, y, " ".join(mots_ligne), fontsize=fontsize, color=color,
                     va="top", fontweight=fontweight, style=fontstyle)
        else:
            largeurs_mots = [largeur_px(m) for m in mots_ligne]
            espace_total_px = largeur_ligne_px - sum(largeurs_mots)
            espace_par_mot_px = espace_total_px / (len(mots_ligne) - 1)
            x_px = x0_px
            for mot, lm in zip(mots_ligne, largeurs_mots):
                x_data = ax.transData.inverted().transform((x_px, 0))[0]
                ax.text(x_data, y, mot, fontsize=fontsize, color=color,
                         va="top", fontweight=fontweight, style=fontstyle)
                x_px += lm + espace_par_mot_px
        y -= interligne
    return y


def section(ax, y, titre, definition, exemple_lignes, utilite, pourquoi=None, sous_titre=None):
    """Dessine un bloc explicatif (titre + définition + encart d'exemple +
    utilité + justification optionnelle) à partir de la coordonnée y (1=haut,
    0=bas de l'axe), et renvoie la coordonnée y disponible juste après le
    bloc. "utilite" est obligatoire (contrairement à "pourquoi") : chaque
    statistique doit dire à quoi elle sert concrètement pour le lecteur, pas
    seulement ce qu'elle mesure — demande explicite de l'utilisateur,
    2026-07-28. "sous_titre" (optionnel) s'affiche à la suite du titre, en
    plus petit et plus clair, pour donner en un mot ce que reflète la
    statistique (ex: "reflète l'état final réel") — sauf si titre+sous_titre
    ne tiennent pas sur la largeur de la page (mesuré en pixels réels via le
    renderer, pas estimé), auquel cas le sous-titre bascule sur sa propre
    ligne juste en dessous plutôt que de déborder hors de la page — repéré
    par l'utilisateur, 2026-08-20, sur « Régénérer » et « Déployer vers la
    VPS », le titre le plus long du guide combiné à un sous-titre déjà
    conséquent."""
    ax.text(0, y, titre, fontsize=13, fontweight="bold", color=COULEUR_ACCENT, va="top")
    if sous_titre:
        fig = ax.figure
        renderer = fig.canvas.get_renderer()
        fp_titre = FontProperties(size=13, weight="bold")
        fp_sous_titre = FontProperties(size=10)
        largeur_titre_px = renderer.get_text_width_height_descent(titre, fp_titre, False)[0]
        largeur_sous_titre_px = renderer.get_text_width_height_descent(sous_titre, fp_sous_titre, False)[0]
        x0_px, x1_px = ax.transData.transform([(0, 0), (1, 0)])[:, 0]
        largeur_disponible_px = x1_px - x0_px
        largeur_titre_data = largeur_titre_px / largeur_disponible_px
        espace_px = 0.012 * largeur_disponible_px
        if largeur_titre_px + espace_px + largeur_sous_titre_px <= largeur_disponible_px:
            ax.text(largeur_titre_data + 0.012, y, sous_titre, fontsize=10, color=COULEUR_GRIS, va="top")
        else:
            y -= 0.032
            ax.text(0, y, sous_titre, fontsize=10, color=COULEUR_GRIS, va="top")
    y -= 0.045

    ax.text(0, y, definition, fontsize=10, color=COULEUR_TITRE, va="top", linespacing=1.5)
    # Hauteur occupée par la définition : approximée par son nombre de
    # lignes (déjà découpées à la main dans le texte source ci-dessous, pas
    # de retour à la ligne automatique) plutôt que mesurée dynamiquement —
    # suffisant ici puisque le texte est entièrement écrit par nous, la
    # largeur de chaque ligne est donc déjà contrôlée.
    y -= 0.028 * (definition.count("\n") + 1) + 0.02

    hauteur_encart = 0.033 * len(exemple_lignes) + 0.025
    ax.add_patch(plt.Rectangle((0, y - hauteur_encart), 1, hauteur_encart,
                                facecolor=COULEUR_EXEMPLE_FOND, edgecolor="none", zorder=1))
    y_ligne = y - 0.02
    for ligne in exemple_lignes:
        ax.text(0.02, y_ligne, ligne, fontsize=9.5, color=COULEUR_TITRE, va="top",
                 family="monospace", zorder=2)
        y_ligne -= 0.033
    y -= hauteur_encart + 0.02

    # "utilite" et "pourquoi" sont écrits en une seule phrase continue dans
    # le code source (pas de \n à la main) — mis en page ici en paragraphe
    # justifié (voir dessiner_paragraphe_justifie), qui calcule lui-même le
    # nombre de lignes réellement nécessaires.
    y = dessiner_paragraphe_justifie(
        ax, 0, y, f"Utile pour : {utilite}", 1, fontsize=9, color=COULEUR_TITRE, fontweight="bold",
    )
    y -= 0.02

    if pourquoi:
        # "\n\n" sépare deux paragraphes distincts (ex: une remarque
        # accessoire après l'explication principale) — dessiner_paragraphe_
        # justifie() ignore les retours à la ligne (texte.split() les
        # traite comme de simples espaces), donc le découpage en paragraphes
        # doit se faire ici, avant l'appel.
        paragraphes = pourquoi.split("\n\n")
        for i, paragraphe in enumerate(paragraphes):
            y = dessiner_paragraphe_justifie(
                ax, 0, y, paragraphe, 1, fontsize=8.5, color=COULEUR_GRIS, fontstyle="italic",
            )
            if i < len(paragraphes) - 1:
                y -= 0.015
        y -= 0.02

    return y - 0.02


def page_contenu(fig, titre_page, blocs):
    ax = fig.add_axes([0.07, 0.05, 0.86, 0.88])
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.text(0, 1.0, titre_page, fontsize=14, fontweight="bold", color=COULEUR_TITRE, va="top")
    # Titre sur 2 lignes (trop long pour tenir sur une seule, voir
    # GUIDE_PAGES) : laisser la place en abaissant le point de départ du
    # premier bloc d'autant.
    y = 0.93 - 0.045 * titre_page.count("\n")
    for bloc in blocs:
        y = section(ax, y, **bloc)


# Contenu du guide, indépendant de sa mise en page : une entrée par page PDF
# (le découpage en pages a été ajusté à la main pour éviter les débordements
# — voir l'historique du fichier — donc en conserver un par entrée ici plutôt
# que de tout regrouper par thème). Source unique consommée à la fois par
# generer() ci-dessous (rendu PDF justifié) et par l'onglet "Guide
# statistiques" de viewer.py (rendu Tkinter natif) — demande explicite de
# l'utilisateur, 2026-07-28.
GUIDE_PAGES = [
    dict(
        titre_page="1. Combien de trains ont été touchés ?",
        blocs=[
            dict(
                titre="Circulations perturbées",
                definition=(
                    "Le nombre de trajets (un train, un jour donné) ayant eu du retard\n"
                    "à un moment de leur trajet — même s'ils l'ont ensuite rattrapé avant\n"
                    "l'arrivée."
                ),
                exemple_lignes=[
                    "Prenons une période avec 200 circulations sur la ligne.",
                    "40 d'entre elles ont eu du retard à un moment de leur trajet.",
                    "→ Affiché : « 40 circulations perturbées / 200 (20 %) »",
                ],
                utilite=(
                    "avoir en un coup d'œil une idée de l'ampleur des perturbations du jour "
                    "ou de la semaine — une bonne ou une mauvaise période pour la ligne. "
                    "« à quel point la journée a été mauvaise »."
                ),
                pourquoi=(
                    "« circulations perturbées » vs "
                    "« ponctualité » ? La ponctualité officielle SNCF/ART (Autorité de "
                    "Régulation des Transports) mesure uniquement le retard à l'arrivée au "
                    "terminus. Un train retardé de 10 minutes entre Caen et Lison mais qui "
                    "arrive à l'heure à Cherbourg sera compté comme 100 % ponctuel dans les "
                    "statistiques officielles, ignorant la perturbation subie par les "
                    "voyageurs descendant à Lison. Notre indicateur compte tout train ayant "
                    "subi un retard à un moment quelconque de son trajet, même s'il a été "
                    "rattrapé ensuite. La ponctualité officielle évalue la performance au "
                    "terminus ; le taux de « circulations perturbées » reflète l'impact réel "
                    "vécu tout au long de la ligne.\n\n"
                    "Le tooltip précise aussi « issues de X trains différents parmi les Y du "
                    "référentiel » — X peut dépasser Y sans que ce soit un souci en cours. Ce "
                    "chiffre porte sur toute la collecte depuis le début : un train observé un "
                    "jour où le référentiel ne le connaissait pas encore reste compté dans X "
                    "même après une mise à jour qui l'a depuis intégré. Ce n'est donc pas un "
                    "indicateur de fraîcheur du référentiel à l'instant présent — voir "
                    "« Vérification GTFS » plus loin dans ce guide."
                ),
            ),
        ],
    ),
    dict(
        titre_page="1. Combien de trains ont été touchés ? (suite)",
        blocs=[
            dict(
                titre="Retard cumulé",
                sous_titre="(reflète l'état final réel)",
                definition=(
                    "La somme des retards observés, en ne comptant chaque passage en gare\n"
                    "qu'une seule fois (le dernier retard connu à ce passage)\n"
                    "pas à chaque fois que le système a vérifié ce train."
                ),
                exemple_lignes=[
                    "Nos 40 circulations perturbées ont, ensemble, touché 90 passages",
                    "en gare (un même train peut être en retard à plusieurs gares).",
                    "Ces 90 passages cumulent 15 h de retard au total.",
                    "→ Affiché : « Retard cumulé : 15 h 00 min »",
                    "(le détail « 90 passages impactés » n'apparaît qu'au-dessus du graphique",
                    "de l'onglet Graphique, pas dans cette barre de stats du haut)",
                ],
                utilite=(
                    "comparer deux journées ou deux semaines entre elles sur le volume réel "
                    "de perturbation, ou chiffrer le temps perdu cumulé sur la ligne (utile "
                    "pour un dossier ou un argumentaire auprès de la SNCF)."
                ),
                pourquoi=(
                    "Cette stat ne garde que le dernier retard connu par gare — si pour ce "
                    "train, le dernier relevé montre 0 min partout, du départ à l'arrivée, "
                    "donc aucun « passage impacté » comptabilisé pour lui, même si des "
                    "retards ont pu être observés plus tôt."
                ),
            ),
        ],
    ),
    dict(
        titre_page="1. Combien de trains ont été touchés ? (suite)",
        blocs=[
            dict(
                titre="Retard moyen / relevé",
                sous_titre="(reflète toute l'histoire des prédictions même corrigées)",
                definition=(
                    "La moyenne de tous les relevés individuels du système\n"
                    "(chaque interrogation, gare par gare) — pas une moyenne par train ni\n"
                    "par passage."
                ),
                exemple_lignes=[
                    "Ce même jour, le système a fait 8 000 relevés au total (la",
                    "plupart à 0 min ou vite corrigés : un train à l'heure reste à",
                    "l'heure à chaque relevé). La somme de tous les retards fait 900",
                    "minutes.",
                    "→ Affiché : « Retard moyen / relevé : 900 ÷ 8 000 = 0.1 min »",
                ],
                utilite=(
                    "avoir un ordre de grandeur pour interpréter l'échelle de la courbe "
                    "d'évolution du retard dans le temps (onglet Graphique) — un écart "
                    "soudain entre « moy. » et « Retard moyen / relevé » — le premier qui "
                    "grimpe bien plus vite que le second — trahit souvent un incident "
                    "ponctuel (coupure partielle de collecte/flux SNCF figé) plutôt qu'un "
                    "vrai pic de retard généralisé."
                ),
                pourquoi=(
                    "Ce chiffre paraît minuscule à côté d'un retard max de 45 min — c'est "
                    "normal, il est dilué par des milliers de relevés à 0. À ne pas "
                    "confondre avec la courbe de l'onglet Graphique : celle-ci calcule une "
                    "moyenne différente (instant par instant, puis ces points sont moyennés "
                    "entre eux) — les deux donnent un ordre de grandeur similaire, rarement "
                    "le chiffre exact.\n"
                    "Accessoirement, elle capture le volume de signaux d'alerte temporaires "
                    "vus par le système en temps réel, même corrigés ensuite — une sorte de "
                    "« volatilité » des prédictions plutôt qu'un résultat final."
                ),
            ),
        ],
    ),
    dict(
        titre_page="2. Où et sur quel train le retard a-t-il été le plus fort ?",
        blocs=[
            dict(
                titre="Retard max",
                definition="Le plus grand retard observé sur la période, avec le train concerné.",
                exemple_lignes=[
                    "→ Affiché : « Retard max : train 1234 → 45 min »",
                    "Ce train a connu le pire retard de la période, 45 minutes, quelque",
                    "part sur son trajet.",
                ],
                utilite=(
                    "repérer tout de suite le pire incident de la période, celui qui a le "
                    "plus affecté un train en particulier."
                ),
                pourquoi=(
                    "Le flux temps réel SNCF ne confirme jamais explicitement l'arrivée d'un "
                    "train : le trajet disparaît simplement du flux une fois terminé, souvent "
                    "juste après l'heure d'arrivée prévue. Ce retard max (et plus généralement "
                    "toute valeur affichée pour un trajet, y compris dans l'onglet « Suivi d'un "
                    "train ») correspond donc à la dernière prédiction connue avant cette "
                    "disparition, pas à une confirmation réelle d'arrivée — dans l'immense "
                    "majorité des cas les deux se rejoignent, mais rien ne le garantit "
                    "formellement."
                ),
            ),
            dict(
                titre="Gare la + touchée",
                definition=(
                    "La gare avec le retard moyen le plus élevé, tous relevés\n"
                    "confondus (y compris ceux à 0 min)."
                ),
                exemple_lignes=[
                    "→ Affiché : « Gare la + touchée : Bayeux → moy 3.6 min »",
                    "En moyenne sur tous les relevés de la période, Bayeux",
                    "affiche 3.6 min de retard — la valeur la plus haute des 11 gares",
                    "de la ligne.",
                ],
                utilite=(
                    "voir si les problèmes se concentrent sur un point précis de la ligne "
                    "(travaux, nœud engorgé) plutôt que d'être répartis partout."
                ),
            ),
        ],
    ),
    dict(
        titre_page="2. Où et sur quel train le retard a-t-il été le plus fort ? (suite)",
        blocs=[
            dict(
                titre="Les couleurs du Tableau",
                definition=(
                    "Trois couleurs de texte dans l'onglet Tableau, selon le retard observé\n"
                    "au dernier relevé d'une ligne — pas de couleur (texte noir) si tout va\n"
                    "bien."
                ),
                exemple_lignes=[
                    "Rouge : retard à l'arrivée ≥ 10 min dans cette gare.",
                    "Orange : retard à l'arrivée entre 5 et 10 min dans cette gare.",
                    "Doré/jaune : arrivée correcte (< 5 min), mais retard au départ ≥ 5 min",
                    "— le train reste immobilisé plus longtemps que prévu à cette gare.",
                ],
                utilite=(
                    "repérer d'un coup d'œil non seulement l'ampleur d'un retard, mais aussi "
                    "son type : un retard déjà là à l'arrivée (rouge/orange) n'annonce pas la "
                    "même chose qu'un train à l'heure qui traîne au départ (doré)."
                ),
                pourquoi=(
                    "Le doré existe pour un cas précis qui resterait sinon invisible : un train "
                    "arrivé pile à l'heure n'a aucune couleur d'alerte si on ne regarde que le "
                    "retard à l'arrivée — alors qu'il peut être en train d'accumuler un vrai "
                    "retard de départ, pas encore visible ailleurs. Sans cette couleur séparée, "
                    "ce genre de ligne resterait noire, comme si de rien n'était, jusqu'à ce que "
                    "le retard finisse par apparaître à la gare suivante."
                ),
            ),
        ],
    ),
    dict(
        titre_page="2. Où et sur quel train le retard a-t-il été le plus fort ? (suite)",
        blocs=[
            dict(
                titre="Ligne dorée : incident frais, ou connu ?",
                sous_titre="(non distingué par l'appli)",
                definition=(
                    "Le doré signale qu'un train attend plus longtemps que prévu au départ\n"
                    "d'une gare — sans dire depuis quand cette attente est connue."
                ),
                exemple_lignes=[
                    "Cas 1 — nouveau : le train vient d'annoncer une attente imprévue",
                    "(incident, croisement non planifié) — un vrai signal à surveiller.",
                    "Cas 2 — déjà connu : l'attente est annoncée depuis longtemps (dès le",
                    "départ du trajet, ou depuis plusieurs relevés) — la SNCF le sait déjà,",
                    "ce n'est pas une surprise, juste un aléa stable.",
                ],
                utilite=(
                    "éviter de s'alarmer pour une ligne dorée qui traîne en réalité depuis des "
                    "heures sans rien de nouveau — mais il faut le vérifier soi-même, l'appli "
                    "ne le fait pas automatiquement."
                ),
                pourquoi=(
                    "Distinguer les deux cas demanderait de comparer ce retard de départ à ce "
                    "qu'il valait aux relevés précédents de ce même train, pas juste au dernier "
                    "— une fonctionnalité jugée utile seulement en surveillance active de "
                    "l'appli (voir si un problème vient d'apparaître), sans intérêt pour les "
                    "statistiques ou les rapports PDF (qui ne retiennent de toute façon que la "
                    "dernière valeur connue) — pas construite pour l'instant. En attendant, "
                    "« Suivi d'un train » permet de vérifier à la main : si le retard de départ "
                    "est stable sur plusieurs relevés d'affilée, c'est le cas 2 ; s'il vient de "
                    "changer par rapport au relevé précédent, c'est le cas 1."
                ),
            ),
        ],
    ),
    dict(
        titre_page="3. Autres points utiles rapports PDF\nquotidien/hebdomadaire/mensuelle",
        blocs=[
            dict(
                titre="« Circulations sur l'axe Paris ↔ Cherbourg »",
                definition=(
                    "Le rapport ne suit pas seulement les trains Paris-Cherbourg de bout\n"
                    "en bout : il suit tout train empruntant un tronçon de cette ligne,\n"
                    "même s'il continue ailleurs ensuite."
                ),
                exemple_lignes=[
                    "Un train Rennes → Caen, qui partage les gares de Lison, Bayeux et",
                    "Caen avec la ligne Paris-Cherbourg, est inclus dans les statistiques",
                    "— même s'il ne va jamais jusqu'à Paris ni jusqu'à Cherbourg.",
                ],
                utilite=(
                    "comprendre pourquoi des trains qu'on ne prend pas forcément soi-même "
                    "(ex: Rennes → Caen) apparaissent dans les statistiques du rapport."
                ),
                pourquoi=(
                    "C'est volontaire : un retard survenu sur un tronçon partagé est un "
                    "vrai signal utile pour la ligne, qu'il vienne d'un train qui va "
                    "jusqu'au bout ou non."
                ),
            ),
            dict(
                titre="Période du rapport (2 h → 2 h)",
                definition=(
                    "Le rapport quotidien couvre une journée de 2 h du matin à 2 h le\n"
                    "lendemain (pas minuit à minuit, ni les 24 dernières heures)."
                ),
                exemple_lignes=[
                    "Un rapport généré le 28/07 à 7 h couvre la période du 27/07 à 2 h",
                    "au 28/07 à 2 h — le dernier cycle complet déjà terminé.",
                ],
                utilite=(
                    "comparer les rapports entre eux de façon cohérente d'un jour sur "
                    "l'autre, avec une même définition de « journée »."
                ),
                pourquoi=(
                    "2 h du matin est le creux du trafic nocturne (peu ou pas de trains) : "
                    "ça limite le risque qu'une circulation soit coupée en deux périodes "
                    "différentes."
                ),
            ),
        ],
    ),
    dict(
        titre_page="3. Autres points utiles rapports PDF\nquotidien/hebdomadaire/mensuelle (suite)",
        blocs=[
            dict(
                titre="Météo et Travaux / alertes",
                definition=(
                    "Moyennes météo (température, pluie, vent) et liste des perturbations\n"
                    "SNCF en cours sur la ligne pendant la période — sans traitement\n"
                    "particulier, affichées telles que publiées par les sources."
                ),
                exemple_lignes=["Rien à calculer ici — ces deux blocs sont les plus simples du rapport."],
                utilite=(
                    "vérifier si un pic de retard coïncide avec une cause probable (mauvais "
                    "temps, travaux signalés)."
                ),
            ),
            dict(
                titre="Circulations annulées",
                definition=(
                    "Nombre de trains entièrement annulés sur la période, parmi ceux qui\n"
                    "desservent au moins une des 11 gares de la ligne — invisibles de toutes\n"
                    "les autres statistiques ci-dessus (un train annulé n'atteint jamais son\n"
                    "terminus, donc jamais considéré « arrivé »)."
                ),
                exemple_lignes=[
                    "→ Affiché : « Circulations annulées sur la période (sur la ligne) :",
                    "  2 (852610, 853430). »",
                ],
                utilite=(
                    "voir d'un coup d'œil si des trains ont été purement et simplement "
                    "supprimés — une perturbation plus grave qu'un simple retard, mais "
                    "invisible du reste du rapport."
                ),
            ),
            dict(
                titre="Rupture de collecte le 31/07/2026",
                definition=(
                    "Un bug de collecte, corrigé ce jour-là, faisait manquer jusqu'à ~1 train\n"
                    "sur 4 certains jours (un identifiant technique mal reconnu, sans rien\n"
                    "signaler). Les statistiques globales augmentent donc mécaniquement à\n"
                    "partir de cette date."
                ),
                exemple_lignes=[
                    "Avant le 31/07/2026 : jusqu'à ~27 % des trains manqués certains jours.",
                    "Après : quasi tous les trains sont captés.",
                ],
                utilite=(
                    "ne pas confondre une hausse des chiffres autour de cette date avec une "
                    "vraie dégradation de la ponctualité."
                ),
                pourquoi=(
                    "L'identifiant technique d'un train intégrait une date qui ne "
                    "correspondait plus toujours à la date réelle — corrigé, mais sans effet "
                    "rétroactif sur l'historique déjà collecté."
                ),
            ),
        ],
    ),
    dict(
        titre_page="4. L'onglet « Vérification GTFS » : les horaires\nsuivis sont-ils toujours à jour ?",
        blocs=[
            dict(
                titre="À quoi sert cet onglet",
                definition=(
                    "Compare chaque jour les horaires théoriques publiés en ligne par la SNCF\n"
                    "à ceux utilisés par l'application (un fichier de référence, mis à jour\n"
                    "manuellement de temps en temps) — pour repérer quand cette référence\n"
                    "devient obsolète."
                ),
                exemple_lignes=[
                    "Une ligne du tableau : « 589 communs (546 identiques, 43 modifiés),",
                    "160 disparus, 4 nouveaux, 2 renommés » — comparé à la référence actuelle.",
                ],
                utilite=(
                    "savoir si les trains suivis par l'application correspondent encore aux "
                    "horaires réels de la SNCF, sans avoir à comparer les fichiers à la main."
                ),
                pourquoi=(
                    "La référence n'est jamais mise à jour toute seule : la SNCF republie ses "
                    "horaires en continu, et un changement (nouvel horaire, nouvel arrêt, "
                    "nouveau train) doit être vérifié avant d'être appliqué, pas remplacé "
                    "silencieusement."
                ),
            ),
        ],
    ),
    dict(
        titre_page="4. L'onglet « Vérification GTFS » (suite)",
        blocs=[
            dict(
                titre="La colonne « Nouveaux »",
                sous_titre="(le chiffre le plus important)",
                definition=(
                    "Le nombre de trains présents dans les horaires SNCF en ligne, mais\n"
                    "absents de la référence utilisée par l'application — ces trains-là ne\n"
                    "sont actuellement suivis par aucun des relevés de l'application."
                ),
                exemple_lignes=[
                    "« Modifiés »/« Disparus » : un horaire théorique affiché peut être un peu",
                    "faux, mais le train reste suivi.",
                    "« Nouveaux » : le train n'est suivi du tout — aucun relevé, aucune",
                    "statistique ne le concerne.",
                ],
                utilite=(
                    "décider quand une mise à jour de la référence (bouton dédié, ou "
                    "manuellement) est vraiment nécessaire, plutôt que de réagir à chaque "
                    "petite variation."
                ),
                pourquoi=(
                    "Les horaires SNCF publiés en ligne ne couvrent jamais que les ~151 "
                    "prochains jours, une fenêtre qui avance d'un jour chaque jour. Un train "
                    "déjà prévu par la SNCF mais plus loin dans le temps devient visible "
                    "d'un coup le jour où cette fenêtre l'atteint — sans que la SNCF n'ait "
                    "rien changé de son côté. Ça peut faire bouger légèrement les chiffres "
                    "d'un jour sur l'autre sans signification particulière (observé : 135 "
                    "puis 160 trains « disparus », 5 puis 4 « nouveaux », entre deux "
                    "vérifications à quelques heures d'écart, sans aucun changement réel "
                    "d'horaire entre-temps).\n\n"
                    "D'où la règle simple à suivre : ne pas s'inquiéter d'un chiffre isolé "
                    "sur un seul jour, mais surveiller si « Nouveaux » reste supérieur à "
                    "zéro plusieurs jours de suite dans le tableau — c'est ce qui distingue "
                    "un vrai changement durable d'un simple effet de la fenêtre glissante."
                ),
            ),
        ],
    ),
    dict(
        titre_page="4. L'onglet « Vérification GTFS » (suite)",
        blocs=[
            dict(
                titre="La colonne « Renommés »",
                definition=(
                    "Un service dont les arrêts et les horaires sont restés exactement\n"
                    "identiques, mais dont l'identifiant technique a changé — ni vraiment\n"
                    "disparu, ni vraiment nouveau."
                ),
                exemple_lignes=[
                    "La SNCF réattribue parfois un service à une route légèrement différente",
                    "(ex: un car de substitution détaché de la ligne principale vers une route",
                    "ad hoc) sans toucher ni aux arrêts ni aux horaires. Sans traitement",
                    "particulier, ce même service apparaîtrait à la fois comme « disparu »",
                    "et « nouveau ».",
                ],
                utilite=(
                    "ne pas confondre un simple changement d'identifiant (sans conséquence "
                    "réelle) avec un vrai changement de desserte — « Renommés » reste un "
                    "signal plus faible que « Nouveaux »/« Disparus » distincts."
                ),
                pourquoi=(
                    "Détecté par rapprochement strict : seuls les services ayant exactement "
                    "les mêmes arrêts et les mêmes horaires des deux côtés sont comptés en "
                    "« renommé » — un service remplacé par un autre proche mais pas identique "
                    "(quelques minutes d'écart) reste compté en disparu + nouveau distinct, "
                    "pas noyé ici."
                ),
            ),
        ],
    ),
    dict(
        titre_page="4. L'onglet « Vérification GTFS » (suite)",
        blocs=[
            dict(
                titre="« Régénérer » et « Déployer vers la VPS »",
                sous_titre="(mettre à jour la référence — application desktop uniquement)",
                definition=(
                    "Deux boutons séparés, en deux étapes volontairement distinctes.\n"
                    "« Régénérer » télécharge l'horaire SNCF du jour et reconstruit la\n"
                    "référence — sur ce PC uniquement, sans rien changer côté VPS. « Déployer\n"
                    "vers la VPS » envoie ensuite cette nouvelle référence là où elle compte\n"
                    "vraiment : la VPS, qui collecte les données en continu et héberge le site."
                ),
                exemple_lignes=[
                    "Avant l'envoi, une confirmation rappelle les deux versions en jeu :",
                    "« Référentiel actuellement sur la VPS : 2026-07-19",
                    "  Référentiel local à déployer : 2026-08-04 »",
                ],
                utilite=(
                    "mettre à jour la référence sans ligne de commande, tout en gardant la main "
                    "à chaque étape — rien ne part vers la VPS sans un clic de confirmation "
                    "explicite."
                ),
                pourquoi=(
                    "Séparer « Régénérer » (local, sans conséquence) de « Déployer » (touche la "
                    "collecte et le site, tous deux en production sur la VPS) évite qu'une "
                    "régénération machinale ne modifie sans le vouloir ce qui tourne réellement. "
                    "Une fois déployée, la vérification est aussitôt relancée là-bas et le service "
                    "web redémarré automatiquement : sans ça, le seuil d'alerte resterait basé sur "
                    "l'ancienne référence jusqu'au prochain passage automatique (3h15), et des "
                    "champs comme « Sens »/« Heure théo. » resteraient basés sur l'ancienne version."
                ),
            ),
        ],
    ),
]


def generer():
    with PdfPages("guide_statistiques.pdf") as pdf:
        fig = plt.figure(figsize=(8.27, 11.69))
        page_titre(fig)
        pdf.savefig(fig)
        plt.close(fig)

        for page in GUIDE_PAGES:
            fig = plt.figure(figsize=(8.27, 11.69))
            page_contenu(fig, page["titre_page"], page["blocs"])
            pdf.savefig(fig)
            plt.close(fig)

    print("Guide généré : guide_statistiques.pdf")


if __name__ == "__main__":
    generer()
