-- 021 — plusieurs corpus et plusieurs encodages côte à côte ; un journal des
-- promotions et une vue qui dit quel encodage est en service (E3, étape 1).
--
-- ÉLÉVATION DE PRIVILÈGE : aucune. Ni extension, ni rôle, ni droit : la migration
-- se joue sous `postgres`, comme toutes les autres, et `bench` reste hors du
-- périmètre de `manga_ro` (012).
--
-- POURQUOI
--
-- Jusqu'ici, `bench` ne connaissait qu'UN corpus (`doc_key` est la clé primaire
-- des documents) et une table de vecteurs ne portait qu'UN encodage par modèle.
-- Construire un corpus v2 à côté du v1 imposait d'effacer le v1 ; l'encoder
-- obligeait à effacer les vecteurs du v1, c'est-à-dire la référence d'E2 et le
-- retour arrière. Et rien ne disait quel jeu de vecteurs l'application doit lire.
--
-- CE QU'ELLE FAIT
--
--   1. bench.corpus — une ligne par version du corpus : règle de construction,
--      découpage (FK vers chunking_strategies), dates, comptes à la clôture.
--      Le corpus existant devient `v1`, SANS QU'AUCUNE DE SES LIGNES CHANGE DE
--      CONTENU : la colonne `corpus_id` est ajoutée avec une valeur par défaut
--      constante (aucune réécriture des lignes, PostgreSQL ≥ 11).
--   2. Documents et fragments portent `corpus_id`. La clé des documents devient
--      (corpus_id, doc_key) : un même `doc_key` (ms_review:<site_id>, kitsu:<id>)
--      existe une fois par version. `chunk_id` reste l'identité d'un fragment.
--      Les tables d'historique de décembre (qrels, retrieval_results) suivent :
--      même colonne, même cascade qu'avant.
--   3. Un encodage dit de quel corpus il vient ; un vecteur est identifié par
--      (encodage_id, chunk_id). Deux encodages du même modèle coexistent dans la
--      même table. Les clés composites garantissent qu'un vecteur relie un
--      fragment et un encodage DU MÊME CORPUS (même principe que la colonne
--      `modele` de 020).
--   4. bench.promotions — journal où l'on AJOUTE sans jamais modifier (ni UPDATE,
--      ni DELETE, ni TRUNCATE : refusés par déclencheur), et la vue
--      bench.v_encodage_en_service qui en tire l'encodage lu par l'application.
--      Même principe que manga.match_decision / v_match_current (001), mais ici
--      l'ajout seul est TENU PAR LA BASE, et chaque ligne est contrôlée à
--      l'insertion (l'« encodage précédent » est celui qui était en service).
--   5. bench.eval_runs — une ligne par run de mesure : son corpus et son
--      encodage. `bench.eval_mesures` n'est pas touchée : ses lignes ne sont pas
--      réécrites et aucune contrainte ne lui est ajoutée.
--   6. Un corpus CLOS ne bouge plus : la base refuse d'y insérer, d'y modifier ou
--      d'y supprimer un document ou un fragment. Seule exception, explicite :
--      `SELECT bench.autoriser_extension('<corpus>')`, dans la transaction qui
--      AJOUTE (ni modification ni suppression). Les vecteurs ne sont pas
--      concernés : un corpus s'encode après sa clôture.
--
-- LA VALEUR PAR DÉFAUT 'v1' — un compromis écrit, et une dette
--
-- Le chargeur du corpus v1 (`corpus.construire`) et les tests existants écrivent
-- sans nommer de corpus : ils continuent d'écrire dans `v1`. Tout code qui écrit
-- une autre version NOMME son corpus. Ce qu'une omission ne peut pas faire : mêler
-- deux corpus. Un fragment ne référence qu'un document de son corpus, un vecteur
-- qu'un fragment et un encodage de son corpus (clés composites). Et le v1 étant
-- clos (6), un document v2 qui y tomberait par omission est REFUSÉ.
-- DETTE : retirer ce défaut quand le chargeur du v1 et les tests nommeront leur
-- corpus (décision de Max du 2026-10-09).
--
-- CE QU'ELLE NE FAIT PAS
--
-- Aucun vecteur, aucun document n'est créé, copié ni supprimé. Aucun index
-- approximatif. Aucun droit. La lecture filtrée par encodage et la confrontation
-- du service au registre vivent dans le code des modules 09 et 10.

-- --------------------------------------------------------------------------- --
--  0. Le découpage du v1, inscrit s'il manque (base neuve)
-- --------------------------------------------------------------------------- --
-- Sur `apimanga`, la ligne existe depuis décembre (chunking_id 1). Une base
-- reconstruite par la chaîne ne l'avait pas : sans elle, le corpus v1 ne
-- pourrait pas nommer son découpage.

INSERT INTO bench.chunking_strategies (name, chunk_size, chunk_overlap, notes)
VALUES ('char_1200_overlap_200', 1200, 200, 'benchmark default')
ON CONFLICT (name) DO NOTHING;

-- --------------------------------------------------------------------------- --
--  1. Le registre des corpus
-- --------------------------------------------------------------------------- --

CREATE TABLE bench.corpus (
    corpus_id    text PRIMARY KEY CHECK (corpus_id ~ '^v[0-9]+$'),
    regle        text NOT NULL CHECK (btrim(regle) <> ''),
    chunking_id  bigint NOT NULL REFERENCES bench.chunking_strategies (chunking_id),
    cree_le      timestamptz NOT NULL DEFAULT now(),
    -- Clos = construit et compté. Un corpus ouvert est en construction.
    clos_le      timestamptz,
    nb_documents integer CHECK (nb_documents >= 0),
    nb_fragments integer CHECK (nb_fragments >= 0),
    CONSTRAINT corpus_clos_check CHECK (
        (clos_le IS NULL) = (nb_documents IS NULL)
        AND (clos_le IS NULL) = (nb_fragments IS NULL)
    )
);

COMMENT ON TABLE bench.corpus IS
    'Une version du corpus de recherche : règle de construction, découpage, '
    'comptes à la clôture. Documents, fragments, encodages et runs de mesure '
    'portent son identifiant.';

INSERT INTO bench.corpus (corpus_id, regle, chunking_id)
SELECT 'v1',
       'Règle de reconstruction du 2026-09-28 (critiques de manga.ms_reviews_all '
       || 'présentes dans le snapshot Manga Sanctuary 2026-07, corps non vide, '
       || 'dédoublonnées par série et corps ; références à un membre masquées) ; '
       || 'part Kitsu du 2026-09-29 (raw Kitsu 20260714T152202Z). '
       || 'Code : corpus.construire (module 05).',
       chunking_id
FROM bench.chunking_strategies
WHERE name = 'char_1200_overlap_200';

-- --------------------------------------------------------------------------- --
--  2. Documents et fragments : leur corpus
-- --------------------------------------------------------------------------- --

-- Les trois clés étrangères vers la clé actuelle des documents tombent, pour
-- être reposées sur la clé composite, à l'identique (même nom, même cascade).
ALTER TABLE bench.corpus_chunks DROP CONSTRAINT corpus_chunks_doc_key_fkey;
ALTER TABLE bench.qrels DROP CONSTRAINT qrels_doc_key_fkey;
ALTER TABLE bench.retrieval_results DROP CONSTRAINT retrieval_results_doc_key_fkey;

ALTER TABLE bench.corpus_docs
    ADD COLUMN corpus_id text NOT NULL DEFAULT 'v1'
        REFERENCES bench.corpus (corpus_id);
ALTER TABLE bench.corpus_docs DROP CONSTRAINT corpus_docs_pkey;
ALTER TABLE bench.corpus_docs
    ADD CONSTRAINT corpus_docs_pkey PRIMARY KEY (corpus_id, doc_key);

ALTER TABLE bench.corpus_chunks
    ADD COLUMN corpus_id text NOT NULL DEFAULT 'v1';
ALTER TABLE bench.corpus_chunks
    DROP CONSTRAINT corpus_chunks_doc_key_chunk_index_key;
ALTER TABLE bench.corpus_chunks
    ADD CONSTRAINT corpus_chunks_doc_key_chunk_index_key
        UNIQUE (corpus_id, doc_key, chunk_index);
ALTER TABLE bench.corpus_chunks
    ADD CONSTRAINT corpus_chunks_doc_key_fkey FOREIGN KEY (corpus_id, doc_key)
        REFERENCES bench.corpus_docs (corpus_id, doc_key) ON DELETE CASCADE;
-- Cible des clés composites des tables de vecteurs.
ALTER TABLE bench.corpus_chunks
    ADD CONSTRAINT corpus_chunks_id_corpus_key UNIQUE (chunk_id, corpus_id);

-- L'historique de décembre : mêmes règles qu'avant, rattachées au v1.
ALTER TABLE bench.qrels
    ADD COLUMN corpus_id text NOT NULL DEFAULT 'v1';
ALTER TABLE bench.qrels
    ADD CONSTRAINT qrels_doc_key_fkey FOREIGN KEY (corpus_id, doc_key)
        REFERENCES bench.corpus_docs (corpus_id, doc_key) ON DELETE CASCADE;
ALTER TABLE bench.retrieval_results
    ADD COLUMN corpus_id text NOT NULL DEFAULT 'v1';
ALTER TABLE bench.retrieval_results
    ADD CONSTRAINT retrieval_results_doc_key_fkey FOREIGN KEY (corpus_id, doc_key)
        REFERENCES bench.corpus_docs (corpus_id, doc_key) ON DELETE CASCADE;

COMMENT ON COLUMN bench.corpus_docs.corpus_id IS
    'Version du corpus (bench.corpus). Défaut v1 : le chargeur du v1 n''en nomme '
    'pas ; tout autre chargeur nomme la sienne.';
COMMENT ON COLUMN bench.corpus_chunks.corpus_id IS
    'Version du corpus, celle de son document (clé composite).';

-- --------------------------------------------------------------------------- --
--  2 bis. Un corpus clos ne bouge plus, sauf extension explicite
-- --------------------------------------------------------------------------- --
-- Clos = construit et compté (`clos_le`). Après, ses documents et ses fragments
-- ne s'insèrent, ne se modifient ni ne se suppriment plus. L'ajout d'un document
-- à un corpus en service (critique de libraire, étape 4) reste possible, mais
-- seulement par une opération NOMMÉE : `bench.autoriser_extension`, valable pour
-- la transaction en cours, pour l'insertion seule. Ce qu'elle ne fait pas : tenir
-- les comptes de clôture à jour, ni dire si l'ajout complète la version ou en
-- crée une autre : décision ouverte, à l'étape 4.
-- Modifier ou retirer un document d'un corpus clos (une critique de libraire)
-- demandera une opération explicite de retrait, à concevoir avec cette décision.
-- Une base neuve laisse le v1 ouvert : le chargeur du v1 et les tests y écrivent
-- comme avant.

CREATE FUNCTION bench.autoriser_extension(p_corpus_id text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM bench.corpus
                   WHERE corpus_id = p_corpus_id AND clos_le IS NOT NULL) THEN
        RAISE EXCEPTION 'corpus % : absent ou ouvert — une extension vise un corpus clos',
            p_corpus_id;
    END IF;
    -- true : le réglage ne vit que le temps de la transaction.
    PERFORM set_config('bench.extension_corpus', p_corpus_id, true);
END;
$$;

COMMENT ON FUNCTION bench.autoriser_extension(text) IS
    'Ouvre, pour la transaction en cours, l''ajout de documents et de fragments à '
    'un corpus clos. Ni modification, ni suppression.';

CREATE FUNCTION bench.corpus_clos_refuser() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_corpus text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_corpus := NEW.corpus_id;
    ELSIF TG_OP = 'DELETE' THEN
        v_corpus := OLD.corpus_id;
    ELSE
        -- Une modification ne sort pas d'un corpus clos et n'y entre pas.
        SELECT corpus_id INTO v_corpus FROM bench.corpus
        WHERE corpus_id IN (OLD.corpus_id, NEW.corpus_id) AND clos_le IS NOT NULL
        LIMIT 1;
    END IF;
    IF v_corpus IS NOT NULL AND EXISTS (
        SELECT 1 FROM bench.corpus WHERE corpus_id = v_corpus AND clos_le IS NOT NULL
    ) THEN
        IF TG_OP = 'INSERT'
           AND current_setting('bench.extension_corpus', true) = v_corpus THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION '%.% : le corpus % est clos — % refusé (une extension passe par bench.autoriser_extension)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME, v_corpus, TG_OP;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER corpus_docs_corpus_clos
    BEFORE INSERT OR UPDATE OR DELETE ON bench.corpus_docs
    FOR EACH ROW EXECUTE FUNCTION bench.corpus_clos_refuser();
CREATE TRIGGER corpus_chunks_corpus_clos
    BEFORE INSERT OR UPDATE OR DELETE ON bench.corpus_chunks
    FOR EACH ROW EXECUTE FUNCTION bench.corpus_clos_refuser();

-- --------------------------------------------------------------------------- --
--  3. Encodages et vecteurs : plusieurs encodages d'un même modèle
-- --------------------------------------------------------------------------- --

ALTER TABLE bench.encodages
    ADD COLUMN corpus_id text NOT NULL DEFAULT 'v1'
        REFERENCES bench.corpus (corpus_id);
-- Mêmes paramètres SUR LE MÊME CORPUS = même encodage (la reprise retrouve sa
-- ligne) ; sur un autre corpus, c'est un autre encodage.
ALTER TABLE bench.encodages DROP CONSTRAINT encodages_parametres_key;
ALTER TABLE bench.encodages
    ADD CONSTRAINT encodages_parametres_key UNIQUE NULLS NOT DISTINCT (
        corpus_id, modele, revision, precision_calcul, prefixe_document,
        prefixe_requete, outil, outil_version, image_digest
    );
ALTER TABLE bench.encodages
    ADD CONSTRAINT encodages_id_modele_corpus_key
        UNIQUE (encodage_id, modele, corpus_id);

COMMENT ON COLUMN bench.encodages.corpus_id IS
    'Le corpus encodé. Un vecteur relie un fragment et un encodage de ce corpus.';

ALTER TABLE bench.vecteurs_bge_m3
    DROP CONSTRAINT vecteurs_bge_m3_chunk_id_fkey,
    DROP CONSTRAINT vecteurs_bge_m3_encodage_id_modele_fkey,
    DROP CONSTRAINT vecteurs_bge_m3_pkey;
ALTER TABLE bench.vecteurs_bge_m3
    ADD COLUMN corpus_id text NOT NULL DEFAULT 'v1';
ALTER TABLE bench.vecteurs_bge_m3
    ADD CONSTRAINT vecteurs_bge_m3_pkey PRIMARY KEY (encodage_id, chunk_id),
    ADD CONSTRAINT vecteurs_bge_m3_chunk_id_fkey FOREIGN KEY (chunk_id, corpus_id)
        REFERENCES bench.corpus_chunks (chunk_id, corpus_id),
    ADD CONSTRAINT vecteurs_bge_m3_encodage_fkey
        FOREIGN KEY (encodage_id, modele, corpus_id)
        REFERENCES bench.encodages (encodage_id, modele, corpus_id);
-- Pour la clé étrangère : retrouver les vecteurs d'un fragment qu'on supprime.
CREATE INDEX vecteurs_bge_m3_chunk_idx ON bench.vecteurs_bge_m3 (chunk_id);

ALTER TABLE bench.vecteurs_embeddinggemma
    DROP CONSTRAINT vecteurs_embeddinggemma_chunk_id_fkey,
    DROP CONSTRAINT vecteurs_embeddinggemma_encodage_id_modele_fkey,
    DROP CONSTRAINT vecteurs_embeddinggemma_pkey;
ALTER TABLE bench.vecteurs_embeddinggemma
    ADD COLUMN corpus_id text NOT NULL DEFAULT 'v1';
ALTER TABLE bench.vecteurs_embeddinggemma
    ADD CONSTRAINT vecteurs_embeddinggemma_pkey PRIMARY KEY (encodage_id, chunk_id),
    ADD CONSTRAINT vecteurs_embeddinggemma_chunk_id_fkey
        FOREIGN KEY (chunk_id, corpus_id)
        REFERENCES bench.corpus_chunks (chunk_id, corpus_id),
    ADD CONSTRAINT vecteurs_embeddinggemma_encodage_fkey
        FOREIGN KEY (encodage_id, modele, corpus_id)
        REFERENCES bench.encodages (encodage_id, modele, corpus_id);
CREATE INDEX vecteurs_embeddinggemma_chunk_idx
    ON bench.vecteurs_embeddinggemma (chunk_id);

COMMENT ON COLUMN bench.vecteurs_bge_m3.corpus_id IS
    'Le corpus du fragment ET de l''encodage (clés composites) : jamais deux corpus mêlés.';
COMMENT ON COLUMN bench.vecteurs_embeddinggemma.corpus_id IS
    'Le corpus du fragment ET de l''encodage (clés composites) : jamais deux corpus mêlés.';

-- --------------------------------------------------------------------------- --
--  4. Ajout seul : le refus commun au journal et aux runs
-- --------------------------------------------------------------------------- --

CREATE FUNCTION bench.refuser_modification_journal() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '%.% : on ajoute une ligne, on n''en modifie ni n''en supprime',
        TG_TABLE_SCHEMA, TG_TABLE_NAME;
END;
$$;

-- --------------------------------------------------------------------------- --
--  5. Le journal des promotions, et l'encodage en service
-- --------------------------------------------------------------------------- --

CREATE TABLE bench.eval_runs (
    run_id      uuid PRIMARY KEY,
    corpus_id   text NOT NULL REFERENCES bench.corpus (corpus_id),
    -- NULL : une mesure sans vecteurs (plein texte, TF-IDF).
    encodage_id bigint REFERENCES bench.encodages (encodage_id),
    inscrit_le  timestamptz NOT NULL DEFAULT now(),
    note        text
);

COMMENT ON TABLE bench.eval_runs IS
    'Un run de mesure : le corpus et l''encodage mesurés. Les lignes de '
    'bench.eval_mesures s''y relient par run_id, sans contrainte ajoutée à '
    'eval_mesures (immuable, inchangée). Ajout seul.';

CREATE TRIGGER eval_runs_ajout_seul
    BEFORE UPDATE OR DELETE ON bench.eval_runs
    FOR EACH ROW EXECUTE FUNCTION bench.refuser_modification_journal();
CREATE TRIGGER eval_runs_sans_vidage
    BEFORE TRUNCATE ON bench.eval_runs
    FOR EACH STATEMENT EXECUTE FUNCTION bench.refuser_modification_journal();

CREATE TABLE bench.promotions (
    promotion_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decide_le             timestamptz NOT NULL DEFAULT now(),
    decision              text NOT NULL
        CHECK (decision IN ('promu', 'refuse', 'retour_arriere')),
    -- L'encodage dont on décide : promu, refusé, ou remis en service.
    encodage_id           bigint NOT NULL REFERENCES bench.encodages (encodage_id),
    -- Celui qui était en service au moment de la décision (NULL : aucun).
    encodage_precedent_id bigint REFERENCES bench.encodages (encodage_id),
    run_id                uuid REFERENCES bench.eval_runs (run_id),
    motif                 text NOT NULL CHECK (btrim(motif) <> ''),
    decide_par            text NOT NULL CHECK (btrim(decide_par) <> ''),
    -- Vrai quand la décision s'écarte de la règle de promotion écrite.
    derogation            boolean NOT NULL DEFAULT false
);

COMMENT ON TABLE bench.promotions IS
    'Journal des décisions sur l''encodage en service : promu, refusé, retour '
    'arrière. Ajout seul (déclencheurs). La lecture de référence est '
    'bench.v_encodage_en_service, jamais la table brute.';

-- L'ordre du journal est celui de l'insertion (promotion_id), pas l'horodatage :
-- deux lignes d'une même transaction partagent le même now().
CREATE VIEW bench.v_encodage_en_service AS
SELECT e.encodage_id, e.corpus_id, e.modele, e.revision, e.dimension,
       e.precision_calcul, e.prefixe_document, e.prefixe_requete, e.outil,
       e.outil_version, e.image_digest,
       p.promotion_id, p.decision, p.decide_le, p.decide_par
FROM (
    SELECT * FROM bench.promotions
    WHERE decision IN ('promu', 'retour_arriere')
    ORDER BY promotion_id DESC
    LIMIT 1
) p
JOIN bench.encodages e USING (encodage_id);

COMMENT ON VIEW bench.v_encodage_en_service IS
    'L''encodage que l''application lit : la dernière promotion ou le dernier '
    'retour arrière du journal. Zéro ou une ligne.';

-- Chaque ligne est contrôlée à l'insertion : un journal cohérent, pas seulement
-- un journal immuable.
CREATE FUNCTION bench.promotions_controler() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    en_service bigint;
BEGIN
    -- Deux décisions simultanées liraient le même « en service ».
    PERFORM pg_advisory_xact_lock(hashtext('bench.promotions'));
    SELECT encodage_id INTO en_service FROM bench.v_encodage_en_service;

    IF NOT EXISTS (SELECT 1 FROM bench.encodages
                   WHERE encodage_id = NEW.encodage_id AND termine_le IS NOT NULL) THEN
        RAISE EXCEPTION 'encodage % : non terminé, on n''en décide pas',
            NEW.encodage_id;
    END IF;
    IF NEW.encodage_precedent_id IS DISTINCT FROM en_service THEN
        RAISE EXCEPTION 'encodage précédent % : l''encodage en service est %',
            NEW.encodage_precedent_id, en_service;
    END IF;
    IF NEW.encodage_id = en_service THEN
        RAISE EXCEPTION 'encodage % : déjà en service', NEW.encodage_id;
    END IF;
    IF NEW.decision = 'retour_arriere' AND NOT EXISTS (
        SELECT 1 FROM bench.promotions
        WHERE encodage_id = NEW.encodage_id
          AND decision IN ('promu', 'retour_arriere')) THEN
        RAISE EXCEPTION 'retour arrière vers % : cet encodage n''a jamais été en service',
            NEW.encodage_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER promotions_controle
    BEFORE INSERT ON bench.promotions
    FOR EACH ROW EXECUTE FUNCTION bench.promotions_controler();
CREATE TRIGGER promotions_ajout_seul
    BEFORE UPDATE OR DELETE ON bench.promotions
    FOR EACH ROW EXECUTE FUNCTION bench.refuser_modification_journal();
CREATE TRIGGER promotions_sans_vidage
    BEFORE TRUNCATE ON bench.promotions
    FOR EACH STATEMENT EXECUTE FUNCTION bench.refuser_modification_journal();

-- --------------------------------------------------------------------------- --
--  6. L'état de départ, sur une base qui porte déjà le corpus (apimanga)
-- --------------------------------------------------------------------------- --
-- Sur une base neuve, rien de ce qui suit ne s'applique : le v1 reste ouvert,
-- le journal reste vide.

-- Le v1 est clos à son état actuel : 48 090 documents, 66 290 fragments.
UPDATE bench.corpus
SET clos_le = now(),
    nb_documents = (SELECT count(*) FROM bench.corpus_docs WHERE corpus_id = 'v1'),
    nb_fragments = (SELECT count(*) FROM bench.corpus_chunks WHERE corpus_id = 'v1')
WHERE corpus_id = 'v1'
  AND EXISTS (SELECT 1 FROM bench.corpus_docs);

-- L'encodage en service à l'issue de la migration : EmbeddingGemma sur le v1,
-- décision de clôture d'E2 (2026-10-07). Exactement un, sinon la migration échoue.
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM bench.encodages
    WHERE modele = 'google/embeddinggemma-300m' AND corpus_id = 'v1'
      AND termine_le IS NOT NULL;
    IF n > 1 THEN
        RAISE EXCEPTION '021 : % encodages EmbeddingGemma terminés sur v1, 1 attendu', n;
    END IF;
END;
$$;

INSERT INTO bench.promotions
    (decision, encodage_id, encodage_precedent_id, run_id, motif, decide_par)
SELECT 'promu', encodage_id, NULL, NULL,
       'Reprise de la décision de Max du 7 octobre 2026 (clôture d''E2) : '
       || 'EmbeddingGemma pour E3, sur le corpus v1. Inscrite à la création du '
       || 'journal (migration 021). Mesure de référence : la mesure 8 (31 '
       || 'questions sur 59).',
       'Max'
FROM bench.encodages
WHERE modele = 'google/embeddinggemma-300m' AND corpus_id = 'v1'
  AND termine_le IS NOT NULL;
