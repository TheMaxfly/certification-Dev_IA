-- Promotion staging.ms_* -> manga.ms_* (cycle mensuel).
--
-- Ce fichier n'est PAS une migration : il est rejoué à chaque cycle, alors
-- qu'une migration est jouée une fois. Il est versionné et exécuté par
-- `identity.charger_ms`, jamais collé à la main dans un client SQL.
--
-- ---------------------------------------------------------------------------
-- UPSERT, JAMAIS DE DELETE
-- ---------------------------------------------------------------------------
-- Trois INSERT ... ON CONFLICT DO UPDATE, de clés series_id / volume_url /
-- review_url. Une fiche absente du snapshot du mois RESTE en base, inchangée :
-- disparaître de Manga Sanctuary n'est pas une preuve d'inexistence, et les
-- tables aval (rag_reviews_docs, ms_kitsu_map, ms_kitsu_ambiguous) référencent
-- ces lignes. Aucun DELETE ici, donc aucune FK ne peut casser.
--
-- ORDRE IMPOSÉ par les FK : séries, puis volumes (FK -> séries), puis critiques
-- (FK -> séries et volumes). Une série nouvelle du snapshot doit exister avant
-- les volumes qui la citent.
--
-- ---------------------------------------------------------------------------
-- CE QUE LES DO UPDATE NE TOUCHENT PAS — le point le plus important du fichier
-- ---------------------------------------------------------------------------
-- Les tables cibles portent bien plus que les champs du scraping :
--   - l'enrichissement Kitsu (kitsu_*, series_*_enriched) et les décisions de
--     rapprochement (match_method, match_score, kitsu_id, needs_review…) —
--     5 608 séries matchées, résultat d'un travail qu'un rechargement ne doit
--     pas effacer ;
--   - les agrégats calculés (series_score_mean, review_count, rag_ready…) ;
--   - work_uid, que seule la cascade (étape C) a le droit de renseigner.
-- Chaque DO UPDATE n'énumère donc QUE les colonnes issues du fichier source.
-- Une colonne absente de la liste garde sa valeur : c'est délibéré, et c'est ce
-- qui rend le rechargement mensuel sûr.
--
-- ---------------------------------------------------------------------------
-- TYPAGE
-- ---------------------------------------------------------------------------
-- Le staging est tout-TEXT ; le typage se fait ici. `NULLIF(x, '')` d'abord :
-- une chaîne vide n'est pas un zéro. Les dates arrivent déjà en ISO
-- (`*_date_iso`, calculées par identity.dates), d'où un simple `::date` —
-- jamais de `to_date` sur du français, qui dépendrait du lc_time du serveur.

-- ---------------------------------------------------------------------------
-- 1. Séries — grain dérivé du fichier volumes (aucun *_series.jsonl n'existe)
-- ---------------------------------------------------------------------------
-- DISTINCT ON (series_id) : le fichier volumes est dénormalisé, chaque volume
-- répète les attributs de sa série. Vérifié sur le snapshot 2026-07 : ces
-- attributs sont CONSTANTS sur les 103 811 lignes des 14 652 séries — aucun
-- désaccord entre deux volumes d'une même série. Le choix de la ligne est donc
-- sans effet ; `ORDER BY volume_url` le rend malgré tout reproductible plutôt
-- que dépendant du plan d'exécution.
INSERT INTO manga.ms_series_enriched (
    series_id, series_url, series_title, series_type, series_category,
    series_year, series_other_titles, series_dessinateur, series_scenariste,
    series_genres, series_tags, series_mag_prepub, series_statuses,
    series_popularity_rank, series_members_rating, series_members_votes,
    series_experts_rating, series_experts_votes, series_synopsis,
    series_related_works
)
SELECT DISTINCT ON (s.series_id)
    s.series_id::bigint,
    NULLIF(s.series_url, ''),
    NULLIF(s.series_title, ''),
    NULLIF(s.series_type, ''),
    NULLIF(s.series_category, ''),
    NULLIF(s.series_year, '')::integer,
    NULLIF(s.series_other_titles, '')::jsonb,
    NULLIF(s.series_dessinateur, ''),
    NULLIF(s.series_scenariste, ''),
    NULLIF(s.series_genres, '')::jsonb,
    NULLIF(s.series_tags, '')::jsonb,
    NULLIF(s.series_mag_prepub, ''),
    NULLIF(s.series_statuses, '')::jsonb,
    NULLIF(s.series_popularity_rank, '')::bigint,
    NULLIF(s.series_members_rating, '')::double precision,
    NULLIF(s.series_members_votes, '')::bigint,
    NULLIF(s.series_experts_rating, '')::double precision,
    NULLIF(s.series_experts_votes, '')::bigint,
    NULLIF(s.series_synopsis, ''),
    NULLIF(s.series_related_works, '')::jsonb
FROM staging.ms_volumes s
WHERE NULLIF(s.series_id, '') IS NOT NULL
ORDER BY s.series_id, s.volume_url
ON CONFLICT (series_id) DO UPDATE SET
    series_url             = EXCLUDED.series_url,
    series_title           = EXCLUDED.series_title,
    series_type            = EXCLUDED.series_type,
    series_category        = EXCLUDED.series_category,
    series_year            = EXCLUDED.series_year,
    series_other_titles    = EXCLUDED.series_other_titles,
    series_dessinateur     = EXCLUDED.series_dessinateur,
    series_scenariste      = EXCLUDED.series_scenariste,
    series_genres          = EXCLUDED.series_genres,
    series_tags            = EXCLUDED.series_tags,
    series_mag_prepub      = EXCLUDED.series_mag_prepub,
    series_statuses        = EXCLUDED.series_statuses,
    series_popularity_rank = EXCLUDED.series_popularity_rank,
    series_members_rating  = EXCLUDED.series_members_rating,
    series_members_votes   = EXCLUDED.series_members_votes,
    series_experts_rating  = EXCLUDED.series_experts_rating,
    series_experts_votes   = EXCLUDED.series_experts_votes,
    series_synopsis        = EXCLUDED.series_synopsis,
    series_related_works   = EXCLUDED.series_related_works;
-- Volontairement absents : kitsu_*, match_*, needs_review, work_uid,
-- series_synopsis_enriched, series_*_enriched, et tous les agrégats.

-- ---------------------------------------------------------------------------
-- 2. Volumes
-- ---------------------------------------------------------------------------
-- volume_ean est posé BRUT : la traçabilité de ce que la source a affiché.
-- Sa lecture normalisée et validée vit dans manga.volume_identity, peuplée par
-- le chargeur (contrôle de la clé EAN-13 en Python, jamais en SQL).
--
-- volume_members_rating (13,64 % renseigné) n'est PAS promu : la table n'a pas
-- de colonne pour lui, alors que volume_experts_rating existe. Écart du schéma
-- historique. La valeur reste dans staging.ms_volumes et dans le raw.
INSERT INTO manga.ms_volumes_enriched (
    volume_url, series_id, volume_title, volume_number, volume_publication_date,
    volume_dessinateur, volume_scenariste, volume_editeur, volume_ean,
    volume_format, volume_pages, volume_country, volume_status,
    volume_tomes_published, volume_tomes_total, volume_members_votes,
    volume_experts_rating, volume_experts_votes, volume_synopsis
)
SELECT
    v.volume_url,
    NULLIF(v.series_id, '')::bigint,
    NULLIF(v.volume_title, ''),
    NULLIF(v.volume_number, '')::integer,
    NULLIF(v.volume_publication_date_iso, '')::date,
    NULLIF(v.volume_dessinateur, ''),
    NULLIF(v.volume_scenariste, ''),
    NULLIF(v.volume_editeur, ''),
    NULLIF(v.volume_ean, ''),
    NULLIF(v.volume_format, ''),
    NULLIF(v.volume_pages, '')::integer,
    NULLIF(v.volume_country, ''),
    NULLIF(v.volume_status, ''),
    NULLIF(v.volume_tomes_published, '')::integer,
    NULLIF(v.volume_tomes_total, '')::integer,
    NULLIF(v.volume_members_votes, '')::bigint,
    NULLIF(v.volume_experts_rating, '')::double precision,
    NULLIF(v.volume_experts_votes, '')::bigint,
    NULLIF(v.volume_synopsis, '')
FROM staging.ms_volumes v
WHERE NULLIF(v.volume_url, '') IS NOT NULL
ON CONFLICT (volume_url) DO UPDATE SET
    series_id               = EXCLUDED.series_id,
    volume_title            = EXCLUDED.volume_title,
    volume_number           = EXCLUDED.volume_number,
    volume_publication_date = EXCLUDED.volume_publication_date,
    volume_dessinateur      = EXCLUDED.volume_dessinateur,
    volume_scenariste       = EXCLUDED.volume_scenariste,
    volume_editeur          = EXCLUDED.volume_editeur,
    volume_ean              = EXCLUDED.volume_ean,
    volume_format           = EXCLUDED.volume_format,
    volume_pages            = EXCLUDED.volume_pages,
    volume_country          = EXCLUDED.volume_country,
    volume_status           = EXCLUDED.volume_status,
    volume_tomes_published  = EXCLUDED.volume_tomes_published,
    volume_tomes_total      = EXCLUDED.volume_tomes_total,
    volume_members_votes    = EXCLUDED.volume_members_votes,
    volume_experts_rating   = EXCLUDED.volume_experts_rating,
    volume_experts_votes    = EXCLUDED.volume_experts_votes,
    volume_synopsis         = EXCLUDED.volume_synopsis;
-- Volontairement absents : review_count, score_*, with_*, first/last_review_*.

-- ---------------------------------------------------------------------------
-- 3. Critiques
-- ---------------------------------------------------------------------------
-- review_date_raw garde le texte de la source ; review_date_iso sa lecture,
-- NULL pour les 29,65 % de dates tronquées au jour de la semaine (« jeu. »).
-- review_date_parse_ok rend cet échec MESURABLE plutôt que silencieux : il se
-- déduit de l'ISO, il n'est pas re-jugé ici.
--
-- review_grain n'est pas listé : son DEFAULT 'volume' (003) s'applique aux
-- insertions, et un rechargement n'a pas à re-trancher le grain d'une critique
-- déjà en base.
--
-- La critique au corps vide (1 dans le snapshot) est promue comme les autres :
-- une critique sans texte reste une critique — elle a un auteur, une note, un
-- tome. La filtrer ici la ferait disparaître des comptes sans laisser de trace.
INSERT INTO manga.ms_reviews_all (
    review_url, series_id, series_title, series_url, volume_number, volume_url,
    review_title, review_score, review_author, review_date_raw, review_date_iso,
    review_date_parse_ok, review_type, review_body
)
SELECT
    r.review_url,
    NULLIF(r.series_id, '')::bigint,
    NULLIF(r.series_title, ''),
    NULLIF(r.series_url, ''),
    NULLIF(r.volume_number, '')::integer,
    NULLIF(r.volume_url, ''),
    NULLIF(r.review_title, ''),
    NULLIF(r.review_score, '')::double precision,
    NULLIF(r.review_author, ''),
    NULLIF(r.review_date, ''),
    NULLIF(r.review_date_iso, '')::date,
    NULLIF(r.review_date_iso, '') IS NOT NULL,
    NULLIF(r.review_type, ''),
    r.review_body
FROM staging.ms_reviews r
WHERE NULLIF(r.review_url, '') IS NOT NULL
ON CONFLICT (review_url) WHERE review_url IS NOT NULL DO UPDATE SET
    series_id            = EXCLUDED.series_id,
    series_title         = EXCLUDED.series_title,
    series_url           = EXCLUDED.series_url,
    volume_number        = EXCLUDED.volume_number,
    volume_url           = EXCLUDED.volume_url,
    review_title         = EXCLUDED.review_title,
    review_score         = EXCLUDED.review_score,
    review_author        = EXCLUDED.review_author,
    review_date_raw      = EXCLUDED.review_date_raw,
    review_date_iso      = EXCLUDED.review_date_iso,
    review_date_parse_ok = EXCLUDED.review_date_parse_ok,
    review_type          = EXCLUDED.review_type,
    review_body          = EXCLUDED.review_body;
-- Volontairement absents : review_id (séquence), review_grain, source_line,
-- rag_text, rag_len, rag_ready (alimentent rag_reviews_docs).
