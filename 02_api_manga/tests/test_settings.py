from __future__ import annotations

import pytest
from psycopg.conninfo import conninfo_to_dict

from app.database import build_conninfo
from app.settings import Settings


def test_conninfo_escapes_special_characters() -> None:
    settings = Settings(db_user="manga user", db_password="quote' and space")

    parsed = conninfo_to_dict(build_conninfo(settings))

    assert parsed["user"] == "manga user"
    assert parsed["password"] == "quote' and space"


def test_settings_validate_pool_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "6")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "5")

    with pytest.raises(ValueError, match="DB_POOL_MIN_SIZE"):
        Settings.from_env()


def test_settings_allow_an_empty_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "0")

    assert Settings.from_env().db_pool_min_size == 0
