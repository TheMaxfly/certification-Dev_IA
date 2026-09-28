-- Empreintes de contenu du schéma `bench`, une ligne par table (par source pour
-- le corpus). Sert à prouver qu'une restauration de l'archive rend exactement
-- l'état archivé.
--
-- À jouer avec des paramètres de session FIXES — `row::text` rend les
-- `timestamptz` dans le fuseau de la session, et les empreintes changeraient
-- d'une machine à l'autre sans que le contenu ait bougé :
--
--   PGOPTIONS='-c TimeZone=UTC -c DateStyle=ISO,YMD -c extra_float_digits=1' \
--     psql "$DSN" -X -f empreintes.sql

SELECT 'corpus_docs:' || source AS t, count(*) AS n,
       md5(string_agg(d::text, E'\n' ORDER BY d.doc_key)) AS empreinte
FROM bench.corpus_docs d GROUP BY source
UNION ALL
SELECT 'corpus_chunks:' || split_part(c.doc_key, ':', 1), count(*),
       md5(string_agg(c::text, E'\n' ORDER BY c.chunk_id))
FROM bench.corpus_chunks c GROUP BY split_part(c.doc_key, ':', 1)
UNION ALL
SELECT 'queries', count(*), md5(string_agg(q::text, E'\n' ORDER BY q.query_id))
FROM bench.queries q
UNION ALL
SELECT 'qrels', count(*), md5(string_agg(q::text, E'\n' ORDER BY q.query_id, q.doc_key))
FROM bench.qrels q
UNION ALL
SELECT 'retrieval_results', count(*),
       md5(string_agg(r::text, E'\n' ORDER BY r.run_id, r.query_id, r.rank))
FROM bench.retrieval_results r
UNION ALL
SELECT 'metrics', count(*),
       md5(string_agg(m::text, E'\n' ORDER BY m.run_id, m.metric_name))
FROM bench.metrics m
UNION ALL
SELECT 'embedding_runs', count(*), md5(string_agg(e::text, E'\n' ORDER BY e.run_id))
FROM bench.embedding_runs e
UNION ALL
SELECT 'faiss_indexes', count(*), md5(string_agg(f::text, E'\n' ORDER BY f.run_id))
FROM bench.faiss_indexes f
UNION ALL
SELECT 'chunking_strategies', count(*),
       md5(string_agg(s::text, E'\n' ORDER BY s.chunking_id))
FROM bench.chunking_strategies s
UNION ALL
SELECT 'embedding_models', count(*), md5(string_agg(m::text, E'\n' ORDER BY m.model_id))
FROM bench.embedding_models m
ORDER BY 1;
