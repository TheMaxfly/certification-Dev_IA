import re
import scrapy

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


class MangaNewsPopulairesSpider(scrapy.Spider):
    name = "manganews_populaires"
    allowed_domains = ["www.manga-news.com"]
    start_urls = ["https://www.manga-news.com/index.php/manga-populaires"]

    custom_settings = {
        # Rapide + léger
        "DOWNLOAD_DELAY": 0.1,
        "CONCURRENT_REQUESTS": 4,
        "ROBOTSTXT_OBEY": True,
    }

    def parse(self, response):
        for block in response.css('#best-blocks .boxed.entries[id^="best-block-"]'):
            category = (block.css("h3::text").get() or "").strip()
            category_desc = " ".join(t.strip() for t in block.css(".rounded-box-content *::text").getall()).strip()

            items = block.css(".section-list .section-list-item")
            for i, it in enumerate(items, start=1):
                a = it.css("a.section-list-item-img")
                url = a.attrib.get("href")
                title = (a.attrib.get("title") or it.css(".section-list-item-title::text").get() or "").strip()
                image_url = it.css("img.entryPicture::attr(src)").get()
                volumes_text = (it.css("span.catIcon::text").get() or "").strip()
                volumes_count = parse_int_first(volumes_text)

                yield {
                    "source": "manga_news",
                    "collection": "populaires",
                    "category": category or None,
                    "category_desc": category_desc or None,
                    "rank_in_category": i,
                    "title": title or None,
                    "serie_url": response.urljoin(url) if url else None,
                    "serie_slug": slug_from_serie_url(url) if url else None,
                    "image_url": response.urljoin(image_url) if image_url else None,
                    "volumes_text": volumes_text or None,
                    "volumes_count": volumes_count,
                }
