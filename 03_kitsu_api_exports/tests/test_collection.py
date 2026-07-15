from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from api_manga.service import MangaService
from api_manga.validate_fixtures import validate_file


def _raw_item(item_id: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "attributes": {
            "slug": f"manga-{item_id}",
            "canonicalTitle": f"Manga {item_id}",
            "titles": {},
            "status": "current",
            "synopsis": "Synopsis",
            "averageRating": "80.0",
            "ratingRank": int(item_id),
            "popularityRank": int(item_id),
        },
        "relationships": {},
    }


def _formatted_item(item_id: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "slug": f"manga-{item_id}",
        "titles": {"canonical": f"Manga {item_id}", "en": None, "ja": None},
        "status": "current",
        "synopsis": "Synopsis",
        "authors": [],
        "ratings": {"average": 8.0, "rank": int(item_id)},
        "popularity": {"rank": int(item_id)},
        "tags": {"categories": [], "genres": []},
    }


class FakeClient:
    def __init__(self, pages: dict[int, list[str]]) -> None:
        self.pages = pages
        self.offsets: list[int] = []

    def fetch_top_publishing_manga(
        self,
        *,
        limit: int,
        offset: int,
        include: str,
        sort: str,
    ) -> dict[str, Any]:
        del limit, include, sort
        self.offsets.append(offset)
        return {
            "data": [_raw_item(item_id) for item_id in self.pages.get(offset, [])],
            "included": [],
        }


class CollectionTests(unittest.TestCase):
    def test_ranked_collection_skips_a_repeated_page(self) -> None:
        client = FakeClient({0: ["1", "2"], 2: ["1", "2"], 4: ["3", "4"]})
        service = MangaService(client)  # type: ignore[arg-type]

        with patch.object(MangaService, "PAGE_SIZE", 2):
            payload = service.get_top_publishing(limit=4, include_authors=False)

        self.assertEqual([item["id"] for item in payload["data"]], ["1", "2", "3", "4"])
        self.assertEqual(client.offsets, [0, 2, 4])

    def test_ranked_collection_stops_if_pagination_never_advances(self) -> None:
        client = FakeClient(
            {
                0: ["1", "2"],
                2: ["1", "2"],
                4: ["1", "2"],
                6: ["1", "2"],
            }
        )
        service = MangaService(client)  # type: ignore[arg-type]

        with (
            patch.object(MangaService, "PAGE_SIZE", 2),
            self.assertRaisesRegex(RuntimeError, "Pagination Kitsu bloquée"),
        ):
            service.get_top_publishing(limit=4, include_authors=False)

    def test_validator_rejects_duplicate_ids(self) -> None:
        payload = {
            "meta": {
                "category": "top_publishing",
                "source": "kitsu",
                "endpoint": "manga",
                "fetched_at": "2026-07-14T00:00:00+00:00",
                "limit": 2,
                "offset": 0,
            },
            "data": [_formatted_item("1"), _formatted_item("1")],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = Path(temp_dir) / "duplicate.json"
            fixture.write_text(json.dumps(payload), encoding="utf-8")
            issues, _ = validate_file(fixture, strict=True, max_items=0)

        self.assertTrue(
            any(
                issue.level == "ERROR" and "Duplicate id" in issue.message
                for issue in issues
            )
        )


if __name__ == "__main__":
    unittest.main()
