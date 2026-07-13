from __future__ import annotations

import time
from typing import Any

import requests


class KitsuClient:
    """Petit client HTTP pour interagir avec l'API Kitsu (JSON:API)."""

    BASE_URL = "https://kitsu.io/api/edge"

    def __init__(
        self, session: requests.Session | None = None, timeout: float = 15.0
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.headers = {"Accept": "application/vnd.api+json"}

    def _request(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        path = path.lstrip("/")
        url = f"{self.BASE_URL}/{path}"

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = self.session.get(
                    url, params=params, headers=self.headers, timeout=self.timeout
                )
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                time.sleep(0.5 * (attempt + 1))

        raise RuntimeError(f"Requête Kitsu échouée après retries: {url} ({last_exc})")

    def fetch_manga_by_slug(self, slug: str) -> dict[str, Any] | None:
        params = {"filter[text]": slug, "page[limit]": 1}
        payload = self._request("manga", params=params)
        return (payload.get("data") or [None])[0]

    def list_manga_by_tag(self, tag: str, limit: int = 10) -> dict[str, Any]:
        params = {"filter[categories]": tag, "page[limit]": min(limit, 20)}
        return self._request("manga", params=params)

    def fetch_trending_manga(self, limit: int = 20) -> dict[str, Any]:
        params = {"page[limit]": min(limit, 20)}
        return self._request("trending/manga", params=params)

    def fetch_manga_detail(
        self, manga_id: str, include: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if include:
            params["include"] = include
        return self._request(f"manga/{manga_id}", params=params)

    # ✅ NOUVEAU : Top “Publishing” (= publications en cours)
    def fetch_top_publishing_manga(
        self,
        limit: int = 10,
        offset: int = 0,
        include: str = "categories,genres",
        sort: str = "popularityRank",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "filter[status]": "current",
            "sort": sort,
            "page[limit]": min(limit, 20),
            "page[offset]": max(offset, 0),
        }
        if include:
            params["include"] = include
        return self._request("manga", params=params)

    # ✅ NOUVEAU : Top “Rated” (= mieux notés)
    def fetch_top_rated_manga(
        self,
        limit: int = 10,
        offset: int = 0,
        include: str = "categories,genres",
        sort: str = "ratingRank",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "sort": sort,
            "page[limit]": min(limit, 20),
            "page[offset]": max(offset, 0),
        }
        if include:
            params["include"] = include
        return self._request("manga", params=params)

    # ✅ NOUVEAU : Top “Popular” (= plus populaires)
    def fetch_most_popular_manga(
        self,
        limit: int = 10,
        offset: int = 0,
        include: str = "categories,genres",
        sort: str = "popularityRank",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "sort": sort,
            "page[limit]": min(limit, 20),
            "page[offset]": max(offset, 0),
        }
        if include:
            params["include"] = include
        return self._request("manga", params=params)

    # ✅ Staff (auteurs) – c’est l’endpoint qui t’a donné Eiichiro Oda
    def fetch_staff(
        self, manga_id: str, include: str = "person", limit: int = 20
    ) -> dict[str, Any]:
        params = {"include": include, "page[limit]": min(limit, 20)}
        return self._request(f"manga/{manga_id}/staff", params=params)

    # (tu peux garder fetch_manga_staff si tu veux,
    # mais fetch_staff est ton “happy path”)
    def fetch_manga_staff(
        self, manga_id: str, include: str = "person", limit: int = 20
    ) -> dict[str, Any]:
        params = {"include": include, "page[limit]": min(limit, 20)}

        try:
            payload = self._request(f"manga/{manga_id}/manga-staff", params=params)
            if payload.get("data") or []:
                return payload
        except RuntimeError:
            pass

        fallback_params = {"filter[manga]": manga_id, **params}
        return self._request("manga-staff", params=fallback_params)
