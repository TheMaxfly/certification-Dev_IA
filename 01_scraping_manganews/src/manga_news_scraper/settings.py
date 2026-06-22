BOT_NAME = "manga_news_scraper"

SPIDER_MODULES = ["manga_news_scraper.spiders"]
NEWSPIDER_MODULE = "manga_news_scraper.spiders"

ROBOTSTXT_OBEY = True

# User-Agent "navigateur" (réduit les 403)
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Politeness + stabilité
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 0.5
AUTOTHROTTLE_MAX_DELAY = 15.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0  # important: lisse le débit

CONCURRENT_REQUESTS = 16               # ok si target_concurrency = 1.0
CONCURRENT_REQUESTS_PER_DOMAIN = 4     # limite les rafales sur manga-news
DOWNLOAD_DELAY = 0.2                   # léger gain
RANDOMIZE_DOWNLOAD_DELAY = True
RETRY_TIMES = 6
DOWNLOAD_TIMEOUT = 30


# Respecter l’ordre des priorités
DEPTH_PRIORITY = 1
SCHEDULER_DISK_QUEUE = "scrapy.squeues.PickleFifoDiskQueue"
SCHEDULER_MEMORY_QUEUE = "scrapy.squeues.FifoMemoryQueue"

# Reprise après crash (à activer si tu veux pouvoir reprendre)
# Remplace le chemin par un dossier existant
JOBDIR = "job_state/manganews_series"

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
FEEDS = {
    "data/enriched/%(feed_name)s.jsonl": {
        "format": "jsonlines",
        "overwrite": True,
    },
}

ITEM_PIPELINES = {
    "manga_news_scraper.pipelines.EnrichPipeline": 100,
#    "manga_news_scraper.pipelines.MangaNewsPostgresPipeline": 300,
}

POSTGRES_DSN = "dbname=apimanga user=postgres password=postgres host=127.0.0.1 port=5432"
PG_BATCH_SIZE = 200
