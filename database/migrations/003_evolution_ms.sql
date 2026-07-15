-- 003 — Évolution des tables Manga Sanctuary pour le snapshot 2026-07.
--
-- Le re-crawl 2026-07 apporte des champs que le schéma ne sait pas encore
-- recevoir (EAN des tomes, grain des critiques), et le rapprochement
-- multi-sources a besoin d'un point d'accroche au moyeu d'identité ainsi que
-- de cibles de matching. Cette migration prépare les STRUCTURES ; elle ne
-- charge rien. Le chargement est une étape distincte (promotion B2).
--
-- PÉRIMÈTRE : strictement ADDITIF — ADD COLUMN, CREATE TABLE, CREATE INDEX.
-- Aucun DROP, aucune réécriture de données, aucune colonne existante touchée.
--
-- CE QUI N'EST PAS RECRÉÉ ICI, parce que le schéma réel le porte déjà :
--   ms_series_enriched.series_genres / series_tags — déjà en jsonb ;
--   ms_reviews_all.volume_number                   — déjà en integer ;
--   ms_reviews_all.review_type                     — déjà en text.
-- Les recréer aurait échoué, ou pire, dupliqué une source de vérité. La liste
-- vient d'une inspection du schéma réel, pas de la spécification.
--
-- Pas d'`IF NOT EXISTS` sur les ADD COLUMN / CREATE TABLE : le runner garantit
-- qu'un fichier n'est joué qu'une fois, et la garde masquerait une divergence
-- (une colonne déjà là, de type inattendu) au lieu de la signaler. Seule
-- l'extension pg_trgm en porte une : elle peut légitimement préexister.
--
-- Politique inchangée : pas de `down`, une transaction par fichier, checksum
-- immuable une fois la migration appliquée (cf. README).

-- ---------------------------------------------------------------------------
-- manga.ms_volumes_enriched — l'EAN du tome, tel que scrapé
-- ---------------------------------------------------------------------------
-- BRUT et TEXT, volontairement : c'est la trace de ce que la source a affiché.
-- 61,90 % des 103 811 volumes 2026-07 en portent un ; parmi eux 99,02 % sont
-- des EAN-13 valides — donc ~1 % ne le sont pas, et cette colonne doit pouvoir
-- les accueillir pour qu'on puisse les voir.
--
-- L'ISBN-13 NORMALISÉ et son contrôle de clé vivent dans manga.volume_identity
-- (isbn13 CHAR(13) + isbn13_valide, cf. 001). On ne duplique pas la
-- normalisation ici : cette colonne est la matière première, volume_identity
-- en est la lecture typée.
ALTER TABLE manga.ms_volumes_enriched
    ADD COLUMN volume_ean TEXT NULL;

COMMENT ON COLUMN manga.ms_volumes_enriched.volume_ean IS
    'EAN brut tel que scrapé (jamais normalisé). L''ISBN-13 typé et son '
    'contrôle de clé sont dans manga.volume_identity.';

-- ---------------------------------------------------------------------------
-- manga.ms_series_enriched — rattachement au moyeu d'identité
-- ---------------------------------------------------------------------------
-- NULLABLE et laissée NULL par cette migration : le peuplement est le travail
-- de la cascade de rapprochement (étape C), qui journalise ses décisions dans
-- manga.match_decision. 003 n'ouvre que la porte.
--
-- BIGINT pour épouser manga.work_identity.work_uid (bigint, identity). Une
-- série Manga Sanctuary sans œuvre rattachée est un état normal, pas une
-- anomalie : work_identity.series_id est lui-même nullable et sans FK,
-- puisqu'une œuvre peut exister sans fiche Manga Sanctuary.
ALTER TABLE manga.ms_series_enriched
    ADD COLUMN work_uid BIGINT NULL
        REFERENCES manga.work_identity (work_uid);

CREATE INDEX ms_series_enriched_work_uid_idx
    ON manga.ms_series_enriched (work_uid);

COMMENT ON COLUMN manga.ms_series_enriched.work_uid IS
    'Rattachement au moyeu manga.work_identity. NULL tant que la cascade de '
    'rapprochement (étape C) n''a pas tranché — un NULL est un état normal.';

-- ---------------------------------------------------------------------------
-- manga.ms_reviews_all — le grain de la critique
-- ---------------------------------------------------------------------------
-- Aujourd'hui constante : les 11 052 critiques du snapshot 2026-07 portent
-- TOUTES une ancre volume (volume_url à 100 %, volume_number à 94,39 %). La
-- colonne est néanmoins créée, et le CHECK laisse la place à 'serie' : le jour
-- où une source produira un avis au grain série, la structure l'accueillera
-- sans migration, et surtout les lectures écrites d'ici là auront déjà dû
-- déclarer le grain qu'elles supposent.
--
-- DEFAULT 'volume' : cohérent avec les lignes déjà en base, qui sont toutes de
-- ce grain. NOT NULL parce qu'une critique sans grain déclaré n'a pas de sens
-- — c'est ce que la colonne existe pour empêcher.
ALTER TABLE manga.ms_reviews_all
    ADD COLUMN review_grain TEXT NOT NULL DEFAULT 'volume'
        CHECK (review_grain IN ('volume', 'serie'));

COMMENT ON COLUMN manga.ms_reviews_all.review_grain IS
    'Grain de l''avis : ''volume'' (ancré sur un tome) ou ''serie''. Constante '
    'à ''volume'' sur le snapshot 2026-07 ; ''serie'' est prévu, pas encore vu.';

-- ---------------------------------------------------------------------------
-- manga.ms_formes — les cibles de matching Manga Sanctuary
-- ---------------------------------------------------------------------------
-- Une ligne par TITRE d'une série : le titre principal, plus chacun de ses
-- alias. Sur le snapshot 2026-07 : 14 652 séries, dont 10 601 (72,35 %) ont au
-- moins un alias, pour 18 135 alias au total (jusqu'à 10 pour une série).
--
-- Pourquoi une table plutôt qu'un tableau sur la série : c'est ici que tapent
-- les étages 1 (égalité exacte) et 3 (trigramme) de la cascade. Une forme doit
-- être une LIGNE indexable, pas un élément de jsonb.
--
-- langue reste NULL côté Manga Sanctuary, et ce n'est pas un oubli : la source
-- (series_other_titles) est une liste plate de chaînes, SANS langue déclarée.
-- La colonne existe pour les sources qui, elles, la déclarent (Wikidata, Kitsu
-- — cf. staging.wd_formes.langue). On aurait pu deviner la langue d'après
-- l'écriture des caractères — 53,92 % des alias sont en CJK, 45,26 % en latin
-- — mais une inférence rangée dans une colonne « langue » devient une donnée
-- source aux yeux du lecteur suivant. On préfère un NULL honnête.
CREATE TABLE manga.ms_formes (
    forme_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    series_id  BIGINT NOT NULL
        REFERENCES manga.ms_series_enriched (series_id) ON DELETE CASCADE,
    -- La chaîne telle que la source l'écrit : c'est elle qu'on ré-affiche.
    forme      TEXT NOT NULL,
    -- La chaîne normalisée, SEULE base de comparaison. Calculée par la
    -- fonction Python testée du module 05 (identity/, NFKD + correctif
    -- dakuten) lors de la promotion, jamais réimplémentée en SQL : deux
    -- normalisations qui divergent d'un caractère font un matching faux et
    -- silencieux. 003 ne crée que la colonne et les index qui l'exploiteront.
    forme_norm TEXT NOT NULL,
    forme_type TEXT NOT NULL
        CHECK (forme_type IN ('title', 'alias')),
    -- Prépare la cohabitation des formes d'autres sources dans cette table.
    source     TEXT NOT NULL DEFAULT 'ms',
    langue     TEXT NULL,
    loaded_at  timestamptz NOT NULL DEFAULT now(),
    -- Deux séries peuvent légitimement partager une forme normalisée (c'est
    -- même tout le problème du rapprochement) : l'unicité est donc portée par
    -- le TRIPLET. Ce qu'elle interdit, c'est de charger deux fois la même
    -- forme pour la même série depuis la même source.
    UNIQUE (series_id, forme_norm, source)
);

-- Garde idempotente : l'extension peut déjà être installée sur la base.
--
-- WITH SCHEMA public, et opclass qualifiée juste en dessous : ni l'un ni
-- l'autre n'est décoratif. Le dump de 000 se termine par un
-- `set_config('search_path', '', false)` de portée SESSION, et le runner joue
-- tout un `up` sur UNE connexion : après 000, le search_path est vide pour les
-- migrations suivantes. Une base neuve (qui exécute 000) et `apimanga` (où 000
-- est seulement marquée) n'ont donc PAS le même search_path au moment où 003
-- passe. Tout écrire en qualifié rend cette migration indifférente à cet état.
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;

-- Étage 3 de la cascade — rapprochement flou. Indexe l'opérateur % (et
-- similarity()), sans quoi chaque candidat imposerait un parcours complet.
CREATE INDEX ms_formes_forme_norm_trgm_idx
    ON manga.ms_formes USING gin (forme_norm public.gin_trgm_ops);

-- Étage 1 — égalité exacte. Non redondant avec l'index UNIQUE ci-dessus, dont
-- la colonne de tête est series_id : une recherche par forme_norm seule ne
-- pourrait pas s'en servir.
CREATE INDEX ms_formes_forme_norm_idx
    ON manga.ms_formes (forme_norm);

COMMENT ON TABLE manga.ms_formes IS
    'Cibles de matching Manga Sanctuary : une ligne par titre (principal ou '
    'alias). Alimente les étages 1 (exact) et 3 (trigramme) de la cascade.';
COMMENT ON COLUMN manga.ms_formes.forme_norm IS
    'Forme normalisée par la fonction Python du module 05 (source de vérité '
    'unique), jamais par du SQL. Peuplée à la promotion.';
COMMENT ON COLUMN manga.ms_formes.langue IS
    'Langue déclarée PAR LA SOURCE. Toujours NULL côté Manga Sanctuary, dont '
    'les alias sont une liste plate sans langue : jamais une inférence.';
