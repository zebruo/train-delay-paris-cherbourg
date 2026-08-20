// Rendu Plotly.js de l'onglet Graphique — équivalent JS de graphiques.py
// (matplotlib), qui ne peut pas être réutilisé tel quel : ses fonctions
// manipulent directement un axe matplotlib. Les données (séries avec trous,
// point max, moyenne) sont calculées côté serveur (voir serie_avec_trous,
// app_fastapi.py) ; ce fichier ne fait que les transformer en traces Plotly.

function traceLigne(serie, couleur, axeX, axeY, nom) {
    return {
        x: serie.points.x, y: serie.points.y,
        type: "scatter", mode: "lines+markers",
        line: { color: couleur, width: 1 },
        marker: { size: 3 },
        name: nom,
        xaxis: axeX, yaxis: axeY,
        hovertemplate: "%{y}<br>%{x}<extra></extra>",
    };
}

// Trous de collecte affichés séparément, en pointillé gris — reproduit le
// double-tracé de tracer_serie_temporelle (graphiques.py) : un segment par
// trou détecté (écart > 10 min entre deux relevés), pas relié comme si
// c'était une vraie donnée à 0. avecLegende : une seule entrée "Absence de
// relevés" pour les deux sous-graphiques (même style dans les deux), pas
// une par sous-graphique — legendgroup partagé pour que le clic sur l'une
// masque les deux d'un coup (demande explicite de l'utilisateur, 2026-08-09).
function traceTrous(serie, axeX, axeY, avecLegende) {
    const x = [];
    const y = [];
    serie.trous.forEach((segment, i) => {
        if (i > 0) {
            x.push(null);
            y.push(null);
        }
        x.push(...segment.x);
        y.push(...segment.y);
    });
    return {
        x: x, y: y, type: "scatter", mode: "lines",
        line: { color: "#bbbbbb", width: 1, dash: "dash" },
        xaxis: axeX, yaxis: axeY,
        hoverinfo: "skip", legendgroup: "trous",
        showlegend: !!avecLegende, name: "Absence de relevés",
    };
}

function traceMax(serie, couleur, axeX, axeY) {
    if (!serie.max) return null;
    return {
        x: [serie.max.x], y: [serie.max.y],
        type: "scatter", mode: "markers",
        marker: { symbol: "star", size: 12, color: couleur, line: { color: "black", width: 0.5 } },
        xaxis: axeX, yaxis: axeY, showlegend: false,
        hovertemplate: serie.max.texte_repere + "<br>" + serie.max.texte_explication + "<extra></extra>",
        hoverlabel: { align: "left" },
    };
}

// Ligne pointillée de référence à la moyenne — en trace plutôt qu'en
// "shape" Plotly, uniquement pour bénéficier du survol (les shapes n'ont
// pas d'info-bulle).
function traceMoyenne(serie, couleur, axeX, axeY) {
    if (!serie.moyenne) return null;
    const xs = serie.points.x.filter((v) => v !== null);
    if (!xs.length) return null;
    return {
        x: [xs[0], xs[xs.length - 1]], y: [serie.moyenne.valeur, serie.moyenne.valeur],
        type: "scatter", mode: "lines",
        line: { color: couleur, width: 1, dash: "dot" },
        xaxis: axeX, yaxis: axeY, showlegend: false,
        hovertemplate: serie.moyenne.texte_repere + "<br>" + serie.moyenne.texte_explication + "<extra></extra>",
        hoverlabel: { align: "left" },
    };
}

// couleurTheme/couleurThemeAlpha : voir theme_commun.js (chargé avant ce
// fichier, base.html).

// Étiquette "moy. X min"/"moy. X %" collée à la ligne pointillée de
// moyenne, ancrée au bord droit du sous-graphique — reproduit
// marquer_moyenne (graphiques.py:95-105) : ax.annotate en xy=(1, moyenne),
// xycoords=("axes fraction", "data"), léger décalage (-4, 4) points, fond
// blanc semi-opaque pour rester lisible même par-dessus une donnée de même
// couleur. "{axe} domain" (Plotly) pour le X = équivalent de "axes
// fraction" (matplotlib) ; refY en coordonnée data pour le Y, comme
// xycoords le fait pour la partie "data".
function annotationMoyenne(serie, couleur, axeXDomain, refY) {
    if (!serie.moyenne) return null;
    return {
        text: serie.moyenne.texte_repere,
        xref: axeXDomain, x: 1, xanchor: "right", xshift: -4,
        yref: refY, y: serie.moyenne.valeur, yanchor: "bottom", yshift: 4,
        showarrow: false,
        font: { size: 10, color: couleur },
        bgcolor: couleurTheme("--fond-secondaire"),
        borderpad: 2,
    };
}

function maxYDeLaSerie(serie) {
    let maxY = 0;
    (serie.points.y || []).forEach((v) => {
        if (typeof v === "number" && v > maxY) maxY = v;
    });
    return maxY;
}

// Même logique que calculerPlageY/calculerTicksY (train.js) — reproduit
// finalize_axes(marge_bas=True) côté viewer.py (graphiques.py:151-154),
// appelé pour les deux sous-graphiques de l'onglet Graphique (self.ax et
// self.ax2), pas seulement Suivi d'un train. Nommées différemment de leurs
// équivalentes dans train.js : les deux scripts sont chargés ensemble sur
// toutes les pages (base.html) et partagent le même scope global — un même
// nom aurait fait gagner silencieusement la définition chargée en dernier
// (repéré en pratique, Playwright, 2026-08-06 : l'onglet Graphique utilisait
// les paliers de train.js, bien plus grossiers, sans aucune erreur JS).
function calculerPlageYGraphique(maxY) {
    if (maxY < 1) return [-0.1, 1];
    const margeBas = Math.max(maxY * 0.02, 0.75);
    const margeHaut = Math.max(maxY * 0.02, 1);
    return [-margeBas, maxY + margeHaut];
}

// Paliers plus fins que train.js (0.5/1/2 min) : "retard moyen" est une
// moyenne, pas un retard brut — pas restreinte aux multiples de 5 min comme
// les valeurs de "Suivi d'un train" (voir mémoire du projet), une valeur
// courante genre 2-3 min n'aurait sinon qu'une seule graduation visible
// ("0") avec un pas de 5.
function pasYGraphique(maxY) {
    return maxY <= 2 ? 0.5 : maxY <= 5 ? 1 : maxY <= 10 ? 2 : maxY <= 20 ? 5 :
        maxY <= 50 ? 10 : maxY <= 100 ? 20 : maxY <= 200 ? 50 : 100;
}

function calculerTicksYGraphique(maxY) {
    const pas = pasYGraphique(maxY);
    const vals = [];
    for (let v = 0; v <= maxY + pas; v += pas) vals.push(Math.round(v * 100) / 100);
    return vals;
}

function dessinerGraphique(donnees) {
    // Mémorisé pour pouvoir redessiner avec les couleurs à jour au bascule
    // clair/sombre (basculerTheme, base.html) — sans ça, gridcolor/bordure/
    // etc. (lus via couleurTheme() une seule fois, au tracé) restaient
    // figés sur l'ancien thème tant que l'onglet n'était pas rechargé
    // (repéré par l'utilisateur, 2026-08-06).
    window._donneesGraphique = donnees;
    const couleurs = { retard: "#1f77b4", pct: "#c2410c" };
    const noms = { retard: "Retard moyen (min)", pct: "% trains en retard" };
    const axes = { retard: { x: "x", y: "y" }, pct: { x: "x2", y: "y2" } };

    const traces = [];
    ["retard", "pct"].forEach((cle, i) => {
        const serie = donnees[cle];
        const { x: axeX, y: axeY } = axes[cle];
        traces.push(traceTrous(serie, axeX, axeY, i === 0));
        traces.push(traceLigne(serie, couleurs[cle], axeX, axeY, "Relevés — " + noms[cle]));
        const max = traceMax(serie, couleurs[cle], axeX, axeY);
        if (max) traces.push(max);
        const moyenne = traceMoyenne(serie, couleurs[cle], axeX, axeY);
        if (moyenne) traces.push(moyenne);
    });

    // Une graduation par heure plutôt que l'espacement automatique : reste
    // lisible sur 24h, deviendrait un magma de texte sur 3j/7j/tout
    // l'historique (même limitation que _render_chart, viewer.py).
    const formatHeure24h = donnees.periode === "dernières 24h";

    const maxYRetard = maxYDeLaSerie(donnees.retard);
    const maxYPct = maxYDeLaSerie(donnees.pct);
    const ticksYRetard = calculerTicksYGraphique(maxYRetard);
    const ticksYPct = calculerTicksYGraphique(maxYPct);
    const bordure = couleurTheme("--bordure");

    const layout = {
        // showline sur les deux xaxis (pas seulement xaxis2, le seul avec
        // des étiquettes) : "matches" ne synchronise que le domaine/la
        // plage, pas le style — sans ça, le sous-graphique du haut
        // (retard moyen) n'avait pas de bordure basse (repéré par
        // l'utilisateur, 2026-08-06).
        // gridcolor : couleur par défaut de Plotly bien trop contrastée en
        // thème sombre (repéré par l'utilisateur, 2026-08-06) — liée à
        // --bordure, comme showline juste à côté.
        xaxis: { anchor: "y", showticklabels: false, showline: true, linecolor: bordure, gridcolor: bordure },
        xaxis2: {
            anchor: "y2", matches: "x", type: "date", title: { text: "Heure du relevé" },
            tickformat: formatHeure24h ? "%Hh" : "%d/%m",
            showline: true, linecolor: bordure, gridcolor: bordure,
        },
        // ticks/minor : petits traits de graduation, comme
        // AutoMinorLocator(2) + tick_params(length=3) côté viewer.py
        // (finalize_axes, graphiques.py:175-177) — absents par défaut chez
        // Plotly (repéré par l'utilisateur, 2026-08-06).
        yaxis: {
            domain: [0.56, 1], title: { text: noms.retard },
            range: calculerPlageYGraphique(maxYRetard), tickmode: "array", tickvals: ticksYRetard,
            zeroline: false, showline: true, linecolor: bordure, gridcolor: bordure,
            ticks: "outside", ticklen: 4, tickcolor: bordure,
            // minor.tickvals (mode "array") ne produit aucun trait visible
            // chez Plotly (testé en isolant le cas, 2026-08-06) — seul
            // minor.dtick fonctionne, d'où le calcul par pas plutôt que par
            // liste explicite (contrairement aux graduations majeures,
            // volontairement en tableau pour éviter une graduation
            // négative — voir calculerPlageYGraphique/calculerTicksYGraphique
            // plus haut). Effet de bord mineur accepté : dtick génère aussi,
            // en toute rigueur, un trait secondaire (sans nombre) juste sous
            // 0 s'il tombe dans la petite marge basse — à peine visible et
            // sans commune mesure avec le vrai bug (une graduation NOMBRÉE
            // en négatif) déjà corrigé sur les majeures.
            minor: { dtick: pasYGraphique(maxYRetard) / 2, ticks: "outside", ticklen: 3, tickcolor: bordure },
        },
        yaxis2: {
            domain: [0, 0.42], title: { text: noms.pct },
            range: calculerPlageYGraphique(maxYPct), tickmode: "array", tickvals: ticksYPct,
            zeroline: false, showline: true, linecolor: bordure, gridcolor: bordure,
            ticks: "outside", ticklen: 4, tickcolor: bordure,
            minor: { dtick: pasYGraphique(maxYPct) / 2, ticks: "outside", ticklen: 3, tickcolor: bordure },
        },
        // Repère "0" pointillé sur les deux sous-graphiques, comme
        // tracer_serie_temporelle (graphiques.py:42) — zeroline (Plotly) ne
        // permet pas de pointillés, remplacée par une shape (même solution
        // que train.js).
        shapes: [
            { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 0, y1: 0,
              line: { color: "gray", width: 0.8, dash: "dot" }, layer: "below" },
            { type: "line", xref: "paper", yref: "y2", x0: 0, x1: 1, y0: 0, y1: 0,
              line: { color: "gray", width: 0.8, dash: "dot" }, layer: "below" },
        ],
        annotations: [
            { text: donnees.titre_haut, xref: "paper", yref: "paper", x: 0.5, y: 1.08, showarrow: false, font: { size: 13 } },
            { text: donnees.titre_bas, xref: "paper", yref: "paper", x: 0.5, y: 0.48, showarrow: false, font: { size: 11 } },
            annotationMoyenne(donnees.retard, couleurs.retard, "x domain", "y"),
            annotationMoyenne(donnees.pct, couleurs.pct, "x2 domain", "y2"),
        ].filter((a) => a !== null),
        margin: { t: 50, r: 20, b: 55, l: 60 },
        showlegend: true,
        // Encart intérieur en haut à gauche du sous-graphique du haut, même
        // disposition que l'onglet Suivi d'un train (voir train.js) plutôt
        // que la légende poussée au-dessus du graphique.
        legend: {
            x: 0.01, y: 0.99, xanchor: "left", yanchor: "top",
            bgcolor: couleurThemeAlpha("--fond-secondaire", 0.5),
            bordercolor: couleurTheme("--bordure"),
            borderwidth: 1,
            font: { color: couleurTheme("--texte"), size: 11 },
        },
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
    };

    // requestAnimationFrame plutôt qu'un appel direct : ce script s'exécute
    // au moment même où htmx insère le fragment dans le DOM (échange
    // #zone-contenu) — le conteneur #graphique-plot (hauteur en vh, voir
    // style.css) n'a pas forcément terminé sa mise en page au même tick.
    // Appeler Plotly.newPlot sur un conteneur dont la taille n'est pas
    // encore stabilisée produit un rendu cassé : le calque légende/
    // annotations (.infolayer, son propre <svg>) se retrouve positionné
    // ~700px sous le graphique principal au lieu d'être superposé dessus
    // (repéré en pratique, Playwright, 2026-08-05).
    requestAnimationFrame(() => {
        // showTips: false — désactive l'info-bulle native de Plotly
        // ("double-click on legend to isolate one trace"), demande explicite
        // de l'utilisateur, 2026-08-09.
        Plotly.newPlot("graphique-plot", traces, layout, { responsive: true, displaylogo: false, showTips: false });
    });
}
