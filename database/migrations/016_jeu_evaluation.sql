-- 016 — le jeu d'évaluation du bloc 2, au grain entité.
--
-- POURQUOI DES TABLES NEUVES PLUTÔT QU'ÉTENDRE `queries` / `qrels`
--
-- `bench.qrels` est au grain DOCUMENT : sa clé est `(query_id, doc_key)`, et
-- `doc_key` est une FK vers `corpus_docs` en ON DELETE CASCADE. Toute
-- reconstruction du corpus détruit donc les jugements — c'est ainsi que 154 des
-- 157 ont disparu le 2026-09-28. Un jeu qui doit comparer deux corpus ne peut
-- pas dépendre de l'un d'eux. Étendre `qrels` imposerait de retirer sa clé et sa
-- FK, c'est-à-dire de modifier les preuves du banc de décembre. Les tables
-- `eval_*` sont posées À CÔTÉ, sans aucun lien vers `corpus_docs` ; `queries` et
-- `qrels` restent l'historique de décembre.
--
-- LES DÉCISIONS QU'ELLE PORTE (point A, 2026-09-29)
--
--   E1  la réponse attendue est une série du catalogue, clé `series_id`, FK vers
--       `manga.ms_series_enriched` : la base fait elle-même le contrôle
--       d'existence. Une issue hors catalogue ne porte PAS de clé ;
--   E3  les CSV versionnés font foi, ces tables en sont la projection. Une
--       version gelée est IMMUABLE : ni UPDATE ni DELETE, ici par déclencheur ;
--   E4  trois modes — proposition, reconnaissance, refus (F7 seulement) ;
--   E5  le mode appartient à la question, pas à la famille ;
--   E7  trois issues — au_catalogue, reconnue_hors_catalogue (F1 ou F2, sans
--       clé), inconnue (F7 seulement, sans clé) ;
--   E9  les mesures se rangent par portée (global, mode, famille) et par
--       périmètre (toutes les questions, ou celles que le corpus peut
--       atteindre — E6), avec le K en colonne et SANS écrasement.
--
-- LES RÈGLES D'UNE QUESTION, TENUES PAR LA BASE
--
--   F7 ⇔ refus ⇔ inconnue                                   (CHECK)
--   reconnue_hors_catalogue ⇒ F1 ou F2, en reconnaissance   (CHECK)
--   origine 'decembre' ⇔ identifiant de décembre renseigné  (CHECK)
--   au_catalogue ⇒ au moins une série attendue ;
--   reconnaissance au catalogue ⇒ exactement une ;
--   autres issues ⇒ aucune                                  (déclencheur différé)
--
-- Le dernier groupe compte des lignes d'une autre table : un CHECK ne le peut
-- pas. Le déclencheur est DIFFÉRÉ à la validation, comme la FK de `014` — c'est
-- l'état validé qui doit être correct, pas l'ordre d'écriture d'une question et
-- de ses attendus.
--
-- CE QU'ELLE NE FAIT PAS
--
-- Aucune question, aucune donnée : le jeu est écrit à la main, puis projeté par
-- son chargeur au gel. Aucune métrique n'est calculée. Aucun droit n'est
-- accordé : `bench` reste hors du périmètre de `manga_ro` (012).

CREATE TABLE bench.eval_jeux (
    version      text PRIMARY KEY CHECK (version ~ '^v[0-9]+$'),
    empreinte    text NOT NULL CHECK (empreinte ~ '^[0-9a-f]{64}$'),
    gele_le      timestamptz NOT NULL,
    declaration  text NOT NULL CHECK (btrim(declaration) <> '')
);

COMMENT ON TABLE bench.eval_jeux IS
    'Une ligne par version gelée du jeu d''évaluation. empreinte = sha256 des CSV '
    'versionnés dont les tables eval_* sont la projection ; declaration = la '
    'déclaration datée de non-contamination (§8.5).';

CREATE TABLE bench.eval_questions (
    jeu_version       text NOT NULL REFERENCES bench.eval_jeux (version),
    question_id       text NOT NULL CHECK (question_id ~ '^Q[0-9]{3}$'),
    texte             text NOT NULL CHECK (btrim(texte) <> ''),
    mode              text NOT NULL
                      CHECK (mode IN ('proposition', 'reconnaissance', 'refus')),
    famille           text NOT NULL CHECK (famille ~ '^F([1-9]|10)$'),
    issue_attendue    text NOT NULL
                      CHECK (issue_attendue IN
                             ('au_catalogue', 'reconnue_hors_catalogue', 'inconnue')),
    origine           text NOT NULL CHECK (origine IN ('nouvelle', 'decembre')),
    origine_query_id  bigint,
    note              text NOT NULL CHECK (btrim(note) <> ''),
    PRIMARY KEY (jeu_version, question_id),
    CONSTRAINT eval_questions_f7_refus_inconnue
        CHECK ((famille = 'F7') = (mode = 'refus')
               AND (mode = 'refus') = (issue_attendue = 'inconnue')),
    CONSTRAINT eval_questions_hors_catalogue
        CHECK (issue_attendue <> 'reconnue_hors_catalogue'
               OR (famille IN ('F1', 'F2') AND mode = 'reconnaissance')),
    CONSTRAINT eval_questions_origine
        CHECK ((origine = 'decembre') = (origine_query_id IS NOT NULL))
);

COMMENT ON COLUMN bench.eval_questions.question_id IS
    'Q001, Q002… — indépendant de la famille : une question reclassée (§8.2) '
    'garde son identifiant d''une version à l''autre.';
COMMENT ON COLUMN bench.eval_questions.origine_query_id IS
    'bench.queries.query_id de décembre, si la question en est reprise. Pas de '
    'FK : les requêtes de décembre sont un historique, pas une dépendance.';

CREATE TABLE bench.eval_attendus (
    jeu_version  text NOT NULL,
    question_id  text NOT NULL,
    series_id    bigint NOT NULL REFERENCES manga.ms_series_enriched (series_id),
    grade        smallint NOT NULL CHECK (grade IN (1, 2)),
    PRIMARY KEY (jeu_version, question_id, series_id),
    FOREIGN KEY (jeu_version, question_id)
        REFERENCES bench.eval_questions (jeu_version, question_id)
);

COMMENT ON COLUMN bench.eval_attendus.grade IS
    '1 = pertinent, 2 = très pertinent (gains du nDCG des questions de proposition).';

CREATE TABLE bench.eval_mesures (
    mesure_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id       uuid NOT NULL,
    jeu_version  text NOT NULL REFERENCES bench.eval_jeux (version),
    portee       text NOT NULL
                 CHECK (portee ~ '^(global|mode:(proposition|reconnaissance|refus)|famille:F([1-9]|10))$'),
    perimetre    text NOT NULL CHECK (perimetre IN ('toutes', 'atteignables')),
    metrique     text NOT NULL
                 CHECK (metrique IN ('hit_rate', 'recall', 'mrr', 'ndcg',
                                     'taux_refus_correct', 'taux_refus_a_tort')),
    k            integer CHECK (k > 0),
    valeur       double precision NOT NULL,
    n_questions  integer NOT NULL CHECK (n_questions >= 0),
    mesure_le    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT eval_mesures_k
        CHECK ((metrique IN ('hit_rate', 'recall', 'mrr', 'ndcg')) = (k IS NOT NULL)),
    CONSTRAINT eval_mesures_unique
        UNIQUE NULLS NOT DISTINCT (run_id, jeu_version, portee, perimetre, metrique, k)
);

COMMENT ON TABLE bench.eval_mesures IS
    'Une mesure = un run, une version du jeu, une portée, un périmètre, une '
    'métrique et son K. Le K est une colonne, jamais un suffixe de nom ; une '
    'mesure en double ÉCHOUE au lieu d''écraser (le défaut ON CONFLICT de '
    'décembre). run_id sans FK : un bras lexical n''a pas de modèle d''embedding, '
    'et embedding_runs en exige un.';

-- --------------------------------------------------------------------------- --
--  Nombre de séries attendues par question — contrôlé à la validation
-- --------------------------------------------------------------------------- --

CREATE FUNCTION bench.eval_controler_attendus() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_version  text;
    v_question text;
    q          record;
    n          integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_version := OLD.jeu_version;
        v_question := OLD.question_id;
    ELSE
        v_version := NEW.jeu_version;
        v_question := NEW.question_id;
    END IF;

    SELECT * INTO q FROM bench.eval_questions
     WHERE jeu_version = v_version AND question_id = v_question;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    SELECT count(*) INTO n FROM bench.eval_attendus
     WHERE jeu_version = v_version AND question_id = v_question;

    IF q.issue_attendue = 'au_catalogue' AND n = 0 THEN
        RAISE EXCEPTION '% / % : issue au_catalogue sans série attendue',
            v_version, v_question;
    END IF;
    IF q.issue_attendue = 'au_catalogue' AND q.mode = 'reconnaissance' AND n <> 1 THEN
        RAISE EXCEPTION '% / % : reconnaissance au catalogue avec % séries attendues (1 exigée)',
            v_version, v_question, n;
    END IF;
    IF q.issue_attendue <> 'au_catalogue' AND n <> 0 THEN
        RAISE EXCEPTION '% / % : issue % avec % séries attendues (aucune admise)',
            v_version, v_question, q.issue_attendue, n;
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER eval_questions_attendus
    AFTER INSERT OR UPDATE ON bench.eval_questions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION bench.eval_controler_attendus();

CREATE CONSTRAINT TRIGGER eval_attendus_nombre
    AFTER INSERT OR UPDATE OR DELETE ON bench.eval_attendus
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION bench.eval_controler_attendus();

-- --------------------------------------------------------------------------- --
--  Une version gelée est immuable
-- --------------------------------------------------------------------------- --

CREATE FUNCTION bench.eval_refuser_modification() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '%.% : un jeu gelé est immuable — publier une nouvelle version',
        TG_TABLE_SCHEMA, TG_TABLE_NAME;
END;
$$;

CREATE TRIGGER eval_jeux_immuable
    BEFORE UPDATE OR DELETE ON bench.eval_jeux
    FOR EACH ROW EXECUTE FUNCTION bench.eval_refuser_modification();
CREATE TRIGGER eval_questions_immuable
    BEFORE UPDATE OR DELETE ON bench.eval_questions
    FOR EACH ROW EXECUTE FUNCTION bench.eval_refuser_modification();
CREATE TRIGGER eval_attendus_immuable
    BEFORE UPDATE OR DELETE ON bench.eval_attendus
    FOR EACH ROW EXECUTE FUNCTION bench.eval_refuser_modification();
CREATE TRIGGER eval_mesures_immuable
    BEFORE UPDATE OR DELETE ON bench.eval_mesures
    FOR EACH ROW EXECUTE FUNCTION bench.eval_refuser_modification();
