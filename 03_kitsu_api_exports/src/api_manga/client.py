from __future__ import annotations

import time
from email.utils import parsedate_to_datetime
from typing import Any

import requests


class KitsuRequestError(RuntimeError):
    """Erreur HTTP Kitsu avec code exploitable par les collecteurs."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class KitsuClient:
    """Petit client HTTP pour interagir avec l'API Kitsu (JSON:API)."""

    BASE_URL = "https://kitsu.io/api/edge"

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: float = 15.0,
        *,
        max_retries: int = 3,
        min_interval: float = 0.0,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.min_interval = max(0.0, min_interval)
        self.request_count = 0
        self._last_request_started = 0.0
        self.headers = {
            "Accept": "application/vnd.api+json",
            "User-Agent": "ApiMangaCertification/0.2 (exhaustive data collector)",
        }

    def _wait_for_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_started
        remaining = self.min_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)

    @staticmethod
    def _retry_delay(response: requests.Response | None, attempt: int) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return max(0.0, float(retry_after))
                except ValueError:
                    try:
                        retry_at = parsedate_to_datetime(retry_after)
                        return max(0.0, retry_at.timestamp() - time.time())
                    except (TypeError, ValueError):
                        pass
        return min(60.0, 0.5 * (2**attempt))

    def _request(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        path = path.lstrip("/")
        url = f"{self.BASE_URL}/{path}"

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            response: requests.Response | None = None
            try:
                self._wait_for_rate_limit()
                self._last_request_started = time.monotonic()
                response = self.session.get(
                    url, params=params, headers=self.headers, timeout=self.timeout
                )
                self.request_count += 1

                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                if response.status_code >= 400:
                    raise KitsuRequestError(
                        f"Kitsu HTTP {response.status_code}: {url}",
                        status_code=response.status_code,
                    )
                return response.json()
            except KitsuRequestError:
                raise
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt + 1 < self.max_retries:
                    time.sleep(self._retry_delay(response, attempt))

        status_code = response.status_code if response is not None else None
        raise KitsuRequestError(
            f"Requête Kitsu échouée après retries: {url} ({last_exc})",
            status_code=status_code,
        )

    def fetch_catalog_page(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        include: str = "categories,genres",
    ) -> dict[str, Any]:
        """Récupère une page du catalogue manga sans filtre de classement."""
        params: dict[str, Any] = {
            "page[limit]": min(max(limit, 1), 20),
            "page[offset]": max(offset, 0),
        }
        if include:
            params["include"] = include
        return self._request("manga", params=params)

    def fetch_relationship_page(
        self,
        manga_id: str,
        relationship: str,
        *,
        limit: int = 20,
        offset: int = 0,
        include: str | None = None,
    ) -> dict[str, Any]:
        """Récupère une page d'une relation exposée sous `/manga/{id}/...`."""
        params: dict[str, Any] = {
            "page[limit]": min(max(limit, 1), 20),
            "page[offset]": max(offset, 0),
        }
        if include:
            params["include"] = include
        return self._request(
            f"manga/{manga_id}/{relationship}",
            params=params,
        )

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
