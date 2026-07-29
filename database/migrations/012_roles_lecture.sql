-- 012 — accès en consultation : le rôle de groupe `manga_ro`.
--
-- CE QU'UN RÔLE EST, ET POURQUOI CE FICHIER N'EN CRÉE QU'UN SEUL
--
-- Un rôle PostgreSQL est un objet de CLUSTER, pas de base : il vit dans
-- `pg_authid`, partagé par toutes les bases de l'instance, et il SURVIT à un
-- `DROP DATABASE`. Une migration, elle, décrit l'état d'UNE base. Les deux
-- notions ne se recouvrent pas, et il faut choisir quoi versionner.
--
-- Ce qui est versionné ici :
--   * `manga_ro`, rôle de GROUPE, `NOLOGIN`, sans mot de passe. Il ne porte
--     aucun secret, sa création est déterministe et rejouable : un fichier de
--     migration est le bon endroit.
--   * les GRANT qui le concernent. Un privilège, contrairement au rôle, est
--     bien un objet de base — il est inscrit dans les `relacl` de `manga` et
--     disparaît avec elle. C'est précisément ce qu'une migration doit décrire,
--     et ce qui doit se reconstruire à l'identique sur une base neuve.
--
-- Ce qui n'est PAS versionné, et ne peut pas l'être :
--   * le rôle de CONNEXION `manga_api`, qui porte un mot de passe. Il est créé
--     par `database/outils/creer_role_lecture.sh`, qui lit le secret dans
--     l'environnement. Aucun mot de passe ne doit entrer dans le dépôt, même
--     de test : un fichier de migration est immuable et son checksum est
--     vérifié à chaque `up`.
--
-- L'articulation des deux : `manga_api` n'a aucun privilège propre, il les
-- hérite tous de `manga_ro`. Faire évoluer les droits de consultation, c'est
-- donc écrire une migration — jamais toucher au rôle de connexion.
--
-- PÉRIMÈTRE : le schéma `manga`, et lui seul. `staging` est jetable (tables de
-- transit rechargées à chaque ELT) et ne fait pas partie de la mise à
-- disposition : aucun droit n'y est accordé, pas même `USAGE`.
--
-- `SELECT` seulement. Ni `INSERT`, ni `UPDATE`, ni `DELETE`, ni `TRUNCATE`, ni
-- `REFERENCES`, ni `TRIGGER`, et aucun droit sur les séquences : sans `USAGE`
-- sur une séquence, `nextval` est refusé, ce qui ferme la dernière voie
-- d'écriture indirecte.

-- Rôle de groupe. Gardé par un test d'existence sur `pg_roles` : `CREATE ROLE`
-- n'accepte pas `IF NOT EXISTS`, et le rôle a pu être créé par un passage
-- antérieur sur une AUTRE base du même cluster — c'est le cas courant du
-- harnais de test, qui crée une base neuve par test dans un conteneur unique.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'manga_ro') THEN
        CREATE ROLE manga_ro NOLOGIN;
    END IF;
END
$$;

COMMENT ON ROLE manga_ro IS
    'Consultation seule du schéma manga. Rôle de groupe, NOLOGIN : on ne s''y '
    'connecte pas, on en devient membre. Droits posés par la migration 012.';

-- `GRANT ... ON DATABASE` exige un nom de base. Il ne peut pas être écrit en
-- dur : cette migration s'applique à `apimanga`, mais aussi à la base jetable
-- du harnais d'intégration et aux bases éphémères des tests, qui portent
-- chacune un nom différent. `current_database()` est la seule écriture juste.
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO manga_ro', current_database());
END
$$;

-- Entrer dans le schéma, puis lire ce qu'il contient. `ALL TABLES` couvre les
-- vues autant que les tables : PostgreSQL les traite ensemble pour les
-- privilèges de relation. Les 8 vues du corpus RAG sont donc incluses.
GRANT USAGE ON SCHEMA manga TO manga_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA manga TO manga_ro;

-- Les objets FUTURS. Attention à la portée réelle de cet ordre : les privilèges
-- par défaut ne s'appliquent qu'aux objets créés par le rôle qui exécute
-- l'`ALTER` — ici `postgres`, puisque c'est lui qui joue les migrations, et le
-- propriétaire de la totalité des 38 relations existantes de `manga`. Une table
-- créée dans `manga` par un autre rôle n'hériterait de rien, et devrait faire
-- l'objet d'un `GRANT` explicite dans une migration ultérieure. Le contrôle est
-- exercé par un test, pas laissé à cette remarque.
ALTER DEFAULT PRIVILEGES IN SCHEMA manga GRANT SELECT ON TABLES TO manga_ro;
