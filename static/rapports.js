// Rendu Plotly.js de l'onglet Rapports — équivalent JS de generer_rapport.py
// (matplotlib). Les données (déjà calculées côté serveur, voir
// calculer_contexte_rapport_pour_affichage/calculer_contexte_rapport_sql,
// app_fastapi.py) ne font ici que devenir des traces Plotly. Les 2
// graphiques par catégorie (retard moyen par gare/jour de semaine)
// réutilisent dessinerBarre/CONFIG_JOUR_HEURE (jour_heure.js, chargé avant
// ce fichier) — seuls les 2 graphiques par jour calendaire (% perturbées,
// cumulé) et le Top 5 (mini-escaliers, réutilisant dessinerEscalier de
// train.js) sont propres à cet onglet.

// couleurTheme : voir theme_commun.js (chargé avant ce fichier, base.html).

// Un seul axe, pas de dual-axis ni de gestion des trous (contrairement à
// graphique.js) : un jour calendaire sans donnée devient un point `null`
// dans "y" (voir _pct_et_cumule_par_jour_sql, app_fastapi.py — reindex sur
// tous les jours de la période), Plotly coupe naturellement la ligne à cet
// endroit sans qu'il soit nécessaire de tracer un segment pointillé séparé
// comme pour les vrais trous de collecte du Graphique.
function traceJournaliere(donnees, couleur, couleurRemplissage) {
    const trace = {
        x: donnees.x, y: donnees.y, type: "scatter", mode: "lines+markers",
        line: { color: couleur, width: 1.6 },
        marker: { size: 4 },
        connectgaps: false,
        hovertemplate: "%{y}<br>%{x|%d/%m/%Y}<extra></extra>",
        showlegend: false,
    };
    if (couleurRemplissage) {
        trace.fill = "tozeroy";
        trace.fillcolor = couleurRemplissage;
    }
    return trace;
}

function calculerTicksYRapport(maxY, plancherPas) {
    const pas = Math.max(plancherPas, Math.ceil(maxY / 6));
    const vals = [];
    for (let v = 0; v <= maxY + pas; v += pas) vals.push(v);
    return vals;
}

function layoutBaseRapport(titre, ylabel, maxY, plancherPas) {
    const bordure = couleurTheme("--bordure");
    return {
        title: { text: titre, font: { size: 11 } },
        xaxis: {
            type: "date", tickformat: "%d/%m",
            showline: true, linecolor: bordure, gridcolor: bordure,
        },
        yaxis: {
            title: { text: ylabel }, rangemode: "tozero",
            tickmode: "array", tickvals: calculerTicksYRapport(maxY, plancherPas),
            showline: true, linecolor: bordure, gridcolor: bordure,
            ticks: "outside", ticklen: 4, tickcolor: bordure,
        },
        margin: { t: 35, r: 15, b: 35, l: 45 },
        showlegend: false,
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        font: { color: couleurTheme("--texte") },
    };
}

function dessinerPctParJour(donnees) {
    window._donneesRapportPctJour = donnees;
    const maxY = Math.max(0, ...donnees.y.filter((v) => typeof v === "number"));
    const layout = layoutBaseRapport("% de circulations perturbées, jour par jour", "% perturbées", maxY, 5);
    requestAnimationFrame(() => {
        Plotly.newPlot(
            "rapport-pct-jour", [traceJournaliere(donnees, "#c2410c", null)], layout,
            { responsive: true, displaylogo: false, displayModeBar: false, showTips: false },
        );
    });
}

function dessinerCumuleJour(donnees) {
    window._donneesRapportCumuleJour = donnees;
    const maxY = Math.max(0, ...donnees.y.filter((v) => typeof v === "number"));
    const layout = layoutBaseRapport("Retard cumulé sur la période (croissant)", "Heures cumulées", maxY, 1);
    requestAnimationFrame(() => {
        Plotly.newPlot(
            "rapport-cumule-jour", [traceJournaliere(donnees, "#2c6ea5", "rgba(44,110,165,0.08)")], layout,
            { responsive: true, displaylogo: false, displayModeBar: false, showTips: false },
        );
    });
}

// Top 5 : mini-graphique "escalier" par circulation, un <div> par carte
// (voir _rapports.html, id="top5-plot-{i}") — réutilise dessinerEscalier
// (train.js) pour les traces (même logique figé/prédiction que Suivi d'un
// train), mais un layout bien plus compact (pas de légende, titre, axes
// allégés) : ce sont 5 petits graphiques d'aperçu, pas la vue détaillée.
function dessinerTop5(indice, donnees) {
    window._donneesRapportTop5 = window._donneesRapportTop5 || {};
    window._donneesRapportTop5[indice] = donnees;

    const traces = dessinerEscalier(donnees).filter((t) => t.name === undefined || !t.name.startsWith("Gare "));
    const maxY = Math.max(0, ...traces.flatMap((t) => (t.y || []).filter((v) => typeof v === "number")));
    const bordure = couleurTheme("--bordure");

    const layout = {
        xaxis: {
            tickmode: "array",
            tickvals: donnees.labels.map((_, i) => i),
            ticktext: donnees.labels,
            tickangle: -45, tickfont: { size: 8 },
            zeroline: false, showline: true, linecolor: bordure, gridcolor: bordure,
        },
        yaxis: {
            rangemode: "tozero", tickfont: { size: 8 },
            zeroline: false, showline: true, linecolor: bordure, gridcolor: bordure,
        },
        margin: { t: 8, r: 8, b: 60, l: 35 },
        showlegend: false,
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        font: { color: couleurTheme("--texte") },
    };

    requestAnimationFrame(() => {
        Plotly.newPlot(
            "top5-plot-" + indice, traces, layout,
            { responsive: true, displaylogo: false, displayModeBar: false, showTips: false },
        );
    });
}