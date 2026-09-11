-- 015 — les personnages : un socle multi-sources, amorcé par Kitsu.
--
-- POURQUOI DES NOMS NEUTRES ALORS QU'UNE SEULE SOURCE EST CHARGÉE
--
-- Une première rédaction nommait ces tables `kitsu_characters`. C'est le nom
-- d'une table qui n'accueillera jamais Wikipédia. Or trois sources sont déjà
-- engagées : Kitsu est collecté (34 293 personnages, 39 161 liens), Wikipédia
-- français vient de l'être (1 367 articles, 9 002 personnages décrits), AniList
-- est cadré mais bloqué. Choisir aujourd'hui un nom de source, c'est s'obliger
-- demain à une migration de renommage, ou à une seconde table jumelle.
--
-- D'où la règle appliquée partout ici : noms neutres, colonne `source`
-- obligatoire sur les QUATRE tables, même si toutes les lignes valent `kitsu`
-- au premier chargement. L'ajout d'une source devient une INSERTION, jamais une
-- migration.
--
-- CE QUE CETTE MIGRATION NE FAIT PAS, DÉLIBÉRÉMENT
--
-- Elle ne crée aucun moyeu d'identité et aucune cascade de fusion. « Est-ce le
-- même personnage chez Kitsu et chez Wikipédia » est une question de cascade —
-- journal append-only, conditions cumulées, seuils calibrés — et on ne conçoit
-- pas une cascade avec une seule source chargée : les conditions se déduisent de
-- ce que les sources ont en commun. La séquence est : une table par source, puis
-- un moyeu, puis la cascade. Cette migration ne fait que le premier pas.
--
-- CE QU'ELLE PRÉPARE POUR CETTE SUITE, SANS RIEN TRANCHER
--
-- Les formes sont indexées et typées, la graphie japonaise porte son propre
-- index, le `mal_id` est conservé, et le rôle d'origine est gardé à côté du rôle
-- normalisé. Quatre matériaux pour une cascade, aucune décision de fusion.
--
-- MESURES QUI ONT DICTÉ CES CHOIX (2026-09-12, sur le raw)
--
-- La graphie CJK n'est PAS une clé primaire : 814 graphies japonaises (3,23 %)
-- sont portées par plusieurs personnages, touchant 1 999 personnages — « クロ »
-- en désigne 11, « 田中 » 10. Elle reste bien meilleure que les formes latines,
-- ambiguës à 5,03 % (3 239 chaînes pour 7 233 personnages : « princess »,
-- « master », « sensei »). C'est un discriminant fort, à coupler à l'œuvre.
-- D'où : un index dédié pour la rendre exploitable, aucune contrainte d'unicité
-- qui supposerait qu'elle identifie.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. `manga.characters` — grain : un personnage TEL QUE LA SOURCE LE DÉCRIT
-- ---------------------------------------------------------------------------
--
-- Pas de déduplication inter-sources ici : deux lignes peuvent décrire le même
-- personnage de fiction vu par deux sources. C'est voulu, et c'est ce que le
-- futur moyeu d'identité aura à rapprocher.

CREATE TABLE IF NOT EXISTS manga.characters (
    character_uid   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source          text        NOT NULL,
    source_id       text        NOT NULL,
    slug            text,
    canonical_name  text        NOT NULL,
    mal_id          text,
    source_created_at timestamptz,
    source_updated_at timestamptz,
    loaded_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT characters_source_non_vide CHECK (btrim(source) <> ''),
    CONSTRAINT characters_nom_non_vide    CHECK (btrim(canonical_name) <> ''),
    CONSTRAINT characters_source_id_unique UNIQUE (source, source_id)
);

COMMENT ON TABLE manga.characters IS
    'Un personnage tel qu''une source le décrit. Aucune déduplication '
    'inter-sources : c''est l''objet du futur moyeu d''identité.';
COMMENT ON COLUMN manga.characters.source IS
    'Obligatoire dès la première source. C''est ce qui rend les ajouts '
    'ultérieurs possibles par insertion plutôt que par migration.';
COMMENT ON COLUMN manga.characters.mal_id IS
    'Identifiant MyAnimeList. CONSERVÉ, contrairement au staff : là-bas les '
    '15 397 personnes portaient la même chaîne littérale « Moved to mappings '
    'relationship. » — l''identifiant était factice. Ici il porte 34 224 '
    'valeurs distinctes sur 34 293 personnages (99,8 %), toutes réelles : '
    'c''est le seul pont d''identifiant entre Kitsu, MAL et AniList. Le point '
    '16 de la feuille, qui prévoyait sa suppression, vaut pour le staff seul.';

CREATE INDEX IF NOT EXISTS idx_characters_source     ON manga.characters (source);
CREATE INDEX IF NOT EXISTS idx_characters_mal_id     ON manga.characters (mal_id)
    WHERE mal_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. `manga.character_forms` — grain : UNE FORME NOMINALE
-- ---------------------------------------------------------------------------
--
-- Patron éprouvé de `wd_auteurs_formes` : 9 090 formes pour 5 453 auteurs, avec
-- un `forme_type`. Le motif est toujours le même — une forme est une CIBLE DE
-- RECHERCHE. Elle doit être indexable, et il faut pouvoir dire LAQUELLE a
-- matché. Un tableau d'alias dans une colonne JSON ne permet ni l'un ni l'autre.

CREATE TABLE IF NOT EXISTS manga.character_forms (
    forme_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    character_uid bigint      NOT NULL
                  REFERENCES manga.characters (character_uid) ON DELETE CASCADE,
    forme         text        NOT NULL,
    forme_norm    text        NOT NULL,
    forme_type    text        NOT NULL,
    forme_lang    text,
    source        text        NOT NULL,
    loaded_at     timestamptz NOT NULL DEFAULT now(),
    -- Contrôle 11 : le raw porte 9 878 entrées `otherNames`, dont 8 chaînes
    -- vides et 2 blancs seuls. Une forme vide n'est pas une forme : elle
    -- n'est ni recherchable ni comparable. La contrainte la refuse plutôt
    -- que de la stocker, et le chargeur en rend compte. 9 868 alias chargés.
    CONSTRAINT character_forms_non_vide CHECK (btrim(forme) <> ''),
    CONSTRAINT character_forms_type_connu
        CHECK (forme_type IN ('canonical', 'name_lang', 'alias')),
    CONSTRAINT character_forms_source_non_vide CHECK (btrim(source) <> ''),
    CONSTRAINT character_forms_unique
        UNIQUE (character_uid, forme_norm, forme_type, source)
);

COMMENT ON COLUMN manga.character_forms.forme_norm IS
    'Forme normalisée pour la recherche — minuscules, sans accent. Jamais '
    'affichée : `forme` porte la graphie d''origine.';
COMMENT ON COLUMN manga.character_forms.forme_lang IS
    'Langue de la forme. La valeur « ja » désigne la graphie japonaise, qui '
    'porte son propre index (voir ci-dessous) : elle ne souffre d''aucune '
    'variante de translittération, là où Dômyôji, Doumyouji et Domyoji ne se '
    'ressemblent pas pour un appariement de chaînes.';

CREATE INDEX IF NOT EXISTS idx_character_forms_uid  ON manga.character_forms (character_uid);
CREATE INDEX IF NOT EXISTS idx_character_forms_norm ON manga.character_forms (forme_norm);
-- `public.gin_trgm_ops` est QUALIFIÉ, et ce n'est pas décoratif : le dump de
-- 000 se termine par un `set_config('search_path', '', false)` de portée
-- SESSION, et le runner joue tout un `up` sur UNE connexion. Sur une base
-- neuve, le search_path est donc vide quand cette migration passe — alors
-- qu'il ne l'est pas sur `apimanga`, où 000 est seulement marquée. Les trois
-- index trigrammes existants (003, 006, 008) qualifient pour la même raison.
CREATE INDEX IF NOT EXISTS idx_character_forms_trgm
    ON manga.character_forms USING gin (forme_norm public.gin_trgm_ops);

-- Index CJK DISTINCT, exigé par la spec. Il n'est pas redondant avec
-- `idx_character_forms_norm` : une recherche par graphie japonaise porte sur la
-- forme d'origine, non normalisée — la normalisation latine n'a aucun sens sur
-- du japonais — et ne concerne que 77,5 % des personnages. Un index partiel est
-- à la fois plus petit et plus sélectif qu'un balayage de l'index général.
CREATE INDEX IF NOT EXISTS idx_character_forms_ja
    ON manga.character_forms (forme)
    WHERE forme_lang = 'ja';

-- ---------------------------------------------------------------------------
-- 3. `manga.character_descriptions` — grain : UNE DESCRIPTION PAR SOURCE
-- ---------------------------------------------------------------------------
--
-- Une première rédaction rangeait la description dans la table du personnage.
-- Trois sources produiront trois textes, en deux langues, sous trois régimes de
-- licence. On n'écrase pas, on ne concatène pas : la règle de préférence entre
-- sources devient un CHOIX DE LECTURE, modifiable, et non une perte
-- irréversible au chargement.

CREATE TABLE IF NOT EXISTS manga.character_descriptions (
    description_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    character_uid  bigint      NOT NULL
                   REFERENCES manga.characters (character_uid) ON DELETE CASCADE,
    source         text        NOT NULL,
    lang           text,
    texte          text        NOT NULL,
    licence        text,
    provenance     text,
    longueur       integer     GENERATED ALWAYS AS (length(texte)) STORED,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT character_descriptions_texte_non_vide CHECK (btrim(texte) <> ''),
    CONSTRAINT character_descriptions_source_non_vide CHECK (btrim(source) <> ''),
    -- Point d'arrêt B, tranché le 2026-09-12 : trois classes, et NULL quand la
    -- règle ne conclut pas. « fr » n'est pas tenté — mesuré à 0,4 % côté Kitsu,
    -- et les marqueurs français (le, la, des) apparaissent aussi dans des noms
    -- propres au sein de textes anglais. Le faux positif coûterait plus que le
    -- gain. La colonne accepte « fr » pour Wikipédia, qui en produira.
    CONSTRAINT character_descriptions_lang_connue
        CHECK (lang IS NULL OR lang IN ('en', 'ja', 'fr')),
    CONSTRAINT character_descriptions_unique UNIQUE (character_uid, source)
);

COMMENT ON COLUMN manga.character_descriptions.longueur IS
    'Dérivée, pour l''arbitrage aval d''un seuil. Mesuré côté Kitsu : médiane '
    '316 caractères, p95 1 832, max 18 842, et 15,3 % des descriptions non '
    'vides sous 80 caractères (« Sacchan''s mother. »). AUCUN filtrage n''est '
    'appliqué au chargement — on charge tout, et le seuil se décide en lecture.';
COMMENT ON COLUMN manga.character_descriptions.licence IS
    'Régime de réutilisation constaté. 7 434 descriptions Kitsu (25,5 %) citent '
    'leur source, dont 6 092 un wiki de la galaxie Wikipedia/Wikia/Fandom — donc '
    'CC BY-SA, attribution obligatoire. À porter au registre C4 avant tout usage '
    'aval, avec Wikipédia et les critiques signées : une seule instruction pour '
    'les trois.';

CREATE INDEX IF NOT EXISTS idx_character_descriptions_uid
    ON manga.character_descriptions (character_uid);
CREATE INDEX IF NOT EXISTS idx_character_descriptions_source
    ON manga.character_descriptions (source);

-- ---------------------------------------------------------------------------
-- 4. `manga.character_work` — grain : UN LIEN ŒUVRE × PERSONNAGE
-- ---------------------------------------------------------------------------
--
-- LE RÔLE APPARTIENT AU LIEN, PAS AU PERSONNAGE. Un même personnage est
-- principal dans une série et secondaire dans un spin-off. C'est la raison
-- structurelle de l'écart entre 34 293 personnages et 39 161 liens : 3 537
-- personnages sont rattachés à plusieurs œuvres, jusqu'à 12.
--
-- L'œuvre est désignée par le couple (référentiel, identifiant) et non par une
-- clé étrangère : chaque source nomme les œuvres à sa façon — `kitsu_id` ici,
-- `series_id` pour Wikipédia. Rapprocher ces référentiels est le travail du
-- moyeu d'identité, que cette migration ne fait pas.

CREATE TABLE IF NOT EXISTS manga.character_work (
    lien_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    character_uid   bigint      NOT NULL
                    REFERENCES manga.characters (character_uid) ON DELETE CASCADE,
    oeuvre_source   text        NOT NULL,
    oeuvre_id       text        NOT NULL,
    role_source     text,
    role_normalise  text,
    source          text        NOT NULL,
    loaded_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT character_work_source_non_vide CHECK (btrim(source) <> ''),
    CONSTRAINT character_work_oeuvre_non_vide CHECK (btrim(oeuvre_id) <> ''),
    -- Point d'arrêt D, tranché le 2026-09-12 : quatre valeurs françaises, la
    -- granularité de la source préservée même sur 19 liens résiduels.
    CONSTRAINT character_work_role_connu
        CHECK (role_normalise IS NULL
               OR role_normalise IN ('principal', 'secondaire',
                                     'recurrent', 'apparition')),
    CONSTRAINT character_work_unique
        UNIQUE (character_uid, oeuvre_source, oeuvre_id, source)
);

COMMENT ON COLUMN manga.character_work.role_source IS
    'Valeur BRUTE de la source, conservée à côté du rôle normalisé. Kitsu porte '
    'main (9 469, 24,2 %), supporting (29 673, 75,8 %), recurring et cameo (19 '
    'liens). AniList emploie un autre vocabulaire, Wikipédia n''en a aucun mais '
    'porte une position dans la section. Une normalisation qui écraserait '
    'l''original rendrait la divergence indétectable.';
COMMENT ON COLUMN manga.character_work.role_normalise IS
    'principal | secondaire | recurrent | apparition. NULL est permis, et c''est '
    'délibéré : une valeur de source qui ne se range dans aucune des quatre doit '
    'rester NULL avec son `role_source` intact, pour être arbitrée — jamais '
    'forcée dans la case la moins fausse.';

CREATE INDEX IF NOT EXISTS idx_character_work_uid    ON manga.character_work (character_uid);
CREATE INDEX IF NOT EXISTS idx_character_work_oeuvre ON manga.character_work (oeuvre_source, oeuvre_id);
CREATE INDEX IF NOT EXISTS idx_character_work_role   ON manga.character_work (role_normalise);

-- ---------------------------------------------------------------------------
-- 5. Étage de staging — TOUT-TEXT, tronqué à chaque chargement
-- ---------------------------------------------------------------------------
--
-- Pattern ELT du projet : raw immuable → staging tout-TEXT → INSERT SELECT typé.
-- Le staging ne contraint rien : son rôle est d'accueillir la donnée telle
-- quelle pour que le typage soit un acte explicite et vérifiable, à un endroit
-- unique. Les dates y arrivent déjà parsées en ISO par le chargeur Python —
-- jamais de `to_date` dépendant du `lc_time` du serveur, qui vaut ici
-- fr_FR.UTF-8.

-- Chaque table porte `loaded_at` et `source_file`, comme les douze tables de
-- staging existantes. Ce ne sont pas des colonnes de donnée : elles disent
-- QUAND et D'OÙ l'atterrissage a eu lieu, ce qui est la seule chose que le
-- staging sait et que la table typée ne saura plus. La convention vaut pour
-- toutes, pas pour un nombre — un test l'exige de chaque table du schéma.

CREATE TABLE IF NOT EXISTS staging.characters (
    source text, source_id text, slug text, canonical_name text,
    mal_id text, source_created_at text, source_updated_at text,
    loaded_at timestamptz DEFAULT now(), source_file text
);

CREATE TABLE IF NOT EXISTS staging.character_forms (
    source text, source_id text, forme text, forme_norm text,
    forme_type text, forme_lang text,
    loaded_at timestamptz DEFAULT now(), source_file text
);

CREATE TABLE IF NOT EXISTS staging.character_descriptions (
    source text, source_id text, lang text, texte text,
    licence text, provenance text,
    loaded_at timestamptz DEFAULT now(), source_file text
);

CREATE TABLE IF NOT EXISTS staging.character_work (
    source text, source_id text, oeuvre_source text, oeuvre_id text,
    role_source text, role_normalise text,
    loaded_at timestamptz DEFAULT now(), source_file text
);

-- ---------------------------------------------------------------------------
-- 6. Droits de consultation
-- ---------------------------------------------------------------------------
--
-- Le rôle `manga_ro` (migration 012) ne reçoit pas automatiquement les droits
-- sur les tables créées après lui : un GRANT explicite rend le droit vrai quel
-- que soit l'ordre d'exécution.

GRANT SELECT ON
    manga.characters,
    manga.character_forms,
    manga.character_descriptions,
    manga.character_work
TO manga_ro;

COMMIT;
