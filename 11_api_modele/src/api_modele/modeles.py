"""Les formes de l'API — le contrat `/v1/` (rapport de l'étape 2, section B).

Les bornes et les défauts de la recherche viennent de `config/reglages.toml`
(`lire_contrat`), lus à l'import : ils font partie du contrat publié.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from api_modele.configuration import lire_contrat

CONTRAT = lire_contrat()
_N = CONTRAT.nombre_series
_P = CONTRAT.passages_par_serie


class Recherche(BaseModel):
    """Une question et ses réglages ; rien d'autre (un champ inconnu est refusé)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    question: Annotated[
        str,
        Field(
            min_length=1,
            max_length=CONTRAT.longueur_question_max,
            description="Le texte de la question, encodé tel quel.",
        ),
    ]
    nombre_series: Annotated[int, Field(ge=_N.min, le=_N.max)] = _N.defaut
    passages_par_serie: Annotated[int, Field(ge=_P.min, le=_P.max)] = _P.defaut
    catalogue_seul: bool = CONTRAT.catalogue_seul
    identite: Annotated[
        str | None,
        Field(
            min_length=1,
            max_length=64,
            pattern=r"^[A-Za-z0-9._@:-]+$",
            description="Recopiée dans le journal, sans effet sur la réponse.",
        ),
    ] = None

    @field_validator("question")
    @classmethod
    def _non_vide(cls, valeur: str) -> str:
        if not valeur.strip():
            raise PydanticCustomError("vide", "la question est vide")
        return valeur


TypeDocument = Literal["critique", "resume_serie", "synopsis_kitsu"]


class Passage(BaseModel):
    fragment_id: int
    doc_key: str
    type_document: TypeDocument
    score: float
    extrait: str


class Serie(BaseModel):
    rang: int
    entite: str = Field(description="« serie:<id> » ou « kitsu:<id> », la clé du banc.")
    serie_id: int | None
    kitsu_id: int | None
    titre: str
    score: float = Field(description="Cosinus du meilleur fragment (non arrondi).")
    passages: list[Passage]


class ReglagesAppliques(BaseModel):
    nombre_series: int
    passages_par_serie: int
    catalogue_seul: bool


class Contexte(BaseModel):
    reglages: ReglagesAppliques
    modele: str
    revision: str
    precision: str
    corpus_id: str
    encodage_id: int
    identifiant_requete: str
    duree_ms: float
    version_api: str


class Resultat(BaseModel):
    series: list[Serie]
    contexte: Contexte


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


class BorneReelle(BaseModel):
    defaut: float
    min: float
    max: float


class ModeDecrit(BaseModel):
    defaut: str
    valeurs: list[str]


class HistoriqueDecrit(BaseModel):
    tours_max: int
    caracteres_max: int


class ReglagesReponse(BaseModel):
    """Les réglages de la route de réponse : défauts et bornes (`reglages.toml`)."""

    temperature: BorneReelle
    longueur_max: BorneEntiere
    mode: ModeDecrit
    historique: HistoriqueDecrit
    reformuler: BorneBooleenne
    nombre_series: BorneEntiere
    passages_par_serie: BorneEntiere
    catalogue_seul: BorneBooleenne


class ProfilDecrit(BaseModel):
    nom: str
    fournisseur: str
    modele: str = Field(description="L'identifiant du modèle chez le fournisseur.")
    statut: Literal["stable", "apercu"]
    disponible: bool = Field(
        description="La clé du profil est présente (et, si le contrôle d'exploitation"
        " a tourné au démarrage, le modèle figure chez le fournisseur)."
    )
    reglages_absents: list[str] = Field(
        description="Les réglages que ce profil n'expose pas : les demander est refusé."
    )
    variable_cle: str = Field(
        description="Le NOM de la variable d'environnement de la clé, jamais sa valeur."
    )


class ModeleLangage(BaseModel):
    """Le modèle de langage : le profil qui rédige, les profils déclarés, les
    réglages de la réponse. Distinct de `modele`, le modèle d'embedding."""

    profil_actif: str
    profils: list[ProfilDecrit]
    reglages_reponse: ReglagesReponse


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
    modele_langage: ModeleLangage
