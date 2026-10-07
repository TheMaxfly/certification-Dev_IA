"""020 — pgvector et les tables de l'encodage : ce que la base refuse, sur base
JETABLE.

Les règles de l'encodage E2 doivent être tenues par la base, pas par la bonne
volonté de l'encodeur : un vecteur de la mauvaise dimension, non unitaire,
rattaché à un fragment inventé ou à l'encodage d'un autre modèle ; un fragment
supprimé sous son vecteur ; un encodage par le service sans son image — tout
cela doit ÉCHOUER ici.
"""

from __future__ import annotations

import math
from argparse import Namespace

import psycopg
import pytest
from conftest import migrate

UP = Namespace(commande="up", target=None)
REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
DIGEST = "sha256:" + "b" * 64


def unitaire(dimension: int, axe: int = 0) -> str:
    v = [0.0] * dimension
    v[axe] = 1.0
    return "[" + ",".join(map(str, v)) + "]"


@pytest.fixture
def base_migree(base):
    migrate.commande_up(UP)
    with psycopg.connect(base) as cx:
        cx.execute(
            "INSERT INTO bench.corpus_docs (doc_key, source, doc_text)"
            " VALUES ('ms:1', 'ms_review', 'Une critique.')"
        )
        cx.execute(
            "INSERT INTO bench.corpus_chunks (chunk_id, doc_key, chunk_index,"
            " chunk_text) VALUES (10, 'ms:1', 0, 'Une'), (11, 'ms:1', 1, 'critique')"
        )
    return base


def encodage(cx, modele="BAAI/bge-m3", dimension=1024, **champs) -> int:
    valeurs = {
        "modele": modele,
        "revision": REVISION,
        "dimension": dimension,
        "precision_calcul": "float32",
        "prefixe_document": "",
        "prefixe_requete": "",
        "outil": "text-embeddings-inference",
        "outil_version": "1.9.4",
        "image": "ghcr.io/huggingface/text-embeddings-inference:86-1.9.4",
        "image_digest": DIGEST,
        "taille_lot": 16,
    } | champs
    colonnes = ", ".join(valeurs)
    marques = ", ".join(["%s"] * len(valeurs))
    return cx.execute(
        f"INSERT INTO bench.encodages ({colonnes}) VALUES ({marques})"  # nosec B608 — noms de colonnes fixes
        " RETURNING encodage_id",
        list(valeurs.values()),
    ).fetchone()[0]


def test_extension_vector_creee_en_0_6_0(base_migree):
    with psycopg.connect(base_migree) as cx:
        version, schema = cx.execute(
            "SELECT extversion, extnamespace::regnamespace::text"
            " FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
    assert (version, schema) == ("0.6.0", "public")


def test_colonnes_typees_par_modele(base_migree):
    with psycopg.connect(base_migree) as cx:
        types = dict(
            cx.execute(
                "SELECT c.relname, format_type(a.atttypid, a.atttypmod)"
                " FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid"
                " WHERE c.relnamespace = 'bench'::regnamespace"
                "   AND a.attname = 'embedding'"
            ).fetchall()
        )
    assert types == {
        "vecteurs_bge_m3": "vector(1024)",
        "vecteurs_embeddinggemma": "vector(768)",
    }


def test_fk_vers_les_fragments_sans_cascade(base_migree):
    with psycopg.connect(base_migree) as cx:
        regles = cx.execute(
            "SELECT conrelid::regclass::text, confdeltype FROM pg_constraint"
            " WHERE contype = 'f' AND confrelid = 'bench.corpus_chunks'::regclass"
            "   AND conrelid::regclass::text LIKE 'bench.vecteurs_%'"
        ).fetchall()
    # 'a' = NO ACTION : ni CASCADE ('c'), ni SET NULL ('n').
    assert sorted(regles) == [
        ("bench.vecteurs_bge_m3", "a"),
        ("bench.vecteurs_embeddinggemma", "a"),
    ]


def test_aucun_index_approximatif(base_migree):
    with psycopg.connect(base_migree) as cx:
        methodes = cx.execute(
            "SELECT DISTINCT am.amname FROM pg_index i"
            " JOIN pg_class c ON c.oid = i.indexrelid"
            " JOIN pg_am am ON am.oid = c.relam"
            " WHERE i.indrelid IN ('bench.vecteurs_bge_m3'::regclass,"
            "                      'bench.vecteurs_embeddinggemma'::regclass)"
        ).fetchall()
    assert methodes == [("btree",)]


def test_un_vecteur_conforme_est_accepte(base_migree):
    with psycopg.connect(base_migree) as cx:
        eid = encodage(cx)
        cx.execute(
            "INSERT INTO bench.vecteurs_bge_m3 (chunk_id, encodage_id, embedding)"
            " VALUES (10, %s, %s)",
            (eid, unitaire(1024)),
        )
        gid = encodage(
            cx,
            modele="google/embeddinggemma-300m",
            dimension=768,
            revision="57c266a740f537b4dc058e1b0cda161fd15afa75",
            prefixe_document="title: none | text: ",
            prefixe_requete="task: search result | query: ",
        )
        cx.execute(
            "INSERT INTO bench.vecteurs_embeddinggemma (chunk_id, encodage_id,"
            " embedding) VALUES (10, %s, %s)",
            (gid, unitaire(768, axe=3)),
        )


@pytest.mark.parametrize(
    ("vecteur", "motif"),
    [
        (unitaire(768), "expected 1024 dimensions"),
        ("[" + ",".join(["0.5"] * 1024) + "]", "vecteurs_bge_m3_embedding_check"),
    ],
    ids=["mauvaise_dimension", "non_unitaire"],
)
def test_vecteur_non_conforme_refuse(base_migree, vecteur, motif):
    with psycopg.connect(base_migree) as cx:
        eid = encodage(cx)
        with pytest.raises(psycopg.Error, match=motif):
            cx.execute(
                "INSERT INTO bench.vecteurs_bge_m3 (chunk_id, encodage_id,"
                " embedding) VALUES (10, %s, %s)",
                (eid, vecteur),
            )


def test_norme_tolere_1e_3(base_migree):
    presque = [0.0] * 1024
    presque[0] = math.sqrt(1.0009)  # norme 1,00045 : dans la tolérance
    with psycopg.connect(base_migree) as cx:
        eid = encodage(cx)
        cx.execute(
            "INSERT INTO bench.vecteurs_bge_m3 (chunk_id, encodage_id, embedding)"
            " VALUES (10, %s, %s)",
            (eid, "[" + ",".join(map(str, presque)) + "]"),
        )


def test_fragment_inconnu_refuse(base_migree):
    with psycopg.connect(base_migree) as cx:
        eid = encodage(cx)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cx.execute(
                "INSERT INTO bench.vecteurs_bge_m3 (chunk_id, encodage_id,"
                " embedding) VALUES (999, %s, %s)",
                (eid, unitaire(1024)),
            )


def test_vecteur_d_un_autre_modele_refuse(base_migree):
    """Un encodage EmbeddingGemma ne peut pas remplir la table de BGE-M3."""
    with psycopg.connect(base_migree) as cx:
        gid = encodage(cx, modele="google/embeddinggemma-300m", dimension=768)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cx.execute(
                "INSERT INTO bench.vecteurs_bge_m3 (chunk_id, encodage_id,"
                " embedding) VALUES (10, %s, %s)",
                (gid, unitaire(1024)),
            )


def test_fragment_vectorise_ne_se_supprime_pas(base_migree):
    """Ni directement, ni par la cascade corpus_docs → corpus_chunks."""
    with psycopg.connect(base_migree) as cx:
        eid = encodage(cx)
        cx.execute(
            "INSERT INTO bench.vecteurs_bge_m3 (chunk_id, encodage_id, embedding)"
            " VALUES (10, %s, %s)",
            (eid, unitaire(1024)),
        )
    for requete in (
        "DELETE FROM bench.corpus_chunks WHERE chunk_id = 10",
        "DELETE FROM bench.corpus_docs WHERE doc_key = 'ms:1'",
    ):
        with psycopg.connect(base_migree) as cx:
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                cx.execute(requete)
    # Le fragment sans vecteur, lui, reste supprimable.
    with psycopg.connect(base_migree) as cx:
        cx.execute("DELETE FROM bench.corpus_chunks WHERE chunk_id = 11")


def test_encodage_par_le_service_sans_image_refuse(base_migree):
    with psycopg.connect(base_migree) as cx:
        with pytest.raises(psycopg.errors.CheckViolation, match="service_image"):
            encodage(cx, image=None, image_digest=None)


def test_repli_sans_image_admis(base_migree):
    with psycopg.connect(base_migree) as cx:
        encodage(
            cx,
            outil="sentence-transformers",
            outil_version="5.2.0",
            image=None,
            image_digest=None,
        )


def test_encodage_termine_compte_ses_fragments(base_migree):
    with psycopg.connect(base_migree) as cx:
        eid = encodage(cx)
        with pytest.raises(psycopg.errors.CheckViolation, match="termine"):
            cx.execute(
                "UPDATE bench.encodages SET termine_le = now() WHERE encodage_id = %s",
                (eid,),
            )
    with psycopg.connect(base_migree) as cx:
        cx.execute(
            "UPDATE bench.encodages SET termine_le = now(), nb_fragments = 2"
            " WHERE encodage_id = %s",
            (eid,),
        )


def test_memes_parametres_meme_encodage(base_migree):
    with psycopg.connect(base_migree) as cx:
        encodage(cx)
        with pytest.raises(psycopg.errors.UniqueViolation):
            encodage(cx)
    # Le repli, sans digest : NULLS NOT DISTINCT, le doublon reste refusé.
    with psycopg.connect(base_migree) as cx:
        repli = {
            "outil": "sentence-transformers",
            "outil_version": "5.2.0",
            "image": None,
            "image_digest": None,
        }
        encodage(cx, **repli)
        with pytest.raises(psycopg.errors.UniqueViolation):
            encodage(cx, **repli)


def test_revision_par_commit_seulement(base_migree):
    with psycopg.connect(base_migree) as cx:
        with pytest.raises(psycopg.errors.CheckViolation, match="revision"):
            encodage(cx, revision="main")
