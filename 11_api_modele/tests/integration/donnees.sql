-- Jeu d'essai de l'intégration du Compose : un corpus clos (`v7`), son encodage
-- EmbeddingGemma vectorisé et promu — le minimum pour que l'API démarre et classe.
--
-- Chargé en `postgres` (le rôle de l'API ne peut pas écrire : c'est l'objet de la
-- migration 023), après les migrations. Les caractéristiques du modèle viennent de la
-- configuration du module 09 (`config/embeddinggemma.env`), passées en variables par
-- le service `donnees` : une seule source, celle que lisent aussi l'API et la doublure.
--
-- Le même jeu que la base de test du module (`tests/conftest.py`, `garnir`) : la série
-- 1 a une critique (fragments 701, 702) ; la série 2 n'est atteinte que par le synopsis
-- Kitsu 50, rattaché par le moyeu (703) ; l'œuvre Kitsu 60 est hors catalogue (704).
-- Un axe par fragment ; la doublure encode toute question sur l'axe du fragment 702.

\set ON_ERROR_STOP on

BEGIN;

INSERT INTO manga.ms_series_enriched (series_id, series_title)
VALUES (1, 'Série 1'), (2, 'Série 2');
INSERT INTO manga.work_identity (series_id, kitsu_id) VALUES (2, '50');

INSERT INTO bench.corpus (corpus_id, regle, chunking_id)
SELECT 'v7', 'essai d''intégration', chunking_id
  FROM bench.chunking_strategies WHERE name = 'char_1200_overlap_200';

INSERT INTO bench.corpus_docs (corpus_id, doc_key, source, series_id, kitsu_id, doc_text, title)
VALUES ('v7', 'ms_review:1', 'ms_review', 1, NULL, 'x', 'Série 1'),
       ('v7', 'kitsu:50', 'kitsu_synopsis', NULL, 50, 'x', 'Kitsu Cinquante'),
       ('v7', 'kitsu:60', 'kitsu_synopsis', NULL, 60, 'x', 'Kitsu Soixante');

INSERT INTO bench.corpus_chunks (corpus_id, chunk_id, doc_key, chunk_index, chunk_text)
VALUES ('v7', 701, 'ms_review:1', 0, 'Une critique, premier fragment.'),
       ('v7', 702, 'ms_review:1', 1, 'Une critique, second fragment.'),
       ('v7', 703, 'kitsu:50', 2, 'A synopsis attached to series two.'),
       ('v7', 704, 'kitsu:60', 3, 'A synopsis outside the catalogue.');

UPDATE bench.corpus SET clos_le = now(), nb_documents = 3, nb_fragments = 4
 WHERE corpus_id = 'v7';

INSERT INTO bench.encodages (corpus_id, modele, revision, dimension, precision_calcul,
       prefixe_document, prefixe_requete, outil, outil_version, image, image_digest,
       taille_lot, nb_fragments, termine_le)
VALUES ('v7', :'modele', :'revision', :dimension, :'precision',
        :'prefixe_document', :'prefixe_requete', 'text-embeddings-inference', '1.9.4',
        'doublure', 'sha256:' || repeat('0', 64), 16, 4, now());

-- Un vecteur unitaire par fragment : 1 sur son axe (701 → 1, 702 → 2…), 0 ailleurs.
INSERT INTO bench.vecteurs_embeddinggemma (chunk_id, encodage_id, corpus_id, embedding)
SELECT k.chunk_id, e.encodage_id, 'v7',
       ('[' || (SELECT string_agg(CASE WHEN i = k.chunk_id - 700 THEN '1' ELSE '0' END,
                                  ',' ORDER BY i)
                  FROM generate_series(1, :dimension) AS i) || ']')::vector
  FROM bench.corpus_chunks k
 CROSS JOIN (SELECT max(encodage_id) AS encodage_id FROM bench.encodages) e
 WHERE k.corpus_id = 'v7';

INSERT INTO bench.promotions (decision, encodage_id, encodage_precedent_id, motif, decide_par)
SELECT 'promu', (SELECT max(encodage_id) FROM bench.encodages),
       (SELECT encodage_id FROM bench.v_encodage_en_service),
       'essai d''intégration du Compose', 'essai';

COMMIT;
