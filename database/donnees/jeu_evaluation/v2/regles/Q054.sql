-- Q054 — F10 — règle du jeu v1 : public = seinen ET genre = sport ET tomes > 20
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
--   genre sport : le code sport et ses descendants au référentiel
--               (course — « Sport mécanique »), par genre_ref.parent
WITH tomes AS (
  SELECT v.series_id,
         coalesce(max(v.volume_tomes_published),
                  count(DISTINCT coalesce(v.volume_number, 1))) AS nb_tomes
  FROM manga.ms_volumes_enriched v
  JOIN staging.ms_volumes s ON s.volume_url = v.volume_url
  GROUP BY v.series_id
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
JOIN tomes t USING (series_id)
WHERE se.series_category_clean = 'Seinen'
  AND EXISTS (SELECT 1 FROM genres g JOIN manga.genre_ref r ON r.code = g.code
              WHERE g.series_id = se.series_id AND 'sport' IN (r.code, r.parent))
  AND t.nb_tomes > 20
ORDER BY se.series_members_rating DESC NULLS LAST, se.series_id
LIMIT 20;
