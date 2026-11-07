"""La séquence : étapes 0 à 8, arrêt au premier échec, durées, rapport toujours.

`lancer` n'appelle **jamais** la promotion : quel que soit le verdict, la chaîne
s'arrête à l'étape 8. Promouvoir et revenir sont des gestes de Max (`deployer`).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chaine import candidat as module_candidat
from chaine import etapes
from chaine.candidat import Candidat
from chaine.codes import SENS, Arret, Code
from chaine.environnement import (
    RACINE_MODULE,
    Cible,
    Http,
    Lanceur,
    chemin_lisible,
    cible,
    commande_affichee,
    empreintes,
    exiger_arbre_propre_et_pousse,
    git,
    pile_en_marche,
    reference,
    suivi_et_inchange,
    valeur,
)

LIVRAISONS = RACINE_MODULE / "livraisons"
TRAVAIL = RACINE_MODULE / ".travail"
Afficher = Callable[[str], None]


@dataclass
class Resultat:
    numero: int
    nom: str
    statut: str  # « tenue », « non lancée », « arrêt », « refus »
    duree_s: float = 0.0
    details: dict[str, Any] = field(default_factory=dict[str, Any])
    code: Code | None = None
    motif: str = ""
    sortie: str = ""


@dataclass
class Passage:
    chemin_candidat: Path
    horodatage: str
    candidat: Candidat | None = None
    cible: Cible | None = None
    commit: str = ""
    encodage: int | None = None
    run: str | None = None
    verdict: dict[str, Any] | None = None
    conditions: list[str] = field(default_factory=list[str])
    image: str | None = None
    regle: dict[str, str] = field(default_factory=dict[str, str])
    resultats: list[Resultat] = field(default_factory=list[Resultat])
    code: Code = Code.INTERNE

    @property
    def nom(self) -> str:
        return self.candidat.nom if self.candidat else self.chemin_candidat.stem

    @property
    def nom_livraison(self) -> str:
        return f"{self.horodatage}_{self.nom}"


@dataclass
class Outils:
    """Ce que la séquence emploie : les commandes, le bouclage, l'horloge, l'écran."""

    lanceur: Lanceur
    http: Http
    afficher: Afficher = print
    horloge: Callable[[], float] = time.monotonic
    dormir: Callable[[float], None] = time.sleep
    maintenant: Callable[[], datetime] = lambda: datetime.now(UTC)
    livraisons: Path = LIVRAISONS
    travail: Path = TRAVAIL


# --------------------------------------------------------------------------- #
#  Les étapes, une par fonction
# --------------------------------------------------------------------------- #


def etape_0(p: Passage, o: Outils, environ: Mapping[str, str]) -> dict[str, Any]:
    p.cible = cible(environ)
    p.candidat = module_candidat.lire(p.chemin_candidat)
    p.commit = exiger_arbre_propre_et_pousse(o.lanceur)
    suivi_et_inchange(o.lanceur, p.chemin_candidat)
    pile = pile_en_marche(o.lanceur)
    if pile:
        raise Arret(
            Code.PREREQUIS,
            f"la pile du monitorage tourne ({len(pile)} conteneurs) : make"
            " monitorage-arret d'abord (une instance d'embedding à la fois)",
        )
    c, cb = p.candidat, p.cible
    port = etapes.port_instance(c.instance)
    statut, corps = o.http(f"http://127.0.0.1:{port}/info", {})
    if statut != 200:
        raise Arret(
            Code.PREREQUIS,
            f"instance {c.instance} injoignable sur 127.0.0.1:{port} : la lever"
            " (cd 09_service_embedding &&"
            f" docker compose --profile {c.instance} up -d)",
        )
    info = etapes.json_de(corps) or {}
    lecture = cb.dsn("verif_lecture")
    if c.encodage is not None:
        registre = valeur(
            o.lanceur,
            "psql.encodages",
            lecture,
            "SELECT encodage_id, modele, revision, precision_calcul, outil_version"
            " FROM bench.encodages ORDER BY encodage_id",
        )
        ligne = next(
            (
                champs[1:]
                for champs in (x.split("\t") for x in registre.splitlines())
                if champs[0] == str(c.encodage)
            ),
            None,
        )
        if ligne is None:
            raise Arret(Code.PREREQUIS, f"encodage {c.encodage} absent du registre")
        cles = ("model_id", "model_sha", "model_dtype", "version")
        attendu = dict(zip(cles, ligne, strict=True))
    else:
        attendu = {
            "model_id": c.modele,
            "model_sha": c.revision,
            "model_dtype": c.precision,
        }
    ecarts = {k: (info.get(k), v) for k, v in attendu.items() if info.get(k) != v}
    if ecarts:
        raise Arret(
            Code.PREREQUIS,
            f"l'instance {c.instance} ne sert pas ce qu'attend le candidat : {ecarts}",
        )
    en_service = valeur(
        o.lanceur,
        "psql.en_service",
        lecture,
        "SELECT encodage_id FROM bench.v_encodage_en_service",
    )
    if en_service != str(c.encodage_en_service):
        raise Arret(
            Code.PREREQUIS,
            f"la préproduction sert l'encodage {en_service or '(aucun)'} ; le candidat"
            f" attend l'encodage {c.encodage_en_service} au départ",
        )
    return {
        "base": cb.base,
        "dossier": chemin_lisible(cb.dossier_export),
        "commit": p.commit,
        "instance": c.instance,
        "info": {
            k: info.get(k) for k in ("model_id", "model_sha", "model_dtype", "version")
        },
        "encodage_en_service": int(en_service),
    }


def etape_1(p: Passage, o: Outils, travail: Path) -> dict[str, Any]:
    c, cb = requis(p)
    lecture = cb.dsn("verif_lecture")
    jeu = valeur(
        o.lanceur,
        "psql.jeu",
        lecture,
        "SELECT empreinte FROM bench.eval_jeux WHERE version = 'v2'",
    )
    if jeu != etapes.empreinte_du_jeu():
        raise Arret(
            Code.DONNEES, f"jeu v2 : empreinte {jeu[:12]}… au lieu de celle du banc"
        )
    lu = empreintes(o.lanceur, lecture, c.corpus)
    ref = reference()
    ecarts = [
        champs[0]
        for champs in lu
        if champs[0] in ("docs", "fragments")
        and [[c.corpus, *champs]] != [x for x in ref if x[:2] == [c.corpus, champs[0]]]
    ]
    if ecarts or not any(ch[0] == "docs" for ch in lu):
        raise Arret(
            Code.DONNEES,
            f"corpus {c.corpus} : empreintes différentes de la référence ({ecarts})",
        )
    blanc = etapes.lire_dry_run(o.lanceur(etapes.dry_run(c, cb, travail)))
    return {
        "jeu": jeu,
        "empreintes": {ch[0]: ch[1:] for ch in lu if ch[0] in ("docs", "fragments")},
        "reconstruction_a_blanc": blanc,
    }


def etape_2(p: Passage, o: Outils, travail: Path) -> dict[str, Any]:
    c, cb = requis(p)
    s = o.lanceur(etapes.construire(c, cb, travail))
    if s.code != 0:
        raise Arret(
            Code.CONSTRUCTION, f"construction en échec (code {s.code})", s.stderr
        )
    return {"construction": "faite"}


def etape_3(p: Passage, o: Outils, travail: Path) -> dict[str, Any]:
    c, cb = requis(p)
    s = o.lanceur(etapes.encoder(c, cb, travail))
    if s.code not in (0, 3):
        raise Arret(Code.ENCODAGE, f"encodeur en échec (code {s.code})", s.stderr)
    bilan = etapes.lire_bilan(travail / "encodage.json")
    p.encodage = int(bilan["encodage_id"])
    en_service = valeur(
        o.lanceur,
        "psql.en_service",
        cb.dsn("verif_lecture"),
        "SELECT encodage_id FROM bench.v_encodage_en_service",
    )
    if en_service != str(c.encodage_en_service):
        raise Arret(
            Code.ENCODAGE, f"après l'encodage, la vue désigne l'encodage {en_service}"
        )
    surveillance: dict[str, Any] = bilan.get("surveillance") or {}
    service: dict[str, Any] = bilan.get("service") or {}
    return {
        "encodage": p.encodage,
        "vecteurs": bilan.get("vecteurs_ecrits"),
        "fragments": bilan.get("fragments_corpus"),
        "duree_encodage_s": bilan.get("duree_encodage_s"),
        "memoire_video_pic": surveillance.get("vram_carte_pic"),
        "memoire_vive_pic": surveillance.get("ram_pic"),
        "service": {
            k: service.get(k)
            for k in ("modele", "revision", "precision", "version", "digest")
        },
    }


def etape_4(p: Passage, o: Outils) -> dict[str, Any]:
    _, cb = requis(p)
    n = encodage(p)
    s = o.lanceur(etapes.exporter(cb, n))
    if s.code != 0:
        raise Arret(
            Code.PREREQUIS,
            f"export de l'échantillon en échec (code {s.code})",
            s.stderr,
        )
    ecrit = Path(s.stdout.strip().splitlines()[-1]) if s.stdout.strip() else None
    if ecrit is None or not ecrit.resolve().is_relative_to(cb.dossier_export):
        raise Arret(
            Code.PREREQUIS,
            "l'échantillon n'a pas été écrit dans le dossier de la préproduction",
        )
    r = etapes.lire_equivalence(o.lanceur(etapes.comparer(cb, n)))
    return {
        "echantillon": chemin_lisible(ecrit),
        "verdict": r.get("verdict"),
        "cos_min": r.get("cos_min"),
        "n": r.get("n"),
        "sous_0999": len(r.get("sous_equivalent") or []),
    }


def etape_5(p: Passage, o: Outils, travail: Path) -> dict[str, Any]:
    c, cb = requis(p)
    n = encodage(p)
    r = etapes.lire_mesure(o.lanceur(etapes.mesurer(c, cb, n, travail)), n)
    p.run = str(r["run_id"])
    d: dict[str, Any] = {
        "run": p.run,
        "experience": etapes.EXPERIENCE,
        "global": r.get("global"),
    }
    if c.reference_run:
        identiques = etapes.lire_rejeu(
            o.lanceur(etapes.rejouer(c, cb, n, c.reference_run, travail))
        )
        d["rejeu"] = {"reference": c.reference_run, "identiques": identiques}
        if not identiques:
            raise Arret(
                Code.MESURE,
                f"le rejeu diffère du run de référence {c.reference_run[:8]}",
            )
    return d


def etape_6(p: Passage, o: Outils, travail: Path) -> dict[str, Any]:
    c, cb = requis(p)
    run = p.run or ""
    regle = git(
        o.lanceur, "regle", "log", "-1", "--format=%H %cI", "--", c.regle
    ).stdout.split()
    p.regle = {
        "fichier": c.regle,
        "commit": regle[0] if regle else "",
        "date": regle[1] if len(regle) > 1 else "",
    }
    r = etapes.lire_verdict(o.lanceur(etapes.juger(cb, run, travail)))
    complet = (
        etapes.json_de((travail / "verdict.json").read_text("utf-8"))
        if (travail / "verdict.json").is_file()
        else None
    )
    p.verdict = r
    nouveau: dict[str, Any] = r.get("nouveau", {})
    d = {
        "verdict": r["verdict"],
        **{
            k: nouveau.get(k)
            for k in ("global", "reconnaissance", "proposition", "questions", "ndcg_10")
        },
        "regle": p.regle,
    }
    if r["verdict"] == "refus proposé":
        p.conditions = etapes.conditions_manquantes(complet or r)
        d["conditions_manquantes"] = p.conditions
        raise Arret(
            Code.REFUS_REGLE,
            "la règle n'est pas tenue : " + ", ".join(p.conditions),
            "",
        ) from None
    return d


def etape_7(p: Passage, o: Outils) -> dict[str, Any]:
    s = o.lanceur(etapes.construire_image())
    if s.code != 0:
        raise Arret(
            Code.PACKAGING,
            f"construction de l'image en échec (code {s.code})",
            s.stderr,
        )
    e = o.lanceur(etapes.empreinte_image())
    if e.code != 0 or not e.stdout.strip():
        raise Arret(Code.PACKAGING, "empreinte de l'image illisible", e.stderr)
    p.image = e.stdout.strip()
    return {"image": etapes.image_de_l_api(), "empreinte": p.image}


def requis(p: Passage) -> tuple[Candidat, Cible]:
    if p.candidat is None or p.cible is None:
        raise Arret(Code.INTERNE, "candidat ou cible absent après l'étape 0")
    return p.candidat, p.cible


def encodage(p: Passage) -> int:
    if p.encodage is None:
        raise Arret(Code.INTERNE, "aucun encodage désigné")
    return p.encodage


# --------------------------------------------------------------------------- #
#  La séquence
# --------------------------------------------------------------------------- #


def lancer(
    chemin_candidat: Path,
    environ: Mapping[str, str],
    outils: Outils,
    *,
    ecrire_rapport: Callable[[Passage, Outils], tuple[Path, Path]] | None = None,
) -> Passage:
    """Les étapes 0 à 8 ; arrêt au premier échec ; le rapport, toujours. Jamais la
    promotion."""
    o = outils
    p = Passage(chemin_candidat, o.maintenant().strftime("%Y%m%dT%H%M%SZ"))
    travail = o.travail / p.nom_livraison
    travail.mkdir(parents=True, exist_ok=True)
    o.afficher(
        f"Chaîne de livraison — candidat {chemin_candidat.stem} — {p.horodatage}"
    )

    def conditionnelle(numero: int) -> bool:
        c = p.candidat
        if c is None:
            return False
        if numero == 2:
            return c.nouvelle_version
        if numero == 3:
            return c.nouvel_encodage
        if numero == 7:
            return bool(p.verdict and p.verdict.get("verdict") == "promotion proposée")
        return True

    sequence: list[tuple[int, Callable[[], dict[str, Any]]]] = [
        (0, lambda: etape_0(p, o, environ)),
        (1, lambda: etape_1(p, o, travail)),
        (2, lambda: etape_2(p, o, travail)),
        (3, lambda: etape_3(p, o, travail)),
        (4, lambda: etape_4(p, o)),
        (5, lambda: etape_5(p, o, travail)),
        (6, lambda: etape_6(p, o, travail)),
        (7, lambda: etape_7(p, o)),
    ]
    p.code = Code.PROMOTION_PROPOSEE
    for numero, faire in sequence:
        e = etapes.ETAPES[numero]
        if numero in (2, 3, 7) and not conditionnelle(numero):
            p.resultats.append(
                Resultat(
                    numero, e.nom, "non lancée", details={"condition": e.condition}
                )
            )
            o.afficher(f"[{numero}] {e.nom} — non lancée ({e.condition} : non)")
            continue
        if numero == 4 and p.encodage is None and p.candidat is not None:
            p.encodage = p.candidat.encodage
        o.afficher(f"[{numero}] {e.nom} …")
        debut = o.horloge()
        try:
            details = faire()
        except Arret as arret:
            duree = round(o.horloge() - debut, 1)
            statut = (
                "refus"
                if arret.code in (Code.REFUS_REGLE, Code.PAS_DE_VERDICT)
                else "arrêt"
            )
            p.resultats.append(
                Resultat(
                    numero,
                    e.nom,
                    statut,
                    duree,
                    {},
                    arret.code,
                    arret.motif,
                    arret.sortie,
                )
            )
            p.code = arret.code
            o.afficher(
                f"    {statut} ({duree} s) — code {int(arret.code)}"
                f" {arret.code.name} : {arret.motif}"
            )
            break
        except Exception as exc:  # noqa: BLE001 — une panne de l'orchestrateur : 90
            duree = round(o.horloge() - debut, 1)
            p.resultats.append(
                Resultat(
                    numero,
                    e.nom,
                    "arrêt",
                    duree,
                    {},
                    Code.INTERNE,
                    f"{type(exc).__name__} : {exc}",
                )
            )
            p.code = Code.INTERNE
            o.afficher(
                f"    arrêt ({duree} s) — code 90 INTERNE : {type(exc).__name__}"
            )
            break
        duree = round(o.horloge() - debut, 1)
        p.resultats.append(Resultat(numero, e.nom, "tenue", duree, details))
        o.afficher(f"    tenue ({duree} s){resume(numero, details)}")
    o.afficher("[8] Rapport et arrêt …")
    if ecrire_rapport is not None:
        md, js = ecrire_rapport(p, o)
        o.afficher(f"    rapport : {chemin_lisible(md)} (et {js.name})")
    o.afficher(f"Code de sortie : {int(p.code)} ({p.code.name}) — {SENS[p.code]}")
    o.afficher(f"Ensuite : {suite(p)}")
    return p


def resume(numero: int, d: dict[str, Any]) -> str:
    morceaux: dict[int, list[str]] = {
        0: [
            f"base {d.get('base')}",
            f"instance {d.get('instance')}",
            f"en service : {d.get('encodage_en_service')}",
        ],
        1: [f"jeu {str(d.get('jeu'))[:8]}…", "corpus conforme"],
        3: [f"encodage {d.get('encodage')}", f"{d.get('vecteurs')} vecteurs"],
        4: [f"cosinus minimal {d.get('cos_min')}", f"{d.get('verdict')}"],
        5: [f"run {str(d.get('run'))[:8]}…"]
        + (["rejeu identique"] if d.get("rejeu") else []),
        6: [
            f"{d.get('verdict')}",
            f"global {d.get('global')}",
            f"reconnaissance {d.get('reconnaissance')}",
        ],
        7: [f"image {str(d.get('empreinte'))[:19]}…"],
    }
    m = morceaux.get(numero, [])
    return " : " + ", ".join(m) if m else ""


def suite(p: Passage) -> str:
    livraison = f"14_chaine/livraisons/{p.nom_livraison}"
    commiter = (
        f"commiter le rapport ({livraison}.md et .json) — docs(chaine): rapport de"
        f" livraison {p.nom_livraison} — et le pousser"
    )
    if p.code == Code.PROMOTION_PROPOSEE:
        return (
            f"{commiter} ; puis, si Max promeut : make chaine-promouvoir"
            f' LIVRAISON={p.nom_livraison} MOTIF="…"'
        )
    if p.code in (Code.REFUS_REGLE, Code.PAS_DE_VERDICT):
        return f"{commiter} ; aucune promotion proposée"
    return f"{commiter} ; corriger la cause (README, « que faire »), puis relancer"


def a_blanc(
    chemin_candidat: Path, environ: Mapping[str, str], afficher: Afficher = print
) -> Code:
    """La séquence, sans rien lancer : commandes, arguments, base visée, ce qui
    écrirait. Seuls la cible et le schéma du candidat sont contrôlés."""
    cb = cible(environ)
    c = module_candidat.lire(chemin_candidat)
    travail = TRAVAIL / f"<horodatage>_{c.nom}"
    n: int | str = c.encodage if c.encodage is not None else "<n, lu dans le bilan>"
    afficher(f"Passage à blanc — candidat {c.nom} — rien n'est lancé")
    afficher(
        f"Base visée : {cb.base} ; dossier des échantillons :"
        f" {chemin_lisible(cb.dossier_export)}"
    )
    plan: list[tuple[int, str, list[Any]]] = [
        (
            0,
            "toujours",
            [
                "git status, rev-parse, branch -r --contains HEAD ; candidat"
                " suivi ; docker ps (pile) ;"
                f" /info de l'instance {c.instance}"
                f" (127.0.0.1:{etapes.port_instance(c.instance)}) ;"
                f" encodage en service attendu : {c.encodage_en_service}"
            ],
        ),
        (
            1,
            "toujours",
            [
                "psql empreintes_corpus.sql (verif_lecture)",
                etapes.dry_run(c, cb, travail),
            ],
        ),
        (
            2,
            "oui" if c.nouvelle_version else "non",
            [etapes.construire(c, cb, travail)],
        ),
        (3, "oui" if c.nouvel_encodage else "non", [etapes.encoder(c, cb, travail)]),
        (4, "toujours", [etapes.exporter(cb, n), etapes.comparer(cb, n)]),
        (
            5,
            "toujours",
            [etapes.mesurer(c, cb, n, travail)]
            + (
                [etapes.rejouer(c, cb, n, c.reference_run, travail)]
                if c.reference_run
                else []
            ),
        ),
        (6, "toujours", [etapes.juger(cb, "<run de l'étape 5>", travail)]),
        (
            7,
            "si promotion proposée",
            [etapes.construire_image(), etapes.empreinte_image()],
        ),
        (
            8,
            "toujours",
            [
                f"livraisons/<horodatage>_{c.nom}.md et .json",
                etapes.attacher(Path(f"livraisons/<horodatage>_{c.nom}.md"), "<run>"),
            ],
        ),
    ]
    for numero, condition, commandes in plan:
        e = etapes.ETAPES[numero]
        afficher(f"[{numero}] {e.nom} — {condition}")
        for commande in commandes:
            if isinstance(commande, str):
                afficher(f"    {commande}")
            else:
                afficher(f"    {commande_affichee(commande)}")
                afficher(f"      écrit : {commande.ecrit}")
    afficher(
        "Aucune promotion : la chaîne s'arrête à l'étape 8. Code de sortie : 0"
        " (passage à blanc)."
    )
    return Code.PROMOTION_PROPOSEE
