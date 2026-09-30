"""016 — le jeu d'évaluation : ce que la base refuse, sur base JETABLE.

Chaque règle validée au point A (E1–E9) doit être tenue par la base, pas par la
bonne volonté du chargeur : une question incohérente, une série inventée, une
version gelée qu'on retouche, une mesure qui en écrase une autre — tout cela
doit ÉCHOUER ici.
"""

from __future__ import annotations

from argparse import Namespace

import psycopg
import pytest
from conftest import migrate

UP = Namespace(commande="up", target=None)
EMPREINTE = "a" * 64
RUN = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def base_migree(base):
    migrate.commande_up(UP)
    with psycopg.connect(base) as cx:
        cx.execute(
            "INSERT INTO manga.ms_series_enriched (series_id, series_title)"
            " VALUES (1, 'Une'), (2, 'Deux'), (3, 'Trois')"
        )
    return base


def question(
    qid="Q001",
    mode="reconnaissance",
    famille="F1",
    issue="au_catalogue",
    origine="nouvelle",
    origine_query_id=None,
    note="teste le titre exact",
    texte="Berserk",
):
    return (qid, texte, mode, famille, issue, origine, origine_query_id, note)


def poser(dsn, questions, attendus, version="v1"):
    """Un jeu complet, en UNE transaction — comme le chargeur au gel."""
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO bench.eval_jeux VALUES (%s, %s, now(), 'aucune question"
            " écrite après exécution')",
            (version, EMPREINTE),
        )
        for q in questions:
            cx.execute(
                "INSERT INTO bench.eval_questions (jeu_version, question_id, texte,"
                " mode, famille, issue_attendue, origine, origine_query_id, note)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (version, *q),
            )
        for qid, series_id, grade in attendus:
            cx.execute(
                "INSERT INTO bench.eval_attendus VALUES (%s, %s, %s, %s)",
                (version, qid, series_id, grade),
            )


JEU_VALIDE = (
    [
        question("Q001"),
        question("Q002", mode="proposition", famille="F3"),
        question("Q003", mode="refus", famille="F7", issue="inconnue"),
        question("Q004", famille="F2", issue="reconnue_hors_catalogue"),
        question("Q005", famille="F4", origine="decembre", origine_query_id=7),
    ],
    [("Q001", 1, 2), ("Q002", 2, 2), ("Q002", 3, 1), ("Q005", 1, 2)],
)


def test_un_jeu_coherent_est_accepte(base_migree):
    poser(base_migree, *JEU_VALIDE)
    with psycopg.connect(base_migree) as cx:
        assert cx.execute("SELECT count(*) FROM bench.eval_questions").fetchone() == (
            5,
        )


def test_019_f11_par_reference_est_admise(base_migree):
    """019 : la onzième famille, « par référence », en proposition et reprise de
    décembre — et une mesure peut se ranger sous elle."""
    poser(
        base_migree,
        [
            question(
                "Q001",
                mode="proposition",
                famille="F11",
                origine="decembre",
                origine_query_id=13,
            )
        ],
        [("Q001", 2, 2)],
    )
    mesurer(base_migree, "hit_rate", 10, portee="famille:F11")


def test_aucun_lien_vers_le_corpus(base_migree):
    """Le jeu doit survivre à toute reconstruction du corpus."""
    with psycopg.connect(base_migree) as cx:
        liens = cx.execute(
            "SELECT conrelid::regclass::text FROM pg_catalog.pg_constraint"
            " WHERE contype = 'f' AND confrelid = 'bench.corpus_docs'::regclass"
            " AND conrelid::regclass::text LIKE 'bench.eval_%'"
        ).fetchall()
    assert liens == []


@pytest.mark.parametrize(
    ("q", "contrainte"),
    [
        (question(mode="reconnaissance", famille="F7", issue="inconnue"), "f7"),
        (question(mode="refus", famille="F7", issue="au_catalogue"), "f7"),
        (question(mode="refus", famille="F3", issue="inconnue"), "f7"),
        (
            question(mode="proposition", issue="reconnue_hors_catalogue"),
            "hors_catalogue",
        ),
        (question(origine="decembre"), "origine"),
        (question(origine_query_id=3), "origine"),
        (question(qid="F3-01"), "question_id"),
        (question(famille="F12"), "famille"),
        (question(note="  "), "note"),
    ],
)
def test_question_incoherente_refusee(base_migree, q, contrainte):
    with pytest.raises(psycopg.errors.CheckViolation, match=contrainte):
        poser(base_migree, [q], [])


@pytest.mark.parametrize(
    ("q", "attendus", "message"),
    [
        (question(), [], "sans série attendue"),
        (question(), [("Q001", 1, 2), ("Q001", 2, 2)], "1 exigée"),
        (question(mode="proposition", famille="F3"), [], "sans série attendue"),
        (
            question(mode="refus", famille="F7", issue="inconnue"),
            [("Q001", 1, 2)],
            "aucune admise",
        ),
        (
            question(famille="F2", issue="reconnue_hors_catalogue"),
            [("Q001", 1, 2)],
            "aucune admise",
        ),
    ],
)
def test_nombre_d_attendus_controle_a_la_validation(base_migree, q, attendus, message):
    with pytest.raises(psycopg.errors.RaiseException, match=message):
        poser(base_migree, [q], attendus)
    with psycopg.connect(base_migree) as cx:
        assert cx.execute("SELECT count(*) FROM bench.eval_jeux").fetchone() == (0,)


@pytest.mark.parametrize("famille", ["F2", "F5", "F6"])
def test_hors_catalogue_admis_dans_toute_famille(base_migree, famille):
    """017 : E7 était trop étroite — un romaji jamais édité est un cas F5."""
    poser(base_migree, [question(famille=famille, issue="reconnue_hors_catalogue")], [])


def test_serie_hors_catalogue_refusee(base_migree):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        poser(base_migree, [question()], [("Q001", 999, 2)])


def test_grade_hors_echelle_refuse(base_migree):
    with pytest.raises(psycopg.errors.CheckViolation):
        poser(base_migree, [question()], [("Q001", 1, 3)])


@pytest.mark.parametrize(
    "ordre",
    [
        "UPDATE bench.eval_questions SET texte = 'retouché'",
        "UPDATE bench.eval_attendus SET grade = 1",
        "DELETE FROM bench.eval_attendus",
        "DELETE FROM bench.eval_questions",
        "UPDATE bench.eval_jeux SET empreinte = repeat('b', 64)",
        "DELETE FROM bench.eval_jeux",
    ],
)
def test_jeu_gele_immuable(base_migree, ordre):
    poser(base_migree, *JEU_VALIDE)
    with (
        pytest.raises(psycopg.errors.RaiseException, match="immuable"),
        psycopg.connect(base_migree) as cx,
    ):
        cx.execute(ordre)


def test_une_nouvelle_version_reste_possible(base_migree):
    poser(base_migree, *JEU_VALIDE)
    poser(base_migree, *JEU_VALIDE, version="v2")


# --------------------------------------------------------------------------- #
#  Mesures
# --------------------------------------------------------------------------- #


def mesurer(dsn, metrique, k, portee="global", perimetre="toutes", valeur=0.5):
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO bench.eval_mesures (run_id, jeu_version, portee, perimetre,"
            " metrique, k, valeur, n_questions) VALUES (%s, 'v1', %s, %s, %s, %s, %s,"
            " 50)",
            (RUN, portee, perimetre, metrique, k, valeur),
        )


def test_une_mesure_en_double_echoue_au_lieu_d_ecraser(base_migree):
    poser(base_migree, *JEU_VALIDE)
    mesurer(base_migree, "ndcg", 10)
    mesurer(base_migree, "ndcg", 5)
    mesurer(base_migree, "ndcg", 10, perimetre="atteignables")
    with pytest.raises(psycopg.errors.UniqueViolation):
        mesurer(base_migree, "ndcg", 10, valeur=0.9)
    mesurer(base_migree, "taux_refus_correct", None, portee="mode:refus")
    with pytest.raises(psycopg.errors.UniqueViolation):
        mesurer(base_migree, "taux_refus_correct", None, portee="mode:refus")


@pytest.mark.parametrize(
    ("metrique", "k", "portee"),
    [
        ("mrr", None, "global"),
        ("taux_refus_correct", 10, "global"),
        ("recall_at_10", 10, "global"),
        ("hit_rate", 10, "famille:F12"),
        ("hit_rate", 10, "mode:tout"),
    ],
)
def test_mesure_mal_formee_refusee(base_migree, metrique, k, portee):
    poser(base_migree, *JEU_VALIDE)
    with pytest.raises(psycopg.errors.CheckViolation):
        mesurer(base_migree, metrique, k, portee=portee)


def test_mesure_immuable(base_migree):
    poser(base_migree, *JEU_VALIDE)
    mesurer(base_migree, "hit_rate", 1)
    with (
        pytest.raises(psycopg.errors.RaiseException, match="immuable"),
        psycopg.connect(base_migree) as cx,
    ):
        cx.execute("UPDATE bench.eval_mesures SET valeur = 1")
