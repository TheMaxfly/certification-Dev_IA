-- 027 — le schéma `application` (E4) et son rôle d'écriture `app_rw`.
--
-- POURQUOI UN SCHÉMA À PART
--
-- L'application du libraire (module 12) garde ses comptes, ses critiques, ses listes,
-- son journal, ses coûts et ses réglages. Ce sont ses données, pas le catalogue : elles
-- vivent dans un schéma à elle, avec un rôle qui n'écrit que là. TOUTES les tables
-- d'E4 sont créées ici, en une migration, pour ne pas repasser par la production à
-- chaque lot (E4, lot 1, décision 1 ; la 028, recherche tolérante, est au lot 2).
--
-- Même construction que 012, 022 et 023 :
--   * `app_rw`, rôle de GROUPE, `NOLOGIN`, sans secret : versionné ici ;
--   * le rôle de CONNEXION `app_conseiller`, qui porte un secret, est créé hors
--     migration par `database/outils/creer_role_application.sh`.
--
-- LE RÔLE DÉJÀ PRÉSENT. Un rôle est un objet du serveur, pas de la base : `apimanga`
-- et `apimanga_preprod` sont sur le même serveur, et le harnais rejoue les migrations
-- dans un conteneur où les rôles existent déjà. Absent, `app_rw` est créé (et son
-- commentaire posé) ; présent, ses attributs sont VÉRIFIÉS — un écart arrête la
-- migration en le nommant — et le rôle n'est pas touché : ni `ALTER ROLE`, ni
-- `COMMENT ON ROLE` (décision de Max, 8 novembre 2026). Seuls ses droits sur le
-- schéma neuf lui sont accordés.
--
-- PÉRIMÈTRE de `app_rw` — objet par objet ; aucun `ON ALL TABLES`, aucun
-- `ALTER DEFAULT PRIVILEGES` : une table créée plus tard n'est accessible que si une
-- migration l'accorde.
--   schéma application : USAGE ;
--   comptes, listes : SELECT, INSERT, UPDATE, DELETE ;
--   critiques : SELECT, INSERT, UPDATE — pas de DELETE (un retrait est un statut) ;
--   journal_actions : SELECT, INSERT — ni UPDATE ni DELETE (ajout seul, garanti par
--     les droits) ;
--   couts_jour, reglages : SELECT, INSERT, UPDATE ;
--   séquences des colonnes d'identité : rien (l'insertion n'en demande aucun droit,
--     éprouvé par les tests) ;
--   `CONNECT` sur la base courante, comme 012, 022 et 023 ;
--   manga, bench, staging, public : RIEN, pas même en lecture.
--
-- AUCUNE CLÉ ÉTRANGÈRE VERS LE CATALOGUE. `series_id` est l'identifiant du catalogue
-- (`manga.ms_series_enriched.series_id`), validé par l'API data du module 02 au
-- moment de l'écriture : le schéma `application` ne dépend pas des instantanés du
-- catalogue, qui se rechargent.

-- --------------------------------------------------------------------------- --
--  Le rôle de groupe : créé s'il manque ; vérifié, jamais modifié, s'il existe
-- --------------------------------------------------------------------------- --
DO $$
DECLARE
    connexion boolean;
    super boolean;
    bases boolean;
    roles boolean;
    replication boolean;
    contournement boolean;
    groupes text;
BEGIN
    SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication,
           rolbypassrls
      INTO connexion, super, bases, roles, replication, contournement
      FROM pg_roles WHERE rolname = 'app_rw';
    IF NOT FOUND THEN
        CREATE ROLE app_rw NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
            NOBYPASSRLS;
        COMMENT ON ROLE app_rw IS
            'Écriture de l''application du libraire (module 12) : le schéma application '
            'et rien d''autre, pas même en lecture. Rôle de groupe, NOLOGIN. Droits posés '
            'par la migration 027 ; le rôle de connexion app_conseiller en hérite.';
        RETURN;
    END IF;
    IF connexion THEN
        RAISE EXCEPTION 'migration 027 : le rôle app_rw existe déjà avec LOGIN '
            '(attendu : NOLOGIN) ; rien n''est modifié';
    END IF;
    IF super THEN
        RAISE EXCEPTION 'migration 027 : le rôle app_rw existe déjà avec SUPERUSER '
            '(attendu : NOSUPERUSER) ; rien n''est modifié';
    END IF;
    IF bases THEN
        RAISE EXCEPTION 'migration 027 : le rôle app_rw existe déjà avec CREATEDB '
            '(attendu : NOCREATEDB) ; rien n''est modifié';
    END IF;
    IF roles THEN
        RAISE EXCEPTION 'migration 027 : le rôle app_rw existe déjà avec CREATEROLE '
            '(attendu : NOCREATEROLE) ; rien n''est modifié';
    END IF;
    IF replication THEN
        RAISE EXCEPTION 'migration 027 : le rôle app_rw existe déjà avec REPLICATION '
            '(attendu : NOREPLICATION) ; rien n''est modifié';
    END IF;
    IF contournement THEN
        RAISE EXCEPTION 'migration 027 : le rôle app_rw existe déjà avec BYPASSRLS '
            '(attendu : NOBYPASSRLS) ; rien n''est modifié';
    END IF;
    SELECT string_agg(g.rolname, ', ' ORDER BY g.rolname)
      INTO groupes
      FROM pg_auth_members m
      JOIN pg_roles g ON g.oid = m.roleid
      JOIN pg_roles r ON r.oid = m.member
     WHERE r.rolname = 'app_rw';
    IF groupes IS NOT NULL THEN
        RAISE EXCEPTION 'migration 027 : le rôle app_rw existe déjà et est membre de % '
            '(attendu : membre d''aucun rôle) ; rien n''est modifié', groupes;
    END IF;
END
$$;

-- `current_database()` : la migration s'applique aussi aux bases jetables des tests.
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO app_rw', current_database());
END
$$;

-- --------------------------------------------------------------------------- --
--  Le schéma
-- --------------------------------------------------------------------------- --
CREATE SCHEMA application;
REVOKE ALL ON SCHEMA application FROM PUBLIC;

COMMENT ON SCHEMA application IS
    'Les données de l''application du libraire (module 12) : comptes, critiques, '
    'listes, journal des actions, coûts du modèle de langage, réglages. Écrit par '
    'app_rw seul (migration 027).';

-- Les comptes : clients (auto-inscrits), libraires et administrateurs (créés par un
-- administrateur). Aucun courriel. `hachage` : l'empreinte Argon2id, jamais le mot de
-- passe. `generation` : incrémentée quand le mot de passe change ou que le compte est
-- désactivé — toute session d'une autre génération est refusée.
CREATE TABLE application.comptes (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pseudo           text        NOT NULL,
    nom_affiche      text        NOT NULL,
    role             text        NOT NULL,
    hachage          text        NOT NULL,
    actif            boolean     NOT NULL DEFAULT true,
    generation       integer     NOT NULL DEFAULT 0,
    doit_changer_mdp boolean     NOT NULL DEFAULT false,
    echecs_connexion integer     NOT NULL DEFAULT 0,
    verrou_jusqu_a   timestamptz,
    cree_le          timestamptz NOT NULL DEFAULT now(),
    modifie_le       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT comptes_pseudo_unique UNIQUE (pseudo),
    CONSTRAINT comptes_pseudo_forme CHECK (pseudo ~ '^[a-z0-9_-]{3,30}$'),
    CONSTRAINT comptes_nom_affiche_longueur
        CHECK (char_length(nom_affiche) BETWEEN 1 AND 60),
    CONSTRAINT comptes_role_connu CHECK (role IN ('client', 'libraire', 'admin')),
    CONSTRAINT comptes_generation_positive CHECK (generation >= 0),
    CONSTRAINT comptes_echecs_positifs CHECK (echecs_connexion >= 0)
);

COMMENT ON TABLE application.comptes IS
    'Comptes de l''application : pseudo unique, empreinte Argon2id, rôle, état. Aucun '
    'courriel. Un compte libraire ne se supprime pas (ses critiques le retiennent).';

-- Les critiques des libraires, sur une série ou sur un tome. C'est la « table
-- distincte » que lira, chaque mois, la construction du corpus (cycle des critiques
-- de libraires) : une critique publiée entre dans la recherche à la promotion
-- suivante. Un retrait est un statut, jamais une suppression (pas de DELETE pour
-- app_rw ; ON DELETE RESTRICT : un compte qui a des critiques ne se supprime pas).
CREATE TABLE application.critiques (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    compte_id      bigint      NOT NULL
        REFERENCES application.comptes (id) ON DELETE RESTRICT,
    series_id      integer     NOT NULL,
    volume_url     text,
    volume_libelle text,
    texte          text        NOT NULL,
    note           smallint    NOT NULL,
    label          text,
    statut         text        NOT NULL DEFAULT 'publiee',
    publie_le      timestamptz NOT NULL DEFAULT now(),
    modifie_le     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT critiques_tome_complet
        CHECK ((volume_url IS NULL) = (volume_libelle IS NULL)),
    CONSTRAINT critiques_texte_longueur CHECK (char_length(texte) BETWEEN 1 AND 10000),
    CONSTRAINT critiques_note_sur_dix CHECK (note BETWEEN 0 AND 10),
    CONSTRAINT critiques_label_forme CHECK (label ~ '^[a-z_]{1,40}$'),
    CONSTRAINT critiques_statut_connu CHECK (statut IN ('publiee', 'retiree'))
);

COMMENT ON TABLE application.critiques IS
    'Critiques signées des libraires (série, ou tome par volume_url). Table distincte '
    'lue chaque mois par la construction du corpus. Retrait = statut retiree ; '
    'aucune suppression physique.';
COMMENT ON COLUMN application.critiques.series_id IS
    'Identifiant du catalogue (manga.ms_series_enriched.series_id), validé par l''API '
    'data du module 02 à l''écriture. Pas de clé étrangère : le schéma application ne '
    'dépend pas des instantanés du catalogue.';
COMMENT ON COLUMN application.critiques.label IS
    'Code d''une liste fixe, validée par l''application ; la base n''en garde que la forme.';

-- Une critique publiée par libraire, série et tome (le tome absent compte comme un
-- tome à part : la critique de la série).
CREATE UNIQUE INDEX critiques_une_publiee
    ON application.critiques (compte_id, series_id, coalesce(volume_url, ''))
    WHERE statut = 'publiee';
CREATE INDEX critiques_publiees_par_serie
    ON application.critiques (series_id)
    WHERE statut = 'publiee';

-- Le journal des actions : en AJOUT SEUL, garanti par les droits (app_rw : SELECT,
-- INSERT). RÈGLE : jamais de texte libre ni de secret dans `details` — ni mot de passe,
-- ni empreinte, ni pseudo, ni texte saisi ; des identifiants et des codes seulement.
CREATE TABLE application.journal_actions (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    le            timestamptz NOT NULL DEFAULT now(),
    par_compte_id bigint
        REFERENCES application.comptes (id) ON DELETE SET NULL,
    action        text        NOT NULL,
    objet_type    text,
    objet_id      bigint,
    details       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT journal_action_connue CHECK (action IN (
        'compte_cree', 'compte_desactive', 'compte_reactive', 'compte_supprime',
        'mot_de_passe_change', 'critique_publiee', 'critique_modifiee',
        'critique_retiree', 'profil_actif_change'
    )),
    CONSTRAINT journal_details_objet CHECK (jsonb_typeof(details) = 'object')
);

COMMENT ON TABLE application.journal_actions IS
    'Journal des actions, en ajout seul (droits de app_rw : SELECT, INSERT). Jamais de '
    'texte libre ni de secret dans details : identifiants et codes seulement.';

-- Les listes d'un compte : un état par série (favori, à lire, à commander). Elles
-- partent avec le compte.
CREATE TABLE application.listes (
    compte_id bigint      NOT NULL
        REFERENCES application.comptes (id) ON DELETE CASCADE,
    series_id integer     NOT NULL,
    etat      text        NOT NULL,
    ajoute_le timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (compte_id, series_id),
    CONSTRAINT listes_etat_connu CHECK (etat IN ('favori', 'a_lire', 'a_commander'))
);

COMMENT ON TABLE application.listes IS
    'Listes d''un compte : favori, à lire, à commander. series_id : identifiant du '
    'catalogue, sans clé étrangère (voir critiques.series_id).';

-- Les coûts du modèle de langage, agrégés par jour, profil et mode (depuis
-- `contexte.cout_estime_usd` des réponses de l'API du modèle).
CREATE TABLE application.couts_jour (
    jour     date          NOT NULL,
    profil   text          NOT NULL,
    mode     text          NOT NULL,
    appels   integer       NOT NULL DEFAULT 0,
    cout_usd numeric(12,6) NOT NULL DEFAULT 0,
    PRIMARY KEY (jour, profil, mode),
    CONSTRAINT couts_mode_connu CHECK (mode IN ('strict', 'enrichi')),
    CONSTRAINT couts_appels_positifs CHECK (appels >= 0),
    CONSTRAINT couts_montant_positif CHECK (cout_usd >= 0)
);

COMMENT ON TABLE application.couts_jour IS
    'Coûts estimés du modèle de langage, par jour, profil et mode (cout_estime_usd).';

-- Les réglages de l'application qu'un administrateur change (le profil actif…).
CREATE TABLE application.reglages (
    cle         text        PRIMARY KEY,
    valeur      text        NOT NULL,
    modifie_par bigint
        REFERENCES application.comptes (id) ON DELETE SET NULL,
    modifie_le  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reglages_cle_forme CHECK (cle ~ '^[a-z_]{1,60}$')
);

COMMENT ON TABLE application.reglages IS
    'Réglages de l''application changés par un administrateur (clé, valeur, auteur).';

-- --------------------------------------------------------------------------- --
--  Les droits de app_rw, objet par objet
-- --------------------------------------------------------------------------- --
GRANT USAGE ON SCHEMA application TO app_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON application.comptes, application.listes
    TO app_rw;
GRANT SELECT, INSERT, UPDATE ON application.critiques TO app_rw;
GRANT SELECT, INSERT ON application.journal_actions TO app_rw;
GRANT SELECT, INSERT, UPDATE ON application.couts_jour, application.reglages
    TO app_rw;
