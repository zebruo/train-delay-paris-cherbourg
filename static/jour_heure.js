// Rendu Plotly.js de l'onglet Par jour / heure — équivalent JS de
// _tracer_barres_fiabilite/_render_jour_heure_tab (viewer.py, matplotlib).
// Contrairement à Graphique/Suivi d'un train, ce sont 6 graphiques Plotly
// totalement indépendants (un conteneur <div> chacun, voir
// _jour_heure.html) plutôt qu'un montage à domaines multiples dans une
// seule figure : chacun a son propre axe catégoriel (jours, heures, ou 2
// catégories), pas d'axe commun à synchroniser entre eux.

// couleurTheme : voir theme_commun.js (chargé avant ce fichier, base.html).

// Paliers fins (0.5/1/2 min...), comme graphique.js — "retard moyen" est
// une moyenne continue, pas un retard brut multiple de 5 (voir mémoire du
// projet). Noms distincts de graphique.js/train.js : ces trois scripts
// sont chargés ensemble sur toutes les pages (base.html) et partagent le
// même scope global — un même nom ferait gagner silencieusement la
// définition chargée en dernier, sans aucune erreur JS (repéré en
// pratique le 2026-08-06 sur l'onglet Graphique).
function pasYJourHeure(maxY) {
    return maxY <= 2 ? 0.5 : maxY <= 5 ? 1 : maxY <= 10 ? 2 : maxY <= 20 ? 5 :
        maxY <= 50 ? 10 : maxY <= 100 ? 20 : maxY <= 200 ? 50 : 100;
}

function calculerTicksYJourHeure(maxY) {
    const pas = pasYJourHeure(maxY);
    const vals = [];
    for (let v = 0; v <= maxY + pas; v += pas) vals.push(Math.round(v * 100) / 100);
    return vals;
}

// Pas de marge négative sous 0 ici (contrairement à calculerPlageY côté
// train.js/graphique.js) : ces barres partent toujours exactement de 0
// (finalize_axes appelé SANS marge_bas=True pour _tracer_barres_fiabilite,
// viewer.py:1920) — rangemode:"tozero" suffit, sans risque de graduation
// négative puisque la plage ne descend jamais sous 0.
function texteBarre(barre, unite) {
    if (barre.valeur === null) return barre.label + " : aucun relevé";
    return barre.label + " : " + barre.valeur + unite + " (n=" + barre.n + " relevés)";
}

const CONFIG_JOUR_HEURE = {
    jour_moyenne: { titre: "Retard moyen par jour", ylabel: "Retard (min)", couleur: "#4a7fb5" },
    jour_pct: { titre: "% en retard par jour", ylabel: "% trains en retard", couleur: "#c2410c" },
    heure_moyenne: { titre: "Retard moyen par heure", ylabel: "Retard (min)", couleur: "#5ba58c" },
    heure_pct: {
        titre: "% en retard par heure", ylabel: "% trains en retard", couleur: "#c2410c",
        xlabel: "Heure (locale)",
    },
    type_jour: { titre: "Jour ouvré vs Weekend/Férié", ylabel: "Retard (min)", couleur: "#8a5cb5" },
    vacances: { titre: "Vacances vs Hors vacances", ylabel: "Retard (min)", couleur: "#b58a2c" },
    // Onglet Rapports (mensuel uniquement, rapports.js) — pas dans
    // dessinerJourHeure (boucle sur les 6 clés ci-dessus seulement) :
    // dessinerBarre appelée directement par _rapports.html pour ces 2-là.
    rapport_gare: {
        titre: "Retard moyen par gare, sur le mois", ylabel: "Retard (min)", couleur: "#8a5cb5",
        tickangle: -30,
    },
    rapport_jour_semaine: {
        titre: "Retard moyen par jour de semaine, sur le mois", ylabel: "Retard (min)", couleur: "#5ba58c",
    },
};

function dessinerBarre(cle, donnees) {
    const config = CONFIG_JOUR_HEURE[cle];
    const barres = donnees.barres;
    const maxY = Math.max(0, ...barres.map((b) => b.valeur || 0));
    const bordure = couleurTheme("--bordure");
    const texte = couleurTheme("--texte");

    const trace = {
        x: barres.map((b) => b.label),
        y: barres.map((b) => b.valeur),
        type: "bar",
        marker: {
            // Barres peu fiables (n < SEUIL_FIABLE) grisées et hachurées,
            // comme _tracer_barres_fiabilite (viewer.py:1903-1908) — une
            // seule circulation matinale peut donner un % en retard de 0
            // ou 100 %, pas représentatif.
            color: barres.map((b) => (b.fiable ? config.couleur : "#dddddd")),
            pattern: { shape: barres.map((b) => (b.fiable ? "" : "/")), fgcolor: "#999999", size: 6, solidity: 0.3 },
        },
        text: barres.map((b) => texteBarre(b, donnees.unite)),
        // "none" : text n'est utilisé que pour l'info-bulle (%{text} dans
        // hovertemplate) — sans ça, Plotly affiche ce texte en permanence
        // à l'intérieur de chaque barre par défaut pour un trace "bar".
        textposition: "none",
        hovertemplate: "%{text}<extra></extra>",
    };

    const labels = barres.map((b) => b.label);
    const layout = {
        // donnees.titre : titre dynamique calculé côté serveur (catégorie au
        // maximum, ex: "Retard moyen par jour — max : mardi (2.5 min)") pour
        // jour_moyenne/jour_pct/heure_moyenne/heure_pct — absent pour
        // type_jour/vacances (2 barres seulement, pas de "max" utile), on
        // retombe alors sur le titre statique de CONFIG_JOUR_HEURE.
        title: { text: donnees.titre || config.titre, font: { size: 11 } },
        xaxis: {
            showline: true, linecolor: bordure,
            // Plotly masque une partie des étiquettes par défaut sur un axe
            // catégoriel dense (24 heures) pour éviter le chevauchement —
            // viewer.py (matplotlib) les affiche toutes ; forcé en tableau
            // explicite pour la même échelle complète "0h" à "23h".
            tickmode: "array", tickvals: labels, ticktext: labels,
            // tickangle par graphique (0 par défaut, comme jour/heure/type_
            // jour/vacances, catégories courtes) : "gare" (Rapports, noms
            // longs type "Paris Saint-Lazare") en horizontal se chevauchait
            // entre étiquettes voisines — repéré en testant l'onglet
            // Rapports en direct (Playwright), 2026-08-17.
            tickangle: config.tickangle || 0,
            // automargin : "Paris Saint-Lazare" (rapport_gare, tickangle -30)
            // débordait de la marge fixe (margin.l, plus bas) et se faisait
            // tronquer à gauche — repéré par l'utilisateur sur capture
            // d'écran, 2026-08-18. Même correctif déjà appliqué au Top 5
            // (dessinerTop5, rapports.js) pour le même type de débordement.
            automargin: true,
        },
        yaxis: {
            title: { text: config.ylabel }, rangemode: "tozero",
            tickmode: "array", tickvals: calculerTicksYJourHeure(maxY),
            showline: true, linecolor: bordure, gridcolor: bordure,
            ticks: "outside", ticklen: 4, tickcolor: bordure,
            minor: { dtick: pasYJourHeure(maxY) / 2, ticks: "outside", ticklen: 3, tickcolor: bordure },
        },
        // Repère à 0, plein (pas pointillé) : ax.axhline(0, color="gray",
        // linewidth=0.6) côté viewer.py (_tracer_barres_fiabilite:1919) —
        // différent du pointillé (0,(4,3)) des autres onglets, les barres
        // partent déjà de 0 donc ce n'est qu'un simple repère de fond.
        shapes: [
            { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 0, y1: 0,
              line: { color: "gray", width: 0.6 }, layer: "below" },
        ],
        margin: {
            t: 35, r: 15, l: 45,
            b: (labels.length > 12 || config.tickangle ? 55 : 30) + (config.xlabel ? 15 : 0),
        },
        showlegend: false,
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        // size: 10 — légendes/graduations trop grandes par défaut (repéré
        // par l'utilisateur, 2026-08-18, sur les graphiques mensuels de
        // l'onglet Rapports, qui réutilisent dessinerBarre pour "retard
        // moyen par gare"/"par jour de semaine") ; même valeur appliquée à
        // l'onglet Par jour/heure, qui appelle aussi cette fonction pour
        // ses 6 graphiques, pour rester cohérent entre les deux.
        font: { size: 10, color: texte },
    };
    if (config.xlabel) {
        layout.xaxis.title = { text: config.xlabel };
    }
    // Ligne pointillée de référence à la moyenne + étiquette "moy. X" au
    // bord droit — porte marquer_moyenne (graphiques.py:81-105) : ax.axhline
    // + ax.annotate. Oublié une première fois (seule l'étiquette avait été
    // ajoutée, pas la ligne elle-même — repéré par l'utilisateur, 2026-08-06).
    if (donnees.moyenne) {
        layout.shapes.push({
            type: "line", xref: "paper", yref: "y", x0: 0, x1: 1,
            y0: donnees.moyenne.valeur, y1: donnees.moyenne.valeur,
            line: { color: config.couleur, width: 1, dash: "dash" }, opacity: 0.6, layer: "below",
        });
        layout.annotations = [{
            text: donnees.moyenne.texte,
            xref: "x domain", x: 1, xanchor: "right", xshift: -4,
            yref: "y", y: donnees.moyenne.valeur, yanchor: "bottom", yshift: 4,
            showarrow: false, font: { size: 9, color: config.couleur },
            bgcolor: couleurTheme("--fond-secondaire"), borderpad: 2,
        }];
    }

    requestAnimationFrame(() => {
        // showTips: false — désactive l'info-bulle native de Plotly
        // ("double-click on legend to isolate one trace"), demande explicite
        // de l'utilisateur, 2026-08-09.
        // displayModeBar: false — sur cette grille de 6 petits graphiques,
        // la barre d'outils (coin haut-droit, apparaît au survol) recouvre
        // le titre de chaque graphique, trop peu de marge disponible en
        // haut sur un format aussi compact (repéré en testant l'activation
        // partout, 2026-08-18 — contrairement à Suivi d'un train, un seul
        // grand graphique où ça ne pose pas ce problème).
        Plotly.newPlot("jh-" + cle, [trace], layout, { responsive: true, displaylogo: false, displayModeBar: false, showTips: false });
    });
}

function dessinerJourHeure(donnees) {
    // Mémorisé pour pouvoir redessiner avec les couleurs à jour au bascule
    // clair/sombre (basculerTheme, base.html) — voir le commentaire
    // équivalent dans graphique.js/train.js.
    window._donneesJourHeure = donnees;
    Object.keys(CONFIG_JOUR_HEURE).forEach((cle) => dessinerBarre(cle, donnees[cle]));
}
