"""Double de test du pool psycopg, partagé par les suites du module.

Il rejoue une liste de résultats dans l'ordre des `execute` et conserve les
requêtes vues : les tests peuvent ainsi affirmer non seulement CE QUI est
renvoyé, mais AVEC QUEL SQL et QUELS paramètres liés.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


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
