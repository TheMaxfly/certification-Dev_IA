-- Propagation du kitsu_id par identifiant — CLASSEMENT (aucune écriture).
--
-- LE DÉFAUT. L'étage 1 (Wikidata) remplit wikidata_qid, mal_id, anilist_id,
-- jamais kitsu_id ; l'étage 2 (Kitsu) saute les séries déjà décidées. Une série
-- identifiée par Wikidata ou par arbitrage restait sans kitsu_id alors que les
-- correspondances Kitsu menaient de son MAL / AniList à une entrée unique.
--
-- LA RÈGLE (2026-09-30) — pures jointures d'identifiants, AUCUNE lecture de
-- titre. Une série est rattachée si, cumulativement :
--   1. elle porte un mal_id ou un anilist_id issu d'une décision antérieure de
--      la cascade, automatique ou arbitrée (décision courante auto | validated) ;
--   2. les correspondances Kitsu (kitsu_mappings) mènent de ces identifiants à
--      EXACTEMENT UNE entrée Kitsu ;
--   3. cette entrée est de type manga, manhwa ou manhua (kitsu_meta) ;
--   4. cette entrée n'est rattachée à AUCUNE autre série ;
--   5. le kitsu_id de la série est vide.
-- L'égalité de titre n'est PAS une condition : le pilote la mesure et la
-- rapporte comme confirmation, jamais pour décider.
--
-- Les cas qui échouent sont classés, jamais tranchés :
--   sans_chemin                aucune entrée Kitsu par identifiant ;
--   conflit_plusieurs_entrees  point A — exclue et listée (décision 2026-09-30) ;
--   hors_type                  entrée ni manga, ni manhwa, ni manhua ;
--   conflit_deja_rattachee     point B — l'entrée est à une autre série : ARRÊT ;
--   conflit_entree_partagee    deux séries déduisent la même entrée : ARRÊT.
--
-- Sans paramètre : exécutable en un seul execute() psycopg. Les tables
-- temporaires vivent jusqu'à la fin de la transaction du pilote.

-- État d'avant : les kitsu_id déjà renseignés, pour prouver qu'aucun ne bouge.
CREATE TEMP TABLE propagation_avant ON COMMIT DROP AS
SELECT series_id, kitsu_id
FROM manga.work_identity
WHERE series_id IS NOT NULL AND kitsu_id IS NOT NULL;

-- 1) CANDIDATES — conditions 1 et 5. La décision source est la décision
--    courante : son QID est recopié dans la décision de propagation, sans quoi
--    v_match_current perdrait le QID de la série.
CREATE TEMP TABLE propagation_candidat ON COMMIT DROP AS
SELECT w.series_id,
       w.mal_id,
       w.anilist_id,
       v.wikidata_qid,
       v.decision_id AS decision_source,
       v.method      AS methode_source
FROM manga.work_identity w
JOIN manga.v_match_current v ON v.series_id = w.series_id
WHERE w.series_id IS NOT NULL
  AND w.kitsu_id IS NULL
  AND (w.mal_id IS NOT NULL OR w.anilist_id IS NOT NULL)
  AND v.status IN ('auto', 'validated');

-- 2) CHEMINS — identifiant externe → entrée Kitsu. Le site fait partie de la
--    clé : un external_id seul est ambigu entre sites.
CREATE TEMP TABLE propagation_chemin ON COMMIT DROP AS
SELECT c.series_id, 'mal'::text AS site, km.kitsu_id
FROM propagation_candidat c
JOIN manga.kitsu_mappings km
  ON km.external_site = 'myanimelist/manga' AND km.external_id = c.mal_id
UNION
SELECT c.series_id, 'anilist', km.kitsu_id
FROM propagation_candidat c
JOIN manga.kitsu_mappings km
  ON km.external_site = 'anilist/manga' AND km.external_id = c.anilist_id;

-- 3) CLASSEMENT — condition 2 compte TOUTES les entrées atteintes, quel que
--    soit leur type : deux fiches Kitsu pour une œuvre sont un conflit, pas un
--    choix à faire à la place de l'humain.
CREATE TEMP TABLE propagation_serie ON COMMIT DROP AS
SELECT c.series_id,
       c.mal_id,
       c.anilist_id,
       c.wikidata_qid,
       c.decision_source,
       c.methode_source,
       count(DISTINCT ch.kitsu_id)::integer AS n_entrees,
       coalesce(array_agg(DISTINCT ch.kitsu_id ORDER BY ch.kitsu_id)
                    FILTER (WHERE ch.kitsu_id IS NOT NULL),
                '{}')                         AS entrees,
       coalesce(array_agg(DISTINCT ch.site ORDER BY ch.site)
                    FILTER (WHERE ch.site IS NOT NULL),
                '{}')                         AS via,
       NULL::bigint                           AS kitsu_id,
       NULL::text                             AS subtype,
       NULL::text                             AS cas
FROM propagation_candidat c
LEFT JOIN propagation_chemin ch ON ch.series_id = c.series_id
GROUP BY c.series_id, c.mal_id, c.anilist_id, c.wikidata_qid,
         c.decision_source, c.methode_source;

UPDATE propagation_serie p
SET kitsu_id = p.entrees[1],
    subtype  = m.subtype
FROM manga.kitsu_meta m
WHERE p.n_entrees = 1 AND m.kitsu_id = p.entrees[1];

-- Une entrée unique sans ligne kitsu_meta : kitsu_id posé, type inconnu.
UPDATE propagation_serie
SET kitsu_id = entrees[1]
WHERE n_entrees = 1 AND kitsu_id IS NULL;

UPDATE propagation_serie p
SET cas = CASE
    WHEN p.n_entrees = 0 THEN 'sans_chemin'
    WHEN p.n_entrees > 1 THEN 'conflit_plusieurs_entrees'
    WHEN p.subtype IS NULL
      OR p.subtype NOT IN ('manga', 'manhwa', 'manhua') THEN 'hors_type'
    WHEN EXISTS (SELECT 1 FROM manga.work_identity w
                 WHERE w.kitsu_id = p.kitsu_id::text
                   AND w.series_id IS DISTINCT FROM p.series_id)
         THEN 'conflit_deja_rattachee'
    ELSE 'rattachable'
END;

-- Condition 4, entre candidates : une entrée déduite par deux séries n'est
-- rattachée à aucune — tout le groupe part en conflit, jamais résolu par ordre
-- d'arrivée.
UPDATE propagation_serie p
SET cas = 'conflit_entree_partagee'
WHERE p.cas = 'rattachable'
  AND p.kitsu_id IN (SELECT kitsu_id FROM propagation_serie
                     WHERE cas = 'rattachable'
                     GROUP BY kitsu_id HAVING count(*) > 1);
