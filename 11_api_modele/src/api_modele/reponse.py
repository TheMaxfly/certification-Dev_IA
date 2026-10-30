"""La route de réponse : une question → une réponse rédigée, tirée des seuls passages.

1. Avant tout appel : le profil (déclaré, disponible) et les réglages qu'il n'expose
   pas (422) ; l'entrée elle-même est validée par sa forme (`DemandeReponse`).
2. Reformulation, s'il y a un historique et que `reformuler` est vrai : un premier
   appel écrit une question autonome (température 0, 150 jetons, fixés). Plus de 500
   caractères : la recherche se fait sur la question d'origine
   (`reformulation_ignoree = "trop_longue"`). Un nom de membre : 503, et la recherche
   n'a pas lieu.
3. Recherche : la fonction interne de `/v1/recherche`, mêmes réglages, même classement.
4. Aucun passage : 200, refus `aucun_passage`, SANS appel au modèle (déterministe,
   gratuit, et c'est ce que le mode strict dirait).
5. Contexte : par série, dans l'ordre du classement, `[S:<serie_id>] <titre> — <type>
   [F:<fragment_id>]` puis le texte du passage tel quel ; la question, l'historique
   plié. Appel de réponse, sortie contrainte par un schéma JSON strict.
6. Contrôles de sortie, dans cet ordre — `schema`, `serie_inconnue`,
   `fragment_inconnu`, `refus_incoherent`, `pseudonyme` — chacun : 503
   `sortie_modele_invalide`, son motif, son compteur ; la sortie fautive n'est ni
   rendue ni journalisée ; pas de second essai.
7. Réponse : les séries citées complétées par leur titre et leur rang (pris dans la
   recherche, jamais chez le modèle), les séries de la recherche, le `contexte`. Dans
   le texte, chaque `[S:<serie_id>]` sans le titre du catalogue juste avant ni juste
   après le reçoit devant lui (le modèle propose, une règle dispose).

L'historique, la question, la question reformulée et la réponse n'entrent jamais au
journal ; seuls leurs nombres et les codes, par la ligne de la requête.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from api_modele import __version__, recherche
from api_modele.configuration import RACINE_MODULE, Profil
from api_modele.erreurs import ErreurApi
from api_modele.etat import EtatApi
from api_modele.fournisseurs import (
    Appliques,
    Demandes,
    ErreurFournisseur,
    Messages,
    Role,
    Sortie,
)
from api_modele.modeles import (
    SCHEMA_REFORMULATION,
    SCHEMA_SORTIE,
    AppelModele,
    ContexteReponse,
    DemandeReponse,
    Latences,
    MotifRefus,
    Recherche,
    ReglagesAppliques,
    ReglagesReponseAppliques,
    Reponse,
    Resultat,
    Serie,
    SerieCitee,
    SortieModele,
    SortieReformulation,
    Tour,
)

INVITES = RACINE_MODULE / "config" / "invites"
INVITE_REPONSE = (INVITES / "reponse.md").read_text(encoding="utf-8").strip()
INVITE_REFORMULATION = (
    (INVITES / "reformulation.md").read_text(encoding="utf-8").strip()
)

#: L'appel de reformulation : fixé, pas exposé (décision de la spec 03).
TEMPERATURE_REFORMULATION = 0.0
LONGUEUR_REFORMULATION = 150
#: Au-delà, la question reformulée n'est pas envoyée à la recherche.
REFORMULATION_MAX = 500
LIBELLES = {
    "critique": "critique de tome",
    "synopsis_kitsu": "synopsis",
    "resume_serie": "résumé de série",
}
ETIQUETTES = {"libraire": "Libraire", "assistant": "Assistant"}
PHRASE_AUCUN_PASSAGE = (
    "Les passages disponibles ne permettent pas de répondre à cette question."
)
#: Le code d'une erreur de la couche des fournisseurs → son statut HTTP.
STATUTS: Mapping[str, int] = {
    "profil_inconnu": 422,
    "profil_indisponible": 422,
    "reglage_non_expose": 422,
    "fournisseur_injoignable": 503,
    "fournisseur_delai": 503,
    "fournisseur_refus": 503,
}


def plier(tours: list[Tour]) -> str:
    """L'historique en lignes étiquetées, dans l'ordre."""
    return "\n".join(f"{ETIQUETTES[t.role]} : {t.contenu}" for t in tours)


def entete(serie: Serie) -> str:
    """`[S:<serie_id>] <titre>` ; une œuvre hors catalogue n'a pas de `serie_id` :
    `[K:<kitsu_id>]`, qu'aucune citation ne peut viser."""
    if serie.serie_id is not None:
        return f"[S:{serie.serie_id}] {serie.titre}"
    return f"[K:{serie.kitsu_id}] {serie.titre}"


def contexte_des_passages(series: list[Serie]) -> str:
    """Les passages, série par série dans l'ordre du classement, textes tels quels ;
    chacun porte son identifiant `[F:<fragment_id>]`, que la sortie cite."""
    return "\n\n".join(
        f"{entete(s)} — {LIBELLES[p.type_document]} [F:{p.fragment_id}]\n{p.extrait}"
        for s in series
        for p in s.passages
    )


MARQUE_SERIE = re.compile(r"\[S:(\d+)\]")


def plat(texte: str) -> str:
    """Le texte sans casse ni rien d'autre que lettres et chiffres : un titre se
    retrouve ainsi malgré espaces, emphase, guillemets et ponctuation."""
    return re.sub(r"\W+", "", texte.casefold())


def accole(titre: str, marque: re.Match[str]) -> bool:
    """Le titre juste avant ou juste après la marque : la règle de `titrer`, que la
    mesure applique aussi au texte du modèle."""
    cle = plat(titre)
    avant = plat(marque.string[: marque.start()])
    apres = plat(marque.string[marque.end() :])
    return avant.endswith(cle) or apres.startswith(cle)


def titre_marque(serie_id: int, titre: str, texte: str) -> bool | None:
    """Le titre accolé à chaque marque de la série ; `None` si le texte ne la marque
    pas (rien à insérer, donc rien à juger)."""
    marques = [m for m in MARQUE_SERIE.finditer(texte) if int(m.group(1)) == serie_id]
    return all(accole(titre, m) for m in marques) if marques else None


def titrer(texte: str, series: list[Serie]) -> str:
    """Le titre du catalogue devant chaque `[S:<serie_id>]` qui ne l'a ni juste avant
    ni juste après ; une marque hors des séries rendues reste telle quelle."""
    titres = {s.serie_id: s.titre for s in series if s.serie_id is not None}

    def completer(m: re.Match[str]) -> str:
        titre = titres.get(int(m.group(1)))
        if titre is None or accole(titre, m):
            return m.group(0)
        return f"{titre} {m.group(0)}"

    return MARQUE_SERIE.sub(completer, texte)


def message_de_reponse(historique: str, question: str, series: list[Serie]) -> str:
    debut = f"{historique}\n\n" if historique else ""
    return (
        f"{debut}Question : {question}\n\nPASSAGES :\n\n{contexte_des_passages(series)}"
    )


def sortie_invalide(etat: EtatApi, motif: str, role: Role) -> ErreurApi:
    """503 ; le motif, le rôle de l'appel ; jamais la sortie elle-même."""
    etat.metriques.sortie_invalide.labels(motif=motif).inc()
    return ErreurApi(
        503,
        "sortie_modele_invalide",
        "La sortie du modèle ne tient pas le contrat de la réponse.",
        {"motif": motif, "role": role},
    )


def controler(
    etat: EtatApi, modele: SortieModele, series: list[Serie], question: str
) -> list[SerieCitee]:
    """Les contrôles de sens, dans l'ordre ; les séries citées, complétées.
    `question` : celle de la requête (un homonyme admis peut en venir)."""
    index = {s.serie_id: s for s in series if s.serie_id is not None}
    for citee in modele.series_citees:
        if citee.serie_id not in index:
            raise sortie_invalide(etat, "serie_inconnue", "reponse")
    for citee in modele.series_citees:
        connus = {p.fragment_id for p in index[citee.serie_id].passages}
        if any(f not in connus for f in citee.fragment_ids):
            raise sortie_invalide(etat, "fragment_inconnu", "reponse")
    if modele.refus:
        coherent = not modele.series_citees and modele.motif_refus is not None
    else:
        coherent = bool(modele.series_citees) and modele.motif_refus is None
    if not coherent:
        raise sortie_invalide(etat, "refus_incoherent", "reponse")
    envoyes = [(p.doc_key, p.extrait) for s in series for p in s.passages]
    if etat.pseudonymes.fautifs(modele.reponse, envoyes, question):
        raise sortie_invalide(etat, "pseudonyme", "reponse")
    return [
        SerieCitee(
            serie_id=c.serie_id,
            titre=index[c.serie_id].titre,
            rang=index[c.serie_id].rang,
            fragment_ids=c.fragment_ids,
        )
        for c in modele.series_citees
    ]


class Deroulement:
    """Ce qu'une requête de réponse a fait : ses appels, ses latences, ses traces
    (`suivi`, recopié dans la ligne de journal de la requête)."""

    def __init__(self, suivi: dict[str, object]) -> None:
        self.sorties: list[tuple[Role, Sortie]] = []
        self.latence_reformulation: float | None = None
        self.latence_modele: float | None = None
        self.suivi = suivi

    def appels(self) -> list[AppelModele]:
        return [
            AppelModele(
                role=role,
                jetons_entree=s.jetons.entree,
                jetons_sortie=s.jetons.sortie,
                jetons_raisonnement=s.jetons.raisonnement,
                estimes=s.jetons.estimes,
                latence_ms=s.latence_ms,
            )
            for role, s in self.sorties
        ]

    def cout(self) -> float | None:
        couts = [s.cout_estime_usd for _, s in self.sorties]
        if any(c is None for c in couts):
            return None
        return sum((c for c in couts if c is not None), 0.0)


async def appeler(
    etat: EtatApi,
    profil: Profil,
    identifiant: str,
    role: Role,
    messages: Messages,
    demandes: Demandes,
    schema: Mapping[str, object],
    deroulement: Deroulement,
) -> Sortie:
    """Un appel de la couche des fournisseurs ; son refus devient son code."""
    debut = time.perf_counter()
    try:
        sortie = await etat.fournisseurs.appeler(
            profil.nom, identifiant, role, messages, demandes, schema
        )
    except ErreurFournisseur as exc:
        raise ErreurApi(STATUTS[exc.code], exc.code, exc.message, exc.details) from exc
    latence = round((time.perf_counter() - debut) * 1000, 1)
    if role == "reformulation":
        deroulement.latence_reformulation = latence
    else:
        deroulement.latence_modele = latence
    deroulement.sorties.append((role, sortie))
    return sortie


def choisir_profil(etat: EtatApi, corps: DemandeReponse) -> Profil:
    """Avant tout appel : un profil déclaré, disponible, qui expose ce qu'on lui
    demande."""
    couche = etat.fournisseurs
    nom = corps.profil or couche.profil_actif
    profil = couche.profils.get(nom)
    if profil is None:
        raise ErreurApi(
            422,
            "profil_inconnu",
            f"Profil inconnu : « {nom} ».",
            {"profil": nom, "profils": list(couche.profils)},
        )
    if not profil.disponible:
        raise ErreurApi(
            422,
            "profil_indisponible",
            f"Le profil « {nom} » est indisponible.",
            {"profil": nom},
        )
    for reglage in ("temperature", "longueur_max"):
        if reglage in corps.model_fields_set and reglage in profil.reglages_absents:
            raise ErreurApi(
                422,
                "reglage_non_expose",
                f"Le profil « {nom} » n'expose pas « {reglage} ».",
                {"reglage": reglage, "profil": nom},
            )
    return profil


async def reformuler(
    etat: EtatApi,
    profil: Profil,
    corps: DemandeReponse,
    identifiant: str,
    deroulement: Deroulement,
) -> tuple[str | None, str | None]:
    """(question reformulée, motif d'abandon) ; (None, None) sans reformulation."""
    if not corps.historique or not corps.reformuler:
        deroulement.suivi["reformulation"] = "sans"
        return None, None
    absents = profil.reglages_absents
    demandes = Demandes(
        temperature=None if "temperature" in absents else TEMPERATURE_REFORMULATION,
        longueur_max=None if "longueur_max" in absents else LONGUEUR_REFORMULATION,
    )
    messages = Messages(
        INVITE_REFORMULATION,
        f"{plier(corps.historique)}\n\nQuestion : {corps.question}",
    )
    sortie = await appeler(
        etat,
        profil,
        identifiant,
        "reformulation",
        messages,
        demandes,
        SCHEMA_REFORMULATION,
        deroulement,
    )
    try:
        question = SortieReformulation.model_validate_json(sortie.texte).question
    except ValidationError:
        raise sortie_invalide(etat, "schema", "reformulation") from None
    if len(question) > REFORMULATION_MAX:
        deroulement.suivi["reformulation"] = "ignoree"
        return None, "trop_longue"
    if etat.pseudonymes.fautifs(question, [], corps.question):
        raise sortie_invalide(etat, "pseudonyme", "reformulation")
    deroulement.suivi["reformulation"] = "faite"
    return question, None


def reglages_appliques(
    corps: DemandeReponse, appliques: Appliques
) -> ReglagesReponseAppliques:
    return ReglagesReponseAppliques(
        temperature=appliques.temperature,
        longueur_max=appliques.longueur_max,
        mode=corps.mode,
        top_p=appliques.top_p,
        reasoning_effort=appliques.reasoning_effort,
    )


async def repondre(
    etat: EtatApi,
    corps: DemandeReponse,
    identifiant: str,
    debut: float,
    suivi: dict[str, object],
) -> Reponse:
    deroulement = Deroulement(suivi)
    suivi["nb_tours_historique"] = len(corps.historique)
    suivi["taille_historique"] = sum(len(t.contenu) for t in corps.historique)
    profil = choisir_profil(etat, corps)
    suivi["profil"] = profil.nom
    fournis = corps.model_fields_set
    demandes = Demandes(
        temperature=corps.temperature if "temperature" in fournis else None,
        longueur_max=corps.longueur_max if "longueur_max" in fournis else None,
    )
    appliques = etat.fournisseurs.appliques(profil, demandes)

    reformulee, ignoree = await reformuler(
        etat, profil, corps, identifiant, deroulement
    )
    question = reformulee or corps.question
    debut_recherche = time.perf_counter()
    resultat: Resultat = await run_in_threadpool(
        recherche.rechercher,
        etat,
        Recherche(
            question=question,
            nombre_series=corps.nombre_series,
            passages_par_serie=corps.passages_par_serie,
            catalogue_seul=corps.catalogue_seul,
        ),
        identifiant,
        debut_recherche,
    )
    latence_recherche = round((time.perf_counter() - debut_recherche) * 1000, 1)
    series = resultat.series

    refus, texte = True, PHRASE_AUCUN_PASSAGE
    motif: MotifRefus | None = "aucun_passage"
    citees: list[SerieCitee] = []
    if any(s.passages for s in series):
        messages = Messages(
            INVITE_REPONSE,
            message_de_reponse(plier(corps.historique), question, series),
        )
        sortie = await appeler(
            etat,
            profil,
            identifiant,
            "reponse",
            messages,
            demandes,
            SCHEMA_SORTIE,
            deroulement,
        )
        try:
            modele = SortieModele.model_validate_json(sortie.texte)
        except ValidationError:
            raise sortie_invalide(etat, "schema", "reponse") from None
        citees = controler(etat, modele, series, corps.question)
        refus, motif = modele.refus, modele.motif_refus
        texte = titrer(modele.reponse, series)

    cout = deroulement.cout()
    suivi.update(refus=refus, nb_series_citees=len(citees), cout_estime_usd=cout)
    totale = round((time.perf_counter() - debut) * 1000, 1)
    derniere = deroulement.sorties[-1][1].date_appel if deroulement.sorties else None
    c = resultat.contexte
    contexte: dict[str, Any] = {
        "reglages": ReglagesAppliques(
            nombre_series=corps.nombre_series,
            passages_par_serie=corps.passages_par_serie,
            catalogue_seul=corps.catalogue_seul,
        ),
        "modele": c.modele,
        "revision": c.revision,
        "precision": c.precision,
        "corpus_id": c.corpus_id,
        "encodage_id": c.encodage_id,
        "identifiant_requete": identifiant,
        "duree_ms": totale,
        "version_api": __version__,
    }
    return Reponse(
        refus=refus,
        motif_refus=motif,
        reponse=texte,
        series_citees=citees,
        series=series,
        contexte=ContexteReponse(
            **contexte,
            profil=profil.nom,
            fournisseur=profil.fournisseur,
            modele_langage=profil.modele,
            statut_profil="apercu" if profil.statut == "apercu" else "stable",
            date_appel=(
                derniere.isoformat(timespec="seconds") if derniere is not None else None
            ),
            reglages_appliques=reglages_appliques(corps, appliques),
            reglages_absents=list(profil.reglages_absents),
            question_reformulee=reformulee,
            reformulation_ignoree="trop_longue" if ignoree else None,
            appels=deroulement.appels(),
            cout_estime_usd=cout,
            latences_ms=Latences(
                recherche=latence_recherche,
                reformulation=deroulement.latence_reformulation,
                modele=deroulement.latence_modele,
                totale=totale,
            ),
        ),
    )
