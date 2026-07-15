"""Création et accès au pool PostgreSQL."""

from __future__ import annotations

from fastapi import HTTPException, Request, status
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

from .settings import Settings


def build_conninfo(settings: Settings) -> str:
    """Construit un DSN sûr, y compris avec des caractères spéciaux."""
    return make_conninfo(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        connect_timeout=settings.db_connect_timeout,
        application_name="api-manga",
    )


def create_pool(settings: Settings) -> ConnectionPool:
    """Crée un pool fermé ; le lifespan FastAPI gère son ouverture/sa fermeture."""
    return ConnectionPool(
        conninfo=build_conninfo(settings),
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        timeout=settings.db_pool_timeout,
        name="api-manga",
        open=False,
    )


def get_pool(request: Request) -> ConnectionPool:
    """Expose le pool aux routes, avec une erreur propre avant initialisation."""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        )
    return pool
