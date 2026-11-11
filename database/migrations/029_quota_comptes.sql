-- 029 — le compteur de questions par compte et par jour (E4, lot 2b).
--
-- POURQUOI
--
-- Un compte client est borné à un nombre de questions au conseiller par jour civil UTC
-- (décision 4 bis du lot 2b, Max, 8 novembre 2026) : le quota suit le compte, pas le
-- cookie — un client qui se reconnecte depuis un autre navigateur garde son compteur.
-- La remise à zéro est le changement de jour : une ligne par (compte, jour), jamais
-- réécrite d'un jour à l'autre. Libraires et administrateurs n'ont pas de quota ; ils
-- n'écrivent pas ici. Même convention de jour que `couts_jour` (027) : l'UTC, passé
-- par l'application.
--
-- AUCUN TEXTE : un compteur. La ligne part avec le compte (`ON DELETE CASCADE`) : ce
-- sont des données du compte, supprimées avec lui (registre des traitements).
--
-- DROITS de `app_rw` : SELECT, INSERT, UPDATE — pas de DELETE (la suppression d'un
-- compte emporte ses compteurs par la cascade, sous le propriétaire). Objet par objet,
-- comme la 027 : aucun `ON ALL TABLES`.
--
-- Appliquée en préproduction au lot 2b, avec la 028 ; en production au lot de
-- livraison continue (`verification/migrations_en_attente.tsv`).

CREATE TABLE application.questions_jour (
    compte_id bigint  NOT NULL REFERENCES application.comptes (id) ON DELETE CASCADE,
    jour      date    NOT NULL,
    questions integer NOT NULL DEFAULT 0,
    PRIMARY KEY (compte_id, jour),
    CONSTRAINT questions_jour_positif CHECK (questions >= 0)
);

COMMENT ON TABLE application.questions_jour IS
    'Questions posées au conseiller par compte et par jour civil UTC ; porte le quota des '
    'comptes clients. Aucun texte : un compteur.';

GRANT SELECT, INSERT, UPDATE ON application.questions_jour TO app_rw;
