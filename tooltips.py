"""
Bulles d'aide (tooltips) utilisées par viewer.py : au survol d'un widget
Tkinter quelconque, des en-têtes de colonnes d'un Treeview, ou d'artistes
matplotlib (barres, points de repère...) sur un canvas intégré.
"""
import tkinter as tk


def _positionner_bulle(fenetre, x, y, recalculer=True):
    """Pose la bulle à (x, y), en la ramenant vers la gauche si besoin pour
    qu'elle ne déborde pas de l'écran à droite — sans ça, une bulle proche du
    bord droit (ex: le stat "Gare la + touchée", le plus à droite de la barre
    du haut) peut partir hors champ.
    recalculer=False : saute le update_idletasks() (coûteux) et réutilise la
    largeur déjà connue de la fenêtre — sûr uniquement si son texte n'a pas
    changé depuis le dernier appel avec recalculer=True (la largeur ne peut
    alors pas avoir changé). Ajouté pour SurvolArtistes, qui repositionne la
    bulle à chaque mouvement de souris sur un graphique — bien plus fréquent
    que les autres tooltips — où le update_idletasks() systématique causait
    un vrai retard d'affichage perceptible (repéré par l'utilisateur,
    2026-07-30)."""
    if recalculer:
        fenetre.update_idletasks()
    x = min(x, fenetre.winfo_screenwidth() - fenetre.winfo_reqwidth() - 5)
    fenetre.wm_geometry(f"+{x}+{y}")


def _creer_bulle_aide(master, texte, wraplength=250):
    """Construit une fenêtre-bulle flottante (Toplevel sans bordure + Label
    fond jaune pâle) — le style commun aux 3 mécanismes de bulle d'aide de
    l'appli (TreeviewHeaderTooltips, SimpleTooltip, SurvolArtistes), qui ne
    diffèrent que par *comment* ils détectent le survol et *quand* ils
    créent/détruisent vs. réutilisent cette fenêtre. Le positionnement
    (wm_geometry) reste à la charge de l'appelant, chacun ayant son propre
    décalage par rapport au curseur."""
    fenetre = tk.Toplevel(master)
    fenetre.wm_overrideredirect(True)
    label = tk.Label(
        fenetre, text=texte, background="#ffffe0", relief="solid",
        borderwidth=1, wraplength=wraplength, justify="left", padx=5, pady=3,
    )
    label.pack()
    return fenetre, label


class TreeviewHeaderTooltips:
    """Affiche une bulle d'aide au survol des entêtes de colonnes d'un Treeview.
    textes_par_colonne : {identifiant_colonne: texte du tooltip}."""

    def __init__(self, tree, textes_par_colonne):
        self.tree = tree
        self.textes_par_colonne = textes_par_colonne
        self.tooltip_window = None
        self.colonne_survolee = None
        tree.bind("<Motion>", self._on_motion)
        tree.bind("<Leave>", lambda e: self._hide())

    def _on_motion(self, event):
        if self.tree.identify_region(event.x, event.y) != "heading":
            self._hide()
            return
        colonne = self.tree.identify_column(event.x)
        colonne_id = self.tree.column(colonne, "id")
        texte = self.textes_par_colonne.get(colonne_id)
        if not texte:
            self._hide()
            return
        if colonne_id != self.colonne_survolee:
            self._hide()
            self.colonne_survolee = colonne_id
            self._show(texte, event.x_root, event.y_root)

    def _show(self, texte, x, y):
        self.tooltip_window, _ = _creer_bulle_aide(self.tree, texte)
        _positionner_bulle(self.tooltip_window, x + 12, y + 20)

    def _hide(self):
        if self.tooltip_window is not None:
            self.tooltip_window.destroy()
            self.tooltip_window = None
        self.colonne_survolee = None


class SimpleTooltip:
    """Bulle d'aide au survol d'un widget quelconque (contrairement à
    TreeviewHeaderTooltips, réservée aux en-têtes de colonnes d'un Treeview).
    au_dessus=True affiche la bulle au-dessus du curseur plutôt qu'en dessous
    — utile pour un widget proche du bas de la fenêtre, où une bulle un peu
    longue déborderait sinon de l'écran."""

    def __init__(self, widget, texte, au_dessus=False):
        self.widget = widget
        self.texte = texte
        self.au_dessus = au_dessus
        self.tooltip_window = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, event):
        self.tooltip_window, _ = _creer_bulle_aide(self.widget, self.texte)
        if self.au_dessus:
            # La hauteur réelle n'est connue qu'une fois le contenu du Label
            # posé (wraplength calculé) — update_idletasks() avant de la lire.
            self.tooltip_window.update_idletasks()
            hauteur = self.tooltip_window.winfo_reqheight()
            _positionner_bulle(self.tooltip_window, event.x_root + 12, event.y_root - hauteur - 10)
        else:
            _positionner_bulle(self.tooltip_window, event.x_root + 12, event.y_root + 20)

    def _hide(self, event=None):
        if self.tooltip_window is not None:
            self.tooltip_window.destroy()
            self.tooltip_window = None


class SurvolArtistes:
    """Info-bulle flottante au survol d'artistes matplotlib (barres, points
    de repère...) sur un canvas Tkinter — matplotlib n'a rien d'équivalent en
    natif pour une figure intégrée à Tkinter (contrairement à SimpleTooltip,
    qui suffit pour un widget Tk classique). Une instance par canvas ;
    enregistrer(artiste, texte) avant chaque rendu, vider() au début de
    chaque rendu suivant (les artistes de l'ancien rendu ne sont plus valides
    une fois les axes effacés). Contrairement aux deux classes ci-dessus, la
    fenêtre est créée une seule fois puis réutilisée (montrée/cachée) plutôt
    que détruite à chaque fois : ce survol est déclenché à chaque mouvement
    de souris, bien plus fréquent qu'un simple survol de widget."""

    def __init__(self, master, canvas):
        self.master = master
        self.canvas = canvas
        self.artistes = []
        self.tooltip_window = None
        self.label = None
        canvas.mpl_connect("motion_notify_event", self._on_motion)

    def enregistrer(self, artiste, texte):
        self.artistes.append((artiste, texte))

    def vider(self):
        self.artistes = []

    def _on_motion(self, event):
        for artiste, texte in self.artistes:
            if artiste.axes is event.inaxes and artiste.contains(event)[0]:
                self._afficher(texte, event)
                return
        self._masquer()

    def _afficher(self, texte, event):
        widget = self.canvas.get_tk_widget()
        # event.y est en coordonnées matplotlib (origine en bas) — à inverser
        # pour repasser en coordonnées écran (origine en haut).
        x_root = widget.winfo_rootx() + int(event.x)
        y_root = widget.winfo_rooty() + widget.winfo_height() - int(event.y)
        nouvelle_fenetre = self.tooltip_window is None
        if nouvelle_fenetre:
            self.tooltip_window, self.label = _creer_bulle_aide(self.master, "", wraplength=320)
        # Ne recalcule la largeur (coûteux, voir _positionner_bulle) que si
        # le texte a réellement changé — en glissant sur le même artiste, la
        # bulle n'a pas besoin d'être remesurée à chaque relevé de position.
        texte_a_change = nouvelle_fenetre or texte != self.label.cget("text")
        if texte_a_change:
            self.label.config(text=texte)
        _positionner_bulle(self.tooltip_window, x_root + 12, y_root + 12, recalculer=texte_a_change)
        self.tooltip_window.deiconify()

    def _masquer(self):
        if self.tooltip_window is not None:
            self.tooltip_window.withdraw()
