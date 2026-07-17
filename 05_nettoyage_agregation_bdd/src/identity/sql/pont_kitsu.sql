-- Étape C, étage 0 de la cascade : le PONT d'identifiants Kitsu → Wikidata.
--
-- PURES JOINTURES D'IDENTIFIANTS, aucune comparaison de titres. Chaîne :
--   ms_kitsu_map (series_id → kitsu_id)
--     × kitsu_mappings (kitsu_id → mal_id / anilist_id ; sites de manga seuls)
--     × wd_pivot (mal_id → qid ; anilist_id → qid).
--
-- Tout est fait dans UNE transaction (semis + décisions + remplissage) : le
-- pilote Python ouvre la transaction, exécute ce script, lit les compteurs sur
-- la table temporaire encore vivante, puis commit (ou rollback en dry-run).
--
-- Sans paramètre : exécutable en un seul execute() psycopg (protocole simple).

-- ---------------------------------------------------------------------------
-- 1) SEMIS DU MOYEU — une ligne work_identity par série MS, identité NULL.
--    Créé AVANT tout matching ; le matching remplit ensuite, il ne crée plus.
--    ON CONFLICT sur l'index UNIQUE PARTIEL de 001 → idempotent au re-run.
-- ---------------------------------------------------------------------------
INSERT INTO manga.work_identity (series_id)
SELECT series_id FROM manga.ms_series_enriched
ON CONFLICT (series_id) WHERE series_id IS NOT NULL DO NOTHING;

-- 2) ms_series_enriched.work_uid pour TOUTES les séries (FK posée en 003).
UPDATE manga.ms_series_enriched s
SET work_uid = w.work_uid
FROM manga.work_identity w
WHERE w.series_id = s.series_id AND s.work_uid IS NULL;

-- ---------------------------------------------------------------------------
-- 3) CANDIDATS — ms_kitsu_map moins les exclusions, moins les déjà-décidés.
--    Exclusions (rule 3) : ms_kitsu_ambiguous et needs_review historique
--    partent à la CASCADE, pas au pont. v_match_current assure l'idempotence :
--    une série déjà décidée n'est jamais rejouée.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE pont_candidat ON COMMIT DROP AS
WITH cand AS (
    SELECT m.series_id, m.kitsu_id
    FROM manga.ms_kitsu_map m
    WHERE NOT EXISTS (
              SELECT 1 FROM manga.ms_kitsu_ambiguous a
              WHERE a.series_id = m.series_id)
      AND NOT EXISTS (
              SELECT 1 FROM manga.ms_series_enriched s
              WHERE s.series_id = m.series_id AND s.needs_review IS TRUE)
      AND NOT EXISTS (
              SELECT 1 FROM manga.v_match_current v
              WHERE v.series_id = m.series_id)
),
-- Un chemin par (série, identifiant externe) ; qid NULL si l'externe n'est pas
-- dans le pivot. LEFT JOIN : garder les externes sans QID pour les compter.
chemins AS (
    SELECT c.series_id, c.kitsu_id, 'mal'::text AS site,
           km.external_id AS ext, w.qid
    FROM cand c
    JOIN manga.kitsu_mappings km
      ON km.kitsu_id = c.kitsu_id AND km.external_site = 'myanimelist/manga'
    LEFT JOIN manga.wd_pivot w ON w.mal_id = km.external_id
    UNION ALL
    SELECT c.series_id, c.kitsu_id, 'anilist',
           km.external_id, w.qid
    FROM cand c
    JOIN manga.kitsu_mappings km
      ON km.kitsu_id = c.kitsu_id AND km.external_site = 'anilist/manga'
    LEFT JOIN manga.wd_pivot w ON w.anilist_id = km.external_id
),
-- Un kitsu_id peut (rarement) viser deux externes d'un même site : min(ext)
-- fixe un choix déterministe. count(DISTINCT qid) tranche la concordance.
agg AS (
    SELECT series_id, kitsu_id,
           count(DISTINCT qid) FILTER (WHERE qid IS NOT NULL) AS n_qid,
           min(qid) FILTER (WHERE qid IS NOT NULL)            AS qid,
           min(ext) FILTER (WHERE site = 'mal')               AS mal_id,
           min(ext) FILTER (WHERE site = 'anilist')           AS anilist_id
    FROM chemins
    GROUP BY series_id, kitsu_id
)
SELECT
    c.series_id,
    c.kitsu_id,
    a.mal_id,
    a.anilist_id,
    -- Le QID n'est retenu que s'il est unique ; sinon la série n'est pas auto.
    CASE WHEN a.n_qid = 1 THEN a.qid END AS qid,
    COALESCE(a.n_qid, 0)                 AS n_qid,
    (a.mal_id IS NOT NULL OR a.anilist_id IS NOT NULL) AS a_mapping,
    CASE
        WHEN a.n_qid = 1  THEN 'auto'        -- un seul QID (chemins concordants)
        WHEN a.n_qid >= 2 THEN 'divergence'  -- deux QID → arbitrage humain
        ELSE 'hors_pont'                     -- aucun QID → cascade ultérieure
    END AS statut
FROM cand c
LEFT JOIN agg a USING (series_id, kitsu_id);

-- ---------------------------------------------------------------------------
-- 4) COLLISIONS D'UNICITÉ — détectées AVANT insert (rule 4). Un identifiant
--    (qid / kitsu_id / mal_id / anilist_id) visé par deux séries viole les
--    index UNIQUE partiels de 001. Tout le groupe part en needs_review, jamais
--    résolu par ordre d'arrivée. On teste contre les autres auto ET contre
--    l'identité DÉJÀ en base (une série d'un run précédent).
-- ---------------------------------------------------------------------------
UPDATE pont_candidat p
SET statut = 'collision'
WHERE p.statut = 'auto' AND (
       p.qid IN (SELECT qid FROM pont_candidat
                 WHERE statut = 'auto' AND qid IS NOT NULL
                 GROUP BY qid HAVING count(*) > 1)
    OR p.kitsu_id IN (SELECT kitsu_id FROM pont_candidat
                      WHERE statut = 'auto'
                      GROUP BY kitsu_id HAVING count(*) > 1)
    OR (p.mal_id IS NOT NULL AND p.mal_id IN (
            SELECT mal_id FROM pont_candidat
            WHERE statut = 'auto' AND mal_id IS NOT NULL
            GROUP BY mal_id HAVING count(*) > 1))
    OR (p.anilist_id IS NOT NULL AND p.anilist_id IN (
            SELECT anilist_id FROM pont_candidat
            WHERE statut = 'auto' AND anilist_id IS NOT NULL
            GROUP BY anilist_id HAVING count(*) > 1))
    OR p.qid IN (SELECT wikidata_qid FROM manga.work_identity
                 WHERE wikidata_qid IS NOT NULL)
    OR p.kitsu_id::text IN (SELECT kitsu_id FROM manga.work_identity
                            WHERE kitsu_id IS NOT NULL)
    OR (p.mal_id IS NOT NULL AND p.mal_id IN (
            SELECT mal_id FROM manga.work_identity WHERE mal_id IS NOT NULL))
    OR (p.anilist_id IS NOT NULL AND p.anilist_id IN (
            SELECT anilist_id FROM manga.work_identity WHERE anilist_id IS NOT NULL))
);

-- ---------------------------------------------------------------------------
-- 5) JOURNAL — une décision match_decision par série TRAITÉE (rule 5).
--    Les hors_pont ne sont pas traités par le pont : aucune décision, ils
--    iront à la cascade. Append-only : jamais d'UPDATE d'une décision.
-- ---------------------------------------------------------------------------
INSERT INTO manga.match_decision (series_id, wikidata_qid, method, score, status)
SELECT series_id, qid, 'kitsu_bridge', 1.0, 'auto'
FROM pont_candidat WHERE statut = 'auto';

INSERT INTO manga.match_decision (series_id, wikidata_qid, method, score, status)
SELECT series_id, NULL, 'kitsu_bridge', NULL, 'needs_review'
FROM pont_candidat WHERE statut IN ('divergence', 'collision');

-- ---------------------------------------------------------------------------
-- 6) IDENTITÉ — remplissage des SEULES décisions auto (rule 4). needs_review
--    et hors_pont : aucun remplissage. kitsu_id de ms_kitsu_map est un bigint,
--    work_identity le porte en TEXT.
-- ---------------------------------------------------------------------------
UPDATE manga.work_identity w
SET kitsu_id     = p.kitsu_id::text,
    mal_id       = p.mal_id,
    anilist_id   = p.anilist_id,
    wikidata_qid = p.qid,
    updated_at   = now()
FROM pont_candidat p
WHERE p.statut = 'auto' AND w.series_id = p.series_id;
