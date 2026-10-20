#!/bin/sh
# Procédure d'accès de vérification : crée (ou met à jour) le rôle de connexion
# `verif_lecture`, membre du groupe `verif_ro` (migration 022), en lecture seule.
#
#   VERIF_LECTURE_PASSWORD='…' sh outils/creer_role_verification.sh [DSN_ADMIN]
#
# Sert à l'étape « état d'apimanga » de `make verify-poste`, et à elle seule.
# DSN_ADMIN par défaut : la base apimanga locale, sous un rôle capable de créer des
# rôles (`postgres` en pratique).
#
# Même discipline que `creer_role_lecture.sh` : le secret vient de l'environnement,
# jamais d'un argument (lisible par `ps`) ; il n'est écrit dans aucun fichier, jamais
# affiché, et passe à psql par son entrée standard. POSIX `sh`.
#
# Deux verrous de lecture seule, indépendants :
#   * les droits : `verif_lecture` n'a aucun privilège propre, il hérite de `verif_ro`
#     (`SELECT` seulement) ;
#   * la session : `default_transaction_read_only = on`, posé sur le rôle — toute
#     transaction ouverte sous ce rôle refuse d'écrire, même sur un objet où un droit
#     d'écriture aurait été accordé par erreur.
set -eu

REFERENCE="${1:-postgresql://postgres@localhost:5432/apimanga}"

echouer() {
    echo "creer_role_verification: $1" >&2
    exit 1
}

if [ -z "${VERIF_LECTURE_PASSWORD:-}" ]; then
    echouer "VERIF_LECTURE_PASSWORD n'est pas définie.
  Le mot de passe du rôle de connexion ne peut venir ni du dépôt ni d'un
  argument. Exemple :
    VERIF_LECTURE_PASSWORD=\"\$(openssl rand -base64 24)\" \\
      sh outils/creer_role_verification.sh"
fi

command -v psql >/dev/null 2>&1 || echouer "le client psql est introuvable."

GROUPE_PRESENT="$(
    psql "$REFERENCE" --no-psqlrc --quiet --tuples-only --no-align \
        --command "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verif_ro')"
)" || echouer "connexion impossible avec le DSN fourni."

if [ "$GROUPE_PRESENT" != "t" ]; then
    echouer "le rôle de groupe verif_ro est absent de ce cluster.
  Appliquer d'abord les migrations (022) :
    DATABASE_URL='$REFERENCE' uv run python migrate.py up"
fi

# Échappement SQL : doubler les apostrophes. Le secret passe par un tube vers sed.
MOT_DE_PASSE_SQL="$(printf '%s' "$VERIF_LECTURE_PASSWORD" | sed "s/'/''/g")"

# Idempotent : crée le rôle, ou met à jour son secret s'il existe déjà.
printf '%s\n' "
DO \$creer\$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verif_lecture') THEN
        ALTER ROLE verif_lecture WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            PASSWORD '$MOT_DE_PASSE_SQL';
        RAISE NOTICE 'verif_lecture existait : mot de passe mis à jour.';
    ELSE
        CREATE ROLE verif_lecture LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            PASSWORD '$MOT_DE_PASSE_SQL';
        RAISE NOTICE 'verif_lecture créé.';
    END IF;
END
\$creer\$;

GRANT verif_ro TO verif_lecture;
ALTER ROLE verif_lecture SET default_transaction_read_only = on;

COMMENT ON ROLE verif_lecture IS
    'Rôle de connexion de l''étape « état d''apimanga » (make verify-poste). Aucun '
    'privilège propre : tout est hérité de verif_ro (migration 022). Lecture seule.';
" | psql "$REFERENCE" --no-psqlrc --quiet --set ON_ERROR_STOP=1 --file - \
    || echouer "l'ordre SQL a échoué (voir le message psql ci-dessus)."

# Relevé final, sans trace du secret.
psql "$REFERENCE" --no-psqlrc --quiet --command "
SELECT r.rolname            AS role,
       r.rolcanlogin        AS peut_se_connecter,
       r.rolsuper           AS superutilisateur,
       pg_has_role('verif_lecture', 'verif_ro', 'MEMBER')  AS membre_de_verif_ro,
       pg_has_role('verif_lecture', 'manga_ro', 'MEMBER')  AS membre_de_manga_ro,
       has_schema_privilege('verif_lecture', 'bench', 'USAGE')   AS usage_bench,
       has_schema_privilege('verif_lecture', 'staging', 'USAGE') AS usage_staging
  FROM pg_roles r
 WHERE r.rolname = 'verif_lecture'"

echo "→ verif_lecture est prêt. Le mot de passe n'a été ni journalisé ni écrit."
echo "  Côté client, le stocker dans ~/.pgpass (chmod 600) :"
echo "    hote:port:base:verif_lecture:<mot_de_passe>"
