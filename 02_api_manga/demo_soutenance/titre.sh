#!/usr/bin/env bash
# Geste 6 (facultatif) — trouver une série À PARTIR DE SON TITRE, puis l'afficher.
#
# Un examinateur dit « essayez One Piece », jamais « essayez 11455 ». Ce script
# fait donc les deux moitiés du chemin, et MONTRE chacune :
#   1. la recherche du series_id, en SQL, sous le rôle de CONSULTATION de l'API
#      (manga_api) — le second moyen de mise à disposition, en lecture seule ;
#   2. la fiche, par l'API, avec la clé de démonstration.
#
# Usage : ./titre.sh "One Piece"
set -uo pipefail

TITRE="${1:?usage: ./titre.sh \"un titre\"}"
BASE="${BASE:-http://127.0.0.1:8000}"
ENV_FILE="$(dirname "$0")/.env"
CLE="$(grep '^API_KEYS=' "$ENV_FILE" | cut -d: -f2-)"
export PGPASSFILE="${PGPASSFILE:-$HOME/.pgpass}"
PSQL_URL="postgresql://manga_api@localhost:5432/apimanga"

# Même mise en scène que `commandes.sh` : le jury lit ce qui s'exécute. Un
# script dont on ne voit pas l'intérieur est une boîte noire, et une boîte
# noire ne prouve rien.
etape() { printf '\033[2m  %s\033[0m\n' "$1"; }
montre() { printf '\033[1;36m  $ %s\033[0m\n' "$1"; }

etape "1. Trouver l'identifiant à partir du titre — en SQL, en lecture seule"
# La requête est montrée AVEC son paramètre, pas avec le titre déjà inséré :
# c'est ainsi qu'elle s'exécute, et cela rend visible qu'aucune saisie de
# l'examinateur n'est concaténée dans le SQL.
montre "psql \"\$PSQL_RO\" -v titre=\"$TITRE\" -c \"SELECT series_id, series_title
        FROM manga.ms_series_enriched
        WHERE series_title ILIKE '%' || :'titre' || '%' ORDER BY … LIMIT 5;\""

# `-v titre=` + `:'titre'` : psql pose lui-même les quotes SQL. Un titre
# contenant une apostrophe (« L'Attaque des Titans ») passe sans échappement
# manuel, et rien de ce que tape l'examinateur n'est interpolé dans le SQL.
CANDIDATS=$(psql "$PSQL_URL" --no-align --tuples-only --quiet \
  -v ON_ERROR_STOP=1 -v titre="$TITRE" <<'SQL'
SELECT series_id || E'\t' || series_title
FROM manga.ms_series_enriched
WHERE series_title ILIKE '%' || :'titre' || '%'
-- Le titre exact d'abord, puis le plus commenté : « One Piece » doit sortir
-- avant « One Piece Party ».
ORDER BY (lower(series_title) = lower(:'titre')) DESC,
         series_review_count DESC NULLS LAST, series_id
LIMIT 5;
SQL
)

if [ -z "$CANDIDATS" ]; then
  printf '\n  Aucune série ne porte ce titre : « %s »\n' "$TITRE"
  printf '  → Le catalogue est celui du MARCHÉ FRANÇAIS (Manga Sanctuary),\n'
  printf '    pas un catalogue mondial. Une absence est une frontière connue.\n\n'
  exit 0
fi

printf '\n'
printf '%s\n' "$CANDIDATS" | awk -F'\t' '{printf "    %-8s %s\n", $1, $2}'
printf '\n'

ID=$(printf '%s\n' "$CANDIDATS" | head -1 | cut -f1)
etape "2. Demander sa fiche à l'API — avec la clé"
montre "curl -s -H \"X-API-Key: \$CLE\" $BASE/series/$ID | jq '{series_id, title, kitsu_id, work_uid, genres_source, genres_enriched}'"
curl -s -H "X-API-Key: $CLE" "$BASE/series/$ID" | jq '{
  series_id, title, kitsu_id, work_uid,
  genres_source:   (.genres_source   | join(", ")),
  genres_enriched: ([.genres_enriched[].code] | join(", "))
}'
