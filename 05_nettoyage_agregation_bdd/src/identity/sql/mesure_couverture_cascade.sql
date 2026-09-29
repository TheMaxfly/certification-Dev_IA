-- Mesure unique du rapport de couverture de la cascade.
--
-- Cette requête est volontairement un unique SELECT à base de CTE. Elle ne
-- crée aucun objet et ne modifie aucune donnée. Le script appelant impose en
-- plus une transaction PostgreSQL en lecture seule.
WITH
catalogue AS (
    SELECT count(*)::integer AS total
    FROM manga.ms_series_enriched
),
-- La décision d'IDENTIFICATION courante. Une décision `kitsu_propagation`
-- (018, 2026-09-30) n'identifie pas : elle complète le kitsu_id d'une identité
-- déjà décidée et désigne sa décision source dans details. On la traverse,
-- pour que chaque série reste comptée sous la méthode qui l'a identifiée.
identification_courante AS (
    SELECT
        v.series_id,
        coalesce(src.method, v.method) AS method,
        v.status,
        coalesce(src.decision_id, v.decision_id) AS decision_id
    FROM manga.v_match_current v
    LEFT JOIN manga.match_decision p
        ON v.method = 'kitsu_propagation' AND p.decision_id = v.decision_id
    LEFT JOIN manga.match_decision src
        ON src.decision_id = (p.details->>'decision_source')::bigint
),
decisions_courantes AS (
    SELECT
        count(*) FILTER (WHERE status = 'auto')::integer AS auto,
        count(*) FILTER (WHERE status = 'needs_review')::integer AS needs_review,
        count(*) FILTER (WHERE status = 'rejected')::integer AS rejected,
        count(*) FILTER (
            WHERE status = 'auto' AND method = 'exact'
        )::integer AS exact,
        count(*) FILTER (
            WHERE status = 'auto' AND method = 'exact_author'
        )::integer AS exact_author,
        count(*) FILTER (
            WHERE status = 'auto' AND method = 'exact_kitsu'
        )::integer AS exact_kitsu,
        count(*) FILTER (
            WHERE status = 'auto' AND method = 'exact_kitsu_author'
        )::integer AS exact_kitsu_author,
        count(*) FILTER (
            WHERE status = 'auto' AND method = 'kitsu_bridge'
        )::integer AS kitsu_bridge_current,
        count(*) FILTER (
            WHERE status = 'auto' AND method = 'llm_review'
        )::integer AS llm_review,
        count(*) FILTER (
            WHERE status = 'auto' AND method = 'trgm'
        )::integer AS trgm_auto,
        count(*) FILTER (
            WHERE status = 'rejected' AND method = 'human_review'
        )::integer AS human_review_rejected,
        array_agg(series_id ORDER BY series_id) FILTER (
            WHERE status = 'rejected' AND method = 'human_review'
        ) AS human_review_rejected_series
    FROM identification_courante
),
propagation AS (
    SELECT count(*)::integer AS total
    FROM manga.v_match_current
    WHERE method = 'kitsu_propagation' AND status = 'auto'
),
orphelines AS (
    SELECT count(*)::integer AS total
    FROM manga.ms_series_enriched s
    LEFT JOIN manga.v_match_current v ON v.series_id = s.series_id
    WHERE v.series_id IS NULL
),
pont_historique AS (
    SELECT count(DISTINCT series_id)::integer AS total
    FROM manga.match_decision
    WHERE method = 'kitsu_bridge' AND status = 'auto'
),
kitsu_formes AS (
    SELECT count(*)::integer AS total
    FROM manga.kitsu_formes
),
strates_llm AS (
    SELECT
        count(*) FILTER (
            WHERE d.details->>'case' = 'promo_seau_adjacent'
        )::integer AS seau_adjacent,
        count(*) FILTER (
            WHERE d.details->>'case' = 'promo_auteur_pseudonyme_ou_romanisation'
        )::integer AS auteur_pseudonyme,
        count(*) FILTER (
            WHERE d.details->>'case' = 'promo_llm_same_haute'
        )::integer AS autres
    FROM identification_courante v
    JOIN manga.match_decision d ON d.decision_id = v.decision_id
    WHERE v.method = 'llm_review' AND v.status = 'auto'
),
pivot AS (
    SELECT
        count(*)::integer AS total,
        count(*) FILTER (
            WHERE NULLIF(btrim(wiki_ja), '') IS NOT NULL
        )::integer AS wiki_ja,
        count(*) FILTER (
            WHERE NULLIF(btrim(wiki_en), '') IS NOT NULL
        )::integer AS wiki_en,
        count(*) FILTER (
            WHERE NULLIF(btrim(wiki_fr), '') IS NOT NULL
        )::integer AS wiki_fr,
        count(*) FILTER (
            WHERE NULLIF(btrim(wiki_ja), '') IS NOT NULL
              AND NULLIF(btrim(wiki_en), '') IS NULL
              AND NULLIF(btrim(wiki_fr), '') IS NULL
        )::integer AS wiki_ja_seul
    FROM manga.wd_pivot
),
identifiants AS (
    SELECT
        count(*) FILTER (WHERE wikidata_qid IS NOT NULL)::integer AS qid,
        count(*) FILTER (WHERE kitsu_id IS NOT NULL)::integer AS kitsu,
        count(*) FILTER (WHERE mal_id IS NOT NULL)::integer AS mal,
        count(*) FILTER (WHERE anilist_id IS NOT NULL)::integer AS anilist
    FROM manga.work_identity
),
genres AS (
    SELECT
        count(*) FILTER (
            WHERE COALESCE(series_genres_enriched, '[]'::jsonb) <> '[]'::jsonb
        )::integer AS couvertes,
        count(*) FILTER (
            WHERE COALESCE(series_genres, '[]'::jsonb) = '[]'::jsonb
              AND COALESCE(kitsu_genres_json, '[]'::jsonb) = '[]'::jsonb
        )::integer AS sans_genre_source
    FROM manga.ms_series_enriched
),
avis_haute AS (
    SELECT
        a.avis_id,
        a.series_id,
        a.candidat_type,
        a.candidat_id,
        a.pre_validation_bandes
    FROM manga.llm_avis a
    JOIN manga.v_match_current v ON v.series_id = a.series_id
    WHERE a.phase = 'file'
      AND a.verdict = 'same_work'
      AND a.confiance = 'haute'
      AND a.dossier_partiel = false
      AND v.status = 'needs_review'
),
series_multi AS (
    SELECT series_id
    FROM avis_haute
    GROUP BY series_id
    HAVING count(*) > 1
),
candidats_multi AS (
    SELECT
        a.series_id,
        a.candidat_type,
        a.candidat_id,
        CASE
            WHEN a.candidat_type = 'qid' THEN (
                SELECT p.mal_id FROM manga.wd_pivot p
                WHERE p.qid = a.candidat_id
            )
            ELSE (
                SELECT km.external_id FROM manga.kitsu_mappings km
                WHERE km.kitsu_id = a.candidat_id::bigint
                  AND km.external_site = 'myanimelist/manga'
                LIMIT 1
            )
        END AS mal_id,
        CASE
            WHEN a.candidat_type = 'qid' THEN (
                SELECT p.anilist_id FROM manga.wd_pivot p
                WHERE p.qid = a.candidat_id
            )
            ELSE (
                SELECT km.external_id FROM manga.kitsu_mappings km
                WHERE km.kitsu_id = a.candidat_id::bigint
                  AND km.external_site = 'anilist/manga'
                LIMIT 1
            )
        END AS anilist_id
    FROM avis_haute a
    JOIN series_multi m ON m.series_id = a.series_id
),
classement_multi AS (
    SELECT
        series_id,
        CASE
            WHEN count(DISTINCT mal_id) FILTER (WHERE mal_id IS NOT NULL) <= 1
             AND count(DISTINCT anilist_id)
                    FILTER (WHERE anilist_id IS NOT NULL) <= 1
             AND count(*) FILTER (
                    WHERE mal_id IS NULL AND anilist_id IS NULL
                 ) = 0
            THEN 'fusible'
            ELSE 'conflit'
        END AS nature
    FROM candidats_multi
    GROUP BY series_id
),
avis_unique AS (
    SELECT a.*
    FROM avis_haute a
    WHERE a.series_id IN (
        SELECT series_id
        FROM avis_haute
        GROUP BY series_id
        HAVING count(*) = 1
    )
),
identite_base AS (
    SELECT
        u.*,
        CASE
            WHEN u.candidat_type = 'kitsu_id' THEN (
                SELECT km.external_id FROM manga.kitsu_mappings km
                WHERE km.kitsu_id = u.candidat_id::bigint
                  AND km.external_site = 'myanimelist/manga'
                LIMIT 1
            )
            ELSE (
                SELECT p.mal_id FROM manga.wd_pivot p
                WHERE p.qid = u.candidat_id
            )
        END AS d_mal,
        CASE
            WHEN u.candidat_type = 'kitsu_id' THEN (
                SELECT km.external_id FROM manga.kitsu_mappings km
                WHERE km.kitsu_id = u.candidat_id::bigint
                  AND km.external_site = 'anilist/manga'
                LIMIT 1
            )
            ELSE (
                SELECT p.anilist_id FROM manga.wd_pivot p
                WHERE p.qid = u.candidat_id
            )
        END AS d_anilist,
        CASE
            WHEN u.candidat_type = 'kitsu_id' THEN u.candidat_id
        END AS d_kitsu
    FROM avis_unique u
),
identite_derivee AS (
    SELECT
        b.*,
        CASE
            WHEN b.candidat_type = 'qid' THEN b.candidat_id
            ELSE (
                SELECT CASE WHEN count(*) = 1 THEN min(p.qid) END
                FROM manga.wd_pivot p
                WHERE p.mal_id = b.d_mal
            )
        END AS d_qid
    FROM identite_base b
),
valeurs_derivees AS (
    SELECT series_id, 'qid'::text AS colonne, d_qid::text AS valeur
    FROM identite_derivee WHERE d_qid IS NOT NULL
    UNION ALL
    SELECT series_id, 'kitsu', d_kitsu::text
    FROM identite_derivee WHERE d_kitsu IS NOT NULL
    UNION ALL
    SELECT series_id, 'mal', d_mal::text
    FROM identite_derivee WHERE d_mal IS NOT NULL
    UNION ALL
    SELECT series_id, 'anilist', d_anilist::text
    FROM identite_derivee WHERE d_anilist IS NOT NULL
),
collisions_externes AS (
    SELECT p.series_id
    FROM identite_derivee p
    JOIN manga.work_identity w ON w.wikidata_qid = p.d_qid
    WHERE p.d_qid IS NOT NULL AND w.series_id <> p.series_id
    UNION
    SELECT p.series_id
    FROM identite_derivee p
    JOIN manga.work_identity w ON w.kitsu_id::text = p.d_kitsu::text
    WHERE p.d_kitsu IS NOT NULL AND w.series_id <> p.series_id
    UNION
    SELECT p.series_id
    FROM identite_derivee p
    JOIN manga.work_identity w ON w.mal_id::text = p.d_mal::text
    WHERE p.d_mal IS NOT NULL AND w.series_id <> p.series_id
    UNION
    SELECT p.series_id
    FROM identite_derivee p
    JOIN manga.work_identity w ON w.anilist_id::text = p.d_anilist::text
    WHERE p.d_anilist IS NOT NULL AND w.series_id <> p.series_id
),
collisions_internes AS (
    SELECT v.series_id
    FROM valeurs_derivees v
    JOIN (
        SELECT colonne, valeur
        FROM valeurs_derivees
        GROUP BY colonne, valeur
        HAVING count(DISTINCT series_id) > 1
    ) d ON d.colonne = v.colonne AND d.valeur = v.valeur
),
series_collision AS (
    SELECT series_id FROM collisions_externes
    UNION
    SELECT series_id FROM collisions_internes
),
residuel AS (
    SELECT
        count(*) FILTER (WHERE verdict = 'undecidable')::integer AS undecidable,
        count(*) FILTER (
            WHERE phase = 'file'
              AND verdict = 'same_work'
              AND confiance = 'moyenne'
        )::integer AS same_work_moyenne
    FROM manga.llm_avis
),
auteurs_ms AS (
    SELECT btrim(series_scenariste) AS auteur
    FROM manga.ms_series_enriched
    WHERE NULLIF(btrim(series_scenariste), '') IS NOT NULL
    UNION ALL
    SELECT btrim(series_dessinateur)
    FROM manga.ms_series_enriched
    WHERE NULLIF(btrim(series_dessinateur), '') IS NOT NULL
),
graphies_ms AS (
    SELECT
        count(*)::integer AS total,
        count(*) FILTER (
            WHERE auteur ~ '[一-龯々〆〤ぁ-ゖァ-ヺーｦ-ﾟ]'
        )::integer AS japonaises,
        count(*) FILTER (
            WHERE auteur !~ '[一-龯々〆〤ぁ-ゖァ-ヺーｦ-ﾟ]'
        )::integer AS romanisees
    FROM auteurs_ms
)
SELECT
    c.total AS catalogue_total,
    d.auto AS identites_auto,
    d.needs_review,
    o.total AS orphelines,
    d.rejected,
    d.exact,
    d.exact_author,
    d.exact_kitsu,
    d.exact_kitsu_author,
    d.kitsu_bridge_current,
    ph.total AS kitsu_bridge_initial,
    d.llm_review,
    d.trgm_auto,
    pr.total AS kitsu_propagation,
    d.human_review_rejected,
    d.human_review_rejected_series,
    kf.total AS kitsu_formes_total,
    sl.seau_adjacent AS llm_seau_adjacent,
    sl.auteur_pseudonyme AS llm_auteur_pseudonyme,
    sl.autres AS llm_autres,
    p.total AS pivot_total,
    p.wiki_ja,
    p.wiki_en,
    p.wiki_fr,
    p.wiki_ja_seul,
    i.qid AS identites_qid,
    i.kitsu AS identites_kitsu,
    i.mal AS identites_mal,
    i.anilist AS identites_anilist,
    g.couvertes AS genres_couvertes,
    g.sans_genre_source,
    r.undecidable,
    (SELECT count(*)::integer FROM classement_multi
     WHERE nature = 'conflit') AS conflits_multi,
    (SELECT count(*)::integer FROM classement_multi
     WHERE nature = 'fusible') AS fusibles,
    (SELECT count(*)::integer FROM series_collision) AS collisions_unicite,
    r.same_work_moyenne,
    gm.total AS graphies_ms_total,
    gm.romanisees AS graphies_ms_romanisees,
    gm.japonaises AS graphies_ms_japonaises
FROM catalogue c
CROSS JOIN decisions_courantes d
CROSS JOIN orphelines o
CROSS JOIN pont_historique ph
CROSS JOIN propagation pr
CROSS JOIN kitsu_formes kf
CROSS JOIN strates_llm sl
CROSS JOIN pivot p
CROSS JOIN identifiants i
CROSS JOIN genres g
CROSS JOIN residuel r
CROSS JOIN graphies_ms gm;
