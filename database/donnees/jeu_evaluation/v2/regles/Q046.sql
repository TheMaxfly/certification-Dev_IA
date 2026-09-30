-- Q046 — F9 — règle du jeu v1 : genre dans (comédie, tranche de vie) ET note > 7.5 ET critiques > 10
--
-- Définitions (jeu v1, décisions du 2026-09-29) :
--   public    = manga.ms_series_enriched.series_category_clean
--   note      = series_members_rating, la note des MEMBRES (/10) — l'avis des
--               lecteurs. Existantes et non retenues : series_experts_rating,
--               series_score_mean (moyenne des critiques), kitsu_rating_average_10
--   critiques = critiques du SNAPSHOT 2026-07, comptées dans les tables
--               (manga.ms_reviews_all ∩ staging.ms_reviews), jamais la colonne
--               figée series_review_count (décembre 2025)
--   genre     = codes du référentiel (manga.genre_mapping, source ms, statut
--               mappe), jamais les libellés bruts
--   année FR  = année du premier volume paru en France,
--               MIN(volume_publication_date) sur les volumes du snapshot ;
--               series_year est l'année VO
--   plafond   = 20 séries, par note des membres décroissante, départagées
--               par series_id
--   seuil critiques : > 7 = décile supérieur (p90 = 7 sur les 3 456 séries
--               critiquées du snapshot 2026-07 ; 303 séries au-delà)
--               texte d'origine : > 10 (149 séries) — même définition partout
WITH critiques AS (
  SELECT a.series_id, count(*) AS n
  FROM manga.ms_reviews_all a
  JOIN staging.ms_reviews s ON s.review_url = a.review_url
  GROUP BY a.series_id
),
genres AS (
  SELECT DISTINCT se.series_id, m.code
  FROM manga.ms_series_enriched se
  CROSS JOIN LATERAL jsonb_array_elements_text(se.series_genres) AS g(libelle)
  JOIN manga.genre_mapping m
    ON m.source = 'ms' AND m.libelle_brut = g.libelle AND m.statut = 'mappe'
)
SELECT se.series_id
FROM manga.ms_series_enriched se
JOIN critiques c USING (series_id)
WHERE EXISTS (SELECT 1 FROM genres g WHERE g.series_id = se.series_id
              AND g.code IN ('comedie', 'tranche_de_vie'))
  AND se.series_members_rating > 7.5
  AND c.n > 7
ORDER BY se.series_members_rating DESC NULLS LAST, se.series_id
LIMIT 20;
