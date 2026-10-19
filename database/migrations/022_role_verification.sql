-- 022 — lecture de vérification : le rôle de groupe `verif_ro`.
--
-- POURQUOI UN SECOND RÔLE DE LECTURE
--
-- L'étape « état d'apimanga » de `make verify-poste` relit la base réelle : les
-- empreintes des corpus et des mesures (`bench`), le catalogue (`manga`) et le suivi
-- des migrations (`public.schema_migrations`). `manga_ro` (012) ne peut pas la porter :
-- son périmètre est `manga` seul, et `bench` en reste exclu par décision (016, 020,
-- 021) — l'API, qui hérite de `manga_ro`, ne doit rien lire de `bench`. Étendre
-- `manga_ro` aurait ouvert `bench` à l'API. Un rôle distinct garde les deux usages
-- séparés (décision de Max, 19 octobre 2026).
--
-- Même construction que 012 :
--   * `verif_ro`, rôle de GROUPE, `NOLOGIN`, sans secret : versionné ici ;
--   * le rôle de CONNEXION `verif_lecture`, qui porte un secret, est créé hors
--     migration par `database/outils/creer_role_verification.sh`.
--
-- PÉRIMÈTRE : `SELECT` sur `manga`, `bench` et `public.schema_migrations`. Rien sur
-- `staging`, rien sur les séquences, aucune écriture d'aucune sorte.

-- Rôle de groupe, gardé par un test d'existence : un rôle est un objet de cluster,
-- il a pu être créé par un passage antérieur sur une autre base du même cluster.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verif_ro') THEN
        CREATE ROLE verif_ro NOLOGIN;
    END IF;
END
$$;

COMMENT ON ROLE verif_ro IS
    'Lecture seule de vérification : manga, bench et le suivi des migrations. Rôle '
    'de groupe, NOLOGIN. Droits posés par la migration 022 ; distinct de manga_ro, '
    'dont l''API hérite.';

-- `current_database()` : la migration s'applique aussi aux bases jetables des tests.
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO verif_ro', current_database());
END
$$;

GRANT USAGE ON SCHEMA manga, bench TO verif_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA manga, bench TO verif_ro;
GRANT SELECT ON public.schema_migrations TO verif_ro;

-- Les objets futurs créés par le rôle qui joue les migrations (`postgres`).
ALTER DEFAULT PRIVILEGES IN SCHEMA manga GRANT SELECT ON TABLES TO verif_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA bench GRANT SELECT ON TABLES TO verif_ro;
