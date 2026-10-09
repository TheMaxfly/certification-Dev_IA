#!/usr/bin/env bash
# Vérification unique du dépôt : le même verdict sur le poste et sur GitHub.
#
#   verifier.sh verify [MODULE]     Ruff, format et tests de chaque module, puis
#                                   actionlint ; un seul module si MODULE est donné
#   verifier.sh indicatif [MODULE]  Bandit et pip-audit — jamais exigés
#   verifier.sh poste               verify, puis les harnais qui ne tournent que sur
#                                   le poste
#
# Lancé par le Makefile de la racine (`make verify`, `make verify MODULE=05`,
# `make verify-indicatif`, `make verify-poste`).
#
# Règles :
#   - chaque module est lancé avec son verrou (`uv run --locked`), dans un
#     environnement vidé : ni VIRTUAL_ENV hérité, ni APIMANGA_DSN, ni DATABASE_URL —
#     aucune suite ne touche la base réelle ;
#   - arrêt au premier échec, avec le module et la commande en cause ;
#   - une ligne par module : passés, sautés, en échec, durée. Un test sauté n'est
#     pas un test passé : il est compté, et au-delà du plafond de
#     verification/sauts_attendus.tsv, c'est un échec ;
#   - les volumes Docker créés pendant la vérification, et eux seuls, sont
#     supprimés à la fin (les bases jetables des tests en laissent un chacune).
set -uo pipefail
export LC_ALL=C

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SORTIES="${VERIFY_SORTIES:-$RACINE/.verify}"
SAUTS="${VERIFY_SAUTS:-$RACINE/verification/sauts_attendus.tsv}"
COUVERTURE="$RACINE/verification/couverture.ini"

# Outils lancés par uvx, à version fixée (absents des verrous des modules).
# Ruff : la version du pre-commit, lue à sa source pour ne jamais en diverger.
RUFF="$(sed -n '/ruff-pre-commit/,/rev:/s/^ *rev: *v//p' "$RACINE/.pre-commit-config.yaml")"
BANDIT=1.9.4
PIP_AUDIT=2.10.1
ACTIONLINT=1.7.12.25  # paquet actionlint-py : actionlint 1.7.12
SHELLCHECK=0.11.0.1   # paquet shellcheck-py : shellcheck 0.11.0

# nom | dossier Ruff | dossier du projet (verrou, tests) | lanceur | options uv | arguments | couverture | prérequis
MODULES=(
  "01|01_scraping_manganews|01_scraping_manganews|unittest|||||"
  "02|02_api_manga|02_api_manga|pytest|||||"
  "03|03_kitsu_api_exports|03_kitsu_api_exports|unittest|||||"
  "04|04_scraping_manga_sanctuary|04_scraping_manga_sanctuary|pytest|--extra dev|tests/||"
  "05|05_nettoyage_agregation_bdd|05_nettoyage_agregation_bdd|pytest|--extra dev|||database"
  "06|06_benchmark_embeddings_llm||ruff-seul||||"
  "07|07_databricks_manga_export|07_databricks_manga_export/lakehouse|pytest|--extra dev|||"
  "08|08_enrichissement_personnages|08_enrichissement_personnages|pytest|--extra dev|tests|.|"
  "09|09_service_embedding|09_service_embedding|pytest|--extra dev||service_embedding|database"
  "10|10_mesures_recherche|10_mesures_recherche|pytest|--extra dev||mesures_recherche|database"
  "database|database|database|pytest|--extra dev|tests/||"
  "demo|demo|demo|pytest|--extra dev|||"
  "workflows|||actionlint||||"
)

RESUME=()      # une ligne par module traité
ECHEC=""       # premier échec : module, commande
VOLUMES_AVANT=""

dire() { printf '\n==> %s\n' "$*"; }

# --------------------------------------------------------------------------- #
#  Environnement vidé
# --------------------------------------------------------------------------- #
CHEMIN="$(printf '%s' "$PATH" | tr ':' '\n' | grep -v -e '/\.venv/' \
  ${VIRTUAL_ENV:+-e "^$VIRTUAL_ENV"} | paste -sd: -)"
PROPRE=(env -i HOME="$HOME" USER="${USER:-$(id -un)}" LANG=C.UTF-8 PATH="$CHEMIN")
for v in TMPDIR JAVA_HOME DOCKER_HOST DOCKER_CONFIG XDG_RUNTIME_DIR CI GITHUB_ACTIONS; do
  [ -n "${!v:-}" ] && PROPRE+=("$v=${!v}")
done
while IFS= read -r v; do PROPRE+=("$v=${!v}"); done < <(compgen -e | grep '^UV_' || true)

# --------------------------------------------------------------------------- #
#  Volumes Docker : relevé avant, suppression de ceux créés, relevé après
# --------------------------------------------------------------------------- #
docker_present() { command -v docker >/dev/null && docker info >/dev/null 2>&1; }

volumes_debut() {
  docker_present || return 0
  VOLUMES_AVANT="$(docker volume ls -q | sort)"
}

volumes_fin() {
  docker_present || return 0
  local apres crees orphelins v supprimes=0 gardes=0
  apres="$(docker volume ls -q | sort)"
  crees="$(comm -13 <(printf '%s\n' "$VOLUMES_AVANT") <(printf '%s\n' "$apres") | sed '/^$/d')"
  orphelins="$(docker volume ls -q -f dangling=true | sort)"
  for v in $crees; do
    if grep -qxF "$v" <<<"$orphelins"; then
      docker volume rm "$v" >/dev/null && supprimes=$((supprimes + 1))
    else
      gardes=$((gardes + 1))
    fi
  done
  printf 'volumes Docker : %s avant, %s créés pendant la vérification, %s supprimés' \
    "$(grep -c . <<<"$VOLUMES_AVANT")" "$(grep -c . <<<"$crees")" "$supprimes"
  [ "$gardes" -gt 0 ] && printf ', %s encore utilisés (gardés)' "$gardes"
  printf ' — %s après\n' "$(docker volume ls -q | grep -c .)"
}

# --------------------------------------------------------------------------- #
#  Résumé : une ligne par module, à l'écran et pour GitHub
# --------------------------------------------------------------------------- #
resume() {
  [ ${#RESUME[@]} -gt 0 ] || return 0
  printf '\n%-10s %-7s %-7s %8s %15s %8s %9s %8s  %s\n' \
    module ruff format passés "sautés/plafond" échecs durée couv. verdict
  local l
  for l in "${RESUME[@]}"; do
    IFS='|' read -r m r f p s pl e d c v <<<"$l"
    printf '%-10s %-7s %-7s %8s %15s %8s %8ss %8s  %s\n' "$m" "$r" "$f" "$p" "$s/$pl" "$e" "$d" "$c" "$v"
  done
  {
    echo "| module | Ruff | format | passés | sautés / plafond | en échec | durée | couverture | verdict |"
    echo "|---|---|---|---:|---:|---:|---:|---:|---|"
    for l in "${RESUME[@]}"; do
      IFS='|' read -r m r f p s pl e d c v <<<"$l"
      echo "| $m | $r | $f | $p | $s / $pl | $e | $d s | $c | $v |"
    done
  } >"$SORTIES/resume.md"
  [ -n "${GITHUB_STEP_SUMMARY:-}" ] && cat "$SORTIES/resume.md" >>"$GITHUB_STEP_SUMMARY"
  return 0
}

terminer() {
  local rc=$?
  resume
  volumes_fin
  if [ -n "$ECHEC" ]; then
    printf '\nÉCHEC — %s\n' "$ECHEC" >&2
    exit 1
  fi
  exit "$rc"
}

echouer() {  # <module> <ce qui échoue> <commande>
  ECHEC="module $1 : $2"$'\n'"  commande : $3"
  exit 1
}

# --------------------------------------------------------------------------- #
#  Un module
# --------------------------------------------------------------------------- #
plafond_sauts() {
  local p
  p="$(awk -F'\t' -v m="$1" '$1 == m { print $2 }' "$SAUTS")"
  echo "${p:-0}"
}

attribut_junit() {  # <fichier> <attribut> : sur le premier <testsuite>
  grep -o '<testsuite [^>]*' "$1" | head -1 | grep -o " $2=\"[0-9]*\"" | grep -o '[0-9]*'
}

verifier_module() {
  local nom lint projet lanceur options args couv prerequis
  IFS='|' read -r nom lint projet lanceur options args couv prerequis <<<"$1"
  local sortie="$SORTIES/$nom" debut ruff=- format=- passes=- sautes=- echecs=- couverture=-
  mkdir -p "$sortie"
  debut=$SECONDS

  # Ruff, depuis la racine : chaque fichier prend la configuration la plus proche.
  if [ -n "$lint" ]; then
    local sortie_ruff=concise
    [ "${GITHUB_ACTIONS:-}" = true ] && sortie_ruff=github
    local cmd=(uvx "ruff@$RUFF" check --no-cache --output-format="$sortie_ruff" "$lint")
    dire "$nom — ${cmd[*]}"
    (cd "$RACINE" && "${PROPRE[@]}" "${cmd[@]}") || {
      RESUME+=("$nom|échec|-|-|-|-|-|$((SECONDS - debut))|-|ÉCHEC"); echouer "$nom" "Ruff" "${cmd[*]}"; }
    ruff=ok
    cmd=(uvx "ruff@$RUFF" format --check --no-cache "$lint")
    dire "$nom — ${cmd[*]}"
    (cd "$RACINE" && "${PROPRE[@]}" "${cmd[@]}") || {
      RESUME+=("$nom|ok|échec|-|-|-|-|$((SECONDS - debut))|-|ÉCHEC"); echouer "$nom" "format" "${cmd[*]}"; }
    format=ok
  fi

  # Prérequis : l'environnement de database/, dont les bases jetables jouent migrate.py.
  if [ "$prerequis" = database ]; then
    local cmd=(uv sync --locked --inexact)
    dire "$nom — prérequis database/ : ${cmd[*]}"
    (cd "$RACINE/database" && "${PROPRE[@]}" "${cmd[@]}") || {
      RESUME+=("$nom|$ruff|$format|-|-|-|-|$((SECONDS - debut))|-|ÉCHEC"); echouer "$nom" "prérequis database/" "(database/) ${cmd[*]}"; }
  fi

  # Tests, ou actionlint.
  local plafond total=0 rc journal="$sortie/tests.log" junit="$sortie/junit.xml"
  plafond=$(plafond_sauts "$nom")
  local cmd=() env_tests=()
  case $lanceur in
    unittest) cmd=(uv run --locked python -m unittest discover -s tests -v) ;;
    pytest)
      # shellcheck disable=SC2206  # options et arguments : mots simples, sans espace
      cmd=(uv run --locked $options pytest $args --junitxml="$junit")
      if [ -n "$couv" ]; then
        cmd+=(--cov="$couv" --cov-report=xml:"$sortie/coverage.xml")
        env_tests=(COVERAGE_FILE="$sortie/.coverage" COVERAGE_RCFILE="$COUVERTURE")
      fi ;;
    actionlint)
      cmd=(uvx --from "actionlint-py==$ACTIONLINT" --with "shellcheck-py==$SHELLCHECK"
           actionlint -no-color)
      projet=. ;;
    ruff-seul) ;;
  esac

  if [ ${#cmd[@]} -gt 0 ]; then
    dire "$nom — (${projet}/) ${cmd[*]}"
    rm -f "$junit"
    (cd "$RACINE/$projet" && "${PROPRE[@]}" "${env_tests[@]}" "${cmd[@]}") 2>&1 | tee "$journal"
    rc=${PIPESTATUS[0]}
    case $lanceur in
      unittest)
        total=$(sed -n 's/^Ran \([0-9]*\) tests\{0,1\} in .*/\1/p' "$journal" | tail -1)
        sautes=$(grep -E '^(OK|FAILED) \(' "$journal" | grep -o 'skipped=[0-9]*' | grep -o '[0-9]*')
        local f e
        f=$(grep -E '^FAILED \(' "$journal" | grep -o 'failures=[0-9]*' | grep -o '[0-9]*')
        e=$(grep -E '^FAILED \(' "$journal" | grep -o 'errors=[0-9]*' | grep -o '[0-9]*')
        sautes=${sautes:-0}; echecs=$(( ${f:-0} + ${e:-0} )); total=${total:-0} ;;
      pytest)
        if [ -s "$junit" ]; then
          total=$(attribut_junit "$junit" tests)
          sautes=$(attribut_junit "$junit" skipped)
          echecs=$(( $(attribut_junit "$junit" failures) + $(attribut_junit "$junit" errors) ))
        fi ;;
    esac
    if [ "$lanceur" = unittest ] || [ "$lanceur" = pytest ]; then
      passes=$(( total - sautes - echecs ))
      if [ -f "$sortie/coverage.xml" ]; then
        couverture=$(grep -o '<coverage [^>]*' "$sortie/coverage.xml" | grep -o 'line-rate="[0-9.]*"' \
          | grep -o '[0-9.]*' | awk '{ printf "%.0f %%", $1 * 100 }')
      fi
    fi
    if [ "$rc" -ne 0 ] || { [ "$echecs" != - ] && [ "$echecs" -gt 0 ]; }; then
      RESUME+=("$nom|$ruff|$format|$passes|$sautes|$plafond|$echecs|$((SECONDS - debut))|$couverture|ÉCHEC")
      echouer "$nom" "tests (code $rc)" "(${projet}/) ${cmd[*]}"
    fi
    if [ "$lanceur" != actionlint ] && [ "$total" -eq 0 ]; then
      RESUME+=("$nom|$ruff|$format|0|0|$plafond|0|$((SECONDS - debut))|-|ÉCHEC")
      echouer "$nom" "aucun test collecté" "(${projet}/) ${cmd[*]}"
    fi
    if [ "$sautes" != - ] && [ "$sautes" -gt "$plafond" ]; then
      RESUME+=("$nom|$ruff|$format|$passes|$sautes|$plafond|$echecs|$((SECONDS - debut))|$couverture|ÉCHEC")
      [ -s "$junit" ] && grep -o 'classname="[^"]*" name="[^"]*" [^>]*><skipped [^>]*message="[^"]*"' "$junit" \
        | sed -E 's/classname="([^"]*)" name="([^"]*)".*message="([^"]*)"/  sauté : \1::\2 — \3/' >&2
      echouer "$nom" "$sautes tests sautés pour un plafond de $plafond (${SAUTS#"$RACINE"/})" \
        "(${projet}/) ${cmd[*]}"
    fi
  fi
  RESUME+=("$nom|$ruff|$format|$passes|$sautes|$plafond|$echecs|$((SECONDS - debut))|$couverture|ok")
}

choisir_modules() {  # [MODULE] → lignes de MODULES
  local l
  if [ -z "${1:-}" ]; then printf '%s\n' "${MODULES[@]}"; return; fi
  for l in "${MODULES[@]}"; do
    [ "${l%%|*}" = "$1" ] && { echo "$l"; return; }
  done
  echo "Module inconnu : $1. Modules : $(printf '%s ' "${MODULES[@]%%|*}")" >&2
  exit 2
}

verify() {
  local l lignes
  lignes="$(choisir_modules "${1:-}")" || exit 2
  while IFS= read -r l; do verifier_module "$l"; done <<<"$lignes"
}

# --------------------------------------------------------------------------- #
#  Indicatif : Bandit et pip-audit, tous les modules, sans arrêt ; jamais exigé
# --------------------------------------------------------------------------- #
indicatif() {
  local l lignes constats=0
  lignes="$(choisir_modules "${1:-}")" || exit 2
  printf '%-10s %-22s %-28s\n' module "Bandit H/M/L" "pip-audit vulnérabilités"
  while IFS= read -r l; do
    local nom lint projet lanceur
    IFS='|' read -r nom lint projet lanceur _ <<<"$l"
    [ "$lanceur" = unittest ] || [ "$lanceur" = pytest ] || continue
    local sortie="$SORTIES/$nom" exclus bandit audit
    mkdir -p "$sortie"
    exclus="$(cd "$RACINE/$lint" && find . -type d \( -name .venv -o -name tests \) -prune -printf '%p,' | sed 's/,$//')"
    (cd "$RACINE/$lint" && "${PROPRE[@]}" uvx "bandit@$BANDIT" -q -r . ${exclus:+-x "$exclus"} \
      -f json -o "$sortie/bandit.json") >"$sortie/bandit.log" 2>&1
    bandit=$(jq -r '.metrics._totals | "\(.["SEVERITY.HIGH"])/\(.["SEVERITY.MEDIUM"])/\(.["SEVERITY.LOW"])"' \
      "$sortie/bandit.json" 2>/dev/null || echo "erreur")
    (cd "$RACINE/$projet" && "${PROPRE[@]}" uv export --locked --all-extras --all-groups \
      --no-emit-project --no-emit-local --format requirements-txt -o "$sortie/verrou.txt" \
      && "${PROPRE[@]}" uvx "pip-audit@$PIP_AUDIT" -r "$sortie/verrou.txt" --no-deps --disable-pip \
        --progress-spinner off -f json -o "$sortie/audit.json") >"$sortie/audit.log" 2>&1
    audit=$(jq '[.dependencies[].vulns[]] | length' "$sortie/audit.json" 2>/dev/null || echo "erreur")
    printf '%-10s %-22s %-28s\n' "$nom" "$bandit" "$audit"
    [ "$bandit" = 0/0/0 ] && [ "$audit" = 0 ] || constats=$((constats + 1))
  done <<<"$lignes"
  printf '\n%s module(s) avec constats — indicatif, jamais exigé. Détail : %s/<module>/\n' \
    "$constats" "$SORTIES"
  [ "$constats" -eq 0 ]
}

# --------------------------------------------------------------------------- #
#  Poste : verify, puis les harnais qui ne tournent que sur ce poste
# --------------------------------------------------------------------------- #
poste() {
  verify
  local cmd
  cmd=(bash .githooks/tester.sh)
  dire "poste — ${cmd[*]}"
  (cd "$RACINE" && "${PROPRE[@]}" "${cmd[@]}") || echouer poste "hooks" "${cmd[*]}"
  cmd=(bash outils/fidelite.sh)
  dire "poste — (database/) ${cmd[*]}"
  (cd "$RACINE/database" && "${PROPRE[@]}" "${cmd[@]}") || echouer poste "fidélité du schéma" "(database/) ${cmd[*]}"
  cmd=(docker compose -f compose.integration.yml run --rm --build smoke)
  dire "poste — (02_api_manga/) ${cmd[*]}"
  local rc
  (cd "$RACINE/02_api_manga" && "${PROPRE[@]}" "${cmd[@]}"); rc=$?
  (cd "$RACINE/02_api_manga" && "${PROPRE[@]}" docker compose -f compose.integration.yml down -v >/dev/null 2>&1)
  [ "$rc" -eq 0 ] || echouer poste "intégration du 02 (code $rc)" "(02_api_manga/) ${cmd[*]}"
  dire "poste — harnais : tester.sh, fidelite.sh, Compose du 02 passés"
}

# --------------------------------------------------------------------------- #
[ -n "$RUFF" ] || { echo "Version de Ruff introuvable dans .pre-commit-config.yaml" >&2; exit 2; }
mkdir -p "$SORTIES"
action="${1:-verify}"; shift || true
case $action in
  verify|poste)
    volumes_debut
    trap terminer EXIT
    if [ "$action" = verify ]; then verify "${1:-}"; else poste; fi ;;
  indicatif) indicatif "${1:-}" ;;
  *) echo "Usage : $0 verify [MODULE] | indicatif [MODULE] | poste" >&2; exit 2 ;;
esac
