-- Q050 — F10 — règle du jeu v1 : public = seinen ET année FR entre 2015 et 2020
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
WITH annee_fr AS (
  SELECT v.series_id, extract(year FROM min(v.volume_publication_date))::int AS annee
  FROM manga.ms_volumes_enriched v
  JOIN staging.ms_volumes s ON s.volume_url = v.volume_url
  GROUP BY v.series_id
)
SELECT se.series_id
FROM manga.ms_series_enriched se
JOIN annee_fr a USING (series_id)
WHERE se.series_category_clean = 'Seinen'
  AND a.annee BETWEEN 2015 AND 2020
ORDER BY se.series_members_rating DESC NULLS LAST, se.series_id
LIMIT 20;
