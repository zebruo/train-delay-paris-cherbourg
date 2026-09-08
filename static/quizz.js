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
        question: "Un train a eu du retard à 2 gares au cours de son trajet, mais son dernier relevé (à l'arrivée) montre 0 min de retard partout. Quelle est sa contribution au « Retard cumulé » ?",
        choix: [
            "La somme de tous les retards observés à un moment ou un autre, même rattrapés",
            "Seul le dernier retard connu à chaque passage compte, sa contribution sera comptée pour 0 min.",
            "Seule la moitié du retard initial est comptée",
            "Il est totalement exclu du calcul, comme un train jamais parti",
        ],
        correct: 1,
        explication: "« Retard cumulé » ne garde que le dernier retard connu par gare, pas la totalité des valeurs observées au fil du temps — si le dernier relevé montre 0 min partout, sa contribution au total est 0, même si des retards ont pu être observés plus tôt puis rattrapés. Le train reste inclus dans le calcul, il n'apporte simplement rien au total.",
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
        explicationHTML:
            "<p>« Retard moyen / relevé » est la moyenne de tous les relevés individuels du système (chaque interrogation, gare par gare) — la plupart à 0 min ou vite corrigés, ce qui dilue fortement la moyenne par rapport à un retard max ponctuel.</p>" +
            "<p>Exemple : 450 relevés ce jour-là (plusieurs dizaines de trains, sondés toutes les 5 min à chacune de leurs gares) — un seul incident isolé atteint 45 min, tous les autres relevés sont à 0 min.</p>" +
            "<pre class=\"quizz-exemple\">449 relevés à 0 min\n" +
            "1 relevé à 45 min (l'incident, le « retard max » du jour)\n\n" +
            "Retard moyen / relevé : (449 × 0 + 1 × 45) ÷ 450 ≈ 0,1 min\n" +
            "Retard max : 45 min</pre>" +
            "<p>Le retard max capture bien l'incident isolé. Retard moyen / relevé, lui, le noie complètement au milieu des centaines d'autres relevés à l'heure — les deux chiffres sont corrects, ils ne répondent juste pas à la même question.</p>",
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
        explicationHTML:
            "<p>Retard cumulé « reflète l'état final réel » : il ne garde que le dernier retard connu à chaque passage en gare. Retard moyen / relevé « reflète toute l'histoire des prédictions même corrigées » : il moyenne chaque relevé individuel du système, y compris les signaux d'alerte temporaires vus en temps réel puis rattrapés.</p>" +
            "<p>Exemple : 2 trains, interrogés toutes les 5 min à chacune de leurs gares (comme le fait vraiment le collecteur).</p>" +
            "<pre class=\"quizz-exemple\">Train 1 — Gare A : 0, 0, 0, 0, 0            (5 relevés, dernier = 0)\n" +
            "Train 1 — Gare B : 0, 0, 0                  (3 relevés, dernier = 0)\n" +
            "Train 1 — Gare C (terminus) : 0, 0, 5, 8, 8 (5 relevés, dernier = 8)\n\n" +
            "Train 2 — Gare A : 0, 0, 0, 0               (4 relevés, dernier = 0)\n" +
            "Train 2 — Gare B : 0, 0, 0, 3, 3            (5 relevés, dernier = 3)\n" +
            "Train 2 — Gare C (terminus) : 0, 0, 10, 15, 15, 15 (6 relevés, dernier = 15)\n\n" +
            "Retard cumulé (dernier relevé, 1 fois par passage — 6 passages) :\n" +
            "(0 + 0 + 8) + (0 + 3 + 15) = 26 min\n\n" +
            "Retard moyen / relevé (tous les relevés, sans exception — 28 relevés) :\n" +
            "82 ÷ 28 ≈ 2,93 min</pre>" +
            "<p>19 des 28 relevés sont à 0 min (trains à l'heure la plupart du temps) — ils ne comptent pour rien dans le Retard cumulé (seul le dernier par passage compte), mais pèsent pleinement dans le Retard moyen / relevé. D'où l'écart : 26 ÷ 6 passages ferait 4,33 min, très différent des 2,93 min réellement obtenus.</p>",
    },
    {
        question: "La stat « Retard moyen / relevé » (barre du haut) et la courbe de l'onglet Graphique donnent souvent des valeurs légèrement différentes (ex: 1.1 min vs 1 min) sur la même période. Pourquoi ?",
        choix: [
            "Un bug fait dériver les deux calculs l'un de l'autre au fil du temps",
            "« Retard moyen / relevé » moyenne à plat chaque relevé individuel ; la courbe du Graphique moyenne d'abord instant par instant, puis moyenne ces points entre eux — deux définitions différentes de « moyenne » appliquées à la même donnée",
            "La courbe du Graphique exclut automatiquement les gares hors ligne, pas la barre du haut",
            "Ce sont deux périodes différentes (24 dernières heures vs 7 derniers jours)",
        ],
        correct: 1,
        explication: "« Retard moyen / relevé » est une moyenne à plat de tous les relevés individuels. La courbe du Graphique calcule une moyenne différente : instant par instant (moyenne des circulations actives à ce moment précis), puis ces points-résultats sont eux-mêmes moyennés entre eux — un calcul en deux étages. Même donnée brute, deux résultats différents : ce n'est pas un bug, ce sont deux définitions de « moyenne » différentes.",
        explicationHTML:
            "<p>« Retard moyen / relevé » est une moyenne à plat de tous les relevés individuels. La courbe du Graphique calcule une moyenne différente : instant par instant (moyenne des circulations actives à ce moment précis), puis ces points-résultats sont eux-mêmes moyennés entre eux — un calcul en deux étages.</p>" +
            "<p>Exemple : train A a 3 relevés (0, 0, 2 min), train B en a 2 (0, 4 min).</p>" +
            "<pre class=\"quizz-exemple\">Instant    Train A    Train B    Moyenne à l'instant\n" +
            "1          0 min      0 min      0 min\n" +
            "2          0 min      4 min      2 min\n" +
            "3          2 min      —          2 min\n\n" +
            "Courbe du Graphique : (0 + 2 + 2) ÷ 3 ≈ 1.33 min\n" +
            "Retard moyen / relevé : (0 + 0 + 2 + 0 + 4) ÷ 5 = 1.2 min</pre>" +
            "<p>Même donnée brute, deux résultats différents — le train B, moins souvent relevé, pèse autant que A à chaque instant dans la courbe, alors qu'il pèse moins dans la moyenne à plat (2 relevés contre 3). Ce n'est pas un bug, ce sont deux définitions de « moyenne » différentes.</p>",
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
        question: "Dans l'onglet Circulations, le chiffre de la colonne « Dép. » (retard au départ) apparaît en doré (jaune) pour une circulation. Que signifie cette couleur ?",
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
        question: "Le chiffre de la colonne « Dép. » devient doré uniquement quand le retard au départ d'une gare atteint 5 min (y compris à la toute première gare du trajet). Cette couleur peut aussi bien signaler un incident tout frais qu'un aléa connu depuis le début du trajet. L'application fait-elle la différence automatiquement entre ces deux cas ?",
        choix: [
            "Oui, une icône distingue les deux cas",
            "Non — et il n'existe actuellement aucun moyen de le vérifier dans l'application, pas même via « Suivi d'un train »",
            "Oui, mais seulement dans les rapports PDF",
            "Non, ce cas n'est jamais affiché en doré",
        ],
        correct: 1,
        explication: "« Suivi d'un train » ne peut pas aider ici : ses deux vues (Escalier et Détail des relevés) tracent le retard à l'arrivée dès qu'il est connu et ignorent alors le retard au départ. Qu'il s'agisse d'un incident tout frais ou d'un aléa récurrent, ce retard au départ n'est visible que dans la colonne « Dép. » du Tableau.",
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
    {
        question: "Cette fois, la colonne « Nouveaux » reste supérieure à zéro plusieurs jours de suite (pas un chiffre isolé qui retombe) — donc un vrai changement durable, pas un effet de la fenêtre glissante. Qui doit s'en occuper ?",
        choix: [
            "Rien à faire, ça finit toujours par se résorber tout seul au bout de 151 jours",
            "N'importe quel utilisateur de cette version web, via un bouton dédié",
            "L'administrateur de l'application — pas accessible depuis cette version web",
            "La SNCF corrige automatiquement la référence utilisée par l'application",
        ],
        correct: 2,
        explication: "Mettre à jour la référence est une action volontaire, réservée à l'administrateur de l'application — cette version web reste volontairement en lecture seule.",
    },
    {
        question: "Parmi les indicateurs de la barre du haut, lequel répond le mieux à « puis-je compter sur cette ligne » pour un usager ?",
        choix: [
            "« Retard cumulé », le total de temps perdu sur la ligne",
            "« Circulations perturbées », le seul conçu pour donner une idée d'ensemble en un coup d'œil",
            "« Retard moyen / relevé », la moyenne de tous les relevés du système",
            "« Retard max », le pire retard observé sur la période",
        ],
        correct: 1,
        explication: "« Circulations perturbées » est le seul indicateur explicitement pensé pour répondre à « à quel point la journée a été mauvaise » en un coup d'œil : un simple pourcentage de circulations touchées, directement compréhensible sans connaître le détail des autres calculs de l'appli.",
    },
    {
        question: "« Trajets sans perturbation » affichent deux pourcentages. Pourquoi ?",
        choix: [
            "C'est une correction de bug, le premier chiffre était faux",
            "Le premier chiffre compte tout retard, même 1 minute rattrapée aussitôt — un total qui peut sembler alarmant sans être très parlant ; le second tolère les perturbations mineures (≤ 5 min) pour isoler ce qui compte vraiment pour un usager",
            "Le second chiffre porte sur une période différente du premier",
            "Les deux mesurent des choses indépendantes, sans lien entre elles",
        ],
        correct: 1,
        explication: "« Trajets sans perturbation » affiche un résultat strict, même pour quelques minutes vite rattrapées — un chiffre « au total » qui peut donner une impression trompeuse. Le second pourcentage tolère les perturbations mineures (≤ 5 min) en affichant ce qui compte vraiment pour un usager.",
    },
    {
        question: "« X % des relevés du flux temps réel SNCF indiquent un train à l'heure » et « Trajets sans perturbation » (en tolérant les perturbations mineures) sont parfois très proches. Quelle est la différence entre ces deux pourcentages ?",
        choix: [
            "Ce sont deux façons différentes d'arrondir exactement le même calcul",
            "Le premier compte chaque passage en gare séparément à 0 min de retard pile (aucune tolérance), le second compte chaque train une seule fois, avec une tolérance de 5 min",
            "Le premier porte sur les 90 derniers jours, le second sur toute la collecte",
            "Le premier exclut les circulations annulées, le second les inclut",
        ],
        correct: 1,
        explication: "« X % des relevés... » compte chaque passage en gare individuellement, à 0 min de retard pile (aucune tolérance) — un train qui dessert 10 gares avec un seul retard compte déjà pour 9 passages « à l'heure » sur 10. « Trajets sans perturbation » compte chaque circulation une seule fois, quel que soit son nombre d'arrêts, et tolère jusqu'à 5 min de retard n'importe où sur le trajet. Les deux chiffres peuvent se ressembler un jour donné par coïncidence, sans être liés par le calcul.",
        explicationHTML:
            "<p>« X % des relevés... » compte chaque passage en gare individuellement, à 0 min de retard pile (aucune tolérance). « Trajets sans perturbation » compte chaque circulation une seule fois, quel que soit son nombre d'arrêts, et tolère jusqu'à 5 min de retard n'importe où sur le trajet.</p>" +
            "<p>Exemple : 2 trains, 3 gares chacun.</p>" +
            "<pre class=\"quizz-exemple\">Gare        Train A    Train B\n" +
            "1           0 min      0 min\n" +
            "2           0 min      0 min\n" +
            "3           0 min      8 min\n\n" +
            "« X % des relevés... à l'heure » (passages à 0 min pile, sur 6 passages) :\n" +
            "5 ÷ 6 ≈ 83,3 %\n\n" +
            "Trajets sans perturbation (tolérant ≤ 5 min, sur 2 trains) :\n" +
            "trA qualifié (max 0 min) ; trB culmine à 8 min (> 5 min) → 1trA ÷ (1trA + 1trB) = 50 %</pre>" +
            "<p>Deux trains, deux définitions différentes de « à l'heure » : 83,3 % côté relevés, 50 % côté circulations. Un jour donné, ces deux pourcentages peuvent se rapprocher par coïncidence — ils ne mesurent pas la même chose.</p>",
    },
    {
        question: "Le tooltip « Retard max » peut correspondre à une circulation pour laquelle « Suivi d'un train » affiche pourtant « trajet théorique introuvable ». Comment est-ce possible ?",
        choix: [
            "C'est une incohérence entre les deux onglets, à corriger",
            "Une circulation ancienne peut avoir un horaire théorique qui a changé depuis (la SNCF republie régulièrement des ajustements) — le référentiel actuel ne retrouve plus la bonne variante, mais le retard mesuré à l'époque reste bien réel",
            "« Suivi d'un train » ne couvre que les 7 derniers jours, contrairement à Retard max",
            "Le train concerné a été supprimé du référentiel SNCF",
        ],
        correct: 1,
        explication: "La SNCF republie régulièrement des ajustements d'horaires théoriques. Un retard peut avoir été mesuré à l'époque par rapport à l'horaire alors en vigueur, mais si cet horaire a changé depuis, le référentiel actuel ne retrouve plus la bonne variante pour reconstruire le trajet théorique — d'où « trajet théorique introuvable » dans Suivi d'un train. Le retard, lui, reste parfaitement réel et compté dans les statistiques.",
    },
    {
        question: "La frise « État de la ligne » (bas de page) et le tooltip « Retard moyen par relevé » (barre du haut) peuvent afficher des valeurs différentes, même quand aucun filtre n'est actif. Pourquoi ?",
        choix: [
            "La frise a un bug d'arrondi",
            "La frise reste toujours calculée sur les 7 derniers jours et les 11 gares de la ligne, en ignorant « Limiter aux trains avec retard » — la barre du haut, elle, suit tous les filtres actifs sur toute la période choisie",
            "La frise ne compte que les trains annulés",
            "Les deux utilisent une source de données différente",
        ],
        correct: 1,
        explication: "La frise reste toujours restreinte à une fenêtre fixe de 7 jours et aux 11 gares de la ligne, et ignore volontairement « Limiter aux trains avec retard » (qui gonflerait artificiellement la moyenne affichée gare par gare, en excluant les trains ponctuels) — alors que le « Retard moyen par relevé » de la barre du haut suit tous les filtres actifs (Gare/Train/Sens/Limiter aux trains avec retard) sur toute la période choisie. Deux indicateurs qui se ressemblent mais ne répondent pas à la même question.",
    },
    {
        question: "« Gare la + touchée » désigne la gare avec le retard moyen par relevé le plus élevé. Un seul train très en retard, resté dans le flux temps réel plusieurs dizaines de minutes (donc sondé à répétition), peut-il à lui seul faire basculer ce classement ?",
        choix: [
            "Non, chaque train ne compte qu'une seule fois dans cette moyenne",
            "Oui — et rien dans ce chiffre ne permet de savoir si une gare l'emporte à cause d'un vrai gros problème ou simplement parce qu'un train y est resté sondé plus longtemps",
            "Non, seule la dernière valeur connue de chaque train compte",
            "Oui, mais uniquement si ce train dessert au moins 3 gares différentes",
        ],
        correct: 1,
        explication: "« Gare la + touchée » moyenne à plat tous les relevés individuels, donc un seul train très en retard, sondé à répétition, peut suffire à faire basculer ce classement.",
        explicationHTML:
            "<p>« Gare la + touchée » moyenne à plat tous les relevés individuels, donc un seul train très en retard, sondé à répétition, peut suffire à faire basculer ce classement.</p>" +
            "<p>Exemple : Gare A n'a qu'un seul train perturbé ce jour-là, mais très en retard et resté longtemps dans le flux ; Gare B a 5 trains différents, chacun un peu en retard.</p>" +
            "<pre class=\"quizz-exemple\">Gare A — 1 train à 60 min, resté 45 min dans le flux (10 relevés) :\n" +
            "10 relevés à 60 min\n" +
            "Gare A — 5 autres trains ponctuels, 1 relevé chacun :\n" +
            "5 relevés à 0 min\n\n" +
            "Gare B — 5 trains DIFFÉRENTS à 8 min chacun, 1 relevé chacun :\n" +
            "5 relevés à 8 min\n\n" +
            "Retard moyen Gare A : (10 × 60 + 5 × 0) ÷ 15 = 40 min\n" +
            "Retard moyen Gare B : (5 × 8) ÷ 5 = 8 min</pre>" +
            "<p>Il ne distingue pas un vrai problème touchant plusieurs trains d'un seul train très en retard resté longtemps dans le flux. Son utilité est plutôt de pointer vers quelque chose à vérifier, un signal à creuser (aller regarder l'onglet Circulations pour cette gare) plutôt que de trancher tout seul.</p>",
    },
];

let quizzOrdre = [];
let quizzIndex = 0;
let quizzScore = 0;
let quizzChoixOrdreCourant = [];
// Historique par question, indexé sur l'ordre FIXE de QUIZZ_QUESTIONS (pas
// quizzOrdre, mélangé) : null (jamais vue), "correct" ou "incorrect" — sert
// uniquement à colorer l'index (quizzConstruireIndex), jamais lu par le
// déroulé normal de la partie (quizzScore/quizzIndex restent la seule
// source de vérité pour la progression affichée).
let quizzHistorique = [];
// Consultation (voir quizzAllerA) : sauter vers une question depuis l'index
// ne doit PAS perturber la partie en cours (demande explicite de
// l'utilisateur, 2026-09-08) — quizzIndex/quizzOrdre restent inchangés tant
// qu'on consulte, quizzQuestionConsultee pointe la question affichée à la
// place le temps de la consultation.
let quizzEnConsultation = false;
let quizzQuestionConsultee = null;
// Distinct de quizzIndex >= quizzOrdre.length : une fois la partie terminée
// (écran #quizz-fin), quizzIndex reste à sa dernière valeur valide plutôt
// que de dépasser quizzOrdre.length — nécessaire pour que quizzIndexFixeActuel
// (quizzOrdre[quizzIndex]) reste toujours valide pendant une consultation
// lancée depuis l'écran de fin (l'index reste utilisable après la partie).
let quizzTermine = false;

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
    quizzHistorique = new Array(QUIZZ_QUESTIONS.length).fill(null);
    quizzEnConsultation = false;
    quizzQuestionConsultee = null;
    quizzTermine = false;
    document.getElementById("quizz-total").textContent = QUIZZ_QUESTIONS.length;
    document.getElementById("quizz-index-panneau").style.display = "none";
    document.getElementById("quizz-bouton-index").classList.remove("ouvert");
    document.getElementById("quizz-fin").style.display = "none";
    document.getElementById("quizz-question-zone").style.display = "";
    quizzAfficherQuestion();
}

// Index de la question actuellement affichée dans l'ordre FIXE de
// QUIZZ_QUESTIONS, qu'elle vienne de la partie en cours ou d'une
// consultation — seul point que quizzAfficherQuestion/quizzChoisirReponse
// interrogent, pour ne jamais avoir à dupliquer ce choix ailleurs.
function quizzIndexFixeActuel() {
    return quizzEnConsultation ? quizzQuestionConsultee : quizzOrdre[quizzIndex];
}

function quizzBasculerIndex() {
    const panneau = document.getElementById("quizz-index-panneau");
    const bouton = document.getElementById("quizz-bouton-index");
    const ouvre = panneau.style.display === "none";
    if (ouvre) quizzConstruireIndex();
    panneau.style.display = ouvre ? "" : "none";
    bouton.classList.toggle("ouvert", ouvre);
}

function quizzConstruireIndex() {
    const liste = document.getElementById("quizz-index-liste");
    liste.innerHTML = "";
    const indexFixeActuel = quizzIndexFixeActuel();
    QUIZZ_QUESTIONS.forEach((question, i) => {
        const ligne = document.createElement("button");
        ligne.type = "button";
        ligne.className = "quizz-index-ligne";
        if (i === indexFixeActuel) ligne.classList.add("quizz-index-courante");
        else if (quizzHistorique[i] === "correct") ligne.classList.add("quizz-index-correcte");
        else if (quizzHistorique[i] === "incorrect") ligne.classList.add("quizz-index-incorrecte");

        const numero = document.createElement("span");
        numero.className = "quizz-index-numero";
        numero.textContent = i + 1;

        const texte = document.createElement("span");
        texte.className = "quizz-index-texte";
        texte.textContent = question.question;

        ligne.appendChild(numero);
        ligne.appendChild(texte);
        ligne.onclick = () => quizzAllerA(i);
        liste.appendChild(ligne);
    });
}

function quizzAllerA(i) {
    quizzEnConsultation = true;
    quizzQuestionConsultee = i;
    document.getElementById("quizz-index-panneau").style.display = "none";
    document.getElementById("quizz-bouton-index").classList.remove("ouvert");
    // La consultation reste possible après la fin de la partie (écran
    // #quizz-fin) : il faut alors ré-afficher la zone de question par-dessus
    // — quizzSuivant restaure l'écran de fin en sortant de consultation si
    // quizzTermine est resté vrai entre-temps.
    if (quizzTermine) {
        document.getElementById("quizz-fin").style.display = "none";
        document.getElementById("quizz-question-zone").style.display = "";
    }
    quizzAfficherQuestion();
}

function quizzAfficherQuestion() {
    const indexFixe = quizzIndexFixeActuel();
    const question = QUIZZ_QUESTIONS[indexFixe];
    // Choix mélangés aussi (pas seulement l'ordre des questions) : sinon la
    // bonne réponse resterait toujours à la même position d'une manche à
    // l'autre pour une question donnée, facile à mémoriser sans comprendre.
    // Rangs (positions à l'écran) -> index original dans question.choix,
    // pour retrouver la bonne réponse au clic sans dépendre du texte.
    quizzChoixOrdreCourant = quizzMelanger(question.choix.map((_, i) => i));

    document.getElementById("quizz-numero").textContent = quizzIndex + 1;
    document.getElementById("quizz-score").textContent = quizzScore;
    document.getElementById("quizz-consultation-bandeau").style.display = quizzEnConsultation ? "" : "none";
    document.getElementById("quizz-question-texte").textContent = question.question;
    document.getElementById("quizz-feedback").style.display = "none";

    const zoneChoix = document.getElementById("quizz-choix");
    zoneChoix.innerHTML = "";
    // Question déjà répondue (consultée depuis l'index) : ne redemande pas
    // la réponse, montre directement la bonne en vert (boutons désactivés)
    // — reconstruire aussi la mauvaise réponse choisie à l'origine
    // demanderait de la conserver en plus du simple correct/incorrect,
    // inutile pour ce que l'index sert à faire (retrouver une explication).
    const dejaRepondue = quizzHistorique[indexFixe] !== null;
    quizzChoixOrdreCourant.forEach((indexOriginal, rang) => {
        const bouton = document.createElement("button");
        bouton.type = "button";
        bouton.className = "quizz-choix-bouton";
        bouton.textContent = question.choix[indexOriginal];
        if (dejaRepondue) {
            bouton.disabled = true;
            if (indexOriginal === question.correct) bouton.classList.add("quizz-correct");
        } else {
            bouton.onclick = () => quizzChoisirReponse(rang);
        }
        zoneChoix.appendChild(bouton);
    });
    if (dejaRepondue) quizzAfficherFeedback(question);
}

function quizzChoisirReponse(rangChoisi) {
    const indexFixe = quizzIndexFixeActuel();
    const question = QUIZZ_QUESTIONS[indexFixe];
    const boutons = document.querySelectorAll("#quizz-choix .quizz-choix-bouton");
    const juste = quizzChoixOrdreCourant[rangChoisi] === question.correct;
    boutons.forEach((bouton, rang) => {
        bouton.disabled = true;
        if (quizzChoixOrdreCourant[rang] === question.correct) bouton.classList.add("quizz-correct");
        else if (rang === rangChoisi) bouton.classList.add("quizz-incorrect");
    });

    quizzHistorique[indexFixe] = juste ? "correct" : "incorrect";
    // Le score affiché ne suit que la progression réelle de la partie —
    // répondre à une question consultée depuis l'index (hors séquence) ne
    // doit pas le modifier, sans quoi il pourrait avancer deux fois pour la
    // même question si son tour normal arrive plus tard dans quizzOrdre.
    if (!quizzEnConsultation && juste) {
        quizzScore++;
        document.getElementById("quizz-score").textContent = quizzScore;
    }
    quizzAfficherFeedback(question);
}

function quizzAfficherFeedback(question) {
    const feedbackEl = document.getElementById("quizz-feedback-texte");
    // explicationHTML : réservé aux quelques questions avec un exemple mis
    // en forme (tableau aligné, voir .quizz-exemple) — contenu écrit à la
    // main ici, jamais dérivé d'une entrée utilisateur, donc sûr en
    // innerHTML. Toutes les autres questions n'ont que `explication`
    // (texte brut) et passent par textContent comme avant.
    if (question.explicationHTML) {
        feedbackEl.innerHTML = question.explicationHTML;
    } else {
        feedbackEl.textContent = question.explication;
    }
    document.getElementById("quizz-bouton-suivant").textContent = quizzEnConsultation ? "Reprendre" : "Suivant";
    document.getElementById("quizz-feedback").style.display = "";
}

function quizzSuivant() {
    // Sortir de consultation revient exactement à la question de la partie
    // en cours, sans avancer quizzIndex — la consultation n'est qu'une
    // parenthèse, jamais une progression (voir quizzIndexFixeActuel).
    // quizzTermine d'abord : si la partie était déjà finie (quizzIndex a
    // dépassé quizzOrdre.length), rien à réafficher via quizzAfficherQuestion
    // (qui planterait sur quizzOrdre[quizzIndex] hors bornes) — l'écran de
    // fin est la bonne chose à restaurer.
    if (quizzEnConsultation) {
        quizzEnConsultation = false;
        quizzQuestionConsultee = null;
        if (quizzTermine) {
            document.getElementById("quizz-question-zone").style.display = "none";
            document.getElementById("quizz-fin").style.display = "";
        } else {
            quizzAfficherQuestion();
        }
        return;
    }

    quizzIndex++;
    if (quizzIndex >= quizzOrdre.length) {
        quizzTermine = true;
        document.getElementById("quizz-question-zone").style.display = "none";
        document.getElementById("quizz-fin").style.display = "";
        document.getElementById("quizz-score-final").textContent =
            quizzScore + " / " + QUIZZ_QUESTIONS.length + " bonnes réponses.";
    } else {
        quizzAfficherQuestion();
    }
}