"""Contrôles d'après encodage — spec E2 §4.3, valeurs posées d'avance.

    uv run python -m service_embedding.verification --sortie verification.json

Lecture seule (`default_transaction_read_only`). Chaque contrôle rend
(attendu, constaté, conforme). Sort en erreur si un contrôle n'est pas conforme.

L'empreinte du jeu d'évaluation est recalculée depuis les fichiers versionnés,
par la même règle que `evaluation.geler.empreinte` (module 05) : sha256 du
manifeste « chemin<TAB>sha256 » trié de tous les fichiers de la version ; elle
est aussi lue dans `bench.eval_jeux`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import psycopg

from service_embedding.configuration import RACINE

# Valeurs posées d'avance (attendus de l'encodage, spec E2 jour 1 ; corpus et jeu v2 :
# état du 2026-09-30). Elles décrivent le corpus v1 et ses deux encodages : depuis
# la migration 021, chaque lecture est bornée à ce corpus et à ses encodages.
CORPUS = "v1"
FRAGMENTS = 66_290
DOCUMENTS = 48_090
ENCODAGES = 2
JEU = "v2"
EMPREINTE_JEU = "31712b0d0b0cbd318ef228da105673af0cba2db431a72462f7635b2f6ec78b30"
DOSSIER_JEU = RACINE.parent / "database" / "donnees" / "jeu_evaluation" / JEU
TABLES = {
    "bench.vecteurs_bge_m3": 1024,
    "bench.vecteurs_embeddinggemma": 768,
}
TOLERANCE_NORME = 1e-3


def empreinte_fichiers(dossier: Path) -> str:
    lignes = sorted(
        (str(f.relative_to(dossier)), hashlib.sha256(f.read_bytes()).hexdigest())
        for f in dossier.rglob("*")
        if f.is_file()
    )
    texte = "".join(f"{chemin}\t{h}\n" for chemin, h in lignes)
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def controle(nom: str, attendu, constate) -> dict:
    return {
        "controle": nom,
        "attendu": attendu,
        "constate": constate,
        "conforme": attendu == constate,
    }


def verifier(dsn: str) -> list[dict]:
    resultats = []
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as cx:

        def un(sql: str, *params):
            return cx.execute(sql, params or None).fetchone()[0]

        # Les vecteurs ne se lisent que filtrés par encodage : ceux du corpus.
        ses_encodages = "SELECT encodage_id FROM bench.encodages WHERE corpus_id = %s"
        for table, dimension in TABLES.items():
            resultats += [
                controle(
                    f"{table} : lignes",
                    FRAGMENTS,
                    un(
                        f"SELECT count(*) FROM {table}"  # nosec B608
                        f" WHERE encodage_id IN ({ses_encodages})",
                        CORPUS,
                    ),
                ),
                controle(
                    f"{table} : vecteurs NULL, dimension fausse, norme hors 1 ± 1e-3",
                    0,
                    un(
                        f"SELECT count(*) FROM {table}"  # nosec B608
                        f" WHERE encodage_id IN ({ses_encodages}) AND ("
                        " embedding IS NULL OR public.vector_dims(embedding) <> %s"
                        " OR abs(public.vector_norm(embedding) - 1) > %s)",
                        CORPUS,
                        dimension,
                        TOLERANCE_NORME,
                    ),
                ),
                controle(
                    f"{table} : chunk_id sans vecteur",
                    0,
                    un(
                        "SELECT count(*) FROM bench.corpus_chunks c"
                        " WHERE c.corpus_id = %s AND NOT EXISTS"
                        f" (SELECT 1 FROM {table} v WHERE v.chunk_id = c.chunk_id"  # nosec B608
                        f"  AND v.encodage_id IN ({ses_encodages}))",
                        CORPUS,
                        CORPUS,
                    ),
                ),
                controle(
                    f"{table} : vecteur sans chunk_id",
                    0,
                    un(
                        f"SELECT count(*) FROM {table} v"  # nosec B608
                        f" WHERE v.encodage_id IN ({ses_encodages}) AND NOT EXISTS"
                        " (SELECT 1 FROM bench.corpus_chunks c"
                        "  WHERE c.chunk_id = v.chunk_id)",
                        CORPUS,
                    ),
                ),
                controle(
                    f"{table} : encodages distincts",
                    1,
                    un(
                        f"SELECT count(DISTINCT encodage_id) FROM {table}"  # nosec B608
                        f" WHERE encodage_id IN ({ses_encodages})",
                        CORPUS,
                    ),
                ),
            ]
        resultats += [
            controle(
                "bench.encodages : lignes",
                ENCODAGES,
                un("SELECT count(*) FROM bench.encodages WHERE corpus_id = %s", CORPUS),
            ),
            controle(
                "bench.encodages : terminés, nombre de fragments = corpus",
                ENCODAGES,
                un(
                    "SELECT count(*) FROM bench.encodages WHERE corpus_id = %s"
                    " AND termine_le IS NOT NULL AND nb_fragments = %s",
                    CORPUS,
                    FRAGMENTS,
                ),
            ),
            controle(
                "corpus : documents / fragments",
                [DOCUMENTS, FRAGMENTS],
                [
                    un(
                        "SELECT count(*) FROM bench.corpus_docs WHERE corpus_id = %s",
                        CORPUS,
                    ),
                    un(
                        "SELECT count(*) FROM bench.corpus_chunks WHERE corpus_id = %s",
                        CORPUS,
                    ),
                ],
            ),
            controle(
                f"jeu {JEU} : empreinte en base",
                EMPREINTE_JEU,
                un("SELECT empreinte FROM bench.eval_jeux WHERE version = %s", JEU),
            ),
        ]
    resultats.append(
        controle(
            f"jeu {JEU} : empreinte des fichiers",
            EMPREINTE_JEU,
            empreinte_fichiers(DOSSIER_JEU),
        )
    )
    return resultats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sortie", type=Path, required=True)
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL absente")
    resultats = verifier(dsn)
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(json.dumps(resultats, ensure_ascii=False, indent=2), "utf-8")
    for r in resultats:
        marque = "OK " if r["conforme"] else "ÉCART"
        attendu, constate = r["attendu"], r["constate"]
        print(f"{marque} {r['controle']} : attendu {attendu}, constaté {constate}")
    return 0 if all(r["conforme"] for r in resultats) else 1


if __name__ == "__main__":
    sys.exit(main())
