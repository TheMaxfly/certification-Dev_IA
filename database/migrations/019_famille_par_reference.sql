-- 019 — F11 « par référence » : une onzième famille au jeu d'évaluation.
--
-- CE QUI CHANGE, ET POURQUOI (décision de Max, 2026-09-30)
--
-- « Un manga comme Takagi, collège, taquineries, pas de violence » n'est pas
-- F8. Le mécanisme n'est pas la combinaison de signaux faibles : c'est la
-- SIMILARITÉ À UN TITRE CONNU — on retrouve la référence, puis ce qui lui
-- ressemble, et la référence elle-même n'est pas une réponse. C'est
-- probablement la question la plus fréquente au comptoir ; elle a sa famille.
-- Les cinq premières viennent des requêtes de décembre (origine `decembre`).
--
-- Deux CHECK de 016 bornaient la famille à F1–F10 : celui de la question, et
-- celui de la portée d'une mesure (`famille:Fn`). Les deux s'ouvrent à F11 ;
-- aucune table, aucune colonne, aucune donnée.

ALTER TABLE bench.eval_questions
    DROP CONSTRAINT eval_questions_famille_check;

ALTER TABLE bench.eval_questions
    ADD CONSTRAINT eval_questions_famille_check
        CHECK (famille ~ '^F([1-9]|1[01])$');

ALTER TABLE bench.eval_mesures
    DROP CONSTRAINT eval_mesures_portee_check;

ALTER TABLE bench.eval_mesures
    ADD CONSTRAINT eval_mesures_portee_check
        CHECK (portee ~ '^(global|mode:(proposition|reconnaissance|refus)|famille:F([1-9]|1[01]))$');
