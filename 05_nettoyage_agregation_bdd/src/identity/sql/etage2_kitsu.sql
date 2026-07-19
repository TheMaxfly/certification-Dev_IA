-- Étage 2 de la cascade : jointure EXACTE multi-formes MS × référentiel Kitsu.
--
-- Deux tables temporaires sont fournies par le pilote Python AVANT ce script :
--   ms_auteur_norm (series_id, auteur_norm) — scénaristes/dessinateurs MS
--     normalisés par identity.normaliser(). JAMAIS de normalisation SQL ad hoc.
--   etage2_param (borne_basse, borne_haute) — la fenêtre d'année CALIBRÉE en
--     phase 1 sur les 1 689 identités du pont, EMPIRIQUE ET SANS PLANCHER
--     (dette 22.4 : la prémisse « VF-après-Japon » est réfutée).
--
-- CE QUI CHANGE PAR RAPPORT À L'ÉTAGE 1. Kitsu porte l'année à 99,9 % (contre
-- 41,0 % pour Wikidata) : le confirmateur année devient réellement disponible.
-- Et Manga Sanctuary porte un kitsu_id HISTORIQUE sur 5 608 séries : quand une
-- forme le confirme, deux sources indépendantes disent la même chose — c'est
-- le signal le plus fort de cet étage, d'où son score de tête.
--
-- Sans paramètre : exécutable en un seul execute() psycopg.

-- ---------------------------------------------------------------------------
-- 1) CANDIDATS — une ligne par (série, kitsu_id) avec ses signaux.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE etage2_candidat ON COMMIT DROP AS
WITH perimetre AS (
    -- Idempotence : une série déjà décidée n'est JAMAIS rejouée. Les 665
    -- needs_review de l'étage 1 en font partie — ils ne reçoivent aucune
    -- décision ici (journal append-only), seulement l'enrichissement de
    -- dossier de l'ANNEXE R, qui n'écrit pas au journal.
    SELECT s.series_id, s.series_year
    FROM manga.ms_series_enriched s
    WHERE NOT EXISTS (
        SELECT 1 FROM manga.v_match_current v WHERE v.series_id = s.series_id)
),
paires AS (
    -- Jointure exacte sur la forme normalisée, toutes formes confondues des
    -- deux côtés (titre ET alias MS × canonical/title/abbreviated Kitsu).
    SELECT DISTINCT p.series_id, kf.kitsu_id
    FROM perimetre p
    JOIN manga.ms_formes mf
      ON mf.series_id = p.series_id
     AND mf.forme_norm IS NOT NULL AND mf.forme_norm <> ''
    JOIN manga.kitsu_formes kf
      ON kf.forme_norm = mf.forme_norm
     AND kf.forme_norm IS NOT NULL AND kf.forme_norm <> ''
)
SELECT
    c.series_id,
    c.kitsu_id,
    p.series_year,
    km.annee AS annee_kitsu,
    -- Le kitsu_id historique de Manga Sanctuary confirme-t-il CE candidat ?
    -- Les séries listées comme ambiguës à l'époque sont exclues : leur
    -- kitsu_id n'a jamais été une décision, seulement une piste.
    EXISTS (
        SELECT 1 FROM manga.ms_kitsu_map mk
        WHERE mk.series_id = c.series_id
          AND mk.kitsu_id = c.kitsu_id
          AND NOT EXISTS (SELECT 1 FROM manga.ms_kitsu_ambiguous a
                          WHERE a.series_id = c.series_id)
    ) AS confirme_historique,
    -- Signal AUTEUR : concordant / discordant / incomparable.
    CASE
        WHEN NOT EXISTS (SELECT 1 FROM ms_auteur_norm m
                         WHERE m.series_id = c.series_id)
          OR NOT EXISTS (SELECT 1 FROM manga.kitsu_staff ks
                         WHERE ks.kitsu_id = c.kitsu_id)
        THEN 'incomparable'
        WHEN EXISTS (SELECT 1 FROM manga.kitsu_staff ks
                     JOIN ms_auteur_norm m ON m.auteur_norm = ks.personne_norm
                     WHERE ks.kitsu_id = c.kitsu_id AND m.series_id = c.series_id)
        THEN 'concordant'
        ELSE 'discordant'
    END AS signal_auteur,
    -- Signal ANNÉE : un CONFIRMATEUR, jamais un discriminant seul.
    CASE
        WHEN p.series_year IS NULL OR km.annee IS NULL THEN 'incomparable'
        WHEN (p.series_year - km.annee)
             BETWEEN (SELECT borne_basse FROM etage2_param)
                 AND (SELECT borne_haute FROM etage2_param)
        THEN 'concordant'
        ELSE 'discordant'
    END AS signal_annee
FROM paires c
JOIN perimetre p ON p.series_id = c.series_id
LEFT JOIN manga.kitsu_meta km ON km.kitsu_id = c.kitsu_id;

CREATE INDEX ON etage2_candidat (series_id);
CREATE INDEX ON etage2_candidat (kitsu_id);

-- ---------------------------------------------------------------------------
-- 2) DÉCISION PAR SÉRIE — application de la matrice figée.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE etage2_serie ON COMMIT DROP AS
WITH agg AS (
    SELECT series_id,
           count(*) AS n_cand,
           count(*) FILTER (WHERE confirme_historique) AS n_historique,
           -- Un candidat « départageable par l'auteur » ne l'est que si l'année
           -- ne le contredit pas : une année discordante interdit l'auto.
           count(*) FILTER (WHERE signal_auteur = 'concordant'
                              AND signal_annee <> 'discordant') AS n_auteur_ok,
           count(*) FILTER (WHERE signal_annee = 'concordant')  AS n_annee_ok
    FROM etage2_candidat
    GROUP BY series_id
),
-- La série porte-t-elle un kitsu_id historique NON ambigu, quel qu'il soit ?
-- Sert à distinguer « historique contredit » de « pas d'historique du tout ».
historique AS (
    SELECT mk.series_id, mk.kitsu_id
    FROM manga.ms_kitsu_map mk
    WHERE mk.kitsu_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM manga.ms_kitsu_ambiguous a
                      WHERE a.series_id = mk.series_id)
),
gagnant AS (
    -- Priorité au candidat confirmé par l'historique : deux sources
    -- indépendantes. Sinon le seul candidat, ou celui que l'auteur départage.
    SELECT DISTINCT ON (c.series_id) c.*
    FROM etage2_candidat c
    JOIN agg a USING (series_id)
    WHERE c.confirme_historique
       OR a.n_cand = 1
       OR (a.n_cand > 1 AND a.n_auteur_ok = 1
           AND c.signal_auteur = 'concordant' AND c.signal_annee <> 'discordant')
    ORDER BY c.series_id, c.confirme_historique DESC,
             (c.signal_auteur = 'concordant') DESC, c.kitsu_id
)
SELECT
    a.series_id,
    g.kitsu_id,
    a.n_cand,
    g.confirme_historique,
    g.signal_auteur,
    g.signal_annee,
    CASE
        -- 1. L'historique confirmé prime : le signal le plus fort de l'étage.
        WHEN a.n_historique >= 1 AND g.confirme_historique
            THEN 'auto_k_historique_confirme'
        -- 2. L'historique existe mais AUCUNE forme ne le confirme : les formes
        --    désignent un autre kitsu_id. La contradiction est un signal, pas
        --    un détail — jamais d'auto, l'humain tranche.
        WHEN a.n_historique = 0
             AND EXISTS (SELECT 1 FROM historique h WHERE h.series_id = a.series_id)
            THEN 'review_k_historique_contredit'
        -- 3. Règle transverse : une année discordante n'est JAMAIS auto.
        WHEN g.signal_annee = 'discordant' THEN 'review_k_annee_discordante'
        WHEN a.n_cand = 1 AND g.signal_auteur = 'concordant'
            THEN 'auto_k_unique_auteur'
        WHEN a.n_cand = 1 AND g.signal_auteur = 'incomparable'
             AND g.signal_annee = 'concordant'
            THEN 'auto_k_unique_annee'
        WHEN a.n_cand = 1 AND g.signal_auteur = 'incomparable'
             AND g.signal_annee = 'incomparable'
            THEN 'review_k_sans_signal'
        WHEN a.n_cand = 1 AND g.signal_auteur = 'discordant'
            THEN 'review_k_auteur_discordant'
        WHEN a.n_cand > 1 AND g.signal_auteur = 'concordant'
            THEN 'auto_k_multi_auteur'
        WHEN a.n_annee_ok = 1 THEN 'review_k_multi_annee_seule'
        ELSE 'review_k_ambiguite'
    END AS cas
FROM agg a
LEFT JOIN gagnant g USING (series_id);

-- ---------------------------------------------------------------------------
-- 3) IDENTIFIANTS DÉRIVÉS — ce que le kitsu_id retenu permet d'atteindre.
-- ---------------------------------------------------------------------------
-- Règle du pont, reconduite : les chemins vers le QID doivent CONCORDER.
-- Divergents -> needs_review. AUCUN mapping -> identité PARTIELLE légitime
-- (kitsu_id seul, qid NULL) : c'est l'étage 0bis réalisé, et les colonnes de
-- work_identity sont indépendantes les unes des autres.
CREATE TEMP TABLE etage2_ident ON COMMIT DROP AS
WITH m AS (
    SELECT s.series_id, s.kitsu_id,
           max(km.external_id) FILTER (
               WHERE km.external_site = 'myanimelist/manga') AS mal_id,
           max(km.external_id) FILTER (
               WHERE km.external_site = 'anilist/manga')     AS anilist_id
    FROM etage2_serie s
    LEFT JOIN manga.kitsu_mappings km ON km.kitsu_id = s.kitsu_id
    WHERE s.cas LIKE 'auto%'
    GROUP BY s.series_id, s.kitsu_id
)
SELECT m.series_id, m.kitsu_id, m.mal_id, m.anilist_id,
       pm.qid AS qid_par_mal,
       pa.qid AS qid_par_anilist,
       coalesce(pm.qid, pa.qid) AS qid,
       (pm.qid IS NOT NULL AND pa.qid IS NOT NULL AND pm.qid <> pa.qid)
           AS qid_divergent
FROM m
LEFT JOIN manga.wd_pivot pm ON pm.mal_id = m.mal_id
LEFT JOIN manga.wd_pivot pa ON pa.anilist_id = m.anilist_id;

-- Chemins divergents : l'étage ne tranche pas entre deux QID contradictoires.
UPDATE etage2_serie s
SET cas = 'review_k_qid_divergent'
FROM etage2_ident i
WHERE i.series_id = s.series_id AND i.qid_divergent;

-- ---------------------------------------------------------------------------
-- 4) COLLISIONS — détectées AVANT insert, jamais résolues par ordre d'arrivée.
-- ---------------------------------------------------------------------------
-- 4a. Plusieurs séries MS visant le même kitsu_id (entre elles, ou contre une
--     identité déjà en base). TOUT le groupe part en review.
UPDATE etage2_serie s
SET cas = 'review_k_collision_kitsu'
WHERE s.cas LIKE 'auto%' AND (
       s.kitsu_id IN (SELECT kitsu_id FROM etage2_serie
                      WHERE cas LIKE 'auto%' AND kitsu_id IS NOT NULL
                      GROUP BY kitsu_id HAVING count(*) > 1)
    OR s.kitsu_id::text IN (SELECT kitsu_id FROM manga.work_identity
                            WHERE kitsu_id IS NOT NULL));

-- 4b. Les index UNIQUE partiels de 001 portent aussi sur qid, mal_id et
--     anilist_id : si l'un est DÉJÀ pris, l'insert échouerait. Le groupe part
--     en review plutôt que de laisser le premier arrivé gagner.
UPDATE etage2_serie s
SET cas = 'review_k_collision_id'
FROM etage2_ident i
WHERE i.series_id = s.series_id AND s.cas LIKE 'auto%' AND (
       (i.qid IS NOT NULL AND (
            i.qid IN (SELECT wikidata_qid FROM manga.work_identity
                      WHERE wikidata_qid IS NOT NULL)
         OR i.qid IN (SELECT qid FROM etage2_ident e
                      JOIN etage2_serie es ON es.series_id = e.series_id
                      WHERE es.cas LIKE 'auto%' AND e.qid IS NOT NULL
                      GROUP BY qid HAVING count(*) > 1)))
    OR (i.mal_id IS NOT NULL AND (
            i.mal_id IN (SELECT mal_id FROM manga.work_identity
                         WHERE mal_id IS NOT NULL)
         OR i.mal_id IN (SELECT mal_id FROM etage2_ident e
                         JOIN etage2_serie es ON es.series_id = e.series_id
                         WHERE es.cas LIKE 'auto%' AND e.mal_id IS NOT NULL
                         GROUP BY mal_id HAVING count(*) > 1)))
    OR (i.anilist_id IS NOT NULL AND (
            i.anilist_id IN (SELECT anilist_id FROM manga.work_identity
                             WHERE anilist_id IS NOT NULL)
         OR i.anilist_id IN (SELECT anilist_id FROM etage2_ident e
                             JOIN etage2_serie es ON es.series_id = e.series_id
                             WHERE es.cas LIKE 'auto%' AND e.anilist_id IS NOT NULL
                             GROUP BY anilist_id HAVING count(*) > 1))));

-- ---------------------------------------------------------------------------
-- 5) JOURNAL — une décision par série TRAITÉE (auto ET needs_review).
-- ---------------------------------------------------------------------------
-- method : les valeurs ajoutées au CHECK par la migration 009.
--   exact_kitsu_author — l'auteur a tranché ;
--   exact_kitsu        — l'historique ou l'année a confirmé, sans l'auteur.
-- details : la case de matrice, sur CHAQUE décision. C'est ce qui rend un
-- dossier ré-instruisable depuis la seule base, sans dépendre des CSV.
INSERT INTO manga.match_decision
    (series_id, wikidata_qid, method, score, status, details)
SELECT s.series_id, i.qid,
       CASE WHEN s.cas IN ('auto_k_unique_auteur', 'auto_k_multi_auteur')
            THEN 'exact_kitsu_author' ELSE 'exact_kitsu' END,
       CASE s.cas WHEN 'auto_k_historique_confirme' THEN 0.96
                  WHEN 'auto_k_unique_auteur'       THEN 0.95
                  WHEN 'auto_k_multi_auteur'        THEN 0.93
                  WHEN 'auto_k_unique_annee'        THEN 0.90 END,
       'auto',
       jsonb_build_object('case', s.cas, 'kitsu_id', s.kitsu_id)
FROM etage2_serie s
LEFT JOIN etage2_ident i ON i.series_id = s.series_id
WHERE s.cas LIKE 'auto%';

INSERT INTO manga.match_decision
    (series_id, wikidata_qid, method, score, status, details)
SELECT series_id, NULL, 'exact_kitsu', NULL, 'needs_review',
       jsonb_build_object('case', cas, 'n_cand', n_cand)
FROM etage2_serie WHERE cas LIKE 'review%';

-- ---------------------------------------------------------------------------
-- 6) IDENTITÉ — remplissage des seules décisions auto.
-- ---------------------------------------------------------------------------
-- kitsu_id est TOUJOURS renseigné à l'auto ; qid/mal_id/anilist_id le sont
-- quand les mappings existent. Une identité partielle (kitsu_id seul) est un
-- résultat LÉGITIME, pas un échec : 2 776 séries ont un mal_id sans QID, le
-- pivot Wikidata ne couvrant que la tête du catalogue.
UPDATE manga.work_identity w
SET kitsu_id     = i.kitsu_id::text,
    wikidata_qid = coalesce(w.wikidata_qid, i.qid),
    mal_id       = coalesce(w.mal_id, i.mal_id),
    anilist_id   = coalesce(w.anilist_id, i.anilist_id),
    updated_at   = now()
FROM etage2_serie s
JOIN etage2_ident i ON i.series_id = s.series_id
WHERE s.cas LIKE 'auto%' AND w.series_id = s.series_id;
