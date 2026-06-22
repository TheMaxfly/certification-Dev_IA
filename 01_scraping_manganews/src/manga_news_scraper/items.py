import scrapy

class MangaNewsSeriesItem(scrapy.Item):
    url = scrapy.Field()
    title_page = scrapy.Field()

    titre_vo = scrapy.Field()
    titre_traduit = scrapy.Field()

    dessin = scrapy.Field()
    dessin_url = scrapy.Field()
    scenario = scrapy.Field()
    scenario_url = scrapy.Field()
    traducteur = scrapy.Field()
    traducteur_url = scrapy.Field()

    editeur_vf = scrapy.Field()
    editeur_vf_url = scrapy.Field()
    collection = scrapy.Field()
    collection_url = scrapy.Field()

    type = scrapy.Field()
    type_url = scrapy.Field()

    genres = scrapy.Field()
    genres_urls = scrapy.Field()

    editeur_vo = scrapy.Field()
    editeur_vo_url = scrapy.Field()
    prepublication = scrapy.Field()
    prepublication_url = scrapy.Field()

    illustration = scrapy.Field()
    origine = scrapy.Field()

    resume = scrapy.Field()
    points_forts = scrapy.Field()

    related_news = scrapy.Field()
    rag_text = scrapy.Field()

