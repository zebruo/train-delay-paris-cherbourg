// Onglet Quizz — contenu entièrement statique, dérivé du guide "Comment
// lire les statistiques" (generer_guide_statistiques.py, GUIDE_PAGES) pour
// ne jamais raconter une histoire différente de celle du guide officiel :
// les questions ET leurs explications reprennent les mêmes faits/textes
// "pourquoi". Aucun aller-retour serveur par question — tout se joue ici.

const QUIZZ_QUESTIONS = [
    {
        question: "Un train est retardé de 10 min entre Caen et Lison, mais rattrape son retard et arrive à l'heure à Cherbourg. Que dit la ponctualité officielle SNCF/ART à son sujet ?",
        choix: [
            "Il est compté comme perturbé, comme dans nos statistiques",
            "Il est compté comme 100 % ponctuel, puisqu'elle ne mesure que le retard à l'arrivée au terminus",
            "Il est exclu des statistiques officielles",
            "Il est compté une fois par gare où il a eu du retard",
        ],
        correct: 1,
        explication: "La ponctualité officielle SNCF/ART mesure uniquement le retard à l'arrivée au terminus — ce train est donc compté 100 % ponctuel, alors même que les voyageurs descendant à Lison ont bien subi une perturbation. « Circulations perturbées » compte au contraire tout train ayant subi un retard à un moment quelconque de son trajet, même rattrapé ensuite.",
    },
    {
        question: "Le tooltip « circulations perturbées » peut afficher plus de trains différents (X) que le nombre total de trains du référentiel actuel (Y). Pourquoi ?",
        choix: [
            "C'est un bug de comptage à corriger",
            "Le référentiel ne couvre qu'une fenêtre glissante d'environ 151 jours : d'anciennes variantes d'horaires peuvent en sortir sans que le train n'ait disparu des statistiques",
            "X compte aussi les trains internationaux",
            "Le référentiel est mis à jour plus souvent que les statistiques",
        ],
        correct: 1,
        explication: "Le référentiel est reconstruit à partir d'un export SNCF limité à une fenêtre glissante d'environ 151 jours : une variante d'horaire ancienne peut en sortir au fil des régénérations et disparaître du référentiel, même si le train a bien circulé et reste compté dans X.",
    },
    {
        question: "Un train a eu du retard à 2 gares au cours de son trajet, mais son dernier relevé (à l'arrivée) montre 0 min de retard partout. Est-il compté dans le « Retard cumulé » ?",
        choix: [
            "Oui, la totalité de son retard observé est additionnée",
            "Non — seul le dernier retard connu à chaque passage compte, donc 0 min ici",
            "Oui, mais seulement à moitié",
            "Non, il est alors exclu de toutes les statistiques",
        ],
        correct: 1,
        explication: "« Retard cumulé » ne garde que le dernier retard connu par gare, pas à chaque fois que le système a vérifié ce train — si le dernier relevé montre 0 min partout, aucun « passage impacté » n'est comptabilisé pour lui, même si des retards ont pu être observés plus tôt.",
    },
    {
        question: "Pourquoi le « Retard moyen / relevé » affiche souvent une valeur minuscule (ex: 0.1 min) alors que le retard max du jour est de 45 min ?",
        choix: [
            "C'est une erreur de calcul à corriger",
            "Il exclut volontairement les gros retards",
            "Il est dilué par des milliers de relevés à 0 min, puisqu'il moyenne chaque interrogation du système, pas juste les trains en retard",
            "Il ne compte que les 10 derniers trains",
        ],
        correct: 2,
        explication: "« Retard moyen / relevé » est la moyenne de tous les relevés individuels du système (chaque interrogation, gare par gare) — la plupart à 0 min ou vite corrigés, ce qui dilue fortement la moyenne par rapport à un retard max ponctuel.",
    },
    {
        question: "Quelle est la vraie différence entre « Retard cumulé » et « Retard moyen / relevé » ?",
        choix: [
            "Ce sont deux noms différents pour exactement le même calcul",
            "Retard cumulé ne compte que les retards au départ, Retard moyen / relevé seulement les retards à l'arrivée",
            "Retard cumulé garde le dernier retard connu par passage en gare (l'état final réel) ; Retard moyen / relevé moyenne chaque interrogation du système, même les prédictions ensuite corrigées",
            "Retard moyen / relevé est calculé sur la semaine, Retard cumulé sur la seule journée en cours",
        ],
        correct: 2,
        explication: "Retard cumulé « reflète l'état final réel » : il ne garde que le dernier retard connu à chaque passage en gare. Retard moyen / relevé « reflète toute l'histoire des prédictions même corrigées » : il moyenne chaque relevé individuel du système, y compris les signaux d'alerte temporaires vus en temps réel puis rattrapés — une sorte de « volatilité » des prédictions plutôt qu'un résultat final, d'où sa valeur souvent bien plus petite.",
    },
    {
        question: "Le flux temps réel SNCF confirme-t-il explicitement qu'un train est bien arrivé à son terminus ?",
        choix: [
            "Oui, un statut « arrivé » est publié pour chaque train",
            "Non — le trajet disparaît simplement du flux une fois terminé ; la dernière prédiction connue avant cette disparition est utilisée",
            "Oui, mais seulement pour les grandes gares",
            "Non, seuls les retards de plus de 30 min sont suivis jusqu'au bout",
        ],
        correct: 1,
        explication: "Le flux temps réel SNCF ne confirme jamais explicitement l'arrivée d'un train : le trajet disparaît simplement du flux une fois terminé, souvent juste après l'heure d'arrivée prévue. Le retard max (et toute valeur affichée pour un trajet) correspond donc à la dernière prédiction connue avant cette disparition, pas à une confirmation réelle.",
    },
    {
        question: "Dans l'onglet Tableau, le chiffre de la colonne « Dép. » (retard au départ) apparaît en doré (jaune) pour une circulation. Que signifie cette couleur ?",
        choix: [
            "Le train est arrivé avec plus de 10 min de retard",
            "Le train a été annulé",
            "Aucune donnée n'est disponible pour ce train",
            "Le train est arrivé correctement (< 5 min) mais reste immobilisé plus longtemps que prévu au départ de cette gare",
        ],
        correct: 3,
        explication: "Le doré existe pour un cas précis qui resterait sinon invisible : un train arrivé pile à l'heure n'a aucune couleur d'alerte si on ne regarde que le retard à l'arrivée, alors qu'il peut être en train d'accumuler un vrai retard de départ, pas encore visible ailleurs.",
    },
    {
        question: "Dans l'onglet Tableau, le chiffre de la colonne « Dép. » devient doré quand un train arrive correctement (< 5 min) mais reste immobilisé plus longtemps que prévu au départ d'une gare. Ce doré peut signaler un incident tout frais... ou un aléa connu depuis le début du trajet. L'application fait-elle la différence automatiquement entre ces deux cas ?",
        choix: [
            "Oui, une icône distingue les deux cas",
            "Non — il faut comparer ce retard de départ aux relevés précédents via l'onglet « Suivi d'un train » pour savoir si c'est nouveau ou stable",
            "Oui, mais seulement dans les rapports PDF",
            "Non, ce cas n'est jamais affiché en doré",
        ],
        correct: 1,
        explication: "Distinguer les deux cas demanderait de comparer ce retard de départ aux relevés précédents de ce même train, pas juste au dernier — pas construit pour l'instant. En attendant, « Suivi d'un train » permet de vérifier à la main : retard stable sur plusieurs relevés = aléa connu ; retard qui vient de changer = incident frais.",
    },
    {
        question: "Un train Rennes → Caen ne va jamais jusqu'à Paris ni Cherbourg. Apparaît-il dans les statistiques de la ligne Paris ↔ Cherbourg ?",
        choix: [
            "Non, seuls les trains de bout en bout sont suivis",
            "Non, seulement s'il est en retard",
            "Oui — tout train empruntant un tronçon de la ligne est inclus, un retard sur ce tronçon est un signal utile",
            "Oui, mais uniquement dans le rapport mensuel",
        ],
        correct: 2,
        explication: "Le rapport ne suit pas seulement les trains Paris-Cherbourg de bout en bout : il suit tout train empruntant un tronçon de cette ligne (ici Lison, Bayeux, Caen), même s'il continue ailleurs ensuite — un retard survenu sur un tronçon partagé reste un vrai signal utile pour la ligne.",
    },
    {
        question: "Un rapport quotidien généré le 28/07 au matin couvre quelle période ?",
        choix: [
            "Minuit à minuit le 28/07",
            "Les 24 dernières heures avant sa génération",
            "Toute la semaine en cours",
            "27/07 2 h → 28/07 2 h — le dernier cycle complet déjà terminé",
        ],
        correct: 3,
        explication: "Le rapport quotidien couvre une journée de 2 h du matin à 2 h le lendemain, pas minuit à minuit ni les 24 dernières heures — 2 h du matin est le creux du trafic nocturne, ce qui limite le risque qu'une circulation soit coupée en deux périodes différentes.",
    },
    {
        question: "Un train est entièrement annulé sur la période. Apparaît-il dans les autres statistiques du rapport (Circulations perturbées, Retard cumulé...) ?",
        choix: [
            "Oui, comme un train avec un retard infini",
            "Non — il n'atteint jamais son terminus, donc jamais considéré « arrivé », et n'apparaît que dans le compteur dédié « Circulations annulées »",
            "Oui, mais seulement dans Retard cumulé",
            "Cela dépend de la gare de départ",
        ],
        correct: 1,
        explication: "Un train annulé n'atteint jamais son terminus, donc n'est jamais considéré comme « arrivé » — il reste invisible de toutes les autres statistiques et n'apparaît que dans le compteur dédié « Circulations annulées ».",
    },
    {
        question: "La colonne « Nouveaux » de Vérification GTFS peut bouger d'un jour à l'autre (ex: 5 puis 4) sans aucun changement réel d'horaire SNCF. Pourquoi ?",
        choix: [
            "Les horaires SNCF publiés en ligne ne couvrent qu'une fenêtre d'environ 151 jours, qui avance chaque jour — un train déjà prévu devient visible d'un coup en entrant dans cette fenêtre",
            "C'est un bug connu de l'onglet, à ignorer",
            "La SNCF republie systématiquement tous ses horaires chaque nuit",
            "Le nombre de gares suivies change chaque jour",
        ],
        correct: 0,
        explication: "Les horaires SNCF publiés en ligne ne couvrent jamais que les ~151 prochains jours, une fenêtre qui avance d'un jour chaque jour — un train déjà prévu par la SNCF mais plus loin dans le temps devient visible d'un coup le jour où cette fenêtre l'atteint. D'où la règle : ne pas s'inquiéter d'un chiffre isolé, mais surveiller si « Nouveaux » reste supérieur à zéro plusieurs jours de suite.",
    },
];

let quizzOrdre = [];
let quizzIndex = 0;
let quizzScore = 0;
let quizzChoixOrdreCourant = [];

function quizzMelanger(tableau) {
    const copie = tableau.slice();
    for (let i = copie.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [copie[i], copie[j]] = [copie[j], copie[i]];
    }
    return copie;
}

function demarrerQuizz() {
    // Élément dédié absent (mauvais onglet, ou htmx pas encore inséré le
    // contenu) : rien à faire, comme les autres redraws de base.html
    // (dessinerGraphique, etc.) qui vérifient toujours la présence de leur
    // conteneur avant d'agir.
    if (!document.getElementById("quizz-question-zone")) return;

    quizzOrdre = quizzMelanger(QUIZZ_QUESTIONS.map((_, i) => i));
    quizzIndex = 0;
    quizzScore = 0;
    document.getElementById("quizz-total").textContent = QUIZZ_QUESTIONS.length;
    document.getElementById("quizz-fin").style.display = "none";
    document.getElementById("quizz-question-zone").style.display = "";
    quizzAfficherQuestion();
}

function quizzAfficherQuestion() {
    const question = QUIZZ_QUESTIONS[quizzOrdre[quizzIndex]];
    // Choix mélangés aussi (pas seulement l'ordre des questions) : sinon la
    // bonne réponse resterait toujours à la même position d'une manche à
    // l'autre pour une question donnée, facile à mémoriser sans comprendre.
    // Rangs (positions à l'écran) -> index original dans question.choix,
    // pour retrouver la bonne réponse au clic sans dépendre du texte.
    quizzChoixOrdreCourant = quizzMelanger(question.choix.map((_, i) => i));

    document.getElementById("quizz-numero").textContent = quizzIndex + 1;
    document.getElementById("quizz-score").textContent = quizzScore;
    document.getElementById("quizz-question-texte").textContent = question.question;
    document.getElementById("quizz-feedback").style.display = "none";

    const zoneChoix = document.getElementById("quizz-choix");
    zoneChoix.innerHTML = "";
    quizzChoixOrdreCourant.forEach((indexOriginal, rang) => {
        const bouton = document.createElement("button");
        bouton.type = "button";
        bouton.className = "quizz-choix-bouton";
        bouton.textContent = question.choix[indexOriginal];
        bouton.onclick = () => quizzChoisirReponse(rang);
        zoneChoix.appendChild(bouton);
    });
}

function quizzChoisirReponse(rangChoisi) {
    const question = QUIZZ_QUESTIONS[quizzOrdre[quizzIndex]];
    const boutons = document.querySelectorAll("#quizz-choix .quizz-choix-bouton");
    boutons.forEach((bouton, rang) => {
        bouton.disabled = true;
        if (quizzChoixOrdreCourant[rang] === question.correct) bouton.classList.add("quizz-correct");
        else if (rang === rangChoisi) bouton.classList.add("quizz-incorrect");
    });

    if (quizzChoixOrdreCourant[rangChoisi] === question.correct) quizzScore++;
    document.getElementById("quizz-score").textContent = quizzScore;
    document.getElementById("quizz-feedback-texte").textContent = question.explication;
    document.getElementById("quizz-feedback").style.display = "";
}

function quizzSuivant() {
    quizzIndex++;
    if (quizzIndex >= quizzOrdre.length) {
        document.getElementById("quizz-question-zone").style.display = "none";
        document.getElementById("quizz-fin").style.display = "";
        document.getElementById("quizz-score-final").textContent =
            quizzScore + " / " + QUIZZ_QUESTIONS.length + " bonnes réponses.";
    } else {
        quizzAfficherQuestion();
    }
}