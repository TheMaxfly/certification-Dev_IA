-- 007 — Manga Insight, typé, dans le schéma manga.
--
-- Même raison d'être que 006 : le staging (002) est TRONQUÉ à chaque
-- chargement, la comparaison des catalogues joint des tables typées. 006 a
-- couvert Wikidata et Kitsu ; il restait Manga Insight, dont le parquet était
-- introuvable au moment de son écriture. Vérifié avant celle-ci :
-- staging.mi_sorties / staging.mi_series existent (002, vides) ;
-- manga.mi_sorties / manga.mi_series n'existent pas.
--
-- ---------------------------------------------------------------------------
-- RÉVISION DE DÉCISION — « upsert clé EAN » est abandonné (2026-07-16)
-- ---------------------------------------------------------------------------
-- La décision de 2026-07 prévoyait un upsert de clé EAN sur la population A.
-- Elle reposait sur l'idée que l'EAN identifie une sortie. MESURÉ sur le
-- fichier réel (59 062 lignes, empreinte au MANIFEST du raw daté) :
--
--   - 1 721 lignes de A (3,52 %) n'ont AUCUN EAN ;
--   - 534 EAN sont portés par plusieurs lignes, soit 687 lignes en trop ;
--   - un upsert de clé EAN ne garderait que 46 492 des 48 900 lignes de A.
--     PERTE : 2 408 lignes (4,92 %), et silencieuse.
--
-- Les doublons ne sont pas tous des rééditions. Certains le sont — trois
-- éditions de « NonNonBa » (2011, 2016, 2024) partagent un EAN, comme les
-- coffrets et collectors le font déjà dans manga.volume_identity (cf. 001).
-- Mais d'autres sont des ERREURS DE LA SOURCE : l'EAN 9782487369641 porte à la
-- fois « Berserk of Gluttony Vol.12 » et « Martial Universe Vol.10 », deux
-- œuvres distinctes du même éditeur. Un upsert aurait fait disparaître l'une
-- des deux, choisie par l'ordre de lecture du fichier.
--
-- Le signal était déjà dans le profil d'origine (46 485 EAN uniques pour
-- 47 109 à 13 chiffres) : la conséquence n'en avait pas été tirée.
--
-- Aucune clé naturelle n'existe, du reste. Mesuré : `ean` perd 2 407 lignes,
-- `ean+titre` en perd 494, `ean+titre+éditeur+date` en perd encore 1 ; seule
-- la ligne entière est unique. « Unnamed: 0 » vaut 1, 2, 3… et ne compte que
-- 655 valeurs distinctes : c'est un index par fichier tableur source, pas un
-- identifiant.
--
-- D'où : PK TECHNIQUE + RECHARGEMENT COMPLET. Chaque chargement remplace le
-- contenu — la table EST le snapshot du mois, sans ambiguïté sur ce qu'elle
-- contient. L'objectif d'origine (voir les corrections rétroactives mensuelles
-- de la source) en est mieux servi qu'avec un upsert : un titre corrigé en
-- amont ne laisse pas sa version périmée derrière lui.
--
-- Ce rechargement est-il un « DELETE » au sens interdit par le cycle mensuel ?
-- Non, et la différence est de nature. La règle protège les tables ms_*, qui
-- portent des FK entrantes et où « absent du snapshot » ne prouve pas
-- l'inexistence. manga.mi_* n'a AUCUNE FK entrante et n'est pas un
-- référentiel : l'architecture du projet en fait un corpus large de
-- comparaison, jamais un socle. Son historique vit dans le raw daté et
-- immuable, que le chargeur ne touche jamais. Rien n'est perdu ; tout est
-- reconstructible.
--
-- ---------------------------------------------------------------------------
-- LES DEUX POPULATIONS
-- ---------------------------------------------------------------------------
-- Un seul parquet, deux grains, séparés sur « Original Url » — vide ⇒ A (grain
-- sortie/volume, 48 900 lignes), rempli ⇒ B (grain série, 10 162 lignes, issu
-- du crawl Manga-News). Partition re-mesurée sur le fichier frais, identique à
-- celle de 002.
--
-- Chaque table ne porte que les colonnes alimentées pour SA population, ce que
-- le fichier frais confirme : « Ean », « Titre », « Unnamed: 0 », « Dessin » et
-- « Scénario » sont à 0 % en B ; « Original Url », « Adresse », « Code HTTP »,
-- « Title » à 0 % en A. « Unnamed: 19 » est vide à 100 % des deux côtés : seule
-- colonne des 43 écartée, comme en 002.
--
-- Noms de colonnes repris À L'IDENTIQUE de staging.mi_* (002) : minuscules,
-- accents retirés, séparateurs -> « _ », préfixe « _ » du parquet -> « meta_ ».
-- Le staging et sa promotion doivent se lire côte à côte sans traduction.
--
-- Migration ADDITIVE. Politique inchangée : pas de `down`, checksum immuable.

-- ---------------------------------------------------------------------------
-- manga.mi_sorties — population A, grain sortie/volume
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manga.mi_sorties (
    sortie_id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- L'EAN brut, tel que la source l'écrit. NULL sur 3,52 % des lignes, et
    -- NON unique : c'est la conclusion mesurée ci-dessus, pas un renoncement.
    ean                      TEXT NULL,
    -- Résultat du contrôle de la clé EAN-13, calculé AU CHARGEMENT par
    -- identity.ean (le même code qu'en B2), jamais en SQL. NULL quand il n'y a
    -- pas d'EAN du tout : « pas d'EAN » et « EAN faux » sont deux états
    -- distincts, et les confondre ferait disparaître les seconds.
    ean_valide               BOOLEAN NULL,
    titre                    TEXT,
    titre_vo                 TEXT,
    editeur_vf               TEXT,
    editeur_vo               TEXT,
    type                     TEXT,
    genre_1                  TEXT,
    genre_2                  TEXT,
    statut_vf                TEXT,
    statut_vo                TEXT,
    pays                     TEXT,
    annee_pays_d_origine     INTEGER,
    -- La source mélange deux formats dans cette colonne : ISO (« 1978-01-04
    -- 00:00:00 ») et français (« 01 Octobre 2025 »). Les deux sont lus par
    -- identity.dates ; la valeur brute reste dans date_sortie_france_raw.
    date_sortie_france       DATE,
    date_sortie_france_raw   TEXT,
    date_sortie_france_annee INTEGER,
    date_sortie_france_mois  INTEGER,
    tomes_vf                 INTEGER,
    tomes_vo                 INTEGER,
    dessin                   TEXT,
    scenario                 TEXT,
    unnamed_0                TEXT,
    meta_categorie           TEXT,
    meta_fichier             TEXT,
    meta_annee_fichier       TEXT,
    meta_mois_fichier        TEXT,
    meta_nouveaute           BOOLEAN,
    meta_nouvelle_edition    BOOLEAN,
    meta_coffret             BOOLEAN,
    meta_collector           BOOLEAN,
    meta_type_titre          TEXT,
    meta_type_source         TEXT,
    meta_doublon_editeur     BOOLEAN,
    meta_editeurs_doublons   TEXT,
    loaded_at                timestamptz NOT NULL DEFAULT now(),
    source_file              TEXT
);

-- NON unique, et c'est tout l'objet de la révision ci-dessus. Même geste que
-- volume_identity_isbn13_idx (001) : un axe de jointure, pas une clé.
CREATE INDEX IF NOT EXISTS mi_sorties_ean_idx
    ON manga.mi_sorties (ean) WHERE ean IS NOT NULL;
CREATE INDEX IF NOT EXISTS mi_sorties_editeur_vf_idx
    ON manga.mi_sorties (editeur_vf);
CREATE INDEX IF NOT EXISTS mi_sorties_date_sortie_idx
    ON manga.mi_sorties (date_sortie_france);

COMMENT ON TABLE manga.mi_sorties IS
    'Manga Insight, population A (grain sortie/volume). La table EST le '
    'snapshot du mois : rechargée en entier à chaque cycle. Historique dans '
    'le raw daté.';
COMMENT ON COLUMN manga.mi_sorties.ean IS
    'EAN brut. NULL sur 3,52 % des lignes et NON unique : 534 EAN portent '
    'plusieurs sorties (rééditions, mais aussi erreurs de la source).';
COMMENT ON COLUMN manga.mi_sorties.ean_valide IS
    'Contrôle de la clé EAN-13, calculé au chargement (jamais en SQL). '
    'NULL = pas d''EAN ; false = EAN présent mais faux.';

-- ---------------------------------------------------------------------------
-- manga.mi_series — population B, grain série
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manga.mi_series (
    serie_id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Le critère de partition ET la clé de lecture de cette population. NON
    -- unique non plus : 11 URL portent 2 lignes. Le rechargement complet rend
    -- la question sans objet — la table reflète le fichier.
    original_url             TEXT,
    adresse                  TEXT,
    adresse_1                TEXT,
    code_http                INTEGER,
    title                    TEXT,
    titre_vo                 TEXT,
    titre_traduit            TEXT,
    editeur_vf               TEXT,
    editeur_vo               TEXT,
    type                     TEXT,
    genre_1                  TEXT,
    genre_2                  TEXT,
    prepublication           TEXT,
    nombre_tomes_vf          TEXT,
    nombre_tomes_vo          TEXT,
    statut_vf                TEXT,
    statut_vo                TEXT,
    pays                     TEXT,
    annee_pays_d_origine     INTEGER,
    annee                    INTEGER,
    date_sortie_france       DATE,
    date_sortie_france_raw   TEXT,
    date_sortie_france_annee INTEGER,
    date_sortie_france_mois  INTEGER,
    tomes_vf                 INTEGER,
    tomes_vo                 INTEGER,
    meta_categorie           TEXT,
    meta_fichier             TEXT,
    meta_nouveaute           BOOLEAN,
    meta_nouvelle_edition    BOOLEAN,
    meta_coffret             BOOLEAN,
    meta_collector           BOOLEAN,
    meta_type_titre          TEXT,
    meta_type_source         TEXT,
    meta_doublon_editeur     BOOLEAN,
    meta_editeurs_doublons   TEXT,
    loaded_at                timestamptz NOT NULL DEFAULT now(),
    source_file              TEXT
);

CREATE INDEX IF NOT EXISTS mi_series_original_url_idx
    ON manga.mi_series (original_url);
CREATE INDEX IF NOT EXISTS mi_series_editeur_vf_idx
    ON manga.mi_series (editeur_vf);

COMMENT ON TABLE manga.mi_series IS
    'Manga Insight, population B (grain série, issu du crawl Manga-News). '
    'Rechargée en entier à chaque cycle, comme mi_sorties.';

-- ---------------------------------------------------------------------------
-- manga.v_mi_ean_multiples — rendre visibles les EAN qui n'identifient rien
-- ---------------------------------------------------------------------------
-- Une VUE plutôt qu'une colonne-drapeau : le fait « cet EAN porte plusieurs
-- sorties » se DÉDUIT de la table. Un drapeau matérialisé serait une seconde
-- vérité à maintenir, et faux dès le premier rechargement où un doublon
-- disparaît.
--
-- Ces lignes ne sont PAS des rejets : elles sont toutes en base. La vue sert à
-- les regarder — et à ne jamais rejoindre MI sur l'EAN sans savoir que 534
-- d'entre eux ramènent plusieurs sorties.
CREATE OR REPLACE VIEW manga.v_mi_ean_multiples AS
SELECT
    s.ean,
    count(*)                                    AS nb_sorties,
    count(DISTINCT s.titre)                     AS nb_titres_distincts,
    count(DISTINCT s.editeur_vf)                AS nb_editeurs_distincts,
    -- Le signal qui distingue la réédition de l'erreur : un même EAN sur des
    -- titres différents est presque toujours une erreur de saisie de la source.
    (count(DISTINCT s.titre) > 1)               AS titres_divergents,
    array_agg(s.titre ORDER BY s.titre)         AS titres,
    array_agg(s.sortie_id ORDER BY s.sortie_id) AS sorties
FROM manga.mi_sorties s
WHERE s.ean IS NOT NULL
GROUP BY s.ean
HAVING count(*) > 1;

COMMENT ON VIEW manga.v_mi_ean_multiples IS
    'EAN portés par plusieurs sorties. titres_divergents = true signale une '
    'erreur probable de la source (deux œuvres, un EAN) plutôt qu''une '
    'réédition. Aucune de ces lignes n''est écartée du chargement.';
