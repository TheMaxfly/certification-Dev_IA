from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from psycopg import OperationalError

from app.main import (
    get_kitsu_core,
    health,
    live,
    rag_doc,
    rag_export,
    search,
)


class FakeCursor:
    def __init__(
        self,
        results: list[Any],
        error: Exception | None = None,
    ) -> None:
        self.results = results
        self.error = error
        self.current: Any = None
        self.executions: list[tuple[str, Any]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.executions.append((sql, params))
        if self.error is not None:
            raise self.error
        self.current = self.results.pop(0)

    def fetchone(self) -> Any:
        return self.current

    def fetchall(self) -> Any:
        return self.current


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor


class FakePool:
    def __init__(
        self,
        results: list[Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.cursor = FakeCursor(results or [], error)

    @contextmanager
    def connection(self) -> Iterator[FakeConnection]:
        yield FakeConnection(self.cursor)


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


def test_rag_export_accepts_a_null_boost() -> None:
    rows = [("kitsu:38", "kitsu", None, "Titres: One Piece")]

    response = rag_export(FakePool([(1,), rows]), limit=1, offset=0)

    assert response.items[0].boost_score == 0.0


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
