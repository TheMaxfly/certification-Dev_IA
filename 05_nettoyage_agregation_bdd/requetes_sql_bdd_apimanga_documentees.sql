-- =====================================================================
-- ApiManga - Script SQL documenté pour la création et le chargement BDD
-- Contexte certification Bloc 1 : C2 (SQL), C3 (agrégation), C4 (BDD)
-- Base cible : PostgreSQL 16 / schéma manga
-- Principe d'import : fichiers CSV -> tables stage TEXT -> tables finales typées
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0. Création du schéma logique
-- ---------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS manga;

-- Pourquoi ?
-- - Le schéma manga isole les objets du projet dans la base apimanga.
-- - IF NOT EXISTS rend la commande réexécutable sans erreur bloquante.

-- ---------------------------------------------------------------------
-- 1. Table finale des séries enrichies : MS + Kitsu
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS manga.ms_series_enriched CASCADE;

CREATE TABLE manga.ms_series_enriched (
  series_id BIGINT PRIMARY KEY,
  series_url TEXT,
  series_title TEXT,
  series_type TEXT,
  series_category TEXT,
  series_year INTEGER,

  series_other_titles JSONB,
  series_genres JSONB,
  series_tags JSONB,
  series_statuses JSONB,
  series_related_works JSONB,

  series_dessinateur TEXT,
  series_scenariste TEXT,
  series_mag_prepub TEXT,

  series_popularity_rank BIGINT,
  series_members_rating DOUBLE PRECISION,
  series_members_votes BIGINT,
  series_experts_rating DOUBLE PRECISION,
  series_experts_votes BIGINT,

  series_synopsis TEXT,
  series_synopsis_enriched TEXT,
  series_category_year_guess INTEGER,
  series_category_clean TEXT,
  series_category_is_allowed BOOLEAN,

  series_volume_count BIGINT,
  series_review_count BIGINT,
  series_score_mean DOUBLE PRECISION,
  series_score_median DOUBLE PRECISION,
  series_score_min DOUBLE PRECISION,
  series_score_max DOUBLE PRECISION,
  series_with_body_count BIGINT,
  series_with_date_count BIGINT,
  series_with_body_pct DOUBLE PRECISION,
  series_with_date_pct DOUBLE PRECISION,
  series_first_review_date_iso DATE,
  series_last_review_date_iso DATE,

  ms_title_main TEXT,
  ms_title_norm TEXT,
  matched_title_norm TEXT,
  ms_title TEXT,

  kitsu_id BIGINT,
  match_method TEXT,
  match_score DOUBLE PRECISION,
  kitsu_id_ms_count INTEGER,
  kitsu_id_collision BOOLEAN,
  fuzzy_low_score BOOLEAN,
  ms_title_norm_len INTEGER,
  title_too_short BOOLEAN,
  needs_review BOOLEAN,
  review_reason TEXT,

  kitsu_slug TEXT,
  kitsu_status TEXT,
  kitsu_title_canonical TEXT,
  kitsu_title_en TEXT,
  kitsu_title_ja TEXT,
  kitsu_title_norm_primary TEXT,
  kitsu_title_norm_canonical TEXT,
  kitsu_title_norm_en TEXT,
  kitsu_title_norm_ja TEXT,
  kitsu_synopsis_clean TEXT,
  kitsu_rating_average_10 DOUBLE PRECISION,
  kitsu_rating_rank BIGINT,
  kitsu_popularity_rank BIGINT,
  kitsu_categories_json JSONB,
  kitsu_genres_json JSONB,
  kitsu_tags_all_json JSONB,

  series_tags_enriched JSONB,
  series_genres_enriched JSONB,

  -- Colonnes techniques conservées pendant l'import car présentes dans le CSV final.
  -- Elles peuvent être supprimées après validation si le schéma est normalisé.
  ms_title_norm_x TEXT,
  ms_title_norm_y TEXT,
  _other_titles_list TEXT,
  kitsu_kitsu_id BIGINT
);

-- Rôle : table centrale du catalogue. MS reste le référentiel principal ; Kitsu ajoute
-- synopsis, tags/genres, rangs, scores et indicateurs de qualité du matching.

-- ---------------------------------------------------------------------
-- 1bis. Import des séries via staging TEXT puis cast typé
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS manga.ms_series_stage;
CREATE TABLE manga.ms_series_stage (
  series_id TEXT, series_url TEXT, series_title TEXT, series_type TEXT,
  series_category TEXT, series_year TEXT, series_other_titles TEXT,
  series_dessinateur TEXT, series_scenariste TEXT, series_genres TEXT,
  series_tags TEXT, series_mag_prepub TEXT, series_statuses TEXT,
  series_popularity_rank TEXT, series_members_rating TEXT, series_members_votes TEXT,
  series_experts_rating TEXT, series_experts_votes TEXT, series_synopsis TEXT,
  series_related_works TEXT, series_category_year_guess TEXT, series_category_clean TEXT,
  series_category_is_allowed TEXT, series_volume_count TEXT, series_review_count TEXT,
  series_score_mean TEXT, series_score_median TEXT, series_score_min TEXT, series_score_max TEXT,
  series_with_body_count TEXT, series_with_date_count TEXT, series_with_body_pct TEXT,
  series_with_date_pct TEXT, series_first_review_date_iso TEXT, series_last_review_date_iso TEXT,
  ms_title_main TEXT, ms_title_norm_x TEXT, _other_titles_list TEXT,
  kitsu_id TEXT, match_method TEXT, match_score TEXT, matched_title_norm TEXT,
  ms_title TEXT, ms_title_norm_y TEXT, kitsu_id_ms_count TEXT, kitsu_id_collision TEXT,
  fuzzy_low_score TEXT, ms_title_norm_len TEXT, title_too_short TEXT, needs_review TEXT,
  review_reason TEXT, kitsu_kitsu_id TEXT, kitsu_slug TEXT, kitsu_status TEXT,
  kitsu_title_canonical TEXT, kitsu_title_en TEXT, kitsu_title_ja TEXT,
  kitsu_title_norm_primary TEXT, kitsu_title_norm_canonical TEXT, kitsu_title_norm_en TEXT,
  kitsu_title_norm_ja TEXT, kitsu_synopsis_clean TEXT, kitsu_rating_average_10 TEXT,
  kitsu_rating_rank TEXT, kitsu_popularity_rank TEXT, kitsu_categories_json TEXT,
  kitsu_genres_json TEXT, kitsu_tags_all_json TEXT, series_synopsis_enriched TEXT,
  series_tags_enriched TEXT, series_genres_enriched TEXT
);

-- Commande à lancer dans psql, pas dans pgAdmin Query Tool :
-- \copy manga.ms_series_stage FROM '/mnt/c/Users/maxim/Downloads/ms_series_enriched_plus_kitsu.csv' WITH (FORMAT csv, HEADER true, DELIMITER ',', QUOTE '"');

TRUNCATE TABLE manga.ms_series_enriched;
INSERT INTO manga.ms_series_enriched (
  series_id, series_url, series_title, series_type, series_category, series_year,
  series_other_titles, series_dessinateur, series_scenariste, series_genres, series_tags,
  series_mag_prepub, series_statuses, series_popularity_rank, series_members_rating,
  series_members_votes, series_experts_rating, series_experts_votes, series_synopsis,
  series_related_works, series_category_year_guess, series_category_clean, series_category_is_allowed,
  series_volume_count, series_review_count, series_score_mean, series_score_median, series_score_min,
  series_score_max, series_with_body_count, series_with_date_count, series_with_body_pct,
  series_with_date_pct, series_first_review_date_iso, series_last_review_date_iso,
  ms_title_main, ms_title_norm_x, _other_titles_list, kitsu_id, match_method, match_score,
  matched_title_norm, ms_title, ms_title_norm_y, kitsu_id_ms_count, kitsu_id_collision,
  fuzzy_low_score, ms_title_norm_len, title_too_short, needs_review, review_reason,
  kitsu_kitsu_id, kitsu_slug, kitsu_status, kitsu_title_canonical, kitsu_title_en, kitsu_title_ja,
  kitsu_title_norm_primary, kitsu_title_norm_canonical, kitsu_title_norm_en, kitsu_title_norm_ja,
  kitsu_synopsis_clean, kitsu_rating_average_10, kitsu_rating_rank, kitsu_popularity_rank,
  kitsu_categories_json, kitsu_genres_json, kitsu_tags_all_json, series_synopsis_enriched,
  series_tags_enriched, series_genres_enriched
)
SELECT
  NULLIF(regexp_replace(series_id, '\.0$', ''), '')::BIGINT,
  NULLIF(series_url,''), NULLIF(series_title,''), NULLIF(series_type,''), NULLIF(series_category,''),
  NULLIF(regexp_replace(series_year, '\.0$', ''), '')::INTEGER,
  NULLIF(series_other_titles,'')::JSONB, NULLIF(series_dessinateur,''), NULLIF(series_scenariste,''),
  NULLIF(series_genres,'')::JSONB, NULLIF(series_tags,'')::JSONB,
  NULLIF(series_mag_prepub,''), NULLIF(series_statuses,'')::JSONB,
  NULLIF(regexp_replace(series_popularity_rank, '\.0$', ''), '')::BIGINT,
  NULLIF(series_members_rating,'')::DOUBLE PRECISION,
  NULLIF(regexp_replace(series_members_votes, '\.0$', ''), '')::BIGINT,
  NULLIF(series_experts_rating,'')::DOUBLE PRECISION,
  NULLIF(regexp_replace(series_experts_votes, '\.0$', ''), '')::BIGINT,
  NULLIF(series_synopsis,''), NULLIF(series_related_works,'')::JSONB,
  NULLIF(regexp_replace(series_category_year_guess, '\.0$', ''), '')::INTEGER,
  NULLIF(series_category_clean,''),
  CASE WHEN lower(coalesce(series_category_is_allowed,'')) IN ('true','t','1') THEN TRUE ELSE FALSE END,
  NULLIF(regexp_replace(series_volume_count, '\.0$', ''), '')::BIGINT,
  NULLIF(regexp_replace(series_review_count, '\.0$', ''), '')::BIGINT,
  NULLIF(series_score_mean,'')::DOUBLE PRECISION,
  NULLIF(series_score_median,'')::DOUBLE PRECISION,
  NULLIF(series_score_min,'')::DOUBLE PRECISION,
  NULLIF(series_score_max,'')::DOUBLE PRECISION,
  NULLIF(regexp_replace(series_with_body_count, '\.0$', ''), '')::BIGINT,
  NULLIF(regexp_replace(series_with_date_count, '\.0$', ''), '')::BIGINT,
  NULLIF(series_with_body_pct,'')::DOUBLE PRECISION,
  NULLIF(series_with_date_pct,'')::DOUBLE PRECISION,
  NULLIF(series_first_review_date_iso,'')::DATE,
  NULLIF(series_last_review_date_iso,'')::DATE,
  NULLIF(ms_title_main,''), NULLIF(ms_title_norm_x,''), NULLIF(_other_titles_list,''),
  NULLIF(regexp_replace(kitsu_id, '\.0$', ''), '')::BIGINT,
  COALESCE(NULLIF(match_method,''), 'NO_MATCH'),
  NULLIF(match_score,'')::DOUBLE PRECISION,
  NULLIF(matched_title_norm,''), NULLIF(ms_title,''), NULLIF(ms_title_norm_y,''),
  NULLIF(regexp_replace(kitsu_id_ms_count, '\.0$', ''), '')::INTEGER,
  CASE WHEN lower(coalesce(kitsu_id_collision,'')) IN ('true','t','1') THEN TRUE ELSE FALSE END,
  CASE WHEN lower(coalesce(fuzzy_low_score,'')) IN ('true','t','1') THEN TRUE ELSE FALSE END,
  NULLIF(regexp_replace(ms_title_norm_len, '\.0$', ''), '')::INTEGER,
  CASE WHEN lower(coalesce(title_too_short,'')) IN ('true','t','1') THEN TRUE ELSE FALSE END,
  CASE WHEN lower(coalesce(needs_review,'')) IN ('true','t','1') THEN TRUE ELSE FALSE END,
  COALESCE(review_reason,''),
  NULLIF(regexp_replace(kitsu_kitsu_id, '\.0$', ''), '')::BIGINT,
  NULLIF(kitsu_slug,''), NULLIF(kitsu_status,''), NULLIF(kitsu_title_canonical,''),
  NULLIF(kitsu_title_en,''), NULLIF(kitsu_title_ja,''),
  NULLIF(kitsu_title_norm_primary,''), NULLIF(kitsu_title_norm_canonical,''),
  NULLIF(kitsu_title_norm_en,''), NULLIF(kitsu_title_norm_ja,''),
  NULLIF(kitsu_synopsis_clean,''), NULLIF(kitsu_rating_average_10,'')::DOUBLE PRECISION,
  NULLIF(regexp_replace(kitsu_rating_rank, '\.0$', ''), '')::BIGINT,
  NULLIF(regexp_replace(kitsu_popularity_rank, '\.0$', ''), '')::BIGINT,
  NULLIF(kitsu_categories_json,'')::JSONB,
  NULLIF(kitsu_genres_json,'')::JSONB,
  NULLIF(kitsu_tags_all_json,'')::JSONB,
  NULLIF(series_synopsis_enriched,''),
  NULLIF(series_tags_enriched,'')::JSONB,
  NULLIF(series_genres_enriched,'')::JSONB
FROM manga.ms_series_stage;

-- ---------------------------------------------------------------------
-- 2. Volumes Manga Sanctuary enrichis
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manga.ms_volumes_enriched (
  volume_url TEXT PRIMARY KEY,
  volume_title TEXT,
  volume_number INTEGER,
  volume_publication_date DATE,
  volume_dessinateur TEXT,
  volume_scenariste TEXT,
  volume_editeur TEXT,
  volume_format TEXT,
  volume_pages INTEGER,
  volume_country TEXT,
  volume_status TEXT,
  volume_tomes_published INTEGER,
  volume_tomes_total INTEGER,
  volume_members_votes BIGINT,
  volume_experts_rating DOUBLE PRECISION,
  volume_experts_votes BIGINT,
  volume_synopsis TEXT,
  series_id BIGINT REFERENCES manga.ms_series_enriched(series_id),
  review_count INTEGER,
  score_mean DOUBLE PRECISION,
  score_median DOUBLE PRECISION,
  score_min DOUBLE PRECISION,
  score_max DOUBLE PRECISION,
  with_body_count INTEGER,
  with_date_count INTEGER,
  with_body_pct DOUBLE PRECISION,
  with_date_pct DOUBLE PRECISION,
  first_review_date_iso DATE,
  last_review_date_iso DATE
);

-- Import : même stratégie que les séries : ms_volumes_stage TEXT puis INSERT typé.
-- Résultat attendu observé : 89 129 volumes.

-- ---------------------------------------------------------------------
-- 3. Reviews : table all + table documents RAG
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manga.ms_reviews_all (
  review_id BIGSERIAL PRIMARY KEY,
  series_id BIGINT REFERENCES manga.ms_series_enriched(series_id),
  series_title TEXT,
  series_url TEXT,
  volume_number INTEGER,
  volume_url TEXT REFERENCES manga.ms_volumes_enriched(volume_url),
  review_url TEXT,
  review_title TEXT,
  review_score DOUBLE PRECISION,
  review_author TEXT,
  review_date_raw TEXT,
  review_date_iso DATE,
  review_type TEXT,
  review_body TEXT,
  source_line BIGINT,
  review_date_parse_ok BOOLEAN,
  rag_text TEXT,
  rag_len INTEGER,
  rag_ready BOOLEAN
);

CREATE TABLE IF NOT EXISTS manga.rag_reviews_docs (
  doc_id BIGSERIAL PRIMARY KEY,
  volume_url TEXT REFERENCES manga.ms_volumes_enriched(volume_url),
  series_id BIGINT REFERENCES manga.ms_series_enriched(series_id),
  review_url TEXT,
  rag_text TEXT,
  rag_len INTEGER,
  rag_ready BOOLEAN
);

-- Table ms_reviews_all : toutes les reviews nettoyées.
-- Table rag_reviews_docs : uniquement les reviews prêtes pour RAG.
-- Résultats observés : 6 749 reviews / volumes avec reviews, 3 187 documents RAG.

-- ---------------------------------------------------------------------
-- 4. Mapping MS <-> Kitsu + ambiguïtés
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manga.ms_kitsu_map (
  series_id BIGINT PRIMARY KEY REFERENCES manga.ms_series_enriched(series_id) ON DELETE CASCADE,
  kitsu_id BIGINT,
  match_method TEXT,
  match_score DOUBLE PRECISION,
  matched_title_norm TEXT,
  ms_title TEXT,
  ms_title_norm TEXT
);

CREATE TABLE IF NOT EXISTS manga.ms_kitsu_ambiguous (
  series_id BIGINT PRIMARY KEY REFERENCES manga.ms_series_enriched(series_id) ON DELETE CASCADE,
  ms_title_main TEXT,
  ms_title_norm TEXT,
  n_exact_candidates INTEGER
);

-- ms_kitsu_map conserve la preuve de la correspondance entre les deux sources.
-- ms_kitsu_ambiguous isole les cas à contrôler manuellement.

-- ---------------------------------------------------------------------
-- 5. Référentiel Kitsu clean
-- ---------------------------------------------------------------------
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

DROP TABLE IF EXISTS manga.kitsu_series_core_stage;
CREATE TABLE manga.kitsu_series_core_stage (
  kitsu_id TEXT, slug TEXT, status TEXT,
  title_canonical TEXT, title_en TEXT, title_ja TEXT,
  title_norm_primary TEXT, title_norm_canonical TEXT, title_norm_en TEXT, title_norm_ja TEXT,
  synopsis_clean TEXT, rating_average_10 TEXT, rating_rank TEXT, popularity_rank TEXT,
  categories_json TEXT, genres_json TEXT, tags_all_json TEXT
);

-- \copy manga.kitsu_series_core_stage FROM '/mnt/c/Users/maxim/Downloads/kitsu_series_core.csv' WITH (FORMAT csv, HEADER true, DELIMITER ',', QUOTE '"');

INSERT INTO manga.kitsu_series_core (
  kitsu_id, slug, status, title_canonical, title_en, title_ja,
  title_norm_primary, title_norm_canonical, title_norm_en, title_norm_ja,
  synopsis_clean, rating_average_10, rating_rank, popularity_rank,
  categories_json, genres_json, tags_all_json
)
SELECT
  NULLIF(regexp_replace(kitsu_id, '\.0$', ''), '')::BIGINT,
  NULLIF(slug,''), NULLIF(status,''),
  NULLIF(title_canonical,''), NULLIF(title_en,''), NULLIF(title_ja,''),
  NULLIF(title_norm_primary,''), NULLIF(title_norm_canonical,''),
  NULLIF(title_norm_en,''), NULLIF(title_norm_ja,''),
  NULLIF(synopsis_clean,''),
  NULLIF(rating_average_10,'')::DOUBLE PRECISION,
  NULLIF(regexp_replace(rating_rank, '\.0$', ''), '')::BIGINT,
  NULLIF(regexp_replace(popularity_rank, '\.0$', ''), '')::BIGINT,
  NULLIF(categories_json,'')::JSONB,
  NULLIF(genres_json,'')::JSONB,
  NULLIF(tags_all_json,'')::JSONB
FROM manga.kitsu_series_core_stage;

-- ---------------------------------------------------------------------
-- 6. Snapshots hebdomadaires Kitsu : tendances / popularité / top publishing
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manga.kitsu_weekly_snapshot (
  list_name TEXT NOT NULL,
  fetched_at_ts TIMESTAMPTZ NOT NULL,
  kitsu_id BIGINT NOT NULL REFERENCES manga.kitsu_series_core(kitsu_id),
  position INTEGER NOT NULL CHECK (position > 0),
  list_rank INTEGER,
  trend_rank INTEGER,
  endpoint TEXT,
  PRIMARY KEY (list_name, fetched_at_ts, kitsu_id),
  CONSTRAINT kitsu_weekly_snapshot_trend_rank_rule CHECK (
    ((list_name <> 'trending_weekly') AND trend_rank IS NULL)
    OR ((list_name = 'trending_weekly') AND trend_rank = position)
  )
);

-- Une table unique évite de multiplier les structures identiques. list_name prend les valeurs :
-- trending_weekly, most_popular, top_publishing.

-- ---------------------------------------------------------------------
-- 7. Index de performance et préparation RAG
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_ms_series_kitsu_id ON manga.ms_series_enriched(kitsu_id);
CREATE INDEX IF NOT EXISTS idx_ms_series_match_method ON manga.ms_series_enriched(match_method);
CREATE INDEX IF NOT EXISTS idx_ms_series_needs_review ON manga.ms_series_enriched(needs_review);
CREATE INDEX IF NOT EXISTS gin_ms_series_tags_enriched ON manga.ms_series_enriched USING GIN(series_tags_enriched);
CREATE INDEX IF NOT EXISTS gin_ms_series_genres_enriched ON manga.ms_series_enriched USING GIN(series_genres_enriched);
CREATE INDEX IF NOT EXISTS gin_ms_series_kitsu_tags ON manga.ms_series_enriched USING GIN(kitsu_tags_all_json);
CREATE INDEX IF NOT EXISTS gin_ms_series_synopsis_tsv ON manga.ms_series_enriched USING GIN(to_tsvector('simple', coalesce(series_synopsis_enriched,'')));

CREATE INDEX IF NOT EXISTS idx_ms_volumes_series_id ON manga.ms_volumes_enriched(series_id);
CREATE INDEX IF NOT EXISTS idx_ms_volumes_number ON manga.ms_volumes_enriched(volume_number);
CREATE INDEX IF NOT EXISTS idx_reviews_all_volume_url ON manga.ms_reviews_all(volume_url);
CREATE INDEX IF NOT EXISTS idx_reviews_all_series_id ON manga.ms_reviews_all(series_id);
CREATE INDEX IF NOT EXISTS idx_reviews_all_rag_ready ON manga.ms_reviews_all(rag_ready);
CREATE INDEX IF NOT EXISTS idx_rag_reviews_docs_volume_url ON manga.rag_reviews_docs(volume_url);
CREATE INDEX IF NOT EXISTS idx_rag_reviews_docs_series_id ON manga.rag_reviews_docs(series_id);
CREATE INDEX IF NOT EXISTS idx_ms_kitsu_map_kitsu_id ON manga.ms_kitsu_map(kitsu_id);
CREATE INDEX IF NOT EXISTS idx_ms_kitsu_map_match_method ON manga.ms_kitsu_map(match_method);
CREATE INDEX IF NOT EXISTS idx_ms_kitsu_ambiguous_title_norm ON manga.ms_kitsu_ambiguous(ms_title_norm);
CREATE INDEX IF NOT EXISTS gin_kitsu_core_categories ON manga.kitsu_series_core USING GIN(categories_json);
CREATE INDEX IF NOT EXISTS gin_kitsu_core_genres ON manga.kitsu_series_core USING GIN(genres_json);
CREATE INDEX IF NOT EXISTS gin_kitsu_core_tags ON manga.kitsu_series_core USING GIN(tags_all_json);

-- ---------------------------------------------------------------------
-- 8. Requêtes de contrôle qualité après import
-- ---------------------------------------------------------------------
SELECT COUNT(*) AS series_total FROM manga.ms_series_enriched;
SELECT match_method, COUNT(*) FROM manga.ms_series_enriched GROUP BY match_method ORDER BY COUNT(*) DESC;
SELECT needs_review, COUNT(*) FROM manga.ms_series_enriched GROUP BY needs_review;
SELECT COUNT(*) AS volumes_total FROM manga.ms_volumes_enriched;
SELECT COUNT(*) AS reviews_all FROM manga.ms_reviews_all;
SELECT COUNT(*) AS rag_docs FROM manga.rag_reviews_docs;

-- Contrôles de cohérence des clés étrangères :
SELECT COUNT(*) AS orphan_volumes
FROM manga.ms_volumes_enriched v
LEFT JOIN manga.ms_series_enriched s ON s.series_id = v.series_id
WHERE s.series_id IS NULL;

SELECT COUNT(*) AS orphan_reviews
FROM manga.ms_reviews_all r
LEFT JOIN manga.ms_volumes_enriched v ON v.volume_url = r.volume_url
WHERE v.volume_url IS NULL;

-- Couverture RAG :
SELECT
  COUNT(DISTINCT volume_url) AS volumes_with_reviews,
  COUNT(DISTINCT volume_url) FILTER (WHERE rag_ready = TRUE) AS volumes_with_rag_doc,
  ROUND(100.0 * COUNT(DISTINCT volume_url) FILTER (WHERE rag_ready = TRUE) / NULLIF(COUNT(DISTINCT volume_url),0), 2) AS pct
FROM manga.ms_reviews_all;
