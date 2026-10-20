"""Les empreintes de contenu de la sauvegarde (`sauvegarde/sauvegarder.sh`), jouées
sur une base migrée.

Une empreinte compare la base source à sa restauration, et deux sauvegardes entre
elles : elle doit ne dépendre que du contenu, jamais de l'ordre physique des lignes.
Chaque table s'agrège donc triée sur une clé unique. Depuis la migration 021,
`doc_key` ne l'est plus seule dans `bench.corpus_docs` (v1 et v2 partagent des
clés) : le tri porte sur (corpus_id, doc_key).
"""

from __future__ import annotations

import re

import psycopg
from conftest import RACINE, harnais

SCRIPT = RACINE.parent / "sauvegarde" / "sauvegarder.sh"


def requetes() -> dict[str, str]:
    """Les requêtes d'empreinte du script, par table, telles qu'il les envoie."""
    texte = SCRIPT.read_text(encoding="utf-8")
    bloc = re.search(r'SQL_EMPREINTES="\$SESSION_FIXE\n(.*?)"\n', texte, re.S)
    assert bloc, "SQL_EMPREINTES introuvable dans le script de sauvegarde"
    sql = bloc.group(1).replace('\\"', '"')
    return {
        re.match(r"SELECT '([^']+)'", r).group(1): r
        for r in (ligne.strip().rstrip(";") for ligne in sql.splitlines())
        if r.startswith("SELECT '")
    }


def empreinte(dsn: str, requete: str) -> str:
    with psycopg.connect(dsn) as cx:
        cx.execute("SET TimeZone = 'UTC'")
        return cx.execute(requete).fetchone()[0]


def remplir(dsn: str, docs: list[tuple[str, str]]) -> None:
    """Un corpus v2, et des documents insérés dans l'ordre donné."""
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO bench.corpus (corpus_id, regle, chunking_id)"
            " SELECT 'v2', 'essai', chunking_id FROM bench.chunking_strategies"
            " WHERE name = 'char_1200_overlap_200'"
        )
        for corpus, cle in docs:
            cx.execute(
                "INSERT INTO bench.corpus_docs (corpus_id, doc_key, source, doc_text)"
                " VALUES (%s, %s, 'ms_review', %s)",
                (corpus, cle, f"texte {corpus} {cle}"),
            )


def test_chaque_table_du_banc_a_sa_requete():
    assert {
        "bench.corpus_docs",
        "bench.corpus_chunks",
        "bench.encodages",
        "bench.eval_mesures",
    } <= set(requetes())


def test_l_empreinte_des_documents_ne_depend_pas_de_l_ordre_physique(
    conteneur_postgres, gabarit
):
    """Les mêmes documents, insérés dans deux ordres : la même empreinte. Une clé
    partagée par v1 et v2 (« ms:1 ») départage le tri par le corpus."""
    docs = [("v1", "ms:1"), ("v2", "ms:1"), ("v1", "ms:2"), ("v2", "ms:3")]
    requete = requetes()["bench.corpus_docs"]
    vues = []
    for ordre in (docs, list(reversed(docs))):
        with harnais.base_depuis_gabarit(conteneur_postgres, gabarit) as dsn:
            remplir(dsn, ordre)
            vues.append(empreinte(dsn, requete))
    assert vues[0] == vues[1], "l'empreinte dépend de l'ordre d'insertion"
