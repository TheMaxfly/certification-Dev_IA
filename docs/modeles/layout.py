"""Modele de mise en page des planches — geometrie, familles, routage.

Un SEUL modele geometrique alimente le `.drawio` et les PNG : les deux sorties
ne peuvent pas diverger, puisqu'elles lisent les memes objets `Boite` et `Lien`.

Contraintes dures appliquees ici, et verifiees par `verifier_disposition()` :
  - toutes les positions sont alignees sur une grille de 20 px ;
  - au moins 60 px de gouttiere entre deux boites ;
  - aucun lien ne traverse une boite ;
  - liens orthogonaux, ancres explicites sur les bords.
"""

from __future__ import annotations

from dataclasses import dataclass, field

GRILLE = 20
GOUTTIERE_MIN = 60

# A3 paysage a 144 dpi : 2380 x 1683. On dessine dans cette page, une planche
# par onglet, pour rester imprimable en A3 sans reduction illisible.
PAGE_L, PAGE_H = 2800, 1980

HAUTEUR_ENTETE = 30
HAUTEUR_LIGNE = 18
MARGE_BOITE = 8


@dataclass(frozen=True)
class Famille:
    cle: str
    libelle: str
    fond: str
    entete: str


FAMILLES: dict[str, Famille] = {
    "coeur": Famille("coeur", "Coeur d'identite", "#E8EAF6", "#9FA8DA"),
    "journal": Famille("journal", "Journal des decisions", "#F3E5F5", "#CE93D8"),
    "ms": Famille("ms", "Manga Sanctuary (socle catalogue)", "#E3F2FD", "#90CAF9"),
    "wd": Famille("wd", "Wikidata (pivot d'identifiants)", "#E8F5E9", "#A5D6A7"),
    "kitsu": Famille("kitsu", "Kitsu (enrichissement)", "#FFF8E1", "#FFD54F"),
    "mi": Famille("mi", "Manga Insight (corpus de comparaison)", "#FCE4EC", "#F48FB1"),
}


@dataclass
class Colonne:
    nom: str
    type_court: str
    pk: bool = False
    fk: bool = False
    unique: bool = False
    non_nul: bool = False

    def libelle(self) -> str:
        """`PK`/`FK`/`U` en prefixe, ` *` en suffixe si NOT NULL."""
        marques = []
        if self.pk:
            marques.append("PK")
        if self.fk:
            marques.append("FK")
        if self.unique and not self.pk:
            marques.append("U")
        prefixe = " ".join(marques)
        prefixe = f"{prefixe} " if prefixe else ""
        etoile = " *" if self.non_nul else ""
        if not self.type_court:  # MCD : un attribut conceptuel n'a pas de type
            return f"{prefixe}{self.nom}{etoile}"
        return f"{prefixe}{self.nom} : {self.type_court}{etoile}"

    def html(self) -> str:
        """Meme libelle, avec la typographie des cles (gras/souligne, italique).

        Le texte est rendu BRUT : c'est l'ecriture dans l'attribut XML qui
        l'echappe une fois et une seule. Echapper ici produirait un `&amp;lt;b&amp;gt;`
        affiche tel quel dans draw.io.
        """
        texte = self.libelle()
        if self.pk:
            return f"<b><u>{texte}</u></b>"
        if self.fk:
            return f"<i>{texte}</i>"
        return texte


@dataclass
class Boite:
    cle: str  # schema.table
    titre: str
    famille: str
    x: int
    y: int
    largeur: int
    colonnes: list[Colonne] = field(default_factory=list)
    note: str = ""  # ex. "… (61 autres colonnes)"
    est_vue: bool = False

    @property
    def hauteur(self) -> int:
        lignes = len(self.colonnes) + (1 if self.note else 0)
        return HAUTEUR_ENTETE + lignes * HAUTEUR_LIGNE + MARGE_BOITE

    @property
    def x2(self) -> int:
        return self.x + self.largeur

    @property
    def y2(self) -> int:
        return self.y + self.hauteur

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x + self.largeur / 2, self.y + self.hauteur / 2)

    def chevauche(self, autre: Boite, marge: int = 0) -> bool:
        return not (
            self.x2 + marge <= autre.x
            or autre.x2 + marge <= self.x
            or self.y2 + marge <= autre.y
            or autre.y2 + marge <= self.y
        )


@dataclass
class Lien:
    source: str
    cible: str
    etiquette: str = ""
    cardinalite_source: str = ""
    cardinalite_cible: str = ""
    applicatif: bool = False  # trait pointille : lien sans contrainte FK
    vue: bool = False
    # Surcharges de routage. La face de sortie choisie automatiquement est la
    # bonne dans la majorite des cas ; ces champs servent aux liens longs qui
    # doivent contourner le corridor central au lieu de le traverser.
    ancre_source: tuple[float, float] | None = None
    ancre_cible: tuple[float, float] | None = None
    decalage: int = 0  # decale le corridor (px) pour ne pas superposer 2 liens


def _echapper(texte: str) -> str:
    return (
        texte.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------- routage ---
def ancres(a: Boite, b: Boite) -> tuple[tuple[float, float], tuple[float, float]]:
    """Bords d'attache : on sort par la face qui regarde la cible.

    Renvoie deux couples (x, y) en coordonnees relatives (0..1), tels que
    draw.io les attend dans exitX/exitY et entryX/entryY.
    """
    ax, ay = a.centre
    bx, by = b.centre
    dx, dy = bx - ax, by - ay
    if abs(dx) >= abs(dy):
        return ((1.0, 0.5), (0.0, 0.5)) if dx > 0 else ((0.0, 0.5), (1.0, 0.5))
    return ((0.5, 1.0), (0.5, 0.0)) if dy > 0 else ((0.5, 0.0), (0.5, 1.0))


def point_absolu(boite: Boite, ancre: tuple[float, float]) -> tuple[float, float]:
    return (boite.x + ancre[0] * boite.largeur, boite.y + ancre[1] * boite.hauteur)


def ancres_du_lien(
    a: Boite, b: Boite, lien: Lien | None = None
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Ancres effectives : celles du lien si elles sont imposees, sinon
    celles que la geometrie suggere."""
    auto_a, auto_b = ancres(a, b)
    if lien is None:
        return auto_a, auto_b
    return (lien.ancre_source or auto_a, lien.ancre_cible or auto_b)


def route(a: Boite, b: Boite, lien: Lien | None = None) -> list[tuple[float, float]]:
    """Polyligne orthogonale en Z : sortie -> coude -> coude -> entree.

    C'est la route que draw.io produit avec `orthogonalEdgeStyle` et des ancres
    explicites ; la calculer ici permet de COMPTER les croisements au lieu de
    les affirmer.
    """
    anc_a, anc_b = ancres_du_lien(a, b, lien)
    decalage = lien.decalage if lien else 0
    p1 = point_absolu(a, anc_a)
    p2 = point_absolu(b, anc_b)
    if anc_a[0] in (0.0, 1.0):  # sortie horizontale -> corridor vertical
        milieu = (p1[0] + p2[0]) / 2 + decalage
        return [p1, (milieu, p1[1]), (milieu, p2[1]), p2]
    milieu = (p1[1] + p2[1]) / 2 + decalage  # sortie verticale -> corridor horizontal
    return [p1, (p1[0], milieu), (p2[0], milieu), p2]


def _segments(points: list[tuple[float, float]]) -> list[tuple]:
    return list(zip(points, points[1:], strict=False))


def _croisent(s1: tuple, s2: tuple) -> bool:
    """Intersection stricte de deux segments orthogonaux (l'un horizontal,
    l'autre vertical). Les contacts en extremite ne comptent pas : deux liens
    qui partent du meme bord se touchent sans se croiser."""
    (x1, y1), (x2, y2) = s1
    (x3, y3), (x4, y4) = s2
    h1, h2 = y1 == y2, y3 == y4
    if h1 == h2:
        return False
    if h2:
        (x1, y1), (x2, y2), (x3, y3), (x4, y4) = (x3, y3), (x4, y4), (x1, y1), (x2, y2)
    # s1 horizontal en y1 de x1..x2 ; s2 vertical en x3 de y3..y4
    xmin, xmax = sorted((x1, x2))
    ymin, ymax = sorted((y3, y4))
    return xmin < x3 < xmax and ymin < y1 < ymax


def compter_croisements(boites: dict[str, Boite], liens: list[Lien]) -> list[tuple]:
    """Paires de liens dont les traces se croisent reellement."""
    routes = {}
    for i, lien in enumerate(liens):
        if lien.source in boites and lien.cible in boites:
            routes[i] = _segments(route(boites[lien.source], boites[lien.cible], lien))
    croisements = []
    indices = sorted(routes)
    for pos, i in enumerate(indices):
        for j in indices[pos + 1 :]:
            if liens[i].source in (liens[j].source, liens[j].cible) or liens[
                i
            ].cible in (liens[j].source, liens[j].cible):
                continue  # liens partageant une boite : contact, pas croisement
            if any(_croisent(s1, s2) for s1 in routes[i] for s2 in routes[j]):
                croisements.append(
                    (
                        f"{liens[i].source}->{liens[i].cible}",
                        f"{liens[j].source}->{liens[j].cible}",
                    )
                )
    return croisements


def liens_traversant_une_boite(
    boites: dict[str, Boite], liens: list[Lien]
) -> list[tuple[str, str]]:
    """Un lien qui passe a travers une boite tierce est un defaut de mise en page."""
    fautifs = []
    for lien in liens:
        if lien.source not in boites or lien.cible not in boites:
            continue
        trace = _segments(route(boites[lien.source], boites[lien.cible], lien))
        for cle, boite in boites.items():
            if cle in (lien.source, lien.cible):
                continue
            for (xa, ya), (xb, yb) in trace:
                if ya == yb:  # segment horizontal
                    if (
                        boite.y < ya < boite.y2
                        and min(xa, xb) < boite.x2
                        and boite.x < max(xa, xb)
                    ):
                        fautifs.append((f"{lien.source}->{lien.cible}", cle))
                        break
                elif (
                    boite.x < xa < boite.x2
                    and min(ya, yb) < boite.y2
                    and boite.y < max(ya, yb)
                ):
                    fautifs.append((f"{lien.source}->{lien.cible}", cle))
                    break
            else:
                continue
            break
    return fautifs


def verifier_disposition(boites: dict[str, Boite], liens: list[Lien]) -> dict:
    """Controle les contraintes dures et rend un rapport exploitable."""
    valeurs = list(boites.values())
    hors_grille = [
        b.cle for b in valeurs if b.x % GRILLE or b.y % GRILLE or b.largeur % GRILLE
    ]
    chevauchements, gouttieres = [], []
    for i, a in enumerate(valeurs):
        for b in valeurs[i + 1 :]:
            if a.chevauche(b):
                chevauchements.append((a.cle, b.cle))
            elif a.chevauche(b, marge=GOUTTIERE_MIN):
                gouttieres.append((a.cle, b.cle))
    debordements = [
        b.cle for b in valeurs if b.x < 0 or b.y < 0 or b.x2 > PAGE_L or b.y2 > PAGE_H
    ]
    return {
        "hors_grille": hors_grille,
        "chevauchements": chevauchements,
        "gouttieres_insuffisantes": gouttieres,
        "debordements": debordements,
        "croisements": compter_croisements(boites, liens),
        "traversees": liens_traversant_une_boite(boites, liens),
    }
