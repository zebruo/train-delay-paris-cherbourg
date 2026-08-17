// Rendu Plotly.js de l'onglet Rapports — équivalent JS de generer_rapport.py
// (matplotlib). Les données (déjà calculées côté serveur, voir
// calculer_contexte_rapport_pour_affichage/calculer_contexte_rapport_sql,
// app_fastapi.py) ne font ici que devenir des traces Plotly. Les 2
// graphiques par catégorie (retard moyen par gare/jour de semaine)
// réutilisent dessinerBarre/CONFIG_JOUR_HEURE (jour_heure.js, chargé avant
// ce fichier) — seuls les 2 graphiques par jour calendaire (% perturbées,
// cumulé) et le Top 5 (mini-graphiques "dernier relevé", propres à cet
// onglet — PAS l'Escalier de train.js) sont définis ici.

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

// Couleur "récent" du rapport PDF (couleur_recent, generer_rapport.py) —
// un seul ton pour tout le tracé du Top 5, contrairement à l'Escalier de
// Suivi d'un train (COULEUR_FIGE bleu/COULEUR_PAS_FIGE orange, train.js) :
// demande explicite de l'utilisateur, 2026-08-17, pour que le Top 5 web
// ressemble au PDF plutôt qu'à Suivi d'un train sur ce point précis. Le
// style plein/pointillé (figé/pas encore atteint) reste différencié, lui,
// exactement comme le PDF (style = "-" if figes[k] else (0, (4, 2))).
const COULEUR_TOP5 = "#f2a53d";

function tracesTop5(donnees) {
    // Pas de shape:"hv" (palier, hérité de l'Escalier de train.js) : le
    // dernier relevé (calculer_dernier_releve, app_fastapi.py) donne de
    // vraies diagonales entre gares réellement observées à CE relevé, pas
    // une valeur tenue jusqu'à la gare suivante — "linear" (défaut Plotly)
    // relie directement les points consécutifs, comme ax_g.plot(...) côté
    // PDF. Demande explicite de l'utilisateur, 2026-08-17.
    const traces = donnees.runs.map((run) => ({
        x: run.x, y: run.y, type: "scatter", mode: "lines",
        line: { color: COULEUR_TOP5, width: 1.8, dash: run.fige ? "solid" : "dash" },
        showlegend: false, hoverinfo: "skip",
    }));
    traces.push({
        x: donnees.points.map((p) => p.x), y: donnees.points.map((p) => p.y),
        type: "scatter", mode: "markers",
        marker: { size: 7, color: COULEUR_TOP5 },
        showlegend: false, hovertemplate: "%{y:.1f} min<extra></extra>",
    });
    return traces;
}

// Top 5 : mini-graphique "dernier relevé" par circulation, un <div> par
// carte (voir _rapports.html, id="top5-plot-{i}") — porte calculer_
// dernier_releve (app_fastapi.py), même forme "runs"/"points" que
// l'Escalier de Suivi d'un train (segments_par_etat/_runs_json communs
// aux deux) mais un contenu différent : uniquement le tout dernier relevé
// de la circulation, tracé en un seul ton (tracesTop5 ci-dessus), avec un
// layout bien plus compact (pas de légende, titre, axes allégés) — ce
// sont 5 petits graphiques d'aperçu, pas la vue détaillée.
function dessinerTop5(indice, donnees) {
    window._donneesRapportTop5 = window._donneesRapportTop5 || {};
    window._donneesRapportTop5[indice] = donnees;

    const traces = tracesTop5(donnees);
    const maxY = Math.max(0, ...traces.flatMap((t) => (t.y || []).filter((v) => typeof v === "number")));
    const bordure = couleurTheme("--bordure");

    const layout = {
        xaxis: {
            tickmode: "array",
            tickvals: donnees.labels.map((_, i) => i),
            ticktext: donnees.labels,
            tickangle: -45,
            // automargin : les noms de gare complets ("Trouville - Deauville"),
            // une fois en taille par défaut (retiré du tickfont:8 réduit
            // précédent, pour matcher Suivi d'un train), débordaient de la
            // marge fixe d'origine et se faisaient tronquer par Plotly (repéré
            // par l'utilisateur, capture d'écran, 2026-08-17) — laisse Plotly
            // calculer lui-même la marge nécessaire plutôt qu'une valeur fixe
            // à deviner.
            automargin: true,
            zeroline: false, showline: true, linecolor: bordure, gridcolor: bordure,
        },
        yaxis: {
            // calculerPlageY (train.js, chargé avant ce fichier) plutôt que
            // rangemode:"tozero" seul : un point à 0 min pile contre le bord
            // bas du cadre se faisait couper à moitié par Plotly (repéré par
            // l'utilisateur, capture d'écran, 2026-08-17) — calculerPlageY
            // ajoute la même petite marge négative que Suivi d'un train pour
            // laisser la place au marqueur.
            range: calculerPlageY(maxY), automargin: true,
            zeroline: false, showline: true, linecolor: bordure, gridcolor: bordure,
        },
        // Repère à 0 en pointillé gris, comme Suivi d'un train (train.js) —
        // remplace le zeroline natif de Plotly (désactivé ci-dessus, ne
        // suit pas le thème et ne propose pas de style pointillé).
        shapes: [
            { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 0, y1: 0,
              line: { color: "gray", width: 0.8, dash: "dot" }, layer: "below" },
        ],
        margin: { t: 8, r: 20, b: 90, l: 55 },
        showlegend: false,
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        font: { color: couleurTheme("--texte") },
    };

    requestAnimationFrame(() => {
        Plotly.newPlot(
            "top5-plot-" + indice, traces, layout,
            { responsive: true, displaylogo: false, displayModeBar: false, showTips: false },
        ).then(() => {
            // griserGaresHorsLigne (train.js) : pas de "plotly_afterplot" ici
            // contrairement à Suivi d'un train — ce mini-graphique n'a pas de
            // légende cliquable pouvant redessiner l'axe X et effacer ce
            // style, un seul appel après le tracé initial suffit.
            griserGaresHorsLigne("top5-plot-" + indice, donnees.hors_ligne);
        });
    });
}