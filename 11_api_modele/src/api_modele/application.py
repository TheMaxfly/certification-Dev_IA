"""L'application : routes `/v1/`, cycle de vie, enveloppe, erreurs.

Au démarrage, le pool est ouvert et la cohérence vérifiée (`demarrage.verifier`) ;
un écart, et l'API ne démarre pas. Les routes de ce bloc : `/v1/sante` (sans clé),
`/v1/info` (avec clé), `/metrics` (réseau interne). La recherche vient au bloc 03.
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from psycopg import Error as ErreurBase
from psycopg_pool import PoolTimeout

from api_modele import __version__, base, demarrage, erreurs, journal, service
from api_modele.configuration import Reglages
from api_modele.debit import Limiteur
from api_modele.enveloppe import Enveloppe
from api_modele.erreurs import Erreur, ErreurApi
from api_modele.etat import EtatApi, etat_de
from api_modele.metriques import Metriques, autorisee
from api_modele.modeles import (
    BorneBooleenne,
    BorneEntiere,
    Info,
    Question,
    ReglagesRecherche,
    Sante,
)
from api_modele.securite import exiger_cle
from api_modele.service import ErreurService, Instance, ServiceNonConforme

DESCRIPTION = """\
L'API du modèle d'embedding en service : une question entre, les séries du
catalogue classées par le modèle sortent, avec les passages qui les justifient.
Le classement est celui que le banc de mesure a mesuré.

Toutes les routes métier sont sous `/v1/` et demandent une clé (`X-API-Key`).
`/v1/` ne reçoit que des ajouts compatibles ; une rupture ouvre `/v2/`.
"""

REPONSES_CLE: dict[int | str, dict[str, object]] = {
    401: {"model": Erreur, "description": "Clé d'accès absente ou invalide."},
    429: {"model": Erreur, "description": "Débit dépassé pour cette clé."},
}

CleExigee = Annotated[str, Depends(exiger_cle)]


def creer_application(
    reglages: Reglages,
    *,
    instance: Instance | None = None,
    horloge: Callable[[], float] = time.monotonic,
) -> FastAPI:
    """L'application, prête à démarrer ; rien n'est ouvert avant son cycle de vie."""
    metriques = Metriques()
    contexte: dict[str, object] = {}

    @asynccontextmanager
    async def cycle(app: FastAPI) -> AsyncGenerator[None]:
        inst = instance or service.charger_instance(reglages.instance)
        client = service.client(inst, reglages)
        pool = base.creer_pool(reglages)
        try:
            try:
                pool.open(wait=True, timeout=reglages.pool_attente_s)
            except PoolTimeout as exc:
                raise demarrage.DemarrageRefuse(
                    "base injoignable : aucune connexion en"
                    f" {reglages.pool_attente_s} s"
                ) from exc
            coherence = demarrage.verifier(pool, client, inst, reglages)
        except demarrage.DemarrageRefuse as exc:
            journal.evenement("demarrage_refuse", {"raison": str(exc)}, 40)
            pool.close()
            raise
        encodage = coherence.encodage
        app.state.etat = EtatApi(
            reglages=reglages,
            pool=pool,
            client=client,
            coherence=coherence,
            limiteur=Limiteur(reglages.debit_par_minute, horloge),
            metriques=metriques,
        )
        contexte.update(corpus_id=encodage.corpus_id, encodage_id=encodage.encodage_id)
        metriques.encodage.labels(
            corpus_id=encodage.corpus_id, encodage_id=str(encodage.encodage_id)
        ).set(1)
        journal.evenement(
            "demarrage",
            {
                "version_api": __version__,
                "modele": encodage.modele,
                "revision": encodage.revision,
                "corpus_id": encodage.corpus_id,
                "encodage_id": encodage.encodage_id,
                "service": client.url,
            },
        )
        try:
            yield
        finally:
            pool.close()
            journal.evenement("arret", {})

    app = FastAPI(
        title="API du modèle",
        version=__version__,
        description=DESCRIPTION,
        lifespan=cycle,
        openapi_url=f"/{reglages.version}/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    erreurs.installer(app)
    app.state.bornes = {
        "nombre_series": (reglages.nombre_series.min, reglages.nombre_series.max),
        "passages_par_serie": (
            reglages.passages_par_serie.min,
            reglages.passages_par_serie.max,
        ),
        "question": (1, reglages.longueur_question_max),
    }
    app.add_middleware(
        Enveloppe,
        metriques=metriques,
        corps_octets_max=reglages.corps_octets_max,
        contexte=contexte,
    )
    prefixe = f"/{reglages.version}"

    @app.get(
        f"{prefixe}/sante",
        response_model=Sante,
        responses={503: {"model": Sante, "description": "Un composant ne répond pas."}},
        tags=["exploitation"],
        summary="Vivant ; base, service et encodage en service cohérents",
    )
    def sante(request: Request) -> JSONResponse:
        etat = etat_de(request)
        corps = sonder(etat)
        statut = 200 if corps.statut == "ok" else 503
        return JSONResponse(corps.model_dump(), status_code=statut)

    @app.get(
        f"{prefixe}/info",
        response_model=Info,
        responses=REPONSES_CLE,
        tags=["exploitation"],
        summary="Le modèle, l'encodage et le corpus servis ; réglages et bornes",
    )
    def info(request: Request, _cle: CleExigee) -> Info:
        return decrire(etat_de(request))

    @app.get("/metrics", include_in_schema=False)
    def exposer_metriques(request: Request) -> Response:
        reseaux = reglages.metriques_reseaux
        if not autorisee(request.client.host if request.client else None, reseaux):
            raise ErreurApi(404, "route_inconnue", "Route inconnue.")
        contenu, type_ = metriques.exposer()
        return Response(content=contenu, media_type=type_)

    return app


def sonder(etat: EtatApi) -> Sante:
    """Base, encodage en service, service : l'état de chacun, sans lever."""
    charge = etat.coherence.encodage
    base_ok, encodage = "injoignable", "inconnu"
    try:
        with base.transaction(etat.pool, etat.reglages) as cx:
            en_service = base.lire_en_service(cx)
        base_ok = "ok"
        encodage = (
            "ok"
            if en_service is not None and en_service.encodage_id == charge.encodage_id
            else "change"
        )
    except (ErreurBase, PoolTimeout):
        pass
    try:
        service.confronter_au_registre(etat.client, charge)
        service_ok = "ok"
    except ServiceNonConforme:
        service_ok = "incoherent"
    except ErreurService:
        service_ok = "injoignable"
    tout_ok = base_ok == service_ok == encodage == "ok"
    return Sante.model_validate(
        {
            "statut": "ok" if tout_ok else "degrade",
            "base": base_ok,
            "service_embedding": service_ok,
            "encodage": encodage,
        }
    )


def decrire(etat: EtatApi) -> Info:
    r = etat.reglages
    c = etat.coherence
    e = c.encodage
    return Info(
        version_api=__version__,
        modele=e.modele,
        revision=e.revision,
        precision=e.precision_calcul,
        dimension=e.dimension,
        outil=e.outil,
        outil_version=e.outil_version,
        corpus_id=e.corpus_id,
        encodage_id=e.encodage_id,
        nb_fragments=c.nb_fragments,
        reglages=ReglagesRecherche(
            nombre_series=BorneEntiere(
                defaut=r.nombre_series.defaut,
                min=r.nombre_series.min,
                max=r.nombre_series.max,
            ),
            passages_par_serie=BorneEntiere(
                defaut=r.passages_par_serie.defaut,
                min=r.passages_par_serie.min,
                max=r.passages_par_serie.max,
            ),
            catalogue_seul=BorneBooleenne(defaut=r.catalogue_seul),
        ),
        question=Question(longueur_max_caracteres=r.longueur_question_max),
        debit_par_minute=r.debit_par_minute,
    )
