-- Étage 3 de la cascade : rapprochement FLOU pg_trgm — dernier étage mécanique.
--
-- Une table temporaire est fournie par le pilote Python AVANT ce script :
--   ms_auteur_norm (series_id, auteur_norm) — auteurs MS normalisés par
--     identity.normaliser(). Signal INFORMATIF ici : il n'entre dans aucune
--     décision, l'étage 3 ne décidant rien.
-- Le seuil est posé par le pilote via SET LOCAL pg_trgm.similarity_threshold.
--
-- LA NATURE DE CET ÉTAGE. Il NE PRODUIT AUCUN AUTO. Tout candidat part en
-- needs_review avec son dossier. Le flou PROPOSE, il ne décide jamais — sa
-- raison d'être est que les orphelins arrivent à l'étage R AVEC des candidats
-- plutôt que sans rien.
--
-- POURQUOI L'OPÉRATEUR % ET NON similarity() >= seuil. `%` est un opérateur
-- indexable : il consulte les index GIN trigramme (ms_formes, wd_formes,
-- kitsu_formes) et ne rapproche que les formes partageant assez de trigrammes.
-- `similarity(a,b) >= 0.85` est une fonction de FILTRE : elle force un produit
-- cartésien complet — 10 312 formes du périmètre × 181 106 formes cibles, soit
-- ~1,9 milliard de calculs de similarité. Les deux donnent le même résultat ;
-- seul `%` le donne dans un temps utile. C'est l'optimisation qui rend cet
-- étage exécutable, et elle repose entièrement sur les index posés en 003/006.
-- similarity() reste appelée, mais seulement sur les paires DÉJÀ retenues par
-- l'index, pour en connaître le score exact.

-- ---------------------------------------------------------------------------
-- 1) FORMES DU PÉRIMÈTRE — figées avant tout rapprochement.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE e3_formes ON COMMIT DROP AS
SELECT mf.series_id, mf.forme_norm
FROM manga.ms_formes mf
JOIN manga.ms_series_enriched s ON s.series_id = mf.series_id
WHERE NOT EXISTS (
    SELECT 1 FROM manga.v_match_current v WHERE v.series_id = s.series_id)
  AND mf.forme_norm IS NOT NULL AND mf.forme_norm <> '';

CREATE INDEX ON e3_formes (series_id);

-- ---------------------------------------------------------------------------
-- 2) PAIRES FLOUES — les deux côtés du référentiel, en une seule population.
-- ---------------------------------------------------------------------------
-- Wikidata et Kitsu sont interrogés séparément (leurs index sont distincts)
-- puis réunis : la suite du traitement ignore la provenance, sauf pour lire les
-- signaux auteur/année au bon endroit.
CREATE TEMP TABLE e3_paire ON COMMIT DROP AS
SELECT f.series_id,
       'wd'::text                                   AS cible_type,
       wf.qid                                       AS cible_id,
       similarity(f.forme_norm, wf.forme_norm)      AS sim,
       f.forme_norm                                 AS forme_ms,
       wf.forme_norm                                AS forme_cible
FROM e3_formes f
JOIN manga.wd_formes wf ON wf.forme_norm % f.forme_norm
UNION ALL
SELECT f.series_id,
       'kitsu'::text,
       kf.kitsu_id::text,
       similarity(f.forme_norm, kf.forme_norm),
       f.forme_norm,
       kf.forme_norm
FROM e3_formes f
JOIN manga.kitsu_formes kf ON kf.forme_norm % f.forme_norm;

-- ---------------------------------------------------------------------------
-- 3) DÉDUPLICATION PAR ŒUVRE-CIBLE — un QID ou un kitsu_id = UN candidat.
-- ---------------------------------------------------------------------------
-- Une œuvre-cible porte plusieurs formes (canonical, titres par langue, alias) :
-- sans cette étape, la même œuvre occuperait les trois places du TOP 3 avec
-- trois de ses propres titres, et masquerait les vrais concurrents. On garde la
-- meilleure paire de chaque œuvre, et la forme qui l'a gagnée.
CREATE TEMP TABLE e3_cand ON COMMIT DROP AS
SELECT DISTINCT ON (series_id, cible_type, cible_id)
       series_id, cible_type, cible_id, sim, forme_ms, forme_cible
FROM e3_paire
ORDER BY series_id, cible_type, cible_id, sim DESC, forme_cible;

CREATE INDEX ON e3_cand (series_id);

-- ---------------------------------------------------------------------------
-- 4) SIGNAUX auteur/année — calculés, INFORMATIFS, jamais décisionnels.
-- ---------------------------------------------------------------------------
-- Ils ne changent aucune décision (l'étage n'en prend pas) : ils enrichissent
-- le dossier que l'étage R recevra. Chaque côté est lu chez lui — wd_auteurs /
-- wd_pivot pour Wikidata, kitsu_staff / kitsu_meta pour Kitsu.
CREATE TEMP TABLE e3_cand_signal ON COMMIT DROP AS
SELECT c.*,
    CASE
        WHEN NOT EXISTS (SELECT 1 FROM ms_auteur_norm m
                         WHERE m.series_id = c.series_id)
        THEN 'incomparable'
        WHEN c.cible_type = 'wd' THEN
            CASE
                WHEN NOT EXISTS (SELECT 1 FROM manga.wd_auteurs wa
                                 JOIN manga.wd_auteurs_formes waf
                                   ON waf.auteur_qid = wa.auteur_qid
                                 WHERE wa.qid = c.cible_id)
                THEN 'incomparable'
                WHEN EXISTS (SELECT 1 FROM manga.wd_auteurs wa
                             JOIN manga.wd_auteurs_formes waf
                               ON waf.auteur_qid = wa.auteur_qid
                             JOIN ms_auteur_norm m
                               ON m.auteur_norm = waf.forme_norm
                             WHERE wa.qid = c.cible_id
                               AND m.series_id = c.series_id)
                THEN 'concordant' ELSE 'discordant'
            END
        ELSE
            CASE
                WHEN NOT EXISTS (SELECT 1 FROM manga.kitsu_staff ks
                                 WHERE ks.kitsu_id = c.cible_id::bigint)
                THEN 'incomparable'
                WHEN EXISTS (SELECT 1 FROM manga.kitsu_staff ks
                             JOIN ms_auteur_norm m
                               ON m.auteur_norm = ks.personne_norm
                             WHERE ks.kitsu_id = c.cible_id::bigint
                               AND m.series_id = c.series_id)
                THEN 'concordant' ELSE 'discordant'
            END
    END AS signal_auteur,
    -- L'écart d'année brut, pas un verdict : l'étage ne tranche pas, et la
    -- politique de bandes (§26) vaudra pour les étages qui DÉCIDENT.
    CASE WHEN c.cible_type = 'wd'
         THEN (SELECT s.series_year - p.annee
               FROM manga.ms_series_enriched s, manga.wd_pivot p
               WHERE s.series_id = c.series_id AND p.qid = c.cible_id)
         ELSE (SELECT s.series_year - km.annee
               FROM manga.ms_series_enriched s, manga.kitsu_meta km
               WHERE s.series_id = c.series_id
                 AND km.kitsu_id = c.cible_id::bigint)
    END AS ecart_annee
FROM e3_cand c;

-- ---------------------------------------------------------------------------
-- 5) TOP 3 PAR SÉRIE — le dossier remis à l'étage R.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE e3_top ON COMMIT DROP AS
SELECT * FROM (
    SELECT cs.*,
           row_number() OVER (PARTITION BY cs.series_id
                              ORDER BY cs.sim DESC, cs.cible_type, cs.cible_id)
               AS rang,
           count(*)     OVER (PARTITION BY cs.series_id) AS n_cand_total,
           max(cs.sim)  OVER (PARTITION BY cs.series_id) AS sim_max
    FROM e3_cand_signal cs
) z
WHERE rang <= 3;

CREATE INDEX ON e3_top (series_id);

-- ---------------------------------------------------------------------------
-- 6) JOURNAL — needs_review UNIQUEMENT, avec les candidats DANS details.
-- ---------------------------------------------------------------------------
-- Les candidats vivent en base, pas seulement dans un CSV : c'est la leçon de
-- la dépendance à l'artefact relevée au rapport de l'étage 2. Un dossier doit
-- être ré-instruisable depuis la SEULE base.
--
-- wikidata_qid reste NULL : l'étage ne conclut pas, et remplir la colonne
-- laisserait croire à une identité retenue. Le QID candidat, lui, est dans
-- details — proposé, pas décidé.
INSERT INTO manga.match_decision
    (series_id, wikidata_qid, method, score, status, details)
SELECT t.series_id,
       NULL,
       'trgm',
       t.sim_max,
       'needs_review',
       jsonb_build_object(
           'case', 'trgm_candidats',
           'n', t.n_cand_total,
           'top', jsonb_agg(
                      jsonb_build_object(
                          'cible',  t.cible_type || ':' || t.cible_id,
                          'sim',    round(t.sim::numeric, 3),
                          'forme_ms',     t.forme_ms,
                          'forme_cible',  t.forme_cible,
                          'auteur', t.signal_auteur,
                          'ecart_annee', t.ecart_annee)
                      ORDER BY t.rang))
FROM e3_top t
GROUP BY t.series_id, t.n_cand_total, t.sim_max;

-- ---------------------------------------------------------------------------
-- 7) AUCUN REMPLISSAGE D'IDENTITÉ.
-- ---------------------------------------------------------------------------
-- Volontairement vide. L'étage 3 n'écrit RIEN dans work_identity : il ne
-- retient aucune identité, il propose des pistes. Toute écriture ici serait une
-- décision déguisée.
