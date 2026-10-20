#!/usr/bin/env bash
# Essai du périmètre de la vérification (verification/dependances.tsv) :
#   - chaque module de MODULES (verifier.sh) figure dans le graphe, et lui seul ;
#   - un changement donné concerne exactement les modules attendus.
#   bash verification/tester_perimetre.sh   → code 0 si tous les scénarios tiennent
set -uo pipefail

ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFIER="$ICI/verifier.sh"
TRAVAIL="$(mktemp -d)"
trap 'rm -rf "$TRAVAIL"' EXIT
echecs=0

ok() { echo "  ok    $1"; }
ko() { echo "  ÉCHEC $1"; echecs=$((echecs + 1)); }

cas() {  # cas <libellé> <modules attendus, séparés par des espaces> <chemins…>
  local libelle="$1" attendu obtenu
  # shellcheck disable=SC2086  # les modules attendus : une liste de mots, voulue
  attendu="$(printf '%s\n' $2 | jq -Rsc 'split("\n") | map(select(length > 0))')"
  shift 2
  obtenu="$(printf '%s\n' "$@" | bash "$VERIFIER" perimetre)"
  if [ "$obtenu" = "$attendu" ]; then ok "$libelle → $obtenu"; else ko "$libelle : attendu $attendu, obtenu $obtenu"; fi
}

echo "Essai du graphe"
if bash "$VERIFIER" graphe >/dev/null 2>&1; then ok "MODULES et le graphe se correspondent"; else ko "MODULES et le graphe divergent"; fi
# Mutation : un module retiré du graphe doit être vu.
grep -v '^08	' "$ICI/dependances.tsv" >"$TRAVAIL/sans_08.tsv"
if VERIFY_GRAPHE="$TRAVAIL/sans_08.tsv" bash "$VERIFIER" graphe >/dev/null 2>&1; then
  ko "un module absent du graphe passe inaperçu"
else
  ok "un module absent du graphe est refusé"
fi
# Mutation : un nom inconnu dans le graphe doit être vu.
{ cat "$ICI/dependances.tsv"; printf '11\t11_inconnu/\n'; } >"$TRAVAIL/en_trop.tsv"
if VERIFY_GRAPHE="$TRAVAIL/en_trop.tsv" bash "$VERIFIER" graphe >/dev/null 2>&1; then
  ko "un module inconnu du graphe passe inaperçu"
else
  ok "un module inconnu du graphe est refusé"
fi

echo "Essai du périmètre (statique tourne toujours)"
cas "aucun changement"                 "statique"
cas "documentation seule"              "statique"                              README.md CONTRIBUTING.md
cas "hooks seuls"                      "statique"                              .githooks/pre-push .githooks/tester.sh
cas "le script de sauvegarde"          "database statique"                     sauvegarde/sauvegarder.sh
cas "un fichier du 01"                 "01 statique"                           01_scraping_manganews/x.py
cas "un fichier du 05"                 "05 demo statique"                      05_nettoyage_agregation_bdd/src/corpus/x.py
cas "l'atteignabilité du 05"           "05 10 demo statique"                   05_nettoyage_agregation_bdd/src/evaluation/atteignabilite.py
cas "Bench_embedding du 06"            "05 statique"                           06_benchmark_embeddings_llm/Bench_embedding.py
cas "un autre fichier du 06"           "statique"                              06_benchmark_embeddings_llm/archive/autre.py
cas "un fichier du 09"                 "09 10 statique"                        09_service_embedding/src/x.py
cas "une migration"                    "02 05 08 09 10 database demo statique" database/migrations/099_x.sql
cas "un outil de database"             "05 08 09 10 database demo statique"    database/outils/x.sh
cas "un autre workflow"                "statique"                              .github/workflows/indicatif.yml
cas "verify.yml"                       "01 02 03 04 05 07 08 09 10 database demo statique" .github/workflows/verify.yml
cas "le moteur de vérification"        "01 02 03 04 05 07 08 09 10 database demo statique" verification/verifier.sh
cas "plusieurs modules"                "01 04 demo statique"                   01_scraping_manganews/a 04_scraping_manga_sanctuary/b
attendu_tout="$(printf '%s\n' 01 02 03 04 05 07 08 09 10 database demo statique | jq -Rsc 'split("\n") | map(select(length > 0))')"
obtenu_tout="$(: | TOUT=1 bash "$VERIFIER" perimetre)"
if [ "$obtenu_tout" = "$attendu_tout" ]; then ok "TOUT=1 → tous les modules"; else ko "TOUT=1 : obtenu $obtenu_tout"; fi

if [ "$echecs" -gt 0 ]; then
  echo "$echecs scénario(s) en échec"
  exit 1
fi
echo "Tous les scénarios tiennent."
