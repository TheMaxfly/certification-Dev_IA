"""Smoke test HTTP exécuté dans le Compose d'intégration."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote_plus
from urllib.request import urlopen

BASE_URL = "http://api:8000"


def get_json(path: str) -> dict[str, Any]:
    with urlopen(f"{BASE_URL}{path}", timeout=10) as response:  # noqa: S310
        assert response.status == 200
        return json.load(response)


def main() -> None:
    assert get_json("/live") == {"status": "ok", "db": "not_checked"}
    assert get_json("/health") == {"status": "ok", "db": "ok"}

    kitsu = get_json("/kitsu/38")
    assert kitsu["title_canonical"] == "One Piece"

    export = get_json("/rag/export?limit=1&offset=0")
    assert export["total"] == 1
    assert export["items"][0]["doc_key"] == "kitsu:38"
    assert export["items"][0]["boost_score"] == 4.25

    document = get_json("/rag/doc/kitsu:38")
    assert document["metadata"]["authors"][0]["name"] == "Eiichiro Oda"

    query = quote_plus("one piece")
    search = get_json(f"/search?q={query}&limit=5")
    assert search["total"] == 1
    assert search["items"][0]["doc_key"] == "kitsu:38"


if __name__ == "__main__":
    main()
