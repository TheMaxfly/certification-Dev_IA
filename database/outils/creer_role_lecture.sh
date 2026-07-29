#!/bin/sh
# Procédure d'accès en consultation : crée (ou met à jour) le rôle de connexion
# `manga_api`, membre du groupe `manga_ro`.
#
# C'est ce script qui EST la procédure. La prose du README l'explique, mais
# n'est pas ce qu'on exécute : un accès obtenu deux fois de deux façons
# différentes n'est pas un accès documenté.
#
#   MANGA_API_PASSWORD='…' sh outils/creer_role_lecture.sh [DSN_ADMIN]
#
# DSN_ADMIN par défaut : la base apimanga locale. Le DSN doit désigner un rôle
# capable de créer des rôles (`postgres` en pratique).
#
# Le mot de passe vient de l'environnement, jamais d'un argument : la ligne de
# commande d'un processus est lisible par `ps`. Il n'est écrit dans aucun
# fichier, jamais affiché, et transmis à psql par son entrée standard — donc
# ni dans argv, ni dans un fichier temporaire.
#
# POSIX `sh` et non bash, délibérément : le harnais d'intégration exécute ce
# script dans l'image `postgres:16-alpine`, dont le shell est busybox. La
# procédure documentée est ainsi celle que les tests exercent, à l'identique.
set -eu

REFERENCE="${1:-postgresql://postgres@localhost:5432/apimanga}"

echouer() {
    echo "creer_role_lecture: $1" >&2
    exit 1
}

if [ -z "${MANGA_API_PASSWORD:-}" ]; then
    echouer "MANGA_API_PASSWORD n'est pas définie.
  Le mot de passe du rôle de connexion ne peut venir ni du dépôt ni d'un
  argument. Exemple :
    MANGA_API_PASSWORD=\"\$(openssl rand -base64 24)\" \\
      sh outils/creer_role_lecture.sh"
fi

command -v psql >/dev/null 2>&1 || echouer "le client psql est introuvable."

# `manga_ro` porte tous les privilèges ; sans lui, `manga_api` serait un rôle
# capable de se connecter et de ne rien lire. Mieux vaut le dire ici que
# laisser découvrir un 42501 à l'exécution.
GROUPE_PRESENT="$(
    psql "$REFERENCE" --no-psqlrc --quiet --tuples-only --no-align \
        --command "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'manga_ro')"
)" || echouer "connexion impossible avec le DSN fourni."

if [ "$GROUPE_PRESENT" != "t" ]; then
    echouer "le rôle de groupe manga_ro est absent de ce cluster.
  Appliquer d'abord les migrations :
    DATABASE_URL='$REFERENCE' uv run python migrate.py up"
fi

# Échappement SQL du mot de passe : doubler les apostrophes. Avec
# `standard_conforming_strings` à `on` (défaut depuis PostgreSQL 9.1), les
# antislashs sont littéraux dans une chaîne simple — rien d'autre à traiter.
# Le secret passe par un tube vers sed, pas par sa ligne de commande.
MOT_DE_PASSE_SQL="$(printf '%s' "$MANGA_API_PASSWORD" | sed "s/'/''/g")"

# Idempotent : crée le rôle, ou met à jour son mot de passe s'il existe déjà.
# `GRANT` de l'appartenance dans les deux cas — il est sans effet si elle est
# déjà acquise, et rattrape un rôle créé à la main hors de cette procédure.
printf '%s\n' "
DO \$creer\$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'manga_api') THEN
        ALTER ROLE manga_api WITH LOGIN PASSWORD '$MOT_DE_PASSE_SQL';
        RAISE NOTICE 'manga_api existait : mot de passe mis à jour.';
    ELSE
        CREATE ROLE manga_api LOGIN PASSWORD '$MOT_DE_PASSE_SQL';
        RAISE NOTICE 'manga_api créé.';
    END IF;
END
\$creer\$;

GRANT manga_ro TO manga_api;

COMMENT ON ROLE manga_api IS
    'Rôle de connexion de l''API et de la consultation directe. Aucun '
    'privilège propre : tout est hérité de manga_ro (migration 012).';
" | psql "$REFERENCE" --no-psqlrc --quiet --set ON_ERROR_STOP=1 --file - \
    || echouer "l'ordre SQL a échoué (voir le message psql ci-dessus)."

# Relevé final : ce que le rôle peut, et sous quelle appartenance. Aucune trace
# du mot de passe.
psql "$REFERENCE" --no-psqlrc --quiet --command "
SELECT r.rolname            AS role,
       r.rolcanlogin        AS peut_se_connecter,
       r.rolsuper           AS superutilisateur,
       pg_has_role('manga_api', 'manga_ro', 'MEMBER') AS membre_de_manga_ro,
       has_schema_privilege('manga_api', 'manga', 'USAGE')  AS usage_manga,
       has_schema_privilege('manga_api', 'staging', 'USAGE') AS usage_staging
  FROM pg_roles r
 WHERE r.rolname = 'manga_api'"

echo "→ manga_api est prêt. Le mot de passe n'a été ni journalisé ni écrit."
echo "  Côté client, le stocker dans ~/.pgpass (chmod 600) :"
echo "    hote:port:base:manga_api:<mot_de_passe>"
