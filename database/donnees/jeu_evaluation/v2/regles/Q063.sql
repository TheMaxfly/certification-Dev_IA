-- Q063 — F9 — règle du jeu v1, reprise de décembre D7 : public dans (shonen, kodomo) ET genre hors (gore, horreur) ET note > 8 ET critiques > 7
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
--   seuil critiques : > 7 = décile supérieur (p90 = 7 sur les 3 456 séries
--               critiquées du snapshot 2026-07), comme Q045
--   genres    : un code compte avec ses descendants au référentiel
--               (genre_ref.parent) — drame couvre tragédie, horreur couvre
--               zombies —, en présence comme en exclusion, comme Q054
--   « pour 11 ans » = public shonen ou kodomo, gore et horreur exclus —
--               c'est une INTERPRÉTATION (décision du 2026-09-30)
--   seuil de critiques conservé : la population n'est pas bornée en
--               longueur, et sans seuil « note > 8 » peut être le 9 d'un
--               seul lecteur
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
WHERE se.series_category_clean IN ('Shonen', 'Kodomo')
  AND NOT EXISTS (SELECT 1 FROM genres g JOIN manga.genre_ref r ON r.code = g.code
                  WHERE g.series_id = se.series_id
                    AND (r.code IN ('gore', 'horreur') OR r.parent IN ('gore', 'horreur')))
  AND se.series_members_rating > 8
  AND c.n > 7
ORDER BY se.series_members_rating DESC NULLS LAST, se.series_id
LIMIT 20;
