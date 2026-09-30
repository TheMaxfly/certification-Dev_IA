-- Q064 — F9 — règle du jeu v1, reprise de décembre D8 : popularité <= 50 ET note > 8
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
--   popularité = series_popularity_rank, rang de popularité Manga Sanctuary
--               (1 = la plus populaire)
--   « incontournable » = popularité (rang <= 50), « pour débuter » = bien
--               noté (note > 8), « classique » couvert par le rang : une
--               œuvre en tête de popularité est une œuvre installée
--               (interprétation, décision du 2026-09-30)
SELECT se.series_id
FROM manga.ms_series_enriched se
WHERE se.series_popularity_rank <= 50
  AND se.series_members_rating > 8
ORDER BY se.series_members_rating DESC NULLS LAST, se.series_id
LIMIT 20;
