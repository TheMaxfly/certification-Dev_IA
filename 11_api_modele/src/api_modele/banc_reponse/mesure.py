"""La mesure : l'estimation, l'essai, le run, les scénarios ; les métriques et le
critère écrit avant la mesure.

**Critère** (décision 6 de l'étape, spec 06 C12), par profil : refus correct
≥ 4 / 5 sur F7 ; refus, ou mention « pas au catalogue », ≥ 4 / 5 sur les cinq
questions hors catalogue ; **zéro** série inventée ; **zéro** sortie invalide pour
un nom de membre ; depuis les métadonnées sur la ligne de titre, **zéro** sortie
invalide `disponibilite` et `demographie`. Pas de seuil sur le recouvrement ni sur
F9 / F10 : à la première mesure, on écrit le chiffre.

« Mention pas au catalogue » : la réponse (non refusée) contient l'une des formes de
`MENTION_HORS_CATALOGUE`, sans tenir compte de la casse. Dire un titre absent des
passages en est une : lecture arrêtée après la première mesure qui l'a montré, et
écrite comme telle. Une série inventée est une sortie que la route rejette (503
`sortie_modele_invalide`, motif `serie_inconnue`) : la réponse rendue n'en contient
jamais, par construction.

**`catalogue_en_tort`** : une sortie `disponibilite` dont la ligne est démentie par un
passage envoyé (`ligne_dementie`). Le contrôle de la route ne change pas, le libraire
reçoit le 503 ; le banc la compte à part, et elle ne compte pas contre le critère :
ce n'est pas le modèle qui s'est trompé.

**Mode enrichi** (E3 étape 6, spec 06, E) : la même mesure, `mode: "enrichi"` dans
chaque requête ; le critère porte sur la partie stricte, inchangé : un 503
`complement_*` n'y entre pas (la question compte comme une sortie invalide, et le
refus correct qu'elle portait peut-être n'est pas compté) ; un nom de membre que le
banc trouverait dans un complément **rendu** y entre (`pseudonymes_rendus`), comme
dans la réponse. Métriques du complément : `complement_present` (sur les questions),
`complement_quand_refus` (sur `refus_rendus`), et pour F7 et pour les questions hors
catalogue ; `complement_titres_moyen` (sur les compléments rendus, absente sans
complément) ; `sorties_invalides_complement` (tous motifs `complement_*`, chacun
compté aussi à part) ; `complement_titre_non_declare` : les compléments dont le texte
nomme un titre du catalogue absent de `complement_titres` (même suite de mots,
lettres et chiffres seuls, sans casse ni accents ; même clé `titre_comparable` pour
dire « déclaré ») ; `complement_titre_hors_controle` : parmi eux, ceux dont le titre
n'est pas non plus celui d'une série rendue — la limite du contrôle de la route.
Titres trouvés gardés par question ; un titre court du catalogue peut être un mot du
texte (faux positif), un titre écrit autrement (« Dragonball » pour « Dragon Ball »)
n'est pas trouvé (faux négatif) : la lecture tranche. L'estimation compte en enrichi
les questions sans passage : l'appel a lieu (question 33).
"""

from __future__ import annotations

import math
import re
import statistics
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import httpx

from api_modele import affirmations, banc
from api_modele.banc_reponse.espion import Espion, Releve, objet
from api_modele.banc_reponse.jeu import Question
from api_modele.base import titre_comparable
from api_modele.configuration import ModeleLangage, Profil
from api_modele.fournisseurs import estimer_cout
from api_modele.modeles import Serie
from api_modele.pseudonymes import Pseudonymes
from api_modele.reponse import (
    INVITE_REFORMULATION,
    LONGUEUR_REFORMULATION,
    MARQUE_SERIE,
    invite_de,
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
    "sorties_disponibilite_max": 0,
    "sorties_demographie_max": 0,
}
#: Les motifs d'une sortie que la ligne de titre contredit.
MOTIFS_DE_LIGNE = ("disponibilite", "demographie")
MENTION_HORS_CATALOGUE = re.compile(
    r"pas (?:au|dans (?:le|notre)) catalogue|hors (?:du )?catalogue"
    r"|ne figure pas (?:au|dans)"
    r"|n['’]appara[iî]t pas dans les passages"
    r"|pas (?:être )?mentionn[ée]e?s? dans les passages",
    re.IGNORECASE,
)
#: Une édition française dite telle quelle dans un passage, en plus des formes du
#: contrôle ; sur le texte aplati (`affirmations.aplatir`).
EDITION_FRANCAISE = re.compile(
    r"\b(?:version|edition|traduction|parution)s?\s+francaises?\b"
)
#: Un passage du message envoyé : sa ligne de titre, son texte, jusqu'à la suivante.
PASSAGE_DU_MESSAGE = re.compile(
    r"^\[S:(\d+)\] [^\n]*\[F:\d+\]\n(.*?)(?=\n\n\[[SK]:\d+\] |\Z)",
    re.MULTILINE | re.DOTALL,
)
#: Suites de mots comptées pour le recouvrement réponse / passages.
NGRAMME = 8
#: Pause entre deux questions d'un run (débit des fournisseurs).
PAUSE_S = 1.0
#: Un refus de débit du fournisseur dans un scénario : quand la requête échoue malgré
#: le nouvel essai de la route, le tour se rejoue après cette attente, `REPRISES_DEBIT`
#: fois au plus.
STATUT_DEBIT = 429
TYPE_DEBIT = "rate_limited"
ATTENTE_DEBIT_S = 10.0
REPRISES_DEBIT = 3
#: Les deux modes de la route (`DemandeReponse.mode`) ; en strict, la requête du banc
#: reste celle d'avant le mode enrichi (sans le champ `mode`).
MODES = ("strict", "enrichi")
PREFIXE_COMPLEMENT = "complement_"
MOT = re.compile(r"[^\W_]+")


def mots(texte: str) -> tuple[str, ...]:
    """Les mots d'un texte, lettres et chiffres seuls, sans casse ni accents."""
    return tuple(MOT.findall(affirmations.aplatir(texte)))


def aucun_titre() -> list[str]:
    """Le catalogue d'un banc qui ne le lit pas (le mode strict n'en a pas besoin)."""
    return []


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
    #: Les titres du catalogue (`manga.ms_series_enriched`), lus une fois, à la
    #: première question : pour compter les titres que le complément nomme sans les
    #: déclarer.
    catalogue: Callable[[], Sequence[str]] = aucun_titre
    _formes: dict[tuple[str, ...], str] | None = field(
        default=None, init=False, repr=False
    )

    @property
    def entetes(self) -> dict[str, str]:
        return {"X-API-Key": self.cle}

    def charger_catalogue(self) -> dict[tuple[str, ...], str]:
        """Les titres du catalogue par leur suite de mots, lus une fois — avant le
        premier appel d'une mesure enrichie : une base en défaut arrête le banc avant
        toute dépense."""
        if self._formes is None:
            formes: dict[tuple[str, ...], str] = {}
            for titre in self.catalogue():
                suite = mots(titre)
                if suite:
                    formes.setdefault(suite, titre)
            self._formes = formes
        return self._formes

    def titres_non_declares(self, texte: str, declares: Sequence[str]) -> list[str]:
        """Les titres du catalogue que `texte` nomme — même suite de mots, lettres et
        chiffres seuls, sans casse ni accents — et qui ne sont pas des titres de
        `declares` (même clé `titre_comparable`) ; le plus long d'abord, titres
        déclarés compris : un titre contenu dans un autre, ou dans un titre déclaré,
        ne compte pas. Dans l'ordre du texte, sans doublon."""
        formes = self.charger_catalogue()
        declarees = {mots(t) for t in declares} - {()}
        longueur = max((len(f) for f in (*formes, *declarees)), default=0)
        suite = mots(texte)
        trouvees: list[tuple[int, int, str | None]] = []
        for debut in range(len(suite)):
            for n in range(1, min(longueur, len(suite) - debut) + 1):
                forme = suite[debut : debut + n]
                if forme in declarees:
                    trouvees.append((debut, debut + n, None))
                elif forme in formes:
                    trouvees.append((debut, debut + n, formes[forme]))
        prises: list[tuple[int, int, str | None]] = []
        for debut, fin, titre in sorted(trouvees, key=lambda x: x[0] - x[1]):
            if all(fin <= a or b <= debut for a, b, _ in prises):
                prises.append((debut, fin, titre))
        cles = {titre_comparable(t) for t in declares}
        trouves: list[str] = []
        for _, _, titre in sorted(prises, key=lambda x: x[0]):
            if titre is None or titre_comparable(titre) in cles or titre in trouves:
                continue
            trouves.append(titre)
        return trouves

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
    #: Les questions dont la recherche ne rend aucun passage : sans appel en strict,
    #: avec appel en enrichi (question 33).
    sans_passage: int = 0

    def en_dict(self) -> dict[str, object]:
        return {
            "questions": self.questions,
            "appels": self.appels,
            "sans_passage": self.sans_passage,
            "jetons_entree": self.jetons_entree,
            "cout_usd": round(self.cout_usd, 6),
        }


def contexte_envoye(
    banc_: Banc, question: str, mode: str = "strict"
) -> tuple[int, bool]:
    """(caractères que l'appel de réponse enverrait, invite comprise ; la recherche
    est-elle sans passage). Sans passage, 0 en strict (la route refuse sans appel) ;
    en enrichi, l'appel a lieu, contexte vide de passages (question 33)."""
    r = banc_.modele_langage.reponse
    series = banc_.rechercher(
        question,
        nombre_series=r.nombre_series.defaut,
        passages_par_serie=r.passages_par_serie.defaut,
        catalogue_seul=r.catalogue_seul,
    )
    vide = not any(s.passages for s in series)
    if vide and mode != "enrichi":
        return 0, True
    return len(invite_de(mode)) + len(message_de_reponse("", question, series)), vide


def caracteres_du_contexte(banc_: Banc, question: str, mode: str = "strict") -> int:
    """Les caractères que l'appel de réponse enverrait (`contexte_envoye`)."""
    return contexte_envoye(banc_, question, mode)[0]


def longueur_par_defaut(banc_: Banc, profil: Profil) -> int:
    """La `longueur_max` d'un appel sans demande : celle du profil, sinon celle de
    la route — la règle de la couche des fournisseurs."""
    if profil.longueur_max is not None:
        return profil.longueur_max
    return banc_.modele_langage.reponse.longueur_max.defaut


def estimer(
    banc_: Banc,
    profil: Profil,
    textes: Iterable[str],
    longueur: int | None = None,
    mode: str = "strict",
) -> Estimation:
    """Le coût au plus d'une série d'appels de réponse : jetons d'entrée comptés
    par caractères, sortie au maximum (`longueur_max` demandée, sinon celle du
    profil, sinon celle par défaut), sans raisonnement — la formule de la couche des
    fournisseurs (`estimer_cout`). En enrichi : l'invite avec le bloc du complément,
    et un appel aussi pour une question sans passage."""
    ml = banc_.modele_langage
    if longueur is None:
        longueur = longueur_par_defaut(banc_, profil)
    questions = appels = jetons = vides = 0
    cout = 0.0
    for texte in textes:
        questions += 1
        caracteres, vide = contexte_envoye(banc_, texte, mode)
        vides += vide
        if not caracteres:
            continue
        entree = math.ceil(caracteres / ml.caracteres_par_jeton)
        estime = estimer_cout(profil, entree, longueur)
        if estime is None:
            raise PlafondDepasse(f"le profil {profil.nom} ne déclare pas de prix")
        appels += 1
        jetons += entree
        cout += estime
    return Estimation(questions, appels, jetons, cout, vides)


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
    #: Le complément rendu (mode enrichi), gardé comme le texte : après le contrôle
    #: des noms de membres refait par le banc.
    complement: str | None = None

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


def noms_trouves(
    banc_: Banc, releve: Releve | None, champ: str = "reponse"
) -> list[dict[str, object]]:
    """Les noms de membres trouvés dans une sortie du modèle (`champ` : sa réponse,
    ou son complément), par leur seule empreinte : figuraient-ils dans le message
    envoyé (passages, question) ; sont-ils admis comme homonymes, pour un document au
    moins ? Jamais un nom ni un texte."""
    if releve is None:
        return []
    texte = releve.complement_modele if champ == "complement" else releve.texte_modele
    if texte is None:
        return []
    p = banc_.pseudonymes
    envoyes = set(p.empreintes_trouvees(releve.message_envoye))
    return [
        {
            "empreinte": h,
            "dans_le_message_envoye": h in envoyes,
            "homonyme_admis": p.homonyme_admis(h),
        }
        for h in p.empreintes_trouvees(texte)
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
    ajout = appel.corps.get("complement")
    if isinstance(ajout, str):
        titres = [
            str(t)
            for t in cast(list[object], appel.corps.get("complement_titres") or [])
        ]
        appel.pseudonymes += banc_.pseudonymes.fautifs(
            "\n".join([ajout, *titres]), envoyes, question
        )
    if appel.pseudonymes == 0:
        appel.texte = texte
        releve = appel.releve_de_reponse
        appel.texte_modele = None if releve is None else releve.texte_modele
        appel.complement = ajout if isinstance(ajout, str) else None
    return appel


def ligne_dementie(message: str, serie_id: int) -> bool:
    """Un passage de la série, tel qu'envoyé au modèle, dit-il une édition française ?
    La ligne qui dit « seulement au Japon » ou « inconnue » est alors démentie : le
    catalogue a tort, pas le modèle."""
    for serie, passage in PASSAGE_DU_MESSAGE.findall(message):
        if int(serie) != serie_id:
            continue
        texte = affirmations.aplatir(passage)
        for motif in (affirmations.FORMES.disponibilite, EDITION_FRANCAISE):
            if any(affirmations.affirmee(texte, f) for f in motif.finditer(texte)):
                return True
    return False


def garder_la_contredite(
    banc_: Banc, run: Run, cle: str, appel: Appel, ligne: dict[str, object]
) -> None:
    """Une sortie que la ligne de titre contredit : la série visée, si le catalogue a
    tort et, pour la lecture humaine (hors dépôt, hors MLflow), son texte s'il ne porte
    aucun nom de membre."""
    if appel.motif not in MOTIFS_DE_LIGNE:
        return
    serie_id = objet(appel.corps.get("details")).get("serie_id")
    ligne["serie_contredite"] = serie_id
    releve = appel.releve_de_reponse
    if appel.motif == "disponibilite" and isinstance(serie_id, int):
        ligne["catalogue_en_tort"] = releve is not None and ligne_dementie(
            releve.message_envoye, serie_id
        )
    texte = None if releve is None else releve.texte_modele
    if texte is not None and not banc_.pseudonymes.empreintes_trouvees(texte):
        run.textes[f"{cle} (sortie refusée, {appel.motif})"] = texte


def garder_le_complement_refuse(banc_: Banc, run: Run, cle: str, appel: Appel) -> None:
    """Une sortie refusée pour son complément (`complement_*`) : pour la lecture
    humaine (hors dépôt, hors MLflow), la réponse et le complément du modèle, si ni
    l'un ni l'autre ne porte un nom de membre."""
    releve = appel.releve_de_reponse
    if releve is None or not (appel.motif or "").startswith(PREFIXE_COMPLEMENT):
        return
    ajout = releve.complement_modele
    if ajout is None:
        return
    texte = f"{releve.texte_modele or ''}\n\nComplément refusé : {ajout}"
    if banc_.pseudonymes.empreintes_trouvees(texte):
        return
    run.textes[f"{cle} (sortie refusée, {appel.motif})"] = texte


def appels_de(appel: Appel, profil: Profil, cpj: float) -> list[dict[str, object]]:
    """Les appels aux fournisseurs d'une requête, en nombres et en noms."""
    return [
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
    ]


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
def corps_de_requete(question: str, profil: Profil, mode: str) -> dict[str, object]:
    """La requête de réponse du banc ; `mode` seulement en enrichi (en strict, la
    requête d'avant le mode enrichi, octet pour octet)."""
    corps: dict[str, object] = {"question": question, "profil": profil.nom}
    if mode != "strict":
        corps["mode"] = mode
    return corps


def ligne_complement(banc_: Banc, appel: Appel) -> dict[str, object]:
    """Ce qu'une réponse rendue en enrichi dit de son complément, sans son texte :
    présence, nombre de titres déclarés, longueur ; les titres du catalogue que le
    texte nomme sans les déclarer, et parmi eux ceux que la recherche n'a pas rendus
    — ceux-là, la route ne les a pas contrôlés (des titres du catalogue, jamais un
    nom)."""
    ajout = appel.corps.get("complement")
    titres = [
        str(t) for t in cast(list[object], appel.corps.get("complement_titres") or [])
    ]
    texte = ajout if isinstance(ajout, str) else ""
    non_declares = banc_.titres_non_declares(texte, titres) if texte else []
    rendus = {
        titre_comparable(str(s.get("titre", "")))
        for s in cast(list[dict[str, object]], appel.corps.get("series") or [])
        if s.get("serie_id") is not None
    }
    return {
        "complement_present": bool(texte),
        "complement_titres": len(titres),
        "complement_longueur": len(texte),
        "complement_titres_non_declares": non_declares,
        "complement_titres_hors_controle": [
            t for t in non_declares if titre_comparable(t) not in rendus
        ],
    }


def essai_appel(
    banc_: Banc,
    profil: Profil,
    question: str,
    temperature: float | None = None,
    longueur_max: int | None = None,
    mode: str = "strict",
) -> tuple[dict[str, object], Appel]:
    """Un appel de réponse sur un profil : (chiffres, l'appel — son texte et son
    complément contrôlés, ou None)."""
    cpj = banc_.modele_langage.caracteres_par_jeton
    estimation = estimer(banc_, profil, [question], longueur_max, mode)
    if mode == "enrichi":
        banc_.charger_catalogue()
    corps = corps_de_requete(question, profil, mode)
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
        "mode": mode,
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
        if mode == "enrichi":
            chiffres |= ligne_complement(banc_, appel)
    return chiffres, appel


def essai(
    banc_: Banc,
    profil: Profil,
    question: str,
    temperature: float | None = None,
    longueur_max: int | None = None,
    mode: str = "strict",
) -> tuple[dict[str, object], str | None]:
    """Un appel de réponse sur un profil : (chiffres, texte contrôlé ou None)."""
    chiffres, appel = essai_appel(
        banc_, profil, question, temperature, longueur_max, mode
    )
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
    mode: str = "strict"

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
    banc_: Banc,
    profil: Profil,
    questions: Sequence[Question],
    plafond: float,
    mode: str = "strict",
) -> Run:
    """Les questions, une à une, réglages par défaut, sans historique. Arrêt
    (`interrompu`) si le coût réel cumulé dépasse le plafond, ou si un fournisseur
    refuse ou ne répond pas : ce qui a été mesuré est gardé. En enrichi, le
    complément de chaque réponse rendue (hors dépôt, pour la lecture de Max) et ses
    chiffres."""
    cpj = banc_.modele_langage.caracteres_par_jeton
    run = Run(profil.nom, mode=mode)
    if mode == "enrichi":
        banc_.charger_catalogue()
    for numero, q in enumerate(questions):
        if numero:
            banc_.dormir(banc_.pause_s)
        appel = demander(banc_, corps_de_requete(q.texte, profil, mode))
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
            "appels": appels_de(appel, profil, cpj),
        }
        if appel.statut == 200:
            ligne |= ligne_reponse(banc_, q, appel)
            if mode == "enrichi":
                ligne |= ligne_complement(banc_, appel)
            if appel.texte is not None:
                run.textes[q.question_id] = appel.texte
            if appel.complement:
                apres = ", après un refus" if appel.corps["refus"] else ""
                run.textes[f"{q.question_id} (complément, non vérifié{apres})"] = (
                    appel.complement
                )
        elif appel.code == "sortie_modele_invalide":
            # Ce que la route a refusé, sans son texte : la forme de la sortie et,
            # pour un nom de membre, ses empreintes.
            releve = appel.releve_de_reponse
            ligne["forme_sortie"] = None if releve is None else releve.forme
            if appel.motif == "pseudonyme":
                ligne["noms_trouves"] = noms_trouves(banc_, releve)
            if appel.motif == "complement_pseudonyme":
                ligne["noms_trouves"] = noms_trouves(banc_, releve, "complement")
            garder_la_contredite(banc_, run, q.question_id, appel, ligne)
            garder_le_complement_refuse(banc_, run, q.question_id, appel)
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
        # Ce que la ligne de titre disait des séries citées : la lecture s'y confronte.
        "metadonnees_citees": {
            s["serie_id"]: s["metadonnees"]
            for s in corps["series"]
            if s["serie_id"] in citees
        },
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
    m["catalogue_en_tort"] = sum(1 for x in lignes if x.get("catalogue_en_tort"))
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
        # Un appel refusé n'a pas de jetons : ceux de son relevé ne sont qu'estimés.
        valeurs = [
            cast(dict[str, int], a["jetons"])[type_]
            for x in lignes
            for a in cast(list[dict[str, object]], x["appels"])
            if a["statut"] == 200
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
    if run.mode == "enrichi":
        m |= metriques_du_complement(lignes, rendues)
    return m


def metriques_du_complement(
    lignes: list[dict[str, object]], rendues: list[dict[str, object]]
) -> dict[str, float]:
    """Les métriques du complément (mode enrichi) ; le critère n'en lit aucune."""
    presents = [x for x in rendues if x.get("complement_present")]
    refus = [x for x in rendues if x["refus"]]
    apports = [x for x in refus if x.get("complement_present")]
    titres = _nombres(presents, "complement_titres")
    m: dict[str, float] = {
        "complement_present": len(presents),
        "refus_rendus": len(refus),
        "complement_quand_refus": len(apports),
        "refus_rendus_F7": sum(1 for x in refus if x["famille"] == "F7"),
        "complement_quand_refus_F7": sum(1 for x in apports if x["famille"] == "F7"),
        "refus_rendus_hors_catalogue": sum(
            1 for x in refus if x["issue"] == "reconnue_hors_catalogue"
        ),
        "complement_quand_refus_hors_catalogue": sum(
            1 for x in apports if x["issue"] == "reconnue_hors_catalogue"
        ),
        "complement_titre_non_declare": sum(
            1 for x in presents if x.get("complement_titres_non_declares")
        ),
        "complement_titre_hors_controle": sum(
            1 for x in presents if x.get("complement_titres_hors_controle")
        ),
        "sorties_invalides_complement": sum(
            1
            for x in lignes
            if x["code"] == "sortie_modele_invalide"
            and str(x["motif"]).startswith(PREFIXE_COMPLEMENT)
        ),
    }
    if presents:  # une moyenne sans complément rendu est sans objet
        m["complement_titres_moyen"] = round(statistics.fmean(titres), 2)
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
    points["disponibilite"] = (
        m.get("sorties_invalides_disponibilite", 0) - m.get("catalogue_en_tort", 0)
        <= CRITERE["sorties_disponibilite_max"]
    )
    points["demographie"] = (
        m.get("sorties_invalides_demographie", 0) <= CRITERE["sorties_demographie_max"]
    )
    points["tenu"] = all(v is True for v in points.values())
    return points


def relire(
    banc_: Banc,
    lignes: Sequence[dict[str, object]],
    textes: Mapping[str, str],
    questions: Mapping[str, Question],
) -> list[dict[str, object]]:
    """Les lignes d'un run mesuré, relues selon les règles d'aujourd'hui : la mention
    sur le texte gardé ; `catalogue_en_tort` sur le message que la route enverrait (la
    recherche seule, aucun appel à un fournisseur). Rien d'autre ne change."""
    r = banc_.modele_langage.reponse
    relues: list[dict[str, object]] = []
    for ligne in lignes:
        relue = dict(ligne)
        cle = cast(str, ligne["question_id"])
        if ligne["statut"] == 200 and cle in textes:
            relue["mention_hors_catalogue"] = bool(
                MENTION_HORS_CATALOGUE.search(textes[cle])
            )
        serie_id = ligne.get("serie_contredite")
        if ligne["motif"] == "disponibilite" and isinstance(serie_id, int):
            q = questions[cle]
            series = banc_.rechercher(
                q.texte,
                nombre_series=r.nombre_series.defaut,
                passages_par_serie=r.passages_par_serie.defaut,
                catalogue_seul=r.catalogue_seul,
            )
            relue["catalogue_en_tort"] = ligne_dementie(
                message_de_reponse("", q.texte, series), serie_id
            )
        relues.append(relue)
    return relues


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


def serie_en_tete(appel: Appel, titres: Mapping[int, str]) -> tuple[int, str] | None:
    """La série citée en tête d'une réponse rendue : sa première marque `[S:<id>]` et
    son titre, lu dans `titres` (les séries rendues depuis le début du scénario : la
    marque peut venir de l'historique) ; `None` si le texte n'en porte aucune."""
    marque = MARQUE_SERIE.search(appel.texte or "")
    if marque is None:
        return None
    serie_id = int(marque.group(1))
    return serie_id, titres.get(serie_id, "")


def nommee_par(reformulee: str, serie_id: int, titre: str) -> str | None:
    """Comment la question reformulée nomme la série : `marque` (son `[S:<id>]`),
    `titre` (son titre du catalogue, en mots entiers, casse ignorée : une
    reformulation n'écrit pas toujours la marque), sinon `None`."""
    marques = (int(m.group(1)) for m in MARQUE_SERIE.finditer(reformulee))
    if serie_id in marques:
        return "marque"
    motif = rf"(?<!\w){re.escape(titre)}(?!\w)"
    return "titre" if titre and re.search(motif, reformulee, re.IGNORECASE) else None


def erreur_du_fournisseur(r: Releve) -> dict[str, str]:
    """Le code d'erreur d'un appel refusé : `code` et `type`, selon le fournisseur ;
    jamais son message, qui peut reprendre ce qu'on lui a envoyé."""
    return {k: v for k, v in (r.erreur or {}).items() if k in ("code", "type")}


def refus_de_debit(appel: Appel) -> bool:
    """La requête a-t-elle échoué sur un refus de débit : un 429 `rate_limited`, rien
    d'autre ? Un refus que le nouvel essai de la route a sauvé ne compte pas."""
    return appel.fournisseur_en_echec and any(
        r.statut == STATUT_DEBIT and (r.erreur or {}).get("type") == TYPE_DEBIT
        for r in appel.releves
    )


def demander_le_tour(banc_: Banc, corps: dict[str, object]) -> list[Appel]:
    """Les tentatives d'un tour, la dernière fait foi : la requête, rejouée après
    `ATTENTE_DEBIT_S` tant que le fournisseur refuse pour débit, `REPRISES_DEBIT` fois
    au plus. Rejouer refait la reformulation, payée à chaque tentative."""
    tentatives = [demander(banc_, corps)]
    while refus_de_debit(tentatives[-1]) and len(tentatives) <= REPRISES_DEBIT:
        banc_.dormir(ATTENTE_DEBIT_S)
        tentatives.append(demander(banc_, corps))
    return tentatives


def scenarios(
    banc_: Banc, profil: Profil, tours: Sequence[Tour], plafond: float
) -> Run:
    """Chaque scénario, tour après tour ; l'historique reconstruit à partir des
    réponses réelles des tours précédents. Pour chaque tour avec historique : la
    première série de la recherche sur la question reformulée, comparée à celle de
    la question autonome attendue (mêmes réglages), et la série citée en tête de la
    réponse précédente, que la question reformulée nomme ou non."""
    cpj = banc_.modele_langage.caracteres_par_jeton
    run = Run(profil.nom)
    historiques: dict[str, list[dict[str, str]]] = {}
    tetes: dict[str, tuple[int, str] | None] = {}
    titres: dict[str, dict[int, str]] = {}
    for numero, t in enumerate(tours):
        if numero:
            banc_.dormir(banc_.pause_s)
        historique = historiques.setdefault(t.scenario, [])
        connus = titres.setdefault(t.scenario, {})
        corps: dict[str, object] = {"question": t.question, "profil": profil.nom}
        if historique:
            corps["historique"] = list(historique)
        tentatives = demander_le_tour(banc_, corps)
        appel = tentatives[-1]
        cout = sum(a.cout(profil, cpj) for a in tentatives)
        for a in tentatives:
            run.noter(a)
        run.cout_reel_usd += cout
        cle = f"{t.scenario}-{t.tour}"
        ligne: dict[str, object] = {
            "scenario": t.scenario,
            "tour": t.tour,
            "avec_historique": bool(historique),
            "statut": appel.statut,
            "code": appel.code,
            "motif": appel.motif,
            "pseudonymes": appel.pseudonymes,
            "cout_usd": round(cout, 6),
            "tentatives": len(tentatives),
            "appels": [
                ligne_appel | {"tentative": rang, "erreur": erreur_du_fournisseur(r)}
                for rang, a in enumerate(tentatives, 1)
                for ligne_appel, r in zip(
                    appels_de(a, profil, cpj), a.releves, strict=True
                )
            ],
        }
        if appel.statut == 200:
            c = appel.corps["contexte"]
            reformulee = c.get("question_reformulee")
            connus.update({s["serie_id"]: s["titre"] for s in appel.corps["series"]})
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
                tete = tetes.get(t.scenario)
                if tete is not None and isinstance(reformulee, str):
                    nommee = nommee_par(reformulee, *tete)
                    ligne |= {
                        "serie_en_tete_precedente": tete[0],
                        "reformulation_fidele": nommee is not None,
                        "fidele_par": nommee,
                    }
            if appel.texte is not None:
                run.textes[cle] = appel.texte
                if isinstance(reformulee, str):
                    run.textes[f"{cle} (question reformulée)"] = reformulee
        elif appel.code == "sortie_modele_invalide":
            garder_la_contredite(banc_, run, cle, appel, ligne)
        run.par_question.append(ligne)
        tetes[t.scenario] = serie_en_tete(appel, connus)
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
    fideles = [x for x in lignes if "reformulation_fidele" in x]
    invalides = Counter(
        cast(str, x["motif"]) for x in lignes if x["code"] == "sortie_modele_invalide"
    )
    return {
        **{f"sorties_invalides_{motif}": n for motif, n in invalides.items()},
        "tours": len(lignes),
        "tours_avec_historique": sum(1 for x in lignes if x.get("avec_historique")),
        "reformulation_meme_serie": sum(1 for x in avec if x["meme_serie"]),
        "reformulation_serie_attendue": sum(
            1 for x in avec if x["serie_attendue_premiere"]
        ),
        "reformulation_fidele": sum(1 for x in fideles if x["reformulation_fidele"]),
        "reformulation_fidele_sur": len(fideles),
        "pseudonymes_rendus": sum(cast(int, x["pseudonymes"]) for x in lignes),
        "sorties_invalides": sum(
            1 for x in lignes if x["code"] == "sortie_modele_invalide"
        ),
        "catalogue_en_tort": sum(1 for x in lignes if x.get("catalogue_en_tort")),
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
    appels = jetons = vides = 0
    cout = 0.0
    precedents: dict[str, int] = {}
    for t in tours:
        historique = precedents.get(t.scenario, 0)
        base, vide = contexte_envoye(banc_, t.question_autonome_attendue)
        vides += vide
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
    return Estimation(len(tours), appels, jetons, cout, vides)
