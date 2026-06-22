from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_env() -> None:
    load_dotenv(project_root() / ".env", override=False)


def resolve_path(user_path: str) -> Path:
    _load_env()
    path = Path(user_path).expanduser()
    if path.is_absolute():
        return path
    return (project_root() / path).resolve()
