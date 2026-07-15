import re

import scrapy
from scrapy.exceptions import CloseSpider

from manga_news_scraper.spiders._access import MangaNewsAccessGuard


def parse_int_first(text: str):
    if not text:
        return None
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def slug_from_serie_url(url: str):
    # ex: https://www.manga-news.com/index.php/serie/Kingdom -> "Kingdom"
    if not url:
        return None
    m = re.search(r"/index\.php/serie/([^/?#]+)", url)
    return m.group(1) if m else None


def clean_text(parts):
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return re.sub(r"\s+", " ", text).strip() or None


class MangaNewsPopulairesSpider(MangaNewsAccessGuard, scrapy.Spider):
    name = "manganews_populaires"
    allowed_domains = ["www.manga-news.com"]
    start_urls = ["https://www.manga-news.com/index.php/manga-populaires"]

    def parse(self, response):
        self.ensure_access(response)

        blocks = response.css('#best-blocks .boxed.entries[id^="best-block-"]')
        if not blocks:
            crawler = getattr(self, "crawler", None)
            if crawler is not None:
                crawler.stats.inc_value("manganews/populaires_structure_errors")
            self.logger.error(
                "Aucun bloc populaire trouvé sur %s : structure à vérifier.",
                response.url,
            )
            raise CloseSpider(reason="manganews_structure_changed_populaires")

        for block in blocks:
            category = (block.css("h3::text").get() or "").strip()
            content = block.css(".rounded-box-content")
            category_desc = clean_text(
                content.xpath(
                    ".//text()[not(ancestor::*[contains(concat(' ', "
                    "normalize-space(@class), ' '), ' section-list ')])]"
                ).getall()
            )

            items = block.css(".section-list .section-list-item")
            if not items:
                self.logger.warning("Catégorie populaire vide: %s", category)
            for i, it in enumerate(items, start=1):
                a = it.css("a.section-list-item-img")
                url = a.attrib.get("href")
                title = (
                    a.attrib.get("title")
                    or clean_text(
                        it.css(
                            ".section-list-item-title::text, "
                            ".section-list-item-title *::text"
                        ).getall()
                    )
                    or ""
                ).strip()
                image_url = (
                    it.css("img.entryPicture::attr(src)").get()
                    or it.css("img.entryPicture::attr(data-src)").get()
                    or it.css("img.entryPicture::attr(data-original)").get()
                )
                volumes_text = clean_text(
                    it.css("span.catIcon::text, span.catIcon *::text").getall()
                )
                volumes_count = parse_int_first(volumes_text)

                yield {
                    "source": "manga_news",
                    "collection": "populaires",
                    "category": category or None,
                    "category_desc": category_desc,
                    "rank_in_category": i,
                    "title": title or None,
                    "serie_url": response.urljoin(url) if url else None,
                    "serie_slug": slug_from_serie_url(url) if url else None,
                    "image_url": response.urljoin(image_url) if image_url else None,
                    "volumes_text": volumes_text,
                    "volumes_count": volumes_count,
                }
