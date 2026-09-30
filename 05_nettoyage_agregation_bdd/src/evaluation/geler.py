"""Gel d'une version du jeu d'évaluation — la projection immuable.

    uv run python -m evaluation.geler --version v1              # à blanc (défaut)
    uv run python -m evaluation.geler --version v1 --executer   # gèle pour de bon

Le CSV source fait foi ; au gel il est PROJETÉ :

  1. confirmation au catalogue (`confirmer`) : toutes les questions confirmées,
     sinon refus ;
  2. contrôles du §8 (`controles`) : aucun échec, sinon refus ;
  3. `questions.csv` et `attendus.csv` écrits depuis la source, séries résolues
     en `series_id` ;
  4. empreinte = sha256 d'un MANIFESTE (chemin, sha256) de tous les fichiers de
     la version — source, liste v1, `controles_v1.json`, règles, déclarations,
     notes, projection ;
  5. chargement de `bench.eval_jeux`, `eval_questions`, `eval_attendus` en UNE
     transaction ; les contraintes différées de 016 sont forcées avant la fin.

Une version gelée est IMMUABLE (déclencheurs de 016) : à blanc, tout est joué
dans un dossier temporaire et une transaction ANNULÉE — le dossier du jeu et la
base restent intacts.
"""

from __future__ import annotations

import csv
import hashlib
import os
import shutil
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

from evaluation.confirmer import JEU_DEFAUT, RAPPORTS_DEFAUT, executer
from evaluation.controles import controler
from evaluation.jeu import COLONNES_QUESTIONS, ecrire_attendus

FICHIER_DECLARATIONS = "DECLARATIONS.md"
PROJECTION = ("questions.csv", "attendus.csv")

app = typer.Typer(add_completion=False, help=__doc__)


class GelRefuse(Exception):
    """Le jeu ne peut pas être gelé ; le message dit pourquoi."""


def sha256(chemin: Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def manifeste(dossier: Path) -> list[tuple[str, str]]:
    """(chemin relatif, sha256) de chaque fichier de la version, trié."""
    return sorted(
        (str(f.relative_to(dossier)), sha256(f))
        for f in dossier.rglob("*")
        if f.is_file()
    )


def empreinte(lignes: list[tuple[str, str]]) -> str:
    texte = "".join(f"{chemin}\t{h}\n" for chemin, h in lignes)
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def projeter(dossier: Path, sortie, bilans) -> int:
    """Écrit questions.csv et attendus.csv (réponses confirmées de `confirmer`,
    séries résolues) ; rend le nombre de couples (question, série)."""
    with (dossier / "questions.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(COLONNES_QUESTIONS)
        w.writerows(
            [getattr(b.question, c) for c in COLONNES_QUESTIONS] for b in bilans
        )
    ecrire_attendus(dossier / "attendus.csv", sortie)
    return sum(len(b.series) for b in bilans)


def charger(cx: psycopg.Connection, version: str, emp: str, declaration: str, bilans):
    cx.execute(
        "INSERT INTO bench.eval_jeux (version, empreinte, gele_le, declaration) "
        "VALUES (%s, %s, now(), %s)",
        (version, emp, declaration),
    )
    for b in bilans:
        q = b.question
        cx.execute(
            "INSERT INTO bench.eval_questions (jeu_version, question_id, texte, mode,"
            " famille, issue_attendue, origine, origine_query_id, note)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                version,
                q.question_id,
                q.texte,
                q.mode,
                q.famille,
                q.issue_attendue,
                q.origine,
                int(q.origine_query_id) if q.origine_query_id else None,
                q.note,
            ),
        )
        for series_id, grade in b.series.items():
            cx.execute(
                "INSERT INTO bench.eval_attendus VALUES (%s, %s, %s, %s)",
                (version, q.question_id, int(series_id), int(grade)),
            )
    # Les déclencheurs différés de 016 se jouent au COMMIT : à blanc, il n'y en a
    # pas. On les force ici, pour que l'essai prouve ce que le gel prouvera.
    cx.execute("SET CONSTRAINTS ALL IMMEDIATE")


def geler(url: str, jeu: Path, version: str, executer_gel: bool) -> dict:
    sortie, bilans = executer(url, jeu)
    statuts = Counter(b.statut for b in bilans)
    if set(statuts) != {"confirmee"}:
        raise GelRefuse(f"questions non confirmées : {dict(statuts)}")
    with psycopg.connect(url, options="-c default_transaction_read_only=on") as cx:
        if cx.execute(
            "SELECT 1 FROM bench.eval_jeux WHERE version = %s", (version,)
        ).fetchone():
            raise GelRefuse(f"{version} est déjà gelée : publier une nouvelle version")
        controles = controler(cx, bilans, jeu)
    if controles.echecs:
        raise GelRefuse("contrôles du §8 en échec : " + "; ".join(controles.echecs))
    if not (jeu / FICHIER_DECLARATIONS).is_file():
        raise GelRefuse(f"{FICHIER_DECLARATIONS} absent : pas de gel sans déclaration")

    with tempfile.TemporaryDirectory() as tmp:
        cible = jeu if executer_gel else Path(tmp) / jeu.name
        if not executer_gel:
            shutil.copytree(jeu, cible)
        n_attendus = projeter(cible, sortie, bilans)
        lignes = manifeste(cible)
        emp = empreinte(lignes)
        declaration = (cible / FICHIER_DECLARATIONS).read_text(encoding="utf-8")
        with psycopg.connect(url) as cx:
            charger(cx, version, emp, declaration, bilans)
            if executer_gel:
                cx.commit()
            else:
                cx.rollback()
    return {
        "questions": len(bilans),
        "attendus": n_attendus,
        "empreinte": emp,
        "manifeste": lignes,
        "familles": Counter(b.question.famille for b in bilans),
        "modes": Counter(b.question.mode for b in bilans),
        "origines": Counter(b.question.origine for b in bilans),
        "gele": executer_gel,
    }


def rapport(r: dict, version: str, horodatage: str) -> str:
    etat = "GELÉ" if r["gele"] else "à blanc — dossier et base intacts"
    return "\n".join(
        [
            f"# Jeu d'évaluation — gel de {version} ({etat})",
            "",
            f"Horodatage `{horodatage}` · {r['questions']} questions · "
            f"{r['attendus']} attendus · empreinte `{r['empreinte']}`",
            "",
            "Familles : "
            + ", ".join(
                f"{f} {n}"
                for f, n in sorted(r["familles"].items(), key=lambda x: int(x[0][1:]))
            ),
            "Modes : " + ", ".join(f"{m} {n}" for m, n in sorted(r["modes"].items())),
            "Origines : "
            + ", ".join(f"{o} {n}" for o, n in sorted(r["origines"].items())),
            "",
            "## Manifeste",
            "",
            "| Fichier | sha256 |",
            "|---|---|",
            *[f"| `{c}` | `{h}` |" for c, h in r["manifeste"]],
            "",
        ]
    )


@app.command()
def principal(
    version: str = typer.Option(JEU_DEFAUT.name, help="Version à geler."),  # noqa: B008
    jeu: Path = typer.Option(JEU_DEFAUT, help="Dossier de la version."),  # noqa: B008
    executer_gel: bool = typer.Option(  # noqa: B008
        False, "--executer", help="Gèle pour de bon (irréversible)."
    ),
    rapports: Path = typer.Option(RAPPORTS_DEFAUT, help="Dossier des rapports."),  # noqa: B008
) -> None:
    """Projette et gèle une version du jeu — à blanc par défaut."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise GelRefuse("DATABASE_URL n'est pas définie.")
    horodatage = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    r = geler(url, jeu, version, executer_gel)
    mode = "gel" if executer_gel else "gel_a_blanc"
    chemin = rapports / f"jeu_evaluation_{mode}_{version}_{horodatage}.md"
    chemin.write_text(rapport(r, version, horodatage), encoding="utf-8")
    typer.echo(
        f"{r['questions']} questions, {r['attendus']} attendus — empreinte "
        f"{r['empreinte']} — {'GELÉ' if executer_gel else 'à blanc'} — {chemin}"
    )


def main() -> int:
    try:
        app()
    except GelRefuse as erreur:
        typer.echo(f"GEL REFUSÉ : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
