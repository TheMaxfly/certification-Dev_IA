-- Jeu d'essai du harnais d'intégration. RÉSERVÉ AUX TESTS : monté uniquement
-- par `compose.integration.yml`, jamais joué contre une base réelle.
--
-- Il s'applique APRÈS les 12 migrations de `database/migrations/` : le schéma
-- est celui de la production, ce fichier n'en crée aucun objet.
--
-- Les six tables peuplées sont exactement celles dont dépend la vue
-- `manga.rag_export_docs`, établies en remontant la chaîne
--   rag_export_docs → rag_docs_scored → rag_docs_all_v2
--                   → {rag_docs_all, rag_ms_hybrid_docs}
--                   → {ms_kitsu_map, rag_kitsu_docs, rag_reviews_docs}
-- plus les cibles des clés étrangères obligatoires : `kitsu_series_core`
-- (← rag_kitsu_docs), `ms_series_enriched` (← ms_kitsu_map, rag_reviews_docs)
-- et `ms_volumes_enriched` (← rag_reviews_docs).
--
-- Objectif : un document par source du corpus — `kitsu_synopsis`, `ms_hybrid`
-- et `ms_review` — soit 3 documents dans `rag_export_docs`.

BEGIN;

-- 1. Socle Kitsu — sert `/kitsu/{kitsu_id}` et porte la FK de rag_kitsu_docs.
INSERT INTO manga.kitsu_series_core (
  kitsu_id, slug, status, title_canonical, title_en, title_ja,
  synopsis_clean, rating_average_10, rating_rank, popularity_rank,
  tags_all_json
) VALUES (
  38, 'one-piece', 'current', 'One Piece', 'One Piece', 'ワンピース',
  'Gol D. Roger, roi des pirates, a cache son tresor a Raftel.',
  8.5, 2, 3,
  '["aventure", "pirates", "shounen"]'::jsonb
);

-- 2. Référentiel séries Manga Sanctuary — cible des FK de ms_kitsu_map et
--    rag_reviews_docs. Seul `series_id` est NOT NULL ; le reste documente.
INSERT INTO manga.ms_series_enriched (
  series_id, series_url, series_title, series_synopsis_enriched
) VALUES (
  736,
  'https://www.manga-sanctuary.com/bdd/manga/736-one-piece/',
  'One Piece',
  'Gol D. Roger, roi des pirates, a cache son tresor a Raftel.'
);

-- 3. Référentiel volumes — cible de rag_reviews_docs.volume_url.
INSERT INTO manga.ms_volumes_enriched (volume_url, series_id) VALUES (
  'https://www.manga-sanctuary.com/bdd/manga/736-one-piece/tome-1.html',
  736
);

-- 4. Document Kitsu du corpus RAG. Les positions hebdomadaires sont portées
--    par cette table (et non plus par kitsu_weekly_snapshot) : ce sont elles
--    que la formule de boost de production consomme.
--      trending_pos = 1, popular_pos = 2, top_pos = 4
INSERT INTO manga.rag_kitsu_docs (
  kitsu_id, doc_text, tags_all_json, trending_pos, popular_pos, top_pos
) VALUES (
  38,
  E'Titres: One Piece | One Piece | ワンピース\n'
  || E'Synopsis: Gol D. Roger, roi des pirates, a cache son tresor a Raftel.\n'
  || 'Tags: aventure, pirates, shounen',
  '["aventure", "pirates", "shounen"]'::jsonb,
  1, 2, 4
);

-- 5. Document de critique. `rag_ready` doit valoir TRUE : rag_docs_all et
--    rag_ms_hybrid_docs filtrent tous deux sur ce drapeau. `doc_id` est fixé
--    explicitement pour que la clé du document soit déterministe
--    (`ms_review:1`), le défaut étant une séquence.
--    Le mot « abordage » n'apparaît que dans cette critique : il permet au
--    smoke test de prouver que la source `ms_review` est bien indexée.
INSERT INTO manga.rag_reviews_docs (
  doc_id, volume_url, series_id, review_url, rag_text, rag_len, rag_ready
) VALUES (
  1,
  'https://www.manga-sanctuary.com/bdd/manga/736-one-piece/tome-1.html',
  736,
  'https://www.manga-sanctuary.com/critique/1-tome-1.html',
  'Avis de lecteur sur le tome 1 : un abordage lisible, un rythme qui tient.',
  72,
  TRUE
);

-- 6. Mapping MS ↔ Kitsu. Il conditionne le document hybride : la vue
--    rag_ms_hybrid_docs ne retient que `match_method = 'exact'` OU
--    `match_score >= 90`. Sans cette ligne, la source `ms_hybrid` est vide.
INSERT INTO manga.ms_kitsu_map (
  series_id, kitsu_id, match_method, match_score, ms_title, ms_title_norm
) VALUES (
  736, 38, 'exact', 100.0, 'One Piece', 'one piece'
);

COMMIT;
