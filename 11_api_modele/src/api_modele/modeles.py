"""Les formes des réponses — le contrat `/v1/` (rapport de l'étape 2, section B)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Sante(BaseModel):
    """Vivant ; base et service joignables ; ce qui est servi est toujours en service.

    Des états seulement : ni version ni modèle (route sans clé)."""

    statut: Literal["ok", "degrade"]
    base: Literal["ok", "injoignable"]
    service_embedding: Literal["ok", "injoignable", "incoherent"]
    encodage: Literal["ok", "change", "inconnu"] = Field(
        description="ok : l'encodage chargé au démarrage est toujours celui de la vue ;"
        " change : une promotion ou un retour arrière a eu lieu depuis (redémarrer) ;"
        " inconnu : la base ne répond pas."
    )


class BorneEntiere(BaseModel):
    defaut: int
    min: int
    max: int


class BorneBooleenne(BaseModel):
    defaut: bool


class ReglagesRecherche(BaseModel):
    nombre_series: BorneEntiere
    passages_par_serie: BorneEntiere
    catalogue_seul: BorneBooleenne


class Question(BaseModel):
    longueur_max_caracteres: int


class Info(BaseModel):
    """Ce que l'API sert : modèle, encodage, corpus, réglages et leurs bornes."""

    version_api: str
    modele: str
    revision: str
    precision: str
    dimension: int
    outil: str
    outil_version: str
    corpus_id: str
    encodage_id: int
    nb_fragments: int
    reglages: ReglagesRecherche
    question: Question
    debit_par_minute: int
