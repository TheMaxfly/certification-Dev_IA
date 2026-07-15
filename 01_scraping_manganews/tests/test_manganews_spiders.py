from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scrapy.exceptions import CloseSpider
from scrapy.http import HtmlResponse, Request

from manga_news_scraper.spiders.manganews_populaires import (
    MangaNewsPopulairesSpider,
)
from manga_news_scraper.spiders.manganews_series import MangaNewsSeriesSpider


def html_response(url: str, body: str, status: int = 200) -> HtmlResponse:
    return HtmlResponse(
        url=url,
        status=status,
        body=body.encode(),
        encoding="utf-8",
        request=Request(url=url),
    )


class MangaNewsSeriesSpiderTests(unittest.TestCase):
    def test_hub_keeps_only_current_alpha_listing_urls(self):
        response = html_response(
            "https://www.manga-news.com/index.php/series/",
            """
            <ul class="alphaLink">
              <li><a href="/index.php/series/">#</a></li>
              <li><a href="/index.php/series/A">A</a></li>
              <li><a href="/index.php/serie/Alpha">Fausse fiche</a></li>
            </ul>
            """,
        )

        requests = list(MangaNewsSeriesSpider().parse(response))

        self.assertEqual(
            {request.url for request in requests},
            {
                "https://www.manga-news.com/index.php/series/",
                "https://www.manga-news.com/index.php/series/A",
            },
        )

    def test_detail_extracts_multiple_authors_and_rag_fields(self):
        response = html_response(
            "https://www.manga-news.com/index.php/serie/Manga",
            """
            <main>
              <h1><span>Manga</span></h1>
              <ul class="entryInfos">
                <li class="title-vo"><span class="entry-data-wrapper">: Manga</span></li>
                <li class="book-by2">Scénario :
                  <a href="/index.php/auteur/A">Autrice A</a>
                  <a href="/index.php/auteur/B">Auteur B</a>
                </li>
                <li class="book-edit-vf">Editeur VF :
                  <a href="/index.php/editeur/Test">Test Editions</a>
                </li>
                <li class="book-type">Type :
                  <a href="/index.php/type/Essai">Essai</a>
                </li>
                <li class="book-genre">Genre :
                  <a href="/index.php/genre/Culture">Culture</a>
                </li>
                <li class="illust"><span>Illustration</span>: n&amp;b</li>
                <li class="book-origin"><span>Origine</span>: Japon - 2019</li>
              </ul>
              <h2>Résumé</h2>
              <div class="bigsize"><p>Un résumé <strong>riche</strong>.</p></div>
              <section id="product-strong">
                <div class="bigsize">Un point <strong>fort</strong>.</div>
              </section>
            </main>
            """,
        )

        items = list(MangaNewsSeriesSpider().parse_series_detail(response))

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["title_page"], "Manga")
        self.assertEqual(item["scenario"], "Autrice A, Auteur B")
        self.assertEqual(item["resume"], "Un résumé riche .")
        self.assertEqual(item["points_forts"], "Un point fort .")
        self.assertIn("Résumé: Un résumé riche .", item["rag_text"])

    def test_resume_schedules_only_missing_detail_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "items.jsonl"
            existing.write_text(
                json.dumps({"url": "https://www.manga-news.com/index.php/serie/Alpha"})
                + "\n",
                encoding="utf-8",
            )
            response = html_response(
                "https://www.manga-news.com/index.php/series/A",
                """
                <a href="/index.php/serie/Alpha">Alpha</a>
                <a href="/index.php/serie/Beta">Beta</a>
                """,
            )

            requests = list(
                MangaNewsSeriesSpider(
                    existing_items_file=str(existing)
                ).parse_series_list(response)
            )

            self.assertEqual(
                [request.url for request in requests],
                ["https://www.manga-news.com/index.php/serie/Beta"],
            )

    def test_cloudflare_response_stops_the_crawl_explicitly(self):
        response = html_response(
            "https://www.manga-news.com/index.php/series/",
            "<html><title>Just a moment...</title><div class='cf-chl-test'></div></html>",
            status=403,
        )

        with self.assertRaises(CloseSpider) as raised:
            list(MangaNewsSeriesSpider().parse(response))

        self.assertEqual(
            raised.exception.reason,
            "manganews_access_blocked_http_403",
        )


class MangaNewsPopulairesSpiderTests(unittest.TestCase):
    def test_populaires_extracts_description_and_lazy_image(self):
        response = html_response(
            "https://www.manga-news.com/index.php/manga-populaires",
            """
            <div id="best-blocks">
              <section class="boxed entries" id="best-block-seinen">
                <h3>Seinen</h3>
                <div class="rounded-box-content">
                  <p>Des intrigues plus complexes.</p>
                  <div class="section-list">
                    <article class="section-list-item">
                      <a class="section-list-item-img"
                         href="/index.php/serie/20th-century-boys"
                         title="20th Century Boys">
                        <img class="entryPicture" data-src="/images/20cb.jpg">
                      </a>
                      <span class="catIcon">22 Volume(s)</span>
                    </article>
                  </div>
                </div>
              </section>
            </div>
            """,
        )

        items = list(MangaNewsPopulairesSpider().parse(response))

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["category"], "Seinen")
        self.assertEqual(item["category_desc"], "Des intrigues plus complexes.")
        self.assertEqual(item["serie_slug"], "20th-century-boys")
        self.assertEqual(item["volumes_count"], 22)
        self.assertEqual(
            item["image_url"], "https://www.manga-news.com/images/20cb.jpg"
        )

    def test_missing_popular_blocks_is_not_a_silent_success(self):
        response = html_response(
            "https://www.manga-news.com/index.php/manga-populaires",
            "<html><h1>Nouvelle structure</h1></html>",
        )

        with self.assertRaises(CloseSpider) as raised:
            list(MangaNewsPopulairesSpider().parse(response))

        self.assertEqual(
            raised.exception.reason,
            "manganews_structure_changed_populaires",
        )


if __name__ == "__main__":
    unittest.main()
