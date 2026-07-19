-- Étage R — assemblage des dossiers soumis au juge. LECTURE SEULE.
--
-- Le pilote Python fournit AVANT ce script :
--   ms_auteur_norm (series_id, auteur_norm) — auteurs MS normalisés par
--     identity.normaliser(), la seule normalisation du projet.
--
-- CE QUE CE SCRIPT PRODUIT. Une table temporaire `r_candidat`, un candidat par
-- ligne, portant de quoi juger : identité MS, identité de la cible, et les
-- signaux calculés. L'assemblage en dossier (regroupement par série, mise en
-- forme du prompt) se fait en Python — le SQL fournit la matière, pas la mise
-- en page.
--
-- POURQUOI RECALCULER LES CANDIDATS DES ÉTAGES 1-2. Leurs décisions ont été
-- journalisées avant l'existence de `details` (étage 1) ou sans la liste des
-- candidats (étage 2) : seul l'étage 3 les porte en base. Le recalcul est
-- désormais SÛR, ce qu'il n'était pas pendant la cascade : le périmètre est
-- figé, plus aucun étage n'écrit dans work_identity, donc un candidat
-- recalculé aujourd'hui sera le même demain (§28.1).
--
-- LES 52 COLLISIONS FONT EXCEPTION. Elles dépendaient de l'état de
-- work_identity au moment du run de l'étage 1, état que l'étage 2 a modifié.
-- Elles sont marquées `dossier_partiel` — le marquage DIT l'incomplétude au
-- lieu de laisser croire à un dossier complet.

-- ---------------------------------------------------------------------------
-- 1. LA FILE — figée avant tout calcul.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE r_file ON COMMIT DROP AS
SELECT v.series_id,
       v.decision_id,
       d.method,
       d.score,
       d.details,
       CASE WHEN d.method = 'trgm'              THEN 'etage3'
            WHEN d.method LIKE 'exact_kitsu%'   THEN 'etage2'
            ELSE 'etage1' END AS origine
FROM manga.v_match_current v
JOIN manga.match_decision d ON d.decision_id = v.decision_id
WHERE v.status = 'needs_review';

CREATE INDEX ON r_file (series_id);
CREATE INDEX ON r_file (origine);

-- ---------------------------------------------------------------------------
-- 2. CANDIDATS DE L'ÉTAGE 1 — recalcul MS × Wikidata sur forme exacte.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE r_candidat_e1 ON COMMIT DROP AS
SELECT DISTINCT f.series_id, 'qid'::text AS candidat_type, wf.qid AS candidat_id
FROM r_file f
JOIN manga.ms_formes mf
  ON mf.series_id = f.series_id AND mf.forme_norm <> ''
JOIN manga.wd_formes wf ON wf.forme_norm = mf.forme_norm
WHERE f.origine = 'etage1';

-- ---------------------------------------------------------------------------
-- 3. CANDIDATS DE L'ÉTAGE 2 — recalcul MS × Kitsu sur forme exacte.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE r_candidat_e2 ON COMMIT DROP AS
SELECT DISTINCT f.series_id,
       'kitsu_id'::text     AS candidat_type,
       kf.kitsu_id::text    AS candidat_id
FROM r_file f
JOIN manga.ms_formes mf
  ON mf.series_id = f.series_id AND mf.forme_norm <> ''
JOIN manga.kitsu_formes kf ON kf.forme_norm = mf.forme_norm
WHERE f.origine = 'etage2';

-- ---------------------------------------------------------------------------
-- 4. CANDIDATS DE L'ÉTAGE 3 — lus DEPUIS details, pas recalculés.
-- ---------------------------------------------------------------------------
-- L'étage 3 a écrit ses candidats en base précisément pour qu'on n'ait pas à
-- les refaire : les relire est le comportement correct, et c'est ce qui rend
-- ses dossiers reproductibles sans dépendre du seuil trigramme courant.
CREATE TEMP TABLE r_candidat_e3 ON COMMIT DROP AS
SELECT f.series_id,
       split_part(c->>'cible', ':', 1) AS candidat_type_brut,
       split_part(c->>'cible', ':', 2) AS candidat_id
FROM r_file f,
     LATERAL jsonb_array_elements(f.details->'top') AS c
WHERE f.origine = 'etage3';

-- ---------------------------------------------------------------------------
-- 5. UNION + SIGNAUX — la matière du jugement.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE r_candidat ON COMMIT DROP AS
WITH tous AS (
    SELECT series_id, candidat_type, candidat_id FROM r_candidat_e1
    UNION ALL
    SELECT series_id, candidat_type, candidat_id FROM r_candidat_e2
    UNION ALL
    SELECT series_id,
           CASE WHEN candidat_type_brut = 'wd' THEN 'qid' ELSE 'kitsu_id' END,
           candidat_id
    FROM r_candidat_e3
)
SELECT DISTINCT ON (t.series_id, t.candidat_type, t.candidat_id)
    t.series_id,
    t.candidat_type,
    t.candidat_id,
    -- Identité de la cible, lue chez elle.
    CASE WHEN t.candidat_type = 'qid'
         THEN (SELECT p.label_principal FROM manga.wd_pivot p
               WHERE p.qid = t.candidat_id)
         ELSE (SELECT kf.forme FROM manga.kitsu_formes kf
               WHERE kf.kitsu_id = t.candidat_id::bigint
                 AND kf.forme_type = 'canonical' LIMIT 1)
    END AS cible_label,
    CASE WHEN t.candidat_type = 'qid'
         THEN (SELECT p.annee FROM manga.wd_pivot p WHERE p.qid = t.candidat_id)
         ELSE (SELECT km.annee FROM manga.kitsu_meta km
               WHERE km.kitsu_id = t.candidat_id::bigint)
    END AS cible_annee,
    CASE WHEN t.candidat_type = 'qid'
         THEN (SELECT p.wiki_en FROM manga.wd_pivot p WHERE p.qid = t.candidat_id)
         ELSE (SELECT km.subtype FROM manga.kitsu_meta km
               WHERE km.kitsu_id = t.candidat_id::bigint)
    END AS cible_contexte,
    -- Formes de la cible : ce que le juge compare aux alias MS.
    CASE WHEN t.candidat_type = 'qid'
         THEN (SELECT string_agg(DISTINCT wf.forme, ' | ' ORDER BY wf.forme)
               FROM manga.wd_formes wf WHERE wf.qid = t.candidat_id)
         ELSE (SELECT string_agg(DISTINCT kf.forme, ' | ' ORDER BY kf.forme)
               FROM manga.kitsu_formes kf
               WHERE kf.kitsu_id = t.candidat_id::bigint)
    END AS cible_formes,
    -- Auteurs de la cible.
    CASE WHEN t.candidat_type = 'qid'
         THEN (SELECT string_agg(DISTINCT waf.forme, ' | ' ORDER BY waf.forme)
               FROM manga.wd_auteurs wa
               JOIN manga.wd_auteurs_formes waf ON waf.auteur_qid = wa.auteur_qid
               WHERE wa.qid = t.candidat_id)
         ELSE (SELECT string_agg(DISTINCT ks.personne, ' | ' ORDER BY ks.personne)
               FROM manga.kitsu_staff ks
               WHERE ks.kitsu_id = t.candidat_id::bigint)
    END AS cible_auteurs,
    -- SIGNAL AUTEUR, calculé sur les colonnes certifiées.
    CASE
        WHEN NOT EXISTS (SELECT 1 FROM ms_auteur_norm m
                         WHERE m.series_id = t.series_id)
        THEN 'incomparable'
        WHEN t.candidat_type = 'qid' THEN
            CASE
                WHEN NOT EXISTS (SELECT 1 FROM manga.wd_auteurs wa
                                 JOIN manga.wd_auteurs_formes waf
                                   ON waf.auteur_qid = wa.auteur_qid
                                 WHERE wa.qid = t.candidat_id)
                THEN 'incomparable'
                WHEN EXISTS (SELECT 1 FROM manga.wd_auteurs wa
                             JOIN manga.wd_auteurs_formes waf
                               ON waf.auteur_qid = wa.auteur_qid
                             JOIN ms_auteur_norm m
                               ON m.auteur_norm = waf.forme_norm
                             WHERE wa.qid = t.candidat_id
                               AND m.series_id = t.series_id)
                THEN 'concordant' ELSE 'discordant'
            END
        ELSE
            CASE
                WHEN NOT EXISTS (SELECT 1 FROM manga.kitsu_staff ks
                                 WHERE ks.kitsu_id = t.candidat_id::bigint)
                THEN 'incomparable'
                WHEN EXISTS (SELECT 1 FROM manga.kitsu_staff ks
                             JOIN ms_auteur_norm m
                               ON m.auteur_norm = ks.personne_norm
                             WHERE ks.kitsu_id = t.candidat_id::bigint
                               AND m.series_id = t.series_id)
                THEN 'concordant' ELSE 'discordant'
            END
    END AS signal_auteur,
    -- ÉCART D'ANNÉE brut. Le juge reçoit le chiffre, pas un verdict : la
    -- politique des bandes (§26) vaut pour les étages qui DÉCIDENT.
    (SELECT s.series_year FROM manga.ms_series_enriched s
     WHERE s.series_id = t.series_id)
    - CASE WHEN t.candidat_type = 'qid'
           THEN (SELECT p.annee FROM manga.wd_pivot p WHERE p.qid = t.candidat_id)
           ELSE (SELECT km.annee FROM manga.kitsu_meta km
                 WHERE km.kitsu_id = t.candidat_id::bigint)
      END AS ecart_annee,
    -- ENRICHISSEMENT CROISÉ (annexe R) : le chemin Kitsu confirme-t-il ce
    -- candidat Wikidata ? Un candidat confirmé par un second référentiel
    -- arrive au juge PRÉ-ÉCLAIRÉ.
    CASE WHEN t.candidat_type = 'qid' AND EXISTS (
             SELECT 1
             FROM manga.ms_formes mf
             JOIN manga.kitsu_formes kf ON kf.forme_norm = mf.forme_norm
             JOIN manga.kitsu_mappings km
               ON km.kitsu_id = kf.kitsu_id
              AND km.external_site = 'myanimelist/manga'
             JOIN manga.wd_pivot wp ON wp.mal_id = km.external_id
             WHERE mf.series_id = t.series_id AND wp.qid = t.candidat_id)
         THEN true ELSE false
    END AS confirme_par_kitsu
FROM tous t
ORDER BY t.series_id, t.candidat_type, t.candidat_id;

CREATE INDEX ON r_candidat (series_id);
