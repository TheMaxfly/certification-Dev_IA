"""Collecte exhaustive, reprenable et enrichie du catalogue manga Kitsu."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .client import KitsuClient, KitsuRequestError
from .exporter import _write_json_streamed

SCHEMA_VERSION = "1.0"
# Version du FORMAT DE REPRISE de state.json (distincte de SCHEMA_VERSION, qui
# versionne les enregistrements collectés).
#   v1 : reprise indexée (next_manga_index) — l'état faisait autorité.
#   v2 : reprise ensembliste (done_manga_ids), reconstruite depuis les ndjson —
#        la donnée écrite fait autorité. Les clés v1 sont ignorées puis purgées.
STATE_VERSION = 2
RELATION_INCLUDES: dict[str, str | None] = {
    "mappings": None,
    "staff": "person",
    "characters": "character",
    "chapters": None,
}
DEFAULT_RELATIONS = ("mappings", "staff", "characters")
MAX_STALE_PAGES = 3
# Clés de reprise v1 : purgées à la migration, jamais relues.
LEGACY_RELATION_KEYS = (
    "next_manga_index",
    "current_manga_id",
    "current_offset",
    "current_seen_ids",
    "current_stale_pages",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_id_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _append_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
        stream.flush()
    return count


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"JSONL invalide: {path}:{line_number}: {exc}"
                ) from exc
            if isinstance(value, dict):
                yield value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_ids_file(path: Path) -> list[str]:
    """Lit un fichier d'IDs : un kitsu_id par ligne, `#` et lignes vides ignorés."""
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            ids.append(entry)
    return list(dict.fromkeys(ids))


def parse_relations(value: str) -> tuple[str, ...]:
    requested = [part.strip() for part in value.split(",") if part.strip()]
    if requested == ["all"]:
        return tuple(RELATION_INCLUDES)
    unknown = sorted(set(requested) - set(RELATION_INCLUDES))
    if unknown:
        raise ValueError(f"Relations Kitsu inconnues: {', '.join(unknown)}")
    return tuple(dict.fromkeys(requested))


class FullCatalogCollector:
    """Collecte le catalogue puis ses relations dans des JSONL append-only."""

    def __init__(
        self,
        client: KitsuClient,
        run_dir: Path,
        *,
        page_size: int = 20,
    ) -> None:
        if not 1 <= page_size <= 20:
            raise ValueError("page_size doit être compris entre 1 et 20")

        self.client = client
        self.run_dir = run_dir
        self.page_size = page_size
        self.state_path = run_dir / "state.json"
        self.catalog_path = run_dir / "manga.ndjson"
        self.errors_path = run_dir / "errors.ndjson"
        self.manifest_path = run_dir / "manifest.json"
        self.top_rated_path = run_dir / "top_rated.json"
        self.relations_dir = run_dir / "relations"
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self.migration_notes: list[str] = []
        self.pages_skipped = 0
        self._relation_indexes: dict[str, dict[str, Any]] = {}

        self.state = self._load_or_create_state()
        self._initial_request_count = int(self.state.get("request_count") or 0)
        self.catalog_ids, self._catalog_seen = self._load_catalog_index()
        self.state["catalog"]["items"] = len(self.catalog_ids)
        # Périmètre de ce run. `catalog_ids` reste la vérité du catalogue complet ;
        # `target_ids` est ce que CE run parcourt (cf. restrict_targets).
        # None = tout le catalogue : surtout pas une copie ici, `collect_catalog`
        # remplit `catalog_ids` APRÈS l'init sur un run neuf.
        self._target_ids: list[str] | None = None

    def _migrate_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Migre un état v1 (reprise indexée) vers v2 (reprise ensembliste).

        Les clés v1 ne sont jamais relues : la reprise se reconstruit depuis les
        ndjson. On les purge pour qu'aucun code ne puisse s'y fier par accident.
        """
        found = int(state.get("state_version") or 1)
        if found >= STATE_VERSION:
            state["state_version"] = STATE_VERSION
            return state

        purged: list[str] = []
        for relation, relation_state in (state.get("relations") or {}).items():
            if not isinstance(relation_state, dict):
                continue
            for key in LEGACY_RELATION_KEYS:
                if key in relation_state:
                    purged.append(f"{relation}.{key}")
                    relation_state.pop(key)
        state["state_version"] = STATE_VERSION
        note = (
            f"Migration state v{found} -> v{STATE_VERSION} : reprise ensembliste "
            "reconstruite depuis les ndjson"
        )
        if purged:
            note += f" ; clés v1 ignorées puis purgées : {', '.join(sorted(purged))}"
        self.migration_notes.append(note)
        return state

    def _load_or_create_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("schema_version") != SCHEMA_VERSION:
                raise RuntimeError(
                    "Version de checkpoint incompatible: "
                    f"{state.get('schema_version')} != {SCHEMA_VERSION}"
                )
            return self._migrate_state(state)

        now = utc_now()
        state: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "state_version": STATE_VERSION,
            "source": "kitsu",
            "api_base_url": self.client.BASE_URL,
            "created_at": now,
            "updated_at": now,
            "status": "running",
            "request_count": 0,
            "requested_relations": [],
            "catalog": {
                "next_offset": 0,
                "items": 0,
                "reported_total": None,
                "pages": 0,
                "stale_pages": 0,
                "done": False,
            },
            "relations": {},
            "last_error": None,
        }
        _write_json_atomic(self.state_path, state)
        return state

    def _save_state(self) -> None:
        self.state["updated_at"] = utc_now()
        self.state["request_count"] = (
            self._initial_request_count + self.client.request_count
        )
        _write_json_atomic(self.state_path, self.state)

    def _load_catalog_index(self) -> tuple[list[str], set[str]]:
        ordered: list[str] = []
        seen: set[str] = set()
        for record in _iter_jsonl(self.catalog_path):
            data = record.get("data") or {}
            manga_id = data.get("id")
            if isinstance(manga_id, str) and manga_id not in seen:
                seen.add(manga_id)
                ordered.append(manga_id)
        return ordered, seen

    @staticmethod
    def _included_for_item(
        item: dict[str, Any], included: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        included_index = {
            (resource.get("type"), resource.get("id")): resource
            for resource in included
            if isinstance(resource, dict)
        }
        resolved: list[dict[str, Any]] = []
        relationships = item.get("relationships") or {}
        for relation_name in ("categories", "genres"):
            relation = relationships.get(relation_name) or {}
            references = relation.get("data") or []
            if isinstance(references, dict):
                references = [references]
            for reference in references:
                if not isinstance(reference, dict):
                    continue
                resource = included_index.get(
                    (reference.get("type"), reference.get("id"))
                )
                if resource is not None and resource not in resolved:
                    resolved.append(resource)
        return resolved

    def collect_catalog(self, *, max_pages: int = 0) -> None:
        catalog_state = self.state["catalog"]
        if catalog_state.get("done"):
            return

        pages_this_run = 0
        while True:
            offset = int(catalog_state.get("next_offset") or 0)
            try:
                payload = self.client.fetch_catalog_page(
                    limit=self.page_size,
                    offset=offset,
                    include="categories,genres",
                )
            except Exception as exc:
                self._record_failure("catalog", None, offset, exc)
                raise

            raw_items = payload.get("data") or []
            included = payload.get("included") or []
            meta = payload.get("meta") or {}
            reported_total = meta.get("count")
            if isinstance(reported_total, int):
                catalog_state["reported_total"] = reported_total

            fetched_at = utc_now()
            new_records: list[dict[str, Any]] = []
            for position, item in enumerate(raw_items):
                if not isinstance(item, dict):
                    continue
                manga_id = item.get("id")
                if not isinstance(manga_id, str) or manga_id in self._catalog_seen:
                    continue
                self._catalog_seen.add(manga_id)
                self.catalog_ids.append(manga_id)
                new_records.append(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "fetched_at": fetched_at,
                        "catalog_offset": offset,
                        "catalog_position": position,
                        "data": item,
                        "included": self._included_for_item(item, included),
                    }
                )

            _append_jsonl(self.catalog_path, new_records)
            if new_records:
                catalog_state["stale_pages"] = 0
            elif raw_items:
                catalog_state["stale_pages"] = (
                    int(catalog_state.get("stale_pages") or 0) + 1
                )
                if catalog_state["stale_pages"] >= MAX_STALE_PAGES:
                    error = RuntimeError(
                        "Pagination du catalogue Kitsu bloquée sans nouvel identifiant "
                        f"à partir de l'offset {offset}"
                    )
                    self._record_failure("catalog", None, offset, error)
                    raise error

            catalog_state["items"] = len(self.catalog_ids)
            catalog_state["pages"] = int(catalog_state.get("pages") or 0) + 1
            catalog_state["next_offset"] = offset + self.page_size
            pages_this_run += 1

            total = catalog_state.get("reported_total")
            if (
                not raw_items
                or len(raw_items) < self.page_size
                or (isinstance(total, int) and len(self.catalog_ids) >= total)
            ):
                catalog_state["done"] = True

            self._save_state()
            if catalog_state["done"]:
                return
            if max_pages > 0 and pages_this_run >= max_pages:
                return

    def _relation_state(self, relation: str) -> dict[str, Any]:
        return self._sync_relation_state(relation)

    @property
    def target_ids(self) -> list[str]:
        """Périmètre du run : le catalogue complet, sauf ciblage explicite."""
        return self.catalog_ids if self._target_ids is None else self._target_ids

    def restrict_targets(self, manga_ids: Sequence[str]) -> dict[str, Any]:
        """Restreint le périmètre du run à une liste d'IDs (ciblage).

        `catalog_ids` n'est pas touché : `done` continue de se juger contre le
        catalogue complet. Les IDs inconnus du catalogue sont signalés, pas
        fatals.
        """
        catalogue = set(self.catalog_ids)
        requested = list(
            dict.fromkeys(
                str(manga_id).strip() for manga_id in manga_ids if str(manga_id).strip()
            )
        )
        known = {manga_id for manga_id in requested if manga_id in catalogue}
        unknown = [manga_id for manga_id in requested if manga_id not in catalogue]
        # Ordre du catalogue : parcours déterministe, indépendant du fichier.
        self._target_ids = [
            manga_id for manga_id in self.catalog_ids if manga_id in known
        ]
        return {
            "requested": len(requested),
            "known": len(known),
            "unknown": unknown,
        }

    def plan(
        self, relations: Sequence[str], *, request_interval: float = 0.0
    ) -> dict[str, Any]:
        """Plan d'exécution SANS aucune requête réseau (dry-run)."""
        per_relation: dict[str, Any] = {}
        for relation in relations:
            if relation not in RELATION_INCLUDES:
                raise ValueError(f"Relation inconnue: {relation}")
            index = self._relation_index(relation)
            done_ids = index["done_manga_ids"]
            remaining = [
                manga_id for manga_id in self.target_ids if manga_id not in done_ids
            ]
            per_relation[relation] = {
                "targets": len(self.target_ids),
                "targets_done": len(self.target_ids) - len(remaining),
                "targets_remaining": len(remaining),
                "done_full_catalog": len(done_ids),
                "pages_written": index["pages"],
                # Plancher : au moins une requête par manga restant. La pagination
                # réelle peut en ajouter, elle n'est pas connue sans réseau.
                "estimated_requests_min": len(remaining),
                "estimated_seconds_min": round(len(remaining) * request_interval, 1),
            }
        return {
            "catalog_items": len(self.catalog_ids),
            "targets": len(self.target_ids),
            "relations": per_relation,
        }

    def _rebuild_relation_index(self, relation: str) -> dict[str, Any]:
        """Reconstruit l'état d'une relation depuis `{relation}.ndjson`.

        La donnée écrite est la source de vérité : aucun compteur de state.json
        n'est relu. Un manga est « done » quand sa DERNIÈRE page est écrite,
        c'est-à-dire une page stockée sans lien `next` (un 404 stocké, dont les
        `links` sont vides, termine donc le manga). Un manga interrompu en milieu
        de pagination reste absent de `done_manga_ids` : ses pages déjà écrites
        seront sautées via `written_page_keys`, seules les manquantes iront au
        réseau.
        """
        path = self.relations_dir / f"{relation}.ndjson"
        written: set[str] = set()
        done: set[str] = set()
        seen_by_manga: dict[str, set[str]] = {}
        items = 0
        not_found = 0

        for record in _iter_jsonl(path):
            key = record.get("key")
            manga_id = record.get("manga_id")
            if not isinstance(key, str) or not isinstance(manga_id, str):
                continue
            if key in written:  # doublon défensif : ne jamais compter deux fois
                continue
            written.add(key)

            data = record.get("data") or []
            items += len(data)
            bucket = seen_by_manga.setdefault(manga_id, set())
            for resource in data:
                if isinstance(resource, dict) and isinstance(resource.get("id"), str):
                    bucket.add(resource["id"])

            if record.get("http_status") == 404:
                not_found += 1
            if not (record.get("links") or {}).get("next"):
                done.add(manga_id)

        return {
            "written_page_keys": written,
            "done_manga_ids": done,
            "seen_ids_by_manga": seen_by_manga,
            "pages": len(written),
            "items": items,
            "not_found": not_found,
        }

    def _relation_index(self, relation: str) -> dict[str, Any]:
        if relation not in self._relation_indexes:
            self._relation_indexes[relation] = self._rebuild_relation_index(relation)
        return self._relation_indexes[relation]

    def _sync_relation_state(self, relation: str) -> dict[str, Any]:
        """Recopie l'index reconstruit dans state.json (état = miroir des données)."""
        index = self._relation_index(relation)
        relation_state = self.state["relations"].setdefault(relation, {})
        for key in LEGACY_RELATION_KEYS:
            relation_state.pop(key, None)
        relation_state["manga_completed"] = len(index["done_manga_ids"])
        relation_state["pages"] = index["pages"]
        relation_state["items"] = index["items"]
        relation_state["not_found"] = index["not_found"]
        # `done` se juge TOUJOURS contre le catalogue complet, jamais contre le
        # périmètre du run : sinon un run ciblé marquerait la relation terminée
        # et le run de fond suivant sauterait tout le reste.
        relation_state["done"] = bool(self.catalog_ids) and index[
            "done_manga_ids"
        ].issuperset(self.catalog_ids)
        return relation_state

    def _record_failure(
        self,
        phase: str,
        manga_id: str | None,
        offset: int,
        exc: Exception,
    ) -> None:
        error = {
            "occurred_at": utc_now(),
            "phase": phase,
            "manga_id": manga_id,
            "offset": offset,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        _append_jsonl(self.errors_path, [error])
        self.state["last_error"] = error
        self.state["status"] = "failed"
        self._save_state()

    def collect_relation(
        self,
        relation: str,
        *,
        max_manga: int = 0,
        max_pages: int = 0,
    ) -> None:
        if relation not in RELATION_INCLUDES:
            raise ValueError(f"Relation inconnue: {relation}")

        relation_state = self._sync_relation_state(relation)
        if relation_state.get("done"):
            return

        index = self._relation_index(relation)
        written_page_keys = index["written_page_keys"]
        done_manga_ids = index["done_manga_ids"]
        output_path = self.relations_dir / f"{relation}.ndjson"
        completed_this_run = 0
        pages_this_run = 0

        for manga_id in self.target_ids:
            if manga_id in done_manga_ids:
                continue

            # Un manga repris hérite des ressources déjà vues : la déduplication
            # inter-pages reste correcte sans relire le réseau.
            seen_resource_ids = set(index["seen_ids_by_manga"].get(manga_id, ()))
            offset = 0
            stale_pages = 0
            manga_done = False

            while not manga_done:
                page_key = f"{relation}:{manga_id}:{offset}"
                if page_key in written_page_keys:
                    # Page déjà collectée : AUCUNE requête. Un manga non « done »
                    # n'a que des pages avec `next`, donc on avance.
                    self.pages_skipped += 1
                    offset += self.page_size
                    continue

                try:
                    payload = self.client.fetch_relationship_page(
                        manga_id,
                        relation,
                        limit=self.page_size,
                        offset=offset,
                        include=RELATION_INCLUDES[relation],
                    )
                    http_status = 200
                except KitsuRequestError as exc:
                    if exc.status_code != 404:
                        self._record_failure(relation, manga_id, offset, exc)
                        raise
                    payload = {"data": [], "included": [], "links": {}}
                    http_status = 404
                except Exception as exc:
                    self._record_failure(relation, manga_id, offset, exc)
                    raise

                raw_data = payload.get("data") or []
                novel_data: list[dict[str, Any]] = []
                for resource in raw_data:
                    if not isinstance(resource, dict):
                        continue
                    resource_id = resource.get("id")
                    if isinstance(resource_id, str):
                        if resource_id in seen_resource_ids:
                            continue
                        seen_resource_ids.add(resource_id)
                    novel_data.append(resource)

                if raw_data and not novel_data:
                    stale_pages += 1
                    if stale_pages >= MAX_STALE_PAGES:
                        error = RuntimeError(
                            f"Pagination {relation} bloquée pour manga_id={manga_id} "
                            f"à l'offset {offset}"
                        )
                        self._record_failure(relation, manga_id, offset, error)
                        raise error
                else:
                    stale_pages = 0

                has_next = bool((payload.get("links") or {}).get("next"))
                record = {
                    "schema_version": SCHEMA_VERSION,
                    "key": page_key,
                    "fetched_at": utc_now(),
                    "manga_id": manga_id,
                    "relationship": relation,
                    "offset": offset,
                    "http_status": http_status,
                    "data": novel_data,
                    "included": payload.get("included") or [],
                    "meta": payload.get("meta") or {},
                    "links": payload.get("links") or {},
                }
                _append_jsonl(output_path, [record])
                written_page_keys.add(page_key)
                index["pages"] += 1
                index["items"] += len(novel_data)
                if http_status == 404:
                    index["not_found"] += 1
                index["seen_ids_by_manga"][manga_id] = seen_resource_ids
                pages_this_run += 1

                if raw_data and has_next:
                    offset += self.page_size
                else:
                    manga_done = True
                    done_manga_ids.add(manga_id)
                    completed_this_run += 1

                relation_state = self._sync_relation_state(relation)
                self._save_state()

                if relation_state.get("done"):
                    return
                if max_pages > 0 and pages_this_run >= max_pages:
                    return
                if manga_done and max_manga > 0 and completed_this_run >= max_manga:
                    return

        self._sync_relation_state(relation)
        self._save_state()

    def collect_relations(
        self,
        relations: Sequence[str],
        *,
        max_manga: int = 0,
        max_pages: int = 0,
    ) -> None:
        requested = list(self.state.get("requested_relations") or [])
        for relation in relations:
            if relation not in requested:
                requested.append(relation)
        self.state["requested_relations"] = requested
        self._save_state()

        for relation in relations:
            self.collect_relation(
                relation,
                max_manga=max_manga,
                max_pages=max_pages,
            )

    @staticmethod
    def _relationship_titles(record: dict[str, Any], relation: str) -> list[str]:
        data = record.get("data") or {}
        references = ((data.get("relationships") or {}).get(relation) or {}).get(
            "data"
        ) or []
        included_index = {
            (resource.get("type"), resource.get("id")): resource
            for resource in record.get("included") or []
            if isinstance(resource, dict)
        }
        titles: list[str] = []
        for reference in references:
            if not isinstance(reference, dict):
                continue
            resource = included_index.get((reference.get("type"), reference.get("id")))
            attributes = (resource or {}).get("attributes") or {}
            title = attributes.get("title") or attributes.get("name")
            if isinstance(title, str) and title.strip() and title not in titles:
                titles.append(title.strip())
        return titles

    def _load_authors(self) -> dict[str, list[dict[str, str | None]]]:
        authors: dict[str, list[dict[str, str | None]]] = {}
        path = self.relations_dir / "staff.ndjson"
        for page in _iter_jsonl(path):
            manga_id = page.get("manga_id")
            if not isinstance(manga_id, str):
                continue
            included_index = {
                (resource.get("type"), resource.get("id")): resource
                for resource in page.get("included") or []
                if isinstance(resource, dict)
            }
            output = authors.setdefault(manga_id, [])
            for staff in page.get("data") or []:
                if not isinstance(staff, dict):
                    continue
                person_ref = (
                    ((staff.get("relationships") or {}).get("person") or {}).get("data")
                ) or {}
                person = included_index.get(
                    (person_ref.get("type"), person_ref.get("id"))
                )
                person_attributes = (person or {}).get("attributes") or {}
                name = person_attributes.get("name")
                role = (staff.get("attributes") or {}).get("role")
                if not isinstance(name, str) or not name.strip():
                    continue
                author = {
                    "name": name.strip(),
                    "role": role.strip()
                    if isinstance(role, str) and role.strip()
                    else None,
                }
                if author not in output:
                    output.append(author)
        return authors

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            return int(value) if value is not None and str(value).strip() else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            return float(value) if value is not None and str(value).strip() else None
        except (TypeError, ValueError):
            return None

    def _normalized_manga(
        self,
        record: dict[str, Any],
        authors: dict[str, list[dict[str, str | None]]],
    ) -> dict[str, Any]:
        data = record.get("data") or {}
        attributes = data.get("attributes") or {}
        titles = attributes.get("titles") or {}
        canonical = attributes.get("canonicalTitle")
        title_en = (
            titles.get("en") or titles.get("en_us") or titles.get("en_jp") or canonical
        )
        title_ja = titles.get("ja_jp") or titles.get("ja")
        manga_id = data.get("id")
        return {
            "id": manga_id,
            "slug": attributes.get("slug"),
            "titles": {"canonical": canonical, "en": title_en, "ja": title_ja},
            "status": attributes.get("status"),
            "synopsis": attributes.get("synopsis"),
            "authors": authors.get(manga_id, []),
            "ratings": {
                "average": self._to_float(attributes.get("averageRating")),
                "rank": self._to_int(attributes.get("ratingRank")),
            },
            "popularity": {"rank": self._to_int(attributes.get("popularityRank"))},
            "tags": {
                "categories": self._relationship_titles(record, "categories"),
                "genres": self._relationship_titles(record, "genres"),
            },
        }

    def build_top_rated(self) -> Path | None:
        if not self.state["catalog"].get("done"):
            return None

        authors = self._load_authors()
        ranked: list[tuple[int, str, dict[str, Any]]] = []
        for record in _iter_jsonl(self.catalog_path):
            normalized = self._normalized_manga(record, authors)
            rank = normalized["ratings"]["rank"]
            manga_id = normalized.get("id")
            if isinstance(rank, int) and isinstance(manga_id, str):
                ranked.append((rank, manga_id, normalized))
        ranked.sort(key=lambda item: (item[0], int(item[1])))

        meta = {
            "category": "top_rated",
            "source": "kitsu",
            "endpoint": "derived from exhaustive manga catalog",
            "fetched_at": utc_now(),
            "limit": len(ranked),
            "offset": 0,
        }
        _write_json_streamed(
            self.top_rated_path,
            meta,
            (item[2] for item in ranked),
            progress_label="top_rated",
            progress_every=5000,
        )
        return self.top_rated_path

    def finalize(self, relations: Sequence[str]) -> dict[str, Any]:
        catalog_done = bool(self.state["catalog"].get("done"))
        relations_done = all(
            bool(self._relation_state(relation).get("done")) for relation in relations
        )
        if catalog_done:
            self.build_top_rated()

        self.state["status"] = (
            "complete" if catalog_done and relations_done else "partial"
        )
        if self.state["status"] != "failed":
            self.state["last_error"] = None
        self._save_state()

        files: list[dict[str, Any]] = []
        candidates = [
            self.catalog_path,
            self.top_rated_path,
            self.errors_path,
            *(self.relations_dir / f"{relation}.ndjson" for relation in relations),
        ]
        for path in candidates:
            if not path.exists():
                continue
            files.append(
                {
                    "path": str(path.relative_to(self.run_dir)),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path)
                    if self.state["status"] == "complete"
                    else None,
                }
            )

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "source": "kitsu",
            "api_base_url": self.client.BASE_URL,
            "generated_at": utc_now(),
            "status": self.state["status"],
            "request_count": self.state["request_count"],
            "catalog": self.state["catalog"],
            "relations": {
                relation: self.state["relations"].get(relation, {})
                for relation in relations
            },
            "files": files,
            "limitations": {
                "individual_volumes_endpoint": False,
                "volume_count_available_on_manga": True,
                "volume_number_available_on_some_chapters": True,
            },
        }
        _write_json_atomic(self.manifest_path, manifest)
        return manifest
