"""Les formes de l'API — le contrat `/v1/` (rapport de l'étape 2, section B ; route de
réponse : rapport de l'étape 3, section B).

Les bornes et les défauts de la recherche et de la réponse viennent de
`config/reglages.toml` (`lire_contrat`, `lire_reglages_reponse`), lus à l'import : ils
font partie du contrat publié.
"""

from __future__ import annotations

from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from api_modele.configuration import lire_contrat, lire_reglages_reponse

CONTRAT = lire_contrat()
_N = CONTRAT.nombre_series
_P = CONTRAT.passages_par_serie
REPONSE = lire_reglages_reponse()
_T = REPONSE.temperature
_L = REPONSE.longueur_max


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


class Metadonnees(BaseModel):
    """Ce que le catalogue dit d'une série (`manga.v_metadonnees_serie`) ; `null` : il
    ne le sait pas. Une œuvre hors catalogue n'a que des `null`."""

    disponibilite: (
        Literal["edite_en_france", "seulement_au_japon", "inconnue"] | None
    ) = Field(
        description="Édité en France : au moins un volume y est paru, pas qu'il est"
        " en stock."
    )
    nb_volumes: int | None = Field(
        description="Le plus grand du champ « tomes parus » et des numéros de volume"
        " distincts."
    )
    nb_volumes_source: (
        Literal["champ", "repli_volumes_distincts", "divergent"] | None
    ) = Field(description="Toute source autre que `champ` se lit « au moins N ».")
    statut: Literal["termine", "en_cours"] | None
    demographie: str | None = Field(description="Le libellé du catalogue, tel quel.")
    annee_fr: int | None = Field(
        description="L'année du premier volume à EAN français."
    )
    auteurs: str | None = Field(
        description="Le scénariste puis le dessinateur, tels que le catalogue les"
        " écrit, joints par une virgule ; un même nom une seule fois."
    )


class Serie(BaseModel):
    rang: int
    entite: str = Field(description="« serie:<id> » ou « kitsu:<id> », la clé du banc.")
    serie_id: int | None
    kitsu_id: int | None
    titre: str
    score: float = Field(description="Cosinus du meilleur fragment (non arrondi).")
    passages: list[Passage]
    metadonnees: Metadonnees


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


# --------------------------------------------------------------------------- #
#  La route de réponse — `POST /v1/reponse` (E3, étape 3)
# --------------------------------------------------------------------------- #
MotifRefus = Literal["aucun_passage", "hors_catalogue", "inconnu"]
#: Les modes de la réponse : `enrichi` depuis 1.4.0 (ajout à une énumération d'entrée).
Mode = Literal["strict", "enrichi"]
#: Les bornes du complément du mode enrichi (spec 06, B.3).
COMPLEMENT_MAX = 600
COMPLEMENT_TITRES_MAX = 5
COMPLEMENT_TITRE_MAX = 200
MotifHistorique = Literal[
    "trop_de_tours",
    "trop_long",
    "premier_tour_non_libraire",
    "roles_non_alternes",
    "dernier_tour_non_assistant",
]


class Tour(BaseModel):
    """Un tour de la conversation, dans l'ordre."""

    model_config = ConfigDict(extra="forbid", strict=True)

    role: Literal["libraire", "assistant"]
    contenu: Annotated[
        str, Field(min_length=1, max_length=REPONSE.historique_tour_caracteres_max)
    ]


def motif_historique(tours: list[Tour]) -> MotifHistorique | None:
    """Pourquoi un historique est refusé, ou None : au plus 10 tours et 4 000
    caractères, alternés, le premier du libraire, le dernier de l'assistant."""
    if len(tours) > REPONSE.historique_tours_max:
        return "trop_de_tours"
    if sum(len(t.contenu) for t in tours) > REPONSE.historique_caracteres_max:
        return "trop_long"
    if not tours:
        return None
    if tours[0].role != "libraire":
        return "premier_tour_non_libraire"
    if any(a.role == b.role for a, b in zip(tours, tours[1:], strict=False)):
        return "roles_non_alternes"
    if tours[-1].role != "assistant":
        return "dernier_tour_non_assistant"
    return None


class DemandeReponse(BaseModel):
    """Une question, l'historique de la conversation et les réglages de la réponse ;
    un champ inconnu est refusé."""

    model_config = ConfigDict(extra="forbid", strict=True)

    question: Annotated[
        str,
        Field(
            min_length=1,
            max_length=CONTRAT.longueur_question_max,
            description="La question du libraire, dernier tour de la conversation.",
        ),
    ]
    historique: list[Tour] = Field(
        default_factory=list[Tour],
        description="Les tours précédents, dans l'ordre : au plus"
        f" {REPONSE.historique_tours_max} tours et"
        f" {REPONSE.historique_caracteres_max} caractères ; ni gardés ni journalisés.",
    )
    reformuler: bool = Field(
        default=REPONSE.reformuler,
        description="Avec un historique : réécrire la question en question autonome"
        " avant la recherche.",
    )
    profil: Annotated[
        str | None,
        Field(
            max_length=64,
            description="Le profil qui rédige (un profil déclaré) ; le profil actif"
            " par défaut.",
        ),
    ] = None
    temperature: Annotated[float, Field(ge=_T.min, le=_T.max)] = _T.defaut
    longueur_max: Annotated[
        int,
        Field(ge=_L.min, le=_L.max, description="Jetons de sortie au plus."),
    ] = _L.defaut
    mode: Mode = Field(
        default="strict",
        description="strict : la réponse tirée des seuls passages. enrichi (1.4.0) :"
        " la même réponse, plus un complément tiré des connaissances du modèle,"
        " étiqueté « non vérifié » ; sans passage, le modèle est appelé quand même et"
        " la partie stricte refuse.",
    )
    nombre_series: Annotated[int, Field(ge=_N.min, le=_N.max)] = (
        REPONSE.nombre_series.defaut
    )
    passages_par_serie: Annotated[int, Field(ge=_P.min, le=_P.max)] = (
        REPONSE.passages_par_serie.defaut
    )
    catalogue_seul: bool = REPONSE.catalogue_seul
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

    @field_validator("historique")
    @classmethod
    def _historique(cls, tours: list[Tour]) -> list[Tour]:
        motif = motif_historique(tours)
        if motif is not None:
            raise PydanticCustomError(
                "historique_invalide", "historique invalide : {motif}", {"motif": motif}
            )
        return tours


class SerieCitee(BaseModel):
    serie_id: int
    titre: str = Field(description="Le titre du catalogue, jamais celui du modèle.")
    rang: int
    fragment_ids: list[int]


class AppelModele(BaseModel):
    role: Literal["reformulation", "reponse"]
    jetons_entree: int
    jetons_sortie: int
    jetons_raisonnement: int
    estimes: bool = Field(description="Vrai : comptés par caractères (sans `usage`).")
    latence_ms: float
    statut: Literal["ok", "refus_debit"] = Field(
        default="ok",
        description="`refus_debit` : refusé pour débit par le fournisseur (aucun jeton,"
        " non facturé), puis rejoué une fois.",
    )


class ReglagesReponseAppliques(BaseModel):
    temperature: float | None
    longueur_max: int | None
    mode: str
    top_p: float | None
    reasoning_effort: str | None


class Latences(BaseModel):
    recherche: float
    reformulation: float | None
    modele: float | None
    totale: float


class ContexteReponse(Contexte):
    """Le contexte de la recherche, plus le modèle de langage et ses appels."""

    profil: str
    fournisseur: str
    modele_langage: str = Field(description="L'identifiant du modèle de langage.")
    statut_profil: Literal["stable", "apercu"]
    date_appel: str | None = Field(
        description="Le dernier appel au modèle (UTC, ISO 8601) ; null sans appel."
    )
    reglages_appliques: ReglagesReponseAppliques
    reglages_absents: list[str]
    question_reformulee: str | None
    reformulation_ignoree: Literal["trop_longue"] | None
    appels: list[AppelModele]
    cout_estime_usd: float | None = Field(
        description="La somme des appels, en dollars ; null si un prix n'est pas"
        " déclaré."
    )
    latences_ms: Latences


def _nul(valeur: object) -> bool:
    return valeur is None


class Reponse(BaseModel):
    """La réponse ; en mode enrichi, le complément s'y ajoute. Les trois champs du
    complément sont **absents** quand il n'y en a pas (mode strict, complément vide) :
    une réponse stricte reste celle de 1.3.1, champ pour champ."""

    refus: bool
    motif_refus: MotifRefus | None
    reponse: str
    series_citees: list[SerieCitee]
    series: list[Serie]
    contexte: ContexteReponse
    complement: str | None = Field(
        default=None,
        exclude_if=_nul,
        description="Mode enrichi : ce que le modèle ajoute de ses connaissances"
        " générales, sans source, NON VÉRIFIÉ ; contrôlé (aucun nom de membre, aucune"
        " disponibilité ni métadonnée affirmée sur une série du catalogue, aucune"
        " marque), mais il peut être faux sur une œuvre hors catalogue. Absent en"
        " strict et quand le modèle n'a rien ajouté. N'entre pas dans l'historique.",
    )
    complement_titres: list[str] | None = Field(
        default=None,
        exclude_if=_nul,
        description="Les titres que le complément nomme (cinq au plus), tels que le"
        " modèle les écrit ; présent avec le complément.",
    )
    complement_etiquette: str | None = Field(
        default=None,
        exclude_if=_nul,
        description="Texte fixe, présent dès qu'un complément l'est : à afficher avec"
        " lui.",
    )


# La sortie du modèle, contrainte par son schéma (rapport de l'étape 3, section D)
# et relue ici, strictement, avant tout contrôle de sens.
class CiteeParLeModele(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    serie_id: int
    fragment_ids: Annotated[list[int], Field(max_length=5)]


class SortieModele(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    refus: bool
    motif_refus: MotifRefus | None
    reponse: Annotated[str, Field(min_length=1, max_length=4000)]
    series_citees: Annotated[list[CiteeParLeModele], Field(max_length=50)]


class SortieModeleEnrichi(SortieModele):
    """La sortie du mode enrichi : les quatre champs stricts, inchangés, plus le
    complément ; vide ou absent, il est valide."""

    complement: Annotated[str | None, Field(max_length=COMPLEMENT_MAX)] = None
    complement_titres: (
        Annotated[
            list[Annotated[str, Field(max_length=COMPLEMENT_TITRE_MAX)]],
            Field(max_length=COMPLEMENT_TITRES_MAX),
        ]
        | None
    ) = None


class SortieReformulation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    question: Annotated[str, Field(min_length=1)]

    @field_validator("question")
    @classmethod
    def _non_vide(cls, valeur: str) -> str:
        if not valeur.strip():
            raise PydanticCustomError("vide", "la question reformulée est vide")
        return valeur


#: Le schéma de la sortie, envoyé au fournisseur (`response_format`, strict).
SCHEMA_SORTIE: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["refus", "motif_refus", "reponse", "series_citees"],
    "properties": {
        "refus": {"type": "boolean"},
        "motif_refus": {
            "type": ["string", "null"],
            "enum": ["aucun_passage", "hors_catalogue", "inconnu", None],
        },
        "reponse": {"type": "string", "minLength": 1, "maxLength": 4000},
        "series_citees": {
            "type": "array",
            "maxItems": 50,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["serie_id", "fragment_ids"],
                "properties": {
                    "serie_id": {"type": "integer"},
                    "fragment_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "maxItems": 5,
                    },
                },
            },
        },
    },
}
#: Le schéma de la sortie du mode enrichi : celui du strict, plus le complément
#: (borné), tous deux requis et nuls admis (forme des schémas stricts).
SCHEMA_SORTIE_ENRICHI: dict[str, object] = {
    **SCHEMA_SORTIE,
    "required": [
        "refus",
        "motif_refus",
        "reponse",
        "series_citees",
        "complement",
        "complement_titres",
    ],
    "properties": {
        **cast(dict[str, object], SCHEMA_SORTIE["properties"]),
        "complement": {"type": ["string", "null"], "maxLength": COMPLEMENT_MAX},
        "complement_titres": {
            "type": ["array", "null"],
            "maxItems": COMPLEMENT_TITRES_MAX,
            "items": {"type": "string", "maxLength": COMPLEMENT_TITRE_MAX},
        },
    },
}
#: Le schéma de la reformulation : une question autonome.
SCHEMA_REFORMULATION: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["question"],
    "properties": {"question": {"type": "string", "maxLength": 500}},
}
