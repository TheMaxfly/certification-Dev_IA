-- 009 — Ce qu'il manque pour l'étage 2 : le staff Kitsu, l'année Kitsu, et de
-- quoi journaliser une décision qui s'explique.
--
-- POURQUOI CE FICHIER EXISTE. L'étage 1 a rapproché MS × Wikidata et laissé
-- 665 dossiers en needs_review, dont 267 « sans signal » : titre exact, aucune
-- contradiction, simplement AUCUN confirmateur. Le pivot Wikidata ne porte
-- l'année qu'à 41,0 %, et ses auteurs ne sont hydratés que depuis 008. Kitsu,
-- lui, porte startDate à 99,9 % sur la cible et 53 183 lignes de staff dont
-- 100 % de noms résolvables (mesuré sur le run 20260714T152202Z, étape 0 de
-- l'étage 2). C'est le confirmateur qui manquait — et il est mécanique.
--
-- QUATRE ÉVOLUTIONS ADDITIVES :
--   1. staging.kitsu_staff + manga.kitsu_staff — les auteurs, côté Kitsu.
--   2. manga.kitsu_meta — l'année et le subtype, grain kitsu_id.
--   3. match_decision.method — élargissement du CHECK (3 valeurs).
--   4. match_decision.details — le contexte de décision, en JSONB.
--
-- Politique inchangée : additive, IF NOT EXISTS, aucune donnée chargée ici —
-- le chargement est le travail de identity/charger_kitsu_staff.

-- ---------------------------------------------------------------------------
-- 1. staging.kitsu_staff — l'atterrissage, tout-TEXT
-- ---------------------------------------------------------------------------
-- Même geste qu'en 002/006 : la zone d'atterrissage ne refuse rien et ne
-- filtre rien. Le filtre subtype (cible {manga, manhwa, manhua}) s'applique à
-- la PROMOTION, comme pour les formes et les mappings — filtrer ici perdrait
-- la mesure de ce qui a été écarté.
--
-- La structure suit le ndjson RÉEL, vérifié avant d'écrire (étape 0) :
--   data[]      -> type 'mediaStaff', attributes.role, relationships.person
--   included[]  -> type 'people', attributes.name
-- Le nom vit dans `included`, pas dans `data` : c'est une jointure INTERNE au
-- fichier, résolue par le chargeur. Les deux identifiants sont conservés ici
-- pour que le staging reste fidèle à la source et auditable ligne à ligne.
CREATE TABLE IF NOT EXISTS staging.kitsu_staff (
    kitsu_id    TEXT,
    personne_id TEXT,
    personne    TEXT,
    role        TEXT,
    staff_id    TEXT,
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    source_file TEXT
);

COMMENT ON TABLE staging.kitsu_staff IS
    'Atterrissage du staff Kitsu (data[] × included[] de staff.ndjson). '
    'Tout-TEXT, non filtré : le subtype est tranché à la promotion.';

-- ---------------------------------------------------------------------------
-- 2. manga.kitsu_staff — le côté auteur du rapprochement
-- ---------------------------------------------------------------------------
-- Grain (kitsu_id, personne, role). Le rôle est CONSERVÉ et non aplati : les
-- trois valeurs réelles du fichier sont 'Story & Art' (29 422), 'Story'
-- (12 095) et 'Art' (11 666) — toutes trois désignent un auteur au sens de la
-- cascade, mais les distinguer garde la porte ouverte à une pondération
-- ultérieure (un scénariste MS face à un 'Art' Kitsu n'est pas tout à fait la
-- même concordance qu'un face-à-face 'Story'/'Story').
--
-- LA RÈGLE QUI TIENT TOUT, identique à ms_formes/wd_formes/kitsu_formes :
-- personne_norm est calculée par la fonction Python `identity.normaliser()`,
-- des DEUX côtés du rapprochement. Une égalité entre deux colonnes normalisées
-- par deux implémentations différentes est un mensonge silencieux. Aucune
-- normalisation en SQL, jamais.
CREATE TABLE IF NOT EXISTS manga.kitsu_staff (
    staff_row_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kitsu_id      BIGINT NOT NULL,
    personne      TEXT NOT NULL,
    personne_norm TEXT NOT NULL,
    role          TEXT NULL,
    loaded_at     timestamptz NOT NULL DEFAULT now(),
    -- Le rôle fait partie de la clé : une même personne peut légitimement être
    -- créditée deux fois sur une œuvre (Story d'un côté, Art de l'autre).
    UNIQUE (kitsu_id, personne_norm, role)
);

-- Les deux axes de lecture de l'étage 2 : « quels auteurs pour ce kitsu_id ? »
-- et « ce nom normalisé est-il crédité sur ce kitsu_id ? ».
CREATE INDEX IF NOT EXISTS kitsu_staff_kitsu_id_idx
    ON manga.kitsu_staff (kitsu_id);
CREATE INDEX IF NOT EXISTS kitsu_staff_personne_norm_idx
    ON manga.kitsu_staff (personne_norm);
CREATE INDEX IF NOT EXISTS kitsu_staff_personne_norm_trgm_idx
    ON manga.kitsu_staff USING gin (personne_norm public.gin_trgm_ops);

COMMENT ON TABLE manga.kitsu_staff IS
    'Auteurs Kitsu (staff), cible {manga, manhwa, manhua}. personne_norm vient '
    'de identity.normaliser(), comme toutes les colonnes *_norm du schéma.';
COMMENT ON COLUMN manga.kitsu_staff.role IS
    'Rôle déclaré par la source : Story & Art | Story | Art. Conservé tel quel '
    '— l''aplatir interdirait toute pondération ultérieure.';

-- ---------------------------------------------------------------------------
-- 3. manga.kitsu_meta — l'année, à 99,9 %
-- ---------------------------------------------------------------------------
-- Grain kitsu_id, une ligne par entrée de la cible. Deux colonnes seulement :
-- l'étage 2 n'a besoin que du confirmateur année et du subtype.
--
-- POURQUOI UNE TABLE ET PAS UNE COLONNE SUR kitsu_formes : kitsu_formes a le
-- grain FORME (155 003 lignes pour 41 249 entrées). Y porter l'année la
-- répéterait ~3,8 fois et ouvrirait la porte à une incohérence interne. Le
-- grain de l'année est l'œuvre.
--
-- annee est un INTEGER extrait de data.attributes.startDate ('YYYY-MM-DD') par
-- le chargeur, pas un cast SQL : 44 entrées de la cible ont startDate NULL et
-- une porte une valeur hors plage — elles arrivent ici en NULL, honnêtement
-- vides plutôt que fausses.
CREATE TABLE IF NOT EXISTS manga.kitsu_meta (
    kitsu_id  BIGINT PRIMARY KEY,
    annee     INTEGER NULL,
    subtype   TEXT NOT NULL CHECK (subtype IN ('manga', 'manhwa', 'manhua')),
    loaded_at timestamptz NOT NULL DEFAULT now()
);

-- Partiel : un kitsu_id sans année n'a rien à faire dans l'index de l'axe année.
CREATE INDEX IF NOT EXISTS kitsu_meta_annee_idx
    ON manga.kitsu_meta (annee) WHERE annee IS NOT NULL;

COMMENT ON TABLE manga.kitsu_meta IS
    'Année (startDate) et subtype Kitsu, grain œuvre. Le CHECK sur subtype rend '
    'le filtre structurel : un novel ne peut pas y entrer.';
COMMENT ON COLUMN manga.kitsu_meta.annee IS
    'Année extraite de startDate en Python. NULL quand la source est vide ou '
    'hors plage (45 cas sur 41 249) — jamais une année inventée.';

-- ---------------------------------------------------------------------------
-- 4. match_decision.method — élargissement du CHECK
-- ---------------------------------------------------------------------------
-- Le CHECK de 001 est un CONTRAT : un étage ne s'autorise pas une méthode, il
-- la fait ajouter par migration. Trois valeurs entrent ici.
--
--   exact_kitsu        — l'étage 2 a conclu sans que l'auteur tranche
--                        (kitsu_id historique confirmé, ou année confirmatrice).
--   exact_kitsu_author — l'auteur a tranché, comme 'exact_author' côté Wikidata.
--   llm_review         — l'étage R (juge LLM), qui n'existe pas encore.
--
-- POURQUOI llm_review MAINTENANT, alors que l'étage R vient APRÈS l'étage 3.
-- Une migration au lieu de deux : la valeur est inerte tant qu'aucun code ne
-- l'écrit, et le CHECK est le seul objet que les deux étages partagent. Le
-- risque d'une valeur autorisée mais inutilisée est nul ; le coût d'une
-- migration 010 qui ne ferait que rouvrir ce même CHECK, lui, est réel.
-- ⚠️ Autoriser n'est pas employer : aucun verdict LLM ne devient 'auto'. Le
-- régime avis-seulement du run 1 reste une règle de CODE, pas de schéma.
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
        'embedding',           -- étage 3 : similarité vectorielle
        'llm_review',          -- étage R : juge LLM (avis, jamais 'auto')
        'manual'               -- arbitrage humain
    ));

-- ---------------------------------------------------------------------------
-- 5. match_decision.details — pourquoi cette décision, et pas une autre
-- ---------------------------------------------------------------------------
-- Le journal savait dire QUI a décidé (decided_by), COMMENT (method) et avec
-- quelle confiance (score) — mais pas POURQUOI. La case de matrice, elle,
-- vivait uniquement dans les CSV de rapport : la base ne portait ni le libellé
-- de cas ni les candidats. Un dossier ne pouvait donc pas être ré-instruit
-- depuis la seule base, ce que l'étage R exige.
--
-- JSONB et non des colonnes typées : le contenu diffère par étage (l'étage 2
-- journalise sa case, l'étage R y logera verdict/confiance/justification) et
-- figer maintenant les colonnes de l'étage R reviendrait à spécifier l'étage R
-- avant de l'avoir conçu.
--
-- NULLABLE, et les décisions des étages 0 et 1 restent SANS details. Le
-- journal est append-only : on ne remplit pas rétroactivement 3 523 lignes
-- pour faire joli. Une décision ancienne sans details dit la vérité — elle a
-- été prise quand la colonne n'existait pas.
ALTER TABLE manga.match_decision ADD COLUMN IF NOT EXISTS details JSONB NULL;

COMMENT ON COLUMN manga.match_decision.details IS
    'Contexte de la décision (JSONB). Étage 2 : {"case": "<libellé>"}. '
    'Étage R : verdict/confiance/justification. NULL sur les décisions des '
    'étages 0 et 1, prises avant l''existence de la colonne — jamais '
    'rétro-rempli : le journal est append-only.';
