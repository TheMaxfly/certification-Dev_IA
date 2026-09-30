-- Q045 — F9 — règle du jeu v1 : public = shonen ET note > 8 ET critiques > 20, tri par note, 20 premiers — seuils à caler sur la distribution de la base
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
--               texte d'origine : > 20 (19 séries seulement)
WITH critiques AS (
  SELECT a.series_id, count(*) AS n
  FROM manga.ms_reviews_all a
  JOIN staging.ms_reviews s ON s.review_url = a.review_url
  GROUP BY a.series_id
)
SELECT se.series_id
FROM manga.ms_series_enriched se
JOIN critiques c USING (series_id)
WHERE se.series_category_clean = 'Shonen'
  AND se.series_members_rating > 8
  AND c.n > 7
ORDER BY se.series_members_rating DESC NULLS LAST, se.series_id
LIMIT 20;
