-- Étage 1 de la cascade : jointure EXACTE multi-formes MS × Wikidata.
--
-- Deux tables temporaires sont fournies par le pilote Python AVANT ce script :
--   ms_auteur_norm (series_id, auteur_norm) — scénaristes/dessinateurs MS
--     normalisés par identity.normaliser(). JAMAIS de normalisation SQL ad hoc :
--     les deux côtés du rapprochement doivent parler la même langue.
--   etage1_param (borne_basse, borne_haute) — la fenêtre d'année CALIBRÉE en
--     phase 1 sur les 1 689 identités sûres du pont.
--
-- Sans paramètre : exécutable en un seul execute() psycopg.

-- ---------------------------------------------------------------------------
-- 1) CANDIDATS — une ligne par (série, qid) avec ses deux signaux.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE etage1_candidat ON COMMIT DROP AS
WITH perimetre AS (
    -- Idempotence : une série déjà décidée n'est jamais rejouée.
    SELECT s.series_id, s.series_year
    FROM manga.ms_series_enriched s
    WHERE NOT EXISTS (
        SELECT 1 FROM manga.v_match_current v WHERE v.series_id = s.series_id)
),
paires AS (
    -- Jointure exacte sur la forme normalisée, toutes formes confondues
    -- (titre ET alias des deux côtés). DISTINCT : deux formes différentes
    -- d'une même série peuvent viser le même qid.
    SELECT DISTINCT p.series_id, wf.qid
    FROM perimetre p
    JOIN manga.ms_formes mf
      ON mf.series_id = p.series_id
     AND mf.forme_norm IS NOT NULL AND mf.forme_norm <> ''
    JOIN manga.wd_formes wf
      ON wf.forme_norm = mf.forme_norm
     AND wf.forme_norm IS NOT NULL AND wf.forme_norm <> ''
)
SELECT
    c.series_id,
    c.qid,
    p.series_year,
    wp.annee AS annee_wd,
    -- Signal AUTEUR : concordant / discordant / incomparable.
    CASE
        WHEN NOT EXISTS (SELECT 1 FROM ms_auteur_norm m
                         WHERE m.series_id = c.series_id)
          OR NOT EXISTS (SELECT 1 FROM manga.wd_auteurs wa
                         JOIN manga.wd_auteurs_formes waf
                           ON waf.auteur_qid = wa.auteur_qid
                         WHERE wa.qid = c.qid)
        THEN 'incomparable'
        WHEN EXISTS (SELECT 1 FROM manga.wd_auteurs wa
                     JOIN manga.wd_auteurs_formes waf
                       ON waf.auteur_qid = wa.auteur_qid
                     JOIN ms_auteur_norm m
                       ON m.auteur_norm = waf.forme_norm
                     WHERE wa.qid = c.qid AND m.series_id = c.series_id)
        THEN 'concordant'
        ELSE 'discordant'
    END AS signal_auteur,
    -- Signal ANNÉE : la fenêtre est un CONFIRMATEUR, jamais un discriminant
    -- seul vers l'auto (cf. matrice).
    CASE
        WHEN p.series_year IS NULL OR wp.annee IS NULL THEN 'incomparable'
        WHEN (p.series_year - wp.annee)
             BETWEEN (SELECT borne_basse FROM etage1_param)
                 AND (SELECT borne_haute FROM etage1_param)
        THEN 'concordant'
        ELSE 'discordant'
    END AS signal_annee
FROM paires c
JOIN perimetre p ON p.series_id = c.series_id
JOIN manga.wd_pivot wp ON wp.qid = c.qid;

CREATE INDEX ON etage1_candidat (series_id);
CREATE INDEX ON etage1_candidat (qid);

-- ---------------------------------------------------------------------------
-- 2) DÉCISION PAR SÉRIE — application de la matrice figée.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE etage1_serie ON COMMIT DROP AS
WITH agg AS (
    SELECT series_id,
           count(*) AS n_cand,
           -- Un candidat « départageable par l'auteur » ne l'est que si l'année
           -- ne le contredit pas : une année discordante interdit l'auto.
           count(*) FILTER (WHERE signal_auteur = 'concordant'
                              AND signal_annee <> 'discordant') AS n_auteur_ok,
           count(*) FILTER (WHERE signal_annee = 'concordant')  AS n_annee_ok
    FROM etage1_candidat
    GROUP BY series_id
),
gagnant AS (
    -- Le seul candidat (n_cand = 1), ou celui que l'auteur départage seul.
    SELECT c.*
    FROM etage1_candidat c
    JOIN agg a USING (series_id)
    WHERE a.n_cand = 1
       OR (a.n_cand > 1 AND a.n_auteur_ok = 1
           AND c.signal_auteur = 'concordant' AND c.signal_annee <> 'discordant')
)
SELECT
    a.series_id,
    g.qid,
    a.n_cand,
    g.signal_auteur,
    g.signal_annee,
    CASE
        -- Règle transverse : une année discordante n'est JAMAIS auto.
        WHEN g.signal_annee = 'discordant' THEN 'review_annee_discordante'
        WHEN a.n_cand = 1 AND g.signal_auteur = 'concordant'
            THEN 'auto_unique_auteur'
        WHEN a.n_cand = 1 AND g.signal_auteur = 'incomparable'
             AND g.signal_annee = 'concordant'
            THEN 'auto_unique_annee'
        WHEN a.n_cand = 1 AND g.signal_auteur = 'incomparable'
             AND g.signal_annee = 'incomparable'
            THEN 'review_sans_signal'
        WHEN a.n_cand = 1 AND g.signal_auteur = 'discordant'
            THEN 'review_auteur_discordant'
        WHEN a.n_cand > 1 AND g.signal_auteur = 'concordant'
            THEN 'auto_multi_auteur'
        -- Multi sans départage par l'auteur : l'année seule ne tranche pas.
        WHEN a.n_annee_ok = 1 THEN 'review_multi_annee_seule'
        ELSE 'review_ambiguite'
    END AS cas
FROM agg a
LEFT JOIN gagnant g USING (series_id);

-- ---------------------------------------------------------------------------
-- 3) COLLISIONS — détectées AVANT insert, jamais résolues par ordre d'arrivée.
-- ---------------------------------------------------------------------------
-- 3a. Deux séries MS visant le même QID (entre elles, ou contre une identité
--     déjà en base issue du pont).
UPDATE etage1_serie s
SET cas = 'review_collision_qid'
WHERE s.cas LIKE 'auto%' AND (
       s.qid IN (SELECT qid FROM etage1_serie
                 WHERE cas LIKE 'auto%' AND qid IS NOT NULL
                 GROUP BY qid HAVING count(*) > 1)
    OR s.qid IN (SELECT wikidata_qid FROM manga.work_identity
                 WHERE wikidata_qid IS NOT NULL));

-- 3b. L'identité arrive complète (qid + mal_id + anilist_id) : si l'un de ces
--     identifiants est DÉJÀ pris par une autre série, les index UNIQUE partiels
--     de 001 refuseraient l'insert. Tout le groupe part en review.
UPDATE etage1_serie s
SET cas = 'review_collision_id'
FROM manga.wd_pivot p
WHERE s.qid = p.qid AND s.cas LIKE 'auto%' AND (
       (p.mal_id IS NOT NULL AND (
            p.mal_id IN (SELECT mal_id FROM manga.work_identity
                         WHERE mal_id IS NOT NULL)
         OR p.mal_id IN (SELECT w.mal_id FROM etage1_serie e
                         JOIN manga.wd_pivot w ON w.qid = e.qid
                         WHERE e.cas LIKE 'auto%' AND w.mal_id IS NOT NULL
                         GROUP BY w.mal_id HAVING count(*) > 1)))
    OR (p.anilist_id IS NOT NULL AND (
            p.anilist_id IN (SELECT anilist_id FROM manga.work_identity
                             WHERE anilist_id IS NOT NULL)
         OR p.anilist_id IN (SELECT w.anilist_id FROM etage1_serie e
                             JOIN manga.wd_pivot w ON w.qid = e.qid
                             WHERE e.cas LIKE 'auto%' AND w.anilist_id IS NOT NULL
                             GROUP BY w.anilist_id HAVING count(*) > 1))));

-- ---------------------------------------------------------------------------
-- 4) JOURNAL — une décision par série TRAITÉE (auto ET needs_review).
-- ---------------------------------------------------------------------------
-- method : la valeur du CHECK de 001 qui décrit COMMENT la décision est prise.
-- 'exact_author' quand l'auteur a tranché, 'exact' sinon — les needs_review
-- portent 'exact', l'étage n'ayant rien conclu.
INSERT INTO manga.match_decision (series_id, wikidata_qid, method, score, status)
SELECT series_id, qid,
       CASE WHEN cas IN ('auto_unique_auteur', 'auto_multi_auteur')
            THEN 'exact_author' ELSE 'exact' END,
       CASE cas WHEN 'auto_unique_auteur' THEN 0.97
                WHEN 'auto_multi_auteur'  THEN 0.95
                WHEN 'auto_unique_annee'  THEN 0.93 END,
       'auto'
FROM etage1_serie WHERE cas LIKE 'auto%';

INSERT INTO manga.match_decision (series_id, wikidata_qid, method, score, status)
SELECT series_id, NULL, 'exact', NULL, 'needs_review'
FROM etage1_serie WHERE cas LIKE 'review%';

-- ---------------------------------------------------------------------------
-- 5) IDENTITÉ — remplissage des seules décisions auto, identité COMPLÈTE.
-- ---------------------------------------------------------------------------
UPDATE manga.work_identity w
SET wikidata_qid = s.qid,
    mal_id       = p.mal_id,
    anilist_id   = p.anilist_id,
    updated_at   = now()
FROM etage1_serie s
JOIN manga.wd_pivot p ON p.qid = s.qid
WHERE s.cas LIKE 'auto%' AND w.series_id = s.series_id;
