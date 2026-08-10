// Rendu Plotly.js de l'onglet Suivi d'un train — équivalent JS de
// _render_train_escalier/_render_train_detail (viewer.py, matplotlib). Les
// données (segments figé/pas-figé, couleurs) sont calculées côté serveur
// (voir calculer_escalier/calculer_detail, app_fastapi.py) ; ce fichier ne
// fait que les transformer en traces Plotly.

const COULEUR_FIGE = "#2c6ea5";
const COULEUR_PAS_FIGE = "#f2a53d";

function traceRun(run, couleur) {
    return {
        x: run.x, y: run.y, type: "scatter", mode: "lines",
        line: { color: couleur, width: 1.8, dash: run.fige ? "solid" : "dash", shape: "hv" },
        showlegend: false, hoverinfo: "skip",
    };
}

function dessinerEscalier(donnees) {
    const traces = donnees.runs.map((r) => traceRun(r, r.fige ? COULEUR_FIGE : COULEUR_PAS_FIGE));
    traces.push({
        x: donnees.points.map((p) => p.x), y: donnees.points.map((p) => p.y),
        type: "scatter", mode: "markers",
        marker: { size: 7, color: donnees.points.map((p) => (p.fige ? COULEUR_FIGE : COULEUR_PAS_FIGE)) },
        showlegend: false, hovertemplate: "%{y:.1f} min<extra></extra>",
    });
    // Légende manuelle (2 entrées fixes, comme _render_train_escalier) : une
    // trace fantôme par entrée, sans point réel affiché sur le graphique.
    traces.push({
        x: [null], y: [null], type: "scatter", mode: "lines",
        line: { color: COULEUR_FIGE, width: 1.8 }, name: "Gare déjà passée (figé)",
    });
    traces.push({
        x: [null], y: [null], type: "scatter", mode: "lines",
        line: { color: COULEUR_PAS_FIGE, width: 1.8, dash: "dash" },
        name: "Gare pas encore atteinte (peut encore changer)",
    });
    return traces;
}

// Vue Détail : contrairement à l'Escalier, la ligne reliant deux gares est
// un vrai segment diagonal (le relevé fige une prévision instantanée pour
// tout le trajet, pas une valeur "tenue" jusqu'à la gare suivante) — donc
// pas de line_shape "hv" ici, une ligne droite classique suffit.
function traceRunDetail(run, couleur, legendgroup, showlegend, poll_time) {
    return {
        x: run.x, y: run.y, type: "scatter", mode: "lines+markers",
        line: { color: couleur, width: 1.3, dash: run.fige ? "solid" : "dash" },
        marker: { size: 4, color: couleur },
        legendgroup: legendgroup, showlegend: showlegend, name: poll_time,
        hovertemplate: "%{y:.1f} min<extra>" + poll_time + "</extra>",
    };
}

function dessinerDetail(donnees) {
    const traces = [];
    donnees.releves.forEach((releve, i) => {
        const legendgroup = "releve-" + i;
        releve.runs.forEach((run, j) => {
            traces.push(traceRunDetail(run, releve.couleur, legendgroup, j === 0, releve.poll_time));
        });
    });
    return traces;
}

function maxYDesTraces(traces) {
    let maxY = 0;
    traces.forEach((t) => {
        (t.y || []).forEach((v) => {
            if (typeof v === "number" && v > maxY) maxY = v;
        });
    });
    return maxY;
}

// Même marge que _finalize_axes côté viewer.py (matplotlib, graphiques.py:
// 151-154) : 2% de l'écart, avec un plancher (0.75 en bas, 1 en haut) pour
// qu'un petit retard max n'écrase pas complètement la marge — un plancher
// de 5 min (essayé d'abord) était bien plus généreux que viewer.py et
// donnait un écart disproportionné pour un trajet à faible retard (repéré
// par l'utilisateur, 2026-08-06).
function calculerPlageY(maxY) {
    if (maxY < 1) {
        // Trajet plat (retard nul partout) : plage fixe resserrée, même cas
        // "degenere" que viewer.py — un pourcentage de presque-zéro donnerait
        // une marge quasi nulle plutôt qu'une plage lisible.
        return [-0.1, 1];
    }
    const margeBas = Math.max(maxY * 0.02, 0.75);
    const margeHaut = Math.max(maxY * 0.02, 1);
    return [-margeBas, maxY + margeHaut];
}

// Un retard n'est jamais négatif : contrairement à viewer.py (qui filtre
// after-coup les ticks Y générés par matplotlib pour ne garder que >= 0),
// Plotly ne propose pas ce filtre — ses graduations automatiques auraient
// sinon affiché un "-5" en bas, dans la marge ajoutée par calculerPlageY.
// Généré à la main plutôt que filtré, pour ne jamais dépendre du pas que
// Plotly aurait choisi automatiquement.
function calculerTicksY(maxY) {
    const pas = maxY <= 20 ? 5 : maxY <= 50 ? 10 : maxY <= 100 ? 20 : maxY <= 200 ? 50 : 100;
    const vals = [];
    for (let v = 0; v <= maxY + pas; v += pas) vals.push(v);
    return vals;
}

// couleurTheme/couleurThemeAlpha : voir theme_commun.js (chargé avant ce
// fichier, base.html).

function dessinerTrain(donnees) {
    // Mémorisé pour pouvoir redessiner avec les couleurs à jour au bascule
    // clair/sombre (basculerTheme, base.html) — voir le commentaire
    // équivalent dans graphique.js.
    window._donneesTrain = donnees;
    const traces = donnees.vue === "Escalier" ? dessinerEscalier(donnees) : dessinerDetail(donnees);
    const maxY = maxYDesTraces(traces);
    const bordure = couleurTheme("--bordure");

    const layout = {
        xaxis: {
            tickmode: "array",
            tickvals: donnees.labels.map((_, i) => i),
            ticktext: donnees.labels,
            tickangle: -45,
            // Plotly trace par défaut une "zeroline" verticale à x=0 (pensée
            // pour un axe numérique) — ici la première gare (Caen) est
            // justement à x=0, donc cette ligne noire tombait pile dessus et
            // se confondait avec le trait bleu au point où ils se croisent.
            // Un axe de gares n'a pas de "zéro" qui ait un sens, désactivée —
            // remplacée ci-dessous par une vraie bordure d'axe (showline),
            // qui elle reste à sa place (le bord du graphique) plutôt que de
            // suivre la position d'une gare.
            zeroline: false,
            showline: true, linecolor: bordure,
            // Couleur du quadrillage liée au thème : la couleur par défaut
            // de Plotly (claire, pensée pour un fond blanc) était bien trop
            // contrastée en thème sombre (repéré par l'utilisateur,
            // 2026-08-06).
            gridcolor: bordure,
        },
        yaxis: {
            title: { text: "Retard (min)" }, range: calculerPlageY(maxY),
            tickmode: "array", tickvals: calculerTicksY(maxY),
            // zeroline (Plotly) ne propose pas de style pointillé — remplacée
            // par une "shape" pour reproduire l'axhline(0, ..., linestyle=
            // (0,(4,3))) de viewer.py (repère discret en arrière-plan, pas
            // une vraie donnée).
            zeroline: false,
            showline: true, linecolor: bordure,
            gridcolor: bordure,
        },
        shapes: [
            {
                type: "line", xref: "paper", yref: "y",
                x0: 0, x1: 1, y0: 0, y1: 0,
                line: { color: "gray", width: 0.8, dash: "dot" },
                layer: "below",
            },
        ],
        // Titre au-dessus du graphique, légende en encart intérieur en haut
        // à gauche (empilée verticalement) — même disposition que
        // viewer.py (ax.legend(loc="upper left")), au lieu de superposer
        // les deux au-dessus du graphique.
        title: { text: donnees.titre, font: { size: 12 } },
        margin: { t: 50, r: 20, b: 120, l: 60 },
        showlegend: true,
        legend: {
            x: 0.01, y: 0.99, xanchor: "left", yanchor: "top",
            bgcolor: couleurThemeAlpha("--fond-secondaire", 0.5),
            bordercolor: bordure,
            borderwidth: 1,
            font: { color: couleurTheme("--texte"), size: 11 },
        },
        font: { color: couleurTheme("--texte") },
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
    };

    // requestAnimationFrame : même précaution que graphique.js (voir son
    // commentaire) — ce script s'exécute au moment de l'échange htmx, le
    // conteneur #train-plot n'a pas forcément terminé sa mise en page.
    requestAnimationFrame(() => {
        // displayModeBar: false — la barre d'outils zoom/pan/export de Plotly
        // apparaît au survol dans la même bande que la légende (y: 1.18,
        // au-dessus du graphique) et intercepte alors les clics destinés à
        // la légende (repéré en pratique, Playwright, 2026-08-06).
        // showTips: false — désactive l'info-bulle native de Plotly
        // ("double-click on legend to isolate one trace"), demande explicite
        // de l'utilisateur, 2026-08-09.
        Plotly.newPlot("train-plot", traces, layout, { responsive: true, displaylogo: false, displayModeBar: false, showTips: false })
            .then((gd) => {
                griserGaresHorsLigne(donnees.hors_ligne);
                // Un clic sur la légende (masquer/afficher un relevé) déclenche
                // en interne un restyle qui régénère les <text> de l'axe X,
                // effaçant le style posé une seule fois ci-dessus — repéré par
                // l'utilisateur, 2026-08-09 (toutes les gares redevenaient
                // noires après un clic légende). "plotly_afterplot" se
                // déclenche après CHAQUE redessin, peu importe la cause (clic
                // légende, zoom...), donc plus fiable qu'un événement ciblé sur
                // la légende spécifiquement.
                gd.on("plotly_afterplot", () => griserGaresHorsLigne(donnees.hors_ligne));
            });
    });
}

// Gares hors ligne Paris-Cherbourg (trains de jonction) grisées, comme
// viewer.py (tick.set_color("#999999")) — pas de mise en forme pseudo-HTML
// disponible sur ticktext dans ce bundle Plotly (testé, sans effet), donc
// stylé directement sur le SVG une fois le graphique posé.
function griserGaresHorsLigne(horsLigne) {
    if (!horsLigne) return;
    const ticks = document.querySelectorAll("#train-plot .xaxislayer-above .xtick text");
    ticks.forEach((tick, i) => {
        if (horsLigne[i]) tick.style.fill = "#999999";
    });
}
