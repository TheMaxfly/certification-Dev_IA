-- 001 — Socle d'identité multi-sources.
--
-- Contexte : aucune plateforme ne partage d'identifiant avec une autre. Le
-- rapprochement passe donc par un pivot d'identifiants et une cascade de
-- méthodes, dont les décisions sont journalisées et auditables.
--
-- Trois tables :
--   manga.work_identity   — une ligne par œuvre, porte les identifiants externes
--   manga.volume_identity — une ligne par volume, porte l'EAN-13
--   manga.match_decision  — journal APPEND-ONLY des décisions de rapprochement
--
-- Idempotence : les deux schémas existent déjà en base réelle, d'où les
-- IF NOT EXISTS. Cette migration ne touche à aucune table existante.

CREATE SCHEMA IF NOT EXISTS manga;
CREATE SCHEMA IF NOT EXISTS staging;

-- ---------------------------------------------------------------------------
-- manga.work_identity — l'œuvre et ses identifiants externes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manga.work_identity (
    work_uid      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- NULLABLE et volontairement sans FK : une œuvre peut exister sans fiche
    -- Manga Sanctuary (œuvres v2 venant du pivot, de Kitsu ou de la MADB).
    series_id     INTEGER NULL,
    wikidata_qid  TEXT NULL,
    kitsu_id      TEXT NULL,
    mal_id        TEXT NULL,
    anilist_id    TEXT NULL,
    -- Réservé v2 : Media Arts Database (Agence japonaise des affaires
    -- culturelles). La colonne existe pour éviter une migration de structure
    -- le jour où la source est branchée.
    madb_id       TEXT NULL,
    disponibilite TEXT NULL
        CHECK (disponibilite IN ('vf_disponible', 'vf_epuisee',
                                 'vo_seulement', 'non_licencie')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Index UNIQUE partiels : chaque identifiant externe est unique *quand il est
-- renseigné*. Un UNIQUE ordinaire l'imposerait aussi aux NULL multiples selon
-- les moteurs ; le partiel dit exactement ce qu'on veut et reste petit, la
-- plupart de ces colonnes étant peu remplies au départ.
CREATE UNIQUE INDEX IF NOT EXISTS work_identity_series_id_uniq
    ON manga.work_identity (series_id) WHERE series_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS work_identity_wikidata_qid_uniq
    ON manga.work_identity (wikidata_qid) WHERE wikidata_qid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS work_identity_kitsu_id_uniq
    ON manga.work_identity (kitsu_id) WHERE kitsu_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS work_identity_mal_id_uniq
    ON manga.work_identity (mal_id) WHERE mal_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS work_identity_anilist_id_uniq
    ON manga.work_identity (anilist_id) WHERE anilist_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS work_identity_madb_id_uniq
    ON manga.work_identity (madb_id) WHERE madb_id IS NOT NULL;

COMMENT ON TABLE manga.work_identity IS
    'Identité d''une œuvre et ses identifiants externes. series_id est nullable : '
    'une œuvre peut exister sans fiche Manga Sanctuary.';
COMMENT ON COLUMN manga.work_identity.madb_id IS
    'Media Arts Database — réservé v2, non alimenté.';
COMMENT ON COLUMN manga.work_identity.disponibilite IS
    'Disponibilité commerciale VF, pour le discours de recommandation.';

-- ---------------------------------------------------------------------------
-- manga.volume_identity — le volume et son EAN-13
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manga.volume_identity (
    -- L'URL du tome est l'identifiant naturel côté Manga Sanctuary : stable,
    -- déjà unique, et déjà la clé des exports du module 04.
    volume_url    TEXT PRIMARY KEY,
    work_uid      BIGINT NULL REFERENCES manga.work_identity (work_uid),
    -- CHAR(13) + CHECK : cadrage strict, chiffres uniquement. Le zéro de tête
    -- est significatif, d'où une chaîne et jamais un entier.
    isbn13        CHAR(13) NULL CHECK (isbn13 ~ '^[0-9]{13}$'),
    -- Résultat du contrôle de la clé EAN-13, calculé AU CHARGEMENT puis stocké.
    -- Le calcul n'a pas sa place en SQL : il appartient au pipeline, qui sait
    -- aussi quoi faire d'un EAN invalide (quarantaine plutôt que rejet).
    isbn13_valide BOOLEAN NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Non unique, volontairement : rééditions, coffrets et éditions collector
-- partagent légitimement un même EAN. C'est un axe de jointure, pas une clé.
CREATE INDEX IF NOT EXISTS volume_identity_isbn13_idx
    ON manga.volume_identity (isbn13) WHERE isbn13 IS NOT NULL;
CREATE INDEX IF NOT EXISTS volume_identity_work_uid_idx
    ON manga.volume_identity (work_uid);

COMMENT ON TABLE manga.volume_identity IS
    'Identité d''un volume. isbn13 n''est pas unique : rééditions et coffrets '
    'partagent un EAN.';
COMMENT ON COLUMN manga.volume_identity.isbn13_valide IS
    'Contrôle de la clé EAN-13, calculé au chargement (jamais en SQL).';

-- ---------------------------------------------------------------------------
-- manga.match_decision — journal des décisions de rapprochement
-- ---------------------------------------------------------------------------
-- APPEND-ONLY. On n'UPDATE ni ne DELETE jamais une ligne : une décision est un
-- fait daté. Se raviser, c'est insérer une nouvelle décision — l'historique
-- reste lisible et auditable (exigence de traçabilité du matching).
-- C'est une convention d'écriture tenue par le pipeline, volontairement pas un
-- trigger : la contrainte technique coûterait plus qu'elle ne rapporte ici.
CREATE TABLE IF NOT EXISTS manga.match_decision (
    decision_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    series_id    INTEGER NOT NULL,
    wikidata_qid TEXT NULL,
    -- Les étages de la cascade, du plus sûr au plus faible.
    method       TEXT NOT NULL
        CHECK (method IN ('kitsu_bridge', 'exact', 'exact_author',
                          'trgm', 'embedding', 'manual')),
    score        REAL NULL,
    -- 'auto' = décidé par le pipeline ; 'validated'/'rejected' = tranché par un
    -- humain ; 'needs_review' = mis en attente de revue.
    status       TEXT NOT NULL
        CHECK (status IN ('auto', 'validated', 'rejected', 'needs_review')),
    decided_at   timestamptz NOT NULL DEFAULT now(),
    decided_by   TEXT NOT NULL DEFAULT 'pipeline'
);

-- DESC : on lit presque toujours la décision la plus récente d'une série.
CREATE INDEX IF NOT EXISTS match_decision_series_decided_idx
    ON manga.match_decision (series_id, decided_at DESC);

COMMENT ON TABLE manga.match_decision IS
    'Journal APPEND-ONLY des décisions de rapprochement : ni UPDATE ni DELETE, '
    'se raviser = insérer une nouvelle décision.';

-- Vue de référence : la dernière décision par série. C'est CETTE lecture que la
-- cascade et les rapports doivent utiliser — jamais la table brute, qui
-- contient tout l'historique.
-- decision_id départage un éventuel ex æquo sur decided_at (deux décisions
-- insérées dans la même transaction partagent le now() transactionnel).
CREATE OR REPLACE VIEW manga.v_match_current AS
SELECT DISTINCT ON (series_id)
    series_id,
    wikidata_qid,
    method,
    score,
    status,
    decided_at,
    decided_by,
    decision_id
FROM manga.match_decision
ORDER BY series_id, decided_at DESC, decision_id DESC;

COMMENT ON VIEW manga.v_match_current IS
    'Dernière décision par series_id — lecture de référence de la cascade.';
