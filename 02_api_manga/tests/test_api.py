from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from psycopg import OperationalError

from app.main import (
    decode_cursor,
    encode_cursor,
    get_kitsu_core,
    health,
    live,
    rag_doc,
    rag_export,
    rag_export_composition,
    rag_preview,
    search,
)
from tests.faux_pool import FakePool


def test_live_does_not_require_database() -> None:
    response = live()

    assert response.model_dump() == {"status": "ok", "db": "not_checked"}


def test_health_reports_database_ready() -> None:
    response = health(FakePool([(1,)]))

    assert response.model_dump() == {"status": "ok", "db": "ok"}


def test_health_hides_database_error_details() -> None:
    pool = FakePool(error=OperationalError("secret hostname"))

    response = health(pool)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload == {"status": "degraded", "db": "error"}
    assert "secret" not in response.body.decode()


def test_kitsu_uses_a_bound_parameter() -> None:
    row = (38, "one-piece", "One Piece", "Pirates", 8.7, 12, 3)
    pool = FakePool([row])

    response = get_kitsu_core(pool, 38)

    assert response.title_canonical == "One Piece"
    assert pool.cursor.executions[0][1] == (38,)


def test_kitsu_returns_404_for_unknown_id() -> None:
    with pytest.raises(HTTPException) as error:
        get_kitsu_core(FakePool([None]), 999999)

    assert error.value.status_code == 404


def test_database_errors_become_a_neutral_503() -> None:
    with pytest.raises(HTTPException) as error:
        get_kitsu_core(FakePool(error=OperationalError("secret hostname")), 38)

    assert error.value.status_code == 503
    assert error.value.detail == "database unavailable"


def test_rag_preview_accepts_a_null_boost() -> None:
    rows = [("kitsu:38", "kitsu", None, "Titres: One Piece")]

    response = rag_preview(FakePool([(1,), rows]), limit=1, offset=0)

    assert response.items[0].boost_score == 0.0


def test_rag_preview_conserve_la_troncature_et_le_tri_par_pertinence() -> None:
    """L'aperçu garde le contrat de l'ancien `/rag/export` : c'est son objet."""
    rows = [("kitsu:38", "kitsu", 120.0, "Titres: One Piece")]
    pool = FakePool([(1,), rows])

    rag_preview(pool, limit=1, offset=0)

    sql = pool.cursor.executions[1][0]
    assert "left(doc_text, 500)" in sql
    assert "ORDER BY boost_score DESC NULLS LAST, doc_key" in sql


def test_rag_export_lit_le_texte_integral_sans_troncature() -> None:
    """Contrat décisif de l'export : aucune fonction de coupe dans le SQL."""
    rows = [("kitsu:38", "kitsu", 120.0, "x" * 10_000, {"kitsu_id": 38})]
    pool = FakePool([rows])

    response = rag_export(pool, limit=1, cursor=None)

    sql = pool.cursor.executions[0][0]
    assert "left(" not in sql
    assert "doc_text" in sql
    assert response.items[0].doc_text == "x" * 10_000


def test_rag_export_ordonne_en_collation_c() -> None:
    """Le prédicat et le tri portent la MÊME collation, sinon la borne dérive."""
    pool = FakePool([[]])

    rag_export(pool, limit=10, cursor=encode_cursor("kitsu:38"))

    sql, params = pool.cursor.executions[0]
    assert 'WHERE doc_key COLLATE "C" > %s' in sql
    assert 'ORDER BY doc_key COLLATE "C"' in sql
    # Le curseur est décodé puis LIÉ, jamais interpolé dans le SQL.
    assert params == ("kitsu:38", 10)
    assert "kitsu:38" not in sql


def test_rag_export_n_expose_plus_offset() -> None:
    """`offset` est retiré, pas déprécié : un export plafonné n'est pas un export."""
    import inspect

    assert "offset" not in inspect.signature(rag_export).parameters


def test_rag_export_ferme_le_parcours_sur_une_page_incomplete() -> None:
    rows = [("kitsu:38", "kitsu", 1.0, "texte", {})]

    response = rag_export(FakePool([rows]), limit=10, cursor=None)

    assert response.next_cursor is None


def test_rag_export_propose_un_curseur_sur_une_page_pleine() -> None:
    rows = [
        ("kitsu:38", "kitsu", 1.0, "texte", {}),
        ("kitsu:39", "kitsu", 1.0, "texte", {}),
    ]

    response = rag_export(FakePool([rows]), limit=2, cursor=None)

    assert response.next_cursor is not None
    assert decode_cursor(response.next_cursor) == "kitsu:39"


def test_le_curseur_fait_un_aller_retour_fidele() -> None:
    for doc_key in ("kitsu:1", "ms_review:6374", "ms_hybrid:8514"):
        assert decode_cursor(encode_cursor(doc_key)) == doc_key


@pytest.mark.parametrize(
    ("curseur", "cas"),
    [
        ("!!!pas-du-base64!!!", "hors alphabet base64"),
        ("YWJ", "tronqué : longueur non multiple de 4"),
        ("////", "base64 valide mais octets non décodables en UTF-8"),
        ("", "vide"),
        ("é" * 4, "non ASCII"),
        (encode_cursor("x" * 256), "clé plus longue que doc_key ne peut l'être"),
    ],
)
def test_un_curseur_illisible_donne_422_jamais_500(curseur: str, cas: str) -> None:
    with pytest.raises(HTTPException) as error:
        decode_cursor(curseur)

    assert error.value.status_code == 422, cas


def test_composition_totalise_ce_qu_elle_detaille() -> None:
    """Un seul GROUP BY : le total ne peut pas dater d'un autre instant."""
    rows = [("kitsu_synopsis", 43085), ("ms_hybrid", 5608), ("ms_review", 3187)]
    pool = FakePool([rows])

    response = rag_export_composition(pool)

    assert response.total == 51880
    assert response.total == sum(entry.documents for entry in response.by_source)
    assert [entry.source for entry in response.by_source] == [
        "kitsu_synopsis",
        "ms_hybrid",
        "ms_review",
    ]
    assert response.measured_at.tzinfo is not None


def test_rag_document_returns_metadata() -> None:
    row = (
        "kitsu:38",
        "kitsu",
        3.0,
        "Titres: One Piece",
        {"kitsu_id": 38},
    )

    response = rag_doc(FakePool([row]), "kitsu:38")

    assert response.metadata == {"kitsu_id": 38}


def test_search_strips_query_and_returns_results() -> None:
    rows = [("kitsu:38", "kitsu", 3.0, 0.8, "Titres: One Piece")]
    pool = FakePool([(1,), rows])

    response = search(pool, " one piece ", limit=10, offset=0)

    assert response.query == "one piece"
    assert pool.cursor.executions[0][1] == ("one piece",)


def test_search_rejects_whitespace_only_query() -> None:
    with pytest.raises(HTTPException) as error:
        search(FakePool(), "  ", limit=10, offset=0)

    assert error.value.status_code == 422
