"""Contrôles du §8 de la spec du jeu d'évaluation — avant le gel.

    uv run python -m evaluation.controles     # rapport ; code 1 si un contrôle échoue

  §8.1 existence        chaque série attendue existe au catalogue, par jointure
                        sur `series_id` ; zéro exception ;
  §8.2 non-recouvrement pour F3, aucun mot SIGNIFICATIF de la question
                        n'apparaît dans le titre ou les critiques d'une série
                        attendue ;
  §8.3 effectifs        ≥ 5 par famille, ≥ 20 par mode hors refus, ≥ 5 refus.

La définition de §8.2 et les effectifs viennent de `v1/controles_v1.json`,
versionné avec le jeu et compris dans son empreinte au gel : un mot est non
significatif s'il est porté par plus de 5 % des critiques du corpus, ou s'il
figure dans `mots_non_significatifs_v1.txt`. La dérivation du seuil est écrite
dans ce fichier : l'intervalle 0,7 – 6,8 % était vide sur les F3, 5 % y a été
choisi (Max, 2026-09-30). Aucune liste de mots-outils extérieure : les
mots-outils dépassent tous le seuil.

Lit le catalogue et les critiques du corpus (`bench.corpus_docs`, source
`ms_review`) — la lecture de documents que §8.2 exige, jamais une récupération.
Le marqueur « synopsis anglais seulement » (`atteignabilite.synopsis_seul`)
partage F3 en deux sous-groupes, à titre d'information : calculé, jamais écrit.
Lecture seule.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

from evaluation.atteignabilite import corpus_lu, synopsis_seul
from evaluation.confirmer import JEU_DEFAUT, RAPPORTS_DEFAUT, Bilan, executer
from evaluation.jeu import FAMILLES
from identity.wikidata_dump import normaliser

FICHIER_CONTROLES = "controles_v1.json"

app = typer.Typer(add_completion=False, help=__doc__)


class ControleEchoue(Exception):
    """Un contrôle du §8 ne passe pas ; le rapport dit lequel."""


@dataclass
class Resultats:
    series: int = 0
    absentes: list[int] = field(default_factory=list)
    recouvrement: dict[str, dict] = field(default_factory=dict)
    effectifs: dict[str, dict[str, int]] = field(default_factory=dict)
    effectifs_en_defaut: list[str] = field(default_factory=list)

    @property
    def echecs(self) -> list[str]:
        e = [f"§8.1 : séries absentes {self.absentes}"] if self.absentes else []
        e += [
            f"§8.2 : {qid} recouvre {r['recouvrement']}"
            for qid, r in self.recouvrement.items()
            if r["recouvrement"]
        ]
        e += [f"§8.3 : {x}" for x in self.effectifs_en_defaut]
        return e


def lire_definitions(jeu: Path) -> tuple[dict, set[str]]:
    definitions = json.loads((jeu / FICHIER_CONTROLES).read_text(encoding="utf-8"))
    liste = definitions["recouvrement_f3"]["liste_mots_non_significatifs"]
    mots = {
        normaliser(ligne)
        for ligne in (jeu / liste).read_text(encoding="utf-8").splitlines()
        if ligne.strip() and not ligne.lstrip().startswith("#")
    }
    return definitions, mots


def mots_significatifs(
    texte: str, liste: set[str], frequence: Counter, n_critiques: int, seuil: float
) -> list[str]:
    """Les mots de la question qui discriminent : ni dans la liste, ni portés par
    plus de `seuil` des critiques. Un jeton d'une lettre (élision) ne compte pas."""
    return [
        m
        for m in dict.fromkeys(normaliser(texte).split())
        if len(m) > 1
        and m not in liste
        and (frequence[m] / n_critiques if n_critiques else 0) <= seuil
    ]


def critiques(
    cx: psycopg.Connection, corpus_id: str | None = None
) -> dict[int, list[set[str]]]:
    """Les critiques du corpus lu, par série, en ensembles de mots normalisés."""
    par_serie: dict[int, list[set[str]]] = {}
    for series_id, texte in cx.execute(
        "SELECT series_id, doc_text FROM bench.corpus_docs"
        " WHERE corpus_id = %(corpus)s AND source = 'ms_review'",
        {"corpus": corpus_lu(cx, corpus_id)},
    ):
        par_serie.setdefault(series_id, []).append(set(normaliser(texte).split()))
    return par_serie


def controler(cx: psycopg.Connection, bilans: list[Bilan], jeu: Path) -> Resultats:
    definitions, liste = lire_definitions(jeu)
    r = Resultats()

    # §8.1 — par jointure, pas par la confirmation qui l'a produite
    ids = sorted({int(s) for b in bilans for s in b.series})
    presents = {
        s
        for (s,) in cx.execute(
            "SELECT e.series_id FROM unnest(%s::bigint[]) AS t(series_id)"
            " JOIN manga.ms_series_enriched e USING (series_id)",
            (ids,),
        )
    }
    r.series, r.absentes = len(ids), sorted(set(ids) - presents)

    # §8.2
    seuil = definitions["recouvrement_f3"]["seuil_frequence_critiques"]
    docs = critiques(cx)
    frequence = Counter(
        m for liste_docs in docs.values() for d in liste_docs for m in d
    )
    n = sum(len(v) for v in docs.values())
    titres = dict(
        cx.execute("SELECT series_id, series_title FROM manga.ms_series_enriched")
    )
    for b in bilans:
        if b.question.famille != "F3":
            continue
        significatifs = mots_significatifs(b.question.texte, liste, frequence, n, seuil)
        series = sorted(int(s) for s in b.series)
        touches: dict[str, list[int]] = {}
        for sid in series:
            cible = set(normaliser(titres.get(sid) or "").split())
            for d in docs.get(sid, []):
                cible |= d
            for m in significatifs:
                if m in cible:
                    touches.setdefault(m, []).append(sid)
        r.recouvrement[b.question.question_id] = {
            "significatifs": significatifs,
            "recouvrement": touches,
            "series": len(series),
            "synopsis_seul": sorted(synopsis_seul(cx, series)),
        }

    # §8.3
    e = definitions["effectifs"]
    familles = Counter(b.question.famille for b in bilans)
    modes = Counter(b.question.mode for b in bilans)
    r.effectifs = {
        "familles": {f: familles.get(f, 0) for f in FAMILLES},
        "modes": dict(modes),
    }
    r.effectifs_en_defaut = [
        f"{f} : {familles.get(f, 0)} < {e['minimum_par_famille']}"
        for f in FAMILLES
        if familles.get(f, 0) < e["minimum_par_famille"]
    ] + [
        f"{m} : {modes.get(m, 0)} < {e['minimum_par_mode_hors_refus']}"
        for m in ("proposition", "reconnaissance")
        if modes.get(m, 0) < e["minimum_par_mode_hors_refus"]
    ]
    if modes.get("refus", 0) < e["minimum_refus"]:
        r.effectifs_en_defaut.append(
            f"refus : {modes.get('refus', 0)} < {e['minimum_refus']}"
        )
    return r


def rapport(r: Resultats, jeu: Path, horodatage: str, n_questions: int) -> str:
    L = [
        "# Jeu d'évaluation — contrôles du §8",
        "",
        f"Horodatage `{horodatage}` · jeu `{jeu.name}` · {n_questions} questions · "
        + ("**tous les contrôles passent**" if not r.echecs else "**ÉCHEC**"),
        "",
        "## §8.1 Existence",
        "",
        f"{r.series} séries attendues distinctes ; absentes du catalogue par "
        f"jointure : {r.absentes or 'aucune'}.",
        "",
        "## §8.2 Non-recouvrement F3",
        "",
        "Définition : `controles_v1.json` (seuil de 5 % des critiques + liste v1).",
        "",
        "| Question | Mots significatifs | Recouvrement | Séries "
        "| dont synopsis anglais seul |",
        "|---|---|---|---:|---:|",
        *[
            f"| {q} | {', '.join(v['significatifs']) or '—'} | "
            f"{v['recouvrement'] or 'aucun'} | {v['series']} | "
            f"{len(v['synopsis_seul'])} |"
            for q, v in r.recouvrement.items()
        ],
        "",
        "## §8.3 Effectifs",
        "",
        "| Famille | Questions |",
        "|---|---:|",
        *[f"| {f} | {n} |" for f, n in r.effectifs["familles"].items()],
        "",
        "Modes : "
        + ", ".join(f"{m} {n}" for m, n in sorted(r.effectifs["modes"].items())),
        "",
        "## Échecs",
        "",
        *([f"- {e}" for e in r.echecs] or ["Aucun."]),
        "",
    ]
    return "\n".join(L)


@app.command()
def principal(
    jeu: Path = typer.Option(JEU_DEFAUT, help="Dossier de la version du jeu."),  # noqa: B008
    rapports: Path = typer.Option(RAPPORTS_DEFAUT, help="Dossier des rapports."),  # noqa: B008
) -> None:
    """Rejoue les contrôles du §8 et écrit leur rapport."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ControleEchoue("DATABASE_URL n'est pas définie.")
    horodatage = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    _, bilans = executer(url, jeu)
    with psycopg.connect(url, options="-c default_transaction_read_only=on") as cx:
        r = controler(cx, bilans, jeu)
    chemin = rapports / f"jeu_evaluation_controles_{horodatage}.md"
    chemin.write_text(rapport(r, jeu, horodatage, len(bilans)), encoding="utf-8")
    typer.echo(
        f"{len(bilans)} questions — échecs : {len(r.echecs)} — rapport : {chemin}"
    )
    if r.echecs:
        raise ControleEchoue("; ".join(r.echecs))


def main() -> int:
    try:
        app()
    except ControleEchoue as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
