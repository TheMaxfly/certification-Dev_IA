"""Configuration de l'API, chargée depuis les variables d'environnement."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} doit être un entier") from exc

    if value < minimum:
        raise ValueError(f"{name} doit être supérieur ou égal à {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Paramètres nécessaires à FastAPI et au pool PostgreSQL."""

    app_name: str = "API Manga"
    app_version: str = "0.3.0"
    app_env: str = "development"
    db_host: str = "host.docker.internal"
    db_port: int = 5432
    db_name: str = "apimanga"
    db_user: str = "postgres"
    db_password: str = ""
    db_connect_timeout: int = 5
    db_pool_timeout: int = 5
    db_pool_min_size: int = 1
    db_pool_max_size: int = 5

    @classmethod
    def from_env(cls) -> Settings:
        """Construit une configuration validée sans journaliser les secrets."""
        defaults = cls()
        settings = cls(
            app_name=os.getenv("APP_NAME", defaults.app_name),
            app_version=os.getenv("APP_VERSION", defaults.app_version),
            app_env=os.getenv("APP_ENV", defaults.app_env),
            db_host=os.getenv("DB_HOST", defaults.db_host),
            db_port=_env_int("DB_PORT", defaults.db_port),
            db_name=os.getenv("DB_NAME", defaults.db_name),
            db_user=os.getenv("DB_USER", defaults.db_user),
            db_password=os.getenv("DB_PASSWORD", defaults.db_password),
            db_connect_timeout=_env_int(
                "DB_CONNECT_TIMEOUT", defaults.db_connect_timeout
            ),
            db_pool_timeout=_env_int("DB_POOL_TIMEOUT", defaults.db_pool_timeout),
            db_pool_min_size=_env_int(
                "DB_POOL_MIN_SIZE", defaults.db_pool_min_size, minimum=0
            ),
            db_pool_max_size=_env_int("DB_POOL_MAX_SIZE", defaults.db_pool_max_size),
        )
        if settings.db_pool_min_size > settings.db_pool_max_size:
            raise ValueError(
                "DB_POOL_MIN_SIZE doit être inférieur ou égal à DB_POOL_MAX_SIZE"
            )
        return settings
