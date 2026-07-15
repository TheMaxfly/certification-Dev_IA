-- 000 — Baseline : l'héritage pré-versionné de la base `apimanga`.
--
-- CAPTURE : 2026-07-15, depuis apimanga (PostgreSQL 16.14 (Ubuntu 16.14-0ubuntu0.24.04.1)).
-- PROVENANCE : fichier GÉNÉRÉ par pg_dump, reproductible à l'identique par
--
--   pg_dump "$DATABASE_URL" --schema-only --no-owner --no-privileges \
--     -n manga -n bench \
--     -T manga.work_identity -T manga.volume_identity \
--     -T manga.match_decision -T manga.v_match_current
--
-- Seule retouche apportée à cette sortie : le retrait des méta-commandes
-- \restrict / \unrestrict dont pg_dump encadre désormais ses fichiers. Ce
-- sont des directives PSQL, pas du SQL : le runner joue la migration via
-- psycopg, qui s'y arrête en erreur de syntaxe. Leur jeton est de surcroît
-- tiré au hasard à chaque dump, ce qui rendrait le checksum du fichier
-- différent à chaque régénération. Aucun objet du schéma n'est concerné.
--
-- Ne pas le réécrire à la main : il ne vaut que s'il est le reflet exact de
-- l'héritage. Toute évolution passe par une migration NNN+1.
--
-- ---------------------------------------------------------------------------
-- LA FRONTIÈRE HÉRITAGE / VERSIONNÉ
-- ---------------------------------------------------------------------------
-- `apimanga` a été construite AVANT que son schéma ne soit versionné. Les
-- tables du module 05 (ELT `ms_*`, `kitsu_*`, `rag_*`) et le schéma `bench`
-- du module 06 existaient déjà quand 001 est arrivée — c'est d'ailleurs
-- pourquoi 001 crée ses schémas en `IF NOT EXISTS`. Rien dans le dépôt ne les
-- décrivait : une base neuve rejouant 001 et 002 n'obtenait PAS `apimanga`,
-- et aucun contrôle ne le signalait.
--
-- 000 ferme cet écart. Son périmètre est défini par SOUSTRACTION — tout ce que
-- 001, 002 et le runner ne créent pas :
--
--   INCLUS (l'héritage)
--     manga  — kitsu_series_authors(_stg), kitsu_series_core(_stage|_stg),
--              kitsu_weekly_snapshot(_stg), ms_kitsu_ambiguous, ms_kitsu_map,
--              ms_reviews, ms_reviews_all, ms_series_enriched,
--              ms_volumes_enriched, rag_kitsu_docs, rag_reviews_docs,
--              + les vues kitsu_rag_docs_v et rag_*
--     bench  — le schéma d'évaluation embeddings/LLM du module 06
--
--   EXCLUS (déjà versionnés, ou hors périmètre)
--     001    — manga.work_identity, manga.volume_identity,
--              manga.match_decision, manga.v_match_current ; schéma staging
--     002    — les 7 tables du schéma staging
--     runner — public.schema_migrations, qu'il crée lui-même
--
-- AUCUNE EXTENSION n'est créée ici, et ce n'est pas un oubli : l'héritage n'en
-- requiert aucune. Les index GIN de `manga` portent sur du `jsonb` ou sur
-- `to_tsvector(...)`, jamais sur des trigrammes — `pg_trgm` n'est pas même
-- installée sur apimanga. L'extension `vector` y est présente mais AUCUNE
-- colonne applicative ne l'utilise (le module 06 garde ses embeddings dans
-- FAISS) : la créer ici imposerait à toute base neuve une dépendance que
-- l'héritage n'a pas.
--
-- ---------------------------------------------------------------------------
-- COMMENT ELLE S'APPLIQUE — ET POURQUOI JAMAIS SUR `apimanga`
-- ---------------------------------------------------------------------------
-- Base NEUVE (tests, reconstruction) : 000 est jouée normalement, puis 001,
-- 002, … La base obtenue est enfin celle du dépôt.
--
-- `apimanga` : ces objets Y EXISTENT DÉJÀ, avec leurs données. Rejouer 000
-- l'y ferait échouer (`CREATE TABLE` sur une table présente), et une baseline
-- ne doit rien recréer. On l'y enregistre SANS l'exécuter :
--
--   uv run python migrate.py mark-applied 000
--
-- ORDRE : l'héritage précède le runner, mais son marquage sur `apimanga` est
-- postérieur à l'application de 001 et 002 (2026-07-15). `applied_at` de 000
-- y est donc plus RÉCENT que celui de 001/002, alors que sa version est plus
-- ancienne. C'est attendu : la baseline date le constat, pas la construction.
--
-- D'où l'absence d'`IF NOT EXISTS` dans tout ce fichier : un `CREATE TABLE IF
-- NOT EXISTS` s'appliquerait partout en silence, y compris sur une base dont
-- les tables DIVERGENT de ce dump, et masquerait précisément l'écart que 000
-- est là pour rendre visible. Ici, une erreur est une information.
--
-- Politique inchangée : pas de `down`, une transaction par fichier, checksum
-- immuable une fois la migration enregistrée (cf. README).
-- ---------------------------------------------------------------------------

--
-- PostgreSQL database dump
--


-- Dumped from database version 16.14 (Ubuntu 16.14-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.14 (Ubuntu 16.14-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: bench; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA bench;


--
-- Name: manga; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA manga;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: chunking_strategies; Type: TABLE; Schema: bench; Owner: -
--

CREATE TABLE bench.chunking_strategies (
    chunking_id bigint NOT NULL,
    name text NOT NULL,
    chunk_size integer NOT NULL,
    chunk_overlap integer NOT NULL,
    tokenizer_name text,
    notes text
);


--
-- Name: chunking_strategies_chunking_id_seq; Type: SEQUENCE; Schema: bench; Owner: -
--

CREATE SEQUENCE bench.chunking_strategies_chunking_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: chunking_strategies_chunking_id_seq; Type: SEQUENCE OWNED BY; Schema: bench; Owner: -
--

ALTER SEQUENCE bench.chunking_strategies_chunking_id_seq OWNED BY bench.chunking_strategies.chunking_id;


--
-- Name: corpus_chunks; Type: TABLE; Schema: bench; Owner: -
--

CREATE TABLE bench.corpus_chunks (
    chunk_id bigint NOT NULL,
    doc_key text NOT NULL,
    chunk_index integer NOT NULL,
    chunk_text text NOT NULL,
    char_start integer,
    char_end integer,
    token_count integer,
    chunk_hash text
);


--
-- Name: corpus_chunks_chunk_id_seq; Type: SEQUENCE; Schema: bench; Owner: -
--

CREATE SEQUENCE bench.corpus_chunks_chunk_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: corpus_chunks_chunk_id_seq; Type: SEQUENCE OWNED BY; Schema: bench; Owner: -
--

ALTER SEQUENCE bench.corpus_chunks_chunk_id_seq OWNED BY bench.corpus_chunks.chunk_id;


--
-- Name: corpus_docs; Type: TABLE; Schema: bench; Owner: -
--

CREATE TABLE bench.corpus_docs (
    doc_key text NOT NULL,
    source text NOT NULL,
    series_id bigint,
    kitsu_id bigint,
    boost_score numeric,
    doc_text text NOT NULL,
    metadata_json jsonb,
    title text
);


--
-- Name: embedding_models; Type: TABLE; Schema: bench; Owner: -
--

CREATE TABLE bench.embedding_models (
    model_id bigint NOT NULL,
    model_name text NOT NULL,
    dim integer NOT NULL,
    notes text
);


--
-- Name: embedding_models_model_id_seq; Type: SEQUENCE; Schema: bench; Owner: -
--

CREATE SEQUENCE bench.embedding_models_model_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: embedding_models_model_id_seq; Type: SEQUENCE OWNED BY; Schema: bench; Owner: -
--

ALTER SEQUENCE bench.embedding_models_model_id_seq OWNED BY bench.embedding_models.model_id;


--
-- Name: embedding_runs; Type: TABLE; Schema: bench; Owner: -
--

CREATE TABLE bench.embedding_runs (
    run_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    model_id bigint NOT NULL,
    chunking_id bigint NOT NULL,
    corpus_ref text DEFAULT 'manga.rag_export_docs'::text NOT NULL,
    notes text
);


--
-- Name: faiss_indexes; Type: TABLE; Schema: bench; Owner: -
--

CREATE TABLE bench.faiss_indexes (
    run_id uuid NOT NULL,
    index_path text NOT NULL,
    meta_path text,
    index_type text NOT NULL,
    metric text NOT NULL,
    built_at timestamp with time zone DEFAULT now() NOT NULL,
    notes text
);


--
-- Name: metrics; Type: TABLE; Schema: bench; Owner: -
--

CREATE TABLE bench.metrics (
    run_id uuid NOT NULL,
    metric_name text NOT NULL,
    metric_value double precision NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: qrels; Type: TABLE; Schema: bench; Owner: -
--

CREATE TABLE bench.qrels (
    query_id bigint NOT NULL,
    doc_key text NOT NULL,
    relevance smallint DEFAULT 1 NOT NULL
);


--
-- Name: queries; Type: TABLE; Schema: bench; Owner: -
--

CREATE TABLE bench.queries (
    query_id bigint NOT NULL,
    split text DEFAULT 'test'::text NOT NULL,
    query_text text NOT NULL,
    lang text DEFAULT 'fr'::text,
    intent text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: queries_query_id_seq; Type: SEQUENCE; Schema: bench; Owner: -
--

CREATE SEQUENCE bench.queries_query_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: queries_query_id_seq; Type: SEQUENCE OWNED BY; Schema: bench; Owner: -
--

ALTER SEQUENCE bench.queries_query_id_seq OWNED BY bench.queries.query_id;


--
-- Name: retrieval_results; Type: TABLE; Schema: bench; Owner: -
--

CREATE TABLE bench.retrieval_results (
    run_id uuid NOT NULL,
    query_id bigint NOT NULL,
    rank integer NOT NULL,
    doc_key text NOT NULL,
    chunk_id bigint,
    score double precision NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: kitsu_series_authors; Type: TABLE; Schema: manga; Owner: -
--

CREATE TABLE manga.kitsu_series_authors (
    kitsu_id bigint NOT NULL,
    author_name text NOT NULL,
    author_role text NOT NULL
);


--
-- Name: kitsu_series_core; Type: TABLE; Schema: manga; Owner: -
--

CREATE TABLE manga.kitsu_series_core (
    kitsu_id bigint NOT NULL,
    slug text,
    status text,
    title_canonical text,
    title_en text,
    title_ja text,
    title_norm_primary text,
    title_norm_canonical text,
    title_norm_en text,
    title_norm_ja text,
    synopsis_clean text,
    rating_average_10 double precision,
    rating_rank bigint,
    popularity_rank bigint,
    categories_json jsonb,
    genres_json jsonb,
    tags_all_json jsonb
);


--
-- Name: kitsu_weekly_snapshot; Type: TABLE; Schema: manga; Owner: -
--

CREATE TABLE manga.kitsu_weekly_snapshot (
    list_name text NOT NULL,
    fetched_at_ts timestamp with time zone NOT NULL,
    kitsu_id bigint NOT NULL,
    "position" integer NOT NULL,
    list_rank integer,
    trend_rank integer,
    endpoint text,
    CONSTRAINT kitsu_weekly_snapshot_position_positive CHECK (("position" > 0)),
    CONSTRAINT kitsu_weekly_snapshot_trend_rank_rule CHECK ((((list_name <> 'trending_weekly'::text) AND (trend_rank IS NULL)) OR ((list_name = 'trending_weekly'::text) AND (trend_rank = "position"))))
);


--
-- Name: kitsu_rag_docs_v; Type: VIEW; Schema: manga; Owner: -
--

CREATE VIEW manga.kitsu_rag_docs_v AS
 WITH latest AS (
         SELECT kitsu_weekly_snapshot.list_name,
            max(kitsu_weekly_snapshot.fetched_at_ts) AS fetched_at_ts
           FROM manga.kitsu_weekly_snapshot
          GROUP BY kitsu_weekly_snapshot.list_name
        ), latest_snap AS (
         SELECT s_1.list_name,
            s_1.fetched_at_ts,
            s_1.kitsu_id,
            s_1."position",
            s_1.list_rank,
            s_1.trend_rank,
            s_1.endpoint
           FROM (manga.kitsu_weekly_snapshot s_1
             JOIN latest l ON (((l.list_name = s_1.list_name) AND (l.fetched_at_ts = s_1.fetched_at_ts))))
        ), signals AS (
         SELECT latest_snap.kitsu_id,
            min(
                CASE
                    WHEN (latest_snap.list_name = 'trending_weekly'::text) THEN latest_snap."position"
                    ELSE NULL::integer
                END) AS trending_pos,
            min(
                CASE
                    WHEN (latest_snap.list_name = 'most_popular'::text) THEN latest_snap."position"
                    ELSE NULL::integer
                END) AS popular_pos,
            min(
                CASE
                    WHEN (latest_snap.list_name = 'top_publishing'::text) THEN latest_snap."position"
                    ELSE NULL::integer
                END) AS top_pos
           FROM latest_snap
          GROUP BY latest_snap.kitsu_id
        ), authors_distinct AS (
         SELECT DISTINCT kitsu_series_authors.kitsu_id,
            (kitsu_series_authors.author_name || COALESCE(((' ('::text || kitsu_series_authors.author_role) || ')'::text), ''::text)) AS author_item,
            kitsu_series_authors.author_name
           FROM manga.kitsu_series_authors
        ), authors AS (
         SELECT authors_distinct.kitsu_id,
            string_agg(authors_distinct.author_item, '; '::text ORDER BY authors_distinct.author_name) AS authors_txt
           FROM authors_distinct
          GROUP BY authors_distinct.kitsu_id
        )
 SELECT k.kitsu_id,
    k.slug,
    k.title_canonical,
    k.title_en,
    k.title_ja,
    k.synopsis_clean,
    k.tags_all_json,
    s.trending_pos,
    s.popular_pos,
    s.top_pos,
    COALESCE(a.authors_txt, ''::text) AS authors_txt,
    TRIM(BOTH FROM concat_ws('
'::text, ('Titres: '::text || concat_ws(' | '::text, k.title_canonical, k.title_en, k.title_ja)),
        CASE
            WHEN (COALESCE(a.authors_txt, ''::text) <> ''::text) THEN ('Auteurs: '::text || a.authors_txt)
            ELSE NULL::text
        END,
        CASE
            WHEN (k.tags_all_json IS NOT NULL) THEN ('Tags: '::text || (k.tags_all_json)::text)
            ELSE NULL::text
        END,
        CASE
            WHEN (k.synopsis_clean IS NOT NULL) THEN ('Synopsis: '::text || k.synopsis_clean)
            ELSE NULL::text
        END)) AS doc_text
   FROM ((manga.kitsu_series_core k
     LEFT JOIN signals s ON ((s.kitsu_id = k.kitsu_id)))
     LEFT JOIN authors a ON ((a.kitsu_id = k.kitsu_id)));


--
-- Name: kitsu_series_authors_stg; Type: TABLE; Schema: manga; Owner: -
--

CREATE TABLE manga.kitsu_series_authors_stg (
    kitsu_id text,
    author_name text,
    author_role text
);


--
-- Name: kitsu_series_core_stage; Type: TABLE; Schema: manga; Owner: -
--

CREATE TABLE manga.kitsu_series_core_stage (
    kitsu_id text,
    slug text,
    status text,
    title_canonical text,
    title_en text,
    title_ja text,
    title_norm_primary text,
    title_norm_canonical text,
    title_norm_en text,
    title_norm_ja text,
    synopsis_clean text,
    rating_average_10 text,
    rating_rank text,
    popularity_rank text,
    categories_json text,
    genres_json text,
    tags_all_json text
);


--
-- Name: kitsu_series_core_stg; Type: TABLE; Schema: manga; Owner: -
--

CREATE TABLE manga.kitsu_series_core_stg (
    kitsu_id text,
    slug text,
    status text,
    title_canonical text,
    title_en text,
    title_ja text,
    title_norm_primary text,
    title_norm_canonical text,
    title_norm_en text,
    title_norm_ja text,
    synopsis_clean text,
    rating_average_10 text,
    rating_rank text,
    popularity_rank text,
    categories_json text,
    genres_json text,
    tags_all_json text
);


--
-- Name: kitsu_weekly_snapshot_stg; Type: TABLE; Schema: manga; Owner: -
--

CREATE TABLE manga.kitsu_weekly_snapshot_stg (
    list_name text,
    fetched_at_ts text,
    endpoint text,
    kitsu_id text,
    "position" text,
    list_rank text,
    trend_rank text
);


--
-- Name: ms_kitsu_ambiguous; Type: TABLE; Schema: manga; Owner: -
--

CREATE TABLE manga.ms_kitsu_ambiguous (
    series_id bigint NOT NULL,
    ms_title_main text,
    ms_title_norm text,
    n_exact_candidates integer
);


--
-- Name: ms_kitsu_map; Type: TABLE; Schema: manga; Owner: -
--

CREATE TABLE manga.ms_kitsu_map (
    series_id bigint NOT NULL,
    kitsu_id bigint,
    match_method text,
    match_score double precision,
    matched_title_norm text,
    ms_title text,
    ms_title_norm text
);


--
-- Name: ms_reviews; Type: TABLE; Schema: manga; Owner: -
--

CREATE TABLE manga.ms_reviews (
    review_id bigint NOT NULL,
    series_id bigint,
    series_title text,
    series_url text,
    volume_number integer,
    volume_url text,
    review_url text,
    review_title text,
    review_score double precision,
    review_author text,
    review_date_raw text,
    review_date_iso date,
    review_type text,
    review_body text,
    source_line bigint,
    review_date_parse_ok boolean
);


--
-- Name: ms_reviews_all; Type: TABLE; Schema: manga; Owner: -
--

CREATE TABLE manga.ms_reviews_all (
    review_id bigint NOT NULL,
    series_id bigint,
    series_title text,
    series_url text,
    volume_number integer,
    volume_url text,
    review_url text,
    review_title text,
    review_score double precision,
    review_author text,
    review_date_raw text,
    review_date_iso date,
    review_type text,
    review_body text,
    source_line bigint,
    review_date_parse_ok boolean,
    rag_text text,
    rag_len integer,
    rag_ready boolean
);


--
-- Name: ms_reviews_all_review_id_seq; Type: SEQUENCE; Schema: manga; Owner: -
--

CREATE SEQUENCE manga.ms_reviews_all_review_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ms_reviews_all_review_id_seq; Type: SEQUENCE OWNED BY; Schema: manga; Owner: -
--

ALTER SEQUENCE manga.ms_reviews_all_review_id_seq OWNED BY manga.ms_reviews_all.review_id;


--
-- Name: ms_reviews_review_id_seq; Type: SEQUENCE; Schema: manga; Owner: -
--

CREATE SEQUENCE manga.ms_reviews_review_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ms_reviews_review_id_seq; Type: SEQUENCE OWNED BY; Schema: manga; Owner: -
--

ALTER SEQUENCE manga.ms_reviews_review_id_seq OWNED BY manga.ms_reviews.review_id;


--
-- Name: ms_series_enriched; Type: TABLE; Schema: manga; Owner: -
--

CREATE TABLE manga.ms_series_enriched (
    series_id bigint NOT NULL,
    series_url text,
    series_title text,
    series_type text,
    series_category text,
    series_year integer,
    series_other_titles jsonb,
    series_genres jsonb,
    series_tags jsonb,
    series_statuses jsonb,
    series_related_works jsonb,
    series_dessinateur text,
    series_scenariste text,
    series_mag_prepub text,
    series_popularity_rank bigint,
    series_members_rating double precision,
    series_members_votes bigint,
    series_experts_rating double precision,
    series_experts_votes bigint,
    series_synopsis text,
    series_synopsis_enriched text,
    series_category_year_guess integer,
    series_category_clean text,
    series_category_is_allowed boolean,
    series_volume_count bigint,
    series_review_count bigint,
    series_score_mean double precision,
    series_score_median double precision,
    series_score_min double precision,
    series_score_max double precision,
    series_with_body_count bigint,
    series_with_date_count bigint,
    series_with_body_pct double precision,
    series_with_date_pct double precision,
    series_first_review_date_iso date,
    series_last_review_date_iso date,
    ms_title_main text,
    ms_title_norm text,
    matched_title_norm text,
    ms_title text,
    kitsu_id bigint,
    match_method text,
    match_score double precision,
    kitsu_id_ms_count integer,
    kitsu_id_collision boolean,
    fuzzy_low_score boolean,
    ms_title_norm_len integer,
    title_too_short boolean,
    needs_review boolean,
    review_reason text,
    kitsu_slug text,
    kitsu_status text,
    kitsu_title_canonical text,
    kitsu_title_en text,
    kitsu_title_ja text,
    kitsu_title_norm_primary text,
    kitsu_title_norm_canonical text,
    kitsu_title_norm_en text,
    kitsu_title_norm_ja text,
    kitsu_synopsis_clean text,
    kitsu_rating_average_10 double precision,
    kitsu_rating_rank bigint,
    kitsu_popularity_rank bigint,
    kitsu_categories_json jsonb,
    kitsu_genres_json jsonb,
    kitsu_tags_all_json jsonb,
    series_tags_enriched jsonb,
    series_genres_enriched jsonb,
    ms_title_norm_x text,
    ms_title_norm_y text,
    _other_titles_list text,
    kitsu_kitsu_id bigint
);


--
-- Name: ms_volumes_enriched; Type: TABLE; Schema: manga; Owner: -
--

CREATE TABLE manga.ms_volumes_enriched (
    volume_url text NOT NULL,
    volume_title text,
    volume_number integer,
    volume_publication_date date,
    volume_dessinateur text,
    volume_scenariste text,
    volume_editeur text,
    volume_format text,
    volume_pages integer,
    volume_country text,
    volume_status text,
    volume_tomes_published integer,
    volume_tomes_total integer,
    volume_members_votes bigint,
    volume_experts_rating double precision,
    volume_experts_votes bigint,
    volume_synopsis text,
    series_id bigint,
    review_count integer,
    score_mean double precision,
    score_median double precision,
    score_min double precision,
    score_max double precision,
    with_body_count integer,
    with_date_count integer,
    with_body_pct double precision,
    with_date_pct double precision,
    first_review_date_iso date,
    last_review_date_iso date
);


--
-- Name: rag_kitsu_docs; Type: TABLE; Schema: manga; Owner: -
--

CREATE TABLE manga.rag_kitsu_docs (
    kitsu_id bigint NOT NULL,
    doc_text text NOT NULL,
    tags_all_json jsonb,
    trending_pos integer,
    popular_pos integer,
    top_pos integer,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: rag_reviews_docs; Type: TABLE; Schema: manga; Owner: -
--

CREATE TABLE manga.rag_reviews_docs (
    doc_id bigint NOT NULL,
    volume_url text,
    series_id bigint,
    review_url text,
    rag_text text,
    rag_len integer,
    rag_ready boolean
);


--
-- Name: rag_docs_all; Type: VIEW; Schema: manga; Owner: -
--

CREATE VIEW manga.rag_docs_all AS
 SELECT 'ms_review'::text AS source,
    ('ms_review:'::text || (rag_reviews_docs.doc_id)::text) AS doc_key,
    rag_reviews_docs.series_id,
    NULL::bigint AS kitsu_id,
    rag_reviews_docs.rag_text AS doc_text,
    jsonb_build_object('doc_id', rag_reviews_docs.doc_id, 'series_id', rag_reviews_docs.series_id, 'volume_url', rag_reviews_docs.volume_url, 'review_url', rag_reviews_docs.review_url, 'rag_len', rag_reviews_docs.rag_len) AS metadata_json
   FROM manga.rag_reviews_docs
  WHERE ((rag_reviews_docs.rag_ready IS TRUE) AND (rag_reviews_docs.rag_text IS NOT NULL) AND (length(rag_reviews_docs.rag_text) > 0))
UNION ALL
 SELECT 'kitsu_synopsis'::text AS source,
    ('kitsu:'::text || (rag_kitsu_docs.kitsu_id)::text) AS doc_key,
    NULL::bigint AS series_id,
    rag_kitsu_docs.kitsu_id,
    rag_kitsu_docs.doc_text,
    jsonb_build_object('kitsu_id', rag_kitsu_docs.kitsu_id, 'trending_pos', rag_kitsu_docs.trending_pos, 'popular_pos', rag_kitsu_docs.popular_pos, 'top_pos', rag_kitsu_docs.top_pos, 'tags', rag_kitsu_docs.tags_all_json) AS metadata_json
   FROM manga.rag_kitsu_docs
  WHERE ((rag_kitsu_docs.doc_text IS NOT NULL) AND (length(rag_kitsu_docs.doc_text) > 0));


--
-- Name: rag_ms_hybrid_docs; Type: VIEW; Schema: manga; Owner: -
--

CREATE VIEW manga.rag_ms_hybrid_docs AS
 WITH map_ok AS (
         SELECT ms_kitsu_map.series_id,
            ms_kitsu_map.kitsu_id,
            ms_kitsu_map.match_method,
            ms_kitsu_map.match_score
           FROM manga.ms_kitsu_map
          WHERE ((ms_kitsu_map.kitsu_id IS NOT NULL) AND ((ms_kitsu_map.match_method = 'exact'::text) OR (ms_kitsu_map.match_score >= (90)::double precision)))
        ), ms_reviews_agg AS (
         SELECT rag_reviews_docs.series_id,
            string_agg(rag_reviews_docs.rag_text, '

---

'::text ORDER BY rag_reviews_docs.doc_id) AS reviews_text
           FROM manga.rag_reviews_docs
          WHERE ((rag_reviews_docs.rag_ready IS TRUE) AND (rag_reviews_docs.rag_text IS NOT NULL) AND (length(rag_reviews_docs.rag_text) > 0))
          GROUP BY rag_reviews_docs.series_id
        )
 SELECT 'ms_hybrid'::text AS source,
    ('ms_hybrid:'::text || (m.series_id)::text) AS doc_key,
    m.series_id,
    m.kitsu_id,
    TRIM(BOTH FROM concat_ws('

'::text,
        CASE
            WHEN (r.reviews_text IS NOT NULL) THEN ('REVIEWS_MS:\n'::text || r.reviews_text)
            ELSE NULL::text
        END,
        CASE
            WHEN (k.doc_text IS NOT NULL) THEN ('SYNOPSIS_KITSU:\n'::text || k.doc_text)
            ELSE NULL::text
        END,
        CASE
            WHEN (k.trending_pos IS NOT NULL) THEN ('SIGNAL: trending_weekly position '::text || (k.trending_pos)::text)
            ELSE NULL::text
        END,
        CASE
            WHEN (k.popular_pos IS NOT NULL) THEN ('SIGNAL: most_popular position '::text || (k.popular_pos)::text)
            ELSE NULL::text
        END,
        CASE
            WHEN (k.top_pos IS NOT NULL) THEN ('SIGNAL: top_publishing position '::text || (k.top_pos)::text)
            ELSE NULL::text
        END)) AS doc_text,
    jsonb_build_object('series_id', m.series_id, 'kitsu_id', m.kitsu_id, 'match_method', m.match_method, 'match_score', m.match_score, 'trending_pos', k.trending_pos, 'popular_pos', k.popular_pos, 'top_pos', k.top_pos, 'tags', k.tags_all_json) AS metadata_json
   FROM ((map_ok m
     LEFT JOIN ms_reviews_agg r ON ((r.series_id = m.series_id)))
     LEFT JOIN manga.rag_kitsu_docs k ON ((k.kitsu_id = m.kitsu_id)));


--
-- Name: rag_docs_all_v2; Type: VIEW; Schema: manga; Owner: -
--

CREATE VIEW manga.rag_docs_all_v2 AS
 SELECT rag_docs_all.source,
    rag_docs_all.doc_key,
    rag_docs_all.series_id,
    rag_docs_all.kitsu_id,
    rag_docs_all.doc_text,
    rag_docs_all.metadata_json
   FROM manga.rag_docs_all
UNION ALL
 SELECT rag_ms_hybrid_docs.source,
    rag_ms_hybrid_docs.doc_key,
    rag_ms_hybrid_docs.series_id,
    rag_ms_hybrid_docs.kitsu_id,
    rag_ms_hybrid_docs.doc_text,
    rag_ms_hybrid_docs.metadata_json
   FROM manga.rag_ms_hybrid_docs
  WHERE ((rag_ms_hybrid_docs.doc_text IS NOT NULL) AND (length(rag_ms_hybrid_docs.doc_text) > 0));


--
-- Name: rag_docs_scored; Type: VIEW; Schema: manga; Owner: -
--

CREATE VIEW manga.rag_docs_scored AS
 SELECT source,
    doc_key,
    series_id,
    kitsu_id,
    doc_text,
    metadata_json,
    ((COALESCE((100.0 / (NULLIF(((metadata_json ->> 'trending_pos'::text))::integer, 0))::numeric), (0)::numeric) + COALESCE((30.0 / (NULLIF(((metadata_json ->> 'popular_pos'::text))::integer, 0))::numeric), (0)::numeric)) + COALESCE((20.0 / (NULLIF(((metadata_json ->> 'top_pos'::text))::integer, 0))::numeric), (0)::numeric)) AS boost_score
   FROM manga.rag_docs_all_v2;


--
-- Name: rag_export_docs; Type: VIEW; Schema: manga; Owner: -
--

CREATE VIEW manga.rag_export_docs AS
 SELECT doc_key,
    source,
    series_id,
    kitsu_id,
    boost_score,
    doc_text,
    metadata_json
   FROM manga.rag_docs_scored
  WHERE ((doc_text IS NOT NULL) AND (length(doc_text) > 0));


--
-- Name: rag_reviews_docs_doc_id_seq; Type: SEQUENCE; Schema: manga; Owner: -
--

CREATE SEQUENCE manga.rag_reviews_docs_doc_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: rag_reviews_docs_doc_id_seq; Type: SEQUENCE OWNED BY; Schema: manga; Owner: -
--

ALTER SEQUENCE manga.rag_reviews_docs_doc_id_seq OWNED BY manga.rag_reviews_docs.doc_id;


--
-- Name: chunking_strategies chunking_id; Type: DEFAULT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.chunking_strategies ALTER COLUMN chunking_id SET DEFAULT nextval('bench.chunking_strategies_chunking_id_seq'::regclass);


--
-- Name: corpus_chunks chunk_id; Type: DEFAULT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.corpus_chunks ALTER COLUMN chunk_id SET DEFAULT nextval('bench.corpus_chunks_chunk_id_seq'::regclass);


--
-- Name: embedding_models model_id; Type: DEFAULT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.embedding_models ALTER COLUMN model_id SET DEFAULT nextval('bench.embedding_models_model_id_seq'::regclass);


--
-- Name: queries query_id; Type: DEFAULT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.queries ALTER COLUMN query_id SET DEFAULT nextval('bench.queries_query_id_seq'::regclass);


--
-- Name: ms_reviews review_id; Type: DEFAULT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.ms_reviews ALTER COLUMN review_id SET DEFAULT nextval('manga.ms_reviews_review_id_seq'::regclass);


--
-- Name: ms_reviews_all review_id; Type: DEFAULT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.ms_reviews_all ALTER COLUMN review_id SET DEFAULT nextval('manga.ms_reviews_all_review_id_seq'::regclass);


--
-- Name: rag_reviews_docs doc_id; Type: DEFAULT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.rag_reviews_docs ALTER COLUMN doc_id SET DEFAULT nextval('manga.rag_reviews_docs_doc_id_seq'::regclass);


--
-- Name: chunking_strategies chunking_strategies_name_key; Type: CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.chunking_strategies
    ADD CONSTRAINT chunking_strategies_name_key UNIQUE (name);


--
-- Name: chunking_strategies chunking_strategies_pkey; Type: CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.chunking_strategies
    ADD CONSTRAINT chunking_strategies_pkey PRIMARY KEY (chunking_id);


--
-- Name: corpus_chunks corpus_chunks_doc_key_chunk_index_key; Type: CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.corpus_chunks
    ADD CONSTRAINT corpus_chunks_doc_key_chunk_index_key UNIQUE (doc_key, chunk_index);


--
-- Name: corpus_chunks corpus_chunks_pkey; Type: CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.corpus_chunks
    ADD CONSTRAINT corpus_chunks_pkey PRIMARY KEY (chunk_id);


--
-- Name: corpus_docs corpus_docs_pkey; Type: CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.corpus_docs
    ADD CONSTRAINT corpus_docs_pkey PRIMARY KEY (doc_key);


--
-- Name: embedding_models embedding_models_model_name_key; Type: CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.embedding_models
    ADD CONSTRAINT embedding_models_model_name_key UNIQUE (model_name);


--
-- Name: embedding_models embedding_models_pkey; Type: CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.embedding_models
    ADD CONSTRAINT embedding_models_pkey PRIMARY KEY (model_id);


--
-- Name: embedding_runs embedding_runs_pkey; Type: CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.embedding_runs
    ADD CONSTRAINT embedding_runs_pkey PRIMARY KEY (run_id);


--
-- Name: faiss_indexes faiss_indexes_pkey; Type: CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.faiss_indexes
    ADD CONSTRAINT faiss_indexes_pkey PRIMARY KEY (run_id);


--
-- Name: metrics metrics_pkey; Type: CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.metrics
    ADD CONSTRAINT metrics_pkey PRIMARY KEY (run_id, metric_name);


--
-- Name: qrels qrels_pkey; Type: CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.qrels
    ADD CONSTRAINT qrels_pkey PRIMARY KEY (query_id, doc_key);


--
-- Name: queries queries_pkey; Type: CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.queries
    ADD CONSTRAINT queries_pkey PRIMARY KEY (query_id);


--
-- Name: retrieval_results retrieval_results_pkey; Type: CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.retrieval_results
    ADD CONSTRAINT retrieval_results_pkey PRIMARY KEY (run_id, query_id, rank);


--
-- Name: kitsu_series_authors kitsu_series_authors_pkey; Type: CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.kitsu_series_authors
    ADD CONSTRAINT kitsu_series_authors_pkey PRIMARY KEY (kitsu_id, author_name, author_role);


--
-- Name: kitsu_series_core kitsu_series_core_pkey; Type: CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.kitsu_series_core
    ADD CONSTRAINT kitsu_series_core_pkey PRIMARY KEY (kitsu_id);


--
-- Name: kitsu_weekly_snapshot kitsu_weekly_snapshot_pkey; Type: CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.kitsu_weekly_snapshot
    ADD CONSTRAINT kitsu_weekly_snapshot_pkey PRIMARY KEY (list_name, fetched_at_ts, kitsu_id);


--
-- Name: ms_kitsu_ambiguous ms_kitsu_ambiguous_pkey; Type: CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.ms_kitsu_ambiguous
    ADD CONSTRAINT ms_kitsu_ambiguous_pkey PRIMARY KEY (series_id);


--
-- Name: ms_kitsu_map ms_kitsu_map_pkey; Type: CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.ms_kitsu_map
    ADD CONSTRAINT ms_kitsu_map_pkey PRIMARY KEY (series_id);


--
-- Name: ms_reviews_all ms_reviews_all_pkey; Type: CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.ms_reviews_all
    ADD CONSTRAINT ms_reviews_all_pkey PRIMARY KEY (review_id);


--
-- Name: ms_reviews ms_reviews_pkey; Type: CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.ms_reviews
    ADD CONSTRAINT ms_reviews_pkey PRIMARY KEY (review_id);


--
-- Name: ms_series_enriched ms_series_enriched_pkey; Type: CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.ms_series_enriched
    ADD CONSTRAINT ms_series_enriched_pkey PRIMARY KEY (series_id);


--
-- Name: ms_volumes_enriched ms_volumes_enriched_pkey; Type: CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.ms_volumes_enriched
    ADD CONSTRAINT ms_volumes_enriched_pkey PRIMARY KEY (volume_url);


--
-- Name: rag_kitsu_docs rag_kitsu_docs_pkey; Type: CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.rag_kitsu_docs
    ADD CONSTRAINT rag_kitsu_docs_pkey PRIMARY KEY (kitsu_id);


--
-- Name: rag_reviews_docs rag_reviews_docs_pkey; Type: CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.rag_reviews_docs
    ADD CONSTRAINT rag_reviews_docs_pkey PRIMARY KEY (doc_id);


--
-- Name: gin_kitsu_core_categories; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX gin_kitsu_core_categories ON manga.kitsu_series_core USING gin (categories_json);


--
-- Name: gin_kitsu_core_genres; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX gin_kitsu_core_genres ON manga.kitsu_series_core USING gin (genres_json);


--
-- Name: gin_kitsu_core_tags; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX gin_kitsu_core_tags ON manga.kitsu_series_core USING gin (tags_all_json);


--
-- Name: gin_ms_reviews_body_tsv; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX gin_ms_reviews_body_tsv ON manga.ms_reviews USING gin (to_tsvector('simple'::regconfig, COALESCE(review_body, ''::text)));


--
-- Name: gin_ms_series_genres_enriched; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX gin_ms_series_genres_enriched ON manga.ms_series_enriched USING gin (series_genres_enriched);


--
-- Name: gin_ms_series_kitsu_tags; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX gin_ms_series_kitsu_tags ON manga.ms_series_enriched USING gin (kitsu_tags_all_json);


--
-- Name: gin_ms_series_synopsis_tsv; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX gin_ms_series_synopsis_tsv ON manga.ms_series_enriched USING gin (to_tsvector('simple'::regconfig, COALESCE(series_synopsis_enriched, ''::text)));


--
-- Name: gin_ms_series_tags_enriched; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX gin_ms_series_tags_enriched ON manga.ms_series_enriched USING gin (series_tags_enriched);


--
-- Name: idx_kitsu_core_popularity_rank; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_kitsu_core_popularity_rank ON manga.kitsu_series_core USING btree (popularity_rank);


--
-- Name: idx_kitsu_core_rating_rank; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_kitsu_core_rating_rank ON manga.kitsu_series_core USING btree (rating_rank);


--
-- Name: idx_ms_kitsu_ambiguous_title_norm; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_ms_kitsu_ambiguous_title_norm ON manga.ms_kitsu_ambiguous USING btree (ms_title_norm);


--
-- Name: idx_ms_kitsu_map_kitsu_id; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_ms_kitsu_map_kitsu_id ON manga.ms_kitsu_map USING btree (kitsu_id);


--
-- Name: idx_ms_kitsu_map_match_method; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_ms_kitsu_map_match_method ON manga.ms_kitsu_map USING btree (match_method);


--
-- Name: idx_ms_reviews_series_id; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_ms_reviews_series_id ON manga.ms_reviews USING btree (series_id);


--
-- Name: idx_ms_reviews_volume_url; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_ms_reviews_volume_url ON manga.ms_reviews USING btree (volume_url);


--
-- Name: idx_ms_series_kitsu_id; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_ms_series_kitsu_id ON manga.ms_series_enriched USING btree (kitsu_id);


--
-- Name: idx_ms_series_match_method; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_ms_series_match_method ON manga.ms_series_enriched USING btree (match_method);


--
-- Name: idx_ms_series_needs_review; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_ms_series_needs_review ON manga.ms_series_enriched USING btree (needs_review);


--
-- Name: idx_ms_volumes_number; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_ms_volumes_number ON manga.ms_volumes_enriched USING btree (volume_number);


--
-- Name: idx_ms_volumes_series_id; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_ms_volumes_series_id ON manga.ms_volumes_enriched USING btree (series_id);


--
-- Name: idx_rag_reviews_docs_series_id; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_rag_reviews_docs_series_id ON manga.rag_reviews_docs USING btree (series_id);


--
-- Name: idx_rag_reviews_docs_volume_url; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_rag_reviews_docs_volume_url ON manga.rag_reviews_docs USING btree (volume_url);


--
-- Name: idx_reviews_all_rag_ready; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_reviews_all_rag_ready ON manga.ms_reviews_all USING btree (rag_ready);


--
-- Name: idx_reviews_all_series_id; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_reviews_all_series_id ON manga.ms_reviews_all USING btree (series_id);


--
-- Name: idx_reviews_all_volume_url; Type: INDEX; Schema: manga; Owner: -
--

CREATE INDEX idx_reviews_all_volume_url ON manga.ms_reviews_all USING btree (volume_url);


--
-- Name: corpus_chunks corpus_chunks_doc_key_fkey; Type: FK CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.corpus_chunks
    ADD CONSTRAINT corpus_chunks_doc_key_fkey FOREIGN KEY (doc_key) REFERENCES bench.corpus_docs(doc_key) ON DELETE CASCADE;


--
-- Name: embedding_runs embedding_runs_chunking_id_fkey; Type: FK CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.embedding_runs
    ADD CONSTRAINT embedding_runs_chunking_id_fkey FOREIGN KEY (chunking_id) REFERENCES bench.chunking_strategies(chunking_id);


--
-- Name: embedding_runs embedding_runs_model_id_fkey; Type: FK CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.embedding_runs
    ADD CONSTRAINT embedding_runs_model_id_fkey FOREIGN KEY (model_id) REFERENCES bench.embedding_models(model_id);


--
-- Name: faiss_indexes faiss_indexes_run_id_fkey; Type: FK CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.faiss_indexes
    ADD CONSTRAINT faiss_indexes_run_id_fkey FOREIGN KEY (run_id) REFERENCES bench.embedding_runs(run_id) ON DELETE CASCADE;


--
-- Name: metrics metrics_run_id_fkey; Type: FK CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.metrics
    ADD CONSTRAINT metrics_run_id_fkey FOREIGN KEY (run_id) REFERENCES bench.embedding_runs(run_id) ON DELETE CASCADE;


--
-- Name: qrels qrels_doc_key_fkey; Type: FK CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.qrels
    ADD CONSTRAINT qrels_doc_key_fkey FOREIGN KEY (doc_key) REFERENCES bench.corpus_docs(doc_key) ON DELETE CASCADE;


--
-- Name: qrels qrels_query_id_fkey; Type: FK CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.qrels
    ADD CONSTRAINT qrels_query_id_fkey FOREIGN KEY (query_id) REFERENCES bench.queries(query_id) ON DELETE CASCADE;


--
-- Name: retrieval_results retrieval_results_chunk_id_fkey; Type: FK CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.retrieval_results
    ADD CONSTRAINT retrieval_results_chunk_id_fkey FOREIGN KEY (chunk_id) REFERENCES bench.corpus_chunks(chunk_id) ON DELETE SET NULL;


--
-- Name: retrieval_results retrieval_results_doc_key_fkey; Type: FK CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.retrieval_results
    ADD CONSTRAINT retrieval_results_doc_key_fkey FOREIGN KEY (doc_key) REFERENCES bench.corpus_docs(doc_key) ON DELETE CASCADE;


--
-- Name: retrieval_results retrieval_results_query_id_fkey; Type: FK CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.retrieval_results
    ADD CONSTRAINT retrieval_results_query_id_fkey FOREIGN KEY (query_id) REFERENCES bench.queries(query_id) ON DELETE CASCADE;


--
-- Name: retrieval_results retrieval_results_run_id_fkey; Type: FK CONSTRAINT; Schema: bench; Owner: -
--

ALTER TABLE ONLY bench.retrieval_results
    ADD CONSTRAINT retrieval_results_run_id_fkey FOREIGN KEY (run_id) REFERENCES bench.embedding_runs(run_id) ON DELETE CASCADE;


--
-- Name: kitsu_series_authors kitsu_series_authors_kitsu_id_fkey; Type: FK CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.kitsu_series_authors
    ADD CONSTRAINT kitsu_series_authors_kitsu_id_fkey FOREIGN KEY (kitsu_id) REFERENCES manga.kitsu_series_core(kitsu_id);


--
-- Name: kitsu_weekly_snapshot kitsu_weekly_snapshot_kitsu_id_fkey; Type: FK CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.kitsu_weekly_snapshot
    ADD CONSTRAINT kitsu_weekly_snapshot_kitsu_id_fkey FOREIGN KEY (kitsu_id) REFERENCES manga.kitsu_series_core(kitsu_id);


--
-- Name: ms_kitsu_ambiguous ms_kitsu_ambiguous_series_id_fkey; Type: FK CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.ms_kitsu_ambiguous
    ADD CONSTRAINT ms_kitsu_ambiguous_series_id_fkey FOREIGN KEY (series_id) REFERENCES manga.ms_series_enriched(series_id) ON DELETE CASCADE;


--
-- Name: ms_kitsu_map ms_kitsu_map_series_id_fkey; Type: FK CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.ms_kitsu_map
    ADD CONSTRAINT ms_kitsu_map_series_id_fkey FOREIGN KEY (series_id) REFERENCES manga.ms_series_enriched(series_id) ON DELETE CASCADE;


--
-- Name: ms_reviews_all ms_reviews_all_series_id_fkey; Type: FK CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.ms_reviews_all
    ADD CONSTRAINT ms_reviews_all_series_id_fkey FOREIGN KEY (series_id) REFERENCES manga.ms_series_enriched(series_id);


--
-- Name: ms_reviews_all ms_reviews_all_volume_url_fkey; Type: FK CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.ms_reviews_all
    ADD CONSTRAINT ms_reviews_all_volume_url_fkey FOREIGN KEY (volume_url) REFERENCES manga.ms_volumes_enriched(volume_url);


--
-- Name: ms_reviews ms_reviews_series_id_fkey; Type: FK CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.ms_reviews
    ADD CONSTRAINT ms_reviews_series_id_fkey FOREIGN KEY (series_id) REFERENCES manga.ms_series_enriched(series_id);


--
-- Name: ms_reviews ms_reviews_volume_url_fkey; Type: FK CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.ms_reviews
    ADD CONSTRAINT ms_reviews_volume_url_fkey FOREIGN KEY (volume_url) REFERENCES manga.ms_volumes_enriched(volume_url);


--
-- Name: ms_volumes_enriched ms_volumes_enriched_series_id_fkey; Type: FK CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.ms_volumes_enriched
    ADD CONSTRAINT ms_volumes_enriched_series_id_fkey FOREIGN KEY (series_id) REFERENCES manga.ms_series_enriched(series_id);


--
-- Name: rag_kitsu_docs rag_kitsu_docs_kitsu_id_fkey; Type: FK CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.rag_kitsu_docs
    ADD CONSTRAINT rag_kitsu_docs_kitsu_id_fkey FOREIGN KEY (kitsu_id) REFERENCES manga.kitsu_series_core(kitsu_id);


--
-- Name: rag_reviews_docs rag_reviews_docs_series_id_fkey; Type: FK CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.rag_reviews_docs
    ADD CONSTRAINT rag_reviews_docs_series_id_fkey FOREIGN KEY (series_id) REFERENCES manga.ms_series_enriched(series_id);


--
-- Name: rag_reviews_docs rag_reviews_docs_volume_url_fkey; Type: FK CONSTRAINT; Schema: manga; Owner: -
--

ALTER TABLE ONLY manga.rag_reviews_docs
    ADD CONSTRAINT rag_reviews_docs_volume_url_fkey FOREIGN KEY (volume_url) REFERENCES manga.ms_volumes_enriched(volume_url);


--
-- PostgreSQL database dump complete
--


