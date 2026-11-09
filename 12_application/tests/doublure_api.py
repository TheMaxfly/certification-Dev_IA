"""La doublure de l'API du modèle : une application de test, jamais empaquetée.

Elle sert `/v1/info`, `/v1/sante` et `/v1/reponse` avec des réponses construites ici, et
**validées contre le contrat** du module 11, chargé par son chemin : une réponse ou une
requête hors contrat est un écart, noté et rendu en 500 ; un code d'erreur que le
contrat n'énumère pas aussi. Seuls les corps hors contrat et les scénarios sans
réponse y échappent, exprès.

Elle sert aussi le **catalogue** — l'API data du module 02, `GET /series`,
`GET /series/{series_id}`, `GET /series/{series_id}/volumes` (E4, lot 3) et `/live` —,
chaque réponse validée contre le contrat du 02 (`02_api_manga/openapi.json`, second
contrat, même mécanisme), sous une clé distincte
(sans clé : 401, comme le 02). Elle compte ses appels par route, `/series/{id}/reviews`
comprise : l'application ne doit jamais l'appeler.

Un seul mécanisme de pilotage : `POST /_essai/scenario`, qui choisit `{info, sante,
reponse, catalogue}` ; il vaut en mémoire (`httpx.ASGITransport`) comme sur un port du
bouclage (audit d'accessibilité, Compose). L'application n'en sait rien, et aucune
question ne porte de commande. Noms et textes factices : aucune donnée réelle, aucun
pseudonyme.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from jsonschema import Draft202012Validator

CONTRAT_CHEMIN = (
    Path(__file__).resolve().parents[2]
    / "11_api_modele"
    / "contrat"
    / "openapi_v1.json"
)
CONTRAT: dict[str, Any] = json.loads(CONTRAT_CHEMIN.read_text(encoding="utf-8"))
CLE_ATTENDUE = "a" * 40
CONTRAT_CATALOGUE_CHEMIN = (
    Path(__file__).resolve().parents[2] / "02_api_manga" / "openapi.json"
)
CONTRAT_CATALOGUE: dict[str, Any] = json.loads(
    CONTRAT_CATALOGUE_CHEMIN.read_text(encoding="utf-8")
)
#: La clé de l'application auprès du catalogue : distincte de celle de l'API du modèle.
CLE_CATALOGUE_ATTENDUE = "c" * 40


def validateur(schema: Mapping[str, Any]) -> Draft202012Validator:
    """Un schéma du contrat ; ses `$ref` se résolvent dans `components`."""
    return Draft202012Validator({**schema, "components": CONTRAT["components"]})


def schema_de_reponse(
    route: str, methode: str, statut: int
) -> Mapping[str, Any] | None:
    reponses = CONTRAT["paths"][route][methode]["responses"]
    decrite = reponses.get(str(statut))
    return None if decrite is None else decrite["content"]["application/json"]["schema"]


def codes_du_contrat(route: str, methode: str, statut: int) -> set[str]:
    """Les codes d'erreur que le contrat énumère pour ce statut (accents graves)."""
    decrite = CONTRAT["paths"][route][methode]["responses"].get(str(statut), {})
    return set(re.findall(r"`([a-z_]+)`", decrite.get("description", "")))


def schema_erreur() -> Mapping[str, Any]:
    return {"$ref": "#/components/schemas/Erreur"}


def schema_demande() -> Mapping[str, Any]:
    return {"$ref": "#/components/schemas/DemandeReponse"}


@dataclass
class Rendu:
    """Ce qu'un scénario rend : statut, corps, en-têtes ; ou un corps brut."""

    statut: int = 200
    corps: dict[str, Any] | None = None
    entetes: dict[str, str] = field(default_factory=dict[str, str])
    brut: bytes | None = None
    #: Attendre avant de répondre : le délai du client doit être dépassé.
    attente_s: float = 0.0


# --------------------------------------------------------------------------- #
#  Les corps, tels que le contrat les décrit
# --------------------------------------------------------------------------- #
def profil(
    nom: str,
    fournisseur: str = "mistral",
    statut: str = "stable",
    disponible: bool = True,
    absents: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "nom": nom,
        "fournisseur": fournisseur,
        "modele": f"modele-de-{nom}",
        "statut": statut,
        "disponible": disponible,
        "reglages_absents": absents or [],
        "variable_cle": f"{fournisseur.upper()}_CLE_FACTICE",
    }


def borne(defaut: int, minimum: int, maximum: int) -> dict[str, int]:
    return {"defaut": defaut, "min": minimum, "max": maximum}


def info(
    profils: list[dict[str, Any]] | None = None,
    actif: str = "mistral-large-2512",
    modes: list[str] | None = None,
    tours_max: int = 10,
    caracteres_max: int = 4000,
    question_max: int = 500,
    temperature_max: float = 1.0,
) -> dict[str, Any]:
    if profils is None:
        profils = [
            profil("mistral-large-2512"),
            profil("mistral-large-4", statut="apercu"),
            profil("gpt-6-luna", "openai"),
            profil("gpt-6-sol", "openai"),
        ]
    return {
        "version_api": "1.4.0",
        "modele": "modele-embedding-factice",
        "revision": "0" * 8,
        "precision": "float32",
        "dimension": 768,
        "outil": "outil-factice",
        "outil_version": "0",
        "corpus_id": "v2",
        "encodage_id": 3,
        "nb_fragments": 100,
        "reglages": {
            "nombre_series": borne(10, 1, 50),
            "passages_par_serie": borne(1, 1, 5),
            "catalogue_seul": {"defaut": True},
        },
        "question": {"longueur_max_caracteres": question_max},
        "debit_par_minute": 60,
        "modele_langage": {
            "profil_actif": actif,
            "profils": profils,
            "reglages_reponse": {
                "temperature": {"defaut": 0.0, "min": 0.0, "max": temperature_max},
                "longueur_max": borne(400, 50, 4000),
                "mode": {"defaut": "strict", "valeurs": modes or ["strict"]},
                "historique": {
                    "tours_max": tours_max,
                    "caracteres_max": caracteres_max,
                },
                "reformuler": {"defaut": True},
                "nombre_series": borne(5, 1, 50),
                "passages_par_serie": borne(2, 1, 5),
                "catalogue_seul": {"defaut": True},
            },
        },
    }


def passage(
    fragment_id: int, type_document: str = "critique", score: float = 0.5
) -> dict[str, Any]:
    return {
        "fragment_id": fragment_id,
        "doc_key": f"doc-factice-{fragment_id}",
        "type_document": type_document,
        "score": score,
        "extrait": f"Passage factice n° {fragment_id}.",
    }


def metadonnees(**valeurs: Any) -> dict[str, Any]:
    return {
        "disponibilite": "edite_en_france",
        "nb_volumes": 12,
        "nb_volumes_source": "champ",
        "statut": "termine",
        "demographie": "Seinen",
        "annee_fr": 2010,
        "auteurs": "Auteur factice",
        **valeurs,
    }


def serie(
    serie_id: int,
    titre: str,
    passages: list[dict[str, Any]],
    rang: int = 1,
    score: float = 0.5,
    **meta: Any,
) -> dict[str, Any]:
    return {
        "rang": rang,
        "entite": f"serie:{serie_id}",
        "serie_id": serie_id,
        "kitsu_id": None,
        "titre": titre,
        "score": score,
        "passages": passages,
        "metadonnees": metadonnees(**meta),
    }


def appel(role: str = "reponse", **valeurs: Any) -> dict[str, Any]:
    return {
        "role": role,
        "jetons_entree": 100,
        "jetons_sortie": 50,
        "jetons_raisonnement": 0,
        "estimes": False,
        "latence_ms": 1000.0,
        "statut": "ok",
        **valeurs,
    }


def contexte(**valeurs: Any) -> dict[str, Any]:
    return {
        "reglages": {
            "nombre_series": 5,
            "passages_par_serie": 2,
            "catalogue_seul": True,
        },
        "modele": "modele-embedding-factice",
        "revision": "0" * 8,
        "precision": "float32",
        "corpus_id": "v2",
        "encodage_id": 3,
        "identifiant_requete": "api-factice-0001",
        "duree_ms": 1200.0,
        "version_api": "1.4.0",
        "profil": "mistral-large-2512",
        "fournisseur": "mistral",
        "modele_langage": "modele-de-mistral-large-2512",
        "statut_profil": "stable",
        "date_appel": "2026-11-03T18:39:00+00:00",
        "reglages_appliques": {
            "temperature": 0.0,
            "longueur_max": 400,
            "mode": "strict",
            "top_p": 1.0,
            "reasoning_effort": None,
        },
        "reglages_absents": [],
        "question_reformulee": None,
        "reformulation_ignoree": None,
        "appels": [appel()],
        "cout_estime_usd": 0.00016,
        "latences_ms": {
            "recherche": 100.0,
            "reformulation": None,
            "modele": 1000.0,
            "totale": 1200.0,
        },
        **valeurs,
    }


def reponse_nominale(**valeurs: Any) -> dict[str, Any]:
    """Cinq séries dans `series`, deux citées, trois passages cités ; des passages non
    cités, y compris dans une série citée : le filtre de l'application est éprouvé."""
    return {
        "refus": False,
        "motif_refus": None,
        "reponse": (
            "Série factice A [S:101] convient à ce lecteur. "
            "Série factice B [S:102] est un autre choix."
        ),
        "series_citees": [
            {
                "serie_id": 101,
                "titre": "Série factice A",
                "rang": 1,
                "fragment_ids": [11],
            },
            {
                "serie_id": 102,
                "titre": "Série factice B",
                "rang": 2,
                "fragment_ids": [21, 22],
            },
        ],
        "series": [
            serie(
                101,
                "Série factice A",
                [passage(11, score=0.83), passage(12, "synopsis_kitsu", 0.47)],
                rang=1,
                score=0.83,
            ),
            serie(
                102,
                "Série factice B",
                [passage(21, "resume_serie", 0.76), passage(22, score=0.612)],
                rang=2,
                score=0.76,
                disponibilite="seulement_au_japon",
                nb_volumes=3,
                nb_volumes_source="divergent",
                statut="en_cours",
            ),
            serie(103, "Série factice C", [passage(31, score=0.64)], 3, 0.64),
            serie(104, "Série factice D", [passage(41, score=0.52)], 4, 0.52),
            serie(105, "Série factice E", [passage(51, score=0.4)], 5, 0.4),
        ],
        "contexte": contexte(),
        **valeurs,
    }


def reponse_refus(
    motif: str,
    texte: str = "Refus factice.",
    series: list[dict[str, Any]] | None = None,
    **ctx: Any,
) -> dict[str, Any]:
    return {
        "refus": True,
        "motif_refus": motif,
        "reponse": texte,
        "series_citees": [],
        "series": (
            [serie(103, "Série factice C", [passage(31)], rang=1)]
            if series is None
            else series
        ),
        "contexte": contexte(**ctx),
    }


def erreur(code: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": f"Message factice de {code}.",
        "details": details,
        "identifiant_requete": "api-factice-0002",
    }


def rendu_503(code: str, details: dict[str, Any] | None = None, **ent: str) -> Rendu:
    entetes = {"Retry-After": ent["retry_after"]} if "retry_after" in ent else {}
    return Rendu(503, erreur(code, details), entetes)


DETAILS_FOURNISSEUR = {"profil": "mistral-large-2512", "role": "reponse"}


def serie_nulle() -> dict[str, Any]:
    return serie(
        106,
        "Série factice F",
        [passage(61)],
        disponibilite=None,
        nb_volumes=None,
        nb_volumes_source=None,
        statut=None,
        demographie=None,
        annee_fr=None,
        auteurs=None,
    )


def reponse_metadonnees_nulles() -> dict[str, Any]:
    return reponse_nominale(
        reponse="Série factice F [S:106] convient.",
        series_citees=[
            {
                "serie_id": 106,
                "titre": "Série factice F",
                "rang": 1,
                "fragment_ids": [61],
            }
        ],
        series=[serie_nulle()],
    )


def reponse_sans_fragment() -> dict[str, Any]:
    return reponse_nominale(
        reponse="Série factice A [S:101] convient.",
        series_citees=[
            {"serie_id": 101, "titre": "Série factice A", "rang": 1, "fragment_ids": []}
        ],
    )


def reponse_texte(texte: str) -> dict[str, Any]:
    return reponse_nominale(reponse=texte)


def reponse_sans_scores(*, nuls: bool) -> dict[str, Any]:
    """Hors contrat, exprès : scores nuls ou absents, le mode démonstration tient."""
    corps = reponse_nominale()
    for une_serie in corps["series"]:
        for objet in (une_serie, *une_serie["passages"]):
            if nuls:
                objet["score"] = None
            else:
                del objet["score"]
    return corps


def reponse_contexte_minimal() -> dict[str, Any]:
    """Hors contrat, exprès : tout ce que seul le mode démonstration lit est absent."""
    garde = contexte(cout_estime_usd=None, latences_ms={"totale": 1200.0})
    for cle in ("reglages", "reglages_appliques", "modele", "revision"):
        del garde[cle]
    corps = reponse_nominale(contexte=garde)
    for une_serie in corps["series"]:
        del une_serie["rang"]
    return corps


def reponse_dans_le_desordre() -> dict[str, Any]:
    corps = reponse_nominale()
    corps["series"].reverse()
    return corps


def reponse_non_citee_piegee() -> dict[str, Any]:
    corps = reponse_nominale()
    non_citee = corps["series"][2]
    non_citee["titre"] = "<b>Titre piégé</b>"
    non_citee["passages"][0]["extrait"] = "<script>alert(2)</script> Extrait piégé."
    return corps


SCENARIOS_REPONSE: dict[str, Callable[[], Rendu]] = {
    "nominale": lambda: Rendu(200, reponse_nominale()),
    "metadonnees_nulles": lambda: Rendu(200, reponse_metadonnees_nulles()),
    "sans_fragment": lambda: Rendu(200, reponse_sans_fragment()),
    "reformulee": lambda: Rendu(
        200,
        reponse_nominale(
            contexte=contexte(
                question_reformulee="Question factice reformulée ?",
                appels=[appel("reformulation"), appel()],
                latences_ms={
                    "recherche": 100.0,
                    "reformulation": 500.0,
                    "modele": 1000.0,
                    "totale": 1700.0,
                },
            )
        ),
    ),
    "reformulation_ignoree": lambda: Rendu(
        200,
        reponse_nominale(
            contexte=contexte(
                reformulation_ignoree="trop_longue",
                appels=[appel("reformulation"), appel()],
            )
        ),
    ),
    "refus_debit_rejoue": lambda: Rendu(
        200,
        reponse_nominale(
            contexte=contexte(
                appels=[
                    appel(
                        jetons_entree=0,
                        jetons_sortie=0,
                        statut="refus_debit",
                        latence_ms=10.0,
                    ),
                    appel(estimes=True),
                ]
            )
        ),
    ),
    "cout_inconnu": lambda: Rendu(
        200, reponse_nominale(contexte=contexte(cout_estime_usd=None))
    ),
    "texte_piege": lambda: Rendu(
        200,
        reponse_texte(
            "<script>alert(1)</script> Série factice A [S:101], "
            "une marque inconnue [S:999], une série non citée [S:103]."
        ),
    ),
    "titre_formes": lambda: Rendu(
        200,
        reponse_texte(
            "*Série Factice a !* [S:101] convient ; [S:102] « série factice B », "
            "et **un mot appuyé** pour finir."
        ),
    ),
    "series_dans_le_desordre": lambda: Rendu(200, reponse_dans_le_desordre()),
    "non_citee_piegee": lambda: Rendu(200, reponse_non_citee_piegee()),
    "scores_nuls": lambda: Rendu(200, reponse_sans_scores(nuls=True)),
    "scores_absents": lambda: Rendu(200, reponse_sans_scores(nuls=False)),
    "contexte_minimal": lambda: Rendu(200, reponse_contexte_minimal()),
    "complement": lambda: Rendu(
        200,
        reponse_nominale(
            complement="Complément factice.",
            complement_titres=["Titre factice hors catalogue"],
        ),
    ),
    "complement_nul": lambda: Rendu(
        200, reponse_nominale(complement="Complément factice.", complement_titres=None)
    ),
    # E4, lot 2b : l'étiquette du complément fournie par l'API (contrat 1.4.0, D9), ou
    # absente (le texte du 12) ; une réponse qui ne cite aucune série ; un refus.
    "enrichi_avec_etiquette": lambda: Rendu(
        200,
        reponse_nominale(
            complement="Complément factice.",
            complement_titres=["Titre factice hors catalogue"],
            complement_etiquette="Étiquette factice de l'API",
            contexte=contexte(
                reglages_appliques={
                    "temperature": 0.0,
                    "longueur_max": 400,
                    "mode": "enrichi",
                    "top_p": 1.0,
                    "reasoning_effort": None,
                }
            ),
        ),
    ),
    "enrichi_sans_etiquette": lambda: Rendu(
        200,
        reponse_nominale(
            complement="Complément factice.",
            complement_titres=["Titre factice hors catalogue"],
        ),
    ),
    "sans_carte": lambda: Rendu(
        200,
        reponse_nominale(
            reponse="Réponse factice qui ne cite aucune série.", series_citees=[]
        ),
    ),
    "refus": lambda: Rendu(200, reponse_refus("hors_catalogue")),
    "refus_aucun_passage": lambda: Rendu(
        200,
        reponse_refus(
            "aucun_passage",
            series=[],
            date_appel=None,
            appels=[],
            cout_estime_usd=0.0,
            latences_ms={
                "recherche": 100.0,
                "reformulation": None,
                "modele": None,
                "totale": 110.0,
            },
        ),
    ),
    "refus_hors_catalogue": lambda: Rendu(200, reponse_refus("hors_catalogue")),
    "refus_inconnu": lambda: Rendu(200, reponse_refus("inconnu")),
    # 4xx
    "cle_invalide": lambda: Rendu(401, erreur("cle_invalide")),
    "corps_trop_volumineux": lambda: Rendu(
        413, erreur("corps_trop_volumineux", {"maximum_octets": 16384})
    ),
    "entree_invalide": lambda: Rendu(
        422, erreur("entree_invalide", {"champs": [{"champ": "x", "raison": "type"}]})
    ),
    "question_trop_longue": lambda: Rendu(
        422,
        erreur(
            "question_trop_longue",
            {"longueur": 501, "maximum": 500, "unite": "caracteres"},
        ),
    ),
    "historique_invalide": lambda: Rendu(
        422, erreur("historique_invalide", {"motif": "trop_long"})
    ),
    "profil_inconnu": lambda: Rendu(
        422, erreur("profil_inconnu", {"profil": "gpt-6-sol", "profils": []})
    ),
    "profil_indisponible": lambda: Rendu(
        422, erreur("profil_indisponible", {"profil": "gpt-6-sol"})
    ),
    "reglage_non_expose": lambda: Rendu(
        422,
        erreur(
            "reglage_non_expose", {"reglage": "temperature", "profil": "gpt-6-luna"}
        ),
    ),
    "debit_depasse": lambda: Rendu(
        429,
        erreur("debit_depasse", {"limite_par_minute": 60, "reessayer_dans_s": 12}),
        {"Retry-After": "12"},
    ),
    "debit_sans_entete": lambda: Rendu(
        429, erreur("debit_depasse", {"limite_par_minute": 60, "reessayer_dans_s": 12})
    ),
    # 5xx
    "service_embedding_indisponible": lambda: rendu_503(
        "service_embedding_indisponible", {"cause": "injoignable"}
    ),
    "service_embedding_incoherent": lambda: rendu_503("service_embedding_incoherent"),
    "base_indisponible": lambda: rendu_503(
        "base_indisponible", {"cause": "injoignable"}
    ),
    "encodage_en_service_change": lambda: rendu_503(
        "encodage_en_service_change", {"charge": 3, "en_service": 4}
    ),
    "fournisseur_injoignable": lambda: rendu_503(
        "fournisseur_injoignable", DETAILS_FOURNISSEUR
    ),
    "fournisseur_delai": lambda: rendu_503("fournisseur_delai", DETAILS_FOURNISSEUR),
    "fournisseur_refus_429": lambda: rendu_503(
        "fournisseur_refus",
        {**DETAILS_FOURNISSEUR, "statut": 429, "code_fournisseur": "1300"},
        retry_after="7",
    ),
    "fournisseur_refus_429_sans_entete": lambda: rendu_503(
        "fournisseur_refus", {**DETAILS_FOURNISSEUR, "statut": 429}
    ),
    "fournisseur_refus_500": lambda: rendu_503(
        "fournisseur_refus", {**DETAILS_FOURNISSEUR, "statut": 500}
    ),
    "fournisseur_longueur": lambda: rendu_503(
        "fournisseur_refus",
        {**DETAILS_FOURNISSEUR, "statut": 200, "motif": "longueur_atteinte"},
    ),
    "fournisseur_corps_inattendu": lambda: rendu_503(
        "fournisseur_refus",
        {**DETAILS_FOURNISSEUR, "statut": 200, "motif": "corps_inattendu"},
    ),
    **{
        f"sortie_{motif}": (
            lambda motif=motif: rendu_503(
                "sortie_modele_invalide", {"motif": motif, "role": "reponse"}
            )
        )
        for motif in (
            "schema",
            "serie_inconnue",
            "fragment_inconnu",
            "refus_incoherent",
            "pseudonyme",
            "disponibilite",
            "demographie",
        )
    },
    "erreur_interne": lambda: Rendu(500, erreur("erreur_interne")),
    "corps_hors_contrat": lambda: Rendu(502, brut=b"<html>passerelle</html>"),
    "reponse_hors_contrat": lambda: Rendu(200, brut=b'{"refus": "peut-etre"}'),
    "delai": lambda: Rendu(200, reponse_nominale(), attente_s=5.0),
}

SCENARIOS_INFO: dict[str, Callable[[], Rendu]] = {
    "nominale": lambda: Rendu(200, info()),
    "sans_large_4": lambda: Rendu(
        200,
        info(
            [
                profil("mistral-large-2512"),
                profil("gpt-6-luna", "openai"),
                profil("gpt-6-sol", "openai"),
            ]
        ),
    ),
    "large_4_actif": lambda: Rendu(200, info(actif="mistral-large-4")),
    "profil_indisponible": lambda: Rendu(
        200,
        info(
            [
                profil("mistral-large-2512"),
                profil("gpt-6-luna", "openai", disponible=False),
            ]
        ),
    ),
    "actif_indisponible": lambda: Rendu(
        200,
        info(
            [
                profil("mistral-large-2512", disponible=False),
                profil("gpt-6-luna", "openai"),
            ]
        ),
    ),
    # E4, lot 5 : la page du profil actif — un profil proposé de chaque fournisseur, un
    # exclu par la configuration (mistral-large-4), un indisponible (gpt-6-sol).
    "administration": lambda: Rendu(
        200,
        info(
            [
                profil("mistral-large-2512"),
                profil("mistral-large-4", statut="apercu"),
                profil("gpt-6-luna", "openai"),
                profil("gpt-6-sol", "openai", disponible=False),
            ]
        ),
    ),
    # E4, lot 5 : l'API répond, mais en erreur — l'état « ko » des pages
    # d'administration.
    "erreur_interne": lambda: Rendu(500, erreur("erreur_interne")),
    "aucun_profil": lambda: Rendu(
        200, info([profil("mistral-large-4", statut="apercu")], actif="mistral-large-4")
    ),
    "fournisseur_inconnu": lambda: Rendu(
        200, info([profil("mistral-large-2512"), profil("autre", "fournisseur-x")])
    ),
    "deux_modes": lambda: Rendu(200, info(modes=["strict", "enrichi"])),
    "bornes_differentes": lambda: Rendu(
        200, info(tours_max=6, caracteres_max=3000, question_max=400)
    ),
    "temperature_bornee": lambda: Rendu(200, info(temperature_max=0.5)),
    "temperature_absente": lambda: Rendu(
        200,
        info(
            [
                profil("mistral-large-2512"),
                profil("gpt-6-luna", "openai", absents=["temperature"]),
            ]
        ),
    ),
    "cle_invalide": lambda: Rendu(401, erreur("cle_invalide")),
    "debit_depasse": lambda: Rendu(
        429,
        erreur("debit_depasse", {"limite_par_minute": 60, "reessayer_dans_s": 12}),
        {"Retry-After": "12"},
    ),
    "corps_hors_contrat": lambda: Rendu(200, brut=b"{}"),
    "delai": lambda: Rendu(200, info(), attente_s=5.0),
}


def controle(
    nom: str, fournisseur: str = "mistral", statut: str = "ok", disponible: bool = True
) -> dict[str, Any]:
    return {
        "nom": nom,
        "fournisseur": fournisseur,
        "modele": f"modele-de-{nom}",
        "variable_cle": f"{fournisseur.upper()}_CLE_FACTICE",
        "configuree": statut != "absente",
        "disponible": disponible,
        "statut": statut,
    }


#: Le test des clés (contrat 1.5.0, E4, lot 5). `route_absente` : l'API 1.4.0 de la
#: pile, qui ne connaît pas la route — un 404 que le contrat 1.5.0 ne déclare pas pour
#: elle, rendu brut.
SCENARIOS_VERIFICATION: dict[str, Callable[[], Rendu]] = {
    "nominale": lambda: Rendu(
        200,
        {
            "verifie_le": "2026-11-10T09:00:00Z",
            "profils": [
                controle("mistral-large-2512"),
                controle("mistral-large-4"),
                controle("gpt-6-luna", "openai", "401", False),
                controle("gpt-6-sol", "openai", "absente", False),
            ],
        },
    ),
    "injoignable": lambda: Rendu(
        200,
        {
            "verifie_le": "2026-11-10T09:00:00Z",
            "profils": [
                controle("mistral-large-2512", statut="injoignable", disponible=False),
                controle("mistral-large-4", disponible=False),
                controle("gpt-6-luna", "openai", "corps_inattendu", False),
            ],
        },
    ),
    "route_absente": lambda: Rendu(
        404,
        brut=json.dumps(erreur("route_inconnue")).encode(),
        entetes={"Content-Type": "application/json"},
    ),
    "trop_tot": lambda: Rendu(
        429,
        erreur("debit_depasse", {"limite_par_minute": 1, "reessayer_dans_s": 42}),
        {"Retry-After": "42"},
    ),
    "trop_tot_sans_entete": lambda: Rendu(
        429, erreur("debit_depasse", {"limite_par_minute": 1, "reessayer_dans_s": 1})
    ),
    "erreur_interne": lambda: Rendu(500, erreur("erreur_interne")),
    "hors_contrat": lambda: Rendu(200, brut=b'{"profils": "aucun"}'),
    "delai": lambda: Rendu(200, {"verifie_le": "x", "profils": []}, attente_s=5.0),
}

SANTE_OK = {
    "statut": "ok",
    "base": "ok",
    "service_embedding": "ok",
    "encodage": "ok",
}
SCENARIOS_SANTE: dict[str, Callable[[], Rendu]] = {
    "ok": lambda: Rendu(200, SANTE_OK),
    "degrade": lambda: Rendu(
        503,
        {
            "statut": "degrade",
            "base": "injoignable",
            "service_embedding": "ok",
            "encodage": "inconnu",
        },
    ),
    "delai": lambda: Rendu(200, SANTE_OK, attente_s=5.0),
}
#: Hors contrat, exprès : le client doit le lire comme `corps_inattendu`.
HORS_CONTRAT = {
    "corps_hors_contrat",
    "reponse_hors_contrat",
    "scores_nuls",
    "scores_absents",
    "contexte_minimal",
}


# --------------------------------------------------------------------------- #
#  Le catalogue (l'API data du module 02), tel que son contrat 0.6.0 le décrit
# --------------------------------------------------------------------------- #
def validateur_catalogue(schema: Mapping[str, Any]) -> Draft202012Validator:
    """Un schéma du contrat du 02 ; ses `$ref` se résolvent dans ses `components`."""
    return Draft202012Validator(
        {**schema, "components": CONTRAT_CATALOGUE["components"]}
    )


def schema_catalogue(route: str, statut: int) -> Mapping[str, Any] | None:
    decrite = CONTRAT_CATALOGUE["paths"][route]["get"]["responses"].get(str(statut))
    return None if decrite is None else decrite["content"]["application/json"]["schema"]


def resultat_catalogue(series_id: int, titre: str, **valeurs: Any) -> dict[str, Any]:
    return {
        "series_id": series_id,
        "title": titre,
        "type": "Manga",
        "year": 2001,
        "auteurs": "Autrice FACTICE",
        "members_rating": 8.25,
        "members_votes": 1234,
        "disponibilite": "edite_en_france",
        "nb_volumes": 12,
        "nb_volumes_source": "champ",
        "correspondance": "titre",
        "titre_correspondant": None,
        **valeurs,
    }


def fiche_catalogue(series_id: int, titre: str, **valeurs: Any) -> dict[str, Any]:
    """Une fiche complète : les champs obligatoires du contrat, factices ; ses sept
    métadonnées sont celles des cartes de l'API du modèle (`metadonnees()`) : une
    fiche et une carte aux mêmes valeurs doivent rendre la même ligne de titre."""
    return {
        "series_id": series_id,
        "work_uid": None,
        "title": titre,
        "url": f"https://catalogue.invalid/serie/{series_id}",
        "type": "Manga",
        "category": "Shonen",
        "year": 2001,
        "other_titles": ["Titre factice second"],
        "dessinateur": "Auteur factice",
        "scenariste": "Auteur factice",
        "magazine_prepublication": None,
        "statuses": [],
        "genres_source": ["Aventure", "Comédie"],
        "genres_enriched": [],
        "tags_source": [],
        "synopsis": "Résumé factice de la série.",
        "synopsis_enriched": "Résumé factice de la série.",
        "synopsis_source": "manga_sanctuary",
        "synopsis_kitsu": None,
        "popularity_rank": None,
        "members_rating": 8.69,
        "members_votes": 6936,
        "experts_rating": None,
        "experts_votes": None,
        "volume_count": 12,
        "review_count": 0,
        "score_mean": None,
        "score_min": None,
        "score_max": None,
        "first_review_date": None,
        "last_review_date": None,
        "kitsu_id": None,
        "needs_review": False,
        **metadonnees(),
        **valeurs,
    }


#: Trois séries factices : une trouvée par son titre, une par un autre titre, une par
#: son auteur.
RESULTATS_NOMINAUX = (
    resultat_catalogue(101, "Série factice A"),
    resultat_catalogue(
        102,
        "Série factice B",
        correspondance="autre_titre",
        titre_correspondant="Titre factice second",
        disponibilite="seulement_au_japon",
    ),
    resultat_catalogue(
        103,
        "Série factice C",
        correspondance="auteur",
        members_rating=None,
        members_votes=None,
        nb_volumes_source="repli_volumes_distincts",
    ),
)
SERIES_FACTICES = {r["series_id"] for r in RESULTATS_NOMINAUX}
SCENARIOS_CATALOGUE = (
    "nominal",
    "vide",
    "une_page",
    "plusieurs_pages",
    "serie_sans_note",
    "serie_inconnue",
    "indisponible",
    "cle_refusee",
    "lent",
    "corps_hors_contrat",
    # E4, lot 3 : les volumes d'une série.
    "sans_volume",
    "beaucoup",
    # E4, lot 2b (contrat 0.6.0) : les suggestions, les sources du résumé.
    "suggestions",
    "resume_kitsu",
    "deux_resumes",
    "sans_resume",
)
#: `plusieurs_pages` : 45 séries, trois pages de 20.
TOTAL_PLUSIEURS_PAGES = 45
#: `beaucoup` : 250 volumes, deux pages de 200.
TOTAL_BEAUCOUP = 250


def volume_catalogue(
    series_id: int, rang: int, numero: int | None, titre: str | None, annee: int | None
) -> dict[str, Any]:
    """Un volume du contrat du 02 : ses champs obligatoires, factices ; l'éditeur porte
    le défaut de source connu (un libellé de champ), que l'application ne lit pas."""
    return {
        "volume_url": f"https://catalogue.invalid/serie/{series_id}/volume/{rang}",
        "title": titre,
        "number": numero,
        "publication_date": None if annee is None else f"{annee}-03-01",
        "ean": None,
        "editeur": "Mag. de prépublication",
        "dessinateur": None,
        "scenariste": None,
        "format": None,
        "pages": None,
        "country": None,
        "status": None,
        "tomes_published": None,
        "tomes_total": None,
        "experts_rating": None,
        "experts_votes": None,
        "review_count": None,
        "synopsis": None,
    }


def volumes_nominaux(series_id: int) -> list[dict[str, Any]]:
    """Trois volumes, dans l'ordre du 02 (numéro, sans numéro en dernier) : deux
    éditions du tome 1, d'années différentes, et un hors-série sans numéro."""
    return [
        volume_catalogue(series_id, 1, 1, "Premier tome factice", 2001),
        volume_catalogue(series_id, 2, 1, "Premier tome factice", 2010),
        volume_catalogue(series_id, 3, None, "Hors-série factice", 2005),
    ]


def volumes_selon(nom: str, series_id: int, limit: int, offset: int) -> Rendu:
    """`GET /series/{series_id}/volumes` selon le scénario."""
    if nom == "indisponible":
        return Rendu(503, {"detail": "database unavailable"})
    if nom == "cle_refusee":
        return Rendu(401, {"detail": "Missing or invalid API key."})
    if nom == "corps_hors_contrat":
        return Rendu(200, brut=b'{"items": "pas une liste"}')
    if nom == "serie_inconnue" or series_id not in SERIES_FACTICES | {1000}:
        return Rendu(404, {"detail": "series_id not found"})
    if nom == "sans_volume":
        tous: list[dict[str, Any]] = []
    elif nom == "beaucoup":
        tous = [
            volume_catalogue(series_id, i, i, f"Tome factice {i}", 2000 + i % 20)
            for i in range(1, TOTAL_BEAUCOUP + 1)
        ]
    else:
        tous = volumes_nominaux(series_id)
    corps = {
        "series_id": series_id,
        "total": len(tous),
        "limit": limit,
        "offset": offset,
        "items": tous[offset : offset + limit],
    }
    return Rendu(200, corps, attente_s=1.0 if nom == "lent" else 0.0)


def recherche_catalogue(nom: str, q: str, limit: int, offset: int) -> Rendu:
    """`GET /series` selon le scénario."""
    if nom == "indisponible":
        return Rendu(503, {"detail": "database unavailable"})
    if nom == "cle_refusee":
        return Rendu(401, {"detail": "Missing or invalid API key."})
    if nom == "corps_hors_contrat":
        return Rendu(200, brut=b'{"inattendu": true}')
    if nom in ("vide", "suggestions"):
        items: list[dict[str, Any]] = []
        total = 0
    elif nom in ("une_page", "plusieurs_pages"):
        total = limit if nom == "une_page" else TOTAL_PLUSIEURS_PAGES
        items = [
            resultat_catalogue(1000 + i, f"Série factice {i + 1}")
            for i in range(offset, min(offset + limit, total))
        ]
    else:
        items = [dict(item) for item in RESULTATS_NOMINAUX]
        total = len(items)
    corps = {"query": q, "total": total, "limit": limit, "offset": offset}
    if nom == "suggestions":
        corps["suggestions"] = [
            {
                "series_id": 101,
                "title": "Série factice A",
                "score": 0.571,
                "correspondance": "titre",
            },
            {
                "series_id": 103,
                "title": "Série factice C",
                "score": 0.4,
                "correspondance": "auteur",
            },
        ]
    return Rendu(
        200, {**corps, "items": items}, attente_s=1.0 if nom == "lent" else 0.0
    )


def fiche_selon(nom: str, series_id: int) -> Rendu:
    """`GET /series/{series_id}` selon le scénario."""
    if nom == "indisponible":
        return Rendu(503, {"detail": "database unavailable"})
    if nom == "cle_refusee":
        return Rendu(401, {"detail": "Missing or invalid API key."})
    if nom == "corps_hors_contrat":
        return Rendu(200, brut=b'{"series_id": "pas un entier"}')
    if nom == "serie_inconnue" or series_id not in SERIES_FACTICES | {1000}:
        return Rendu(404, {"detail": "series_id not found"})
    titre = next(
        (r["title"] for r in RESULTATS_NOMINAUX if r["series_id"] == series_id),
        "Série factice",
    )
    if nom == "serie_sans_note":
        corps = fiche_catalogue(
            series_id, titre, members_rating=None, members_votes=None
        )
    elif nom == "resume_kitsu":
        corps = fiche_catalogue(
            series_id,
            titre,
            synopsis="Résumé brut factice.",
            synopsis_enriched="Factice synopsis from Kitsu.",
            synopsis_source="kitsu",
            synopsis_kitsu="Factice synopsis from Kitsu.",
        )
    elif nom == "deux_resumes":
        corps = fiche_catalogue(
            series_id,
            titre,
            synopsis_source="manga_sanctuary",
            synopsis_kitsu="Factice synopsis from Kitsu.",
        )
    elif nom == "sans_resume":
        corps = fiche_catalogue(
            series_id,
            titre,
            synopsis=None,
            synopsis_enriched=None,
            synopsis_source=None,
            synopsis_kitsu=None,
        )
    else:
        corps = fiche_catalogue(series_id, titre)
    return Rendu(200, corps, attente_s=1.0 if nom == "lent" else 0.0)


def connu(nom: str, scenarios: Mapping[str, Any]) -> str:
    """Un scénario du catalogue ; un nom inconnu est une erreur de test."""
    if nom not in scenarios:
        raise KeyError(nom)
    return nom


class Doublure:
    """L'application de test, ses scénarios, ce qu'elle a reçu."""

    def __init__(self) -> None:
        self.scenario_info = "nominale"
        self.scenario_sante = "ok"
        self.scenario_reponse = "nominale"
        self.scenario_catalogue = "nominal"
        self.scenario_verification = "nominale"
        #: Les tests des clés reçus (E4, lot 5) : leur nombre, et la clé de chacun.
        self.verifications: list[str | None] = []
        self.cles_catalogue: list[str | None] = []
        self.appels_catalogue: dict[str, int] = {}
        #: Les recherches reçues : (q, limit, offset).
        self.recherches: list[tuple[str, int, int]] = []
        #: Les lectures de volumes reçues : (series_id, limit, offset).
        self.lectures_volumes: list[tuple[int, int, int]] = []
        self.corps_recus: list[dict[str, Any]] = []
        self.cles_recues: list[str | None] = []
        self.cles_sante: list[str | None] = []
        self.ecarts: list[str] = []
        self.app = self._creer()

    def choisir(
        self,
        *,
        info: str | None = None,
        sante: str | None = None,
        reponse: str | None = None,
        catalogue: str | None = None,
        verification: str | None = None,
    ) -> None:
        if verification is not None:
            self.scenario_verification = connu(verification, SCENARIOS_VERIFICATION)
        if catalogue is not None:
            if catalogue not in SCENARIOS_CATALOGUE:
                raise KeyError(catalogue)
            self.scenario_catalogue = catalogue
        if info is not None:
            self.scenario_info = connu(info, SCENARIOS_INFO)
        if sante is not None:
            self.scenario_sante = connu(sante, SCENARIOS_SANTE)
        if reponse is not None:
            self.scenario_reponse = connu(reponse, SCENARIOS_REPONSE)

    def _verifier(self, route: str, methode: str, nom: str, rendu: Rendu) -> str | None:
        """Le corps d'un scénario contre le contrat : un écart, ou None."""
        if nom in HORS_CONTRAT or rendu.brut is not None:
            return None
        schema = schema_de_reponse(route, methode, rendu.statut)
        if schema is None and rendu.statut == 500:
            schema = schema_erreur()
        if schema is None:
            return f"{route} {rendu.statut} : statut absent du contrat"
        erreurs = [e.message for e in validateur(schema).iter_errors(rendu.corps)]
        if erreurs:
            return f"{route} {rendu.statut} ({nom}) : {erreurs[0]}"
        codes = codes_du_contrat(route, methode, rendu.statut)
        if codes and rendu.statut >= 400:
            code = (rendu.corps or {}).get("code")
            if code not in codes:
                return f"{route} {rendu.statut} : code {code} absent du contrat"
        return None

    async def _rendre(
        self, route: str, methode: str, nom: str, rendu: Rendu
    ) -> Response:
        ecart = self._verifier(route, methode, nom, rendu)
        if ecart is not None:
            self.ecarts.append(ecart)
            return JSONResponse(erreur("erreur_interne"), status_code=500)
        if rendu.attente_s:
            await asyncio.sleep(rendu.attente_s)
        entetes = {"X-Identifiant-Requete": "api-factice-entete", **rendu.entetes}
        if rendu.brut is not None:
            return Response(rendu.brut, status_code=rendu.statut, headers=entetes)
        return JSONResponse(rendu.corps, status_code=rendu.statut, headers=entetes)

    def _creer(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.post("/_essai/scenario")
        async def scenario(request: Request) -> dict[str, str]:
            demande = await request.json()
            self.choisir(
                info=demande.get("info"),
                sante=demande.get("sante"),
                reponse=demande.get("reponse"),
                catalogue=demande.get("catalogue"),
                verification=demande.get("verification"),
            )
            return {
                "info": self.scenario_info,
                "reponse": self.scenario_reponse,
                "catalogue": self.scenario_catalogue,
            }

        @app.get("/_essai/ecarts")
        async def ecarts() -> list[str]:
            return self.ecarts

        @app.get("/v1/info")
        async def route_info(request: Request) -> Response:
            self.cles_recues.append(request.headers.get("X-API-Key"))
            nom = self.scenario_info
            return await self._rendre("/v1/info", "get", nom, SCENARIOS_INFO[nom]())

        @app.get("/v1/sante")
        async def route_sante(request: Request) -> Response:
            self.cles_sante.append(request.headers.get("X-API-Key"))
            nom = self.scenario_sante
            return await self._rendre("/v1/sante", "get", nom, SCENARIOS_SANTE[nom]())

        @app.post("/v1/reponse")
        async def route_reponse(request: Request) -> Response:
            self.cles_recues.append(request.headers.get("X-API-Key"))
            corps = await request.json()
            self.corps_recus.append(corps)
            erreurs = [
                e.message for e in validateur(schema_demande()).iter_errors(corps)
            ]
            if erreurs:
                self.ecarts.append(f"requête hors contrat : {erreurs[0]}")
                return JSONResponse(erreur("entree_invalide"), status_code=422)
            nom = self.scenario_reponse
            rendu = SCENARIOS_REPONSE[nom]()
            return await self._rendre("/v1/reponse", "post", nom, rendu)

        @app.post("/v1/verification")
        async def route_verification(request: Request) -> Response:
            self.verifications.append(request.headers.get("X-API-Key"))
            nom = self.scenario_verification
            rendu = SCENARIOS_VERIFICATION[nom]()
            return await self._rendre("/v1/verification", "post", nom, rendu)

        self._creer_catalogue(app)
        return app

    async def _rendre_catalogue(self, route: str, nom: str, rendu: Rendu) -> Response:
        """Le corps d'un scénario du catalogue contre le contrat du 02 : un écart est
        noté et rendu en 500 ; un corps hors contrat voulu y échappe."""
        if rendu.brut is None:
            schema = schema_catalogue(route, rendu.statut)
            if schema is None:
                ecart = f"{route} {rendu.statut} : statut absent du contrat du 02"
            else:
                erreurs = [
                    e.message
                    for e in validateur_catalogue(schema).iter_errors(rendu.corps)
                ]
                ecart = f"{route} ({nom}) : {erreurs[0]}" if erreurs else None
            if ecart is not None:
                self.ecarts.append(ecart)
                return JSONResponse({"detail": "erreur"}, status_code=500)
        if rendu.attente_s:
            await asyncio.sleep(rendu.attente_s)
        if rendu.brut is not None:
            return Response(rendu.brut, status_code=rendu.statut)
        return JSONResponse(rendu.corps, status_code=rendu.statut)

    def _compter(self, route: str, request: Request) -> str | None:
        self.appels_catalogue[route] = self.appels_catalogue.get(route, 0) + 1
        cle = request.headers.get("X-API-Key")
        self.cles_catalogue.append(cle)
        return cle

    def _creer_catalogue(self, app: FastAPI) -> None:
        sans_cle = Rendu(401, {"detail": "Missing or invalid API key."})

        @app.get("/live")
        async def route_live() -> Response:
            self.appels_catalogue["/live"] = self.appels_catalogue.get("/live", 0) + 1
            return JSONResponse({"status": "ok", "db": "not_checked"})

        @app.get("/health")
        async def route_health() -> Response:
            # E4, lot 5 : la page des services. Le catalogue « indisponible » : sa
            # base ne répond pas (503) ; sinon, la normalisation de la 028.
            self.appels_catalogue["/health"] = (
                self.appels_catalogue.get("/health", 0) + 1
            )
            if self.scenario_catalogue == "indisponible":
                rendu = Rendu(503, {"status": "degraded", "db": "error"})
            else:
                corps = {"status": "ok", "db": "ok", "normalisation": "fonction"}
                rendu = Rendu(200, corps)
            return await self._rendre_catalogue("/health", "", rendu)

        @app.get("/series")
        async def route_series(request: Request) -> Response:
            nom = self.scenario_catalogue
            if not self._compter("/series", request):
                return await self._rendre_catalogue("/series", nom, sans_cle)
            parametres = request.query_params
            demande = (
                parametres.get("q", ""),
                int(parametres.get("limit", "20")),
                int(parametres.get("offset", "0")),
            )
            self.recherches.append(demande)
            rendu = recherche_catalogue(nom, *demande)
            return await self._rendre_catalogue("/series", nom, rendu)

        @app.get("/series/{series_id}")
        async def route_serie(request: Request, series_id: int) -> Response:
            nom = self.scenario_catalogue
            route = "/series/{series_id}"
            if not self._compter(route, request):
                return await self._rendre_catalogue(route, nom, sans_cle)
            return await self._rendre_catalogue(route, nom, fiche_selon(nom, series_id))

        @app.get("/series/{series_id}/volumes")
        async def route_volumes(request: Request, series_id: int) -> Response:
            nom = self.scenario_catalogue
            route = "/series/{series_id}/volumes"
            if not self._compter(route, request):
                return await self._rendre_catalogue(route, nom, sans_cle)
            limit = int(request.query_params.get("limit", "50"))
            offset = int(request.query_params.get("offset", "0"))
            if not (1 <= limit <= 200 and 0 <= offset <= 10000):
                self.ecarts.append(
                    f"{route} : limit {limit}, offset {offset} hors bornes"
                )
            self.lectures_volumes.append((series_id, limit, offset))
            rendu = volumes_selon(nom, series_id, limit, offset)
            return await self._rendre_catalogue(route, nom, rendu)

        @app.get("/series/{series_id}/reviews")
        async def route_critiques(request: Request, series_id: int) -> Response:
            # Comptée pour prouver qu'elle n'est jamais appelée ; rendue conforme.
            self._compter("/series/{series_id}/reviews", request)
            vide = {"series_id": series_id, "total": 0, "limit": 50, "offset": 0}
            rendu = Rendu(200, {**vide, "items": []})
            return await self._rendre_catalogue(
                "/series/{series_id}/reviews", "", rendu
            )


# L'application servie telle quelle (conteneur de l'essai Compose, port du bouclage).
DOUBLURE = Doublure()
app = DOUBLURE.app
