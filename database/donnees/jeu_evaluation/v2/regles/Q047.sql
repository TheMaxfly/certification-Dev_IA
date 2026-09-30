-- Q047 — F9 — règle du jeu v1 : public dans (shonen, shojo) ET tomes < 10 ET note > 8 ET critiques > 20
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
--   nb_tomes  = tomes parus (volume_tomes_published) quand le champ est
--               rempli, sinon nombre de numéros de volume distincts, un volume
--               sans numéro comptant comme le volume 1 — sur les volumes du
--               snapshot 2026-07 (définition d, décision du 2026-09-29).
--               Faiblesse connue : quand le champ est vide, les rééditions
--               sous une autre numérotation sont surcomptées. Pour « > 20 »,
--               quelques séries d'une quinzaine de tomes peuvent entrer ; pour
--               « < 10 », quelques courtes rééditées sortir. Bruit accepté pour
--               un filtre de LONGUEUR.
--   seuil critiques : décile supérieur DE LA POPULATION VISÉE (décision du
--               2026-09-29). Les critiques MS sont écrites par volume : leur
--               nombre suit la longueur, et un seuil global (> 7) favorise les
--               séries longues. Ici : séries critiquées de moins de 10 tomes (nb_tomes < 10) — p90 = 4, d'où > 4.
--               Le plancher reste : sans lui, « note > 8 » sur une série courte
--               peut être la note d'un seul lecteur.
--               texte d'origine : > 20 (19 séries seulement)
WITH critiques AS (
  SELECT a.series_id, count(*) AS n
  FROM manga.ms_reviews_all a
  JOIN staging.ms_reviews s ON s.review_url = a.review_url
  GROUP BY a.series_id
),
tomes AS (
  SELECT v.series_id,
         coalesce(max(v.volume_tomes_published),
                  count(DISTINCT coalesce(v.volume_number, 1))) AS nb_tomes
  FROM manga.ms_volumes_enriched v
  JOIN staging.ms_volumes s ON s.volume_url = v.volume_url
  GROUP BY v.series_id
)
SELECT se.series_id
FROM manga.ms_series_enriched se
JOIN critiques c USING (series_id)
JOIN tomes t USING (series_id)
WHERE se.series_category_clean IN ('Shonen', 'Shojo')
  AND t.nb_tomes < 10
  AND se.series_members_rating > 8
  AND c.n > 4
ORDER BY se.series_members_rating DESC NULLS LAST, se.series_id
LIMIT 20;
