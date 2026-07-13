from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .service import MangaService


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run_id_now() -> str:
    # Format safe for filenames
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_latest_marker(dir_path: Path, run_id: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "LATEST").write_text(run_id + "\n", encoding="utf-8")


def _read_latest_marker(dir_path: Path) -> str | None:
    p = dir_path / "LATEST"
    if not p.exists():
        return None
    value = p.read_text(encoding="utf-8").strip()
    return value or None


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


def _write_json_streamed(
    path: Path,
    meta: dict[str, Any],
    items: Iterable[object],
    *,
    progress_label: str | None = None,
    progress_every: int = 500,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write("{\n")
        f.write('"meta": ')
        f.write(json.dumps(meta, ensure_ascii=False))
        f.write(',\n"data": [\n')
        first = True
        count = 0
        for item in items:
            if not first:
                f.write(",\n")
            first = False
            f.write(json.dumps(item, ensure_ascii=False))
            count += 1
            if progress_label and progress_every > 0 and count % progress_every == 0:
                print(f"[export] {progress_label}: {count} items...", flush=True)
        f.write("\n]\n}\n")
    tmp.replace(path)


def _is_valid_json(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except Exception:
        return False


def _append_ndjson(path: Path, items: Iterable[object]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            count += 1
    return count


def _ndjson_to_json_array(
    ndjson_path: Path, json_path: Path, meta: dict[str, Any]
) -> None:
    tmp = json_path.with_suffix(json_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as out:
        out.write("{\n")
        out.write('"meta": ')
        out.write(json.dumps(meta, ensure_ascii=False))
        out.write(',\n"data": [\n')
        first = True
        with ndjson_path.open("r", encoding="utf-8") as inp:
            for line in inp:
                line = line.strip()
                if not line:
                    continue
                if not first:
                    out.write(",\n")
                first = False
                out.write(line)
        out.write("\n]\n}\n")
    tmp.replace(json_path)


def export_trending_weekly(service: MangaService, out_dir: Path, limit: int) -> Path:
    payload = service.get_weekly_trending(limit=limit)
    out_path = out_dir / "trending_weekly.json"
    export: dict[str, Any] = {
        "meta": {
            "category": "trending_weekly",
            "source": "kitsu",
            "endpoint": "trending/manga",
            "fetched_at": _iso_utc_now(),
            "limit": limit,
            "offset": 0,
        },
        **payload,
    }
    _write_json(out_path, export)
    return out_path


def export_top_publishing(
    service: MangaService,
    out_dir: Path,
    limit: int,
    offset: int,
    include_authors: bool = True,
) -> Path:
    payload = service.get_top_publishing(
        limit=limit, offset=offset, include_authors=include_authors
    )
    out_path = out_dir / "top_publishing.json"
    export: dict[str, Any] = {
        "meta": {
            "category": "top_publishing",
            "source": "kitsu",
            "endpoint": "manga?filter[status]=current&sort=popularityRank",
            "fetched_at": _iso_utc_now(),
            "limit": limit,
            "offset": offset,
        },
        **payload,
    }
    _write_json(out_path, export)
    return out_path


def export_top_rated(
    service: MangaService,
    out_dir: Path,
    limit: int,
    offset: int,
    include_authors: bool = True,
    *,
    force: bool = False,
    resume: bool = True,
    max_pages: int = 0,
) -> Path:
    out_path = out_dir / "top_rated.json"
    meta = {
        "category": "top_rated",
        "source": "kitsu",
        "endpoint": "manga?sort=ratingRank",
        "fetched_at": _iso_utc_now(),
        "limit": limit,
        "offset": offset,
    }

    if limit <= 0:
        if out_path.exists() and not force and _is_valid_json(out_path):
            return out_path

        state_path = out_dir / "top_rated.state.json"
        ndjson_path = out_dir / "top_rated.ndjson"
        tmp_json_path = out_path.with_suffix(out_path.suffix + ".tmp")

        if force and out_path.exists():
            backup = out_path.with_suffix(
                out_path.suffix + f".bak.{_iso_utc_now().replace(':', '-')}"
            )
            out_path.replace(backup)
        if force and tmp_json_path.exists():
            tmp_json_path.unlink(missing_ok=True)

        if force and not resume:
            state_path.unlink(missing_ok=True)
            ndjson_path.unlink(missing_ok=True)

        state: dict[str, Any] = {}
        if resume and state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                state = {}

        next_offset = int(state.get("next_offset") or max(offset, 0))
        done = bool(state.get("done") or False)
        written = int(state.get("written") or 0)

        if done and out_path.exists() and _is_valid_json(out_path) and not force:
            return out_path

        pages_done = 0
        page_size = service.PAGE_SIZE
        while True:
            payload = service.get_top_rated(
                limit=page_size,
                offset=next_offset,
                include_authors=include_authors,
            )
            data = payload.get("data") or []
            if not data:
                done = True
                break

            wrote_now = _append_ndjson(ndjson_path, data)
            written += wrote_now
            next_offset += page_size
            pages_done += 1

            state_path.write_text(
                json.dumps(
                    {
                        "done": False,
                        "next_offset": next_offset,
                        "written": written,
                        "meta": meta,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            if wrote_now < page_size:
                done = True
                break

            if max_pages > 0 and pages_done >= max_pages:
                break

            if written and written % 500 == 0:
                print(f"[export] top_rated: {written} items...", flush=True)

        if done:
            meta_final = {
                **meta,
                "fetched_at": _iso_utc_now(),
                "limit": 0,
                "offset": offset,
            }
            _ndjson_to_json_array(ndjson_path, out_path, meta=meta_final)
            state_path.write_text(
                json.dumps(
                    {
                        "done": True,
                        "next_offset": next_offset,
                        "written": written,
                        "meta": meta_final,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            ndjson_path.unlink(missing_ok=True)
        return out_path

    payload = service.get_top_rated(
        limit=limit, offset=offset, include_authors=include_authors
    )
    _write_json(out_path, {"meta": meta, **payload})
    return out_path


def export_most_popular(
    service: MangaService,
    out_dir: Path,
    limit: int,
    offset: int,
    include_authors: bool = True,
) -> Path:
    payload = service.get_most_popular(
        limit=limit, offset=offset, include_authors=include_authors
    )
    out_path = out_dir / "most_popular.json"
    export: dict[str, Any] = {
        "meta": {
            "category": "most_popular",
            "source": "kitsu",
            "endpoint": "manga?sort=popularityRank",
            "fetched_at": _iso_utc_now(),
            "limit": limit,
            "offset": offset,
        },
        **payload,
    }
    _write_json(out_path, export)
    return out_path


def export_all(
    service: MangaService,
    out_dir: Path,
    *,
    trending_limit: int,
    publishing_limit: int,
    publishing_offset: int,
    rated_limit: int,
    rated_offset: int,
    popular_limit: int,
    popular_offset: int,
    rated_include_authors: bool = False,
    publishing_include_authors: bool = True,
    popular_include_authors: bool = True,
    force_top_rated: bool = False,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return {
        "trending_weekly": export_trending_weekly(
            service, out_dir, limit=trending_limit
        ),
        "top_publishing": export_top_publishing(
            service,
            out_dir,
            limit=publishing_limit,
            offset=publishing_offset,
            include_authors=publishing_include_authors,
        ),
        "top_rated": export_top_rated(
            service,
            out_dir,
            limit=rated_limit,
            offset=rated_offset,
            include_authors=rated_include_authors,
            force=force_top_rated,
        ),
        "most_popular": export_most_popular(
            service,
            out_dir,
            limit=popular_limit,
            offset=popular_offset,
            include_authors=popular_include_authors,
        ),
    }
