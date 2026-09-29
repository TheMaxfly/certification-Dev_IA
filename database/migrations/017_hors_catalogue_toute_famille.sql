-- 017 — « reconnue hors catalogue » : toute famille, en reconnaissance.
--
-- CE QUI CHANGE, ET POURQUOI
--
-- 016 réservait l'issue `reconnue_hors_catalogue` aux familles F1 et F2
-- (décision E7 du 2026-09-29). La contrainte était trop étroite : un titre en
-- romaji d'une œuvre jamais éditée en France est le cas F5 par excellence, et
-- un personnage d'une œuvre non éditée relève de F6. Ce qui caractérise
-- l'issue n'est pas la famille — c'est qu'il n'y a qu'une bonne réponse à
-- reconnaître, et qu'elle n'a pas de clé au catalogue.
--
-- Règle nouvelle : reconnue_hors_catalogue ⇒ mode = reconnaissance, quelle que
-- soit la famille. Un CHECK remplacé, aucun schéma touché, aucune donnée.
--
-- Les autres règles de 016 restent : F7 ⇔ refus ⇔ inconnue ; origine ; nombre
-- de séries attendues par issue et par mode (déclencheur différé) ;
-- immuabilité d'une version gelée.

ALTER TABLE bench.eval_questions
    DROP CONSTRAINT eval_questions_hors_catalogue;

ALTER TABLE bench.eval_questions
    ADD CONSTRAINT eval_questions_hors_catalogue
        CHECK (issue_attendue <> 'reconnue_hors_catalogue'
               OR mode = 'reconnaissance');
