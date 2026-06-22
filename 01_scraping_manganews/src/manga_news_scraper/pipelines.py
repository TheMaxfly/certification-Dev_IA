import os
import re
import json
import datetime as dt
import psycopg2
from psycopg2.extras import execute_values
# manga_news_scraper/pipelines.py
from itemadapter import ItemAdapter
from manga_news_scraper.utils.enrich_jsonl import enrich_item

import datetime as dt
from itemadapter import ItemAdapter
from manga_news_scraper.utils.enrich_jsonl import enrich_item


def _truthy_text(x) -> bool:
    if x is None:
        return False
    s = str(x).strip()
    return s != "" and s.lower() != "nan"


def _to_int_safe(x):
    try:
        if x is None:
            return None
        return int(float(x))  # gère "2014.0" etc.
    except Exception:
        return None


class EnrichPipeline:
    # ⚠️ conseille d’unifier le style des versions.
    # Comme tu valides "manganews.series.v1" côté manganews_series,
    # je garde le format "dot" pour être cohérent avec attentes GX.
    SERIES_SCHEMA_VERSION = "manganews.series.v1"
    POPULAIRES_SCHEMA_VERSION = "manganews.populaires.v1"

    # version de la logique d’enrichissement (traçabilité pipeline)
    ENRICH_VERSION = "enrich_item:v2"

    def process_item(self, item, spider):
        data = ItemAdapter(item).asdict()
        data = enrich_item(data)

        # --- 1) Normalisation slug : garder UNE seule clé ---
        if "serie_slug" not in data and "series_slug" in data:
            data["serie_slug"] = data.pop("series_slug")
        data.pop("series_slug", None)

        # --- 2) Détection “populaires” ---
        collection = (data.get("collection") or "").strip().lower()
        is_populaires = (collection == "populaires") or spider.name.endswith("populaires")

        # --- 3) schema/enrich/scraped_at (toujours renseignés) ---
        data["schema_version"] = (
            self.POPULAIRES_SCHEMA_VERSION if is_populaires else self.SERIES_SCHEMA_VERSION
        )
        data["enrich_version"] = self.ENRICH_VERSION
        data["scraped_at"] = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

        # --- 4) RAG indexability ---
        if is_populaires:
            # populaires = liste courte, pas de texte riche -> ne pas polluer l’index
            data["indexable_rag"] = False
        else:
            data.setdefault(
                "indexable_rag",
                bool(data.get("rag_text") or data.get("resume") or data.get("points_forts"))
            )

        # --- 5) Flags de cohérence (pour GX : CRITICAL vs WARNING) ---
        rag_text = data.get("rag_text")
        rag_char_len = data.get("rag_char_len") or 0
        indexable_rag = bool(data.get("indexable_rag"))
        data["rag_is_consistent"] = (not indexable_rag) or (_truthy_text(rag_text) and rag_char_len > 0)

        has_resume = bool(data.get("has_resume"))
        resume = data.get("resume")
        data["resume_is_consistent"] = (not has_resume) or _truthy_text(resume)

        # WARNING (non bloquant au début)
        origin_has_year = bool(data.get("origin_has_year"))
        origin_year_i = _to_int_safe(data.get("origin_year"))
        current_year = dt.datetime.now(dt.timezone.utc).year
        data["origin_year_is_realistic"] = (not origin_has_year) or (
            origin_year_i is not None and 1950 <= origin_year_i <= current_year
        )

        data["genres_norm_is_list"] = isinstance(data.get("genres_norm"), list)

        type_norm = data.get("type_norm")
        type_raw = data.get("type")
        data["type_is_present"] = _truthy_text(type_norm) or _truthy_text(type_raw)

        return data




class ValidationError(Exception):
    pass

def normalize_spaces(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None

def parse_origin(origin: str | None):
    """
    Ex: "Japon - 2018" -> ("Japon", 2018)
    """
    if not origin:
        return (None, None)
    origin = origin.replace(":", "").strip()
    m = re.search(r"^(?P<country>.+?)\s*-\s*(?P<year>\d{4})$", origin)
    if not m:
        return (normalize_spaces(origin), None)
    return (normalize_spaces(m.group("country")), int(m.group("year")))

class MangaNewsPostgresPipeline:
    """
    - normalise
    - valide
    - upsert en PostgreSQL
    - batch insert (performance)
    """

    def __init__(self, dsn: str, batch_size: int = 200):
        self.dsn = dsn
        self.batch_size = batch_size
        self.buffer = []
        self.conn = None
        self.cur = None

    @classmethod
    def from_crawler(cls, crawler):
        dsn = crawler.settings.get("POSTGRES_DSN") or os.getenv("APIMANGA_DSN") or os.getenv("POSTGRES_DSN")
        if not dsn:
            raise RuntimeError("POSTGRES_DSN manquant (settings.py ou variable d'environnement).")
        batch_size = crawler.settings.getint("PG_BATCH_SIZE", 200)
        return cls(dsn=dsn, batch_size=batch_size)

    def open_spider(self, spider):
        self.conn = psycopg2.connect(self.dsn)
        self.conn.autocommit = False
        self.cur = self.conn.cursor()

    def close_spider(self, spider):
        self.flush()
        if self.cur:
            self.cur.close()
        if self.conn:
            self.conn.commit()
            self.conn.close()

    def process_item(self, item, spider):
        # --- Normalisation ---
        url = normalize_spaces(item.get("url"))
        title_page = normalize_spaces(item.get("title_page"))
        titre_vo = normalize_spaces(item.get("titre_vo"))
        titre_traduit = normalize_spaces(item.get("titre_traduit"))

        resume = normalize_spaces(item.get("resume"))
        points_forts = normalize_spaces(item.get("points_forts"))
        rag_text = normalize_spaces(item.get("rag_text"))

        genres = item.get("genres") or []
        genres = [normalize_spaces(g) for g in genres if normalize_spaces(g)]

        origin_raw = normalize_spaces(item.get("origine"))
        origin_country, origin_year = parse_origin(origin_raw)

        # --- Validations (règles de base) ---
        if not url:
            raise ValidationError("url manquante")

        if not (titre_traduit or titre_vo or title_page):
            raise ValidationError("aucun titre disponible (titre_traduit/titre_vo/title_page)")

        # Pour le RAG, on veut au moins une source de texte
        if not (resume or points_forts):
            # tu peux choisir warning plutôt que drop
            raise ValidationError("resume ET points_forts manquants (doc RAG vide)")

        if origin_year is not None and not (1900 <= origin_year <= 2100):
            raise ValidationError(f"origin_year incohérent: {origin_year}")

        row = (
            url,
            title_page,
            titre_vo,
            titre_traduit,
            normalize_spaces(item.get("dessin")),
            normalize_spaces(item.get("scenario")),
            normalize_spaces(item.get("traducteur")),
            normalize_spaces(item.get("editeur_vf")),
            normalize_spaces(item.get("collection")),
            normalize_spaces(item.get("type")),
            json.dumps(genres, ensure_ascii=False),
            origin_country,
            origin_year,
            resume,
            points_forts,
            rag_text,
            dt.datetime.utcnow(),
            "manganews",
        )

        self.buffer.append(row)
        if len(self.buffer) >= self.batch_size:
            self.flush()

        return item

    def flush(self):
        if not self.buffer:
            return

        sql = """
        INSERT INTO manga.mn_series (
            url, title_page, titre_vo, titre_traduit,
            dessin, scenario, traducteur,
            editeur_vf, collection, type,
            genres_json,
            origin_country, origin_year,
            resume, points_forts, rag_text,
            scraped_at, source
        ) VALUES %s
        ON CONFLICT (url) DO UPDATE SET
            title_page = EXCLUDED.title_page,
            titre_vo = EXCLUDED.titre_vo,
            titre_traduit = EXCLUDED.titre_traduit,
            dessin = EXCLUDED.dessin,
            scenario = EXCLUDED.scenario,
            traducteur = EXCLUDED.traducteur,
            editeur_vf = EXCLUDED.editeur_vf,
            collection = EXCLUDED.collection,
            type = EXCLUDED.type,
            genres_json = EXCLUDED.genres_json,
            origin_country = EXCLUDED.origin_country,
            origin_year = EXCLUDED.origin_year,
            resume = EXCLUDED.resume,
            points_forts = EXCLUDED.points_forts,
            rag_text = EXCLUDED.rag_text,
            scraped_at = EXCLUDED.scraped_at,
            source = EXCLUDED.source
        ;
        """

        execute_values(self.cur, sql, self.buffer, page_size=self.batch_size)
        self.conn.commit()
        self.buffer.clear()

