// Helpers partagés par graphique.js/train.js/jour_heure.js — étaient
// dupliqués à l'identique dans chacun (les 3 scripts partagent le même
// scope global, chargés ensemble sur toutes les pages, voir base.html) ;
// regroupés ici et chargé en premier, plutôt que triplés (audit
// static/+templates/, 2026-08-10).

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

// pasYGraphique/pasYJourHeure (audit .js, 2026-08-31) : mêmes paliers,
// dupliqués à l'identique dans graphique.js et jour_heure.js — regroupés
// ici pour la même raison que couleurTheme ci-dessus. Paliers plus fins
// que train.js (0.5/1/2 min) : "retard moyen" est une moyenne, pas un
// retard brut — pas restreinte aux multiples de 5 min comme les valeurs de
// "Suivi d'un train" (voir mémoire du projet), une valeur courante genre
// 2-3 min n'aurait sinon qu'une seule graduation visible ("0") avec un pas
// de 5.
function pasYAxe(maxY) {
    return maxY <= 2 ? 0.5 : maxY <= 5 ? 1 : maxY <= 10 ? 2 : maxY <= 20 ? 5 :
        maxY <= 50 ? 10 : maxY <= 100 ? 20 : maxY <= 200 ? 50 : 100;
}

function calculerTicksYAxe(maxY) {
    const pas = pasYAxe(maxY);
    const vals = [];
    for (let v = 0; v <= maxY + pas; v += pas) vals.push(Math.round(v * 100) / 100);
    return vals;
}
