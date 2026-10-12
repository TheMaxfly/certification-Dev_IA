"""Plusieurs corpus dans `bench` (migration 021) — ce que le module 09 doit tenir.

- une seule écriture de vecteurs, qui exige le corpus nommé, sans défaut ; tout
  encodage inscrit nomme son corpus (règle de CONTRIBUTING.md) ;
- l'hôte du service est un réglage de la configuration ;
- deux encodages d'un même modèle coexistent, un par corpus ; l'encodeur ne
  lit que les fragments du corpus encodé, et ne devine pas le corpus.
"""

from __future__ import annotations

import ast
import inspect
import re
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from test_encodage import doublure  # noqa: F401 — la fixture du service simulé

from service_embedding import encodage
from service_embedding.client import ClientService
from service_embedding.configuration import ConfigurationInvalide, charger
from service_embedding.encodage import EncodageRefuse, encoder_corpus

SRC = Path(encodage.__file__).resolve().parent
ECRITURE = re.compile(
    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(\s+TABLE)?|COPY)\s+(ONLY\s+)?"
    r"bench\.(corpus_docs|corpus_chunks|vecteurs_\w+)\b",
    re.IGNORECASE,
)
#: Une écriture dont la table n'est connue qu'à l'exécution échappe à la règle :
#: elle est refusée partout.
ECRITURE_DYNAMIQUE = re.compile(
    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|COPY)\s+\{", re.I
)


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


def litteraux() -> list[tuple[Path, int, int, str]]:
    trouves = []
    for fichier in sorted(SRC.rglob("*.py")):
        for n in ast.walk(ast.parse(fichier.read_text(encoding="utf-8"))):
            t = texte_sql(n)
            if t is not None:
                trouves.append((fichier, n.lineno, n.end_lineno, t))
    return trouves


# --------------------------------------------------------------------------- #
#  La règle d'écriture, sur le code source
# --------------------------------------------------------------------------- #


def test_toute_ecriture_de_vecteur_passe_par_ecrire_vecteurs():
    lignes, debut = inspect.getsourcelines(encodage.ecrire_vecteurs)
    fin = debut + len(lignes)
    dans_la_porte, hors_porte = 0, []
    for f, a, b, texte in litteraux():
        if ECRITURE.search(texte):
            if f == Path(encodage.__file__).resolve() and debut <= a and b <= fin:
                dans_la_porte += 1
            else:
                hors_porte.append(f"{f.name}:{a}")
    assert not hors_porte, f"écriture hors de ecrire_vecteurs : {hors_porte}"
    assert dans_la_porte == 2, "témoin : les deux tables de vecteurs"


def test_aucune_ecriture_a_table_dynamique():
    assert [
        f"{f.name}:{a}" for f, a, _, t in litteraux() if ECRITURE_DYNAMIQUE.search(t)
    ] == []


def test_tout_encodage_inscrit_nomme_son_corpus():
    inscriptions = [
        t for *_, t in litteraux() if re.search(r"INSERT\s+INTO\s+bench\.encodages", t)
    ]
    assert inscriptions and all("corpus_id" in t for t in inscriptions)


def test_le_corpus_est_nomme_sans_defaut():
    corpus = inspect.signature(encodage.ecrire_vecteurs).parameters["corpus_id"]
    assert corpus.kind is inspect.Parameter.KEYWORD_ONLY
    assert corpus.default is inspect.Parameter.empty


# --------------------------------------------------------------------------- #
#  L'hôte du service : un réglage
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("nom", ["bge-m3", "embeddinggemma"])
def test_l_hote_vient_de_la_configuration(nom):
    instance = charger(nom)
    assert instance.hote == "127.0.0.1"
    assert ClientService(instance).url == f"http://127.0.0.1:{instance.port}"
    autre = replace(instance, hote="tei-cpu.local")
    assert ClientService(autre).url == f"http://tei-cpu.local:{instance.port}"
    assert ClientService(autre, hote="10.0.0.2").hote == "10.0.0.2", (
        "désignation explicite"
    )


@pytest.mark.parametrize("valeur", ["", "127.0.0.1/x", '"un hote"'])
def test_un_hote_mal_forme_est_refuse(tmp_path, valeur):
    from service_embedding.configuration import DOSSIER_CONFIG

    texte = (DOSSIER_CONFIG / "bge-m3.env").read_text(encoding="utf-8")
    (tmp_path / "bge-m3.env").write_text(
        texte.replace("CLIENT_HOTE=127.0.0.1", f"CLIENT_HOTE={valeur}"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationInvalide, match="CLIENT_HOTE|guillemets|illisible"):
        charger("bge-m3", tmp_path)


# --------------------------------------------------------------------------- #
#  Deux corpus, deux encodages du même modèle
# --------------------------------------------------------------------------- #


def corpus_v2(dsn: str) -> None:
    """Un v2 : le même document que le v1 (même doc_key), découpé autrement."""
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO bench.corpus (corpus_id, regle, chunking_id)"
            " SELECT 'v2', 'essai', chunking_id FROM bench.chunking_strategies"
            " WHERE name = 'char_1200_overlap_200'"
        )
        cx.execute(
            "INSERT INTO bench.corpus_docs (corpus_id, doc_key, source, doc_text)"
            " VALUES ('v2', 'ms:1', 'ms_review', 'x')"
        )
        cx.execute(
            "INSERT INTO bench.corpus_chunks (corpus_id, chunk_id, doc_key,"
            " chunk_index, chunk_text)"
            " VALUES ('v2', 201, 'ms:1', 0, 'Une critique, en un seul morceau.')"
        )


def par_encodage(dsn: str) -> list[tuple]:
    with psycopg.connect(dsn) as cx:
        return cx.execute(
            "SELECT e.corpus_id, e.encodage_id, e.nb_fragments, count(v.chunk_id)"
            " FROM bench.encodages e"
            " LEFT JOIN bench.vecteurs_bge_m3 v USING (encodage_id)"
            " GROUP BY 1, 2, 3 ORDER BY 1"
        ).fetchall()


def test_deux_encodages_du_meme_modele_un_par_corpus(base, doublure):  # noqa: F811
    instance, client, service = doublure("bge-m3")
    v1 = encoder_corpus(instance, client, service, base)
    assert (v1["corpus_id"], v1["vecteurs_ecrits"]) == ("v1", 3)
    corpus_v2(base)
    v2 = encoder_corpus(instance, client, service, base, corpus_id="v2")
    assert (v2["corpus_id"], v2["vecteurs_ecrits"], v2["termine"]) == ("v2", 1, True)
    (c1, e1, n1, m1), (c2, e2, n2, m2) = par_encodage(base)
    assert (c1, n1, m1, c2, n2, m2) == ("v1", 3, 3, "v2", 1, 1) and e1 != e2
    # Rejeu du v2 : rien à encoder, rien d'écrit.
    assert (
        encoder_corpus(instance, client, service, base, corpus_id="v2")[
            "vecteurs_ecrits"
        ]
        == 0
    )


def test_plusieurs_corpus_sans_nom_refuse(base, doublure):  # noqa: F811
    instance, client, service = doublure("bge-m3")
    corpus_v2(base)
    with pytest.raises(EncodageRefuse, match="nommer le corpus"):
        encoder_corpus(instance, client, service, base)
    assert par_encodage(base) == []


def test_corpus_inconnu_refuse(base, doublure):  # noqa: F811
    instance, client, service = doublure("bge-m3")
    with pytest.raises(EncodageRefuse, match="inconnu"):
        encoder_corpus(instance, client, service, base, corpus_id="v7")


def test_un_second_encodage_du_meme_corpus_reste_refuse(base, doublure):  # noqa: F811
    instance, client, service = doublure("bge-m3")
    corpus_v2(base)
    encoder_corpus(instance, client, service, base, corpus_id="v2")
    autre = replace(service, digest="sha256:" + "d" * 64)
    with pytest.raises(EncodageRefuse, match="pour le corpus v2"):
        encoder_corpus(instance, client, autre, base, corpus_id="v2")
    # Sur le v1, cet autre encodage est admis : un encodage par modèle et par corpus.
    assert encoder_corpus(instance, client, autre, base, corpus_id="v1")["termine"]
