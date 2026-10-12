"""Plusieurs corpus dans `bench` (migration 021) — ce que le module 05 doit tenir.

- une seule porte d'écriture du corpus, qui exige le corpus nommé, sans défaut
  (règle de CONTRIBUTING.md) : vérifié sur le code source, sans base ;
- le chargeur du v1 ne lit ni ne touche un autre corpus — rejeu, rechargement
  complet et cible qui change compris ;
- la lecture du rattachement porte sur UN corpus : nommé, sinon celui de
  l'encodage en service ; jamais deviné entre plusieurs.
"""

from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path

import psycopg
import pytest
from conftest import lire

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_corpus_construire import charger, ecrire_raw, jeu  # noqa: E402, F401

from corpus import ecriture  # noqa: E402
from evaluation import atteignabilite  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src"
#: Une écriture d'une table du corpus ou des vecteurs, dans un littéral SQL.
ECRITURE = re.compile(
    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(\s+TABLE)?|COPY)\s+(ONLY\s+)?"
    r"bench\.(corpus_docs|corpus_chunks|vecteurs_\w+)\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
#  La règle d'écriture, sur le code source
# --------------------------------------------------------------------------- #


def texte_sql(n: ast.AST) -> str | None:
    """Le texte d'un littéral ; un f-string est recomposé, `{…}` à la place de
    chaque valeur — sans quoi `f"COPY {table}"` échapperait au contrôle."""
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return n.value
    if isinstance(n, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) else "{…}" for v in n.values
        )
    return None


def ecritures_du_module() -> list[tuple[Path, int, int]]:
    trouvees = []
    for fichier in sorted(SRC.rglob("*.py")):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        for n in ast.walk(arbre):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                if ECRITURE.search(n.value):
                    trouvees.append((fichier.resolve(), n.lineno, n.end_lineno))
    return trouvees


def test_toute_ecriture_du_corpus_passe_par_ecrire_corpus():
    lignes, debut = inspect.getsourcelines(ecriture.ecrire_corpus)
    porte = (
        Path(inspect.getsourcefile(ecriture)).resolve(),
        debut,
        debut + len(lignes),
    )
    trouvees = ecritures_du_module()
    hors_porte = [
        f"{f.relative_to(SRC.resolve())}:{a}"
        for f, a, b in trouvees
        if not (f == porte[0] and porte[1] <= a and b <= porte[2])
    ]
    assert not hors_porte, f"écriture du corpus hors de ecrire_corpus : {hors_porte}"
    # Témoin : la règle voit bien les écritures de la porte elle-même.
    assert len(trouvees) == 6


#: Une écriture dont la table n'est connue qu'à l'exécution échappe à la règle.
ECRITURE_DYNAMIQUE = re.compile(
    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|COPY)\s+\{", re.I
)
#: Les seules admises, nommées avec leur motif : le COPY du chargeur Manga
#: Sanctuary n'écrit que son staging (`staging.ms_volumes`, `staging.ms_reviews`).
DYNAMIQUES_ADMISES = {("identity/charger_ms.py", "copier")}


def test_aucune_autre_ecriture_a_table_dynamique():
    trouvees = set()
    for fichier in sorted(SRC.rglob("*.py")):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        for fonction in ast.walk(arbre):
            if not isinstance(fonction, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for n in ast.walk(fonction):
                t = texte_sql(n)
                if t is not None and ECRITURE_DYNAMIQUE.search(t):
                    trouvees.add((str(fichier.relative_to(SRC)), fonction.name))
    assert trouvees == DYNAMIQUES_ADMISES


def test_le_corpus_est_nomme_sans_defaut():
    corpus = inspect.signature(ecriture.ecrire_corpus).parameters["corpus_id"]
    assert corpus.kind is inspect.Parameter.KEYWORD_ONLY
    assert corpus.default is inspect.Parameter.empty


# --------------------------------------------------------------------------- #
#  Le chargeur du v1 ne touche pas au v2
# --------------------------------------------------------------------------- #

V2_DOCS = [
    ("ms_review:101", "ms_review", "Une autre version."),
    ("ms_synopsis:1", "x", "Un résumé."),
]


def ecrire_v2(url: str) -> None:
    """Un corpus v2 : un document de même clé qu'un document du v1, un autre que
    le v1 n'a pas ; écrits par la porte du module, corpus nommé."""
    with psycopg.connect(url) as cx, cx.cursor() as cur:
        cur.execute(
            "INSERT INTO bench.corpus (corpus_id, regle, chunking_id)"
            " SELECT 'v2', 'essai', chunking_id FROM bench.chunking_strategies"
            " WHERE name = 'char_1200_overlap_200'"
        )
        cur.execute(
            "CREATE TEMP TABLE cible_docs (doc_key text, source text, series_id bigint,"
            " kitsu_id bigint, boost_score numeric, doc_text text, metadata_json jsonb,"
            " title text) ON COMMIT DROP;"
            "CREATE TEMP TABLE cible_chunks (doc_key text, chunk_index integer,"
            " chunk_text text, char_start integer, char_end integer, chunk_hash text)"
            " ON COMMIT DROP"
        )
        for doc_key, source, texte in V2_DOCS:
            cur.execute(
                "INSERT INTO cible_docs (doc_key, source, doc_text)"
                " VALUES (%s, %s, %s)",
                (doc_key, source, texte),
            )
            cur.execute(
                "INSERT INTO cible_chunks (doc_key, chunk_index, chunk_text)"
                " VALUES (%s, 0, %s)",
                (doc_key, texte),
            )
        ecritures = ecriture.ecrire_corpus(cur, corpus_id="v2")
    assert sum(ecritures["docs ajoutés"].values()) == 2


def etat_v2(url: str) -> list[tuple]:
    return lire(
        url,
        "SELECT d.doc_key, d.doc_text, k.chunk_id, k.chunk_text"
        " FROM bench.corpus_docs d JOIN bench.corpus_chunks k"
        " ON k.corpus_id = d.corpus_id AND k.doc_key = d.doc_key"
        " WHERE d.corpus_id = 'v2' ORDER BY 1, 3",
    )


def test_un_rejeu_du_chargeur_v1_ne_touche_pas_au_v2(jeu):  # noqa: F811
    premier = charger(jeu)
    ecrire_v2(jeu["url"])
    avant = etat_v2(jeu["url"])
    assert len(avant) == 2

    rejeu = charger(jeu)
    assert all(not v for v in rejeu["ecritures"].values()), rejeu["ecritures"]
    assert rejeu["empreinte_apres"] == premier["empreinte_apres"], "v1 inchangé"
    assert etat_v2(jeu["url"]) == avant, "v2 inchangé, chunk_id compris"


def test_un_v1_qui_change_ne_touche_pas_au_v2(jeu, tmp_path):  # noqa: F811
    charger(jeu)
    ecrire_v2(jeu["url"])
    avant = etat_v2(jeu["url"])
    # La cible du v1 change : une critique entre au snapshot, une autre en sort.
    jeu["raw"] = ecrire_raw(tmp_path, site_ids=[101, 102, 103, 104, 105])
    r = charger(jeu)
    assert r["ecritures"]["docs ajoutés"] and r["ecritures"]["docs retirés"]
    assert etat_v2(jeu["url"]) == avant


def test_le_rechargement_complet_du_v1_ne_vide_que_le_v1(jeu):  # noqa: F811
    charger(jeu)
    ecrire_v2(jeu["url"])
    avant = etat_v2(jeu["url"])
    r = charger(jeu, a_blanc=True)
    assert sum(r["ecritures"]["vidage"].values()) == len(
        lire(jeu["url"], "SELECT 1 FROM bench.corpus_docs WHERE corpus_id = 'v1'")
    )
    assert etat_v2(jeu["url"]) == avant


# --------------------------------------------------------------------------- #
#  Le corpus lu
# --------------------------------------------------------------------------- #


def deux_corpus(url: str) -> None:
    """v1 : la série 1 par une critique ; v2 : les séries 1 et 2."""
    with psycopg.connect(url) as cx:
        cx.execute(
            "INSERT INTO manga.ms_series_enriched (series_id, series_title)"
            " VALUES (1, 'Un'), (2, 'Deux')"
        )
        cx.execute(
            "INSERT INTO bench.corpus (corpus_id, regle, chunking_id)"
            " SELECT 'v2', 'essai', chunking_id FROM bench.chunking_strategies"
            " WHERE name = 'char_1200_overlap_200'"
        )
        for corpus_id, series in (("v1", (1,)), ("v2", (1, 2))):
            for s in series:
                cx.execute(
                    "INSERT INTO bench.corpus_docs (corpus_id, doc_key, source,"
                    " series_id, doc_text) VALUES (%s, %s, 'ms_review', %s, 'x')",
                    (corpus_id, f"ms_review:{s}", s),
                )
                cx.execute(
                    "INSERT INTO bench.corpus_chunks (corpus_id, doc_key, chunk_index,"
                    " chunk_text) VALUES (%s, %s, 0, 'x')",
                    (corpus_id, f"ms_review:{s}"),
                )


def mettre_en_service(url: str, corpus_id: str) -> None:
    with psycopg.connect(url) as cx:
        eid = cx.execute(
            "INSERT INTO bench.encodages (corpus_id, modele, revision, dimension,"
            " precision_calcul, prefixe_document, prefixe_requete, outil,"
            " outil_version, image, image_digest, taille_lot, nb_fragments,"
            " termine_le) VALUES (%s, 'google/embeddinggemma-300m', %s, 768,"
            " 'float32', '', '', 'text-embeddings-inference', '1.9.4', 'img', %s,"
            " 16, 1, now()) RETURNING encodage_id",
            (corpus_id, "a" * 40, "sha256:" + "b" * 64),
        ).fetchone()[0]
        cx.execute(
            "INSERT INTO bench.promotions (decision, encodage_id, motif, decide_par)"
            " VALUES ('promu', %s, 'essai', 'essai')",
            (eid,),
        )


def test_un_seul_corpus_est_lu_sans_etre_nomme(base):
    with psycopg.connect(base) as cx:
        assert atteignabilite.corpus_lu(cx) == "v1"


def test_plusieurs_corpus_sans_service_il_faut_nommer(base):
    deux_corpus(base)
    with psycopg.connect(base) as cx:
        with pytest.raises(atteignabilite.CorpusNonDesigne):
            atteignabilite.atteignables(cx)
        assert atteignabilite.atteignables(cx, "v1") == {1}
        assert atteignabilite.atteignables(cx, "v2") == {1, 2}
        assert len(atteignabilite.rattachement(cx, "v2")) == 2


def test_sans_corpus_nomme_on_lit_celui_du_service(base):
    deux_corpus(base)
    mettre_en_service(base, "v2")
    with psycopg.connect(base) as cx:
        assert atteignabilite.corpus_lu(cx) == "v2"
        assert atteignabilite.atteignables(cx) == {1, 2}
        assert atteignabilite.atteignables(cx, "v1") == {1}, "le nom l'emporte"
        decomposition, _ = atteignabilite.mesurer(cx, "v1")
        assert decomposition["atteignables"] == 1
