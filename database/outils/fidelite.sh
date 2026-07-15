#!/usr/bin/env bash
# Preuve de fidélité : la base reconstruite depuis le dépôt est-elle identique
# à la base réelle ?
#
# Rejoue TOUTES les migrations sur un PostgreSQL jetable, puis compare son
# schéma à celui d'apimanga. Un diff vide signifie que le dépôt sait
# reconstruire la base. Un diff non vide signifie que la baseline ment — ce
# contrôle existe pour que ça se voie.
#
#   bash outils/fidelite.sh [DSN_REFERENCE]
#
# DSN_REFERENCE par défaut : la base apimanga locale.
# Nécessite Docker (base jetable) et le client pg_dump.
set -euo pipefail

REFERENCE="${1:-postgresql://postgres@localhost:5432/apimanga}"
IMAGE="${IMAGE_POSTGRES:-postgres:16-alpine}"
SCHEMAS=(-n manga -n bench -n staging)

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRAVAIL="$(mktemp -d)"
NOM="fidelite-$$"

nettoyer() {
    docker rm -f "$NOM" >/dev/null 2>&1 || true
    rm -rf "$TRAVAIL"
}
trap nettoyer EXIT

echo "=== base jetable ($IMAGE) ==="
docker run --rm -d --name "$NOM" -e POSTGRES_PASSWORD=postgres -p 0:5432 "$IMAGE" >/dev/null
PORT="$(docker inspect "$NOM" --format '{{(index .NetworkSettings.Ports "5432/tcp" 0).HostPort}}')"
for _ in $(seq 1 60); do
    docker exec "$NOM" pg_isready -U postgres >/dev/null 2>&1 && break
    sleep 0.3
done
sleep 1
JETABLE="postgresql://postgres:postgres@127.0.0.1:$PORT/postgres"

echo "=== rejeu du dépôt sur base vierge ==="
(cd "$RACINE" && DATABASE_URL="$JETABLE" uv run python migrate.py up)
(cd "$RACINE" && DATABASE_URL="$JETABLE" uv run python migrate.py status)

# Le même binaire pg_dump des deux côtés : seul le contenu doit différer.
normaliser() {
    # Hors commentaires (dont la version serveur et la date du dump),
    # méta-commandes psql, préambule SET, lignes vides.
    grep -vE '^--' \
        | grep -vE '^\\(restrict|unrestrict) ' \
        | grep -vE '^(SET |SELECT pg_catalog\.set_config)' \
        | grep -vE '^\s*$'
}

pg_dump "$REFERENCE" --schema-only --no-owner --no-privileges "${SCHEMAS[@]}" \
    | normaliser > "$TRAVAIL/reference.sql"
pg_dump "$JETABLE" --schema-only --no-owner --no-privileges "${SCHEMAS[@]}" \
    | normaliser > "$TRAVAIL/jetable.sql"

echo
echo "=== diff de fidélité ==="
echo "  référence : $(wc -l < "$TRAVAIL/reference.sql") lignes"
echo "  jetable   : $(wc -l < "$TRAVAIL/jetable.sql") lignes"

if diff -u "$TRAVAIL/reference.sql" "$TRAVAIL/jetable.sql" > "$TRAVAIL/diff.txt"; then
    echo "  ✅ DIFF VIDE — le dépôt reconstruit fidèlement la base de référence."
    exit 0
fi

echo "  ❌ ÉCARTS — le dépôt ne reconstruit PAS la base de référence :"
sed 's/^/    /' "$TRAVAIL/diff.txt"
exit 1
