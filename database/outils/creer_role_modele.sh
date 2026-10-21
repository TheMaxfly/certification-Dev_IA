#!/bin/sh
# Procédure d'accès de l'API du modèle : crée (ou met à jour) le rôle de connexion
# `modele_api`, membre du groupe `modele_ro` (migration 023), en lecture seule.
#
#   MODELE_API_PASSWORD='…' sh outils/creer_role_modele.sh [DSN_ADMIN]
#
# Sert à l'API du modèle (module 11) et à l'étape « équivalence de l'API » de
# `make verify-poste`. DSN_ADMIN par défaut : la base apimanga locale, sous un rôle
# capable de créer des rôles (`postgres` en pratique).
#
# Même discipline que `creer_role_verification.sh` : le secret vient de
# l'environnement, jamais d'un argument (lisible par `ps`) ; il n'est écrit dans aucun
# fichier, jamais affiché, et passe à psql par son entrée standard. POSIX `sh`.
#
# Deux verrous de lecture seule, indépendants :
#   * les droits : `modele_api` n'a aucun privilège propre, il hérite de `modele_ro`
#     (`SELECT` énumérés seulement) ;
#   * la session : `default_transaction_read_only = on`, posé sur le rôle — toute
#     transaction ouverte sous ce rôle refuse d'écrire, même sur un objet où un droit
#     d'écriture aurait été accordé par erreur.
# Et une borne : `CONNECTION LIMIT 10` (pool de l'API : 5 au plus, plus une marge) —
# une API emballée ne prend pas toutes les connexions du serveur.
set -eu

REFERENCE="${1:-postgresql://postgres@localhost:5432/apimanga}"

echouer() {
    echo "creer_role_modele: $1" >&2
    exit 1
}

if [ -z "${MODELE_API_PASSWORD:-}" ]; then
    echouer "MODELE_API_PASSWORD n'est pas définie.
  Le mot de passe du rôle de connexion ne peut venir ni du dépôt ni d'un
  argument. Exemple :
    MODELE_API_PASSWORD=\"\$(openssl rand -base64 24)\" \\
      sh outils/creer_role_modele.sh"
fi

command -v psql >/dev/null 2>&1 || echouer "le client psql est introuvable."

GROUPE_PRESENT="$(
    psql "$REFERENCE" --no-psqlrc --quiet --tuples-only --no-align \
        --command "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'modele_ro')"
)" || echouer "connexion impossible avec le DSN fourni."

if [ "$GROUPE_PRESENT" != "t" ]; then
    echouer "le rôle de groupe modele_ro est absent de ce cluster.
  Appliquer d'abord les migrations (023) :
    DATABASE_URL='$REFERENCE' uv run python migrate.py up"
fi

# Échappement SQL : doubler les apostrophes. Le secret passe par un tube vers sed.
MOT_DE_PASSE_SQL="$(printf '%s' "$MODELE_API_PASSWORD" | sed "s/'/''/g")"

# Idempotent : crée le rôle, ou met à jour son secret et ses attributs s'il existe.
printf '%s\n' "
DO \$creer\$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'modele_api') THEN
        ALTER ROLE modele_api WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            CONNECTION LIMIT 10 PASSWORD '$MOT_DE_PASSE_SQL';
        RAISE NOTICE 'modele_api existait : mot de passe mis à jour.';
    ELSE
        CREATE ROLE modele_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            CONNECTION LIMIT 10 PASSWORD '$MOT_DE_PASSE_SQL';
        RAISE NOTICE 'modele_api créé.';
    END IF;
END
\$creer\$;

GRANT modele_ro TO modele_api;
ALTER ROLE modele_api SET default_transaction_read_only = on;

COMMENT ON ROLE modele_api IS
    'Rôle de connexion de l''API du modèle (module 11). Aucun privilège propre : '
    'tout est hérité de modele_ro (migration 023). Lecture seule, 10 connexions.';
" | psql "$REFERENCE" --no-psqlrc --quiet --set ON_ERROR_STOP=1 --file - \
    || echouer "l'ordre SQL a échoué (voir le message psql ci-dessus)."

# Relevé final, sans trace du secret.
psql "$REFERENCE" --no-psqlrc --quiet --command "
SELECT r.rolname            AS role,
       r.rolcanlogin        AS peut_se_connecter,
       r.rolsuper           AS superutilisateur,
       r.rolconnlimit       AS connexions_max,
       pg_has_role('modele_api', 'modele_ro', 'MEMBER') AS membre_de_modele_ro,
       pg_has_role('modele_api', 'manga_ro', 'MEMBER')  AS membre_de_manga_ro,
       pg_has_role('modele_api', 'verif_ro', 'MEMBER')  AS membre_de_verif_ro,
       has_schema_privilege('modele_api', 'staging', 'USAGE') AS usage_staging
  FROM pg_roles r
 WHERE r.rolname = 'modele_api'"

echo "→ modele_api est prêt. Le mot de passe n'a été ni journalisé ni écrit."
echo "  Côté client, le stocker dans ~/.pgpass (chmod 600) :"
echo "    hote:port:base:modele_api:<mot_de_passe>"
