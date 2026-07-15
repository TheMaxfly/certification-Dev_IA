-- Schéma minimal et vues consommées par 02_api_manga.
-- Le script est rejouable : il ne supprime aucune donnée métier.

BEGIN;

CREATE SCHEMA IF NOT EXISTS manga;

CREATE TABLE IF NOT EXISTS manga.kitsu_series_core (
  kitsu_id BIGINT PRIMARY KEY,
  slug TEXT,
  status TEXT,
  title_canonical TEXT,
  title_en TEXT,
  title_ja TEXT,
  title_norm_primary TEXT,
  title_norm_canonical TEXT,
  title_norm_en TEXT,
  title_norm_ja TEXT,
  synopsis_clean TEXT,
  rating_average_10 DOUBLE PRECISION,
  rating_rank BIGINT,
  popularity_rank BIGINT,
  categories_json JSONB,
  genres_json JSONB,
  tags_all_json JSONB
);

CREATE TABLE IF NOT EXISTS manga.kitsu_series_authors (
  kitsu_id BIGINT NOT NULL REFERENCES manga.kitsu_series_core(kitsu_id)
    ON DELETE CASCADE,
  author_name TEXT NOT NULL,
  author_role TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (kitsu_id, author_name, author_role)
);

CREATE TABLE IF NOT EXISTS manga.kitsu_weekly_snapshot (
  list_name TEXT NOT NULL,
  fetched_at_ts TIMESTAMPTZ NOT NULL,
  kitsu_id BIGINT NOT NULL REFERENCES manga.kitsu_series_core(kitsu_id)
    ON DELETE CASCADE,
  position INTEGER NOT NULL CHECK (position > 0),
  list_rank INTEGER,
  trend_rank INTEGER,
  endpoint TEXT,
  PRIMARY KEY (list_name, fetched_at_ts, kitsu_id)
);

CREATE INDEX IF NOT EXISTS idx_kitsu_weekly_latest
  ON manga.kitsu_weekly_snapshot (list_name, fetched_at_ts DESC);

CREATE INDEX IF NOT EXISTS gin_kitsu_core_tags
  ON manga.kitsu_series_core USING GIN (tags_all_json);

-- Seul le snapshot le plus récent de chaque liste participe au boost.
-- Pondération explicite : tendance x3, popularité x2, publication x1.
-- Pour chaque signal, le poids est divisé par la position dans la liste.
CREATE OR REPLACE VIEW manga.rag_docs_scored AS
WITH latest_runs AS (
  SELECT list_name, max(fetched_at_ts) AS fetched_at_ts
  FROM manga.kitsu_weekly_snapshot
  GROUP BY list_name
),
latest_signals AS (
  SELECT
    snapshot.kitsu_id,
    min(snapshot.position) FILTER (
      WHERE snapshot.list_name = 'trending_weekly'
    ) AS trending_pos,
    min(snapshot.position) FILTER (
      WHERE snapshot.list_name = 'most_popular'
    ) AS popular_pos,
    min(snapshot.position) FILTER (
      WHERE snapshot.list_name = 'top_publishing'
    ) AS top_pos
  FROM manga.kitsu_weekly_snapshot AS snapshot
  JOIN latest_runs AS latest
    ON latest.list_name = snapshot.list_name
   AND latest.fetched_at_ts = snapshot.fetched_at_ts
  GROUP BY snapshot.kitsu_id
),
authors AS (
  SELECT
    kitsu_id,
    string_agg(
      concat_ws(': ', author_name, nullif(author_role, '')),
      '; ' ORDER BY author_name, author_role
    ) AS authors_text,
    jsonb_agg(
      jsonb_build_object('name', author_name, 'role', nullif(author_role, ''))
      ORDER BY author_name, author_role
    ) AS authors_json
  FROM manga.kitsu_series_authors
  GROUP BY kitsu_id
),
prepared AS (
  SELECT
    core.*,
    signals.trending_pos,
    signals.popular_pos,
    signals.top_pos,
    author_list.authors_text,
    coalesce(author_list.authors_json, '[]'::jsonb) AS authors_json,
    nullif(
      concat_ws(
        ' | ',
        nullif(core.title_canonical, ''),
        nullif(core.title_en, ''),
        nullif(core.title_ja, '')
      ),
      ''
    ) AS titles_text,
    CASE
      WHEN jsonb_typeof(core.tags_all_json) = 'array'
      THEN (
        SELECT string_agg(tag.value, ', ' ORDER BY tag.value)
        FROM jsonb_array_elements_text(core.tags_all_json) AS tag(value)
      )
    END AS tags_text
  FROM manga.kitsu_series_core AS core
  LEFT JOIN latest_signals AS signals USING (kitsu_id)
  LEFT JOIN authors AS author_list USING (kitsu_id)
),
documents AS (
  SELECT
    'kitsu:' || kitsu_id::text AS doc_key,
    'kitsu'::text AS source,
    (
      coalesce(3.0 / nullif(trending_pos, 0), 0.0)
      + coalesce(2.0 / nullif(popular_pos, 0), 0.0)
      + coalesce(1.0 / nullif(top_pos, 0), 0.0)
    )::DOUBLE PRECISION AS boost_score,
    concat_ws(
      E'\n',
      CASE WHEN titles_text IS NOT NULL THEN 'Titres: ' || titles_text END,
      CASE
        WHEN nullif(btrim(synopsis_clean), '') IS NOT NULL
        THEN 'Synopsis: ' || btrim(synopsis_clean)
      END,
      CASE WHEN tags_text IS NOT NULL THEN 'Tags: ' || tags_text END,
      CASE
        WHEN authors_text IS NOT NULL THEN 'Auteurs: ' || authors_text
      END
    ) AS doc_text,
    jsonb_strip_nulls(
      jsonb_build_object(
        'kitsu_id', kitsu_id,
        'slug', slug,
        'status', status,
        'title_canonical', title_canonical,
        'title_en', title_en,
        'title_ja', title_ja,
        'rating_average_10', rating_average_10,
        'rating_rank', rating_rank,
        'popularity_rank', popularity_rank,
        'tags', coalesce(tags_all_json, '[]'::jsonb),
        'authors', authors_json,
        'trending_pos', trending_pos,
        'popular_pos', popular_pos,
        'top_pos', top_pos
      )
    ) AS metadata_json
  FROM prepared
)
SELECT doc_key, source, boost_score, doc_text, metadata_json
FROM documents;

CREATE OR REPLACE VIEW manga.rag_export_docs AS
SELECT doc_key, source, boost_score, doc_text, metadata_json
FROM manga.rag_docs_scored
WHERE nullif(btrim(doc_text), '') IS NOT NULL;

COMMIT;
