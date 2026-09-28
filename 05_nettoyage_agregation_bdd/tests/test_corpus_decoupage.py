"""Découpage : réplique exacte du banc de décembre.

La référence n'est pas recopiée ici : `chunk_text_chars` est EXTRAITE du source
de `06_.../Bench_embedding.py` par l'AST, puis comparée à `decouper` sur des
textes variés. Le module 06 n'est ni importé (faiss, pandas…) ni modifié — il
est lu. Si quelqu'un change l'un sans l'autre, ce test le dit.
"""

from __future__ import annotations

import ast
import hashlib
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from corpus import decoupage  # noqa: E402

BANC = (
    Path(__file__).resolve().parents[2]
    / "06_benchmark_embeddings_llm/Bench_embedding.py"
)


def reference_decembre():
    arbre = ast.parse(BANC.read_text(encoding="utf-8"))
    fonction = next(
        n
        for n in arbre.body
        if isinstance(n, ast.FunctionDef) and n.name == "chunk_text_chars"
    )
    espace: dict = {}
    exec(compile(ast.Module([fonction], []), str(BANC), "exec"), espace)  # noqa: S102
    return espace["chunk_text_chars"]


def textes():
    alea = random.Random(20260928)
    alphabet = "abcdéè çà\n  .,;—漢字"
    yield ""
    yield "   "
    yield "court"
    yield "x" * 1200
    yield "x" * 1201
    yield "  " + "y" * 3000 + "  "
    yield ("mot " * 900).strip()
    for _ in range(200):
        n = alea.choice([10, 49, 50, 800, 1199, 1200, 1201, 2200, 5000, 20000])
        yield "".join(alea.choice(alphabet) for _ in range(n))


def test_identique_a_la_fonction_de_decembre():
    decembre = reference_decembre()
    for texte in textes():
        assert decoupage.decouper(texte, 1200, 200) == decembre(texte, 1200, 200)


def test_positions_relatives_au_texte_epure():
    texte = "  " + "a" * 1300
    fragments = decoupage.decouper(texte)
    assert fragments[0] == (0, 1200, "a" * 1200)
    assert fragments[1][:2] == (1000, 1300)


def test_aucune_troncature():
    texte = "".join(chr(0x61 + i % 26) for i in range(5000))
    for debut, fin, fragment in decoupage.decouper(texte):
        assert fragment == texte[debut:fin]
        assert len(fragment) <= decoupage.TAILLE


@pytest.mark.parametrize(("longueur", "decoupe"), [(49, False), (50, True)])
def test_plancher_de_decembre(longueur, decoupe):
    assert decoupage.est_decoupe("z" * longueur) is decoupe


def test_empreinte_est_le_sha1_du_banc():
    attendu = hashlib.sha1("été".encode()).hexdigest()
    assert decoupage.empreinte_fragment("été") == attendu
