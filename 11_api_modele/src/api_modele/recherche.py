"""La recherche : une question → les séries classées, avec leurs passages.

1. La question est encodée par le client du module 09 (préfixe de question de sa
   configuration, refus de troncature). Plus longue que la borne : 422 avant tout
   appel (validation de l'entrée). Refusée par le service : on lui compte les jetons
   (`/tokenize`) ; au-delà de sa limite, 422 `question_trop_longue` ; sinon 503.
2. Le classement est celui du banc (`classement.Classeur`), sur les vecteurs de
   l'encodage en service, chargés au démarrage.
3. Une transaction en lecture seule, bornée : l'encodage est-il toujours en service
   (sinon 503 `encodage_en_service_change`) ; les titres ; le texte des passages.

Aucune écriture en base.
"""

from __future__ import annotations

import time

from psycopg import Error as ErreurBase
from psycopg.errors import QueryCanceled
from psycopg_pool import PoolTimeout

from api_modele import __version__, base, service
from api_modele.classement import EntiteClassee
from api_modele.erreurs import ErreurApi
from api_modele.etat import EtatApi
from api_modele.modeles import (
    Contexte,
    Passage,
    Recherche,
    ReglagesAppliques,
    Resultat,
    Serie,
    TypeDocument,
)
from api_modele.service import ErreurService, ServiceNonConforme

TYPES_DOCUMENT: dict[str, TypeDocument] = {
    "ms_review": "critique",
    "ms_synopsis": "resume_serie",
    "kitsu_synopsis": "synopsis_kitsu",
}


def encoder(etat: EtatApi, question: str) -> list[float]:
    """Le vecteur de la question ; une erreur du service devient son code."""
    try:
        return etat.client.encoder([question], role="requete")[0]
    except TimeoutError as exc:  # la réponse n'est pas venue dans le délai
        raise ErreurApi(
            503,
            "service_embedding_indisponible",
            "Le service d'embedding n'a pas répondu dans le délai.",
            {"cause": "delai_depasse", "delai_s": etat.reglages.delai_service_s},
        ) from exc
    except ErreurService as exc:
        raise _refus(etat, question) from exc
    except OSError as exc:
        raise ErreurApi(
            503,
            "service_embedding_indisponible",
            "Service d'embedding injoignable.",
            {"cause": "injoignable"},
        ) from exc


def _refus(etat: EtatApi, question: str) -> ErreurApi:
    """Pourquoi le service a refusé : trop de jetons, incohérence, ou indisponible."""
    client = etat.client
    limite = etat.coherence.info.get("max_input_length")
    try:
        jetons: int | None = client.compter_jetons([question], "requete")[0]
    except (ErreurService, OSError):
        jetons = None
    if isinstance(limite, int) and jetons is not None and jetons > limite:
        return ErreurApi(
            422,
            "question_trop_longue",
            f"La question dépasse {limite} jetons.",
            {"longueur": jetons, "maximum": limite, "unite": "jetons"},
        )
    try:
        service.confronter_au_registre(client, etat.coherence.encodage)
        cause = "refus"
    except ServiceNonConforme:
        return ErreurApi(
            503,
            "service_embedding_incoherent",
            "Le service d'embedding ne sert plus l'encodage en service.",
        )
    except (ErreurService, OSError):
        cause = "injoignable"
    return ErreurApi(
        503,
        "service_embedding_indisponible",
        "Le service d'embedding a refusé la question.",
        {"cause": cause},
    )


def completer(etat: EtatApi, entites: list[EntiteClassee]) -> list[Serie]:
    """Titres et passages, lus dans une transaction bornée, en lecture seule."""
    charge = etat.coherence.encodage
    corpus = charge.corpus_id
    try:
        with base.transaction(etat.pool, etat.reglages) as cx:
            en_service = base.lire_en_service(cx)
            if en_service is None or en_service.encodage_id != charge.encodage_id:
                raise ErreurApi(
                    503,
                    "encodage_en_service_change",
                    "L'encodage en service a changé depuis le démarrage : redémarrer.",
                    {
                        "charge": charge.encodage_id,
                        "en_service": None
                        if en_service is None
                        else en_service.encodage_id,
                    },
                )
            titres_series, titres_kitsu = base.lire_titres(
                cx,
                corpus,
                [e.serie_id for e in entites if e.serie_id is not None],
                [e.kitsu_id for e in entites if e.kitsu_id is not None],
            )
            textes = base.lire_passages(
                cx, corpus, [f.chunk_id for e in entites for f in e.fragments]
            )
    except QueryCanceled as exc:
        raise ErreurApi(
            503,
            "base_indisponible",
            "La base n'a pas répondu dans le délai.",
            {"cause": "delai_depasse", "delai_s": etat.reglages.delai_base_s},
        ) from exc
    except (ErreurBase, PoolTimeout) as exc:
        raise ErreurApi(
            503, "base_indisponible", "Base injoignable.", {"cause": "injoignable"}
        ) from exc

    series: list[Serie] = []
    for e in entites:
        titre = (
            titres_series.get(e.serie_id, "")
            if e.serie_id is not None
            else titres_kitsu.get(e.kitsu_id or 0, "")
        )
        passages = [
            Passage(
                fragment_id=f.chunk_id,
                doc_key=textes[f.chunk_id][0],
                type_document=TYPES_DOCUMENT[textes[f.chunk_id][1]],
                score=f.score,
                extrait=textes[f.chunk_id][2],
            )
            for f in e.fragments
        ]
        series.append(
            Serie(
                rang=e.rang,
                entite=e.entite,
                serie_id=e.serie_id,
                kitsu_id=e.kitsu_id,
                titre=titre,
                score=e.score,
                passages=passages,
            )
        )
    return series


def rechercher(
    etat: EtatApi, corps: Recherche, identifiant: str, debut: float
) -> Resultat:
    vecteur = encoder(etat, corps.question)
    entites = etat.classeur.classer(
        vecteur,
        catalogue_seul=corps.catalogue_seul,
        nombre=corps.nombre_series,
        passages=corps.passages_par_serie,
    )
    series = completer(etat, entites)
    e = etat.coherence.encodage
    return Resultat(
        series=series,
        contexte=Contexte(
            reglages=ReglagesAppliques(
                nombre_series=corps.nombre_series,
                passages_par_serie=corps.passages_par_serie,
                catalogue_seul=corps.catalogue_seul,
            ),
            modele=e.modele,
            revision=str(etat.coherence.info.get("model_sha", e.revision)),
            precision=e.precision_calcul,
            corpus_id=e.corpus_id,
            encodage_id=e.encodage_id,
            identifiant_requete=identifiant,
            duree_ms=round((time.perf_counter() - debut) * 1000, 3),
            version_api=__version__,
        ),
    )
