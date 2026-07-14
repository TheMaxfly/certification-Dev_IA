"""Le crawl doit s'arrêter net sur blocage, sans jamais rien contourner."""

from __future__ import annotations

import pytest
from manga_sanctuary.spiders.manga_sanctuary_volumes import MangaSanctuaryVolumesSpider
from scrapy.exceptions import CloseSpider
from scrapy.http import HtmlResponse

URL = "https://www.manga-sanctuary.com/bdd/series.html"

PAGE_NORMALE = b"<html><body><h1>Series</h1></body></html>"
PAGE_CHALLENGE = (
    b"<html><head><title>Just a moment...</title></head>"
    b"<body><div class='cf-chl-widget'></div></body></html>"
)


@pytest.fixture
def spider() -> MangaSanctuaryVolumesSpider:
    return MangaSanctuaryVolumesSpider()


def response_de(status: int, body: bytes = PAGE_NORMALE) -> HtmlResponse:
    return HtmlResponse(url=URL, body=body, encoding="utf-8", status=status)


@pytest.mark.parametrize("status", [403, 429, 503])
def test_statut_de_blocage_arrete_le_crawl(spider, status):
    with pytest.raises(CloseSpider) as arret:
        spider.ensure_access(response_de(status))

    assert arret.value.reason == f"manga_sanctuary_access_blocked_http_{status}"


def test_challenge_anti_bot_arrete_le_crawl_malgre_un_200(spider):
    """Un challenge se sert en HTTP 200 : le statut seul ne suffit pas."""
    with pytest.raises(CloseSpider):
        spider.ensure_access(response_de(200, PAGE_CHALLENGE))


def test_page_normale_laisse_passer(spider):
    assert spider.ensure_access(response_de(200)) is None


def test_statuts_de_blocage_atteignent_le_callback():
    """Sans handle_httpstatus_list, Scrapy filtrerait les 403/429/503 avant le
    callback : le garde ne verrait jamais rien et le crawl continuerait."""
    assert MangaSanctuaryVolumesSpider.handle_httpstatus_list == [403, 429, 503]


def test_le_garde_est_cable_sur_tous_les_callbacks():
    source = MangaSanctuaryVolumesSpider
    for callback in (
        source.parse_index,
        source.parse_series,
        source.parse_volume,
        source.parse_staff_review,
    ):
        assert "ensure_access" in callback.__code__.co_names, callback.__name__
