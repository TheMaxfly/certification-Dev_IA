from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

import requests
from api_manga.client import KitsuClient, KitsuRequestError


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self.payload = payload
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)  # type: ignore[arg-type]

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls = 0
        self.last_headers: dict[str, str] = {}

    def get(self, _url: str, **kwargs: Any) -> FakeResponse:
        self.last_headers = kwargs["headers"]
        response = self.responses[self.calls]
        self.calls += 1
        return response


class ClientTests(unittest.TestCase):
    def test_client_does_not_retry_a_permanent_404(self) -> None:
        session = FakeSession([FakeResponse(404, {})])
        client = KitsuClient(session=session, max_retries=5)  # type: ignore[arg-type]

        with self.assertRaises(KitsuRequestError) as error:
            client.fetch_relationship_page("38", "volumes")

        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(session.calls, 1)

    def test_client_retries_a_server_error_and_identifies_itself(self) -> None:
        session = FakeSession(
            [FakeResponse(503, {}), FakeResponse(200, {"data": [{"id": "1"}]})]
        )
        client = KitsuClient(session=session, max_retries=2)  # type: ignore[arg-type]

        with patch("api_manga.client.time.sleep"):
            payload = client.fetch_catalog_page(limit=1)

        self.assertEqual(payload["data"][0]["id"], "1")
        self.assertEqual(session.calls, 2)
        self.assertIn("ApiMangaCertification", session.last_headers["User-Agent"])


if __name__ == "__main__":
    unittest.main()
