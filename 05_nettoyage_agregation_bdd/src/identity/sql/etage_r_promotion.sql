-- Étage R, run 2 — assemblage de la population À PROMOUVOIR. Construit des
-- tables temporaires ; N'ÉCRIT RIEN dans match_decision / work_identity (les
-- écritures sont des INSERT SELECT / UPDATE FROM séparés, appliqués seulement
-- hors dry-run).
--
-- POPULATION : verdict LLM 'same_work' confiance 'haute' (phase='file'), non
-- partiel, série encore needs_review, à candidat UNIQUE (les séries à candidats
-- multiples — 60 mesurées — sont EXCLUES : unicité, jamais par ordre d'arrivée).
-- Chaque promue reçoit une identité dérivée du candidat (wd_pivot / kitsu_mappings)
-- et une strate traçable. Les collisions inter-séries sur un index UNIQUE partiel
-- de work_identity excluent le GROUPE entier.

-- ---------------------------------------------------------------------------
-- 1. Les avis promouvables (avant unicité)
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE promo_avis ON COMMIT DROP AS
SELECT a.avis_id, a.series_id, a.candidat_type, a.candidat_id,
       a.modele, a.prompt_version, a.pre_validation_bandes,
       d.details->>'case' AS cas_origine
FROM manga.llm_avis a
JOIN manga.v_match_current v ON v.series_id = a.series_id
JOIN manga.match_decision d ON d.decision_id = v.decision_id
WHERE a.phase = 'file' AND a.verdict = 'same_work' AND a.confiance = 'haute'
  AND a.dossier_partiel = false
  AND v.status = 'needs_review';

-- ---------------------------------------------------------------------------
-- 2. Séries à candidat UNIQUE (les multi sont écartées vers l'humain)
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE promo_unique ON COMMIT DROP AS
SELECT * FROM promo_avis
WHERE series_id IN (
    SELECT series_id FROM promo_avis GROUP BY series_id HAVING count(*) = 1
);

-- ---------------------------------------------------------------------------
-- 3. Identité dérivée + strate
-- ---------------------------------------------------------------------------
-- QID pour un candidat Kitsu : par le PONT (mal partagé), et SEULEMENT si le
-- mal désigne un qid UNIQUE dans wd_pivot — sinon NULL (identité partielle
-- mal/anilist légitime). Aucun risque de divergence ici : le candidat est unique.
CREATE TEMP TABLE promo_identite ON COMMIT DROP AS
WITH base AS (
    SELECT u.*,
        CASE WHEN u.candidat_type = 'kitsu_id'
             THEN (SELECT km.external_id FROM manga.kitsu_mappings km
                   WHERE km.kitsu_id = u.candidat_id::bigint
                     AND km.external_site = 'myanimelist/manga' LIMIT 1)
             ELSE (SELECT p.mal_id FROM manga.wd_pivot p WHERE p.qid = u.candidat_id)
        END AS d_mal,
        CASE WHEN u.candidat_type = 'kitsu_id'
             THEN (SELECT km.external_id FROM manga.kitsu_mappings km
                   WHERE km.kitsu_id = u.candidat_id::bigint
                     AND km.external_site = 'anilist/manga' LIMIT 1)
             ELSE (SELECT p.anilist_id FROM manga.wd_pivot p WHERE p.qid = u.candidat_id)
        END AS d_anilist,
        CASE WHEN u.candidat_type = 'kitsu_id' THEN u.candidat_id ELSE NULL END AS d_kitsu
    FROM promo_unique u
)
SELECT b.*,
    CASE WHEN b.candidat_type = 'qid' THEN b.candidat_id
         ELSE (SELECT CASE WHEN count(*) = 1 THEN min(p.qid) END
               FROM manga.wd_pivot p WHERE p.mal_id = b.d_mal)
    END AS d_qid,
    CASE WHEN b.pre_validation_bandes THEN 'promo_seau_adjacent'
         WHEN b.cas_origine = 'review_k_auteur_discordant'
              THEN 'promo_auteur_pseudonyme_ou_romanisation'
         ELSE 'promo_llm_same_haute'
    END AS strate
FROM base b;

-- ---------------------------------------------------------------------------
-- 4. Collisions — sur un index UNIQUE partiel de work_identity
-- ---------------------------------------------------------------------------
-- Externe : la valeur dérivée est déjà portée par une AUTRE série en base.
-- Interne : deux séries du lot dérivent la même valeur.
-- Dans les deux cas, le GROUPE entier est exclu (jamais résolu par ordre).
CREATE TEMP TABLE promo_collision ON COMMIT DROP AS
WITH vals AS (
    SELECT series_id, 'qid'::text AS col, d_qid AS val FROM promo_identite
        WHERE d_qid IS NOT NULL
    UNION ALL SELECT series_id, 'kitsu', d_kitsu FROM promo_identite
        WHERE d_kitsu IS NOT NULL
    UNION ALL SELECT series_id, 'mal', d_mal FROM promo_identite
        WHERE d_mal IS NOT NULL
    UNION ALL SELECT series_id, 'anilist', d_anilist FROM promo_identite
        WHERE d_anilist IS NOT NULL
),
externe AS (
    SELECT p.series_id FROM promo_identite p JOIN manga.work_identity w
        ON w.wikidata_qid = p.d_qid WHERE p.d_qid IS NOT NULL AND w.series_id <> p.series_id
    UNION SELECT p.series_id FROM promo_identite p JOIN manga.work_identity w
        ON w.kitsu_id = p.d_kitsu WHERE p.d_kitsu IS NOT NULL AND w.series_id <> p.series_id
    UNION SELECT p.series_id FROM promo_identite p JOIN manga.work_identity w
        ON w.mal_id = p.d_mal WHERE p.d_mal IS NOT NULL AND w.series_id <> p.series_id
    UNION SELECT p.series_id FROM promo_identite p JOIN manga.work_identity w
        ON w.anilist_id = p.d_anilist WHERE p.d_anilist IS NOT NULL AND w.series_id <> p.series_id
),
interne AS (
    SELECT v.series_id FROM vals v
    JOIN (SELECT col, val FROM vals GROUP BY col, val
          HAVING count(DISTINCT series_id) > 1) dup
      ON dup.col = v.col AND dup.val = v.val
)
SELECT series_id, 'externe'::text AS nature FROM externe
UNION SELECT series_id, 'interne' FROM interne WHERE series_id NOT IN (SELECT series_id FROM externe);

-- ---------------------------------------------------------------------------
-- 5. La population finale à promouvoir
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE promo_final ON COMMIT DROP AS
SELECT * FROM promo_identite
WHERE series_id NOT IN (SELECT series_id FROM promo_collision);
