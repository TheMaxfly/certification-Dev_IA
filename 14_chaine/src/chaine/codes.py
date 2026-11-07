"""Les codes de sortie nommés de la chaîne (décisions de l'étape, § 7).

Un refus de la règle n'est pas une panne : toutes les étapes ont tourné, la dernière
a dit non. Le code est non nul parce qu'un orchestrateur sort en erreur quand il
n'aboutit pas, et nommé à part pour qu'on ne le confonde pas avec une panne.
"""

from __future__ import annotations

from enum import IntEnum


class Code(IntEnum):
    PROMOTION_PROPOSEE = 0
    REFUS_REGLE = 10
    PAS_DE_VERDICT = 11
    PREREQUIS = 20
    CANDIDAT_INVALIDE = 21
    DONNEES = 30
    CONSTRUCTION = 40
    ENCODAGE = 50
    TEST_MODELE = 60
    MESURE = 70
    PACKAGING = 80
    INTERNE = 90


SENS: dict[Code, str] = {
    Code.PROMOTION_PROPOSEE: "toutes les étapes tenues, règle tenue ; arrêt avant la"
    " promotion",
    Code.REFUS_REGLE: "la règle n'est pas tenue ; aucune promotion proposée",
    Code.PAS_DE_VERDICT: "la règle refuse de juger (encodage en service, autre"
    " corpus…)",
    Code.PREREQUIS: "un préalable manque (cible, dossier, arbre, candidat, pile,"
    " instance, état de départ, mot de passe)",
    Code.CANDIDAT_INVALIDE: "le fichier de candidat ne suit pas son schéma",
    Code.DONNEES: "le test des données a échoué",
    Code.CONSTRUCTION: "la construction du corpus a échoué",
    Code.ENCODAGE: "l'encodage a échoué, ou n'a rien créé",
    Code.TEST_MODELE: "équivalence de l'encodage sous 0,99",
    Code.MESURE: "la mesure a échoué, ou le rejeu de référence n'est pas identique",
    Code.PACKAGING: "la construction de l'image, ou l'API de préproduction, a échoué",
    Code.INTERNE: "erreur inattendue de l'orchestrateur",
}


class Arret(Exception):
    """Une étape s'arrête : son code nommé, et pourquoi."""

    def __init__(self, code: Code, motif: str, sortie: str = "") -> None:
        super().__init__(motif)
        self.code = code
        self.motif = motif
        self.sortie = sortie
