-- Promotion staging.kitsu_mappings -> manga.kitsu_mappings.
--
-- Rejouée à chaque cycle, donc PAS une migration. Versionnée et exécutée par
-- `identity.charger_kitsu`, jamais collée à la main.
--
-- LES DEUX FILTRES, appliqués ICI et non au chargement — le staging enregistre
-- ce que la source dit, la promotion décide de ce qui entre :
--
--   1. externalSite. Seuls les sites de MANGA sont retenus :
--      myanimelist/manga, anilist/manga, mangaupdates. Les 89 autres mappings
--      pointent vers des anime, un personnage, une série TV (myanimelist/anime,
--      anilist/anime, animenewsnetwork, thetvdb, mydramalist, anidb,
--      myanimelist/character) : rapprocher une œuvre papier via l'id de son
--      adaptation animée serait un faux match, pas un match faible.
--
--   2. subtype. La jointure sur manga.kitsu_formes tient lieu de filtre : cette
--      table ne contient QUE la cible {manga, manhwa, manhua} — son CHECK le
--      garantit structurellement. C'est la traduction en SQL du « filtre par
--      jointure sur manga.ndjson du même run » : le ndjson n'est pas une table,
--      mais kitsu_formes en est la projection cible, chargée juste avant depuis
--      ce même fichier.
--
-- Ce que le filtre écarte est MESURÉ par le chargeur (staging complet - retenus)
-- plutôt que perdu : c'est la raison d'être du staging non filtré.
INSERT INTO manga.kitsu_mappings (kitsu_id, external_site, external_id)
SELECT DISTINCT
    s.kitsu_id::bigint,
    s.external_site,
    s.external_id
FROM staging.kitsu_mappings s
WHERE s.external_site IN ('myanimelist/manga', 'anilist/manga', 'mangaupdates')
  AND NULLIF(s.kitsu_id, '') IS NOT NULL
  AND NULLIF(s.external_id, '') IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM manga.kitsu_formes f
      WHERE f.kitsu_id = s.kitsu_id::bigint
  )
ON CONFLICT (kitsu_id, external_site, external_id) DO NOTHING;
