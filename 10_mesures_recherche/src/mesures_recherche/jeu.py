"""Le jeu d'évaluation gelé : vérifié, puis lu.

`verifier_empreinte` est la PREMIÈRE action de chaque exécution : l'empreinte
des fichiers versionnés de la version, recalculée par la règle du gel
(`evaluation.geler.empreinte`, module 05 : sha256 du manifeste
« chemin<TAB>sha256 » trié), et celle de `bench.eval_jeux` doivent toutes deux
valoir celle de la configuration. Sinon, arrêt.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

ISSUE_MESURABLE = "au_catalogue"


class ArretEmpreinte(Exception):
    """Le jeu n'est pas celui que la configuration déclare : on ne mesure pas."""


@dataclass(frozen=True)
class Question:
    question_id: str
    texte: str
    mode: str
    famille: str
    issue: str
    attendus: dict[int, int] = field(default_factory=dict)  # series_id → grade

    @property
    def de_rang(self) -> bool:
        """Mesurable au rang : une série attendue au moins, au catalogue."""
        return self.issue == ISSUE_MESURABLE and bool(self.attendus)


def empreinte_fichiers(dossier: Path) -> str:
    lignes = sorted(
        (str(f.relative_to(dossier)), hashlib.sha256(f.read_bytes()).hexdigest())
        for f in dossier.rglob("*")
        if f.is_file()
    )
    texte = "".join(f"{chemin}\t{h}\n" for chemin, h in lignes)
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def verifier_empreinte(cx, version: str, attendue: str, dossier: Path) -> str:
    fichiers = empreinte_fichiers(dossier)
    ligne = cx.execute(
        "SELECT empreinte FROM bench.eval_jeux WHERE version = %s", (version,)
    ).fetchone()
    base = ligne[0] if ligne else None
    if fichiers != attendue or base != attendue:
        raise ArretEmpreinte(
            f"jeu {version} : empreinte attendue {attendue[:8]}…, fichiers "
            f"{fichiers[:8]}…, base {base[:8] + '…' if base else 'absente'}"
        )
    return attendue


def lire_jeu(cx, version: str) -> list[Question]:
    attendus: dict[str, dict[int, int]] = {}
    for qid, series_id, grade in cx.execute(
        "SELECT question_id, series_id, grade FROM bench.eval_attendus"
        " WHERE jeu_version = %s",
        (version,),
    ):
        attendus.setdefault(qid, {})[series_id] = grade
    return [
        Question(qid, texte, mode, famille, issue, attendus.get(qid, {}))
        for qid, texte, mode, famille, issue in cx.execute(
            "SELECT question_id, texte, mode, famille, issue_attendue"
            " FROM bench.eval_questions WHERE jeu_version = %s ORDER BY question_id",
            (version,),
        )
    ]
