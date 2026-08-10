// Helpers de thème partagés par graphique.js/train.js/jour_heure.js —
// étaient dupliqués à l'identique dans chacun (les 3 scripts partagent le
// même scope global, chargés ensemble sur toutes les pages, voir
// base.html) ; regroupés ici et chargé en premier, plutôt que triplés
// (audit static/+templates/, 2026-08-10).

function couleurTheme(nomVariable) {
    return getComputedStyle(document.documentElement).getPropertyValue(nomVariable).trim();
}

// Fond de légende semi-transparent (demande explicite de l'utilisateur,
// 2026-08-09) : couleurTheme() renvoie une couleur pleine (#rrggbb), pas
// directement utilisable comme bgcolor semi-transparent — reconvertie ici
// en rgba(). Pas utilisée par jour_heure.js (pas de légende sur ces
// graphiques), seulement par graphique.js/train.js.
function couleurThemeAlpha(nomVariable, alpha) {
    const hex = couleurTheme(nomVariable).replace("#", "");
    const r = parseInt(hex.substring(0, 2), 16);
    const g = parseInt(hex.substring(2, 4), 16);
    const b = parseInt(hex.substring(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
