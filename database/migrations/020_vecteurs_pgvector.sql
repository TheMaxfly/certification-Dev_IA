-- 020 — pgvector dans la chaîne, et les tables de l'encodage du corpus (E2).
--
-- ÉLÉVATION DE PRIVILÈGE — À LIRE AVANT DE JOUER CETTE MIGRATION
--
-- `CREATE EXTENSION vector` exige un SUPERUTILISATEUR : pgvector 0.6.0 n'est pas
-- une extension « trusted » (`pg_available_extensions` : superuser = t,
-- trusted = f). Elle se joue donc sous `postgres`, comme toutes les migrations
-- (cf. database/README.md, « les composants qui écrivent restent en
-- postgres »). Le rôle de consultation `manga_api` ne le pourrait pas, et n'a
-- pas à le pouvoir. Le serveur doit aussi porter le paquet de l'extension
-- (Debian / Ubuntu : `postgresql-16-pgvector`) — cf. INSTALLATION.md.
--
-- POURQUOI ICI, ET MAINTENANT
--
-- `apimanga` porte `vector` 0.6.0 depuis décembre, installée HORS de la chaîne :
-- aucune migration ne la créait, aucune colonne ne l'utilisait, et `000` ne la
-- décrit pas. Une base reconstruite par la chaîne ne l'avait donc pas — sans que
-- rien ne le signale, puisque `fidelite.sh` ne comparait que des schémas.
-- À partir de cette migration, la chaîne crée l'extension (sur `apimanga`,
-- IF NOT EXISTS n'y fait rien), deux colonnes en dépendent, et `fidelite.sh`
-- compare aussi les extensions et leurs versions.
--
-- CE QU'ELLE CRÉE
--
--   bench.encodages              une ligne par encodage : modèle, révision,
--                                dimension, précision, préfixes, outil et image
--                                du service (avec son digest), taille de lot,
--                                nombre de fragments et dates ;
--   bench.vecteurs_bge_m3        vector(1024), un vecteur par fragment ;
--   bench.vecteurs_embeddinggemma vector(768), un vecteur par fragment.
--
-- Une table de vecteurs PAR MODÈLE : la dimension est dans le type de la colonne,
-- et la base refuse un vecteur de la mauvaise taille. La clé étrangère composite
-- `(encodage_id, modele)` garantit en plus qu'une ligne de `vecteurs_bge_m3` vient
-- d'un encodage de BGE-M3, et de rien d'autre.
--
-- `chunk_id` référence `bench.corpus_chunks` SANS `ON DELETE CASCADE` : un
-- fragment qui porte un vecteur ne peut pas être supprimé sans qu'on ait
-- d'abord décidé du sort de son vecteur. Une reconstruction du corpus (qui
-- passe par la cascade `corpus_docs → corpus_chunks`) échouera donc tant que
-- des vecteurs existent — c'est voulu : des vecteurs ne disparaissent pas en
-- silence avec le texte qui les a produits.
--
-- Chaque vecteur est unitaire à 1e-3 près (CHECK) : le service les rend
-- normalisés, et la recherche par produit scalaire en dépend.
--
-- CE QU'ELLE NE CRÉE PAS
--
-- Aucun index approximatif (ivfflat, hnsw) : la recherche est exacte (décision
-- de la spec E2). Aucune donnée : les vecteurs sont écrits par l'encodeur du
-- module 09. Aucun droit : `bench` reste hors du périmètre de `manga_ro` (012).
-- Tout est qualifié par son schéma, `public.vector` compris (cf. la note de 003
-- sur le search_path d'une base neuve et d'`apimanga`).

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

CREATE TABLE bench.encodages (
    encodage_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    modele           text NOT NULL CHECK (btrim(modele) <> ''),
    revision         text NOT NULL CHECK (revision ~ '^[0-9a-f]{40}$'),
    dimension        integer NOT NULL CHECK (dimension > 0),
    precision_calcul text NOT NULL
        CHECK (precision_calcul IN ('float32', 'float16', 'bfloat16')),
    -- Chaîne vide = aucun préfixe (BGE-M3) ; jamais NULL, pour que « aucun » se
    -- lise comme une décision et non comme une valeur manquante.
    prefixe_document text NOT NULL,
    prefixe_requete  text NOT NULL,
    -- L'outil qui a PRODUIT les vecteurs : le service, ou le repli
    -- sentence-transformers prévu par la spec E2 (§2.2).
    outil            text NOT NULL
        CHECK (outil IN ('text-embeddings-inference', 'sentence-transformers')),
    outil_version    text NOT NULL CHECK (btrim(outil_version) <> ''),
    image            text,
    image_digest     text CHECK (image_digest ~ '^sha256:[0-9a-f]{64}$'),
    taille_lot       integer NOT NULL CHECK (taille_lot > 0),
    nb_fragments     integer CHECK (nb_fragments >= 0),
    commence_le      timestamptz NOT NULL DEFAULT now(),
    termine_le       timestamptz,
    -- Un encodage par le service nomme son image, épinglée par digest.
    CONSTRAINT encodages_service_image_check CHECK (
        outil <> 'text-embeddings-inference'
        OR (image IS NOT NULL AND image_digest IS NOT NULL)
    ),
    -- Un encodage est terminé quand il a compté ses fragments, et seulement alors.
    CONSTRAINT encodages_termine_check CHECK (
        (termine_le IS NULL) = (nb_fragments IS NULL)
    ),
    -- Mêmes paramètres = même encodage : la reprise retrouve sa ligne au lieu
    -- d'en ouvrir une seconde.
    CONSTRAINT encodages_parametres_key UNIQUE NULLS NOT DISTINCT (
        modele, revision, precision_calcul, prefixe_document, prefixe_requete,
        outil, outil_version, image_digest
    ),
    -- Cible de la clé composite des tables de vecteurs.
    CONSTRAINT encodages_id_modele_key UNIQUE (encodage_id, modele)
);

COMMENT ON TABLE bench.encodages IS
    'Un encodage du corpus par un modèle (E2) : ce qui a produit les vecteurs.';

CREATE TABLE bench.vecteurs_bge_m3 (
    chunk_id    bigint PRIMARY KEY
        REFERENCES bench.corpus_chunks (chunk_id),
    encodage_id bigint NOT NULL,
    modele      text NOT NULL DEFAULT 'BAAI/bge-m3'
        CHECK (modele = 'BAAI/bge-m3'),
    embedding   public.vector(1024) NOT NULL
        CHECK (abs(public.vector_norm(embedding) - 1) <= 1e-3),
    FOREIGN KEY (encodage_id, modele)
        REFERENCES bench.encodages (encodage_id, modele)
);

COMMENT ON TABLE bench.vecteurs_bge_m3 IS
    'Vecteurs BGE-M3 (1 024 d, unitaires) des fragments de bench.corpus_chunks.';

CREATE TABLE bench.vecteurs_embeddinggemma (
    chunk_id    bigint PRIMARY KEY
        REFERENCES bench.corpus_chunks (chunk_id),
    encodage_id bigint NOT NULL,
    modele      text NOT NULL DEFAULT 'google/embeddinggemma-300m'
        CHECK (modele = 'google/embeddinggemma-300m'),
    embedding   public.vector(768) NOT NULL
        CHECK (abs(public.vector_norm(embedding) - 1) <= 1e-3),
    FOREIGN KEY (encodage_id, modele)
        REFERENCES bench.encodages (encodage_id, modele)
);

COMMENT ON TABLE bench.vecteurs_embeddinggemma IS
    'Vecteurs EmbeddingGemma (768 d, unitaires) des fragments de bench.corpus_chunks.';
