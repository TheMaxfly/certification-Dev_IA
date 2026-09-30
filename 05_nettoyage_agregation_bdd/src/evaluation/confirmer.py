"""Confirme les réponses écrites du jeu contre le catalogue — par clé exacte.

    uv run python -m evaluation.confirmer            # vérifie et rapporte
    uv run python -m evaluation.confirmer --ecrire   # remplit les colonnes de l'outil

LA RÈGLE (ajoutée au §4 de la spec le 2026-09-29). La réponse s'écrit d'abord,
telle que Max la connaît ; l'outil la confirme par titre exact, auteur ou
identifiant, jamais par une recherche sur les mots de la question. Une réponse
non retrouvable ainsi met la question DE CÔTÉ : on ne cherche pas autrement.

Ce que l'outil lit : le catalogue et les référentiels d'identité (cf.
`catalogue.TABLES_LUES`), en session LECTURE SEULE. Ce qu'il ne lit jamais : le
corpus, les fragments, un index — rien de ce que mesurera le jeu.

STATUTS D'UNE QUESTION
  confirmee  toutes ses réponses écrites sont confirmées, et le nombre de séries
             attendues respecte son mode et son issue ;
  de_cote    au moins une réponse écrite est introuvable par les trois voies ;
  a_revoir   ambiguïté (un titre qui désigne plusieurs séries), issue
             contredite par le catalogue, ou nombre de séries incohérent.

`--ecrire` réécrit `attendus.csv` : les trois colonnes de l'outil sont
recalculées depuis `reponse_ecrite`, jamais l'inverse ; une ligne `auteur:` se
déplie en une ligne par série. Relancé, il rend un fichier identique.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

from evaluation import catalogue as cat
from evaluation import regles
from evaluation.jeu import (
    FAMILLES,
    FICHIER_SOURCE,
    Attendu,
    JeuInvalide,
    Question,
    analyser_source,
    ecrire_attendus,
    lire_attendus,
    lire_questions,
    lire_reponse,
)
from identity.wikidata_dump import normaliser

RACINE = Path(__file__).resolve().parents[3]
JEU_DEFAUT = RACINE / "database/donnees/jeu_evaluation/v1"
RAPPORTS_DEFAUT = RACINE / "05_nettoyage_agregation_bdd/rapports"

#: Familles dont la question ne devrait pas porter le titre attendu (§4).
FAMILLES_SANS_TITRE = ("F3", "F6", "F8", "F9", "F10")
SEUIL_RECOUVREMENT = 0.5
MOTS_VIDES = frozenset(
    "les des une dans pour avec sur par qui que est son ses aux the and".split()
    + ["manga", "mangas"]
)

#: Clé combinée : « titre: Monster ; auteur: Naoki Urasawa » — les séries qui
#: portent ce titre ET cet auteur. Deux égalités strictes, jamais une recherche.
COMBINEE = re.compile(r"^(.*?)\s*;\s*auteur\s*:\s*(\S.*)$", re.IGNORECASE)

app = typer.Typer(add_completion=False, help=__doc__)


@dataclass
class Resolution:
    """Le sort d'une réponse écrite."""

    statut: str  # confirmee | ambigue | introuvable | a_revoir
    lignes: list[Attendu]
    detail: str = ""
    signalements: list[str] = field(default_factory=list)


@dataclass
class Bilan:
    question: Question
    statut: str
    series: dict[int, str] = field(default_factory=dict)  # series_id → grade
    motifs: list[str] = field(default_factory=list)
    recouvrement: float | None = None
    signalements: list[str] = field(default_factory=list)


def _ligne_seule(a: Attendu, statut: str, detail: str) -> Attendu:
    return a.completer(confirmation=f"{statut}: {detail}" if detail else statut)


def resoudre(
    q: Question,
    a: Attendu,
    catalogue: cat.Catalogue,
    cx: psycopg.Connection,
    jeu: Path | None = None,
) -> Resolution:
    voie, valeur = lire_reponse(a.reponse_ecrite)

    if voie == "regle":
        try:
            ids = regles.executer(cx, regles.lire(jeu or JEU_DEFAUT, valeur))
        except regles.RegleInvalide as erreur:
            return Resolution(
                "a_revoir", [_ligne_seule(a, "a_revoir", str(erreur))], str(erreur)
            )
        if not ids:
            return Resolution("introuvable", [_ligne_seule(a, "introuvable", "regle")])
        hors = [i for i in ids if i not in catalogue.series]
        if hors:
            detail = f"séries hors catalogue : {hors}"
            return Resolution("a_revoir", [_ligne_seule(a, "a_revoir", detail)], detail)
        return Resolution(
            "confirmee",
            [a.completer(i, catalogue.series[i].titre, "regle") for i in ids],
        )

    if q.issue_attendue == "au_catalogue":
        if voie == "id":
            s = catalogue.par_id(valeur)
            if s is None:
                return Resolution("introuvable", [_ligne_seule(a, "introuvable", "id")])
            return Resolution("confirmee", [a.completer(s.series_id, s.titre, "id")])
        combinee = COMBINEE.match(valeur) if voie == "titre" else None
        if combinee:
            titre, auteur = combinee.groups()
            de_l_auteur = catalogue.par_auteur(auteur)
            trouves = {
                i: f"{forme}+auteur"
                for i, forme in catalogue.par_titre(titre).items()
                if i in de_l_auteur
            }
            voie = "titre+auteur"
        else:
            trouves = (
                catalogue.par_titre(valeur)
                if voie == "titre"
                else catalogue.par_auteur(valeur)
            )
        if not trouves:
            return Resolution("introuvable", [_ligne_seule(a, "introuvable", voie)])
        if len(trouves) > 1 and (voie != "auteur" or q.mode == "reconnaissance"):
            ids = " | ".join(str(i) for i in sorted(trouves))
            candidats = " ; ".join(
                f"{i} « {catalogue.series[i].titre} » "
                f"({catalogue.series[i].dessinateur or '?'})"
                for i in sorted(trouves)
            )
            return Resolution(
                "ambigue", [_ligne_seule(a, "ambigue", ids)], f"{voie} → {candidats}"
            )
        return Resolution(
            "confirmee",
            [
                a.completer(i, catalogue.series[i].titre, f"{voie}:{trouves[i]}")
                for i in sorted(trouves)
            ],
        )

    # Issues sans clé : on vérifie une ABSENCE, et pour « reconnue » une présence.
    au_catalogue = (
        catalogue.par_titre(valeur) if voie == "titre" else catalogue.par_auteur(valeur)
    )
    if au_catalogue:
        ids = " | ".join(str(i) for i in sorted(au_catalogue))
        detail = f"présente au catalogue ({ids})"
        return Resolution("a_revoir", [_ligne_seule(a, "a_revoir", detail)], detail)

    if q.issue_attendue == "inconnue":
        ailleurs = cat.presences_hors_catalogue(cx, voie, valeur)
        if ailleurs:
            detail = f"existe dans {', '.join(ailleurs)}"
            return Resolution("a_revoir", [_ligne_seule(a, "a_revoir", detail)], detail)
        return Resolution("confirmee", [_ligne_seule(a, "inconnue_confirmee", "")])

    # reconnue_hors_catalogue
    if voie == "titre":
        kitsu = cat.kitsu_par_titre(cx, valeur)
        if not kitsu:
            detail = "inconnue de Kitsu"
            return Resolution("a_revoir", [_ligne_seule(a, "a_revoir", detail)], detail)
        rattaches = cat.rattaches_au_catalogue(cx, kitsu)
        if rattaches:
            detail = f"rattachée au catalogue par la cascade ({rattaches})"
            return Resolution("a_revoir", [_ligne_seule(a, "a_revoir", detail)], detail)
        bloquants, signalements = cat.verifier_absence(cx, catalogue, kitsu)
        if bloquants:
            detail = "au catalogue : " + " ; ".join(bloquants)
            return Resolution(
                "a_revoir", [_ligne_seule(a, "a_revoir", detail)], detail, signalements
            )
        return Resolution(
            "confirmee",
            [
                _ligne_seule(
                    a, "hors_catalogue_confirmee", f"Kitsu, {len(kitsu)} œuvre(s)"
                )
            ],
            signalements=signalements,
        )
    if "kitsu_staff" not in cat.presences_hors_catalogue(cx, "auteur", valeur):
        detail = "auteur inconnu de Kitsu"
        return Resolution("a_revoir", [_ligne_seule(a, "a_revoir", detail)], detail)
    return Resolution(
        "confirmee", [_ligne_seule(a, "hors_catalogue_confirmee", "Kitsu, auteur")]
    )


def recouvrement(texte: str, titres: list[str]) -> float:
    """Part des mots significatifs du titre attendu présents dans la question."""
    mots_q = set(normaliser(texte).split())
    ratios = []
    for titre in titres:
        mots_t = {m for m in normaliser(titre).split() if len(m) >= 3} - MOTS_VIDES
        if mots_t:
            ratios.append(len(mots_t & mots_q) / len(mots_t))
    return max(ratios, default=0.0)


def confirmer(
    questions: list[Question],
    attendus: list[Attendu],
    catalogue: cat.Catalogue,
    cx: psycopg.Connection,
    jeu: Path | None = None,
) -> tuple[list[Attendu], list[Bilan]]:
    """Attendus complétés, dans l'ordre d'écriture ; un bilan par question."""
    sortie: list[Attendu] = []
    bilans: list[Bilan] = []
    for q in questions:
        b = Bilan(q, "confirmee")
        groupes: dict[str, list[Attendu]] = {}
        for a in attendus:
            if a.question_id == q.question_id:
                groupes.setdefault(a.reponse_ecrite, []).append(a)
        for reponse, lignes in groupes.items():
            grades = {x.grade for x in lignes}
            if len(grades) > 1:
                b.motifs.append(
                    f"« {reponse} » : grades contradictoires {sorted(grades)}"
                )
            r = resoudre(q, lignes[0], catalogue, cx, jeu)
            b.signalements += [f"« {reponse} » : {x}" for x in r.signalements]
            sortie += r.lignes
            if r.statut == "introuvable":
                b.motifs.append(
                    f"« {reponse} » : introuvable par {lire_reponse(reponse)[0]}"
                )
                b.statut = "de_cote"
            elif r.statut != "confirmee":
                b.motifs.append(f"« {reponse} » : {r.statut} — {r.detail}")
            for x in r.lignes:
                if x.series_id:
                    i = int(x.series_id)
                    if i in b.series and b.series[i] != x.grade:
                        b.motifs.append(f"série {i} : grades contradictoires")
                    b.series[i] = x.grade

        if q.issue_attendue == "au_catalogue" and not groupes:
            b.motifs.append("aucune réponse écrite")
        if q.issue_attendue == "reconnue_hors_catalogue" and not groupes:
            b.motifs.append("l'œuvre reconnue n'est pas écrite")
        if b.statut != "de_cote" and not b.motifs:
            n = len(b.series)
            if (
                q.issue_attendue == "au_catalogue"
                and q.mode == "reconnaissance"
                and n != 1
            ):
                b.motifs.append(f"reconnaissance avec {n} séries attendues (1 exigée)")
        if b.statut != "de_cote" and b.motifs:
            b.statut = "a_revoir"

        if q.famille in FAMILLES_SANS_TITRE and b.series:
            titres = [catalogue.series[i].titre for i in b.series]
            b.recouvrement = recouvrement(q.texte, titres)
        bilans.append(b)
    return sortie, bilans


# --------------------------------------------------------------------------- #
#  Rapport
# --------------------------------------------------------------------------- #


def empreinte(chemin: Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def ecrire_rapport(
    chemin: Path, jeu: Path, bilans: list[Bilan], horodatage: str
) -> None:
    statuts = Counter(b.statut for b in bilans)
    familles = Counter(b.question.famille for b in bilans)
    modes = Counter(b.question.mode for b in bilans)
    signales = [
        b
        for b in bilans
        if b.recouvrement is not None and b.recouvrement >= SEUIL_RECOUVREMENT
    ]
    lieu = jeu.relative_to(RACINE) if jeu.is_relative_to(RACINE) else jeu
    L = [
        "# Jeu d'évaluation — confirmation au catalogue",
        "",
        f"Horodatage `{horodatage}` · jeu `{lieu}`",
        "",
        *[
            f"- `{f.name}` sha256 `{empreinte(f)}`"
            for f in sorted(jeu.iterdir())
            if f.is_file() and f.suffix in (".csv", ".txt")
        ],
        *[
            f"- `regles/{r.name}` sha256 `{empreinte(r)}`"
            for r in sorted((jeu / "regles").glob("*.sql"))
        ],
        f"- Lu, en lecture seule : {', '.join(f'`{t}`' for t in cat.TABLES_LUES)}. "
        "Ni corpus, ni fragment, ni index.",
        "",
        "## Statuts",
        "",
        "| Statut | Questions |",
        "|---|---:|",
        *[
            f"| {s} | {statuts.get(s, 0)} |"
            for s in ("confirmee", "a_revoir", "de_cote", "invalide")
        ],
        "",
        "## Effectifs (contrôle §8.3 : ≥ 5 par famille, ≥ 20 par mode hors refus)",
        "",
        "| Famille | Questions | | Mode | Questions |",
        "|---|---:|---|---|---:|",
    ]
    lignes_f = list(FAMILLES)
    lignes_m = ["proposition", "reconnaissance", "refus"]
    for i in range(max(len(lignes_f), len(lignes_m))):
        f = lignes_f[i] if i < len(lignes_f) else ""
        m = lignes_m[i] if i < len(lignes_m) else ""
        nf = familles.get(f, 0) if f else ""
        nm = modes.get(m, 0) if m else ""
        L.append(f"| {f} | {nf} | | {m} | {nm} |")
    L += [
        "",
        "## Questions",
        "",
        "| Id | Famille | Mode | Issue | Statut | Séries | Recouvrement du titre |",
        "|---|---|---|---|---|---:|---:|",
    ]
    for b in bilans:
        q = b.question
        rec = "" if b.recouvrement is None else f"{b.recouvrement:.0%}"
        if b in signales:
            rec = f"**{rec} ⚠**"
        L.append(
            f"| {q.question_id} | {q.famille} | {q.mode} | {q.issue_attendue} "
            f"| {b.statut} | {len(b.series)} | {rec} |"
        )
    motifs = [b for b in bilans if b.motifs]
    if motifs:
        L += ["", "## Motifs", ""]
        L += [
            f"- **{b.question.question_id}** ({b.statut}) — {'; '.join(b.motifs)}"
            for b in motifs
        ]
    avec_signalements = [b for b in bilans if b.signalements]
    if avec_signalements:
        L += [
            "",
            "## Signalements (hors catalogue) — à juger, non bloquants",
            "",
        ]
        L += [
            f"- **{b.question.question_id}** — {'; '.join(b.signalements)}"
            for b in avec_signalements
        ]
    if signales:
        L += [
            "",
            f"## Recouvrement signalé (≥ {SEUIL_RECOUVREMENT:.0%}, familles "
            f"{', '.join(FAMILLES_SANS_TITRE)})",
            "",
            "La question porte une bonne part des mots du titre attendu : elle teste "
            "peut-être la reconnaissance du titre plutôt que sa famille. Signalé, "
            "jamais corrigé par l'outil.",
            "",
        ]
        L += [f"- {b.question.question_id} — {b.recouvrement:.0%}" for b in signales]
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("\n".join(L) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
#  Commande
# --------------------------------------------------------------------------- #


def executer(url: str, jeu: Path) -> tuple[list[Attendu], list[Bilan]]:
    """Lit le jeu, le confirme en session lecture seule ; n'écrit rien."""
    erreurs: list[tuple[str, str]] = []
    if (jeu / FICHIER_SOURCE).is_file():
        questions, attendus, erreurs = analyser_source(jeu / FICHIER_SOURCE)
    else:
        questions = lire_questions(jeu / "questions.csv")
        attendus = lire_attendus(jeu / "attendus.csv", questions)
    # Lecture seule dès la PREMIÈRE requête : `SET SESSION CHARACTERISTICS` ne
    # vaudrait que pour les transactions suivantes, pas pour celle qu'il ouvre.
    with psycopg.connect(url, options="-c default_transaction_read_only=on") as cx:
        sortie, bilans = confirmer(
            questions, attendus, cat.Catalogue.charger(cx), cx, jeu
        )
    # Une erreur de structure rend la question INVALIDE, mais n'arrête pas le
    # diagnostic : le reste est confirmé au catalogue, et tout est listé.
    for b in bilans:
        propres = [m for qid, m in erreurs if qid == b.question.question_id]
        if propres:
            b.statut = "invalide"
            b.motifs = propres + b.motifs
    return sortie, bilans


@app.command()
def principal(
    # noqa: B008 — `typer.Option()` en défaut est l'API de Typer, pas un oubli.
    jeu: Path = typer.Option(JEU_DEFAUT, help="Dossier de la version du jeu."),  # noqa: B008
    ecrire: bool = typer.Option(  # noqa: B008
        False, "--ecrire", help="Remplir series_id / titre_catalogue / confirmation."
    ),
    rapports: Path = typer.Option(RAPPORTS_DEFAUT, help="Dossier des rapports."),  # noqa: B008
) -> None:
    """Confirme les réponses écrites contre le catalogue, par clé exacte."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise JeuInvalide(["DATABASE_URL n'est pas définie"])
    horodatage = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    source = (jeu / FICHIER_SOURCE).is_file()
    if source and ecrire:
        raise JeuInvalide(
            [f"{FICHIER_SOURCE} se corrige à la main : --ecrire ne s'y applique pas"]
        )
    questions = (
        analyser_source(jeu / FICHIER_SOURCE)[0]
        if source
        else lire_questions(jeu / "questions.csv")
    )
    if not questions:
        typer.echo("Aucune question écrite : rien à confirmer.")
        return
    chemin = rapports / f"jeu_evaluation_confirmation_{horodatage}.md"
    sortie, bilans = executer(url, jeu)
    ecrire_rapport(chemin, jeu, bilans, horodatage)  # empreintes AVANT écriture
    if ecrire:
        ecrire_attendus(jeu / "attendus.csv", sortie)
    statuts = Counter(b.statut for b in bilans)
    typer.echo(f"{len(bilans)} questions — {dict(statuts)} — rapport : {chemin}")
    if statuts.get("invalide"):
        raise JeuInvalide(
            [f"{statuts['invalide']} question(s) invalide(s) : cf. rapport"]
        )


def main() -> int:
    try:
        app()
    except JeuInvalide as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
