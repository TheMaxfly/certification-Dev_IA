# manga_sanctuary/items.py
import scrapy


class VolumeItem(scrapy.Item):
    # ----------- Contexte série ----------- #
    series_id = scrapy.Field()
    series_url = scrapy.Field()
    series_title = scrapy.Field()
    series_type = scrapy.Field()  # Manga, Manhwa, Comics, etc.
    series_category = scrapy.Field()  # Shonen, Shojo, Seinen, etc.
    series_year = scrapy.Field()
    series_other_titles = scrapy.Field()  # liste de titres (JP, romaji, FR, etc.)
    series_dessinateur = scrapy.Field()
    series_scenariste = scrapy.Field()
    series_genres = scrapy.Field()  # liste
    series_tags = scrapy.Field()  # liste éventuelle
    series_mag_prepub = scrapy.Field()

    # Oeuvres liées
    # ex. [{"title": "...", "type": "Manga", "year": 2008, "url": "..."}]
    series_related_works = scrapy.Field()

    # Statuts / tomes par pays au niveau série
    series_statuses = (
        scrapy.Field()
    )  # ex. [{"pays": "français", "statut": "Complète", "tomes": "42 tomes"}]
    series_popularity_rank = scrapy.Field()  # 6985, etc.
    series_members_rating = scrapy.Field()  # 8.72
    series_members_votes = scrapy.Field()  # 893
    series_experts_rating = scrapy.Field()  # 7.88
    series_experts_votes = scrapy.Field()  # 64

    series_synopsis = scrapy.Field()

    # ----------- Infos tome / édition ----------- #
    volume_url = scrapy.Field()
    volume_title = scrapy.Field()
    volume_number = scrapy.Field()  # 1, 2, ...
    volume_publication_date = scrapy.Field()
    volume_dessinateur = scrapy.Field()
    volume_scenariste = scrapy.Field()
    volume_editeur = scrapy.Field()
    volume_format = scrapy.Field()
    volume_pages = scrapy.Field()

    volume_country = scrapy.Field()  # pays de l’édition (drapeau)
    volume_status = scrapy.Field()  # Complète / En cours
    volume_tomes_published = scrapy.Field()  # 42
    volume_tomes_total = scrapy.Field()  # 42

    # Notes tome
    volume_members_rating = scrapy.Field()
    volume_members_votes = scrapy.Field()
    volume_experts_rating = scrapy.Field()
    volume_experts_votes = scrapy.Field()

    # Texte synopsis de cette page tome
    volume_synopsis = scrapy.Field()


class ReviewItem(scrapy.Item):
    """
    Critique staff complète, liée à un TOME précis.
    """

    # -------- Contexte série --------
    series_id = scrapy.Field()
    series_title = scrapy.Field()
    series_url = scrapy.Field()

    # -------- Contexte tome --------
    volume_number = scrapy.Field()
    volume_url = scrapy.Field()
    volume_title = scrapy.Field()  # <-- pratique pour le debug / jointures humaines

    # -------- Critique --------
    review_url = scrapy.Field()
    review_title = scrapy.Field()
    review_score = scrapy.Field()  # float (géré dans le pipeline)
    review_author = scrapy.Field()
    review_date = scrapy.Field()
    review_type = scrapy.Field()  # ex. "Staff"
    review_body = scrapy.Field()  # texte intégral de la critique (tous les paragraphes)
