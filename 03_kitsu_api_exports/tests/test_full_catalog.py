from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from api_manga.full_catalog import FullCatalogCollector, parse_relations
from api_manga.validate_fixtures import validate_file


def _manga(item_id: str, rating_rank: int) -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "manga",
        "attributes": {
            "slug": f"manga-{item_id}",
            "canonicalTitle": f"Manga {item_id}",
            "titles": {"en": f"Manga {item_id}"},
            "status": "finished",
            "synopsis": "Synopsis",
            "averageRating": "80.0",
            "ratingRank": rating_rank,
            "popularityRank": int(item_id),
            "posterImage": {"original": f"https://images/{item_id}.jpg"},
        },
        "relationships": {
            "categories": {"data": [{"type": "categories", "id": "1"}]},
            "genres": {"data": [{"type": "genres", "id": "2"}]},
        },
    }


class FakeFullClient:
    BASE_URL = "https://example.test/api"

    def __init__(self) -> None:
        self.request_count = 0

    def fetch_catalog_page(
        self, *, limit: int, offset: int, include: str
    ) -> dict[str, Any]:
        del limit, include
        self.request_count += 1
        if offset > 0:
            return {"data": [], "meta": {"count": 2}, "links": {}}
        return {
            "data": [_manga("1", 2), _manga("2", 1)],
            "included": [
                {"id": "1", "type": "categories", "attributes": {"title": "Action"}},
                {"id": "2", "type": "genres", "attributes": {"name": "Fantasy"}},
            ],
            "meta": {"count": 2},
            "links": {},
        }

    def fetch_relationship_page(
        self,
        manga_id: str,
        relationship: str,
        *,
        limit: int,
        offset: int,
        include: str | None,
    ) -> dict[str, Any]:
        del limit, offset, include
        self.request_count += 1
        if relationship == "mappings":
            return {
                "data": [
                    {
                        "id": f"mapping-{manga_id}",
                        "type": "mappings",
                        "attributes": {
                            "externalSite": "myanimelist/manga",
                            "externalId": manga_id,
                        },
                    }
                ],
                "links": {},
            }
        if relationship == "staff" and manga_id == "1":
            return {
                "data": [
                    {
                        "id": "staff-1",
                        "type": "mediaStaff",
                        "attributes": {"role": "Story & Art"},
                        "relationships": {
                            "person": {"data": {"type": "people", "id": "9"}}
                        },
                    }
                ],
                "included": [
                    {
                        "id": "9",
                        "type": "people",
                        "attributes": {"name": "Mangaka"},
                    }
                ],
                "links": {},
            }
        return {"data": [], "included": [], "links": {}}


class PagedRelationshipClient(FakeFullClient):
    def fetch_catalog_page(
        self, *, limit: int, offset: int, include: str
    ) -> dict[str, Any]:
        del limit, include
        self.request_count += 1
        return {
            "data": [_manga("1", 1)] if offset == 0 else [],
            "included": [],
            "meta": {"count": 1},
            "links": {},
        }

    def fetch_relationship_page(
        self,
        manga_id: str,
        relationship: str,
        *,
        limit: int,
        offset: int,
        include: str | None,
    ) -> dict[str, Any]:
        del manga_id, relationship, limit, include
        self.request_count += 1
        if offset == 0:
            return {
                "data": [
                    {"id": "chapter-1", "type": "chapters"},
                    {"id": "chapter-2", "type": "chapters"},
                ],
                "links": {"next": "page-2"},
            }
        return {
            "data": [{"id": "chapter-3", "type": "chapters"}],
            "links": {},
        }


class FullCatalogTests(unittest.TestCase):
    def test_complete_collection_is_resumable_and_builds_top_rated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            client = FakeFullClient()
            collector = FullCatalogCollector(client, run_dir, page_size=2)  # type: ignore[arg-type]

            collector.collect_catalog()
            collector.collect_relations(("mappings", "staff"))
            manifest = collector.finalize(("mappings", "staff"))

            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(manifest["catalog"]["items"], 2)
            top_rated = json.loads(
                (run_dir / "top_rated.json").read_text(encoding="utf-8")
            )
            self.assertEqual([item["id"] for item in top_rated["data"]], ["2", "1"])
            self.assertEqual(top_rated["data"][1]["authors"][0]["name"], "Mangaka")
            self.assertEqual(top_rated["data"][0]["tags"]["categories"], ["Action"])
            issues, count = validate_file(
                run_dir / "top_rated.json", strict=True, max_items=0
            )
            self.assertEqual(count, 2)
            self.assertFalse([issue for issue in issues if issue.level == "ERROR"])

            manga_lines_before = (run_dir / "manga.ndjson").read_text(encoding="utf-8")
            resumed_client = FakeFullClient()
            resumed = FullCatalogCollector(
                resumed_client,
                run_dir,
                page_size=2,  # type: ignore[arg-type]
            )
            resumed.collect_catalog()
            resumed.collect_relations(("mappings", "staff"))

            self.assertEqual(resumed_client.request_count, 0)
            self.assertEqual(
                (run_dir / "manga.ndjson").read_text(encoding="utf-8"),
                manga_lines_before,
            )

    def test_relationship_resume_continues_at_the_next_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            first_client = PagedRelationshipClient()
            collector = FullCatalogCollector(
                first_client,
                run_dir,
                page_size=2,  # type: ignore[arg-type]
            )
            collector.collect_catalog()
            collector.collect_relation("chapters", max_pages=1)

            first_state = json.loads(
                (run_dir / "state.json").read_text(encoding="utf-8")
            )
            # Pagination interrompue : la page 0 est écrite, mais la réponse
            # portait un lien `next` -> le manga n'est PAS terminé.
            chapters_state = first_state["relations"]["chapters"]
            self.assertEqual(chapters_state["pages"], 1)
            self.assertEqual(chapters_state["manga_completed"], 0)
            self.assertFalse(chapters_state["done"])
            # La reprise v2 est ensembliste : plus aucun curseur dans l'état.
            self.assertNotIn("current_offset", chapters_state)
            self.assertNotIn("next_manga_index", chapters_state)

            resumed_client = PagedRelationshipClient()
            resumed = FullCatalogCollector(
                resumed_client,
                run_dir,
                page_size=2,  # type: ignore[arg-type]
            )
            resumed.collect_relation("chapters")

            # Non-régression du défaut (b) : la page 0 déjà écrite est sautée
            # SANS requête ; seule la page manquante part au réseau.
            self.assertEqual(resumed_client.request_count, 1)
            self.assertEqual(resumed.pages_skipped, 1)

            records = [
                json.loads(line)
                for line in (run_dir / "relations/chapters.ndjson")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            chapter_ids = [
                chapter["id"] for record in records for chapter in record["data"]
            ]
            self.assertEqual(chapter_ids, ["chapter-1", "chapter-2", "chapter-3"])
            final_state = json.loads(
                (run_dir / "state.json").read_text(encoding="utf-8")
            )
            self.assertTrue(final_state["relations"]["chapters"]["done"])

    def test_parse_relations_supports_all_and_rejects_unknown_names(self) -> None:
        self.assertEqual(
            parse_relations("mappings,staff,mappings"), ("mappings", "staff")
        )
        self.assertIn("chapters", parse_relations("all"))
        with self.assertRaisesRegex(ValueError, "inconnues"):
            parse_relations("volumes")


if __name__ == "__main__":
    unittest.main()
