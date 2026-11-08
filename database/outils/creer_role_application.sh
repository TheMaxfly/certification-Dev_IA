#!/bin/sh
# Procédure d'accès de l'application : crée le rôle de connexion `app_conseiller`,
# membre du groupe `app_rw` (migration 027), le seul rôle par lequel l'application du
# libraire (module 12) se connecte à sa base.
#
#   sh outils/creer_role_application.sh 'postgresql://postgres@localhost:5432/apimanga_preprod'
#
# DSN_ADMIN, obligatoire : la base visée (`apimanga_preprod` ou `apimanga`, aucune
# autre), sous un rôle capable de créer des rôles (`postgres` en pratique), SANS mot de
# passe (il est dans ~/.pgpass ; un DSN qui en porte un est refusé). Pas de valeur par
# défaut : la base se nomme à chaque fois.
#
# Même structure que `creer_role_verification.sh` (garde, messages, relevé final), à
# deux différences près, voulues (E4, lot 1, spec 01) :
#   * le mot de passe est SAISI AU CLAVIER, sans écho, deux fois — jamais en argument
#     (lisible par `ps`), en variable d'environnement ni dans l'historique. C'est
#     `\password` de psql qui le demande, sur le terminal : il n'envoie au serveur que
#     son empreinte SCRAM, jamais le mot de passe en clair, qui n'entre donc ni dans un
#     ordre SQL ni dans un journal du serveur ;
#   * si le rôle existe, REFUS : aucune réinitialisation silencieuse du mot de passe.
#
# Un rôle est un objet du SERVEUR : créé une fois (en préproduction), il vaut aussi
# pour la production du même serveur ; seule une ligne `~/.pgpass` par base s'ajoute.
#
# Ce que reçoit `app_conseiller` : LOGIN, 10 connexions au plus, l'appartenance à
# `app_rw`, rien d'autre (ni superutilisateur, ni création de base ou de rôle, ni
# réplication, ni contournement des politiques de lignes). POSIX `sh`.
set -eu

ROLE=app_conseiller

echouer() {
    echo "creer_role_application: $1" >&2
    exit 1
}

if [ "$#" -ne 1 ] || [ -z "$1" ]; then
    echouer "un argument attendu : le DSN d'administration de la base visée.
  Exemple :
    sh outils/creer_role_application.sh \\
      'postgresql://postgres@localhost:5432/apimanga_preprod'"
fi
REFERENCE="$1"

# Un mot de passe dans le DSN serait lisible par `ps` et resterait dans l'historique :
# il se range dans ~/.pgpass. Le DSN n'est jamais répété dans le message.
case "$REFERENCE" in
    *password=* | *://*:*@*)
        echouer "le DSN porte un mot de passe : il serait lisible par ps et resterait
  dans l'historique. Le retirer du DSN et le ranger dans ~/.pgpass." ;;
esac

command -v psql >/dev/null 2>&1 || echouer "le client psql est introuvable."

if [ ! -t 0 ]; then
    echouer "le mot de passe se saisit au clavier : l'entrée standard n'est pas un
  terminal. Lancer la commande dans un terminal, sans tube ni redirection."
fi

lire() {
    psql "$REFERENCE" --no-psqlrc --quiet --tuples-only --no-align \
        --set ON_ERROR_STOP=1 --command "$1" </dev/null
}

BASE="$(lire "SELECT current_database()")" ||
    echouer "connexion impossible avec le DSN fourni."

case "$BASE" in
    apimanga | apimanga_preprod) ;;
    *) echouer "la base « $BASE » est refusée : seules apimanga et apimanga_preprod
  sont admises." ;;
esac

if [ "$(lire "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw')")" != "t" ]; then
    echouer "le rôle de groupe app_rw est absent de ce serveur.
  Appliquer d'abord la migration 027 :
    DATABASE_URL='<DSN de la base>' uv run python migrate.py up --target 027"
fi

if [ "$(lire "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'application')")" != "t" ]; then
    echouer "le schéma application est absent de la base $BASE : la migration 027 n'y
  est pas appliquée."
fi

if [ "$(lire "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$ROLE')")" != "f" ]; then
    echouer "le rôle $ROLE existe déjà : rien n'est modifié (aucune réinitialisation du
  mot de passe). Un rôle vaut pour tout le serveur : pour une autre base du même
  serveur, il suffit d'une ligne ~/.pgpass."
fi

echo "Création de $ROLE sur la base $BASE. Le mot de passe est demandé deux fois, sans écho."

# Une seule transaction : si la saisie échoue (deux mots de passe différents, saisie
# interrompue), psql s'arrête et rien n'est créé.
printf '%s\n' "
BEGIN;
CREATE ROLE $ROLE LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 10 IN ROLE app_rw;
COMMENT ON ROLE $ROLE IS
    'Rôle de connexion de l''application du libraire (module 12). Aucun privilège '
    'propre : tout est hérité de app_rw (migration 027), le schéma application seul.';
\\password $ROLE
COMMIT;
" | psql "$REFERENCE" --no-psqlrc --quiet --set ON_ERROR_STOP=1 --file - ||
    echouer "le rôle n'a pas été créé (saisie différente, interrompue, ou ordre refusé :
  voir le message psql ci-dessus)."

# Relevé final, sans trace du secret.
psql "$REFERENCE" --no-psqlrc --quiet --command "
SELECT r.rolname                                  AS role,
       r.rolcanlogin                              AS peut_se_connecter,
       r.rolsuper                                 AS superutilisateur,
       r.rolconnlimit                             AS connexions_max,
       pg_has_role('$ROLE', 'app_rw', 'MEMBER')   AS membre_de_app_rw,
       has_schema_privilege('$ROLE', 'application', 'USAGE') AS usage_application,
       has_schema_privilege('$ROLE', 'manga', 'USAGE')       AS usage_manga
  FROM pg_roles r
 WHERE r.rolname = '$ROLE'" </dev/null

echo "→ $ROLE est prêt. Le mot de passe n'a été ni affiché, ni journalisé, ni écrit."
echo "  Côté client, le stocker dans ~/.pgpass (chmod 600), une ligne par base :"
echo "    hote:port:$BASE:$ROLE:<mot_de_passe>"
