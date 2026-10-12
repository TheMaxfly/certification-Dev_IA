"""021 — plusieurs corpus et plusieurs encodages côte à côte ; le journal des
promotions ; un corpus clos qui ne bouge plus. Sur base JETABLE.

Ce que la base doit tenir d'elle-même : deux encodages d'un même modèle dans une
même table sans jamais mêler deux corpus ; un seul encodage en service, tiré d'un
journal où l'on ajoute sans jamais modifier ; un corpus clos fermé à toute
écriture, sauf l'extension nommée ; et, sur une base qui porte déjà un corpus,
une migration qui ne change ni ne réécrit aucune ligne.
"""

from __future__ import annotations

from argparse import Namespace

import psycopg
import pytest
from conftest import migrate

UP = Namespace(commande="up", target=None)
JUSQUA_020 = Namespace(commande="up", target="020")
GEMMA = "google/embeddinggemma-300m"
BGE = "BAAI/bge-m3"
REVISION = {
    GEMMA: "57c266a740f537b4dc058e1b0cda161fd15afa75",
    BGE: "5617a9f6" + "0" * 32,
}
DIMENSION = {GEMMA: 768, BGE: 1024}
TABLE = {GEMMA: "bench.vecteurs_embeddinggemma", BGE: "bench.vecteurs_bge_m3"}
DIGEST = "sha256:" + "b" * 64

#: Contenu des tables, colonnes NOMMÉES : une colonne ajoutée par 021 ne compte
#: pas, une valeur changée, si.
EMPREINTES = {
    "docs": "SELECT md5(string_agg(ROW(doc_key, source, series_id, kitsu_id,"
    " boost_score, doc_text, metadata_json, title)::text, '|' ORDER BY doc_key))"
    " FROM bench.corpus_docs",
    "fragments": "SELECT md5(string_agg(ROW(chunk_id, doc_key, chunk_index,"
    " chunk_text, char_start, char_end, token_count, chunk_hash)::text, '|'"
    " ORDER BY chunk_id)) FROM bench.corpus_chunks",
    "gemma": "SELECT md5(string_agg(ROW(chunk_id, encodage_id, modele,"
    " embedding)::text, '|' ORDER BY chunk_id)) FROM bench.vecteurs_embeddinggemma",
    "encodages": "SELECT md5(string_agg(ROW(encodage_id, modele, revision,"
    " termine_le, nb_fragments)::text, '|' ORDER BY encodage_id))"
    " FROM bench.encodages",
    "mesures": "SELECT md5(string_agg(ROW(mesure_id, run_id, metrique, k,"
    " valeur)::text, '|' ORDER BY mesure_id)) FROM bench.eval_mesures",
}


def unitaire(modele: str, axe: int = 0) -> str:
    v = ["0"] * DIMENSION[modele]
    v[axe] = "1"
    return "[" + ",".join(v) + "]"


def encodage(cx, modele=GEMMA, corpus=None, termine=True) -> int:
    colonnes = (
        "modele, revision, dimension, precision_calcul, prefixe_document,"
        " prefixe_requete, outil, outil_version, image, image_digest, taille_lot"
    )
    valeurs = [
        modele,
        REVISION[modele],
        DIMENSION[modele],
        "float32",
        "",
        "",
        "text-embeddings-inference",
        "1.9.4",
        "img",
        DIGEST,
        16,
    ]
    if corpus is not None:
        colonnes += ", corpus_id"
        valeurs.append(corpus)
    eid = cx.execute(
        f"INSERT INTO bench.encodages ({colonnes})"  # nosec B608 — colonnes fixes
        f" VALUES ({', '.join(['%s'] * len(valeurs))}) RETURNING encodage_id",
        valeurs,
    ).fetchone()[0]
    if termine:
        terminer(cx, eid)
    return eid


def terminer(cx, eid: int) -> None:
    cx.execute(
        "UPDATE bench.encodages SET termine_le = now(), nb_fragments = 1"
        " WHERE encodage_id = %s",
        (eid,),
    )


def corpus(cx, corpus_id: str, clos: bool = False) -> None:
    cx.execute(
        "INSERT INTO bench.corpus (corpus_id, regle, chunking_id)"
        " SELECT %s, 'essai', chunking_id FROM bench.chunking_strategies"
        " WHERE name = 'char_1200_overlap_200'",
        (corpus_id,),
    )
    if clos:
        clore(cx, corpus_id)


def clore(cx, corpus_id: str) -> None:
    cx.execute(
        "UPDATE bench.corpus SET clos_le = now(),"
        " nb_documents = (SELECT count(*) FROM bench.corpus_docs"
        "                 WHERE corpus_id = %s),"
        " nb_fragments = (SELECT count(*) FROM bench.corpus_chunks"
        "                 WHERE corpus_id = %s)"
        " WHERE corpus_id = %s",
        (corpus_id, corpus_id, corpus_id),
    )


def document(cx, corpus_id: str, doc_key: str, n_fragments: int = 1) -> list[int]:
    cx.execute(
        "INSERT INTO bench.corpus_docs (corpus_id, doc_key, source, doc_text)"
        " VALUES (%s, %s, 'ms_review', 'Une critique.')",
        (corpus_id, doc_key),
    )
    return [
        cx.execute(
            "INSERT INTO bench.corpus_chunks (corpus_id, doc_key, chunk_index,"
            " chunk_text) VALUES (%s, %s, %s, 'Un fragment.') RETURNING chunk_id",
            (corpus_id, doc_key, i),
        ).fetchone()[0]
        for i in range(n_fragments)
    ]


def vecteur(cx, eid: int, chunk_id: int, corpus_id: str, modele=GEMMA) -> None:
    cx.execute(
        f"INSERT INTO {TABLE[modele]} (encodage_id, chunk_id, corpus_id, embedding)"  # nosec B608
        " VALUES (%s, %s, %s, %s)",
        (eid, chunk_id, corpus_id, unitaire(modele)),
    )


def promouvoir(cx, decision, eid, precedent, run_id=None) -> None:
    cx.execute(
        "INSERT INTO bench.promotions (decision, encodage_id, encodage_precedent_id,"
        " run_id, motif, decide_par) VALUES (%s, %s, %s, %s, 'essai', 'essai')",
        (decision, eid, precedent, run_id),
    )


def en_service(cx) -> list[tuple]:
    return cx.execute(
        "SELECT encodage_id, corpus_id FROM bench.v_encodage_en_service"
    ).fetchall()


@pytest.fixture
def base_migree(base):
    migrate.commande_up(UP)
    return base


# --------------------------------------------------------------------------- #
#  La migration elle-même
# --------------------------------------------------------------------------- #


def test_base_neuve_v1_inscrit_ouvert_et_journal_vide(base_migree):
    with psycopg.connect(base_migree) as cx:
        assert cx.execute(
            "SELECT c.corpus_id, s.name, s.chunk_size, s.chunk_overlap, c.clos_le"
            " FROM bench.corpus c JOIN bench.chunking_strategies s USING (chunking_id)"
        ).fetchall() == [("v1", "char_1200_overlap_200", 1200, 200, None)]
        assert cx.execute("SELECT count(*) FROM bench.promotions").fetchone() == (0,)
        assert en_service(cx) == []


@pytest.fixture
def base_avec_corpus(base):
    """L'état d'`apimanga` avant 021, en miniature : un corpus, deux encodages
    terminés et leurs vecteurs, une mesure."""
    migrate.commande_up(JUSQUA_020)
    with psycopg.connect(base) as cx:
        cx.execute(
            "INSERT INTO bench.corpus_docs (doc_key, source, doc_text) VALUES"
            " ('ms_review:1', 'ms_review', 'a'), ('kitsu:2', 'kitsu_synopsis', 'b')"
        )
        cx.execute(
            "INSERT INTO bench.corpus_chunks (chunk_id, doc_key, chunk_index,"
            " chunk_text) VALUES (10, 'ms_review:1', 0, 'a'),"
            " (11, 'ms_review:1', 1, 'a2'), (12, 'kitsu:2', 0, 'b')"
        )
        for modele in (BGE, GEMMA):
            eid = cx.execute(
                "INSERT INTO bench.encodages (modele, revision, dimension,"
                " precision_calcul, prefixe_document, prefixe_requete, outil,"
                " outil_version, image, image_digest, taille_lot, nb_fragments,"
                " termine_le) VALUES (%s, %s, %s, 'float32', '', '',"
                " 'text-embeddings-inference', '1.9.4', 'img', %s, 16, 3, now())"
                " RETURNING encodage_id",
                (modele, REVISION[modele], DIMENSION[modele], DIGEST),
            ).fetchone()[0]
            for c in (10, 11, 12):
                cx.execute(
                    f"INSERT INTO {TABLE[modele]} (chunk_id, encodage_id, embedding)"  # nosec B608
                    " VALUES (%s, %s, %s)",
                    (c, eid, unitaire(modele)),
                )
        cx.execute(
            "INSERT INTO bench.eval_jeux VALUES ('v1', %s, now(), 'essai')", ("c" * 64,)
        )
        cx.execute(
            "INSERT INTO bench.eval_mesures (run_id, jeu_version, portee, perimetre,"
            " metrique, k, valeur, n_questions) VALUES"
            " ('00000000-0000-0000-0000-000000000008', 'v1', 'global', 'toutes',"
            " 'hit_rate', 10, 0.5254, 59)"
        )
    return base


def empreintes(dsn: str) -> dict:
    with psycopg.connect(dsn) as cx:
        cx.execute("SET TimeZone = 'UTC'; SET extra_float_digits = 3")
        return {nom: cx.execute(sql).fetchone()[0] for nom, sql in EMPREINTES.items()}


def fichiers_des_tables(dsn: str) -> dict:
    tables = (
        "bench.corpus_docs",
        "bench.corpus_chunks",
        "bench.vecteurs_bge_m3",
        "bench.vecteurs_embeddinggemma",
        "bench.encodages",
        "bench.eval_mesures",
        "bench.qrels",
        "bench.retrieval_results",
    )
    with psycopg.connect(dsn) as cx:
        return {
            t: cx.execute("SELECT pg_relation_filenode(%s)", (t,)).fetchone()[0]
            for t in tables
        }


def test_021_ne_change_ni_ne_reecrit_aucune_ligne(base_avec_corpus):
    avant, fichiers = (
        empreintes(base_avec_corpus),
        fichiers_des_tables(base_avec_corpus),
    )
    assert migrate.commande_up(UP) == 0
    assert empreintes(base_avec_corpus) == avant
    # Même fichier de données : aucune table réécrite (défaut constant).
    assert fichiers_des_tables(base_avec_corpus) == fichiers


def test_021_clot_le_v1_et_met_gemma_en_service(base_avec_corpus):
    migrate.commande_up(UP)
    with psycopg.connect(base_avec_corpus) as cx:
        assert cx.execute(
            "SELECT corpus_id, nb_documents, nb_fragments, clos_le IS NOT NULL"
            " FROM bench.corpus"
        ).fetchall() == [("v1", 2, 3, True)]
        ((eid, corpus_id, modele, decision, decide_par, precedent, motif),) = (
            cx.execute(
                "SELECT p.encodage_id, e.corpus_id, e.modele, p.decision, p.decide_par,"
                " p.encodage_precedent_id, p.motif"
                " FROM bench.promotions p JOIN bench.encodages e USING (encodage_id)"
            ).fetchall()
        )
        assert (corpus_id, modele, decision, decide_par, precedent) == (
            "v1",
            GEMMA,
            "promu",
            "Max",
            None,
        )
        assert motif.startswith("Reprise de la décision de Max du 7 octobre 2026")
        assert en_service(cx) == [(eid, "v1")]
        assert cx.execute("SELECT count(*) FROM bench.eval_runs").fetchone() == (0,)
        assert cx.execute("SELECT count(*) FROM bench.eval_mesures").fetchone() == (1,)


# --------------------------------------------------------------------------- #
#  Plusieurs corpus, plusieurs encodages d'un même modèle
# --------------------------------------------------------------------------- #


def test_deux_encodages_du_meme_modele_coexistent(base_migree):
    with psycopg.connect(base_migree) as cx:
        c1 = document(cx, "v1", "ms_review:1")[0]
        corpus(cx, "v2")
        c2 = document(cx, "v2", "ms_review:1")[0]  # même doc_key, autre version
        e1, e2 = encodage(cx), encodage(cx, corpus="v2")  # mêmes paramètres
        vecteur(cx, e1, c1, "v1")
        vecteur(cx, e2, c2, "v2")
        par_encodage = cx.execute(
            "SELECT encodage_id, corpus_id, count(*) FROM bench.vecteurs_embeddinggemma"
            " GROUP BY 1, 2 ORDER BY 1"
        ).fetchall()
        assert par_encodage == [(e1, "v1", 1), (e2, "v2", 1)]
        promouvoir(cx, "promu", e1, None)
        promouvoir(cx, "promu", e2, e1)
        assert en_service(cx) == [(e2, "v2")], "un seul encodage en service"


def test_memes_parametres_sur_le_meme_corpus_refuses(base_migree):
    with psycopg.connect(base_migree) as cx:
        encodage(cx)
        with pytest.raises(psycopg.errors.UniqueViolation):
            encodage(cx)


@pytest.mark.parametrize(
    "cas",
    ["encodage v2, fragment v1", "encodage v2 déclaré v1", "fragment v2 déclaré v1"],
)
def test_un_vecteur_ne_mele_jamais_deux_corpus(base_migree, cas):
    with psycopg.connect(base_migree) as cx:
        c1 = document(cx, "v1", "ms_review:1")[0]
        corpus(cx, "v2")
        c2 = document(cx, "v2", "ms_review:1")[0]
        e2 = encodage(cx, corpus="v2")
        eid, chunk_id, corpus_id = {
            "encodage v2, fragment v1": (e2, c1, "v2"),
            "encodage v2 déclaré v1": (e2, c1, "v1"),
            "fragment v2 déclaré v1": (e2, c2, "v1"),
        }[cas]
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            vecteur(cx, eid, chunk_id, corpus_id)


def test_fragment_d_un_document_d_un_autre_corpus_refuse(base_migree):
    with psycopg.connect(base_migree) as cx:
        document(cx, "v1", "ms_review:1")
        corpus(cx, "v2")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cx.execute(
                "INSERT INTO bench.corpus_chunks (corpus_id, doc_key, chunk_index,"
                " chunk_text) VALUES ('v2', 'ms_review:1', 0, 'x')"
            )


# --------------------------------------------------------------------------- #
#  Le journal des promotions
# --------------------------------------------------------------------------- #


@pytest.fixture
def journal(base_migree):
    """v1 et v2 encodés ; l'encodage du v1 en service."""
    with psycopg.connect(base_migree) as cx:
        e1 = encodage(cx)
        corpus(cx, "v2")
        e2 = encodage(cx, corpus="v2")
        bge = encodage(cx, modele=BGE)
        promouvoir(cx, "promu", e1, None)
    return base_migree, e1, e2, bge


@pytest.mark.parametrize(
    "requete",
    [
        "UPDATE bench.promotions SET motif = 'autre'",
        "DELETE FROM bench.promotions",
        "TRUNCATE bench.promotions",
    ],
)
def test_une_ligne_du_journal_ne_se_modifie_ni_ne_se_supprime(journal, requete):
    dsn, *_ = journal
    with psycopg.connect(dsn) as cx:
        with pytest.raises(psycopg.errors.RaiseException, match="on ajoute une ligne"):
            cx.execute(requete)


def test_promotion_puis_retour_arriere(journal):
    dsn, e1, e2, _ = journal
    with psycopg.connect(dsn) as cx:
        run = cx.execute(
            "INSERT INTO bench.eval_runs (run_id, corpus_id, encodage_id)"
            " VALUES (gen_random_uuid(), 'v2', %s) RETURNING run_id",
            (e2,),
        ).fetchone()[0]
        promouvoir(cx, "promu", e2, e1, run)
        assert en_service(cx) == [(e2, "v2")]
        promouvoir(cx, "retour_arriere", e1, e2)
        assert en_service(cx) == [(e1, "v1")]
        promouvoir(cx, "refuse", e2, e1)
        assert en_service(cx) == [(e1, "v1")], "un refus ne change rien"
        assert cx.execute("SELECT count(*) FROM bench.promotions").fetchone() == (4,)


@pytest.mark.parametrize(
    "cas, motif",
    [
        ("précédent faux", "l'encodage en service est"),
        ("non terminé", "non terminé"),
        ("déjà en service", "déjà en service"),
        ("retour vers un encodage jamais en service", "jamais été en service"),
    ],
)
def test_une_decision_incoherente_est_refusee(journal, cas, motif):
    dsn, e1, e2, bge = journal
    with psycopg.connect(dsn) as cx:
        if cas == "non terminé":
            corpus(cx, "v3")
            e3 = encodage(cx, corpus="v3", termine=False)
        decision, eid, precedent = {
            "précédent faux": ("promu", e2, None),
            "non terminé": ("promu", e3 if cas == "non terminé" else None, e1),
            "déjà en service": ("promu", e1, e1),
            "retour vers un encodage jamais en service": ("retour_arriere", bge, e1),
        }[cas]
        with pytest.raises(psycopg.errors.RaiseException, match=motif):
            promouvoir(cx, decision, eid, precedent)


@pytest.mark.parametrize(
    "requete", ["UPDATE bench.eval_runs SET note = 'x'", "DELETE FROM bench.eval_runs"]
)
def test_un_run_inscrit_ne_se_modifie_pas(base_migree, requete):
    with psycopg.connect(base_migree) as cx:
        cx.execute(
            "INSERT INTO bench.eval_runs (run_id, corpus_id)"
            " VALUES (gen_random_uuid(), 'v1')"
        )
        with pytest.raises(psycopg.errors.RaiseException, match="on ajoute une ligne"):
            cx.execute(requete)


# --------------------------------------------------------------------------- #
#  Un corpus clos ne bouge plus ; l'extension explicite
# --------------------------------------------------------------------------- #


@pytest.fixture
def v1_clos_encode(base_migree):
    """Le v1 construit, encodé, en service, puis clos — l'état d'`apimanga`."""
    with psycopg.connect(base_migree) as cx:
        chunks = document(cx, "v1", "ms_review:1", n_fragments=2)
        eid = encodage(cx)
        for c in chunks:
            vecteur(cx, eid, c, "v1")
        promouvoir(cx, "promu", eid, None)
        clore(cx, "v1")
    return base_migree, eid, chunks


@pytest.mark.parametrize(
    "requete",
    [
        "INSERT INTO bench.corpus_docs (doc_key, source, doc_text)"
        " VALUES ('ms_review:9', 'ms_review', 'x')",
        "INSERT INTO bench.corpus_chunks (corpus_id, doc_key, chunk_index, chunk_text)"
        " VALUES ('v1', 'ms_review:1', 7, 'x')",
        "UPDATE bench.corpus_docs SET title = 'x' WHERE doc_key = 'ms_review:1'",
        "UPDATE bench.corpus_chunks SET chunk_text = 'x' WHERE chunk_index = 0",
        "DELETE FROM bench.corpus_chunks WHERE chunk_index = 1",
        "DELETE FROM bench.corpus_docs WHERE doc_key = 'ms_review:1'",
    ],
)
def test_un_corpus_clos_refuse_toute_ecriture(v1_clos_encode, requete):
    dsn, *_ = v1_clos_encode
    with psycopg.connect(dsn) as cx:
        with pytest.raises(
            psycopg.errors.RaiseException, match="le corpus v1 est clos"
        ):
            cx.execute(requete)


def test_ajouter_un_document_a_un_corpus_encode_et_en_service(v1_clos_encode):
    """Exigence 6 : document, fragments et vecteurs ajoutés à un corpus déjà
    encodé, sans violer une contrainte — par l'extension nommée."""
    dsn, eid, _ = v1_clos_encode
    with psycopg.connect(dsn) as cx:
        cx.execute("SELECT bench.autoriser_extension('v1')")
        for c in document(cx, "v1", "libraire:1", n_fragments=2):
            vecteur(cx, eid, c, "v1")
    with psycopg.connect(dsn) as cx:
        assert cx.execute(
            "SELECT count(*) FROM bench.vecteurs_embeddinggemma v"
            " JOIN bench.corpus_chunks k USING (chunk_id)"
            " WHERE v.encodage_id = %s AND k.doc_key = 'libraire:1'",
            (eid,),
        ).fetchone() == (2,)
        assert en_service(cx) == [(eid, "v1")]


def test_ajouter_a_un_corpus_ouvert_deja_encode(base_migree):
    with psycopg.connect(base_migree) as cx:
        eid = encodage(cx)
        vecteur(cx, eid, document(cx, "v1", "ms_review:1")[0], "v1")
        vecteur(cx, eid, document(cx, "v1", "libraire:1")[0], "v1")


def test_l_extension_n_ouvre_que_l_insertion_et_pour_une_transaction(v1_clos_encode):
    dsn, *_ = v1_clos_encode
    with psycopg.connect(dsn) as cx:
        cx.execute("SELECT bench.autoriser_extension('v1')")
        document(cx, "v1", "libraire:1")
        with pytest.raises(psycopg.errors.RaiseException, match="UPDATE refusé"):
            cx.execute(
                "UPDATE bench.corpus_docs SET title = 'x' WHERE doc_key = 'libraire:1'"
            )
    with psycopg.connect(dsn) as cx:
        with pytest.raises(psycopg.errors.RaiseException, match="INSERT refusé"):
            document(cx, "v1", "libraire:2")


@pytest.mark.parametrize("corpus_id", ["v1", "v9"])
def test_extension_d_un_corpus_ouvert_ou_absent_refusee(base_migree, corpus_id):
    with psycopg.connect(base_migree) as cx:
        with pytest.raises(psycopg.errors.RaiseException, match="absent ou ouvert"):
            cx.execute("SELECT bench.autoriser_extension(%s)", (corpus_id,))


def test_encoder_un_corpus_clos_reste_possible(base_migree):
    """Construire, clore, encoder : les vecteurs ne sont pas concernés."""
    with psycopg.connect(base_migree) as cx:
        corpus(cx, "v2")
        c = document(cx, "v2", "ms_review:1")[0]
        clore(cx, "v2")
        vecteur(cx, encodage(cx, corpus="v2"), c, "v2")


def test_un_fragment_vectorise_d_un_corpus_ouvert_ne_se_supprime_pas(base_migree):
    """La protection de 020 tient, la clé étrangère passée en composite."""
    with psycopg.connect(base_migree) as cx:
        c = document(cx, "v1", "ms_review:1")[0]
        vecteur(cx, encodage(cx), c, "v1")
    for requete in (
        "DELETE FROM bench.corpus_chunks",
        "DELETE FROM bench.corpus_docs",
    ):
        with psycopg.connect(base_migree) as cx:
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                cx.execute(requete)
