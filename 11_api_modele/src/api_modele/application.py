"""L'application : routes `/v1/`, cycle de vie, enveloppe, erreurs.

Au démarrage, le pool est ouvert, la cohérence vérifiée (`demarrage.verifier`) et
le classement du banc chargé (rattachement et vecteurs de l'encodage en service) ;
puis la liste des pseudonymes est lue (`manga.v_pseudonymes`, gardée en mémoire) et
la couche des fournisseurs du modèle de langage est posée (son contrôle
d'exploitation, s'il est demandé : `/v1/models`) ; un écart, et l'API ne démarre pas.
Routes : `/v1/recherche` et `/v1/info` (avec clé), `/v1/sante` (sans clé), `/metrics`
(réseau interne), `/v1/openapi.json` (le contrat) et, sur réglage, `/v1/docs`
(Swagger UI).
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from psycopg import Error as ErreurBase
from psycopg_pool import PoolTimeout

from api_modele import (
    __version__,
    base,
    demarrage,
    documentation,
    erreurs,
    journal,
    memoire,
    pseudonymes,
    recherche,
    service,
)
from api_modele import reponse as route_reponse
from api_modele.classement import Classeur
from api_modele.configuration import Borne, Reglages
from api_modele.debit import Limiteur
from api_modele.enveloppe import Enveloppe
from api_modele.erreurs import Erreur, ErreurApi
from api_modele.etat import EtatApi, etat_de
from api_modele.fournisseurs import ErreurFournisseur, Fournisseurs
from api_modele.metriques import Metriques, autorisee
from api_modele.modeles import (
    BorneBooleenne,
    BorneEntiere,
    BorneReelle,
    DemandeReponse,
    HistoriqueDecrit,
    Info,
    ModeDecrit,
    ModeleLangage,
    ProfilDecrit,
    Question,
    Recherche,
    ReglagesRecherche,
    ReglagesReponse,
    Reponse,
    Resultat,
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

Depuis 1.1.0, `/v1/reponse` : un modèle de langage, choisi par profil, rédige en
français une réponse tirée des seuls passages de la recherche, ou refuse ; la réponse
porte ses sources, le profil, les réglages, les jetons, le coût et les latences.
"""

REPONSES_CLE: dict[int | str, dict[str, object]] = {
    401: {"model": Erreur, "description": "Clé d'accès absente ou invalide."},
    429: {"model": Erreur, "description": "Débit dépassé pour cette clé."},
}

REPONSES_RECHERCHE: dict[int | str, dict[str, object]] = {
    **REPONSES_CLE,
    413: {"model": Erreur, "description": "Corps de requête trop volumineux."},
    422: {
        "model": Erreur,
        "description": "Entrée invalide (`entree_invalide`) ou question trop longue"
        " (`question_trop_longue`).",
    },
    503: {
        "model": Erreur,
        "description": "Service d'embedding indisponible ou incohérent, base"
        " indisponible, ou encodage en service changé depuis le démarrage.",
    },
}

REPONSES_REPONSE: dict[int | str, dict[str, object]] = {
    **REPONSES_CLE,
    413: {"model": Erreur, "description": "Corps de requête trop volumineux."},
    422: {
        "model": Erreur,
        "description": "Entrée invalide (`entree_invalide`), question trop longue"
        " (`question_trop_longue`), historique invalide (`historique_invalide`),"
        " profil inconnu ou indisponible (`profil_inconnu`, `profil_indisponible`),"
        " réglage que le profil n'expose pas (`reglage_non_expose`). Aucun appel au"
        " modèle n'a eu lieu.",
    },
    503: {
        "model": Erreur,
        "description": "La recherche ne répond pas : service d'embedding"
        " indisponible (`service_embedding_indisponible`) ou incohérent"
        " (`service_embedding_incoherent`), base indisponible"
        " (`base_indisponible`), encodage en service changé depuis le démarrage"
        " (`encodage_en_service_change`) ; le fournisseur du modèle est injoignable"
        " (`fournisseur_injoignable`), trop lent (`fournisseur_delai`) ou refuse"
        " l'appel (`fournisseur_refus`) ; la sortie du modèle ne tient pas le"
        " contrat (`sortie_modele_invalide`, son motif dans les détails).",
    },
}
#: L'exemple exact du 503 de `/v1/sante` (dette de l'étape 2, soldée à l'étape 3).
EXEMPLE_SANTE_503 = {
    "statut": "degrade",
    "base": "injoignable",
    "service_embedding": "ok",
    "encodage": "inconnu",
}

CleExigee = Annotated[str, Depends(exiger_cle)]


def charger_classement(
    pool: base.Pool, reglages: Reglages, coherence: demarrage.Coherence
) -> Classeur:
    """Le classement du banc, pour l'encodage en service ; rendu de la mémoire."""
    encodage = coherence.encodage
    debut = time.perf_counter()
    try:
        with base.transaction(pool, reglages, reglages.delai_chargement_s) as cx:
            classeur = Classeur.charger(
                cx, encodage.corpus_id, reglages.table_vecteurs, encodage.encodage_id
            )
    except (ErreurBase, PoolTimeout, ValueError) as exc:
        raise demarrage.DemarrageRefuse(f"chargement du classement : {exc}") from exc
    if classeur.nb_fragments != coherence.nb_fragments:
        raise demarrage.DemarrageRefuse(
            f"{classeur.nb_fragments} fragments chargés, l'encodage en compte"
            f" {coherence.nb_fragments}"
        )
    memoire.rendre_au_systeme()
    journal.evenement(
        "chargement",
        {
            "corpus_id": classeur.corpus_id,
            "fragments": classeur.nb_fragments,
            "entites": classeur.nb_entites,
            "matrice_octets": classeur.octets_matrice,
            "duree_s": round(time.perf_counter() - debut, 1),
            "memoire_residente_octets": memoire.rss_octets(),
            "memoire_pic_octets": memoire.pic_octets(),
        },
    )
    return classeur


def creer_application(
    reglages: Reglages,
    *,
    instance: Instance | None = None,
    horloge: Callable[[], float] = time.monotonic,
    environ: Mapping[str, str] | None = None,
    homonymes: Path = pseudonymes.FICHIER_HOMONYMES,
) -> FastAPI:
    """L'application, prête à démarrer ; rien n'est ouvert avant son cycle de vie.

    `environ` : où la couche des fournisseurs lit les clés au moment de l'appel
    (l'environnement du processus par défaut) ; `homonymes` : la liste des
    homonymes admis (celle de l'étape 1 par défaut)."""
    metriques = Metriques()
    contexte: dict[str, object] = {}

    @asynccontextmanager
    async def cycle(app: FastAPI) -> AsyncGenerator[None]:
        inst = instance or service.charger_instance(reglages.instance)
        client = service.client(inst, reglages)
        pool = base.creer_pool(reglages)
        fournisseurs = Fournisseurs(
            reglages.modele_langage,
            os.environ if environ is None else environ,
            metriques,
        )
        try:
            try:
                pool.open(wait=True, timeout=reglages.pool_attente_s)
            except PoolTimeout as exc:
                raise demarrage.DemarrageRefuse(
                    "base injoignable : aucune connexion en"
                    f" {reglages.pool_attente_s} s"
                ) from exc
            coherence = demarrage.verifier(pool, client, inst, reglages)
            classeur = charger_classement(pool, reglages, coherence)
            noms = pseudonymes.charger(pool, reglages, homonymes)
            try:
                await fournisseurs.demarrer()
            except ErreurFournisseur as exc:
                raise demarrage.DemarrageRefuse(exc.message) from exc
        except demarrage.DemarrageRefuse as exc:
            journal.evenement("demarrage_refuse", {"raison": str(exc)}, 40)
            pool.close()
            await fournisseurs.fermer()
            raise
        encodage = coherence.encodage
        app.state.etat = EtatApi(
            reglages=reglages,
            pool=pool,
            client=client,
            coherence=coherence,
            classeur=classeur,
            limiteur=Limiteur(reglages.debit_par_minute, horloge),
            metriques=metriques,
            fournisseurs=fournisseurs,
            pseudonymes=noms,
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
                "profil_actif": fournisseurs.profil_actif,
                "profils_disponibles": [
                    nom for nom, p in fournisseurs.profils.items() if p.disponible
                ],
                # Des nombres seulement : jamais un nom.
                "pseudonymes": noms.nb_noms,
                "homonymes_admis": noms.nb_admis,
            },
        )
        try:
            yield
        finally:
            pool.close()
            await fournisseurs.fermer()
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
        "temperature": (
            reglages.modele_langage.reponse.temperature.min,
            reglages.modele_langage.reponse.temperature.max,
        ),
        "longueur_max": (
            reglages.modele_langage.reponse.longueur_max.min,
            reglages.modele_langage.reponse.longueur_max.max,
        ),
    }
    app.add_middleware(
        Enveloppe,
        metriques=metriques,
        corps_octets_max=reglages.corps_octets_max,
        contexte=contexte,
    )
    prefixe = f"/{reglages.version}"

    @app.post(
        f"{prefixe}/recherche",
        response_model=Resultat,
        responses=REPONSES_RECHERCHE,
        tags=["recherche"],
        summary="Les séries classées pour une question, avec leurs passages",
    )
    def rechercher(request: Request, corps: Recherche, _cle: CleExigee) -> Resultat:
        debut = time.perf_counter()
        etat = etat_de(request)
        if corps.identite is not None:
            request.state.identite = corps.identite
        request.state.reglages = {
            "nombre_series": corps.nombre_series,
            "passages_par_serie": corps.passages_par_serie,
            "catalogue_seul": corps.catalogue_seul,
        }
        if etat.reglages.journal_question:
            request.state.question = corps.question
        return recherche.rechercher(etat, corps, erreurs.identifiant(request), debut)

    @app.post(
        f"{prefixe}/reponse",
        response_model=Reponse,
        responses=REPONSES_REPONSE,
        tags=["reponse"],
        summary="Une réponse rédigée par le modèle de langage, des seuls passages",
    )
    async def repondre(
        request: Request, corps: DemandeReponse, _cle: CleExigee
    ) -> Reponse:
        debut = time.perf_counter()
        etat = etat_de(request)
        if corps.identite is not None:
            request.state.identite = corps.identite
        request.state.reglages = {
            "nombre_series": corps.nombre_series,
            "passages_par_serie": corps.passages_par_serie,
            "catalogue_seul": corps.catalogue_seul,
            "temperature": corps.temperature,
            "longueur_max": corps.longueur_max,
            "mode": corps.mode,
        }
        if etat.reglages.journal_question:
            request.state.question = corps.question
        # Des nombres et des codes : jamais un texte (journal de la requête).
        suivi: dict[str, object] = {}
        request.state.reponse = suivi
        return await route_reponse.repondre(
            etat, corps, erreurs.identifiant(request), debut, suivi
        )

    @app.get(
        f"{prefixe}/sante",
        response_model=Sante,
        responses={
            503: {
                "model": Sante,
                "description": "Un composant ne répond pas.",
                "content": {"application/json": {"example": EXEMPLE_SANTE_503}},
            }
        },
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

    if reglages.documentation:

        @app.get(f"{prefixe}/docs", include_in_schema=False)
        def documenter() -> HTMLResponse:
            contenu, politique = documentation.page(
                app.title, f"{prefixe}/openapi.json"
            )
            return HTMLResponse(contenu, headers={"Content-Security-Policy": politique})

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


def _borne(borne: Borne) -> BorneEntiere:
    return BorneEntiere(defaut=borne.defaut, min=borne.min, max=borne.max)


def decrire_modele_langage(etat: EtatApi) -> ModeleLangage:
    """Le profil actif, les profils tels que la couche les sert (disponibilité
    effective), les réglages de la réponse ; le nom des variables, jamais une clé."""
    ml = etat.reglages.modele_langage
    r = ml.reponse
    return ModeleLangage(
        profil_actif=etat.fournisseurs.profil_actif,
        profils=[
            ProfilDecrit(
                nom=p.nom,
                fournisseur=p.fournisseur,
                modele=p.modele,
                statut="apercu" if p.statut == "apercu" else "stable",
                disponible=p.disponible,
                reglages_absents=list(p.reglages_absents),
                variable_cle=p.variable_cle,
            )
            for p in etat.fournisseurs.profils.values()
        ],
        reglages_reponse=ReglagesReponse(
            temperature=BorneReelle(
                defaut=r.temperature.defaut,
                min=r.temperature.min,
                max=r.temperature.max,
            ),
            longueur_max=_borne(r.longueur_max),
            mode=ModeDecrit(defaut=r.mode_defaut, valeurs=list(r.modes)),
            historique=HistoriqueDecrit(
                tours_max=r.historique_tours_max,
                caracteres_max=r.historique_caracteres_max,
            ),
            reformuler=BorneBooleenne(defaut=r.reformuler),
            nombre_series=_borne(r.nombre_series),
            passages_par_serie=_borne(r.passages_par_serie),
            catalogue_seul=BorneBooleenne(defaut=r.catalogue_seul),
        ),
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
        modele_langage=decrire_modele_langage(etat),
    )
