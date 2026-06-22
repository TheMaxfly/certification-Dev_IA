import os, json
import psycopg2
from psycopg2.extras import execute_values

DSN = os.getenv("POSTGRES_DSN") or os.getenv("APIMANGA_DSN")
if not DSN:
    raise SystemExit("POSTGRES_DSN/APIMANGA_DSN manquant")

PATH = "data/enriched/manganews_series.backfilled.jsonl"  # adapte si besoin

def read_jsonl(path):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)

def jdumps(x):
    return json.dumps(x, ensure_ascii=False) if x is not None else None

def normalize_source(value):
    if value is None:
        return None
    s = str(value).strip()
    if s in ("manganews", "manga_news"):
        return "manga_news"
    return s

rows = []
for it in read_jsonl(PATH):
    rows.append((
        it.get("url"),
        normalize_source(it.get("source")),
        it.get("source_id"),
        it.get("title_page"),
        it.get("titre_vo"),
        it.get("titre_traduit"),
        it.get("dessin"),
        it.get("dessin_url"),
        it.get("scenario"),
        it.get("scenario_url"),
        it.get("traducteur"),
        it.get("traducteur_url"),
        it.get("editeur_vf"),
        it.get("editeur_vf_url"),
        it.get("collection"),
        it.get("collection_url"),
        it.get("type"),
        it.get("type_url"),
        jdumps(it.get("genres")),
        jdumps(it.get("genres_urls")),
        it.get("editeur_vo"),
        it.get("editeur_vo_url"),
        it.get("prepublication"),
        it.get("prepublication_url"),
        it.get("illustration"),
        it.get("origine"),
        it.get("resume"),
        it.get("points_forts"),
        jdumps(it.get("related_news")),
        it.get("rag_text"),
        it.get("rag_char_len"),
        it.get("indexable_rag"),
        it.get("has_resume"),
        it.get("serie_slug"),
        it.get("origin_country"),
        it.get("origin_year"),
        it.get("origin_has_year"),
        it.get("title_page_norm"),
        it.get("type_norm"),
        it.get("origin_country_norm"),
        jdumps(it.get("genres_norm")),
        it.get("rag_is_consistent"),
        it.get("resume_is_consistent"),
        it.get("origin_year_is_realistic"),
        it.get("genres_norm_is_list"),
        it.get("type_is_present"),
        it.get("schema_version"),
        it.get("enrich_version"),
        it.get("scraped_at"),
    ))

sql = """
INSERT INTO manga.mn_series (
  url, source, source_id,
  title_page, titre_vo, titre_traduit,
  dessin, dessin_url, scenario, scenario_url, traducteur, traducteur_url,
  editeur_vf, editeur_vf_url, collection, collection_url,
  type, type_url,
  genres, genres_urls,
  editeur_vo, editeur_vo_url, prepublication, prepublication_url, illustration,
  origine, resume, points_forts, related_news,
  rag_text, rag_char_len, indexable_rag, has_resume,
  serie_slug, origin_country, origin_year, origin_has_year,
  title_page_norm, type_norm, origin_country_norm, genres_norm,
  rag_is_consistent, resume_is_consistent, origin_year_is_realistic, genres_norm_is_list, type_is_present,
  schema_version, enrich_version, scraped_at
) VALUES %s
ON CONFLICT (url) DO UPDATE SET
  source=EXCLUDED.source,
  source_id=EXCLUDED.source_id,
  title_page=EXCLUDED.title_page,
  titre_vo=EXCLUDED.titre_vo,
  titre_traduit=EXCLUDED.titre_traduit,
  dessin=EXCLUDED.dessin,
  dessin_url=EXCLUDED.dessin_url,
  scenario=EXCLUDED.scenario,
  scenario_url=EXCLUDED.scenario_url,
  traducteur=EXCLUDED.traducteur,
  traducteur_url=EXCLUDED.traducteur_url,
  editeur_vf=EXCLUDED.editeur_vf,
  editeur_vf_url=EXCLUDED.editeur_vf_url,
  collection=EXCLUDED.collection,
  collection_url=EXCLUDED.collection_url,
  type=EXCLUDED.type,
  type_url=EXCLUDED.type_url,
  genres=EXCLUDED.genres,
  genres_urls=EXCLUDED.genres_urls,
  editeur_vo=EXCLUDED.editeur_vo,
  editeur_vo_url=EXCLUDED.editeur_vo_url,
  prepublication=EXCLUDED.prepublication,
  prepublication_url=EXCLUDED.prepublication_url,
  illustration=EXCLUDED.illustration,
  origine=EXCLUDED.origine,
  resume=EXCLUDED.resume,
  points_forts=EXCLUDED.points_forts,
  related_news=EXCLUDED.related_news,
  rag_text=EXCLUDED.rag_text,
  rag_char_len=EXCLUDED.rag_char_len,
  indexable_rag=EXCLUDED.indexable_rag,
  has_resume=EXCLUDED.has_resume,
  serie_slug=EXCLUDED.serie_slug,
  origin_country=EXCLUDED.origin_country,
  origin_year=EXCLUDED.origin_year,
  origin_has_year=EXCLUDED.origin_has_year,
  title_page_norm=EXCLUDED.title_page_norm,
  type_norm=EXCLUDED.type_norm,
  origin_country_norm=EXCLUDED.origin_country_norm,
  genres_norm=EXCLUDED.genres_norm,
  rag_is_consistent=EXCLUDED.rag_is_consistent,
  resume_is_consistent=EXCLUDED.resume_is_consistent,
  origin_year_is_realistic=EXCLUDED.origin_year_is_realistic,
  genres_norm_is_list=EXCLUDED.genres_norm_is_list,
  type_is_present=EXCLUDED.type_is_present,
  schema_version=EXCLUDED.schema_version,
  enrich_version=EXCLUDED.enrich_version,
  scraped_at=EXCLUDED.scraped_at
;
"""

conn = psycopg2.connect(DSN)
conn.autocommit = False
try:
    with conn.cursor() as cur:
        execute_values(cur, sql, rows, page_size=500)
    conn.commit()
    print(f"OK: {len(rows)} lignes upsert dans manga.mn_series")
finally:
    conn.close()
