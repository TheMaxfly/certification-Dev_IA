-- Empreintes de contenu d'un corpus de `bench`, de ses vecteurs et des mesures.
--
--   psql -X -q -A -t -v corpus=v1 -f database/outils/empreintes_corpus.sql
--
-- Lecture seule. Sert aux contrôles « rien n'a bougé » : avant et après une
-- migration, avant et après la construction d'un autre corpus.
--
-- Colonnes NOMMÉES : une colonne ajoutée par une migration (`corpus_id`, 021) ne
-- change pas l'empreinte du contenu existant. Paramètres de session fixés : le
-- texte d'une ligne dépend du fuseau, du format des dates et des flottants.
-- Avant 021 (aucune colonne `corpus_id`), le corpus entier est le corpus v1.

\set ON_ERROR_STOP on
SET TimeZone = 'UTC';
SET DateStyle = 'ISO, YMD';
SET IntervalStyle = 'postgres';
SET extra_float_digits = 3;

SELECT EXISTS (
  SELECT 1 FROM information_schema.columns
  WHERE table_schema = 'bench' AND table_name = 'corpus_docs'
    AND column_name = 'corpus_id'
) AS a_corpus \gset

\if :a_corpus
SELECT 'docs', count(*), md5(string_agg(ROW(doc_key, source, series_id, kitsu_id,
  boost_score, doc_text, metadata_json, title)::text, E'\n' ORDER BY doc_key))
FROM bench.corpus_docs WHERE corpus_id = :'corpus';
SELECT 'fragments', count(*), md5(string_agg(ROW(chunk_id, doc_key, chunk_index,
  chunk_text, char_start, char_end, token_count, chunk_hash)::text, E'\n'
  ORDER BY chunk_id))
FROM bench.corpus_chunks WHERE corpus_id = :'corpus';
SELECT 'vecteurs_bge_m3', count(*), md5(string_agg(md5(ROW(chunk_id, encodage_id,
  modele, embedding)::text), '' ORDER BY encodage_id, chunk_id))
FROM bench.vecteurs_bge_m3
WHERE encodage_id IN (SELECT encodage_id FROM bench.encodages WHERE corpus_id = :'corpus');
SELECT 'vecteurs_embeddinggemma', count(*), md5(string_agg(md5(ROW(chunk_id,
  encodage_id, modele, embedding)::text), '' ORDER BY encodage_id, chunk_id))
FROM bench.vecteurs_embeddinggemma
WHERE encodage_id IN (SELECT encodage_id FROM bench.encodages WHERE corpus_id = :'corpus');
SELECT 'encodages', count(*), md5(string_agg(ROW(encodage_id, modele, revision,
  dimension, precision_calcul, prefixe_document, prefixe_requete, outil,
  outil_version, image, image_digest, taille_lot, nb_fragments, commence_le,
  termine_le)::text, E'\n' ORDER BY encodage_id))
FROM bench.encodages WHERE corpus_id = :'corpus';
\else
SELECT 'docs', count(*), md5(string_agg(ROW(doc_key, source, series_id, kitsu_id,
  boost_score, doc_text, metadata_json, title)::text, E'\n' ORDER BY doc_key))
FROM bench.corpus_docs;
SELECT 'fragments', count(*), md5(string_agg(ROW(chunk_id, doc_key, chunk_index,
  chunk_text, char_start, char_end, token_count, chunk_hash)::text, E'\n'
  ORDER BY chunk_id))
FROM bench.corpus_chunks;
SELECT 'vecteurs_bge_m3', count(*), md5(string_agg(md5(ROW(chunk_id, encodage_id,
  modele, embedding)::text), '' ORDER BY encodage_id, chunk_id))
FROM bench.vecteurs_bge_m3;
SELECT 'vecteurs_embeddinggemma', count(*), md5(string_agg(md5(ROW(chunk_id,
  encodage_id, modele, embedding)::text), '' ORDER BY encodage_id, chunk_id))
FROM bench.vecteurs_embeddinggemma;
SELECT 'encodages', count(*), md5(string_agg(ROW(encodage_id, modele, revision,
  dimension, precision_calcul, prefixe_document, prefixe_requete, outil,
  outil_version, image, image_digest, taille_lot, nb_fragments, commence_le,
  termine_le)::text, E'\n' ORDER BY encodage_id))
FROM bench.encodages;
\endif

-- Les mesures : immuables, toutes versions confondues.
SELECT 'eval_mesures', count(*), md5(string_agg(ROW(mesure_id, run_id, jeu_version,
  portee, perimetre, metrique, k, valeur, n_questions, mesure_le)::text, E'\n'
  ORDER BY mesure_id))
FROM bench.eval_mesures;
