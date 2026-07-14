import re

import scrapy

from ..items import ReviewItem, VolumeItem
from ._access import MangaSanctuaryAccessGuard


class MangaSanctuaryVolumesSpider(MangaSanctuaryAccessGuard, scrapy.Spider):
    name = "manga_sanctuary_volumes"
    allowed_domains = ["manga-sanctuary.com", "www.manga-sanctuary.com"]

    # ------------------------------------------------------------------ #
    #  START REQUESTS : SCRAP GLOBAL (# + A-Z)
    # ------------------------------------------------------------------ #
    def start_requests(self):
        """
        Pages d’index :
        - # : https://www.manga-sanctuary.com/bdd/series.html
        - A : https://www.manga-sanctuary.com/bdd/series-lettre-A.html
        - ...
        - Z : https://www.manga-sanctuary.com/bdd/series-lettre-Z.html
        """
        # Lettre spéciale "#"
        yield scrapy.Request(
            "https://www.manga-sanctuary.com/bdd/series.html",
            callback=self.parse_index,
            cb_kwargs={"letter": "#"},
        )

        # Lettres A à Z
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            url = f"https://www.manga-sanctuary.com/bdd/series-lettre-{letter}.html"
            yield scrapy.Request(
                url,
                callback=self.parse_index,
                cb_kwargs={"letter": letter},
            )

    # --------------------------------------------------------------- #
    # Helper : extraction d'un synopsis (série OU tome)
    # --------------------------------------------------------------- #
    def extract_synopsis(self, response):
        """
        Extraction générique du synopsis :

        1) On tente d'abord :
           - le premier <p> avec style contenant "text-align:justify"
        2) Si rien, fallback plus large :
           - n'importe quel <p> avec "text-align" et "justify"
        3) On concatène tous les textes (y compris ceux séparés par <br>).
        """

        # 1) Premier essai : style text-align:justify
        texts = response.xpath(
            "//p[contains(@style, 'text-align:justify')][1]//text()"
        ).getall()

        # 2) Fallback : tout <p> avec text-align ET justify si rien trouvé
        if not texts:
            texts = response.xpath(
                "//p[contains(@style, 'text-align') and contains(@style, 'justify')][1]//text()"  # noqa: E501
            ).getall()

        if not texts:
            return None

        synopsis = " ".join(t.strip() for t in texts if t.strip())
        synopsis = re.sub(r"\s+", " ", synopsis).strip()
        return synopsis or None

    # ------------------------------------------------------------------ #
    #  INDEX DES SÉRIES
    # ------------------------------------------------------------------ #
    def parse_index(self, response, letter):
        """
        Page liste de séries (par lettre).
        On prend les liens vers toutes les fiches /bdd/... de type
        manga / manhwa / manhua / bd / comics.
        """
        self.ensure_access(response)
        self.logger.info(f"[INDEX] Lettre {letter} -> {response.url}")

        for href in response.css("a[href^='/bdd/']::attr(href)").getall():
            # Filtrage des types d’œuvres.
            if not any(
                t in href
                for t in (
                    "/bdd/manga/",
                    "/bdd/manhwa/",
                    "/bdd/manhua/",
                    "/bdd/bd/",
                    "/bdd/comics/",
                )
            ):
                continue

            url = response.urljoin(href)
            yield scrapy.Request(
                url,
                callback=self.parse_series,
                cb_kwargs={"letter": letter},
            )

    # ------------------------------------------------------------------ #
    #  FICHE SÉRIE
    # ------------------------------------------------------------------ #
    def parse_series(self, response, letter):
        """
        Fiche série :
        - Autres titres
        - Type, catégorie, année
        - Dessinateur, scénariste
        - Genres, tags
        - Statuts / nb tomes par pays (FR/JP, etc.)
        - Popularité
        - Notes membres / experts (série)
        - Synopsis (série)
        - Oeuvres liées
        Puis on suit toutes les URLs des tomes (liens contenant '-vol-').
        """

        self.ensure_access(response)

        series_url = response.url
        series_title = response.css("h1::text").get(default="").strip()
        series_synopsis = self.extract_synopsis(response)

        # ID de la série (ex : /bdd/manga/38583-my-hero-academia/ -> 38583)
        m = re.search(r"/(\d+)-", series_url)
        series_id = m.group(1) if m else None

        # ---------- Autres titres ----------
        # Le bloc d'infos est un <ul> de <li><span>LABEL</span><span>VALEUR</span></li>.
        # Les alias au-delà du premier occupent des <li> frères dont le span de
        # label est VIDE : le test porte donc sur span[1], car une ligne de
        # continuation a bien un span non vide — celui de la valeur.
        # L'intersection Kayessian (count(A | B) = 1 <=> A et B sont le même
        # noeud) exige que la ligne étiquetée la plus proche en amont soit
        # « Autres titres » : c'est elle qui borne la collecte au bloc alias et
        # empêche de déborder sur « Type ».
        ancre_alias = "//li[normalize-space(span[1])='Autres titres']"
        other_titles = response.xpath(
            f"{ancre_alias}/span[2]//text()"
            f" | {ancre_alias}/following-sibling::li["
            "not(normalize-space(span[1]))"
            f" and count(preceding-sibling::li[normalize-space(span[1])][1]"
            f" | {ancre_alias}) = 1"
            "]/span[2]//text()"
        ).getall()
        other_titles = [t.strip() for t in other_titles if t.strip()]

        # Helper pour récupérer un champ texte juste après un label
        def extract_after(label, a_tag=True):
            if a_tag:
                return response.xpath(
                    f"normalize-space(//text()[contains(., '{label}')]/following::a[1]/text())"  # noqa: E501
                ).get()
            else:
                return response.xpath(
                    f"normalize-space(//text()[contains(., '{label}')]/following::text()[1])"  # noqa: E501
                ).get()

        series_type = extract_after("Type")
        series_category = extract_after("Catégorie")
        series_year = extract_after("Année", a_tag=False)
        series_dessinateur = extract_after("Dessinateur")
        series_scenariste = extract_after("Scénariste")

        # Genres — le parent des <a> est un <span>, jamais un <p>.
        series_genres = response.xpath(
            "//li[normalize-space(span[1])='Genres']/span[2]//a/text()"
        ).getall()
        series_genres = [g.strip() for g in series_genres if g.strip()]

        # Tags (si présents) — chaque <a> est encapsulé dans un <div>.
        series_tags = response.xpath(
            "//li[normalize-space(span[1])='Tags']/span[2]//a/text()"
        ).getall()
        # Le « # » est décoratif dans le libellé du lien.
        series_tags = [t.strip().lstrip("#") for t in series_tags if t.strip()]

        # Mag. prépub (optionnel)
        series_mag_prepub = extract_after("Mag. prépub.")

        # ---------- Statuts / tomes par pays (au niveau série) ----------
        series_statuses = []
        for span in response.css("span.badge-primary"):
            country = span.css("img::attr(alt)").get(default="")
            country = country.replace("drapeau", "").strip()
            text = "".join(span.css("::text").getall()).strip()
            text = text.replace(country, "").strip()
            entry = {
                "pays": country or None,
                "statut": text or None,
                "tomes": None,
            }
            series_statuses.append(entry)

        # nb de tomes global (ex. "42 tomes")
        tomes_text = response.xpath(
            "normalize-space(//span[contains(@class, 'badge-primary')]/following::text()[contains(., 'tome')][1])"  # noqa: E501
        ).get()
        if series_statuses and tomes_text:
            series_statuses[0]["tomes"] = tomes_text.strip()

        # ---------- Popularité ----------
        popularity_raw = response.css("a[href='/popularite.php'] span::text").re_first(
            r"(\d+)"
        )
        series_popularity_rank = int(popularity_raw) if popularity_raw else None

        # ---------- Notes série (membres / experts) ----------
        def note_bloc(label):
            """
            Extrait une note et un nb de votes pour "Les membres" / "Les experts"
            de façon robuste (pas de ValueError).
            """
            base = (
                f"//text()[contains(., '{label}')]/following::text()[normalize-space()]"
            )
            rating_text = response.xpath(base + "[1]").re_first(r"[-\d\.,/]+")
            votes_text = response.xpath(base + "[2]").re_first(r"\d+")

            rating_val = None
            if rating_text:
                r = rating_text.strip()
                if r and r != "-":
                    # ex: "7,5/10" -> "7,5"
                    r = re.split(r"[^\d,\.]+", r)[0]
                    try:
                        rating_val = float(r.replace(",", "."))
                    except ValueError:
                        rating_val = None

            votes_val = None
            if votes_text:
                try:
                    votes_val = int(votes_text)
                except ValueError:
                    votes_val = None

            return rating_val, votes_val

        series_members_rating, series_members_votes = note_bloc("Les membres")
        series_experts_rating, series_experts_votes = note_bloc("Les experts")

        # ---------- Oeuvres liées ----------
        related_works = []
        for h6 in response.xpath("//h5[contains(., 'Oeuvres liées')]/following::h6[a]"):
            title = h6.xpath("normalize-space(a/text())").get()
            url = h6.xpath("a/@href").get()
            if not url:
                continue

            url = response.urljoin(url)

            # On ignore les liens de type /news/... (tops hebdo, articles, etc.)
            if "/news/" in url:
                continue

            type_year_text = h6.xpath(
                "following-sibling::text()[normalize-space()][1]"
            ).get()
            work_type = None
            work_year = None
            if type_year_text:
                type_year_text = type_year_text.strip()
                m_ty = re.search(r"(.+?)\((\d{4})\)", type_year_text)
                if m_ty:
                    work_type = m_ty.group(1).strip()
                    work_year = int(m_ty.group(2))
                else:
                    work_type = type_year_text

            related_works.append(
                {
                    "title": title,
                    "type": work_type,
                    "year": work_year,
                    "url": url,
                }
            )

        # ---------- Meta série à transmettre aux tomes ----------
        series_meta = {
            "series_id": series_id,
            "series_url": series_url,
            "series_title": series_title,
            "series_type": series_type,
            "series_category": series_category,
            "series_year": series_year,
            "series_other_titles": other_titles,
            "series_dessinateur": series_dessinateur,
            "series_scenariste": series_scenariste,
            "series_genres": series_genres,
            "series_tags": series_tags,
            "series_mag_prepub": series_mag_prepub,
            "series_statuses": series_statuses,
            "series_popularity_rank": series_popularity_rank,
            "series_members_rating": series_members_rating,
            "series_members_votes": series_members_votes,
            "series_experts_rating": series_experts_rating,
            "series_experts_votes": series_experts_votes,
            "series_synopsis": series_synopsis,
            "series_related_works": related_works,
        }

        # ---------- Suivre tous les tomes (toutes éditions) ----------
        for href in response.css("a[href*='-vol-']::attr(href)").getall():
            volume_url = response.urljoin(href)
            yield scrapy.Request(
                volume_url,
                callback=self.parse_volume,
                cb_kwargs={"series_meta": series_meta},
            )

    # ------------------------------------------------------------------ #
    #  FICHE TOME
    # ------------------------------------------------------------------ #
    def parse_volume(self, response, series_meta):
        """
        Fiche tome :
        - URL, titre, numéro
        - date de parution
        - dessinateur, scénariste (édition)
        - éditeur
        - format, nombre de pages
        - statut, nb de tomes pour cette édition
        - notes membres / experts du tome
        - synopsis tome (si présent)
        plus toutes les infos "série" transmises via series_meta.
        """
        self.ensure_access(response)

        item = VolumeItem()

        # ---- Reporter toutes les infos série dans l'item volume ----
        for key, value in series_meta.items():
            if key in item.fields:
                item[key] = value

        # ---- Infos tome spécifiques ----
        item["volume_url"] = response.url

        volume_title = response.css("h1::text").get(default="").strip()
        item["volume_title"] = volume_title

        # Numéro de tome (match simple sur un nombre dans le titre)
        num = re.search(r"\b(\d+)\b", volume_title)
        volume_number = int(num.group(1)) if num else None
        item["volume_number"] = volume_number

        # Date de parution
        item["volume_publication_date"] = response.xpath(
            "normalize-space(//text()[contains(., 'Date parution')]/following::text()[1])"  # noqa: E501
        ).get()

        # Dessinateur / Scénariste spécifiques à l'édition (souvent identiques)
        item["volume_dessinateur"] = response.xpath(
            "normalize-space(//text()[contains(., 'Dessinateur')]/following::a[1]/text())"  # noqa: E501
        ).get()
        item["volume_scenariste"] = response.xpath(
            "normalize-space(//text()[contains(., 'Scénariste')]/following::a[1]/text())"  # noqa: E501
        ).get()

        # Éditeur
        item["volume_editeur"] = response.xpath(
            "normalize-space(//text()[contains(., 'Editeur')]/following::a[1]/text())"
        ).get()

        # EAN-13 : seule source de la page (ni ISBN, ni <meta>, ni JSON-LD) et
        # absent d'environ 40 à 50 % des fiches — le repli de jointure avec
        # Manga Insight reste donc nécessaire.
        ean = response.xpath(
            "//li[normalize-space(span[1])='EAN-13']/span[2]/text()"
        ).get()
        item["volume_ean"] = ean.strip() if ean else None

        # Format
        item["volume_format"] = response.xpath(
            "normalize-space(//text()[contains(., 'Format')]/following::text()[1])"
        ).get()

        # Pages
        pages_text = response.xpath(
            "normalize-space(//text()[contains(., 'Pages')]/following::text()[1])"
        ).get()
        if pages_text:
            m_pages = re.search(r"(\d+)", pages_text)
            item["volume_pages"] = int(m_pages.group(1)) if m_pages else None

        # Statut édition + pays (badge primaire sur la fiche tome)
        badge = response.css("span.badge-primary")
        if badge:
            country = badge.css("img::attr(alt)").get(default="")
            country = country.replace("drapeau", "").strip()
            status_text = "".join(badge.css("::text").getall()).strip()
            status_text = status_text.replace(country, "").strip()
            item["volume_country"] = country or None
            item["volume_status"] = status_text or None

        # "2 tomes (sur 2)" pour cette édition
        tomes_edition = response.xpath(
            "normalize-space(//span[contains(@class,'badge-primary')]/following::text()[contains(., 'tome')][1])"  # noqa: E501
        ).get()
        if tomes_edition:
            m1 = re.search(r"(\d+)\s+tome", tomes_edition)
            if m1:
                item["volume_tomes_published"] = int(m1.group(1))
            m2 = re.search(r"sur\s+(\d+)", tomes_edition)
            if m2:
                item["volume_tomes_total"] = int(m2.group(1))
            else:
                item["volume_tomes_total"] = item.get("volume_tomes_published")

        # Notes tome (membres / experts) – version robuste
        def note_bloc(label):
            base = (
                f"//text()[contains(., '{label}')]/following::text()[normalize-space()]"
            )
            rating_text = response.xpath(base + "[1]").re_first(r"[-\d\.,/]+")
            votes_text = response.xpath(base + "[2]").re_first(r"\d+")

            rating_val = None
            if rating_text:
                r = rating_text.strip()
                if r and r != "-":
                    r = re.split(r"[^\d,\.]+", r)[0]
                    try:
                        rating_val = float(r.replace(",", "."))
                    except ValueError:
                        rating_val = None

            votes_val = None
            if votes_text:
                try:
                    votes_val = int(votes_text)
                except ValueError:
                    votes_val = None

            return rating_val, votes_val

        volume_members_rating, volume_members_votes = note_bloc("Les membres")
        volume_experts_rating, volume_experts_votes = note_bloc("Les experts")

        item["volume_members_rating"] = volume_members_rating
        item["volume_members_votes"] = volume_members_votes
        item["volume_experts_rating"] = volume_experts_rating
        item["volume_experts_votes"] = volume_experts_votes

        # Synopsis du tome : on tente d'abord l'heuristique générale…
        volume_synopsis = self.extract_synopsis(response)
        if not volume_synopsis:
            # … puis fallback : paragraphe juste avant "Les tomes de cette édition"
            volume_synopsis = response.xpath(
                "normalize-space(//h5[contains(., 'Les tomes de cette édition')]/preceding::p[1]/text())"  # noqa: E501
            ).get()
        item["volume_synopsis"] = volume_synopsis

        # On yield d'abord l'item volume
        yield item

        # ---------- Bloc critique staff spécifique à ce tome (si présent) ----------
        # Sur une page tome, on a un bloc "Critiques du staff sur ce tome"
        # ou "Dernières critiques du staff" avec un lien vers
        # /fiche_serie_critique.php?id=...
        review_href = response.xpath(
            "//h5[contains(., 'Critiques du staff sur ce tome') "
            "or contains(., 'Dernières critiques du staff')]/"
            "following::a[contains(@href, 'fiche_serie_critique.php')][1]/@href"
        ).get()

        if review_href:
            review_url = response.urljoin(review_href)
            yield scrapy.Request(
                review_url,
                callback=self.parse_staff_review,
                cb_kwargs={
                    "series_meta": series_meta,
                    "volume_number": volume_number,
                    "volume_url": item["volume_url"],
                },
            )

    # ------------------------------------------------------------------ #
    #  FICHE CRITIQUE STAFF (LIÉE À UN TOME)
    # ------------------------------------------------------------------ #
    def parse_staff_review(self, response, series_meta, volume_number, volume_url):
        """
        Page de critique staff complète :
        - titre de la critique
        - score
        - auteur
        - date
        - texte intégral (tous les <p> et sous-noeuds dans le bloc de critique)
        Liée à une série + un tome (volume_number / volume_url).
        """
        self.ensure_access(response)

        review = ReviewItem()

        # Contexte série
        review["series_id"] = series_meta.get("series_id")
        review["series_title"] = series_meta.get("series_title")
        review["series_url"] = series_meta.get("series_url")

        # Contexte tome
        review["volume_number"] = volume_number
        review["volume_url"] = volume_url

        review["review_url"] = response.url

        # Titre de la critique
        review["review_title"] = response.css("h1::text").get(default="").strip()

        # Score (span itemprop='ratingValue')
        score_text = response.css("span[itemprop='ratingValue']::text").get()
        if score_text:
            try:
                review["review_score"] = float(score_text.replace(",", "."))
            except ValueError:
                review["review_score"] = None
        else:
            review["review_score"] = None

        # Auteur + date dans le paragraphe "par XXX le ..."
        meta_p = "".join(
            response.xpath("//p[contains(., 'par ')][1]//text()").getall()
        ).strip()

        author = response.css("p a[href*='membre.php']::text").get()
        date_str = None

        # On essaie de capturer ce qui suit "le " jusqu'à une grosse séparation
        # (pour éviter d'englober "Staff" etc.)
        m = re.search(r"le\s+(.+)", meta_p)
        if m:
            raw_date = m.group(1).strip()
            # On coupe si on trouve un séparateur typique (tabs / gros espaces)
            date_str = re.split(r"\s{2,}", raw_date)[0].strip()

        review["review_author"] = author
        review["review_date"] = date_str

        # Type de critique : Staff (badge)
        badge = response.css("span.badge-outline-primary::text").get()
        review["review_type"] = badge.strip() if badge else "Staff"

        # Texte intégral de la critique :
        # on prend tous les textes dans le bloc de critique, sans les images.
        # Deux structures coexistent : les critiques récentes sont en <p>, les
        # anciennes en texte brut séparé par des <br> (aucun <p>). Exiger //p
        # perdait donc l'intégralité de ces dernières.
        body_parts = response.xpath(
            "//div[contains(@class,'post-single') "
            "and contains(@class,'text-justify')]//text()"
        ).getall()
        review["review_body"] = " ".join(p.strip() for p in body_parts if p.strip())

        yield review
