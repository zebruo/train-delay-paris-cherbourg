"""
Fonctions de tracé matplotlib partagées entre viewer.py (Tkinter) et
app_streamlit.py (Streamlit) : ne dépendent que d'un `ax` matplotlib passé en
paramètre, jamais de Tkinter ni d'un canvas particulier. L'info-bulle au
survol (paramètre `survol`, une instance de `tooltips.SurvolArtistes`) est
optionnelle : laissée à `None`, aucune info-bulle n'est enregistrée — c'est
le cas pour Streamlit, où le graphique est une image statique `st.pyplot`
sans interaction possible.
"""
import pandas as pd
from matplotlib.ticker import AutoMinorLocator

from formatting import PARIS_TZ


def tracer_serie_temporelle(ax, serie, couleur):
    """Trace une série temporelle en couleur pleine, avec les trous de
    collecte en gris pointillé plutôt que reliés comme de vraies données. Un
    relevé toutes les 5 min (cron de collecte) : un écart entre deux points
    consécutifs nettement supérieur à ça (marge prise à 10 min) signale un
    vrai trou de collecte (ex: peu/pas de trains la nuit), pas une série de
    vraies valeurs à 0 (voir mémoire du projet, 2026-07-21). Utilisée pour le
    retard moyen et pour la proportion de trains en retard (2026-07-23),
    d'où la factorisation."""
    SEUIL_TROU = pd.Timedelta(minutes=10)
    ecarts = serie.index.to_series().diff()
    apres_trou = ecarts[ecarts > SEUIL_TROU].index
    for t in apres_trou:
        i = serie.index.get_loc(t)
        ax.plot(
            serie.index[i - 1:i + 1], serie.values[i - 1:i + 1],
            color="#bbbbbb", linestyle="--", linewidth=1, zorder=1,
        )
    # Ligne principale coupée aux mêmes endroits (NaN juste avant chaque
    # reprise) pour ne pas la relier en travers du trou.
    coupures = pd.Series(float("nan"), index=apres_trou - pd.Timedelta(milliseconds=1))
    avec_coupures = pd.concat([serie, coupures]).sort_index()
    ax.plot(avec_coupures.index, avec_coupures.values, color=couleur,
            marker="o", markersize=1.5, linewidth=1, zorder=2)
    # En arrière-plan (zorder) et en pointillés : à 0 min de retard, une
    # ligne de référence pleine et au premier plan masquerait la courbe.
    ax.axhline(0, color="gray", linewidth=0.8, linestyle=(0, (4, 3)), zorder=1)


def marquer_maximum(ax, serie, couleur, unite, explication, survol=None):
    """Repère le pic de la série (valeur + date/heure) : une étoile sur le
    point, une étiquette à côté, et — si `survol` est fourni (SurvolArtistes)
    — une info-bulle sur les deux qui explique ce que ce maximum apporte de
    plus que les stats déjà affichées ailleurs. Appelée après finalize_axes
    plutôt que depuis tracer_serie_temporelle : comme pour les barres "n=",
    le cadrage à marge quasi nulle laisse trop peu de place en haut pour
    l'étiquette si elle est ajoutée avant — on agrandit donc l'axe après
    coup, une fois sa vraie position connue (voir mémoire du projet,
    2026-07-23)."""
    serie_valide = serie.dropna()
    if serie_valide.empty:
        return
    t_max = serie_valide.idxmax()
    v_max = serie_valide.loc[t_max]
    if v_max <= 0:
        # Une courbe plate à 0 n'a pas de vrai pic à signaler — une étoile
        # "Max : 0.0" pointerait arbitrairement le premier point, comme si
        # c'était une information notable.
        return
    point, = ax.plot(t_max, v_max, marker="*", markersize=11, color=couleur,
                      markeredgecolor="black", markeredgewidth=0.5, zorder=4)
    t_max_local = pd.Timestamp(t_max).tz_convert(PARIS_TZ)
    etiquette = ax.annotate(
        f"Max : {v_max:.1f}{unite}\n{t_max_local.strftime('%d/%m %Hh%M')}",
        xy=(t_max, v_max), xytext=(8, 8), textcoords="offset points",
        fontsize=7, color="#333333",
        bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=couleur, alpha=0.9),
    )
    if survol is not None:
        survol.enregistrer(point, explication)
        survol.enregistrer(etiquette, explication)
    y_min, y_max = ax.get_ylim()
    ax.set_ylim(top=y_max + (y_max - y_min) * 0.18)


def marquer_moyenne(ax, serie, couleur, unite, explication, survol=None):
    """Ligne pointillée horizontale à la moyenne de la période/vue
    actuellement affichée — complète marquer_maximum (qui montre le pire
    moment) en donnant un niveau de référence pour juger si un point/une
    barre est au-dessus ou en dessous de la tendance générale, plutôt que de
    devoir comparer à l'œil sans repère.
    survol : instance SurvolArtistes à utiliser pour l'info-bulle — laissé
    à None (aucune info-bulle) pour un rendu sans interaction possible
    (ex: image statique st.pyplot côté Streamlit)."""
    serie_valide = serie.dropna()
    if serie_valide.empty:
        return
    moyenne = serie_valide.mean()
    ligne = ax.axhline(moyenne, color=couleur, linestyle="--", linewidth=1, alpha=0.6, zorder=1)
    etiquette = ax.annotate(
        f"moy. {moyenne:.1f}{unite}",
        xy=(1, moyenne), xycoords=("axes fraction", "data"),
        xytext=(-4, 4), textcoords="offset points",
        fontsize=7, color=couleur, ha="right",
        # Fond blanc semi-opaque : sans lui, l'étiquette devient illisible
        # quand la ligne de moyenne passe pile devant une barre de la même
        # couleur (repéré par l'utilisateur, 2026-07-30, sur les graphiques
        # à barres de l'onglet Par jour/heure).
        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8),
    )
    if survol is not None:
        survol.enregistrer(ligne, explication)
        survol.enregistrer(etiquette, explication)


def finalize_axes(ax, marge_bas=False, marge_x_min=0.1):
    """Marges/bordures communes aux graphiques 'Graphique' et 'Suivi d'un
    train' : zéro exactement à l'intersection des axes (pas de marge de 5%
    par défaut), plage Y minimale lisible quand toutes les valeurs sont
    quasi identiques (sinon matplotlib invente une échelle du type
    '1e-17'), et suppression des bordures haut/droite.
    marge_bas=True (onglet 'Suivi d'un train' uniquement) redonne un peu
    d'espace sous la valeur minimale, pour que la ligne pointillée de
    référence à 0 reste visible en pointillés au lieu de se confondre avec
    le bord inférieur du graphique.
    marge_x_min : plancher de la marge en X, dans l'unité de l'axe appelant
    — 0.1 par défaut convient à l'axe par gares (indices ~0-10) de "Suivi
    d'un train", mais est bien trop grand pour un axe temporel (0.1 = 0.1
    JOUR = 2.4h) : le Graphique doit passer un plancher bien plus petit,
    sinon cette marge, fixe en valeur absolue, devient très visible sur une
    courte période ("dernières 24h") tout en restant invisible sur une
    longue ("7 derniers jours") — voir mémoire du projet, 2026-07-24."""
    ax.margins(x=0, y=0)
    y_min, y_max = ax.get_ylim()
    degenere = y_max - y_min < 1
    if degenere:
        centre = (y_min + y_max) / 2
        if marge_bas:
            # Mêmes marges resserrées que le cas normal (voir plus bas) —
            # sinon un trajet plat (ex: 0 min partout) affichait un grand
            # espace vide au-dessus/en-dessous, disproportionné par rapport
            # aux trajets avec un vrai retard.
            ax.set_ylim(centre - 0.1, centre + 1)
        else:
            bas, haut = centre - 2.5, centre + 2.5
            if bas < 0:
                # Un retard/% n'est jamais négatif : ne pas descendre sous 0
                # juste pour garder une plage symétrique artificielle (ex:
                # barres d'une catégorie sans aucune variation, toutes à 0
                # min) — on décale la plage vers le haut plutôt que de la
                # rétrécir, pour garder la même échelle visuelle que les
                # autres graphiques dégénérés.
                haut -= bas
                bas = 0
            ax.set_ylim(bas, haut)
    elif marge_bas:
        marge = max((y_max - y_min) * 0.02, 0.75)
        marge_haut = max((y_max - y_min) * 0.02, 1)
        ax.set_ylim(y_min - marge, y_max + marge_haut)

    # Un retard (ou un %) n'est jamais négatif : sur les deux onglets
    # utilisant cette méthode (Graphique/Suivi d'un train ET Par jour/
    # heure), une graduation en dessous de 0 n'a pas de sens — filtrée
    # systématiquement, pas seulement quand marge_bas laisse une marge sous
    # 0 pour le pointillé de référence. Sans ça, un trajet/une catégorie
    # sans aucune variation (ex: 0 min partout) retombait sur la plage
    # symétrique par défaut de la branche "degenere" ci-dessus, avec des
    # graduations négatives visibles.
    _, y_max_actuel = ax.get_ylim()
    # Borné aussi par le haut avec la limite actuelle : le locator par
    # défaut propose parfois une graduation légèrement au-delà (ex: 30 pour
    # une vue qui va jusqu'à 26) — la fixer explicitement sans ce filtre
    # réélargirait l'axe pour l'inclure.
    ax.set_yticks([t for t in ax.get_yticks() if 0 <= t <= y_max_actuel])
    # Petits traits intermédiaires entre deux graduations (ex: entre 1 et 2
    # min) sans chiffres, pour lire les valeurs plus finement sans alourdir
    # l'axe. Le locator automatique déborde sous 0 (la marge du bas n'a pas
    # de graduation majeure pour le contenir) — filtré explicitement pour
    # les mêmes raisons que les majeures.
    ax.yaxis.set_minor_locator(AutoMinorLocator(2))
    ax.set_yticks([t for t in ax.yaxis.get_minorticklocs() if 0 <= t <= y_max_actuel], minor=True)
    ax.tick_params(axis="y", which="minor", length=3)

    if marge_bas:
        # Petite marge en x aussi, pour que la première/dernière gare ne
        # soit pas collée au bord du graphique. Calculée à partir de
        # dataLim (l'étendue réelle des données tracées), pas de
        # get_xlim() : le Graphique appelle cette fonction successivement
        # sur ax puis ax2, qui partagent leur axe X (sharex) — lire
        # get_xlim() au 2e appel aurait relu la plage déjà élargie par le
        # 1er, doublant la marge (et créant un grand vide au début du
        # graphique qui ressemblait à tort à un trou de collecte, voir
        # mémoire du projet, 2026-07-24). dataLim n'est pas affecté par
        # set_xlim(), donc stable peu importe l'ordre ou le nombre d'appels.
        x_min, x_max = ax.dataLim.intervalx
        marge_x = max((x_max - x_min) * 0.02, marge_x_min)
        ax.set_xlim(x_min - marge_x, x_max + marge_x)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
