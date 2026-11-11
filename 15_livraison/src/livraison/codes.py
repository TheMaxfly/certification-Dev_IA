"""Les codes de sortie nommés de `livrer`.

Une santé rouge n'est pas une panne de la commande : tout a tourné, un service manque.
Elle sort en 1, comme le demande la décision 5 du cadre ; un préalable qui manque, une
construction ou un lever qui échouent ont leur code à part.
"""

from __future__ import annotations

from enum import IntEnum


class Code(IntEnum):
    OK = 0
    SANTE_ROUGE = 1
    PREREQUIS = 20
    CONSTRUCTION = 40
    DEPLOIEMENT = 80
    INTERNE = 90


SENS: dict[Code, str] = {
    Code.OK: "fait, et la santé est verte",
    Code.SANTE_ROUGE: "déployé, mais un contrôle de santé n'est pas tenu",
    Code.PREREQUIS: "un préalable manque (étiquette, arbre, images, autre cible en"
    " marche, fichiers d'environnement, migrations en attente)",
    Code.CONSTRUCTION: "la construction d'une image a échoué",
    Code.DEPLOIEMENT: "la pile ne se lève pas, ou ne répond pas dans le délai",
    Code.INTERNE: "erreur inattendue, ou un journal qui porterait un secret",
}


class Arret(Exception):
    """Une étape s'arrête : son code nommé, et pourquoi."""

    def __init__(self, code: Code, motif: str, sortie: str = "") -> None:
        super().__init__(motif)
        self.code = code
        self.motif = motif
        self.sortie = sortie
