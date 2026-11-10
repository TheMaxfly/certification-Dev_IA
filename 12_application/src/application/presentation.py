"""Ce que les gabarits reçoivent : la réponse de l'API et sa description, en forme.

Tout est piloté par `/v1/info` : profils disponibles, bornes, modes. Aucun texte ici :
ils sont dans `[textes]` de `config/reglages.toml`. Aucun HTML non plus : les gabarits
échappent tout.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlencode

from application import __version__
from application.client_api import (
    ContexteReponse,
    ErreurApi,
    EtatApi,
    Info,
    Metadonnees,
    Passage,
    ProfilDecrit,
    ReglagesAppliques,
    ReglagesReponseAppliques,
    Reponse,
    Serie,
)
from application.config import Fournisseur, Position, Reglages


def dire(textes: Mapping[str, str], cle: str, **valeurs: object) -> str:
    return textes[cle].format(**valeurs)


def fournisseur_de(reglages: Reglages, nom: str) -> Fournisseur:
    """Le nom et l'hébergement d'un fournisseur, s'ils sont connus de la configuration :
    `/v1/info` ne dit pas l'hébergement."""
    connu = reglages.fournisseurs.get(nom)
    if connu is not None:
        return connu
    return Fournisseur(nom=nom, hebergement=reglages.textes["valeur_inconnue"])


def profils_proposes(info: Info, reglages: Reglages) -> list[ProfilDecrit]:
    """Les profils disponibles que la configuration n'exclut pas, dans l'ordre reçu."""
    return [
        profil
        for profil in info.modele_langage.profils
        if profil.disponible and profil.nom not in reglages.profils_exclus
    ]


def profil_par_defaut(
    info: Info, reglages: Reglages, regle: str | None = None
) -> str | None:
    """Le profil qui part quand personne n'en choisit (E4, lot 5, D12) : celui que
    l'administrateur a réglé (`regle`), s'il est proposé ; sinon le défaut de l'API —
    son profil actif s'il est proposé, sinon le premier proposé ; sinon aucun."""
    proposes = [p.nom for p in profils_proposes(info, reglages)]
    if regle is not None and regle in proposes:
        return regle
    actif = info.modele_langage.profil_actif
    if actif in proposes:
        return actif
    return proposes[0] if proposes else None


def regle_indisponible(
    info: Info | None, reglages: Reglages, regle: str | None
) -> bool:
    """Le profil réglé n'est plus proposé (indisponible, retiré ou exclu) : la question
    part avec le défaut de l'API, l'administrateur en est averti. Sans `/v1/info`, rien
    ne se dit."""
    if regle is None or info is None:
        return False
    return regle not in [p.nom for p in profils_proposes(info, reglages)]


@dataclass(frozen=True, slots=True)
class OptionProfil:
    nom: str
    etiquette: str
    choisi: bool


def options_profils(
    info: Info, reglages: Reglages, choisi: str | None, regle: str | None = None
) -> list[OptionProfil]:
    """Chaque option dit son fournisseur et son hébergement : ils se lisent au moment du
    choix. Le profil actif de l'application (`regle` s'il est proposé, E4, lot 5) est
    présélectionné et dit « actif »."""
    proposes = profils_proposes(info, reglages)
    noms = [p.nom for p in proposes]
    actif = profil_par_defaut(info, reglages, regle)
    retenu = choisi if choisi in noms else actif
    t = reglages.textes
    options: list[OptionProfil] = []
    for profil in proposes:
        fournisseur = fournisseur_de(reglages, profil.fournisseur)
        etiquette = dire(
            t,
            "profil_option",
            nom=profil.nom,
            fournisseur=fournisseur.nom,
            hebergement=fournisseur.hebergement,
        )
        if profil.nom == actif:
            etiquette += f" {t['suffixe_actif']}"
        if profil.statut == "apercu":
            etiquette += f" {t['suffixe_apercu']}"
        options.append(OptionProfil(profil.nom, etiquette, profil.nom == retenu))
    return options


def positions_proposees(info: Info | None, reglages: Reglages) -> tuple[Position, ...]:
    """Les positions de température dans les bornes de l'API."""
    if info is None:
        return reglages.positions
    borne = info.modele_langage.reglages_reponse.temperature
    return tuple(p for p in reglages.positions if borne.min <= p.valeur <= borne.max)


def modes_proposes(info: Info | None) -> list[str]:
    """Les modes publiés par l'API ; plusieurs : le sélecteur apparaît."""
    return [] if info is None else info.modele_langage.reglages_reponse.mode.valeurs


def mode_par_defaut(info: Info | None) -> str | None:
    return None if info is None else info.modele_langage.reglages_reponse.mode.defaut


def reformuler_par_defaut(info: Info | None, reglages: Reglages) -> bool:
    """Le défaut publié par l'API ; sans elle, celui de la configuration."""
    if info is None:
        return reglages.reformuler_defaut
    return info.modele_langage.reglages_reponse.reformuler.defaut


def etat_du_service(
    etat: EtatApi, info: Info | None, reglages: Reglages, regle: str | None = None
) -> str:
    """L'état, et le profil qui sert : l'actif de l'application (E4, lot 5)."""
    t = reglages.textes
    if etat == "cle":
        return t["etat_cle"]
    if etat == "ko" or info is None:
        return t["etat_ko"]
    actif = profil_par_defaut(info, reglages, regle) or info.modele_langage.profil_actif
    modele = next(
        (p.modele for p in info.modele_langage.profils if p.nom == actif),
        t["valeur_inconnue"],
    )
    return dire(t, "etat_ok", profil=actif, modele=modele)


def pied_de_page(etat: EtatApi, info: Info | None, reglages: Reglages) -> str:
    t = reglages.textes
    if etat == "ok" and info is not None:
        return dire(
            t, "pied_versions", version=__version__, version_api=info.version_api
        )
    return dire(t, "pied_versions_ko", version=__version__)


@dataclass(frozen=True, slots=True)
class LigneProfil:
    nom: str
    fournisseur: str
    hebergement: str
    actif: bool


def lignes_a_propos(
    info: Info | None, reglages: Reglages, regle: str | None = None
) -> list[LigneProfil]:
    """Tous les profils du sélecteur, avec leur hébergement : la page ne dit pas
    seulement celui du profil actif, qui mentirait dès qu'on en choisit un autre."""
    if info is None:
        return []
    actif = profil_par_defaut(info, reglages, regle)
    lignes: list[LigneProfil] = []
    for profil in profils_proposes(info, reglages):
        fournisseur = fournisseur_de(reglages, profil.fournisseur)
        lignes.append(
            LigneProfil(
                nom=profil.nom,
                fournisseur=fournisseur.nom,
                hebergement=fournisseur.hebergement,
                actif=profil.nom == actif,
            )
        )
    return lignes


# --------------------------------------------------------------------------- #
#  Le texte de la réponse : marques, titres, emphase
# --------------------------------------------------------------------------- #
MARQUE_SERIE = re.compile(r"\[S:(\d+)\]")
MARQUE_ET_ESPACE = re.compile(r"\s*\[S:(\d+)\]")
#: Une emphase colle à son texte : `4* et 5*` n'en est pas une.
EMPHASE = re.compile(r"\*\*(?=\S)([^*\n]+?)(?<=\S)\*\*|\*(?=\S)([^*\n]+?)(?<=\S)\*")


@dataclass(frozen=True, slots=True)
class Segment:
    """Un morceau de texte : les gabarits l'échappent, jamais de HTML ici."""

    genre: Literal["texte", "em", "strong", "lien"]
    texte: str = ""
    cible: int | None = None
    enfants: tuple[Segment, ...] = ()


def plat(texte: str) -> str:
    """Le texte sans casse ni rien d'autre que lettres et chiffres : la règle de l'API
    pour retrouver un titre malgré espaces, emphase, guillemets et ponctuation."""
    return re.sub(r"\W+", "", texte.casefold())


def avec_emphase(texte: str) -> list[Segment]:
    """`*x*` et `**x**` en emphase ; un astérisque seul reste tel quel."""
    segments: list[Segment] = []
    curseur = 0
    for trouve in EMPHASE.finditer(texte):
        if trouve.start() > curseur:
            segments.append(Segment("texte", texte[curseur : trouve.start()]))
        if trouve.group(1) is not None:
            segments.append(Segment("strong", trouve.group(1)))
        else:
            segments.append(Segment("em", trouve.group(2)))
        curseur = trouve.end()
    if curseur < len(texte):
        segments.append(Segment("texte", texte[curseur:]))
    return segments


def debut_du_titre(avant: str, cle: str) -> int | None:
    """Où commence le titre qui finit `avant`, à la règle de `plat` près ; son emphase
    ouvrante comprise. None : le titre n'est pas juste avant."""
    if not cle or not plat(avant).endswith(cle):
        return None
    for i in range(len(avant) - 1, -1, -1):
        if plat(avant[i:]) == cle:
            while i > 0 and avant[i - 1] == "*":
                i -= 1
            return i
    return None  # pragma: no cover : `endswith` garantit qu'un indice convient


def fin_du_titre(apres: str, cle: str) -> tuple[int, int] | None:
    """Où est le titre qui commence `apres`, à la règle de `plat` près. None : il n'est
    pas juste après."""
    if not cle or not plat(apres).startswith(cle):
        return None
    debut = 0
    while not (apres[debut].isalnum() or apres[debut] == "*"):
        debut += 1
    for j in range(debut + 1, len(apres) + 1):
        if plat(apres[debut:j]) == cle:
            if apres[debut] == "*":
                # L'emphase ouverte se ferme après la ponctuation du titre.
                fin = j
                while fin < len(apres) and not apres[fin].isalnum():
                    if apres[fin] == "*":
                        j = fin + 1
                    fin += 1
            return debut, j
    return None  # pragma: no cover : `startswith` garantit qu'un indice convient


def segments_de(texte: str, titres: Mapping[int, str]) -> list[Segment]:
    """Le texte d'une réponse : chaque marque `[S:<id>]` d'une série citée devient un
    lien vers sa carte, qui enveloppe le titre écrit par le modèle, juste avant ou juste
    après (la règle de l'API, réécrite) ; sinon, il porte le titre du catalogue. Une
    marque sans carte ne s'affiche pas : le texte du modèle reste, sans lien."""
    texte = MARQUE_ET_ESPACE.sub(
        lambda m: m.group(0) if int(m.group(1)) in titres else "", texte
    )
    segments: list[Segment] = []
    curseur = 0
    for marque in MARQUE_SERIE.finditer(texte):
        cible = int(marque.group(1))
        cle = plat(titres[cible])
        avant = texte[curseur : marque.start()]
        if (
            not avant.strip()
            and segments
            and segments[-1].genre == "lien"
            and segments[-1].cible == cible
        ):
            # La même série, marquée de nouveau juste après son titre : un seul lien.
            curseur = marque.end()
            continue
        debut = debut_du_titre(avant, cle)
        suite = marque.end()
        if debut is not None:
            segments += avec_emphase(avant[:debut])
            interieur = avec_emphase(avant[debut:].rstrip())
        else:
            segments += avec_emphase(avant)
            apres = fin_du_titre(texte[marque.end() :], cle)
            if apres is not None:
                segments += avec_emphase(texte[marque.end() : marque.end() + apres[0]])
                interieur = avec_emphase(
                    texte[marque.end() + apres[0] : marque.end() + apres[1]]
                )
                suite = marque.end() + apres[1]
            else:
                interieur = [Segment("texte", titres[cible])]
        segments.append(Segment("lien", cible=cible, enfants=tuple(interieur)))
        curseur = suite
    segments += avec_emphase(texte[curseur:])
    return segments


def paragraphes_de(texte: str, titres: Mapping[int, str]) -> list[list[Segment]]:
    return [
        segments_de(morceau, titres)
        for morceau in re.split(r"\n\s*\n", texte)
        if morceau.strip()
    ]


def segments_d_un_tour(contenu: str) -> list[Segment]:
    """Un tour précédent : aucune carte à viser, les marques ne s'affichent pas ; le
    champ caché, lui, garde le texte brut."""
    return avec_emphase(MARQUE_ET_ESPACE.sub("", contenu))


# --------------------------------------------------------------------------- #
#  La réponse : cartes, ligne discrète, détails techniques
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class PassageCite:
    genre: str
    texte: str
    #: Le mode démonstration seul : hors de lui, la valeur n'atteint pas le gabarit.
    score: str | None = None


@dataclass(frozen=True, slots=True)
class Mesure:
    """Le mode démonstration, sur une carte : le rang et le score de la série."""

    rang: str
    score: str


@dataclass(frozen=True, slots=True)
class Carte:
    serie_id: int
    titre: str
    metadonnees: tuple[tuple[str, str], ...]
    passages: tuple[PassageCite, ...]
    mesure: Mesure | None = None


@dataclass(frozen=True, slots=True)
class LigneClassement:
    rang: str
    titre: str
    score: str
    etat: str
    passages: int
    #: La carte à viser : une série citée seulement.
    cible: int | None


@dataclass(frozen=True, slots=True)
class NonCites:
    """Les passages non cités d'une série, repliés."""

    resume: str
    passages: tuple[PassageCite, ...]


@dataclass(frozen=True, slots=True)
class Trace:
    """Le mode démonstration : ce que l'API a rendu, rien de calculé ici."""

    phrase: tuple[Segment, ...]
    classement: tuple[LigneClassement, ...]
    non_cites: tuple[NonCites, ...]
    question: tuple[tuple[str, str], ...]
    recherche: tuple[tuple[str, str], ...]
    modele: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Affichage:
    """Ce que la zone de réponse montre."""

    question: str
    refus: bool
    motif: str | None
    paragraphes: list[list[Segment]]
    cartes: tuple[Carte, ...]
    ligne: str
    appels: tuple[str, ...]
    identifiants: str
    sans_carte: str | None
    complement: str | None
    complement_titres: tuple[str, ...]
    #: L'étiquette du complément : celle de l'API (D9), le texte du 12 en repli.
    complement_etiquette: str = ""
    #: None hors du mode démonstration : la section n'existe pas.
    trace: Trace | None = None
    #: Le pont vers le catalogue (E4, lot 2b, décision 6) : une réponse qui refuse ou
    #: ne cite aucune série propose de chercher la question dans le catalogue.
    lien_catalogue: str | None = None


def tomes_de(m: Metadonnees, t: Mapping[str, str]) -> str:
    """Le nombre, tel quel pour la source `champ` ; pour toute autre, un minimum."""
    if m.nb_volumes is None:
        return t["valeur_inconnue"]
    if m.nb_volumes_source == "champ":
        return str(m.nb_volumes)
    return dire(t, "tomes_au_moins", n=m.nb_volumes)


def metadonnees_de(m: Metadonnees, t: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    inconnu = t["valeur_inconnue"]
    statuts = {"termine": t["statut_termine"], "en_cours": t["statut_en_cours"]}
    disponibilites = {
        "edite_en_france": t["dispo_france"],
        "seulement_au_japon": t["dispo_japon"],
        "inconnue": t["dispo_inconnue"],
    }
    return (
        (t["carte_auteurs"], m.auteurs or inconnu),
        (t["carte_tomes"], tomes_de(m, t)),
        (t["carte_statut"], statuts[m.statut] if m.statut else inconnu),
        (t["carte_public"], m.demographie or inconnu),
        (
            t["carte_disponibilite"],
            disponibilites[m.disponibilite] if m.disponibilite else inconnu,
        ),
    )


def decimales(valeur: float | None, t: Mapping[str, str], n: int = 2) -> str:
    """Un nombre à la française ; absent ou nul : le tiret, jamais une erreur."""
    if valeur is None:
        return t["demo_absent"]
    return f"{valeur:.{n}f}".replace(".", ",")


def ou_absent(valeur: object, t: Mapping[str, str]) -> str:
    return t["demo_absent"] if valeur is None else str(valeur)


def mesure_de(serie: Serie, t: Mapping[str, str]) -> Mesure:
    return Mesure(ou_absent(serie.rang, t), decimales(serie.score, t))


def passage_en_forme(
    passage: Passage, t: Mapping[str, str], score: str | None
) -> PassageCite:
    genres = {
        "critique": t["passage_critique"],
        "resume_serie": t["passage_resume"],
        "synopsis_kitsu": t["passage_synopsis"],
    }
    return PassageCite(
        dire(
            t,
            "passage_extrait",
            genre=genres[passage.type_document],
            fragment=passage.fragment_id,
        ),
        passage.extrait,
        score,
    )


def fragments_cites(corps: Reponse) -> dict[int | None, set[int]]:
    """Par série citée, ses fragments. L'API ne dédoublonne pas : une série citée deux
    fois les réunit."""
    fragments: dict[int | None, set[int]] = {}
    for citee in corps.series_citees:
        fragments.setdefault(citee.serie_id, set()).update(citee.fragment_ids)
    return fragments


def cartes_de(
    corps: Reponse, t: Mapping[str, str], demonstration: bool = False
) -> tuple[Carte, ...]:
    """Une carte par série citée, dans l'ordre ; ses seuls passages cités. `series`
    porte toutes les séries de la recherche : le reste ne s'affiche pas. Rang et
    scores : le mode démonstration seul."""
    index = {s.serie_id: s for s in corps.series if s.serie_id is not None}
    fragments = fragments_cites(corps)
    vues: set[int] = set()
    cartes: list[Carte] = []
    for citee in corps.series_citees:
        serie = index.get(citee.serie_id)
        if serie is None or citee.serie_id in vues:
            continue
        vues.add(citee.serie_id)
        cartes.append(
            Carte(
                serie_id=citee.serie_id,
                titre=citee.titre,
                metadonnees=metadonnees_de(serie.metadonnees, t),
                passages=tuple(
                    passage_en_forme(
                        p, t, decimales(p.score, t) if demonstration else None
                    )
                    for p in serie.passages
                    if p.fragment_id in fragments[citee.serie_id]
                ),
                mesure=mesure_de(serie, t) if demonstration else None,
            )
        )
    return tuple(cartes)


def ordre_du_rang(serie: Serie) -> tuple[bool, int]:
    """Le rang rendu par l'API ; sans rang, en dernier."""
    return serie.rang is None, serie.rang or 0


def classement_de(
    series: list[Serie],
    fragments: Mapping[int | None, set[int]],
    t: Mapping[str, str],
) -> tuple[LigneClassement, ...]:
    """Toutes les séries de la recherche ; citée ou non se dit en texte."""
    return tuple(
        LigneClassement(
            rang=ou_absent(serie.rang, t),
            titre=serie.titre,
            score=decimales(serie.score, t),
            etat=t["trace_citee" if serie.serie_id in fragments else "trace_non_citee"],
            passages=len(serie.passages),
            cible=serie.serie_id if serie.serie_id in fragments else None,
        )
        for serie in series
    )


def non_cites_de(
    series: list[Serie],
    fragments: Mapping[int | None, set[int]],
    t: Mapping[str, str],
) -> tuple[NonCites, ...]:
    """Par série, ce que la recherche a retrouvé et que le modèle n'a pas cité."""
    groupes: list[NonCites] = []
    for serie in series:
        cites = fragments.get(serie.serie_id, set[int]())
        restes = [p for p in serie.passages if p.fragment_id not in cites]
        if restes:
            groupes.append(
                NonCites(
                    dire(t, "trace_non_cites_resume", titre=serie.titre, n=len(restes)),
                    tuple(
                        passage_en_forme(p, t, decimales(p.score, t)) for p in restes
                    ),
                )
            )
    return tuple(groupes)


def lignes_question(
    question: str, c: ContexteReponse, t: Mapping[str, str]
) -> tuple[tuple[str, str], ...]:
    """L'origine, la reformulée s'il y en a une, et celle qui a cherché : l'API cherche
    sur la reformulée quand elle existe ; une reformulation ignorée n'en rend pas."""
    if c.question_reformulee is not None:
        reformulee, partie = c.question_reformulee, t["trace_partie_reformulee"]
    elif c.reformulation_ignoree is not None:
        reformulee, partie = t["reformulation_ignoree"], t["trace_partie_origine"]
    else:
        reformulee, partie = t["reformulation_sans"], t["trace_partie_origine"]
    return (
        (t["trace_question_origine"], question),
        (t["trace_question_reformulee"], reformulee),
        (t["trace_question_partie"], partie),
    )


def lignes_recherche(
    c: ContexteReponse, t: Mapping[str, str]
) -> tuple[tuple[str, str], ...]:
    r = c.reglages or ReglagesAppliques()
    seul = {True: t["trace_oui"], False: t["trace_non"], None: t["demo_absent"]}
    return (
        (t["trace_nombre_series"], ou_absent(r.nombre_series, t)),
        (t["trace_passages_par_serie"], ou_absent(r.passages_par_serie, t)),
        (t["trace_catalogue_seul"], seul[r.catalogue_seul]),
        (t["trace_modele_embedding"], ou_absent(c.modele, t)),
        (t["trace_revision"], ou_absent(c.revision, t)),
        (t["trace_corpus"], c.corpus_id),
        (t["trace_encodage"], str(c.encodage_id)),
    )


def lignes_modele(
    c: ContexteReponse, t: Mapping[str, str]
) -> tuple[tuple[str, str], ...]:
    r = c.reglages_appliques or ReglagesReponseAppliques()
    temps = c.latences_ms

    def latence(valeur: float | None) -> str:
        if valeur is None:
            return t["demo_absent"]
        return dire(t, "ligne_latence", latence=round(valeur))

    cout = c.cout_estime_usd
    return (
        (t["trace_profil"], c.profil),
        (t["trace_fournisseur"], c.fournisseur),
        (t["trace_modele"], c.modele_langage),
        (
            t["trace_temperature"],
            (
                t["demo_absent"]
                if r.temperature is None
                else f"{r.temperature:g}".replace(".", ",")
            ),
        ),
        (t["trace_longueur"], ou_absent(r.longueur_max, t)),
        (
            t["trace_cout"],
            (
                t["demo_absent"]
                if cout is None
                else dire(t, "trace_cout_valeur", cout=decimales(cout, t, 6))
            ),
        ),
        (t["trace_latence_recherche"], latence(temps.recherche)),
        (t["trace_latence_reformulation"], latence(temps.reformulation)),
        (t["trace_latence_modele"], latence(temps.modele)),
        (t["trace_latence_totale"], latence(temps.totale)),
        (t["trace_identifiant"], c.identifiant_requete),
    )


def trace_de(question: str, corps: Reponse, t: Mapping[str, str]) -> Trace:
    fragments = fragments_cites(corps)
    series = sorted(corps.series, key=ordre_du_rang)
    return Trace(
        phrase=tuple(avec_emphase(t["trace_phrase"])),
        classement=classement_de(series, fragments, t),
        non_cites=non_cites_de(series, fragments, t),
        question=lignes_question(question, corps.contexte, t),
        recherche=lignes_recherche(corps.contexte, t),
        modele=lignes_modele(corps.contexte, t),
    )


def date_lisible(valeur: str) -> str:
    """Le dernier appel, en UTC ; une valeur illisible reste telle quelle."""
    try:
        return datetime.fromisoformat(valeur).strftime("%d/%m/%Y %H:%M UTC")
    except ValueError:
        return valeur


def ligne_discrete(corps: Reponse, t: Mapping[str, str]) -> str:
    c = corps.contexte
    if c.question_reformulee is not None:
        reformulation = dire(t, "reformulation_faite", question=c.question_reformulee)
    elif c.reformulation_ignoree is not None:
        reformulation = t["reformulation_ignoree"]
    else:
        reformulation = t["reformulation_sans"]
    morceaux = (
        dire(
            t,
            "ligne_profil",
            profil=c.profil,
            fournisseur=c.fournisseur,
            modele=c.modele_langage,
        ),
        (
            dire(t, "ligne_date", date=date_lisible(c.date_appel))
            if c.date_appel is not None
            else t["ligne_sans_appel"]
        ),
        reformulation,
        (
            dire(t, "ligne_cout", cout=f"{c.cout_estime_usd:.4f}".replace(".", ","))
            if c.cout_estime_usd is not None
            else t["ligne_cout_inconnu"]
        ),
        dire(t, "ligne_latence", latence=round(c.latences_ms.totale)),
    )
    return " · ".join(morceaux)


def appels_de(corps: Reponse, t: Mapping[str, str]) -> tuple[str, ...]:
    roles = {
        "reformulation": t["role_appel_reformulation"],
        "reponse": t["role_appel_reponse"],
    }
    lignes: list[str] = []
    for appel in corps.contexte.appels:
        role = roles[appel.role]
        if appel.statut == "refus_debit":
            lignes.append(dire(t, "details_refus_debit", role=role))
            continue
        ligne = dire(
            t,
            "details_appel",
            role=role,
            entree=appel.jetons_entree,
            sortie=appel.jetons_sortie,
            raisonnement=appel.jetons_raisonnement,
            latence=round(appel.latence_ms),
        )
        lignes.append(f"{ligne} {t['details_estimes']}" if appel.estimes else ligne)
    return tuple(lignes)


def lien_vers_catalogue(question: str, reglages: Reglages) -> str | None:
    """`/catalogue?q=` : la question coupée à la borne du catalogue, sans les espaces
    des bords ; trop courte pour une recherche, pas de lien."""
    regles = reglages.catalogue
    q = question[: regles.q_max].strip()
    if len(q) < regles.q_min:
        return None
    return "/catalogue?" + urlencode({"q": q})


def affichage_de(
    question: str,
    corps: Reponse,
    reglages: Reglages,
    *,
    demonstration: bool = False,
    complement: bool = False,
) -> Affichage:
    """La zone de réponse ; le mode démonstration n'ajoute que ce que l'API a déjà
    rendu, et, éteint, rien de cela n'atteint le gabarit. Le complément du mode enrichi
    n'atteint le gabarit que pour un rôle qui choisit le mode (`complement`) : le
    public est en strict, et son HTML n'en porte jamais le bloc."""
    t = reglages.textes
    motifs = {
        "aucun_passage": t["motif_aucun_passage"],
        "hors_catalogue": t["motif_hors_catalogue"],
        "inconnu": t["motif_inconnu"],
    }
    cartes = cartes_de(corps, t, demonstration)
    avec_carte = {c.serie_id for c in cartes}
    sans_carte = [
        i
        for i in dict.fromkeys(MARQUE_SERIE.findall(corps.reponse))
        if int(i) not in avec_carte
    ]
    return Affichage(
        question=question,
        refus=corps.refus,
        motif=(
            dire(t, "refus_motif", motif=motifs[corps.motif_refus])
            if corps.refus and corps.motif_refus is not None
            else None
        ),
        paragraphes=paragraphes_de(
            corps.reponse, {c.serie_id: c.titre for c in cartes}
        ),
        cartes=cartes,
        ligne=ligne_discrete(corps, t),
        appels=appels_de(corps, t),
        identifiants=dire(
            t,
            "details_ids",
            corpus_id=corps.contexte.corpus_id,
            encodage_id=corps.contexte.encodage_id,
            identifiant=corps.contexte.identifiant_requete,
        ),
        sans_carte=(
            dire(t, "details_sans_carte", ids=", ".join(sans_carte))
            if sans_carte
            else None
        ),
        complement=corps.complement if complement else None,
        complement_titres=tuple(corps.complement_titres or ()) if complement else (),
        complement_etiquette=corps.complement_etiquette or t["complement_etiquette"],
        trace=trace_de(question, corps, t) if demonstration else None,
        lien_catalogue=(
            lien_vers_catalogue(question, reglages)
            if corps.refus or not cartes
            else None
        ),
    )


# --------------------------------------------------------------------------- #
#  Les cas dégradés : un encart par cas, le texte du tableau des décisions
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Encart:
    """Ce que voit le libraire, le statut de la page, l'attente à rendre."""

    texte: str
    statut: int
    retry_after: int | None = None
    #: La liste des profils a pu changer : retour au profil par défaut.
    profil_a_revoir: bool = False
    #: La conversation s'arrête là : le lien vers une nouvelle est proposé.
    nouvelle_conversation: bool = False
    #: Le titre de l'encart, s'il en a un à lui (les bornes, E4, lot 2b) ; None : le
    #: titre commun.
    titre: str | None = None


CODES_RECHERCHE = frozenset(
    {
        "service_embedding_indisponible",
        "service_embedding_incoherent",
        "base_indisponible",
        "encodage_en_service_change",
    }
)
MOTIFS_SORTIE = frozenset(
    {
        "schema",
        "serie_inconnue",
        "fragment_inconnu",
        "refus_incoherent",
        "pseudonyme",
        "disponibilite",
        "demographie",
    }
)


def encart_transport(delai: bool, reglages: Reglages) -> Encart:
    cle = "degrade_delai" if delai else "degrade_injoignable"
    return Encart(reglages.textes[cle], 503)


def encart_erreur(
    erreur: ErreurApi, profil: str, identifiant: str, reglages: Reglages
) -> Encart:
    """Un code du contrat → son encart ; le texte vient de la configuration, le motif
    est traduit, la sortie du modèle n'y entre jamais."""
    t = reglages.textes
    code, details = erreur.code, erreur.details
    nom_profil = str(details.get("profil", profil))
    if code == "debit_depasse":
        n = erreur.retry_after or reglages.attente_debit_defaut_s
        return Encart(dire(t, "degrade_debit", n=n), 429, n)
    if code == "fournisseur_refus" and details.get("statut") == 429:
        n = erreur.retry_after or reglages.attente_fournisseur_defaut_s
        return Encart(dire(t, "degrade_saturation", n=n), 503, n)
    if code == "fournisseur_refus" and details.get("motif") == "longueur_atteinte":
        return Encart(dire(t, "degrade_longueur", profil=nom_profil), 503)
    if code in ("fournisseur_refus", "fournisseur_injoignable", "fournisseur_delai"):
        return Encart(dire(t, "degrade_modele", profil=nom_profil), 503)
    if code == "sortie_modele_invalide":
        motif = str(details.get("motif", ""))
        traduit = (
            t[f"sortie_{motif}"] if motif in MOTIFS_SORTIE else t["valeur_inconnue"]
        )
        return Encart(dire(t, "degrade_sortie", motif=traduit), 503)
    if code in CODES_RECHERCHE:
        return Encart(t["degrade_recherche"], 503)
    if code in ("profil_indisponible", "profil_inconnu"):
        return Encart(
            dire(t, "degrade_profil", profil=nom_profil), 422, profil_a_revoir=True
        )
    if code == "reglage_non_expose":
        reglage = str(details.get("reglage", ""))
        if reglage == "temperature":
            reglage = t["temperature_legende"].lower()
        return Encart(
            dire(t, "degrade_reglage", profil=nom_profil, reglage=reglage), 422
        )
    return Encart(
        dire(
            t,
            "degrade_service",
            identifiant=erreur.identifiant_requete or identifiant,
        ),
        erreur.statut if erreur.statut == 422 else 502,
    )


# --------------------------------------------------------------------------- #
#  Les critiques signées (E4, lot 3)
# --------------------------------------------------------------------------- #
MOIS = (
    "janvier février mars avril mai juin juillet août septembre octobre novembre"
    " décembre"
).split()


def date_en_lettres(moment: datetime) -> str:
    """« 12 novembre 2026 », « 1er décembre 2026 » : la date d'une critique, dans le
    fuseau de la base (celui de la session)."""
    jour = "1er" if moment.day == 1 else str(moment.day)
    return f"{jour} {MOIS[moment.month - 1]} {moment.year}"


def paragraphes_critique(texte: str) -> list[list[str]]:
    """Le texte d'une critique en paragraphes (sur les lignes vides), chacun en lignes :
    le gabarit les sépare, échappées ; rien n'est jamais marqué « sûr »."""
    return [
        [ligne.strip() for ligne in bloc.split("\n") if ligne.strip()]
        for bloc in re.split(r"\n\s*\n", texte)
        if bloc.strip()
    ]
