import os

BOT_NAME = "manga_news_scraper"

SPIDER_MODULES = ["manga_news_scraper.spiders"]
NEWSPIDER_MODULE = "manga_news_scraper.spiders"

ROBOTSTXT_OBEY = True

# Ne pas usurper un navigateur. En cas d'autorisation explicite de Manga-News,
# définir un User-Agent identifiable avec une adresse de contact.
USER_AGENT = os.getenv("MANGANEWS_USER_AGENT", "manga-news-scraper/0.2")

# Politeness + stabilité
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 30.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 0.5

CONCURRENT_REQUESTS = 4
CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_DELAY = 1.0
RANDOMIZE_DOWNLOAD_DELAY = True
RETRY_TIMES = 2
DOWNLOAD_TIMEOUT = 30
COOKIES_ENABLED = False
TELNETCONSOLE_ENABLED = False

EXTENSIONS = {
    "manga_news_scraper.extensions.RunStatusExtension": 10,
}


# Respecter l’ordre des priorités
DEPTH_PRIORITY = 1
SCHEDULER_DISK_QUEUE = "scrapy.squeues.PickleFifoDiskQueue"
SCHEDULER_MEMORY_QUEUE = "scrapy.squeues.FifoMemoryQueue"

# JOBDIR n'est volontairement pas global : réutiliser un ancien dossier ferait
# ignorer les URLs déjà vues et produirait un faux rafraîchissement incomplet.
# Pour reprendre un crawl interrompu, passer un dossier neuf avec -s JOBDIR=...

# Exports
FEED_EXPORT_ENCODING = "utf-8"


def feed_uri_params(params, spider):
    # Map spider -> fixed output filename.
    name_map = {
        "manganews_series": "manganews_series",
        "manganews_populaires": "populaires",
    }
    params["feed_name"] = name_map.get(spider.name, spider.name)
    return params


FEED_URI_PARAMS = feed_uri_params
# Aucun export implicite : un crawl bloqué ne doit jamais écraser le dernier jeu
# valide avec un fichier vide. Utiliser scripts/run_scrape.py pour la promotion
# atomique ou fournir explicitement -O pour un export de diagnostic.

ITEM_PIPELINES = {
    "manga_news_scraper.pipelines.EnrichPipeline": 100,
    #    "manga_news_scraper.pipelines.MangaNewsPostgresPipeline": 300,
}

POSTGRES_DSN = os.getenv("POSTGRES_DSN") or os.getenv("APIMANGA_DSN")
PG_BATCH_SIZE = 200
