import re
import string
import scrapy


SERIES_HUB_URL = "https://www.manga-news.com/index.php/series/?public="

# --- Helpers de nettoyage ---
def clean_text_list(parts):
    txt = " ".join(p.strip() for p in parts if p and p.strip())
    return re.sub(r"\s+", " ", txt).strip() or None

def clean_colon_prefix(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"^\s*:\s*", "", s.strip())
    return s or None

def is_alpha_page(url: str) -> bool:
    """
    Pages de listing:
      - https://www.manga-news.com/index.php/series/        (#)
      - https://www.manga-news.com/index.php/series/A..Z
    """
    u = url.rstrip("/")
    return bool(re.match(r"^https://www\.manga-news\.com/index\.php/series(?:/[A-Z])?$", u))

def is_series_detail_url(url: str) -> bool:
    """
    Les fiches séries sont souvent en:
      - /index.php/serie/<slug>   (singulier)
    Parfois on peut aussi rencontrer:
      - /index.php/series/<slug>  (pluriel)
    On accepte les deux, et on exclut /series/A..Z et /series/ (listing).
    """
    u = url.rstrip("/")

    # listing root / A..Z => EXCLU
    if re.match(r"^https://www\.manga-news\.com/index\.php/series(?:/[A-Z])?$", u):
        return False

    # fiche singulier
    if re.match(r"^https://www\.manga-news\.com/index\.php/serie/[^/]+$", u):
        return True

    # fiche pluriel (au cas où)
    if re.match(r"^https://www\.manga-news\.com/index\.php/series/[^/]+$", u):
        # exclure /series/A..Z au cas où (déjà géré plus haut)
        tail = u.split("/")[-1]
        if tail in string.ascii_uppercase:
            return False
        return True

    return False


class MangaNewsSeriesSpider(scrapy.Spider):
    """
    Spider intégral:
      - Start => page hub /series/?public= (récupère # + A..Z)
      - Pour chaque lettre => récupère URLs fiches + pagination
      - Pour chaque fiche => extrait top info + résumé + points forts
    """
    name = "manganews_series"
    allowed_domains = ["www.manga-news.com"]
    start_urls = [SERIES_HUB_URL]

    

    # ------------- 1) HUB (# + A..Z) -------------
    def parse(self, response):
        """
        Ici se fait la partie 'index' : on récupère les liens # + A..Z depuis ul.alphaLink
        puis on envoie ces pages vers parse_series_list().
        """
        alpha_hrefs = response.css("ul.alphaLink a::attr(href)").getall()
        if not alpha_hrefs:
            self.logger.warning("Aucun lien alphaLink trouvé sur %s", response.url)

        for href in alpha_hrefs:
            url = response.urljoin(href)
            if is_alpha_page(url):
                yield scrapy.Request(url, callback=self.parse_series_list)

        # (fallback) au cas où alphaLink est absent: on construit A..Z + #
        if not alpha_hrefs:
            base = "https://www.manga-news.com/index.php/series/"
            yield scrapy.Request(base, callback=self.parse_series_list)
            for letter in string.ascii_uppercase:
                yield scrapy.Request(base + letter, callback=self.parse_series_list)

    # ------------- 2) LISTING LETTRE -------------
    def parse_series_list(self, response):
        """
        Page listing (# ou lettre). Objectif:
          - extraire les URLs de fiches séries
          - suivre la pagination
        """
 
        # DEBUG: montre les liens "serie" visibles sur la page
        cands = []
        for href in response.css("a::attr(href)").getall():
            if href and ("serie" in href):
                cands.append(response.urljoin(href))

        self.logger.info("DEBUG %s : %d href contenant 'serie'. Exemples: %s",
                        response.url, len(cands), cands[:20])

        

        
        # A) extraction brute de tous les liens, puis filtre regex.
        #    (quand tu me donnes le HTML du tableau, on pourra cibler précisément le bon selector)
        found = 0
        for href in response.css("a::attr(href)").getall():
            url = response.urljoin(href)
            if is_series_detail_url(url):
                found += 1
                yield scrapy.Request(url, callback=self.parse_series_detail)

        if found == 0:
            self.logger.info("0 fiche trouvée sur %s (peut-être selector à affiner)", response.url)

        # B) pagination : 1) rel=next, 2) lien contenant "Suivant", 3) pagination numérique
        next_href = response.css("a[rel='next']::attr(href)").get()
        if not next_href:
            next_href = response.xpath("//a[contains(normalize-space(.), 'Suivant')]/@href").get()

        if next_href:
            yield response.follow(next_href, callback=self.parse_series_list)
            return

        # fallback pagination numérique (si présence d’un pager avec pages)
        # on suit la prochaine page si on détecte un paramètre p= ou page=
        pager_hrefs = response.css(".pagination a::attr(href), .pager a::attr(href)").getall()
        for href in pager_hrefs:
            if href and ("p=" in href or "page=" in href):
                # on les suit toutes (Scrapy déduplique automatiquement)
                yield response.follow(href, callback=self.parse_series_list)

    # ------------- 3) FICHE SERIE -------------
    def parse_series_detail(self, response):
        """
        Extraction des champs sur la fiche.
        Basée sur TON HTML (ul.entryInfos + résumé + points forts).
        """
        titre_vo = clean_colon_prefix(response.css("ul.entryInfos li.title-vo span.entry-data-wrapper::text").get())
        titre_traduit = clean_colon_prefix(response.css("ul.entryInfos li.trad span.entry-data-wrapper::text").get())

        dessin = response.css("ul.entryInfos li.book-by a::text").get()
        dessin_url = response.css("ul.entryInfos li.book-by a::attr(href)").get()

        scenario = response.css("ul.entryInfos li.book-by2 a::text").get()
        scenario_url = response.css("ul.entryInfos li.book-by2 a::attr(href)").get()

        traducteur = response.css("ul.entryInfos li.tradcuteur a::text").get()
        traducteur_url = response.css("ul.entryInfos li.tradcuteur a::attr(href)").get()

        editeur_vf = response.css("ul.entryInfos li.book-edit-vf a::text").get()
        editeur_vf_url = response.css("ul.entryInfos li.book-edit-vf a::attr(href)").get()

        collection = response.css("ul.entryInfos li.book-coll a::text").get()
        collection_url = response.css("ul.entryInfos li.book-coll a::attr(href)").get()

        type_ = response.css("ul.entryInfos li.book-type a::text").get()
        type_url = response.css("ul.entryInfos li.book-type a::attr(href)").get()

        genres = [g.strip() for g in response.css("ul.entryInfos li.book-genre a::text").getall() if g.strip()]
        genres_urls = response.css("ul.entryInfos li.book-genre a::attr(href)").getall()

        editeur_vo = response.css("ul.entryInfos li.book-edit-vo a::text").get()
        editeur_vo_url = response.css("ul.entryInfos li.book-edit-vo a::attr(href)").get()

        prepub = response.css("ul.entryInfos li.prepub a::text").get()
        prepub_url = response.css("ul.entryInfos li.prepub a::attr(href)").get()

        illust_raw = clean_text_list(response.css("ul.entryInfos li.illust::text").getall())
        origine_raw = clean_text_list(response.css("ul.entryInfos li.book-origin::text").getall())

        # Résumé : bloc "Résumé" -> sibling suivant (div.bigsize dans ton exemple)
        resume = clean_text_list(
            response.xpath("//h2[normalize-space()='Résumé']/following-sibling::*[1]//text()").getall()
        )

        # Points forts : id stable dans ton exemple
        points_forts = clean_text_list(response.css("#product-strong div.bigsize::text").getall())

        # Optionnel: dernières news (titres+urls)
        related_news = []
        for a in response.css("#product-related-news ul.content-box-list a"):
            related_news.append({
                "title": clean_text_list(a.css("::text").getall()),
                "url": response.urljoin(a.attrib.get("href", "")),
            })

        item = {
            "source": "manga_news",
            "url": response.url,
            "title_page": (response.css("h1::text").get() or "").strip() or None,

            "titre_vo": titre_vo,
            "titre_traduit": titre_traduit,

            "dessin": (dessin or "").strip() or None,
            "dessin_url": response.urljoin(dessin_url) if dessin_url else None,

            "scenario": (scenario or "").strip() or None,
            "scenario_url": response.urljoin(scenario_url) if scenario_url else None,

            "traducteur": (traducteur or "").strip() or None,
            "traducteur_url": response.urljoin(traducteur_url) if traducteur_url else None,

            "editeur_vf": (editeur_vf or "").strip() or None,
            "editeur_vf_url": response.urljoin(editeur_vf_url) if editeur_vf_url else None,

            "collection": (collection or "").strip() or None,
            "collection_url": response.urljoin(collection_url) if collection_url else None,

            "type": (type_ or "").strip() or None,
            "type_url": response.urljoin(type_url) if type_url else None,

            "genres": genres,
            "genres_urls": [response.urljoin(u) for u in genres_urls if u],

            "editeur_vo": (editeur_vo or "").strip() or None,
            "editeur_vo_url": response.urljoin(editeur_vo_url) if editeur_vo_url else None,

            "prepublication": (prepub or "").strip() or None,
            "prepublication_url": response.urljoin(prepub_url) if prepub_url else None,

            "illustration": clean_colon_prefix(illust_raw),
            "origine": clean_colon_prefix(origine_raw),

            "resume": resume,
            "points_forts": points_forts,

            "related_news": related_news,  # optionnel
        }

        # Texte prêt pour embeddings RAG
        item["rag_text"] = clean_text_list([
            f"Titre VO: {item['titre_vo']}" if item["titre_vo"] else "",
            f"Titre traduit: {item['titre_traduit']}" if item["titre_traduit"] else "",
            f"Dessin: {item['dessin']}" if item["dessin"] else "",
            f"Scénario: {item['scenario']}" if item["scenario"] else "",
            f"Éditeur VF: {item['editeur_vf']}" if item["editeur_vf"] else "",
            f"Type: {item['type']}" if item["type"] else "",
            f"Genres: {', '.join(item['genres'])}" if item["genres"] else "",
            f"Origine: {item['origine']}" if item["origine"] else "",
            f"Résumé: {item['resume']}" if item["resume"] else "",
            f"Points forts: {item['points_forts']}" if item["points_forts"] else "",
        ])

        yield item
