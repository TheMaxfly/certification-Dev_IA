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
) VALUES
  (736,
   'https://www.manga-sanctuary.com/bdd/manga/736-one-piece/',
   'One Piece',
   'Gol D. Roger, roi des pirates, a cache son tresor a Raftel.'),
  (8514,
   'https://www.manga-sanctuary.com/bdd/manga/8514-serie-b/',
   'Serie B',
   NULL),
  (9999,
   'https://www.manga-sanctuary.com/bdd/manga/9999-serie-c/',
   'Serie C',
   NULL);

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

-- 7. Critiques : les DEUX tables, avec des comptes DIFFÉRENTS.
--
--    C'est le cœur de la fixture pour 3b. `ms_reviews_all` est le référentiel
--    complet (11 074 lignes en production), `ms_reviews` le corpus RAG
--    historique (3 187). Elles portent les mêmes colonnes : servir l'une pour
--    l'autre ne produit aucune erreur, seulement deux tiers de données en
--    moins. La fixture pose donc 5 lignes d'un côté, 1 de l'autre, dont 3
--    contre 1 pour la série 736 : un endpoint qui se tromperait de table
--    renverrait 1 au lieu de 3, et le smoke test le verrait. Deux séries
--    supplémentaires exercent l'alignement de review_url sur trois séries.
INSERT INTO manga.ms_reviews_all (
  series_id, volume_number, volume_url, review_url, review_title,
  review_score, review_author, review_date_iso, review_date_raw, review_body
) VALUES
  (736, 1, 'https://www.manga-sanctuary.com/bdd/manga/736-one-piece/tome-1.html',
   'https://www.manga-sanctuary.com/critique/1-tome-1.html', 'Un abordage lisible',
   9.0, 'lecteur_a', DATE '2024-03-01', '1 mars 2024',
   'Avis de lecteur sur le tome 1 : un abordage lisible, un rythme qui tient.'),
  (736, 2, NULL, 'https://www.manga-sanctuary.com/critique/2-tome-2.html',
   'Le souffle tient', 8.0, 'lecteur_b', NULL, 'jeu.',
   'Deuxieme tome, le souffle tient.'),
  (736, NULL, NULL, 'https://www.manga-sanctuary.com/critique/3-serie.html',
   'Sur la serie entiere', 7.5, 'lecteur_c', DATE '2024-05-10', '10 mai 2024',
   'Un avis qui porte sur la serie et non sur un tome.'),
  (8514, NULL, NULL, 'https://www.manga-sanctuary.com/critique/4-serie.html',
   'Critique serie B', 7.0, 'lecteur_d', DATE '2024-06-01', '1 juin 2024',
   'Un avis sur la serie B.'),
  (9999, NULL, NULL, 'https://www.manga-sanctuary.com/critique/5-serie.html',
   'Critique serie C', 6.5, 'lecteur_e', DATE '2024-07-01', '1 juillet 2024',
   'Un avis sur la serie C.');

-- Le corpus RAG hérité : UNE seule des trois critiques de la série 736. Le
-- filtre qui a produit cette table en production n'est pas rejoué ici — seul
-- l'écart de volume compte pour le contrôle.
INSERT INTO manga.ms_reviews (
  series_id, volume_url, review_url, review_title, review_score, review_body
) VALUES (
  736, 'https://www.manga-sanctuary.com/bdd/manga/736-one-piece/tome-1.html',
  'https://www.manga-sanctuary.com/critique/1-tome-1.html', 'Un abordage lisible',
  9.0, 'Avis de lecteur sur le tome 1 : un abordage lisible, un rythme qui tient.'
);

-- 8. Une série SANS aucune critique — le témoin du 200-liste-vide.
--    Sans elle, rien ne distinguerait « série inconnue » de « série sans
--    critique », et c'est la confusion classique de ce genre d'API.
INSERT INTO manga.ms_series_enriched (series_id, series_title) VALUES
  (999, 'Serie sans critique');

-- 9. Identité : le moyeu, et une décision courante.
--    `work_identity` porte les identifiants croisés ; `v_match_current` (vue
--    sur match_decision) porte la provenance du lien. La route `/identity`
--    joint les deux, car la vue est indexée par `series_id`, pas par
--    `work_uid`.
INSERT INTO manga.work_identity (work_uid, series_id, wikidata_qid, kitsu_id)
  OVERRIDING SYSTEM VALUE
VALUES (4242, 736, 'Q173065', '38');

UPDATE manga.ms_series_enriched SET work_uid = 4242 WHERE series_id = 736;

INSERT INTO manga.match_decision (
  series_id, wikidata_qid, method, score, status, decided_by
) VALUES (736, 'Q173065', 'exact', 1.0, 'auto', 'fixture');

COMMIT;
