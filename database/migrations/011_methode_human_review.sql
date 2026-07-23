-- 011 — La revue HUMAINE tracée : `human_review` au CHECK des méthodes.
--
-- POURQUOI CE FICHIER. Le run 2 de l'étage R lève le régime avis-seulement.
-- Deux écritures décisionnelles y coexistent : la PROMOTION des verdicts LLM
-- haute confiance (method='llm_review', déjà au CHECK depuis 009) et la
-- CORRECTION humaine des faux positifs du socle (série 1428 « Sister », faux
-- match kitsu_bridge vers Q1045285 « Chocotto Sister », confirmé humainement).
--
-- POURQUOI PAS 'manual'. La provenance doit rester LISIBLE au rapport de
-- couverture : le couple (llm_review, human_review) dit « revue LLM » vs
-- « revue humaine », cohérent avec l'étage R. Mutualiser la correction sur le
-- 'manual' générique effacerait cette distinction — on ne saurait plus si une
-- décision manuelle vient d'un arbitrage d'étage R ou d'un geste ad hoc.
--
-- ADDITIVE, exactement comme 009 : on rouvre le CHECK et on ajoute UNE valeur.
-- Les 9 366 décisions existantes restent valides (aucune n'utilise
-- human_review). Autoriser n'est pas employer : seul l'humain l'écrit, au run 2.

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
        'manual'               -- arbitrage manuel générique (hérité de 001)
    ));

COMMENT ON CONSTRAINT match_decision_method_check ON manga.match_decision IS
    'Contrat des méthodes de la cascade. 011 ajoute human_review (revue '
    'humaine tracée du run 2 de l''étage R), distincte du manual générique.';
