# Scrapy settings for manga_sanctuary project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html

import os

BOT_NAME = "manga_sanctuary"

SPIDER_MODULES = ["manga_sanctuary.spiders"]
NEWSPIDER_MODULE = "manga_sanctuary.spiders"

ADDONS = {}


# User-Agent identifiable du projet : ne jamais crawler sous l'UA Scrapy par
# défaut. Même convention que le module 01 (surchargeable par variable
# d'environnement). Le robots.txt du site autorise /bdd/ pour le groupe « * »,
# dont relève cet UA.
USER_AGENT = os.getenv("MANGA_SANCTUARY_USER_AGENT", "manga-sanctuary-scraper/0.1")

# Obey robots.txt rules
ROBOTSTXT_OBEY = True

# Concurrency and throttling settings
CONCURRENT_REQUESTS = 12
CONCURRENT_REQUESTS_PER_DOMAIN = 4
DOWNLOAD_DELAY = 0.25

# Disable cookies (enabled by default)
COOKIES_ENABLED = False

# Disable Telnet Console (enabled by default)
# TELNETCONSOLE_ENABLED = False

# Override the default request headers:
# DEFAULT_REQUEST_HEADERS = {
#    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
#    "Accept-Language": "en",
# }

# Enable or disable spider middlewares
# See https://docs.scrapy.org/en/latest/topics/spider-middleware.html
# SPIDER_MIDDLEWARES = {
#    "manga_sanctuary.middlewares.MangaSanctuarySpiderMiddleware": 543,
# }

# Enable or disable downloader middlewares
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
# DOWNLOADER_MIDDLEWARES = {
#    "manga_sanctuary.middlewares.MangaSanctuaryDownloaderMiddleware": 543,
# }

# Enable or disable extensions
# See https://docs.scrapy.org/en/latest/topics/extensions.html
EXTENSIONS = {
    "manga_sanctuary.extensions.RunStatusExtension": 10,
}

# Configure item pipelines
# See https://docs.scrapy.org/en/latest/topics/item-pipeline.html
ITEM_PIPELINES = {
    "manga_sanctuary.pipelines.CleanAndTypePipeline": 200,
}

# Enable and configure the AutoThrottle extension (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/autothrottle.html
AUTOTHROTTLE_ENABLED = True
# The initial download delay
AUTOTHROTTLE_START_DELAY = 0.25
# The maximum download delay to be set in case of high latencies
AUTOTHROTTLE_MAX_DELAY = 15
# The average number of requests Scrapy should be sending in parallel to
# each remote server
AUTOTHROTTLE_TARGET_CONCURRENCY = 2
# Enable showing throttling stats for every response received:
# AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html#httpcache-middleware-settings
# HTTPCACHE_ENABLED = True
# HTTPCACHE_EXPIRATION_SECS = 0
# HTTPCACHE_DIR = "httpcache"
# HTTPCACHE_IGNORE_HTTP_CODES = []
# HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.FilesystemCacheStorage"

# Set settings whose default value is deprecated to a future-proof value

RETRY_ENABLED = True
RETRY_TIMES = 2
RETRY_HTTP_CODES = [500, 502, 503, 504, 522, 524, 408]


# JOBDIR n'est volontairement pas global : un dossier partagé entre deux crawls
# ferait ignorer les URLs déjà vues et produirait un faux rafraîchissement
# incomplet. scripts/run_scrape.py en donne un par run — neuf pour un crawl
# neuf, et réutilisé par ses reprises, la file étant la seule mémoire de ce qui
# a déjà été demandé. Reprendre avec un JOBDIR vide re-crawlerait l'acquis.

# Exports
FEED_EXPORT_ENCODING = "utf-8"

# Aucun export implicite : un crawl bloqué ne doit jamais écraser le dernier jeu
# valide avec un fichier partiel. Les deux flux d'items (VolumeItem, ReviewItem)
# sont routés par scripts/run_scrape.py, qui exporte d'abord dans un dossier de
# run puis promeut atomiquement vers data/raw/<AAAA-MM>/ après validation.
# Pour un export de diagnostic, fournir explicitement -O.
