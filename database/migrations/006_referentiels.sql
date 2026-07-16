-- 006 — Les référentiels de la cascade, TYPÉS, dans le schéma manga.
--
-- POURQUOI CE FICHIER EXISTE. 002 a posé un staging tout-TEXT pour Wikidata et
-- Kitsu, et c'était le bon geste : une zone d'atterrissage ne doit rien refuser.
-- Mais le staging est TRONQUÉ à chaque chargement — c'est sa définition. La
-- cascade de rapprochement (étape C), elle, joint ces référentiels à chaque
-- décision : elle ne peut pas s'appuyer sur des tables jetables, ni comparer du
-- TEXT à des colonnes typées sans que le moteur ne renonce à ses index.
-- D'où ce miroir typé et durable : staging = ce que le fichier dit, manga = ce
-- sur quoi on décide.
--
-- Il manquait par ailleurs une table pour les MAPPINGS Kitsu — le pont
-- kitsu_id -> myanimelist/anilist/mangaupdates, sans lequel le pivot Wikidata
-- (qui ne connaît que mal_id et anilist_id) ne peut PAS rejoindre Kitsu.
-- Vérifié avant d'écrire : aucune table du dépôt ni de l'héritage ne les
-- accueille. C'est l'étage 0 de la cascade qui n'existait pas.
--
-- CE QUI N'EST PAS RECRÉÉ ICI : manga.kitsu_series_core (héritage, 43 085
-- lignes) porte déjà slug, titres et synopsis Kitsu. 006 ne la double pas ;
-- kitsu_formes ne porte QUE les formes de matching. Attention toutefois : les
-- title_norm_* de kitsu_series_core viennent de l'ancien code du module 05, et
-- ne sont PAS garantis identiques à forme_norm ici, calculé par normaliser().
-- C'est précisément pour ça que la cascade doit lire kitsu_formes, et pas
-- kitsu_series_core.title_norm_*.
--
-- LA RÈGLE QUI TIENT TOUT : forme_norm est calculée par la fonction Python
-- `identity.normaliser()`, des DEUX côtés du rapprochement (ms_formes en B2,
-- wd_formes et kitsu_formes ici). Une jointure d'égalité entre deux colonnes
-- normalisées par deux implémentations différentes est un mensonge : elle
-- renvoie « pas de match » là où les titres sont les mêmes, en silence. Aucune
-- normalisation en SQL, jamais.
--
-- Migration ADDITIVE (CREATE TABLE / CREATE INDEX). Politique inchangée : pas
-- de `down`, une transaction par fichier, checksum immuable (cf. README).

-- ---------------------------------------------------------------------------
-- 1. staging.kitsu_mappings — l'atterrissage qui manquait
-- ---------------------------------------------------------------------------
-- Tout-TEXT, comme le reste du staging (002) : le filtrage (subtype cible,
-- externalSite retenus) se fait à la PROMOTION. Filtrer ici ferait perdre la
-- trace de ce qui a été écarté, et donc la mesure du filtre — c'est la leçon
-- déjà écrite dans 002 à propos du subtype Kitsu.
CREATE TABLE IF NOT EXISTS staging.kitsu_mappings (
    kitsu_id      TEXT,
    external_site TEXT,
    external_id   TEXT,
    mapping_id    TEXT,
    loaded_at     timestamptz NOT NULL DEFAULT now(),
    source_file   TEXT
);

COMMENT ON TABLE staging.kitsu_mappings IS
    'Atterrissage des mappings Kitsu (data[] de mappings.ndjson). Tout-TEXT, '
    'non filtré : le subtype et le site sont tranchés à la promotion.';

-- ---------------------------------------------------------------------------
-- 2. manga.wd_pivot — le pivot d'identifiants Wikidata
-- ---------------------------------------------------------------------------
-- Wikidata est le seul référentiel qui porte À LA FOIS mal_id et anilist_id :
-- c'est par lui que Kitsu et le reste du monde se rejoignent, faute
-- d'identifiant commun entre plateformes.
--
-- Colonnes = union réelle de wd_pivot.csv (qid, mal_id, anilist_id) et de
-- wd_entities.csv (qid, label_principal, annee, mal_id, anilist_id, ann_id,
-- wiki_fr, wiki_en) : les deux fichiers ont le même grain (8 214 lignes, une
-- par qid) et les mêmes mal_id/anilist_id. Deux tables pour un seul grain
-- seraient une jointure permanente sans contrepartie.
CREATE TABLE IF NOT EXISTS manga.wd_pivot (
    qid             TEXT PRIMARY KEY,
    label_principal TEXT,
    annee           INTEGER,
    -- TEXT et non BIGINT : ce sont des identifiants externes, pas des nombres.
    -- On ne les additionne pas, et un zéro de tête resterait significatif.
    mal_id          TEXT,
    anilist_id      TEXT,
    ann_id          TEXT,
    wiki_fr         TEXT,
    wiki_en         TEXT,
    loaded_at       timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Index partiels : ce sont les deux axes de jointure vers Kitsu, via
-- manga.kitsu_mappings. Partiels parce qu'un qid sans mal_id est fréquent et
-- n'a rien à faire dans l'index.
CREATE INDEX IF NOT EXISTS wd_pivot_mal_id_idx
    ON manga.wd_pivot (mal_id) WHERE mal_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS wd_pivot_anilist_id_idx
    ON manga.wd_pivot (anilist_id) WHERE anilist_id IS NOT NULL;

COMMENT ON TABLE manga.wd_pivot IS
    'Pivot Wikidata : le seul référentiel portant mal_id ET anilist_id. '
    'Grain unique par qid — fusion de wd_pivot.csv et wd_entities.csv.';

-- ---------------------------------------------------------------------------
-- 3. manga.wd_formes — le côté ÉTIQUETÉ du rapprochement
-- ---------------------------------------------------------------------------
-- Contrairement aux alias Manga Sanctuary (liste plate, sans langue), Wikidata
-- DÉCLARE la langue de chaque forme : 15 540 en, 10 077 ja, 3 051 fr. La
-- colonne langue est donc renseignée ici — c'est une donnée de la source, pas
-- une inférence, et elle permettra de comparer ce qui est comparable.
CREATE TABLE IF NOT EXISTS manga.wd_formes (
    forme_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    qid        TEXT NOT NULL REFERENCES manga.wd_pivot (qid) ON DELETE CASCADE,
    forme      TEXT NOT NULL,
    forme_norm TEXT NOT NULL,
    -- Les deux seules valeurs produites par wikidata_dump.py : 18 273 label,
    -- 10 395 alias.
    forme_type TEXT NOT NULL CHECK (forme_type IN ('label', 'alias')),
    langue     TEXT NULL,
    loaded_at  timestamptz NOT NULL DEFAULT now(),
    -- Sans la source dans la clé, contrairement à ms_formes : cette table ne
    -- contient que du Wikidata. Deux qid peuvent partager une forme normalisée
    -- (œuvres homonymes) — c'est un fait, pas une erreur.
    UNIQUE (qid, forme_norm)
);

CREATE INDEX IF NOT EXISTS wd_formes_forme_norm_idx
    ON manga.wd_formes (forme_norm);
CREATE INDEX IF NOT EXISTS wd_formes_forme_norm_trgm_idx
    ON manga.wd_formes USING gin (forme_norm public.gin_trgm_ops);

COMMENT ON TABLE manga.wd_formes IS
    'Formes Wikidata (label ou alias), langue DÉCLARÉE par la source. '
    'forme_norm vient de identity.normaliser(), comme ms_formes.';
COMMENT ON COLUMN manga.wd_formes.langue IS
    'Langue déclarée par Wikidata (fr|en|ja). Contrairement aux alias Manga '
    'Sanctuary, ce n''est jamais une inférence.';

-- ---------------------------------------------------------------------------
-- 4. manga.wd_auteurs — l'étage « exact_author » de la cascade
-- ---------------------------------------------------------------------------
-- wd_auteurs.csv ne porte que des qid d'auteurs (qid, auteur_qid) : Wikidata
-- désigne l'auteur par son entité, pas par son nom. auteur_norm reste donc
-- NULLABLE et vide tant que les entités auteurs ne sont pas hydratées — mieux
-- vaut une colonne honnêtement vide qu'un nom inventé à partir d'un Q-id.
CREATE TABLE IF NOT EXISTS manga.wd_auteurs (
    qid         TEXT NOT NULL REFERENCES manga.wd_pivot (qid) ON DELETE CASCADE,
    auteur_qid  TEXT NOT NULL,
    auteur      TEXT NULL,
    auteur_norm TEXT NULL,
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (qid, auteur_qid)
);

CREATE INDEX IF NOT EXISTS wd_auteurs_auteur_qid_idx
    ON manga.wd_auteurs (auteur_qid);
CREATE INDEX IF NOT EXISTS wd_auteurs_auteur_norm_idx
    ON manga.wd_auteurs (auteur_norm) WHERE auteur_norm IS NOT NULL;

COMMENT ON COLUMN manga.wd_auteurs.auteur IS
    'Nom de l''auteur — NULL tant que les entités auteurs ne sont pas '
    'hydratées : la source ne donne qu''un Q-id.';

-- ---------------------------------------------------------------------------
-- 5. manga.kitsu_mappings — le pont vers MAL / AniList / MangaUpdates
-- ---------------------------------------------------------------------------
-- L'étage 0 de la cascade (« kitsu_bridge », cf. le CHECK de match_decision en
-- 001) : Wikidata donne mal_id et anilist_id, Kitsu donne kitsu_id + ses
-- mappings vers ces mêmes sites. La jointure se fait ici, sur des identifiants
-- exacts — le seul étage de la cascade qui ne repose pas sur des titres.
CREATE TABLE IF NOT EXISTS manga.kitsu_mappings (
    kitsu_id      BIGINT NOT NULL,
    external_site TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    loaded_at     timestamptz NOT NULL DEFAULT now(),
    -- Un kitsu_id peut légitimement pointer plusieurs fois vers un même site
    -- (rééditions, fusions de fiches) : la clé est le TRIPLET.
    UNIQUE (kitsu_id, external_site, external_id)
);

-- L'axe de jointure réel : « quel kitsu_id porte ce mal_id ? ». Le site fait
-- partie de la clé de lecture, external_id seul étant ambigu entre sites.
CREATE INDEX IF NOT EXISTS kitsu_mappings_site_external_idx
    ON manga.kitsu_mappings (external_site, external_id);
CREATE INDEX IF NOT EXISTS kitsu_mappings_kitsu_id_idx
    ON manga.kitsu_mappings (kitsu_id);

COMMENT ON TABLE manga.kitsu_mappings IS
    'Pont kitsu_id -> site externe (étage kitsu_bridge de la cascade). '
    'Filtré à la promotion : subtype cible + sites manga uniquement.';

-- ---------------------------------------------------------------------------
-- 6. manga.kitsu_formes — les formes Kitsu de la cible
-- ---------------------------------------------------------------------------
-- forme_type suit les clés RÉELLES du ndjson, et non une liste supposée :
--   'canonical'   — attributes.canonicalTitle (41 249 sur la cible)
--   'title'       — attributes.titles{<langue>}, dict langue -> titre. Les clés
--                   observées sont bien plus riches que ja/en : en_jp, ja_jp,
--                   en, en_us, ko_kr, en_kr, zh_cn, en_cn, ru_ru, es_es. La
--                   langue est donc STOCKÉE dans `langue` plutôt qu'écrasée
--                   dans le type — sans quoi on perdrait le coréen et le
--                   chinois, précisément les manhwa et manhua de la cible.
--   'abbreviated' — attributes.abbreviatedTitles[] (61 739 sur la cible)
--
-- subtype est répété ici bien qu'il vive déjà sur l'entrée : la cascade filtre
-- sur lui à chaque requête de formes, et une jointure vers manga.ndjson n'est
-- pas possible — ce fichier n'est pas une table.
CREATE TABLE IF NOT EXISTS manga.kitsu_formes (
    forme_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kitsu_id   BIGINT NOT NULL,
    forme      TEXT NOT NULL,
    forme_norm TEXT NOT NULL,
    forme_type TEXT NOT NULL
        CHECK (forme_type IN ('canonical', 'title', 'abbreviated')),
    langue     TEXT NULL,
    subtype    TEXT NOT NULL CHECK (subtype IN ('manga', 'manhwa', 'manhua')),
    loaded_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kitsu_id, forme_norm)
);

CREATE INDEX IF NOT EXISTS kitsu_formes_forme_norm_idx
    ON manga.kitsu_formes (forme_norm);
CREATE INDEX IF NOT EXISTS kitsu_formes_forme_norm_trgm_idx
    ON manga.kitsu_formes USING gin (forme_norm public.gin_trgm_ops);
CREATE INDEX IF NOT EXISTS kitsu_formes_kitsu_id_idx
    ON manga.kitsu_formes (kitsu_id);

COMMENT ON TABLE manga.kitsu_formes IS
    'Formes Kitsu de la cible {manga, manhwa, manhua}. Le CHECK sur subtype '
    'rend le filtre structurel : un novel ne peut pas y entrer.';
COMMENT ON COLUMN manga.kitsu_formes.langue IS
    'Clé de attributes.titles (ja_jp, en_jp, ko_kr, zh_cn…) pour forme_type '
    '= ''title''. NULL pour canonical et abbreviated, qui n''en déclarent pas.';
