-- 004 — Staging des exports Manga Sanctuary (snapshot 2026-07).
--
-- Zone d'atterrissage du chargeur JSONL, même pattern que 002 :
--   - TEXT partout : un fichier source ne doit jamais faire échouer un
--     chargement sur une question de type. Le typage se fait à la PROMOTION.
--   - Aucune contrainte, aucune FK, aucun index : ces tables sont TRUNCATE
--     puis rechargées à chaque cycle mensuel.
--   - loaded_at + source_file : savoir quel fichier a produit quelle ligne.
--
-- Les colonnes sont calquées sur les clés RÉELLES des fichiers, relevées par
-- le profilage du snapshot : 39 clés pour les volumes, 12 pour les critiques.
-- Aucune n'est écartée, y compris celles qui n'ont pas de colonne d'accueil
-- en aval (cf. volume_members_rating ci-dessous) : le staging enregistre ce
-- que la source dit, il n'arbitre pas.
--
-- GRAIN : il n'existe AUCUN fichier *_series.jsonl. Le fichier volumes est
-- dénormalisé — chaque ligne porte le volume ET les attributs de sa série.
-- staging.ms_volumes est donc la source des DEUX grains : ms_volumes_enriched
-- (une ligne par volume_url) et ms_series_enriched (DISTINCT ON (series_id)).
--
-- Politique inchangée : pas de `down`, une transaction par fichier, checksum
-- immuable une fois appliquée (cf. README).

-- ---------------------------------------------------------------------------
-- staging.ms_volumes — 39 clés du fichier volumes + 1 dérivée + 2 techniques
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staging.ms_volumes (
    -- Grain série (20 clés) : dénormalisé, répété sur chaque volume.
    series_id                   TEXT,
    series_url                  TEXT,
    series_title                TEXT,
    series_type                 TEXT,
    series_category             TEXT,
    series_year                 TEXT,
    -- Listes JSON conservées en TEXT brut, castées jsonb à la promotion.
    series_other_titles         TEXT,
    series_dessinateur          TEXT,
    series_scenariste           TEXT,
    series_genres               TEXT,
    series_tags                 TEXT,
    series_mag_prepub           TEXT,
    series_statuses             TEXT,
    series_popularity_rank      TEXT,
    series_members_rating       TEXT,
    series_members_votes        TEXT,
    series_experts_rating       TEXT,
    series_experts_votes        TEXT,
    series_synopsis             TEXT,
    series_related_works        TEXT,
    -- Grain volume (19 clés).
    volume_url                  TEXT,
    volume_title                TEXT,
    volume_number               TEXT,
    -- Date FR telle que scrapée : « mar. 27 nov. 2012 », mais aussi les
    -- sentinelles « Date inconnue » (1 875) et « A paraître » (230). Conservée
    -- brute ; la version exploitable est volume_publication_date_iso.
    volume_publication_date     TEXT,
    volume_dessinateur          TEXT,
    volume_scenariste           TEXT,
    volume_editeur              TEXT,
    volume_ean                  TEXT,
    volume_format               TEXT,
    volume_pages                TEXT,
    volume_country              TEXT,
    volume_status               TEXT,
    volume_tomes_published      TEXT,
    volume_tomes_total          TEXT,
    -- ATTENTION : manga.ms_volumes_enriched n'a PAS de colonne d'accueil pour
    -- volume_members_rating (13,64 % renseigné), alors que volume_experts_rating
    -- existe. Écart du schéma historique, pas du snapshot. La clé est chargée
    -- ici — donc tracée et mesurable — mais la promotion ne peut pas la poser.
    -- Lui ouvrir une colonne est une décision d'évolution, pas de staging.
    volume_members_rating       TEXT,
    volume_members_votes        TEXT,
    volume_experts_rating       TEXT,
    volume_experts_votes        TEXT,
    volume_synopsis             TEXT,
    -- DÉRIVÉE par le chargeur, et non lue dans le fichier : date ISO 8601
    -- (« 2012-11-27 ») ou NULL si non parsable. Reste du TEXT — le staging ne
    -- type pas — mais un TEXT que la promotion peut caster en `date` par un
    -- simple ::date, sans dépendre du lc_time du serveur.
    --
    -- Le parsing des mois français appartient à Python (identity.dates), pour
    -- la même raison que la normalisation des titres : une seule
    -- implémentation, testée, plutôt qu'une seconde en SQL qui divergerait.
    volume_publication_date_iso TEXT,
    loaded_at                   timestamptz NOT NULL DEFAULT now(),
    source_file                 TEXT
);

COMMENT ON TABLE staging.ms_volumes IS
    'Atterrissage du fichier volumes MS. Dénormalisé : source des deux grains '
    '(volumes, et séries par DISTINCT ON (series_id)). TRUNCATE à chaque cycle.';
COMMENT ON COLUMN staging.ms_volumes.volume_publication_date_iso IS
    'DÉRIVÉE par le chargeur (identity.dates), pas lue du fichier : date ISO '
    'ou NULL. Le fichier garde sa date FR brute dans volume_publication_date.';
COMMENT ON COLUMN staging.ms_volumes.volume_members_rating IS
    'Chargée mais NON promue : manga.ms_volumes_enriched n''a pas de colonne '
    'pour cette clé. Écart du schéma historique, à trancher hors staging.';

-- ---------------------------------------------------------------------------
-- staging.ms_reviews — 12 clés du fichier critiques + 1 dérivée + 2 techniques
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staging.ms_reviews (
    series_id       TEXT,
    series_title    TEXT,
    series_url      TEXT,
    volume_number   TEXT,
    volume_url      TEXT,
    review_url      TEXT,
    review_title    TEXT,
    review_score    TEXT,
    review_author   TEXT,
    -- Date FR brute. 29,65 % des valeurs sont TRONQUÉES au jour de la semaine
    -- (« jeu. », « dim. ») par la source : elles ne portent aucune date et
    -- resteront non parsables. C'est un fait du scraping, pas une anomalie de
    -- chargement — d'où review_date_parse_ok en aval, qui le rend mesurable.
    review_date     TEXT,
    review_type     TEXT,
    review_body     TEXT,
    -- DÉRIVÉE par le chargeur (cf. staging.ms_volumes).
    review_date_iso TEXT,
    loaded_at       timestamptz NOT NULL DEFAULT now(),
    source_file     TEXT
);

COMMENT ON TABLE staging.ms_reviews IS
    'Atterrissage du fichier critiques MS. TRUNCATE à chaque cycle.';
COMMENT ON COLUMN staging.ms_reviews.review_date_iso IS
    'DÉRIVÉE par le chargeur (identity.dates), pas lue du fichier : date ISO '
    'ou NULL. NULL pour les 29,65 % de dates tronquées au jour par la source.';
