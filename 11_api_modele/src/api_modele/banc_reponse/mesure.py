"""La mesure : l'estimation, l'essai, le run, les scénarios ; les métriques et le
critère écrit avant la mesure.

**Critère** (décision 6 de l'étape, spec 06 C12), par profil : refus correct
≥ 4 / 5 sur F7 ; refus, ou mention « pas au catalogue », ≥ 4 / 5 sur les cinq
questions hors catalogue ; **zéro** série inventée ; **zéro** sortie invalide pour
un nom de membre. Pas de seuil sur le recouvrement ni sur F9 / F10 : à la première
mesure, on écrit le chiffre.

« Mention pas au catalogue » : la réponse (non refusée) contient l'une des formes de
`MENTION_HORS_CATALOGUE`, sans tenir compte de la casse. Une série inventée est une
sortie que la route rejette (503 `sortie_modele_invalide`, motif `serie_inconnue`) :
la réponse rendue n'en contient jamais, par construction.
"""

from __future__ import annotations

import math
import re
import statistics
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import httpx

from api_modele import banc
from api_modele.banc_reponse.espion import Espion, Releve, objet
from api_modele.banc_reponse.jeu import Question
from api_modele.configuration import ModeleLangage, Profil
from api_modele.fournisseurs import estimer_cout
from api_modele.modeles import Serie
from api_modele.pseudonymes import Pseudonymes
from api_modele.reponse import (
    INVITE_REFORMULATION,
    INVITE_REPONSE,
    LONGUEUR_REFORMULATION,
    message_de_reponse,
    titre_marque,
)

#: Le motif d'une sortie coupée par la borne de longueur (`details.motif`).
MOTIF_LONGUEUR = "longueur_atteinte"
CRITERE = {
    "refus_correct_F7_min": 4,
    "refus_correct_hors_catalogue_min": 4,
    "series_inventees_max": 0,
    "sorties_pseudonyme_max": 0,
}
MENTION_HORS_CATALOGUE = re.compile(
    r"pas (?:au|dans (?:le|notre)) catalogue|hors (?:du )?catalogue"
    r"|ne figure pas (?:au|dans)",
    re.IGNORECASE,
)
#: Suites de mots comptées pour le recouvrement réponse / passages.
NGRAMME = 8
#: Pause entre deux questions d'un run (débit des fournisseurs).
PAUSE_S = 1.0


class Client(Protocol):
    """Ce que le banc demande à l'API dans le processus (un `TestClient`)."""

    def post(
        self, url: str, *, json: object, headers: dict[str, str]
    ) -> httpx.Response: ...


class PlafondDepasse(Exception):
    """L'estimation, ou le coût réel cumulé, dépasse le plafond d'un run."""


@dataclass
class Banc:
    """L'API dans le processus, sa clé, ses réglages, sa liste de pseudonymes et
    l'espion des appels aux fournisseurs."""

    api: Client
    cle: str
    modele_langage: ModeleLangage
    pseudonymes: Pseudonymes
    espion: Espion
    pause_s: float = PAUSE_S
    dormir: Callable[[float], None] = time.sleep

    @property
    def entetes(self) -> dict[str, str]:
        return {"X-API-Key": self.cle}

    def profil(self, nom: str) -> Profil:
        profil = self.modele_langage.profil(nom)
        if profil is None:
            raise ValueError(f"profil inconnu : {nom}")
        if not profil.disponible:
            raise ValueError(f"profil indisponible (clé absente) : {nom}")
        return profil

    def rechercher(self, question: str, **reglages: object) -> list[Serie]:
        r = self.api.post(
            "/v1/recherche",
            json={"question": question, **reglages},
            headers=self.entetes,
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"/v1/recherche : {r.status_code} {r.json().get('code')}"
            )
        return [Serie.model_validate(s) for s in r.json()["series"]]


# --------------------------------------------------------------------------- #
#  Estimation (aucun appel)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Estimation:
    questions: int
    appels: int
    jetons_entree: int
    cout_usd: float

    def en_dict(self) -> dict[str, object]:
        return {
            "questions": self.questions,
            "appels": self.appels,
            "jetons_entree": self.jetons_entree,
            "cout_usd": round(self.cout_usd, 6),
        }


def caracteres_du_contexte(banc_: Banc, question: str) -> int:
    """Les caractères que l'appel de réponse enverrait (invite comprise) ; 0 si la
    recherche ne rend aucun passage (la route refuse alors sans appel)."""
    r = banc_.modele_langage.reponse
    series = banc_.rechercher(
        question,
        nombre_series=r.nombre_series.defaut,
        passages_par_serie=r.passages_par_serie.defaut,
        catalogue_seul=r.catalogue_seul,
    )
    if not any(s.passages for s in series):
        return 0
    return len(INVITE_REPONSE) + len(message_de_reponse("", question, series))


def longueur_par_defaut(banc_: Banc, profil: Profil) -> int:
    """La `longueur_max` d'un appel sans demande : celle du profil, sinon celle de
    la route — la règle de la couche des fournisseurs."""
    if profil.longueur_max is not None:
        return profil.longueur_max
    return banc_.modele_langage.reponse.longueur_max.defaut


def estimer(
    banc_: Banc, profil: Profil, textes: Iterable[str], longueur: int | None = None
) -> Estimation:
    """Le coût au plus d'une série d'appels de réponse : jetons d'entrée comptés
    par caractères, sortie au maximum (`longueur_max` demandée, sinon celle du
    profil, sinon celle par défaut), sans raisonnement — la formule de la couche des
    fournisseurs (`estimer_cout`)."""
    ml = banc_.modele_langage
    if longueur is None:
        longueur = longueur_par_defaut(banc_, profil)
    questions = appels = jetons = 0
    cout = 0.0
    for texte in textes:
        questions += 1
        caracteres = caracteres_du_contexte(banc_, texte)
        if not caracteres:
            continue
        entree = math.ceil(caracteres / ml.caracteres_par_jeton)
        estime = estimer_cout(profil, entree, longueur)
        if estime is None:
            raise PlafondDepasse(f"le profil {profil.nom} ne déclare pas de prix")
        appels += 1
        jetons += entree
        cout += estime
    return Estimation(questions, appels, jetons, cout)


def garder_plafond(estimation: Estimation, plafond: float) -> None:
    if estimation.cout_usd > plafond:
        raise PlafondDepasse(
            f"coût estimé {estimation.cout_usd:.4f} $ > plafond {plafond:.2f} $ :"
            " aucun appel"
        )


# --------------------------------------------------------------------------- #
#  Un appel de la route, et ce qu'on en garde
# --------------------------------------------------------------------------- #
@dataclass
class Appel:
    """Une requête de réponse : son statut, son corps, les appels aux fournisseurs
    qu'elle a faits, et le texte rendu (tenu à part, contrôlé)."""

    statut: int
    corps: dict[str, Any]
    releves: list[Releve]
    texte: str | None = None
    #: Le texte du modèle avant l'insertion des titres par la route (mémoire seule).
    texte_modele: str | None = None
    pseudonymes: int = 0
    passages: list[str] = field(default_factory=list[str])

    @property
    def code(self) -> str | None:
        return None if self.statut == 200 else cast(str | None, self.corps.get("code"))

    @property
    def motif(self) -> str | None:
        motif = objet(self.corps.get("details")).get("motif")
        return motif if isinstance(motif, str) else None

    def cout(self, profil: Profil, caracteres_par_jeton: float) -> float:
        return sum(r.cout(profil, caracteres_par_jeton) for r in self.releves)

    @property
    def releve_de_reponse(self) -> Releve | None:
        return next((r for r in reversed(self.releves) if r.role == "reponse"), None)

    @property
    def fournisseur_en_echec(self) -> bool:
        """Un fournisseur injoignable, en délai ou en refus : le run s'arrête. Une
        sortie coupée par la borne (`longueur_atteinte`) est une question en échec,
        pas un fournisseur en échec : le run continue."""
        return (self.code or "").startswith("fournisseur_") and (
            self.motif != MOTIF_LONGUEUR
        )


def noms_trouves(banc_: Banc, releve: Releve | None) -> list[dict[str, object]]:
    """Les noms de membres trouvés dans une sortie du modèle, par leur seule
    empreinte : figuraient-ils dans le message envoyé (passages, question) ; sont-ils
    admis comme homonymes, pour un document au moins ? Jamais un nom ni un texte."""
    if releve is None or releve.texte_modele is None:
        return []
    p = banc_.pseudonymes
    envoyes = set(p.empreintes_trouvees(releve.message_envoye))
    return [
        {
            "empreinte": h,
            "dans_le_message_envoye": h in envoyes,
            "homonyme_admis": p.homonyme_admis(h),
        }
        for h in p.empreintes_trouvees(releve.texte_modele)
    ]


def demander(banc_: Banc, corps: dict[str, object]) -> Appel:
    """Une requête de réponse ; le texte n'est gardé qu'après le contrôle des noms
    de membres (la route l'a déjà fait ; le banc le refait, sur la même liste)."""
    debut = len(banc_.espion.releves)
    r = banc_.api.post("/v1/reponse", json=corps, headers=banc_.entetes)
    appel = Appel(r.status_code, r.json(), banc_.espion.releves[debut:])
    if r.status_code != 200:
        return appel
    envoyes = [
        (cast(str, p["doc_key"]), cast(str, p["extrait"]))
        for s in appel.corps["series"]
        for p in s["passages"]
    ]
    appel.passages = [e for _, e in envoyes]
    texte = cast(str, appel.corps["reponse"])
    reformulee = appel.corps["contexte"].get("question_reformulee")
    question = cast(str, corps["question"])
    appel.pseudonymes = banc_.pseudonymes.fautifs(texte, envoyes, question) + (
        banc_.pseudonymes.fautifs(reformulee, [], question)
        if isinstance(reformulee, str)
        else 0
    )
    if appel.pseudonymes == 0:
        appel.texte = texte
        releve = appel.releve_de_reponse
        appel.texte_modele = None if releve is None else releve.texte_modele
    return appel


def releve_en_dict(r: Releve, profil: Profil, cpj: float) -> dict[str, object]:
    entree, sortie, raisonnement, estimes = r.jetons(profil, cpj)
    prompt = r.entier("prompt_tokens")
    return {
        "role": r.role,
        "statut": r.statut,
        "parametres_envoyes": {k: v for k, v in r.parametres.items() if k != "model"},
        "modele_demande": r.parametres.get("model"),
        "modele_rendu": r.modele_rendu,
        "usage": r.usage,
        "fin": r.fin,
        "forme_contenu": r.forme_contenu,
        "latence_ms": r.latence_ms,
        "erreur": r.erreur,
        "jetons": {
            "entree": entree,
            "sortie": sortie,
            "raisonnement": raisonnement,
            "estimes": estimes,
        },
        "caracteres_envoyes": r.caracteres_envoyes,
        "caracteres_par_jeton": (
            round(r.caracteres_envoyes / prompt, 3) if prompt else None
        ),
        "cout_usd": round(r.cout(profil, cpj), 6),
    }


# --------------------------------------------------------------------------- #
#  L'essai : un appel, ses chiffres
# --------------------------------------------------------------------------- #
def essai(
    banc_: Banc,
    profil: Profil,
    question: str,
    temperature: float | None = None,
    longueur_max: int | None = None,
) -> tuple[dict[str, object], str | None]:
    """Un appel de réponse sur un profil : (chiffres, texte contrôlé ou None)."""
    cpj = banc_.modele_langage.caracteres_par_jeton
    estimation = estimer(banc_, profil, [question], longueur_max)
    corps: dict[str, object] = {"question": question, "profil": profil.nom}
    if temperature is not None:
        corps["temperature"] = temperature
    if longueur_max is not None:
        corps["longueur_max"] = longueur_max
    appel = demander(banc_, corps)
    chiffres: dict[str, object] = {
        "profil": profil.nom,
        "fournisseur": profil.fournisseur,
        "modele": profil.modele,
        "statut_profil": profil.statut,
        "temperature_demandee": temperature,
        "longueur_max_demandee": longueur_max,
        "estimation": estimation.en_dict(),
        "statut": appel.statut,
        "code": appel.code,
        "details": None if appel.statut == 200 else appel.corps.get("details"),
        "appels": [releve_en_dict(r, profil, cpj) for r in appel.releves],
        "cout_reel_usd": round(appel.cout(profil, cpj), 6),
        "pseudonymes": appel.pseudonymes,
    }
    if appel.statut == 200:
        c = appel.corps["contexte"]
        chiffres |= {
            "refus": appel.corps["refus"],
            "motif_refus": appel.corps["motif_refus"],
            "nb_series_citees": len(appel.corps["series_citees"]),
            "reglages_appliques": c["reglages_appliques"],
            "latences_ms": c["latences_ms"],
            "date_appel": c["date_appel"],
            "longueur_reponse": len(appel.corps["reponse"]),
        }
    return chiffres, appel.texte


# --------------------------------------------------------------------------- #
#  Le run
# --------------------------------------------------------------------------- #
def recouvrement(texte: str, passages: list[str]) -> float | None:
    """Part des suites de `NGRAMME` mots de la réponse présentes telles quelles dans
    un passage envoyé (la fonction du module 10, réutilisée)."""
    calcul = cast(
        Callable[[str, list[str], int], float | None],
        banc.module("generation").part_copiee,
    )
    return calcul(texte, passages, NGRAMME)


@dataclass
class Run:
    profil: str
    statut: str = "termine"
    motif_interruption: str | None = None
    par_question: list[dict[str, object]] = field(
        default_factory=list[dict[str, object]]
    )
    textes: dict[str, str] = field(default_factory=dict[str, str])
    cout_reel_usd: float = 0.0
    #: Pris de la première réponse rendue : réglages appliqués, corpus, encodage.
    contexte: dict[str, object] = field(default_factory=dict[str, object])
    modeles_rendus: set[str] = field(default_factory=set[str])

    def noter(self, appel: Appel) -> None:
        self.modeles_rendus.update(
            r.modele_rendu for r in appel.releves if r.modele_rendu is not None
        )
        if appel.statut == 200 and not self.contexte:
            c = appel.corps["contexte"]
            self.contexte = {
                "reglages": c["reglages"],
                "reglages_appliques": c["reglages_appliques"],
                "corpus_id": c["corpus_id"],
                "encodage_id": c["encodage_id"],
                "version_api": c["version_api"],
                "date_appel": c["date_appel"],
            }


def mesurer(
    banc_: Banc, profil: Profil, questions: Sequence[Question], plafond: float
) -> Run:
    """Les questions, une à une, réglages par défaut, sans historique. Arrêt
    (`interrompu`) si le coût réel cumulé dépasse le plafond, ou si un fournisseur
    refuse ou ne répond pas : ce qui a été mesuré est gardé."""
    cpj = banc_.modele_langage.caracteres_par_jeton
    run = Run(profil.nom)
    for numero, q in enumerate(questions):
        if numero:
            banc_.dormir(banc_.pause_s)
        appel = demander(banc_, {"question": q.texte, "profil": profil.nom})
        run.noter(appel)
        cout = appel.cout(profil, cpj)
        run.cout_reel_usd += cout
        ligne: dict[str, object] = {
            "question_id": q.question_id,
            "famille": q.famille,
            "issue": q.issue,
            "statut": appel.statut,
            "code": appel.code,
            "motif": appel.motif,
            "cout_usd": round(cout, 6),
            "pseudonymes": appel.pseudonymes,
            "appels": [
                {
                    "role": r.role,
                    "statut": r.statut,
                    "modele_rendu": r.modele_rendu,
                    "jetons": dict(
                        zip(
                            ("entree", "sortie", "raisonnement", "estimes"),
                            r.jetons(profil, cpj),
                            strict=True,
                        )
                    ),
                    "fin": r.fin,
                    "latence_ms": r.latence_ms,
                    "forme_contenu": r.forme_contenu,
                }
                for r in appel.releves
            ],
        }
        if appel.statut == 200:
            ligne |= ligne_reponse(banc_, q, appel)
            if appel.texte is not None:
                run.textes[q.question_id] = appel.texte
        elif appel.code == "sortie_modele_invalide":
            # Ce que la route a refusé, sans son texte : la forme de la sortie et,
            # pour un nom de membre, ses empreintes.
            releve = appel.releve_de_reponse
            ligne["forme_sortie"] = None if releve is None else releve.forme
            if appel.motif == "pseudonyme":
                ligne["noms_trouves"] = noms_trouves(banc_, releve)
        run.par_question.append(ligne)
        if appel.fournisseur_en_echec:
            run.statut, run.motif_interruption = "interrompu", appel.code
            break
        if run.cout_reel_usd > plafond:
            run.statut = "interrompu"
            run.motif_interruption = (
                f"coût réel {run.cout_reel_usd:.4f} $ > plafond {plafond:.2f} $"
            )
            break
    return run


def ligne_reponse(banc_: Banc, q: Question, appel: Appel) -> dict[str, object]:
    """Ce qu'une réponse rendue apporte à la ligne de sa question (sans texte)."""
    corps = appel.corps
    texte = cast(str, corps["reponse"])
    citees = [cast(int, s["serie_id"]) for s in corps["series_citees"]]
    latences = corps["contexte"]["latences_ms"]
    # Ce que le modèle fait seul : sur son texte, avant l'insertion des titres, et
    # selon la règle de l'insertion (titre juste avant ou juste après la marque).
    titres = (
        None
        if appel.texte_modele is None
        else [
            titre_marque(
                cast(int, s["serie_id"]), cast(str, s["titre"]), appel.texte_modele
            )
            for s in corps["series_citees"]
        ]
    )
    ligne: dict[str, object] = {
        "refus": corps["refus"],
        "motif_refus": corps["motif_refus"],
        "series_citees": citees,
        "series_rendues": [s["serie_id"] for s in corps["series"]],
        "nb_series_citees": len(citees),
        "longueur_reponse": len(texte),
        "mention_hors_catalogue": bool(MENTION_HORS_CATALOGUE.search(texte)),
        "recouvrement_8": None
        if corps["refus"]
        else recouvrement(texte, appel.passages),
        "latence_modele_ms": latences["modele"],
        "latence_totale_ms": latences["totale"],
        "titres_presents": None if titres is None else titres.count(True),
        "titres_marques": None
        if titres is None
        else sum(1 for t in titres if t is not None),
    }
    if q.attend_une_serie:
        top = banc_.rechercher(q.texte)
        ligne["citee_attendue_1"] = any(c in q.attendues for c in citees)
        ligne["hit_rate_10"] = any(s.serie_id in q.attendues for s in top[:10])
    return ligne


# --------------------------------------------------------------------------- #
#  Métriques et critère
# --------------------------------------------------------------------------- #
def _centile(valeurs: list[float], rang: int) -> float | None:
    if not valeurs:
        return None
    if len(valeurs) == 1:
        return valeurs[0]
    return statistics.quantiles(valeurs, n=100, method="inclusive")[rang - 1]


def _nombres(lignes: list[dict[str, object]], cle: str) -> list[float]:
    return [float(cast(float, x[cle])) for x in lignes if x.get(cle) is not None]


def metriques(run: Run) -> dict[str, float]:
    """Les métriques du run, calculées par des règles, sans jury."""
    lignes = run.par_question
    rendues = [x for x in lignes if x["statut"] == 200]
    m: dict[str, float] = {
        "questions": len(lignes),
        "reponses_rendues": len(rendues),
        "refus_correct_F7": sum(
            1 for x in rendues if x["famille"] == "F7" and x["refus"]
        ),
        "questions_F7": sum(1 for x in lignes if x["famille"] == "F7"),
        "refus_correct_hors_catalogue": sum(
            1
            for x in rendues
            if x["issue"] == "reconnue_hors_catalogue"
            and (x["refus"] or x["mention_hors_catalogue"])
        ),
        "questions_hors_catalogue": sum(
            1 for x in lignes if x["issue"] == "reconnue_hors_catalogue"
        ),
        "refus_a_tort": sum(
            1 for x in rendues if x["issue"] == "au_catalogue" and x["refus"]
        ),
        "questions_attendant_une_serie": sum(
            1 for x in lignes if x["issue"] == "au_catalogue"
        ),
        "series_inventees": sum(1 for x in lignes if x["motif"] == "serie_inconnue"),
        "pseudonymes_rendus": sum(cast(int, x["pseudonymes"]) for x in lignes),
        "cout_usd": round(run.cout_reel_usd, 6),
    }
    for motif, n in Counter(
        cast(str, x["motif"]) for x in lignes if x["code"] == "sortie_modele_invalide"
    ).items():
        m[f"sorties_invalides_{motif}"] = n
    m["sorties_invalides"] = sum(
        1 for x in lignes if x["code"] == "sortie_modele_invalide"
    )
    m["longueur_atteinte"] = sum(1 for x in lignes if x["motif"] == MOTIF_LONGUEUR)
    de_rang = [x for x in rendues if "citee_attendue_1" in x]
    if de_rang:
        m["citee_attendue_1"] = sum(1 for x in de_rang if x["citee_attendue_1"])
        m["hit_rate_10"] = sum(1 for x in de_rang if x["hit_rate_10"])
        m["questions_de_rang"] = len(de_rang)
    titrees = [x for x in rendues if x.get("titres_presents") is not None]
    # Sur les séries citées que le texte marque ; les autres sont comptées à part.
    titres_cites = sum(cast(int, x["titres_marques"]) for x in titrees)
    if titrees:
        m["citees_sans_marque"] = (
            sum(cast(int, x["nb_series_citees"]) for x in titrees) - titres_cites
        )
    if titres_cites:
        presents = sum(cast(int, x["titres_presents"]) for x in titrees)
        m["titres_presents"] = presents
        m["titres_cites"] = titres_cites
        m["titre_present"] = round(presents / titres_cites, 4)
    recouvrements = _nombres(rendues, "recouvrement_8")
    if recouvrements:
        m["recouvrement_8_moyen"] = round(statistics.fmean(recouvrements), 4)
        m["recouvrement_8_max"] = round(max(recouvrements), 4)
    for type_ in ("entree", "sortie", "raisonnement"):
        valeurs = [
            cast(dict[str, int], a["jetons"])[type_]
            for x in lignes
            for a in cast(list[dict[str, object]], x["appels"])
        ]
        m[f"jetons_{type_}_somme"] = sum(valeurs)
        if valeurs:
            m[f"jetons_{type_}_moyenne"] = round(statistics.fmean(valeurs), 1)
    for nom in ("latence_modele_ms", "latence_totale_ms"):
        valeurs = _nombres(rendues, nom)
        mediane, p95 = _centile(valeurs, 50), _centile(valeurs, 95)
        if mediane is not None and p95 is not None:
            m[f"{nom}_mediane"] = round(mediane, 1)
            m[f"{nom}_p95"] = round(p95, 1)
    for nom in ("nb_series_citees", "longueur_reponse"):
        valeurs = _nombres(rendues, nom)
        if valeurs:
            m[f"{nom}_moyen"] = round(statistics.fmean(valeurs), 2)
    return m


def verdict(m: dict[str, float]) -> dict[str, object]:
    """Le critère écrit avant la mesure, point par point ; « sans objet » pour une
    famille absente du run."""
    points: dict[str, object] = {}
    if m["questions_F7"]:
        points["F7"] = m["refus_correct_F7"] >= CRITERE["refus_correct_F7_min"]
    if m["questions_hors_catalogue"]:
        points["hors_catalogue"] = (
            m["refus_correct_hors_catalogue"]
            >= CRITERE["refus_correct_hors_catalogue_min"]
        )
    points["series_inventees"] = (
        m["series_inventees"] <= CRITERE["series_inventees_max"]
    )
    points["pseudonymes"] = (
        m.get("sorties_invalides_pseudonyme", 0) + m["pseudonymes_rendus"]
        <= CRITERE["sorties_pseudonyme_max"]
    )
    points["tenu"] = all(v is True for v in points.values())
    return points


# --------------------------------------------------------------------------- #
#  Les scénarios de conversation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Tour:
    scenario: str
    tour: int
    question: str
    question_autonome_attendue: str
    serie_attendue_id: int | None


def premiere_serie(banc_: Banc, question: str) -> int | None:
    """La première série de la recherche, aux réglages de la route de réponse."""
    r = banc_.modele_langage.reponse
    series = banc_.rechercher(
        question,
        nombre_series=r.nombre_series.defaut,
        passages_par_serie=r.passages_par_serie.defaut,
        catalogue_seul=r.catalogue_seul,
    )
    return series[0].serie_id if series else None


def scenarios(
    banc_: Banc, profil: Profil, tours: Sequence[Tour], plafond: float
) -> Run:
    """Chaque scénario, tour après tour ; l'historique reconstruit à partir des
    réponses réelles des tours précédents. Pour chaque tour avec historique : la
    première série de la recherche sur la question reformulée, comparée à celle de
    la question autonome attendue (mêmes réglages)."""
    cpj = banc_.modele_langage.caracteres_par_jeton
    run = Run(profil.nom)
    historiques: dict[str, list[dict[str, str]]] = {}
    for numero, t in enumerate(tours):
        if numero:
            banc_.dormir(banc_.pause_s)
        historique = historiques.setdefault(t.scenario, [])
        corps: dict[str, object] = {"question": t.question, "profil": profil.nom}
        if historique:
            corps["historique"] = list(historique)
        appel = demander(banc_, corps)
        run.noter(appel)
        run.cout_reel_usd += appel.cout(profil, cpj)
        cle = f"{t.scenario}-{t.tour}"
        ligne: dict[str, object] = {
            "scenario": t.scenario,
            "tour": t.tour,
            "avec_historique": bool(historique),
            "statut": appel.statut,
            "code": appel.code,
            "motif": appel.motif,
            "pseudonymes": appel.pseudonymes,
            "cout_usd": round(appel.cout(profil, cpj), 6),
        }
        if appel.statut == 200:
            c = appel.corps["contexte"]
            reformulee = c.get("question_reformulee")
            ligne["refus"] = appel.corps["refus"]
            ligne["reformulation_faite"] = isinstance(reformulee, str)
            if historique:
                attendue = premiere_serie(banc_, t.question_autonome_attendue)
                obtenue = premiere_serie(
                    banc_, reformulee if isinstance(reformulee, str) else t.question
                )
                ligne |= {
                    "premiere_serie_reformulee": obtenue,
                    "premiere_serie_autonome": attendue,
                    "meme_serie": obtenue is not None and obtenue == attendue,
                    "serie_attendue_premiere": (
                        t.serie_attendue_id is not None
                        and obtenue == t.serie_attendue_id
                    ),
                }
            if appel.texte is not None:
                run.textes[cle] = appel.texte
                if isinstance(reformulee, str):
                    run.textes[f"{cle} (question reformulée)"] = reformulee
        run.par_question.append(ligne)
        historique.append({"role": "libraire", "contenu": t.question})
        historique.append(
            {"role": "assistant", "contenu": appel.texte or "(pas de réponse)"}
        )
        if appel.fournisseur_en_echec:
            run.statut, run.motif_interruption = "interrompu", appel.code
            break
        if run.cout_reel_usd > plafond:
            run.statut = "interrompu"
            run.motif_interruption = (
                f"coût réel {run.cout_reel_usd:.4f} $ > plafond {plafond:.2f} $"
            )
            break
    return run


def metriques_scenarios(run: Run) -> dict[str, float]:
    lignes = run.par_question
    avec = [x for x in lignes if x.get("avec_historique") and "meme_serie" in x]
    return {
        "tours": len(lignes),
        "tours_avec_historique": sum(1 for x in lignes if x.get("avec_historique")),
        "reformulation_meme_serie": sum(1 for x in avec if x["meme_serie"]),
        "reformulation_serie_attendue": sum(
            1 for x in avec if x["serie_attendue_premiere"]
        ),
        "pseudonymes_rendus": sum(cast(int, x["pseudonymes"]) for x in lignes),
        "sorties_invalides": sum(
            1 for x in lignes if x["code"] == "sortie_modele_invalide"
        ),
        "cout_usd": round(run.cout_reel_usd, 6),
    }


def estimer_scenarios(banc_: Banc, profil: Profil, tours: Sequence[Tour]) -> Estimation:
    """Au plus : par tour, l'appel de réponse sur la question autonome attendue,
    plus l'historique au maximum (réponses précédentes à `longueur_max` jetons) ; et,
    avec historique, l'appel de reformulation (historique et question, 150 jetons
    de sortie)."""
    ml = banc_.modele_langage
    longueur = longueur_par_defaut(banc_, profil)
    cpj = ml.caracteres_par_jeton
    appels = jetons = 0
    cout = 0.0
    precedents: dict[str, int] = {}
    for t in tours:
        historique = precedents.get(t.scenario, 0)
        base = caracteres_du_contexte(banc_, t.question_autonome_attendue)
        entree = math.ceil((base + historique) / cpj)
        appels += 1
        jetons += entree
        cout += estimer_cout(profil, entree, longueur) or math.inf
        if historique:
            entree_r = math.ceil(
                (len(INVITE_REFORMULATION) + historique + len(t.question)) / cpj
            )
            appels += 1
            jetons += entree_r
            cout += estimer_cout(profil, entree_r, LONGUEUR_REFORMULATION) or math.inf
        precedents[t.scenario] = (
            historique + len(t.question) + math.ceil(longueur * cpj)
        )
    return Estimation(len(tours), appels, jetons, cout)
