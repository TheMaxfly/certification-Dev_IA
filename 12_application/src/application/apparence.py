"""L'apparence de l'application (E4, lot 7, story AP1) : la couleur de chacun des six
espaces, l'intensité du lavis et la matière, réglées par l'administrateur.

- **Les espaces restent des espaces** : un gabarit déclare son espace d'origine
  (`{% block espace %}sakura{% endblock %}`, lot 6) ; au rendu, l'espace est traduit
  en la couleur réglée pour lui.
- **Liste blanche, puis dictionnaire de constantes** : une valeur lue en base ou reçue
  d'un formulaire est d'abord validée contre sa liste fermée, puis traduite par un
  dictionnaire dont les valeurs sont écrites ici — jamais concaténée dans une classe ou
  un chemin. Aucune valeur de l'utilisateur n'atteint le CSS (mutations `E4P-01`,
  `E4P-02`).
- **Sans réglage** : la couleur d'origine de chaque espace, 85 %, aquarelle — le rendu
  du lot 6 à l'identique (`esp-<espace>`, et rien d'autre).

Les clés de `application.reglages` (migration 027) : `apparence_<espace>` (la couleur
posée sur l'espace qui porte ce nom d'origine), `apparence_intensite`,
`apparence_matiere`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from application import journal

#: Les six espaces, par leur nom d'origine (la couleur que le lot 6 leur a donnée).
ESPACES = ("menthe", "ciel", "sakura", "yuzu", "glycine", "brume")
#: Les six pigments du paquet Senpai.
COULEURS = ESPACES
INTENSITES = ("60", "70", "85", "100")
MATIERES = ("aquarelle", "aplat")
INTENSITE_DEFAUT = "85"
MATIERE_DEFAUT = "aquarelle"
#: L'espace d'un gabarit qui n'en déclarerait pas un connu : celui de `base.html`.
ESPACE_DE_REPLI = "ciel"

#: Les clés de réglage, dans l'ordre de l'écran.
CLE_ESPACE = {e: f"apparence_{e}" for e in ESPACES}
CLE_INTENSITE = "apparence_intensite"
CLE_MATIERE = "apparence_matiere"
CLES = (*CLE_ESPACE.values(), CLE_INTENSITE, CLE_MATIERE)

#: La couleur → sa classe sur `<body>` (section 2 de `senpai.css`).
CLASSE_COULEUR = {
    "menthe": "esp-menthe",
    "ciel": "esp-ciel",
    "sakura": "esp-sakura",
    "yuzu": "esp-yuzu",
    "glycine": "esp-glycine",
    "brume": "esp-brume",
}
#: L'intensité → sa classe ; 85 % est le défaut de la feuille, sans classe.
CLASSE_INTENSITE = {
    "60": "intensite-60",
    "70": "intensite-70",
    "85": "",
    "100": "intensite-100",
}
#: La matière → sa classe ; l'aquarelle est le défaut, sans classe.
CLASSE_MATIERE = {"aquarelle": "", "aplat": "matiere-aplat"}
#: La couleur → la touche du paquet, la pastille de l'écran « Apparence ».
TOUCHE_COULEUR = {
    "menthe": "/statique/touches/touche-menthe.webp",
    "ciel": "/statique/touches/touche-ciel.webp",
    "sakura": "/statique/touches/touche-sakura.webp",
    "yuzu": "/statique/touches/touche-yuzu.webp",
    "glycine": "/statique/touches/touche-glycine.webp",
    "brume": "/statique/touches/touche-brume.webp",
}


@dataclass(frozen=True, slots=True)
class Apparence:
    """L'apparence réglée ; immuable. `couleurs` : espace d'origine → couleur."""

    couleurs: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({e: e for e in ESPACES})
    )
    intensite: str = INTENSITE_DEFAUT
    matiere: str = MATIERE_DEFAUT

    def valeurs(self) -> dict[str, str]:
        """Les huit clés de réglage et leur valeur (l'écriture, la comparaison)."""
        return {
            **{CLE_ESPACE[e]: self.couleurs[e] for e in ESPACES},
            CLE_INTENSITE: self.intensite,
            CLE_MATIERE: self.matiere,
        }


DEFAUT = Apparence()


def valider(cle: str, valeur: object) -> str | None:
    """La valeur si elle est dans la liste fermée de sa clé ; None sinon."""
    if cle in CLE_ESPACE.values():
        admises: tuple[str, ...] = COULEURS
    elif cle == CLE_INTENSITE:
        admises = INTENSITES
    elif cle == CLE_MATIERE:
        admises = MATIERES
    else:
        return None
    return valeur if isinstance(valeur, str) and valeur in admises else None


def depuis_reglages(lignes: Mapping[str, str]) -> Apparence:
    """L'apparence d'après les réglages lus ; une valeur absente ou hors liste : le
    défaut de cette clé seule. Une valeur hors liste se journalise par sa clé (jamais la
    valeur : elle peut porter n'importe quoi)."""
    defauts = DEFAUT.valeurs()
    retenues: dict[str, str] = {}
    for cle in CLES:
        if cle not in lignes:
            retenues[cle] = defauts[cle]
            continue
        valide = valider(cle, lignes[cle])
        if valide is None:
            journal.evenement("apparence_valeur_ignoree", {"cle": cle}, logging.WARNING)
            retenues[cle] = defauts[cle]
        else:
            retenues[cle] = valide
    return Apparence(
        couleurs=MappingProxyType({e: retenues[CLE_ESPACE[e]] for e in ESPACES}),
        intensite=retenues[CLE_INTENSITE],
        matiere=retenues[CLE_MATIERE],
    )


def classe_couleur(couleur: str, repli: str) -> str:
    """La classe de `<body>` d'une couleur, par le dictionnaire ; hors liste, celle du
    repli (l'espace d'origine)."""
    return CLASSE_COULEUR.get(couleur, CLASSE_COULEUR[repli])


def touche(couleur: str) -> str:
    """Le chemin de la touche d'une couleur (la pastille de l'écran « Apparence »), par
    le dictionnaire ; hors liste, celle de l'espace de repli."""
    return TOUCHE_COULEUR.get(couleur, TOUCHE_COULEUR[ESPACE_DE_REPLI])


def classes_body(apparence: Apparence, espace_du_gabarit: object) -> str:
    """Les classes de `<body>` : la couleur réglée de l'espace du gabarit, puis
    l'intensité et la matière si elles diffèrent du défaut — par dictionnaires de
    constantes seulement."""
    espace = str(espace_du_gabarit).strip()
    if espace not in ESPACES:
        espace = ESPACE_DE_REPLI
    couleur = apparence.couleurs.get(espace, espace)
    classes = (
        classe_couleur(couleur, espace),
        CLASSE_INTENSITE.get(apparence.intensite, ""),
        CLASSE_MATIERE.get(apparence.matiere, ""),
    )
    return " ".join(c for c in classes if c)


def partages(apparence: Apparence) -> list[tuple[str, list[str]]]:
    """Les couleurs posées sur plusieurs espaces, dans l'ordre des couleurs : le repère
    de page s'affaiblit (guide § 7, décision 6 : un avertissement, pas un refus)."""
    resultat: list[tuple[str, list[str]]] = []
    for couleur in COULEURS:
        espaces = [e for e in ESPACES if apparence.couleurs[e] == couleur]
        if len(espaces) > 1:
            resultat.append((couleur, espaces))
    return resultat
