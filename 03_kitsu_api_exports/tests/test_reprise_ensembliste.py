"""Tests de la reprise ensembliste (state v2) du collecteur Kitsu exhaustif.

Invariant vérifié ici : **la donnée écrite est la source de vérité**. Aucun
compteur ni curseur de `state.json` ne peut contredire les `{relation}.ndjson`.
Tous ces tests sont hors réseau : `_OfflineClient` fait échouer le test à la
moindre requête.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from api_manga.full_catalog import STATE_VERSION, FullCatalogCollector, parse_ids_file


class _OfflineClient:
    """Interdit tout réseau : une requête = échec explicite du test."""

    BASE_URL = "https://kitsu.io/api/edge"

    def __init__(self) -> None:
        self.request_count = 0

    def fetch_catalog_page(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("Requête catalogue interdite dans ce test")

    def fetch_relationship_page(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("Requête relation interdite dans ce test")


def _seed_catalog(run_dir: Path, manga_ids: list[str]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "manga.ndjson").open("w", encoding="utf-8") as stream:
        for position, manga_id in enumerate(manga_ids):
            stream.write(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "fetched_at": "2026-07-14T00:00:00+00:00",
                        "catalog_offset": 0,
                        "catalog_position": position,
                        "data": {"id": manga_id, "type": "manga", "attributes": {}},
                        "included": [],
                    }
                )
                + "\n"
            )


def _seed_page(
    run_dir: Path,
    relation: str,
    manga_id: str,
    offset: int,
    *,
    resource_ids: list[str],
    has_next: bool,
    http_status: int = 200,
) -> None:
    path = run_dir / "relations" / f"{relation}.ndjson"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "key": f"{relation}:{manga_id}:{offset}",
                    "fetched_at": "2026-07-14T00:00:00+00:00",
                    "manga_id": manga_id,
                    "relationship": relation,
                    "offset": offset,
                    "http_status": http_status,
                    "data": [{"id": rid, "type": relation} for rid in resource_ids],
                    "included": [],
                    "meta": {},
                    "links": {"next": "page-suivante"} if has_next else {},
                }
            )
            + "\n"
        )


def _collector(run_dir: Path) -> FullCatalogCollector:
    return FullCatalogCollector(_OfflineClient(), run_dir, page_size=2)  # type: ignore[arg-type]


class ReconstructionTests(unittest.TestCase):
    def test_nominal_toutes_les_pages_ecrites(self) -> None:
        """Dernière page écrite (sans `next`) -> le manga est done."""
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            _seed_catalog(run_dir, ["1", "2"])
            _seed_page(run_dir, "mappings", "1", 0, resource_ids=["a"], has_next=False)
            _seed_page(run_dir, "mappings", "2", 0, resource_ids=["b"], has_next=False)

            index = _collector(run_dir)._rebuild_relation_index("mappings")

            self.assertEqual(index["done_manga_ids"], {"1", "2"})
            self.assertEqual(index["pages"], 2)
            self.assertEqual(index["items"], 2)
            self.assertEqual(
                index["written_page_keys"], {"mappings:1:0", "mappings:2:0"}
            )

    def test_page_404_termine_le_manga(self) -> None:
        """Un 404 stocké a des `links` vides : le manga est terminé, pas à refaire."""
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            _seed_catalog(run_dir, ["1"])
            _seed_page(
                run_dir,
                "staff",
                "1",
                0,
                resource_ids=[],
                has_next=False,
                http_status=404,
            )

            index = _collector(run_dir)._rebuild_relation_index("staff")

            self.assertEqual(index["done_manga_ids"], {"1"})
            self.assertEqual(index["not_found"], 1)

    def test_partiel_pagination_interrompue(self) -> None:
        """Page avec `next` mais suite manquante -> manga NON done, ids mémorisés."""
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            _seed_catalog(run_dir, ["1", "2"])
            _seed_page(
                run_dir, "mappings", "1", 0, resource_ids=["a", "b"], has_next=True
            )
            _seed_page(run_dir, "mappings", "2", 0, resource_ids=["c"], has_next=False)

            index = _collector(run_dir)._rebuild_relation_index("mappings")

            self.assertEqual(index["done_manga_ids"], {"2"})
            self.assertNotIn("1", index["done_manga_ids"])
            # Les ressources déjà vues sont conservées : la déduplication
            # inter-pages reste correcte à la reprise, sans relire le réseau.
            self.assertEqual(index["seen_ids_by_manga"]["1"], {"a", "b"})

    def test_doublon_de_cle_compte_une_seule_fois(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            _seed_catalog(run_dir, ["1"])
            _seed_page(run_dir, "mappings", "1", 0, resource_ids=["a"], has_next=False)
            _seed_page(run_dir, "mappings", "1", 0, resource_ids=["a"], has_next=False)

            index = _collector(run_dir)._rebuild_relation_index("mappings")

            self.assertEqual(index["pages"], 1)
            self.assertEqual(index["items"], 1)


class MigrationLegacyTests(unittest.TestCase):
    def test_state_v1_est_migre_et_ses_cles_ignorees(self) -> None:
        """L'état v1 ment (done=True) ; la donnée dit non. La donnée gagne."""
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            _seed_catalog(run_dir, ["1", "2"])
            # Seul le manga "1" est réellement collecté.
            _seed_page(run_dir, "mappings", "1", 0, resource_ids=["a"], has_next=False)
            (run_dir / "state.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "source": "kitsu",
                        "api_base_url": "https://kitsu.io/api/edge",
                        "created_at": "2026-07-14T00:00:00+00:00",
                        "updated_at": "2026-07-14T00:00:00+00:00",
                        "status": "partial",
                        "request_count": 42,
                        "requested_relations": ["mappings"],
                        "catalog": {
                            "next_offset": 40,
                            "items": 2,
                            "reported_total": 2,
                            "pages": 1,
                            "stale_pages": 0,
                            "done": True,
                        },
                        "relations": {
                            "mappings": {
                                "next_manga_index": 999,
                                "current_manga_id": "2",
                                "current_offset": 20,
                                "current_seen_ids": ["zz"],
                                "current_stale_pages": 1,
                                "manga_completed": 999,
                                "pages": 999,
                                "items": 999,
                                "not_found": 0,
                                "done": True,
                            }
                        },
                        "last_error": None,
                    }
                ),
                encoding="utf-8",
            )

            collector = _collector(run_dir)
            state = collector._sync_relation_state("mappings")

            self.assertEqual(collector.state["state_version"], STATE_VERSION)
            self.assertTrue(collector.migration_notes)
            for legacy in (
                "next_manga_index",
                "current_manga_id",
                "current_offset",
                "current_seen_ids",
                "current_stale_pages",
            ):
                self.assertNotIn(legacy, state)
            # Les compteurs mensongers sont remplacés par la réalité des ndjson.
            self.assertEqual(state["manga_completed"], 1)
            self.assertEqual(state["pages"], 1)
            self.assertEqual(state["items"], 1)
            # "2" n'est pas collecté : la relation n'est PAS done.
            self.assertFalse(state["done"])


class CiblageTests(unittest.TestCase):
    def test_ids_inconnus_signales_sans_echec(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            _seed_catalog(run_dir, ["1", "2", "3"])
            collector = _collector(run_dir)

            report = collector.restrict_targets(["2", "404404", "1", "404404"])

            self.assertEqual(report["requested"], 3)  # dédoublonné
            self.assertEqual(report["known"], 2)
            self.assertEqual(report["unknown"], ["404404"])
            # Ordre du catalogue, pas ordre du fichier.
            self.assertEqual(collector.target_ids, ["1", "2"])

    def test_run_cible_ne_marque_jamais_la_relation_done(self) -> None:
        """Garde-fou : un run ciblé complet ne doit pas clore la relation.

        Sinon le run de fond suivant verrait `done=True` et sauterait tout le
        reste du catalogue.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            _seed_catalog(run_dir, ["1", "2", "3"])
            _seed_page(run_dir, "mappings", "1", 0, resource_ids=["a"], has_next=False)

            collector = _collector(run_dir)
            collector.restrict_targets(["1"])  # cible entièrement couverte
            state = collector._sync_relation_state("mappings")

            self.assertEqual(collector.target_ids, ["1"])
            self.assertFalse(state["done"])  # "2" et "3" restent à faire

    def test_ids_deja_couverts_sont_sautes_sans_requete(self) -> None:
        """Cible déjà collectée -> collect_relation ne doit émettre aucune requête."""
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            _seed_catalog(run_dir, ["1", "2"])
            _seed_page(run_dir, "mappings", "1", 0, resource_ids=["a"], has_next=False)

            collector = _collector(run_dir)
            collector.restrict_targets(["1"])
            # _OfflineClient lève si une requête part : l'absence d'exception
            # est la preuve du skip réseau.
            collector.collect_relation("mappings")

            self.assertEqual(collector.client.request_count, 0)

    def test_plan_dry_run_n_emet_aucune_requete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            _seed_catalog(run_dir, ["1", "2", "3"])
            _seed_page(run_dir, "mappings", "1", 0, resource_ids=["a"], has_next=False)

            collector = _collector(run_dir)
            plan = collector.plan(("mappings",), request_interval=0.5)

            info = plan["relations"]["mappings"]
            self.assertEqual(plan["catalog_items"], 3)
            self.assertEqual(info["targets_done"], 1)
            self.assertEqual(info["targets_remaining"], 2)
            self.assertEqual(info["estimated_requests_min"], 2)
            self.assertEqual(info["estimated_seconds_min"], 1.0)
            self.assertEqual(collector.client.request_count, 0)


class ParseIdsFileTests(unittest.TestCase):
    def test_commentaires_lignes_vides_et_doublons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ids.txt"
            path.write_text(
                "# export ms_kitsu_map\n"
                "1626\n"
                "\n"
                "  31626  # commentaire en fin de ligne\n"
                "1626\n"
                "   \n",
                encoding="utf-8",
            )

            self.assertEqual(parse_ids_file(path), ["1626", "31626"])


if __name__ == "__main__":
    unittest.main()
