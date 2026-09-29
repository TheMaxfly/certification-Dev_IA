-- Propagation du kitsu_id par identifiant — ÉCRITURE.
--
-- Joué par le pilote APRÈS le classement (propagation_kitsu.sql) et ses
-- points d'arrêt, dans la même transaction. Ne touche que les séries
-- 'rattachable'.

-- 1) JOURNAL — append-only, une décision par rattachement. La méthode nomme
--    l'étage (migration 018) ; details porte le motif, les identifiants source
--    et la décision source, pour que la provenance se lise depuis la seule
--    base. Le QID est celui de la décision source : v_match_current le garde.
INSERT INTO manga.match_decision
    (series_id, wikidata_qid, method, score, status, details)
SELECT series_id,
       wikidata_qid,
       'kitsu_propagation',
       1.0,
       'auto',
       jsonb_build_object(
           'case',            'propagation_identifiant',
           'kitsu_id',        kitsu_id,
           'via',             to_jsonb(via),
           'mal_id',          mal_id,
           'anilist_id',      anilist_id,
           'decision_source', decision_source,
           'methode_source',  methode_source)
FROM propagation_serie
WHERE cas = 'rattachable';

-- 2) IDENTITÉ — kitsu_id seul. Garde `kitsu_id IS NULL` : un kitsu_id déjà
--    renseigné n'est jamais réécrit, même si le classement se trompait.
UPDATE manga.work_identity w
SET kitsu_id   = p.kitsu_id::text,
    updated_at = now()
FROM propagation_serie p
WHERE p.cas = 'rattachable'
  AND w.series_id = p.series_id
  AND w.kitsu_id IS NULL;
