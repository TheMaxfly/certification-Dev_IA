"""La doublure de l'API du modèle : une application de test, jamais empaquetée.

Elle sert `/v1/info`, `/v1/sante` et `/v1/reponse` avec des réponses construites ici, et
**validées contre le contrat** du module 11, chargé par son chemin : une réponse ou une
requête hors contrat est un écart, noté et rendu en 500 ; un code d'erreur que le
contrat n'énumère pas aussi. Seuls les corps hors contrat et les scénarios sans
réponse y échappent, exprès.

Un seul mécanisme de pilotage : `POST /_essai/scenario`, qui choisit `{info, sante,
reponse}` ; il vaut en mémoire (`httpx.ASGITransport`) comme sur un port du bouclage
(audit d'accessibilité, Compose). L'application n'en sait rien, et aucune question ne
porte de commande. Noms et textes factices : aucune donnée réelle, aucun pseudonyme.
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
        "version_api": "1.3.1",
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


def passage(fragment_id: int, type_document: str = "critique") -> dict[str, Any]:
    return {
        "fragment_id": fragment_id,
        "doc_key": f"doc-factice-{fragment_id}",
        "type_document": type_document,
        "score": 0.5,
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
    **meta: Any,
) -> dict[str, Any]:
    return {
        "rang": rang,
        "entite": f"serie:{serie_id}",
        "serie_id": serie_id,
        "kitsu_id": None,
        "titre": titre,
        "score": 0.5,
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
        "version_api": "1.3.1",
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
                [passage(11), passage(12, "synopsis_kitsu")],
                rang=1,
            ),
            serie(
                102,
                "Série factice B",
                [passage(21, "resume_serie"), passage(22)],
                rang=2,
                disponibilite="seulement_au_japon",
                nb_volumes=3,
                nb_volumes_source="divergent",
                statut="en_cours",
            ),
            serie(103, "Série factice C", [passage(31)], rang=3),
            serie(104, "Série factice D", [passage(41)], rang=4),
            serie(105, "Série factice E", [passage(51)], rang=5),
        ],
        "contexte": contexte(),
        **valeurs,
    }


def reponse_refus(
    motif: str, texte: str = "Refus factice.", **ctx: Any
) -> dict[str, Any]:
    return {
        "refus": True,
        "motif_refus": motif,
        "reponse": texte,
        "series_citees": [],
        "series": [serie(103, "Série factice C", [passage(31)], rang=1)],
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
    "complement": lambda: Rendu(
        200,
        reponse_nominale(
            complement="Complément factice.",
            complement_titres=["Titre factice hors catalogue"],
        ),
    ),
    "refus_aucun_passage": lambda: Rendu(
        200,
        reponse_refus(
            "aucun_passage",
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
HORS_CONTRAT = {"corps_hors_contrat", "reponse_hors_contrat"}


class Doublure:
    """L'application de test, ses scénarios, ce qu'elle a reçu."""

    def __init__(self) -> None:
        self.scenario_info = "nominale"
        self.scenario_sante = "ok"
        self.scenario_reponse = "nominale"
        self.corps_recus: list[dict[str, Any]] = []
        self.cles_recues: list[str | None] = []
        self.ecarts: list[str] = []
        self.app = self._creer()

    def choisir(
        self,
        *,
        info: str | None = None,
        sante: str | None = None,
        reponse: str | None = None,
    ) -> None:
        if info is not None:
            self.scenario_info = SCENARIOS_INFO[info].__name__ and info
        if sante is not None:
            self.scenario_sante = SCENARIOS_SANTE[sante].__name__ and sante
        if reponse is not None:
            self.scenario_reponse = SCENARIOS_REPONSE[reponse].__name__ and reponse

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
            )
            return {"info": self.scenario_info, "reponse": self.scenario_reponse}

        @app.get("/v1/info")
        async def route_info(request: Request) -> Response:
            self.cles_recues.append(request.headers.get("X-API-Key"))
            nom = self.scenario_info
            return await self._rendre("/v1/info", "get", nom, SCENARIOS_INFO[nom]())

        @app.get("/v1/sante")
        async def route_sante() -> Response:
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

        return app


# L'application servie telle quelle (conteneur de l'essai Compose, port du bouclage).
DOUBLURE = Doublure()
app = DOUBLURE.app
