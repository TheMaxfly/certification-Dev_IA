-- Q053 — F10 — règle du jeu v1 : tomes = 1 ET note > 8 ET critiques > 50 — « tomes = 1 » lu comme one-shot (décision du 2026-09-29)
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
--   one_shot  = ouvrage en UN volume : un seul numéro de volume distinct sur
--               les volumes du snapshot 2026-07, un volume sans numéro comptant
--               comme le volume 1 — une réédition reste un one-shot
--               (définition b, décision du 2026-09-29). C'est une notion de
--               FORMAT, distincte de nb_tomes : « tomes parus = 1 » est rempli
--               pour des séries à plusieurs numéros. Aucun marqueur de format
--               « one-shot » au catalogue (le référentiel porte « Histoires
--               courtes », un recueil — autre notion).
--   seuil critiques : décile supérieur DE LA POPULATION VISÉE (décision du
--               2026-09-29). Les critiques MS sont écrites par volume : leur
--               nombre suit la longueur, et un seuil global (> 7) favorise les
--               séries longues. Ici : one-shots critiqués (one_shot) — p90 = 1, d'où > 1.
--               Le plancher reste : sans lui, « note > 8 » sur une série courte
--               peut être la note d'un seul lecteur.
--               texte d'origine : > 50 (1 série seulement)
WITH critiques AS (
  SELECT a.series_id, count(*) AS n
  FROM manga.ms_reviews_all a
  JOIN staging.ms_reviews s ON s.review_url = a.review_url
  GROUP BY a.series_id
),
one_shot AS (
  SELECT v.series_id
  FROM manga.ms_volumes_enriched v
  JOIN staging.ms_volumes s ON s.volume_url = v.volume_url
  GROUP BY v.series_id
  HAVING count(DISTINCT coalesce(v.volume_number, 1)) = 1
)
SELECT se.series_id
FROM manga.ms_series_enriched se
JOIN critiques c USING (series_id)
JOIN one_shot o USING (series_id)
WHERE se.series_members_rating > 8
  AND c.n > 1
ORDER BY se.series_members_rating DESC NULLS LAST, se.series_id
LIMIT 20;
