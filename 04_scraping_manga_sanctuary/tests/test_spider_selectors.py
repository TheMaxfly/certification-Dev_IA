"""Verrouille les cinq correctifs de sélecteurs validés par le canari de 2026-07.

`canari/07_verifier.py` fait le même travail, mais contre `canari/html/` — des
pages du site, gitignorées, donc absentes en CI. Les fixtures utilisées ici sont
écrites à la main : elles reproduisent les structures constatées sans embarquer
le moindre contenu du site, et rendent la suite rejouable partout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from manga_sanctuary.items import ReviewItem, VolumeItem
from manga_sanctuary.spiders.manga_sanctuary_volumes import MangaSanctuaryVolumesSpider
from scrapy.http import HtmlResponse, Request

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BASE_URL = "https://www.manga-sanctuary.com/bdd/manga/12345-parmi-eux-hanakimi/"


@pytest.fixture
def spider() -> MangaSanctuaryVolumesSpider:
    return MangaSanctuaryVolumesSpider()


def response_de(nom: str, url: str = BASE_URL) -> HtmlResponse:
    return HtmlResponse(
        url=url, body=(FIXTURES / nom).read_bytes(), encoding="utf-8", status=200
    )


def series_meta(spider, response) -> dict:
    """parse_series ne rend pas la meta : elle la transmet aux requêtes tome."""
    for sortie in spider.parse_series(response, letter="P"):
        if isinstance(sortie, Request) and "series_meta" in (sortie.cb_kwargs or {}):
            return dict(sortie.cb_kwargs["series_meta"])
    raise AssertionError("parse_series n'a émis aucune requête tome")


# --------------------------------------------------------------------------- #
#  Correctif 5.1 — genres et tags (0 % -> ~88 % / ~28 %)
# --------------------------------------------------------------------------- #


def test_genres_collectes(spider):
    meta = series_meta(spider, response_de("serie.html"))

    assert meta["series_genres"] == ["action", "aventure", "comédie"]


def test_tags_collectes_sans_diese_decoratif(spider):
    meta = series_meta(spider, response_de("serie.html"))

    assert meta["series_tags"] == ["super pouvoirs", "travestissement"]


# --------------------------------------------------------------------------- #
#  Correctif 5.2 — alias complets (+47 % de rappel)
# --------------------------------------------------------------------------- #


def test_alias_de_continuation_tous_collectes(spider):
    meta = series_meta(spider, response_de("serie.html"))

    assert meta["series_other_titles"] == [
        "花ざかりの君たちへ",
        "Hanazakari no Kimitachi e",
        "Hana-Kimi",
        "Hana-Kimi - For you in full blossom",
    ]


def test_alias_ne_debordent_pas_sur_le_label_suivant(spider):
    """La borne Kayessian doit arrêter la collecte au premier label non vide :
    la ligne de continuation de « Type » n'est pas un alias."""
    meta = series_meta(spider, response_de("serie.html"))

    assert not any("Continuation" in t for t in meta["series_other_titles"])
    assert meta["series_type"] == "Manga"


# --------------------------------------------------------------------------- #
#  Correctif 5.4 — EAN-13 (jointure Manga Insight)
# --------------------------------------------------------------------------- #


def volume_de(spider, nom: str) -> VolumeItem:
    items = [
        sortie
        for sortie in spider.parse_volume(
            response_de(nom, url=f"{BASE_URL}vol-1.html"), series_meta={}
        )
        if isinstance(sortie, VolumeItem)
    ]
    assert items, "parse_volume n'a émis aucun VolumeItem"
    return items[0]


def test_ean_collecte_en_chaine(spider):
    item = volume_de(spider, "tome_avec_ean.html")

    # Chaîne, jamais int : la clé de contrôle et le cadrage 13 chiffres doivent
    # survivre à la jointure avec Manga Insight.
    assert item["volume_ean"] == "9782355929489"
    assert isinstance(item["volume_ean"], str)


def test_ean_absent_donne_none(spider):
    item = volume_de(spider, "tome_sans_ean.html")

    assert item["volume_ean"] is None


# --------------------------------------------------------------------------- #
#  Correctif 5.5 — review_body (+112 % de corpus RAG)
# --------------------------------------------------------------------------- #


def review_de(spider, nom: str) -> ReviewItem:
    reviews = list(
        spider.parse_staff_review(
            response_de(nom, url=f"{BASE_URL}critique.php?id=1"),
            series_meta={},
            volume_number=1,
            volume_url=f"{BASE_URL}vol-1.html",
        )
    )
    assert reviews, "parse_staff_review n'a émis aucun ReviewItem"
    return reviews[0]


def test_corps_recupere_sans_aucun_paragraphe(spider):
    """La structure texte + <br> sans <p> : 52,8 % du corpus, perdue avant."""
    review = review_de(spider, "review_sauts_de_ligne.html")

    assert "Luno était un manga plutôt attendu" in review["review_body"]
    assert "Le trait est fin" in review["review_body"]


def test_corps_en_paragraphes_reste_un_sur_ensemble_strict(spider):
    """Non-régression : le sélecteur corrigé ne perd rien de l'ancien, et
    récupère en plus le chapô situé hors <p>."""
    response = response_de("review_paragraphes.html")
    ancien = response.xpath(
        "//div[contains(@class,'post-single') and contains(@class,'text-justify')]"
        "//p//text()"
    ).getall()

    corps = review_de(spider, "review_paragraphes.html")["review_body"]

    for fragment in (f.strip() for f in ancien if f.strip()):
        assert fragment in corps
    assert "Chapô hors paragraphe." in corps
