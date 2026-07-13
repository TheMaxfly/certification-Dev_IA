from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Callable

from .client import KitsuClient


class MangaService:
    """Couche métier exposant des données manga formatées pour ton projet."""

    COLLECTION_INCLUDE = "categories,genres"
    STAFF_INCLUDE = "person"
    STAFF_LIMIT = 20

    def __init__(self, client: KitsuClient) -> None:
        self.client = client

    PAGE_SIZE = 20

    # ----------- Résumé par slug/titre -----------

    def get_manga_summary(self, slug: str) -> dict[str, Any] | None:
        manga = self.client.fetch_manga_by_slug(slug)
        if not manga:
            return None

        manga_id = manga.get("id")
        detail_payload: dict[str, Any] = {}
        if isinstance(manga_id, str):
            try:
                detail_payload = self.client.fetch_manga_detail(
                    manga_id, include=self.COLLECTION_INCLUDE
                )
            except Exception:
                detail_payload = {}

        detail_item = detail_payload.get("data") or manga
        included_idx = self._index_included(detail_payload.get("included") or [])
        categories = self._resolve_rel_titles(detail_item, "categories", included_idx)
        genres = self._resolve_rel_titles(detail_item, "genres", included_idx)
        attrs = detail_item.get("attributes") or {}

        authors = self._get_manga_authors(manga_id) if isinstance(manga_id, str) else []
        authors_str = (
            ", ".join(
                f"{author['name']} ({author.get('role')})"
                if author.get("role")
                else author["name"]
                for author in authors
                if isinstance(author, dict) and isinstance(author.get("name"), str)
            )
            or "Non renseignés"
        )

        summary = {
            "Titre": attrs.get("canonicalTitle") or attrs.get("slug"),
            "Slug": attrs.get("slug"),
            "Synopsis": attrs.get("synopsis"),
            "Statut": attrs.get("status"),
            "Début": attrs.get("startDate"),
            "Fin": attrs.get("endDate"),
            "Chapitres": attrs.get("chapterCount"),
            "Volumes": attrs.get("volumeCount"),
            "Note moyenne": attrs.get("averageRating"),
            "Classement popularité": attrs.get("popularityRank"),
            "Auteurs": authors_str,
            "Genres": ", ".join(genres) if genres else None,
            "Catégories": ", ".join(categories) if categories else None,
        }

        return {key: value for key, value in summary.items() if value is not None}

    # ----------- Liste par tag/catégorie -----------

    def list_manga_by_tag(self, tag: str, limit: int = 10) -> dict[str, Any]:
        limit = max(1, limit)
        return self.client.list_manga_by_tag(tag, limit=limit)

    # ----------- Trending hebdomadaire -----------

    def get_weekly_trending(self, limit: int = 20) -> dict[str, Any]:
        payload = self.client.fetch_trending_manga(limit=limit)
        out: list[dict[str, Any]] = []

        for item in (payload.get("data") or [])[: max(0, limit)]:
            manga_id = item.get("id")
            detail_item = item
            included_idx: dict[tuple[str, str], dict[str, Any]] = {}

            if isinstance(manga_id, str):
                try:
                    detail_payload = self.client.fetch_manga_detail(
                        manga_id, include=self.COLLECTION_INCLUDE
                    )
                    detail_item = detail_payload.get("data") or detail_item
                    included_idx = self._index_included(
                        detail_payload.get("included") or []
                    )
                except Exception:
                    included_idx = {}

            out.append(
                self._format_manga_item(
                    item=detail_item,
                    included_idx=included_idx,
                    authors=self._get_manga_authors(manga_id)
                    if isinstance(manga_id, str)
                    else [],
                )
            )

        return {"data": out}

    # ----------- Top “rated” (mieux notés) -----------

    def get_top_rated(
        self, limit: int = 10, offset: int = 0, include_authors: bool = True
    ) -> dict[str, Any]:
        return self._collect_ranked_manga(
            fetch_fn=self.client.fetch_top_rated_manga,
            limit=limit,
            offset=offset,
            sort="ratingRank",
            include_authors=include_authors,
        )

    # ----------- Top “popular” (plus populaires) -----------

    def get_most_popular(
        self, limit: int = 10, offset: int = 0, include_authors: bool = True
    ) -> dict[str, Any]:
        return self._collect_ranked_manga(
            fetch_fn=self.client.fetch_most_popular_manga,
            limit=limit,
            offset=offset,
            sort="popularityRank",
            include_authors=include_authors,
        )

    # ----------- NOUVEAU : Top “publishing” (top publications) -----------

    def get_top_publishing(
        self, limit: int = 10, offset: int = 0, include_authors: bool = True
    ) -> dict[str, Any]:
        return self._collect_ranked_manga(
            fetch_fn=self.client.fetch_top_publishing_manga,
            limit=limit,
            offset=offset,
            sort="popularityRank",
            include_authors=include_authors,
        )

    def iter_top_rated(
        self, offset: int = 0, include_authors: bool = False
    ) -> Iterator[dict[str, Any]]:
        return self._iter_ranked_manga(
            fetch_fn=self.client.fetch_top_rated_manga,
            offset=offset,
            sort="ratingRank",
            include_authors=include_authors,
        )

    def _collect_ranked_manga(
        self,
        fetch_fn: Callable[..., dict[str, Any]],
        limit: int,
        offset: int,
        sort: str,
        include_authors: bool,
    ) -> dict[str, Any]:
        """
        Récupère une liste paginée de mangas triés (ratingRank/popularityRank).
        - `limit` <= 0 signifie "tout" (pagination jusqu'à épuisement).
        """
        out: list[dict[str, Any]] = []
        page_offset = max(offset, 0)

        remaining: int | None
        if limit <= 0:
            remaining = None
        else:
            remaining = max(0, limit)

        while True:
            page_limit = (
                self.PAGE_SIZE if remaining is None else min(self.PAGE_SIZE, remaining)
            )
            if page_limit <= 0:
                break

            page_items = list(
                self._iter_ranked_manga_page(
                    fetch_fn=fetch_fn,
                    page_offset=page_offset,
                    page_limit=page_limit,
                    sort=sort,
                    include_authors=include_authors,
                )
            )
            if not page_items:
                break

            for formatted in page_items:
                out.append(formatted)
                if remaining is not None:
                    remaining -= 1
                    if remaining <= 0:
                        break

            if remaining is not None and remaining <= 0:
                break

            if len(page_items) < page_limit:
                break

            page_offset += len(page_items)

        return {"data": out}

    def _iter_ranked_manga(
        self,
        fetch_fn: Callable[..., dict[str, Any]],
        offset: int,
        sort: str,
        include_authors: bool,
    ) -> Iterator[dict[str, Any]]:
        page_offset = max(offset, 0)
        while True:
            page_limit = self.PAGE_SIZE
            items = list(
                self._iter_ranked_manga_page(
                    fetch_fn=fetch_fn,
                    page_offset=page_offset,
                    page_limit=page_limit,
                    sort=sort,
                    include_authors=include_authors,
                )
            )
            if not items:
                break
            yield from items
            if len(items) < page_limit:
                break
            page_offset += page_limit

    def _iter_ranked_manga_page(
        self,
        fetch_fn: Callable[..., dict[str, Any]],
        page_offset: int,
        page_limit: int,
        sort: str,
        include_authors: bool,
    ) -> Iterator[dict[str, Any]]:
        payload = fetch_fn(
            limit=page_limit,
            offset=page_offset,
            include=self.COLLECTION_INCLUDE,
            sort=sort,
        )

        data = payload.get("data") or []
        if not data:
            return

        included_idx = self._index_included(payload.get("included") or [])
        for item in data:
            manga_id = item.get("id")
            if not isinstance(manga_id, str):
                continue
            authors = self._get_manga_authors(manga_id) if include_authors else []
            yield self._format_manga_item(
                item=item, included_idx=included_idx, authors=authors
            )

    # ----------- Formatage -----------

    def _format_manga_item(
        self,
        item: dict[str, Any],
        included_idx: dict[tuple[str, str], dict[str, Any]],
        authors: list[dict[str, str]],
    ) -> dict[str, Any]:
        attrs = item.get("attributes") or {}
        titles = attrs.get("titles") or {}

        categories = self._resolve_rel_titles(item, "categories", included_idx)
        genres = self._resolve_rel_titles(item, "genres", included_idx)

        canonical = self._as_str(attrs.get("canonicalTitle"))
        title_en = self._pick_first_str(
            titles.get("en"),
            titles.get("en_us"),
            titles.get("en_jp"),
            canonical,
        )
        title_ja = self._pick_first_str(
            titles.get("ja_jp"),
            titles.get("ja"),
        )

        return {
            "id": item.get("id"),
            "slug": attrs.get("slug"),
            "titles": {
                "canonical": canonical,
                "en": title_en,
                "ja": title_ja,
            },
            "status": attrs.get("status"),
            "synopsis": attrs.get("synopsis"),
            "authors": authors,
            "ratings": {
                "average": self._to_float(attrs.get("averageRating")),
                "rank": self._to_int(attrs.get("ratingRank")),
            },
            "popularity": {
                "rank": self._to_int(attrs.get("popularityRank")),
            },
            "tags": {
                "categories": categories,
                "genres": genres,
            },
        }

    # ----------- Auteurs -----------

    def _get_manga_authors(self, manga_id: str) -> list[dict[str, str]]:
        """
        Auteurs via /manga/{id}/staff?include=person
        Filtre sur rôles “story/writer/author” et “art/artist/illustr...”
        """
        # 1) Happy path : /staff
        try:
            staff_payload = self.client.fetch_staff(
                manga_id, include=self.STAFF_INCLUDE, limit=self.STAFF_LIMIT
            )
            authors = self._extract_authors_from_staff_payload(staff_payload)
            if authors:
                return authors
        except Exception:
            pass

        # 2) Fallback : /manga-staff (si jamais utile)
        try:
            staff_payload = self.client.fetch_manga_staff(
                manga_id, include=self.STAFF_INCLUDE, limit=self.STAFF_LIMIT
            )
            return self._extract_authors_from_staff_payload(staff_payload)
        except Exception:
            return []

    def _extract_authors_from_staff_payload(
        self, staff_payload: dict[str, Any]
    ) -> list[dict[str, str]]:
        included_idx = self._index_included(staff_payload.get("included") or [])
        out: list[dict[str, str]] = []
        seen = set()

        for staff in staff_payload.get("data") or []:
            attrs = staff.get("attributes") or {}
            role_norm = str(attrs.get("role") or "").strip().lower()

            is_story = any(
                k in role_norm
                for k in ("story", "writer", "author", "scenario", "script")
            )
            is_art = any(
                k in role_norm
                for k in ("art", "artist", "illustr", "drawing", "pencils", "ink")
            )
            if not (is_story or is_art):
                continue

            person_ref = (
                ((staff.get("relationships") or {}).get("person") or {}).get("data")
            ) or {}
            ref_type = person_ref.get("type")
            ref_id = person_ref.get("id")
            if not (isinstance(ref_type, str) and isinstance(ref_id, str)):
                continue

            person = included_idx.get((ref_type, ref_id))
            if not person:
                continue

            name = (person.get("attributes") or {}).get("name")
            if not isinstance(name, str) or not name.strip():
                continue

            if is_story and is_art:
                display_role = "Scénario & Dessin"
            elif is_story:
                display_role = "Scénario"
            else:
                display_role = "Dessin"

            key = (name.strip(), display_role)
            if key in seen:
                continue
            seen.add(key)

            out.append({"name": name.strip(), "role": display_role})

        return out

    # ----------- Helpers JSON:API -----------

    @staticmethod
    def _index_included(
        included: list[dict[str, Any]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        idx: dict[tuple[str, str], dict[str, Any]] = {}
        for obj in included:
            t = obj.get("type")
            i = obj.get("id")
            if isinstance(t, str) and isinstance(i, str):
                idx[(t, i)] = obj
        return idx

    @staticmethod
    def _resolve_rel_titles(
        item: dict[str, Any],
        rel_name: str,
        included_idx: dict[tuple[str, str], dict[str, Any]],
    ) -> list[str]:
        rel = (item.get("relationships") or {}).get(rel_name) or {}
        refs = rel.get("data") or []
        titles: list[str] = []

        for ref in refs:
            ref_type = ref.get("type")
            ref_id = ref.get("id")
            if not isinstance(ref_type, str) or not isinstance(ref_id, str):
                continue

            obj = included_idx.get((ref_type, ref_id))
            if not obj:
                continue

            attrs = obj.get("attributes") or {}
            value = attrs.get("title") or attrs.get("name") or attrs.get("slug")
            if isinstance(value, str) and value.strip():
                titles.append(value.strip())

        return titles

    @staticmethod
    def _as_str(value: Any) -> str | None:
        return value if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _pick_first_str(*values: Any) -> str | None:
        for v in values:
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            try:
                return int(s)
            except ValueError:
                return None
        return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            try:
                return float(s)
            except ValueError:
                return None
        return None
