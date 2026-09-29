-- 018 — `kitsu_propagation` au CHECK des méthodes : l'étage qui propage le
-- kitsu_id d'une identité déjà décidée.
--
-- LE DÉFAUT QUE CET ÉTAGE CORRIGE (établi le 2026-09-30). L'étage 1 (Wikidata)
-- remplit wikidata_qid, mal_id et anilist_id — jamais kitsu_id. L'étage 2
-- (Kitsu) saute les séries déjà décidées. Une série identifiée par Wikidata ou
-- par arbitrage restait donc sans kitsu_id, même quand les correspondances
-- Kitsu menaient de son MAL / AniList à une entrée unique : One Piece, Naruto,
-- Death Note, Monster. 1 168 séries.
--
-- POURQUOI UNE MÉTHODE À SOI, ET PAS 'kitsu_bridge'. Le pont (étage 0) va de
-- Kitsu vers Wikidata et IDENTIFIE ; la propagation va de l'identité décidée
-- vers Kitsu et COMPLÈTE. Les ranger sous le même nom ferait passer 1 168
-- séries identifiées par Wikidata ou par le juge sous l'étiquette du pont, et
-- doublerait le « pont historique » du rapport de couverture. La décision de
-- propagation désigne sa décision source dans `details` : la provenance reste
-- lisible des deux côtés.
--
-- ADDITIVE, comme 009 et 011 : on rouvre le CHECK et on ajoute UNE valeur.
-- Les décisions existantes restent valides. Aucune table, aucune colonne,
-- aucune donnée.

ALTER TABLE manga.match_decision
    DROP CONSTRAINT IF EXISTS match_decision_method_check;

ALTER TABLE manga.match_decision
    ADD CONSTRAINT match_decision_method_check CHECK (method IN (
        'kitsu_bridge',        -- étage 0 : jointures d'identifiants pures
        'exact',               -- étage 1 : titre exact MS × Wikidata
        'exact_author',        -- étage 1 : départagé par l'auteur
        'exact_kitsu',         -- étage 2 : titre exact MS × Kitsu
        'exact_kitsu_author',  -- étage 2 : départagé par l'auteur
        'trgm',                -- étage 3 : similarité trigramme
        'embedding',           -- étage 3bis : similarité vectorielle (réservé)
        'llm_review',          -- étage R : juge LLM (promu au run 2)
        'human_review',        -- étage R : correction/arbitrage humain tracé
        'kitsu_propagation',   -- propagation du kitsu_id par identifiant
        'manual'               -- arbitrage manuel générique (hérité de 001)
    ));

COMMENT ON CONSTRAINT match_decision_method_check ON manga.match_decision IS
    'Contrat des méthodes de la cascade. 018 ajoute kitsu_propagation : le '
    'kitsu_id d''une identité déjà décidée, déduit de son MAL / AniList par '
    'les correspondances Kitsu.';
