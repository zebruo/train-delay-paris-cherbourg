// Bascule thème clair/sombre — même clé localStorage "theme" que
// basculerTheme() côté desktop (base.html), pour rester cohérent si
// l'utilisateur visite les deux versions sur le même navigateur. Pas de
// bascule visuelle des icônes à faire ici : gérée en CSS pur via
// [data-theme] (voir mobile.css), déjà à jour au prochain repaint.
function basculerThemeMobile() {
    var actuel = document.documentElement.dataset.theme || "dark";
    var nouveau = actuel === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = nouveau;
    localStorage.setItem("theme", nouveau);
}

// Bascule d'onglet mobile — même principe que activerOnglet (base.html,
// desktop), en plus simple (pas de filtres partagés à masquer/afficher).
function activerOngletMobile(bouton, nom) {
    document.getElementById("mobile-ecran-actif").value = nom;
    document.querySelectorAll(".mobile-onglet").forEach((b) => b.classList.remove("actif"));
    bouton.classList.add("actif");
}

// Formulaire "Quel train prenez-vous habituellement ?" (_mobile_choix_train.html,
// écrans "Mon train"/Favoris) — un changement de Gare de départ invalide Jour/
// Heure/le train déjà résolu (choisis pour l'ancien trajet), sans qu'aucun
// événement "change" ne se déclenche dessus tout seul (htmx ne fait que
// remplacer les <option> de la Gare d'arrivée). Repéré en usage réel
// (2026-08-26) : changer de gare d'arrivée ou de départ après avoir déjà
// résolu un train laissait l'ancienne liste d'heures, et l'ancienne carte
// affichée, sans lien avec le nouveau trajet en cours de saisie.
function reinitialiserApresGareDepart(mode) {
    const jour = document.getElementById("mobile-choix-jour-" + mode);
    const heure = document.getElementById("mobile-choix-heure-" + mode);
    if (jour) jour.value = "";
    if (heure) heure.innerHTML = '<option value="" selected disabled hidden>Choisissez…</option>';
    viderCandidatsTrain(mode);
}

function viderCandidatsTrain(mode) {
    const candidats = document.getElementById("mobile-candidats-" + mode);
    if (candidats) candidats.innerHTML = "";
}

// Même placeholder que rafraichirFavoris (ci-dessous) : affiché immédiatement
// au clic sur un candidat, avant que la réponse de /mobile/carte_train
// n'arrive (htmx ne touche pas au contenu de la cible tant que la requête est
// en cours — sans ce placeholder, l'écran restait vide pendant le chargement).
function afficherChargementCarte(id) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = '<div class="mobile-carte"><p class="mobile-info">Chargement…</p></div>';
}

// Favoris (écran Favoris) — stockage 100% côté client (localStorage), pas de
// compte utilisateur dans l'appli (voir plan d'implémentation). Un favori est
// {gare, heure, train, train_affiche, destination} ; les stats affichées sont
// récupérées en direct via /mobile/carte_train (même route que "Mon train"),
// rien n'est recalculé/mis en cache côté client.
const CLE_FAVORIS_MOBILE = "mobile_favoris";

function chargerFavoris() {
    try {
        return JSON.parse(localStorage.getItem(CLE_FAVORIS_MOBILE)) || [];
    } catch {
        return [];
    }
}

function sauvegarderFavoris(liste) {
    try {
        localStorage.setItem(CLE_FAVORIS_MOBILE, JSON.stringify(liste));
        return true;
    } catch {
        afficherToastMobile("Impossible d'enregistrer (stockage local indisponible).");
        return false;
    }
}

function ajouterFavori(favori) {
    const liste = chargerFavoris();
    liste.push(favori);
    if (!sauvegarderFavoris(liste)) return;
    afficherToastMobile("Trajet ajouté aux favoris.");
    rafraichirFavoris();
    const formulaire = document.getElementById("mobile-nouveau-favori");
    if (formulaire) formulaire.style.display = "none";
}

function supprimerFavori(indice) {
    const liste = chargerFavoris();
    liste.splice(indice, 1);
    sauvegarderFavoris(liste);
    rafraichirFavoris();
}

function rafraichirFavoris() {
    const conteneur = document.getElementById("mobile-favoris-liste");
    if (!conteneur) return;
    const liste = chargerFavoris();
    if (liste.length === 0) {
        conteneur.innerHTML = '<p class="mobile-info">Aucun favori enregistré.</p>';
        return;
    }
    conteneur.innerHTML = liste.map((f, i) => `
        <div class="mobile-favori-bloc">
            <button class="mobile-favori-retirer" onclick="supprimerFavori(${i})" title="Retirer des favoris">✕</button>
            <div class="mobile-carte-train-wrapper"
                 hx-get="/mobile/carte_train?train=${encodeURIComponent(f.train)}&gare=${encodeURIComponent(f.gare)}&heure=${encodeURIComponent(f.heure)}&destination=${encodeURIComponent(f.destination)}&mode=favori"
                 hx-trigger="load" hx-target="this" hx-swap="innerHTML">
                <div class="mobile-carte"><p class="mobile-info">Chargement…</p></div>
            </div>
        </div>
    `).join("");
    // Indispensable : le HTML injecté hors d'une réponse htmx (innerHTML
    // direct) n'est pas auto-scanné par htmx pour ses attributs hx-*.
    htmx.process(conteneur);
}

function afficherToastMobile(message) {
    const toast = document.createElement("div");
    toast.className = "mobile-toast";
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 2500);
}

// Détail d'un jour (barre du mini-graphique OU ligne de la liste "Retards
// constatés", carte de stats train) — tap tactile n'ayant pas d'équivalent
// au survol souris (title=), remplacé par une modale plutôt qu'un toast
// (qui reste réservé aux confirmations Favoris) : contenu à lire posément
// (horaire, contexte, météo), pas une notification éphémère. Tous les
// champs textuels (horaireTexte/contexteTexte) sont déjà formatés côté
// serveur (calculer_carte_stats_train_sql) — cette fonction ne fait
// qu'assembler le HTML, jamais de mise en forme de données ici. Champs
// optionnels transmis en chaîne vide ('') par le template quand absents
// (ex: train hors référentiel, retard inconnu à cette gare précise ce
// jour-là) — chaque section n'est incluse que si sa donnée est présente,
// plutôt que d'afficher un champ vide.
function afficherModaleJour(info) {
    const texteRetard = info.retardMin === 0 ? "À l'heure" : `+${info.retardMin} min`;
    // classe vaut "verte"/"orange"/"rouge" (mobile-barre-{{ classe }}), mais
    // mobile-etat-* attend "vert" sans e (mobile-etat-vert/-orange/-rouge) —
    // seule "verte" a besoin d'être normalisée.
    const classeEtat = info.classe === "verte" ? "vert" : info.classe;

    const blocs = [];
    if (info.horaireTexte) {
        blocs.push(`
            <div class="mobile-modale-bloc">
                <p class="mobile-modale-bloc-label">Horaire</p>
                <p class="mobile-modale-bloc-val">${info.horaireTexte}</p>
            </div>
        `);
    }
    if (info.contexteTexte) {
        blocs.push(`
            <div class="mobile-modale-bloc">
                <p class="mobile-modale-bloc-label">Contexte</p>
                <p class="mobile-modale-bloc-val">${info.contexteTexte}</p>
            </div>
        `);
    }
    if (info.meteoTemp !== "" || info.meteoPluie !== "" || info.meteoVent !== "") {
        blocs.push(`
            <div class="mobile-modale-bloc">
                <p class="mobile-modale-bloc-label">Météo ce jour-là</p>
                <div class="mobile-modale-meteo">
                    ${info.meteoTemp !== "" ? `<div><div class="mobile-modale-meteo-val">${info.meteoTemp}°</div><div class="mobile-modale-meteo-unite">température</div></div>` : ""}
                    ${info.meteoPluie !== "" ? `<div><div class="mobile-modale-meteo-val">${info.meteoPluie} mm</div><div class="mobile-modale-meteo-unite">pluie</div></div>` : ""}
                    ${info.meteoVent !== "" ? `<div><div class="mobile-modale-meteo-val">${info.meteoVent} km/h</div><div class="mobile-modale-meteo-unite">vent</div></div>` : ""}
                </div>
            </div>
        `);
    }

    const fond = document.createElement("div");
    fond.className = "mobile-modale-fond";
    fond.innerHTML = `
        <div class="mobile-modale">
            <button class="mobile-modale-fermer" aria-label="Fermer">✕</button>
            <p class="mobile-modale-date">${info.date}</p>
            <p class="mobile-modale-gare">${info.gare}</p>
            <p class="mobile-modale-retard mobile-etat-${classeEtat}">${texteRetard}</p>
            ${blocs.length ? `<div class="mobile-modale-separateur"></div>${blocs.join("")}` : ""}
        </div>
    `;
    const fermer = () => fond.remove();
    fond.addEventListener("click", (e) => { if (e.target === fond) fermer(); });
    fond.querySelector(".mobile-modale-fermer").addEventListener("click", fermer);
    document.body.appendChild(fond);
}

// Aide mobile — contenu fixe (pas de données serveur), rendu par le serveur
// dans _mobile_aide.html (inclus une fois par mobile_base.html, caché par
// défaut) : ce fichier ne fait qu'afficher/masquer le bloc, jamais de
// construction de HTML ici (convention "template idiot" du reste de l'appli).
function afficherAideMobile() {
    document.getElementById("mobile-aide").style.display = "flex";
}

function masquerAideMobile() {
    document.getElementById("mobile-aide").style.display = "none";
}
