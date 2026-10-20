#!/usr/bin/env bash
# Vérification unique du dépôt : le même verdict sur le poste et sur GitHub.
#
#   verifier.sh verify [MODULE]     Ruff, format et tests des modules concernés par les
#                                   changements depuis `origin/develop` et de ceux qui
#                                   en dépendent (verification/dependances.tsv) ; tous
#                                   les modules si TOUT=1 ; un seul si MODULE est donné.
#                                   La tâche `statique` tourne toujours : Ruff du 06 et
#                                   de docs/modeles/, actionlint, shellcheck, essais des
#                                   hooks et du périmètre
#   verifier.sh indicatif [MODULE]  Bandit et pip-audit — jamais exigés
#   verifier.sh poste               mémoire d'abord ; verify complet, sauf si l'arbre,
#                                   propre, est déjà vert (FORCER=1 pour le rejouer) ;
#                                   puis les harnais qui ne tournent que sur le poste
#   verifier.sh perimetre           lit des chemins changés sur l'entrée, écrit les
#                                   modules concernés (liste JSON) : pour GitHub
#   verifier.sh graphe              contrôle que MODULES et le graphe se correspondent
#   verifier.sh coherence           MODULES, le graphe, la matrice de verify.yml et les
#                                   modules à base jetable se correspondent ; REGLES=1
#                                   (poste) : les contrôles obligatoires de l'ensemble
#                                   de règles de GitHub aussi, relevés par gh
#   verifier.sh equivalence         l'étape d'équivalence (poste) seule : le service,
#                                   puis l'API du modèle, sur la même instance
#   verifier.sh preparer MODULE     uv sync --locked de ce module (et de database/
#                                   s'il en dépend) : l'étape d'avant verify sur GitHub
#   verifier.sh image               tire, par empreinte, l'image PostgreSQL que les
#                                   fixtures des bases jetables désignent
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
#   - les exigences d'un module (9e champ de MODULES : Pyright strict, Bandit,
#     pip-audit) sont bloquantes : un constat est un échec. Un module neuf les porte
#     dès son premier commit ;
#   - les volumes Docker créés pendant la vérification, et eux seuls, sont
#     supprimés à la fin (les bases jetables des tests en laissent un chacune).
set -uo pipefail
export LC_ALL=C

# VERIFY_RACINE, VERIFY_MODULES, VERIFY_WORKFLOW : pour les essais du moteur
# (verification/tester_verifier.sh), sur un dépôt jetable et des modules factices.
RACINE="${VERIFY_RACINE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SORTIES="${VERIFY_SORTIES:-$RACINE/.verify}"
SAUTS="${VERIFY_SAUTS:-$RACINE/verification/sauts_attendus.tsv}"
GRAPHE="${VERIFY_GRAPHE:-$RACINE/verification/dependances.tsv}"
# Verdict du dernier `make verify` complet : l'arbre vérifié, s'il était propre et vert.
VERDICT="$SORTIES/verdict_complet"
COUVERTURE="$RACINE/verification/couverture.ini"
WORKFLOW="${VERIFY_WORKFLOW:-$RACINE/.github/workflows/verify.yml}"
# L'ensemble de règles qui protège `main` et `develop` (contrôles obligatoires).
ENSEMBLE_REGLES=24783901

# Outils lancés par uvx, à version fixée (absents des verrous des modules).
# Ruff : la version du pre-commit, lue à sa source pour ne jamais en diverger.
RUFF="$(sed -n '/ruff-pre-commit/,/rev:/s/^ *rev: *v//p' "$RACINE/.pre-commit-config.yaml")"
BANDIT=1.9.4
PIP_AUDIT=2.10.1
PYRIGHT=1.1.414       # paquet pyright[nodejs] : Node embarqué, sans dépendance au poste
ACTIONLINT=1.7.12.25  # paquet actionlint-py : actionlint 1.7.12
SHELLCHECK=0.11.0.1   # paquet shellcheck-py : shellcheck 0.11.0

# nom | dossier Ruff | dossier du projet (verrou, tests) | lanceur | options uv | arguments | couverture | prérequis | exigences
# Exigences (bloquantes, après les tests) : `pyright` (strict, configuré dans le
# pyproject du module), `bandit` (sur src/), `pip-audit` (sur le verrou), séparées
# par des virgules. Les modules d'avant E3 étape 2 n'en portent pas : leurs
# constats restent indicatifs (`make verify-indicatif`).
MODULES=(
  "01|01_scraping_manganews|01_scraping_manganews|pytest|--extra dev|tests|.|"
  "02|02_api_manga|02_api_manga|pytest|||.|"
  "03|03_kitsu_api_exports|03_kitsu_api_exports|pytest|--extra dev|tests|.|"
  "04|04_scraping_manga_sanctuary|04_scraping_manga_sanctuary|pytest|--extra dev|tests/|.|"
  "05|05_nettoyage_agregation_bdd|05_nettoyage_agregation_bdd|pytest|--extra dev||.|database"
  "07|07_databricks_manga_export|07_databricks_manga_export/lakehouse|pytest|--extra dev||.|"
  "08|08_enrichissement_personnages|08_enrichissement_personnages|pytest|--extra dev|tests|.|"
  "09|09_service_embedding|09_service_embedding|pytest|--extra dev||service_embedding|database"
  "10|10_mesures_recherche|10_mesures_recherche|pytest|--extra dev||mesures_recherche|database"
  "11|11_api_modele|11_api_modele|pytest|--extra dev||api_modele|database|pyright,bandit,pip-audit"
  "database|database|database|pytest|--extra dev|tests/|.|"
  "demo|demo|demo|pytest|--extra dev||.|"
  # Toujours, sans filtre : Ruff du 06 et de docs/modeles/, actionlint, shellcheck,
  # essais des hooks et du périmètre (voir verifier_statique).
  "statique|||statique||||"
)
if [ -n "${VERIFY_MODULES:-}" ]; then mapfile -t MODULES <"$VERIFY_MODULES"; fi

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
  printf '\n%-10s %-7s %-7s %8s %15s %8s %9s %8s %-10s %s\n' \
    module ruff format passés "sautés/plafond" échecs durée couv. exigences verdict
  local l
  # 11e champ, les exigences : absent des lignes qui n'en ont pas (« - »).
  for l in "${RESUME[@]}"; do
    IFS='|' read -r m r f p s pl e d c v x <<<"$l"
    printf '%-10s %-7s %-7s %8s %15s %8s %8ss %8s %-10s %s\n' "$m" "$r" "$f" "$p" "$s/$pl" "$e" "$d" "$c" "${x:--}" "$v"
  done
  {
    echo "| module | Ruff | format | passés | sautés / plafond | en échec | durée | couverture | exigences | verdict |"
    echo "|---|---|---|---:|---:|---:|---:|---:|---|---|"
    for l in "${RESUME[@]}"; do
      IFS='|' read -r m r f p s pl e d c v x <<<"$l"
      echo "| $m | $r | $f | $p | $s / $pl | $e | $d s | $c | ${x:--} | $v |"
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
  local nom lint projet lanceur options args couv prerequis exigences
  IFS='|' read -r nom lint projet lanceur options args couv prerequis exigences <<<"$1"
  if [ "$lanceur" = statique ]; then verifier_statique "$nom"; return; fi
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
  # Sur GitHub, `preparer` vient de le synchroniser (VERIFY_PREPARE=1) : pas deux fois.
  if [ "$prerequis" = database ] && [ "${VERIFY_PREPARE:-}" != 1 ]; then
    local cmd=(uv sync --locked --inexact)
    dire "$nom — prérequis database/ : ${cmd[*]}"
    (cd "$RACINE/database" && "${PROPRE[@]}" "${cmd[@]}") || {
      RESUME+=("$nom|$ruff|$format|-|-|-|-|$((SECONDS - debut))|-|ÉCHEC"); echouer "$nom" "prérequis database/" "(database/) ${cmd[*]}"; }
  fi

  # Tests.
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
    # Avant le code de sortie : pytest et unittest rendent 5 quand rien n'a tourné.
    if [ "$total" -eq 0 ]; then
      RESUME+=("$nom|$ruff|$format|0|0|$plafond|0|$((SECONDS - debut))|-|ÉCHEC")
      echouer "$nom" "aucun test collecté" "(${projet}/) ${cmd[*]}"
    fi
    if [ "$rc" -ne 0 ] || { [ "$echecs" != - ] && [ "$echecs" -gt 0 ]; }; then
      RESUME+=("$nom|$ruff|$format|$passes|$sautes|$plafond|$echecs|$((SECONDS - debut))|$couverture|ÉCHEC")
      echouer "$nom" "tests (code $rc)" "(${projet}/) ${cmd[*]}"
    fi
    if [ "$sautes" != - ] && [ "$sautes" -gt "$plafond" ]; then
      RESUME+=("$nom|$ruff|$format|$passes|$sautes|$plafond|$echecs|$((SECONDS - debut))|$couverture|ÉCHEC")
      [ -s "$junit" ] && grep -o 'classname="[^"]*" name="[^"]*" [^>]*><skipped [^>]*message="[^"]*"' "$junit" \
        | sed -E 's/classname="([^"]*)" name="([^"]*)".*message="([^"]*)"/  sauté : \1::\2 — \3/' >&2
      echouer "$nom" "$sautes tests sautés pour un plafond de $plafond (${SAUTS#"$RACINE"/})" \
        "(${projet}/) ${cmd[*]}"
    fi
  fi
  if [ "$nom" = 02 ] && ! integration_02; then
    RESUME+=("$nom|$ruff|$format|$passes|$sautes|$plafond|$echecs|$((SECONDS - debut))|$couverture|ÉCHEC")
    echouer "$nom" "intégration Compose" "(02_api_manga/) docker compose -f compose.integration.yml run --rm --build smoke"
  fi
  local exige=""
  if [ -n "$exigences" ]; then
    if ! exiger "$nom" "$projet" "$exigences"; then
      RESUME+=("$nom|$ruff|$format|$passes|$sautes|$plafond|$echecs|$((SECONDS - debut))|$couverture|ÉCHEC|$EXIGE")
      echouer "$nom" "exigence $REFUS" "(${projet}/) $COMMANDE_REFUS"
    fi
    exige=$EXIGE
  fi
  RESUME+=("$nom|$ruff|$format|$passes|$sautes|$plafond|$echecs|$((SECONDS - debut))|$couverture|ok|$exige")
}

# Les exigences d'un module (9e champ de MODULES), bloquantes, après ses tests :
# l'environnement du module existe alors (`uv run --locked`, ou `preparer` sur
# GitHub). Un constat, comme une panne d'outil, est un échec. Pour les essais du
# moteur, VERIFY_DOUBLURE_PYRIGHT, _BANDIT et _PIP_AUDIT désignent un exécutable qui
# remplace l'outil ; il reçoit le nom de l'exigence et le dossier du projet.
EXIGE=""          # exigences tenues / déclarées, pour le résumé
REFUS=""          # l'exigence refusée
COMMANDE_REFUS="" # sa commande
exiger() {  # exiger <nom> <projet> <exigences, séparées par des virgules>
  local nom="$1" projet="$2" sortie="$SORTIES/$1" exigence doublure faites=0 rc
  local -a liste cmd
  IFS=',' read -ra liste <<<"$3"
  : >"$sortie/exigences.log"
  for exigence in "${liste[@]}"; do
    doublure="VERIFY_DOUBLURE_${exigence^^}"
    doublure="${doublure//-/_}"
    case $exigence in
      pyright) cmd=(uvx --from "pyright[nodejs]==$PYRIGHT" pyright
        --pythonpath "$RACINE/$projet/.venv/bin/python" -p .) ;;
      bandit) cmd=(uvx "bandit@$BANDIT" -q -c pyproject.toml -r src) ;;
      pip-audit) cmd=(uvx "pip-audit@$PIP_AUDIT" -r "$sortie/verrou_exige.txt" --no-deps
        --disable-pip --strict --progress-spinner off) ;;
      *)
        EXIGE="$faites/${#liste[@]}"; REFUS="$exigence (inconnue)"
        COMMANDE_REFUS="9e champ de MODULES (verification/verifier.sh) : pyright, bandit, pip-audit"
        return 1 ;;
    esac
    [ -n "${!doublure:-}" ] && cmd=("${!doublure}" "$exigence" "$RACINE/$projet")
    dire "$nom — exigence $exigence : ${cmd[*]}"
    if [ "$exigence" = pip-audit ] && [ -z "${!doublure:-}" ]; then
      # Le verrou d'abord, en requirements épinglés et hachés (paquets locaux exclus).
      (cd "$RACINE/$projet" && "${PROPRE[@]}" uv export --locked --all-extras --all-groups \
        --no-emit-project --no-emit-local --format requirements-txt \
        -o "$sortie/verrou_exige.txt" >/dev/null) 2>&1 | tee -a "$sortie/exigences.log"
      rc=${PIPESTATUS[0]}
      if [ "$rc" -ne 0 ]; then
        EXIGE="$faites/${#liste[@]}"; REFUS="$exigence (export du verrou)"
        COMMANDE_REFUS="uv export --locked …"
        return 1
      fi
    fi
    (cd "$RACINE/$projet" && "${PROPRE[@]}" "${cmd[@]}") 2>&1 | tee -a "$sortie/exigences.log"
    rc=${PIPESTATUS[0]}
    if [ "$rc" -ne 0 ]; then
      EXIGE="$faites/${#liste[@]}"; REFUS="$exigence (code $rc)"; COMMANDE_REFUS="${cmd[*]}"
      return 1
    fi
    faites=$((faites + 1))
  done
  EXIGE="$faites/${#liste[@]}"
}

# L'intégration du 02 : PostgreSQL jetable, schéma rejoué depuis database/migrations/,
# rôle de lecture, API réelle, contrôles HTTP. Sur le poste comme sur GitHub.
integration_02() {
  local cmd=(docker compose -f compose.integration.yml run --rm -T --build smoke) rc
  dire "02 — (02_api_manga/) ${cmd[*]}"
  # Entrée fermée : `run` lirait sinon celle de l'appelant.
  (cd "$RACINE/02_api_manga" && "${PROPRE[@]}" "${cmd[@]}" </dev/null); rc=$?
  (cd "$RACINE/02_api_manga" && "${PROPRE[@]}" docker compose -f compose.integration.yml down -v >/dev/null 2>&1)
  return "$rc"
}

# La tâche statique : ce qui ne s'exécute dans aucun module. Chaque contrôle compte
# pour un « passé » ; le premier échec arrête.
verifier_statique() {
  local nom="$1" sortie="$SORTIES/$1" debut=$SECONDS controles=0 sortie_ruff=concise
  mkdir -p "$sortie"
  : >"$sortie/statique.log"
  [ "${GITHUB_ACTIONS:-}" = true ] && sortie_ruff=github
  controle() {  # controle <libellé> <commande…>
    local libelle="$1"
    shift
    dire "$nom — $libelle : $*"
    (cd "$RACINE" && "${PROPRE[@]}" "$@") 2>&1 | tee -a "$sortie/statique.log"
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
      RESUME+=("$nom|-|-|$controles|0|0|1|$((SECONDS - debut))|-|ÉCHEC")
      echouer "$nom" "$libelle" "$*"
    fi
    controles=$((controles + 1))
  }
  controle "Ruff du 06, de docs/modeles et de verification" uvx "ruff@$RUFF" check \
    --no-cache --output-format="$sortie_ruff" 06_benchmark_embeddings_llm docs/modeles verification
  controle "format du 06, de docs/modeles et de verification" uvx "ruff@$RUFF" format \
    --check --no-cache 06_benchmark_embeddings_llm docs/modeles verification
  controle "mutations versionnées : lisibles, texte visé présent une fois" \
    uv run --no-project python -I verification/rejouer_mutations.py --controler
  controle "actionlint" uvx --from "actionlint-py==$ACTIONLINT" --with "shellcheck-py==$SHELLCHECK" \
    actionlint -no-color
  controle "shellcheck des hooks et de verification/" uvx --from "shellcheck-py==$SHELLCHECK" \
    shellcheck -x -P .githooks .githooks/pre-commit .githooks/pre-push .githooks/cles-fournisseurs \
    .githooks/controler-texte \
    verification/verifier.sh verification/etat_apimanga.sh verification/tester_perimetre.sh \
    verification/tester_verifier.sh verification/controler_workflows.sh \
    sauvegarde/sauvegarder.sh sauvegarde/tester_sauvegarde.sh
  # tester.sh : ses `eval '…'` sont voulus (SC2016), et ses variables ne servent que
  # dans ces chaînes (SC2034).
  controle "shellcheck de tester.sh" uvx --from "shellcheck-py==$SHELLCHECK" \
    shellcheck -e SC2016,SC2034 .githooks/tester.sh
  controle "essais des hooks" bash .githooks/tester.sh
  controle "essais du périmètre" bash verification/tester_perimetre.sh
  controle "cohérence MODULES, graphe, matrice" bash verification/verifier.sh coherence
  controle "règles des workflows" bash verification/controler_workflows.sh
  controle "essais du moteur de vérification" bash verification/tester_verifier.sh
  controle "essais de la sauvegarde" bash sauvegarde/tester_sauvegarde.sh
  RESUME+=("$nom|ok|ok|$controles|0|0|0|$((SECONDS - debut))|-|ok")
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

# --------------------------------------------------------------------------- #
#  Périmètre : les modules qu'un changement concerne (verification/dependances.tsv)
# --------------------------------------------------------------------------- #
graphe_noms() { grep -v '^[[:space:]]*#' "$GRAPHE" | grep -v '^[[:space:]]*$' | cut -f1; }
prefixes_de() { awk -F'\t' -v m="$1" '$1 == m { print $2 }' "$GRAPHE"; }

graphe() {  # MODULES et le graphe se correspondent, un pour un
  local noms n ecarts=0
  noms="$(graphe_noms)"
  for n in "${MODULES[@]%%|*}"; do
    grep -qxF -- "$n" <<<"$noms" ||
      { echo "graphe : le module $n est absent de ${GRAPHE#"$RACINE"/}" >&2; ecarts=$((ecarts + 1)); }
  done
  while IFS= read -r n; do
    [ "$n" = '*' ] && continue
    printf '%s\n' "${MODULES[@]%%|*}" | grep -qxF -- "$n" ||
      { echo "graphe : $n n'est pas un module de MODULES (verifier.sh)" >&2; ecarts=$((ecarts + 1)); }
  done <<<"$noms"
  [ "$ecarts" -eq 0 ]
}

# MODULES, le graphe, la matrice de verify.yml et les modules à base jetable (ceux dont
# les tests chargent le harnais, et dont GitHub tire l'image) se correspondent. REGLES=1
# (poste) : les contrôles obligatoires de l'ensemble de règles aussi — un module neuf y
# entre dans la même étape —, et l'ensemble est actif, sans exemption.
coherence() {
  local ecarts=0 attendus matrice jetables declares l n projet exiges regles
  graphe || ecarts=$((ecarts + 1))
  attendus="$(printf '%s\n' "${MODULES[@]%%|*}" | grep -vx statique | sort)"
  matrice="$(sed -nE 's/^[[:space:]]*module:[[:space:]]*\[(.*)\][[:space:]]*$/\1/p' "$WORKFLOW" |
    tr -d '" ' | tr ',' '\n' | sed '/^$/d' | sort)"
  if [ "$attendus" != "$matrice" ]; then
    echo "cohérence : la matrice de ${WORKFLOW#"$RACINE"/} ≠ MODULES :" \
      "$(diff <(echo "$attendus") <(echo "$matrice") | grep '^[<>]' | paste -sd' ')" >&2
    ecarts=$((ecarts + 1))
  fi
  grep -qE '^  statique:' "$WORKFLOW" ||
    { echo "cohérence : pas de tâche statique dans ${WORKFLOW#"$RACINE"/}" >&2; ecarts=$((ecarts + 1)); }
  jetables="$(for l in "${MODULES[@]}"; do
      IFS='|' read -r n _ projet _ <<<"$l"
      [ -n "$projet" ] && grep -qs harnais_tests "$RACINE/$projet/tests/conftest.py" && echo "$n"
    done | sort)"
  declares="$(sed -nE "s/^[[:space:]]*MODULES_BASE_JETABLE:[[:space:]]*'(.*)'.*/\1/p" "$WORKFLOW" |
    tr -d '"[] ' | tr ',' '\n' | sed '/^$/d' | sort)"
  if [ "$jetables" != "$declares" ]; then
    echo "cohérence : modules à base jetable [$(paste -sd' ' <<<"$jetables")] ≠" \
      "MODULES_BASE_JETABLE [$(paste -sd' ' <<<"$declares")]" >&2
    ecarts=$((ecarts + 1))
  fi
  if [ "${REGLES:-}" = 1 ]; then
    exiges="$( { while IFS= read -r n; do echo "verify $n"; done <<<"$attendus"; echo statique; } | sort)"
    if ! regles="$(cd "$RACINE" && gh api "repos/{owner}/{repo}/rulesets/$ENSEMBLE_REGLES" --jq \
        '[.enforcement, (.bypass_actors | length | tostring)] + [.rules[] | select(.type == "required_status_checks") | .parameters.required_status_checks[].context] | .[]')"; then
      echo "cohérence : ensemble de règles $ENSEMBLE_REGLES illisible (gh)" >&2
      ecarts=$((ecarts + 1))
    else
      [ "$(sed -n 1p <<<"$regles")" = active ] ||
        { echo "cohérence : ensemble de règles $ENSEMBLE_REGLES inactif" >&2; ecarts=$((ecarts + 1)); }
      [ "$(sed -n 2p <<<"$regles")" = 0 ] ||
        { echo "cohérence : ensemble de règles $ENSEMBLE_REGLES avec exemption" >&2; ecarts=$((ecarts + 1)); }
      if [ "$(sed '1,2d' <<<"$regles" | sort)" != "$exiges" ]; then
        echo "cohérence : contrôles obligatoires ≠ tâches de verify.yml :" \
          "$(diff <(echo "$exiges") <(sed '1,2d' <<<"$regles" | sort) | grep '^[<>]' | paste -sd' ')" >&2
        ecarts=$((ecarts + 1))
      fi
    fi
  fi
  [ "$ecarts" -eq 0 ]
}

touche() {  # touche <préfixes…> : vrai si un chemin de $FICHIERS commence par l'un d'eux
  local p
  for p in "$@"; do
    awk -v p="$p" 'substr($0, 1, length(p)) == p { trouve = 1; exit } END { exit !trouve }' \
      <<<"$FICHIERS" && return 0
  done
  return 1
}

modules_concernes() {  # chemins changés sur l'entrée → lignes de MODULES concernées
  local l prefixes tous=0
  FICHIERS="$(sed '/^$/d')"
  graphe || exit 2
  # shellcheck disable=SC2046  # préfixes : mots simples, sans espace
  [ -n "$FICHIERS" ] && touche $(prefixes_de '*') && tous=1
  for l in "${MODULES[@]}"; do
    prefixes="$(prefixes_de "${l%%|*}")"
    # shellcheck disable=SC2086
    if [ "$prefixes" = "(toujours)" ] || [ "$tous" = 1 ] ||
        { [ -n "$FICHIERS" ] && touche $prefixes; }; then
      echo "$l"
    fi
  done
}

base_perimetre() { git -C "$RACINE" merge-base HEAD origin/develop 2>/dev/null; }

fichiers_touches() {  # <base> : changés depuis la base, arbre de travail compris
  {
    git -C "$RACINE" diff --name-only --no-renames "$1" HEAD
    git -C "$RACINE" diff --name-only --no-renames HEAD
    git -C "$RACINE" ls-files --others --exclude-standard
  } | sort -u
}

perimetre() {  # chemins sur l'entrée → liste JSON des modules concernés (TOUT=1 : tous)
  local lignes
  if [ "${TOUT:-}" = 1 ]; then
    graphe || exit 2
    lignes="$(choisir_modules)"
  else
    lignes="$(modules_concernes)" || exit 2
  fi
  cut -d'|' -f1 <<<"$lignes" | jq -Rsc 'split("\n") | map(select(length > 0))'
}

arbre_propre() { [ -z "$(git -C "$RACINE" status --porcelain)" ]; }
arbre() { git -C "$RACINE" rev-parse 'HEAD^{tree}'; }

verify() {
  local l lignes base fichiers complet=0 depart=""
  if [ -n "${1:-}" ]; then
    lignes="$(choisir_modules "$1")" || exit 2
  elif [ "${TOUT:-}" = 1 ]; then
    lignes="$(choisir_modules)"; complet=1
  elif ! base="$(base_perimetre)"; then
    dire "périmètre : origin/develop introuvable, tous les modules"
    lignes="$(choisir_modules)"; complet=1
  else
    fichiers="$(fichiers_touches "$base")"
    lignes="$(modules_concernes <<<"$fichiers")" || exit 2
    dire "périmètre : $(grep -c . <<<"$fichiers") fichier(s) changé(s) depuis origin/develop (${base:0:7}) ;" \
      "modules : $(cut -d'|' -f1 <<<"$lignes" | paste -sd' ')"
    if [ -z "$lignes" ]; then
      echo "aucun module concerné : rien à vérifier (TOUT=1 pour tout vérifier)"
      return 0
    fi
    [ "$(grep -c . <<<"$lignes")" -eq "${#MODULES[@]}" ] && complet=1
  fi
  # Un verdict ne vaut que pour un passage complet, sur un arbre propre du début à la fin.
  if [ "$complet" = 1 ]; then
    rm -f "$VERDICT"
    arbre_propre && depart="$(arbre)"
  fi
  # Un tableau, pas une lecture de l'entrée : une commande qui la lit ne peut pas
  # écourter la liste en silence.
  local liste
  mapfile -t liste <<<"$lignes"
  for l in "${liste[@]}"; do verifier_module "$l"; done
  [ "${#RESUME[@]}" -eq "${#liste[@]}" ] ||
    echouer "vérification" "${#RESUME[@]} module(s) traité(s) sur ${#liste[@]} prévus" "verify"
  if [ -n "$depart" ] && arbre_propre && [ "$(arbre)" = "$depart" ]; then
    printf '%s\t%s\n' "$depart" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$VERDICT"
    dire "verdict enregistré : arbre ${depart:0:12}, propre et vert (${VERDICT#"$RACINE"/})"
  fi
}

# --------------------------------------------------------------------------- #
#  Indicatif : Bandit et pip-audit, tous les modules, sans arrêt ; jamais exigé.
#  Les constats ne font pas échouer : seule une panne d'outil ou un rapport manquant le fait.
# --------------------------------------------------------------------------- #
indicatif() {
  # Réussit dès que Bandit et pip-audit ont tourné et produit leurs rapports, quel
  # que soit le nombre de constats ; échoue si un outil plante ou si un rapport manque.
  local l lignes pannes=() th=0 tm=0 tl=0 ta=0
  lignes="$(choisir_modules "${1:-}")" || exit 2
  local tableau=("| module | Bandit H / M / L | pip-audit : vulnérabilités | paquets non audités |"
                 "|---|---|---:|---:|")
  printf '%-10s %-18s %-26s %s\n' module "Bandit H/M/L" "pip-audit vulnérabilités" "non audités"
  while IFS= read -r l; do
    local nom lint projet lanceur
    IFS='|' read -r nom lint projet lanceur _ <<<"$l"
    [ "$lanceur" = unittest ] || [ "$lanceur" = pytest ] || continue
    local sortie="$SORTIES/$nom" exclus bandit audit non_audites erreurs
    mkdir -p "$sortie"
    # Un rapport d'une exécution précédente ne doit jamais compter.
    rm -f "$sortie/bandit.json" "$sortie/audit.json" "$sortie/verrou.txt"

    exclus="$(cd "$RACINE/$lint" && find . -type d \( -name .venv -o -name tests \) -prune -printf '%p,' | sed 's/,$//')"
    (cd "$RACINE/$lint" && "${PROPRE[@]}" uvx "bandit@$BANDIT" -q -r . ${exclus:+-x "$exclus"} \
      -f json -o "$sortie/bandit.json") >"$sortie/bandit.log" 2>&1
    if bandit=$(jq -er '.metrics._totals | "\(.["SEVERITY.HIGH"])/\(.["SEVERITY.MEDIUM"])/\(.["SEVERITY.LOW"])"' \
        "$sortie/bandit.json" 2>/dev/null); then
      IFS=/ read -r h m b <<<"$bandit"
      th=$((th + h)); tm=$((tm + m)); tl=$((tl + b))
      erreurs=$(jq '.errors | length' "$sortie/bandit.json")
      if [ "$erreurs" -gt 0 ]; then
        pannes+=("$nom : Bandit n'a pas pu analyser $erreurs fichier(s) — $sortie/bandit.json")
        bandit="$bandit, $erreurs erreur(s)"
      fi
    else
      bandit=panne
      pannes+=("$nom : Bandit n'a pas produit de rapport lisible — $sortie/bandit.log")
    fi

    audit=panne; non_audites=-
    if ! (cd "$RACINE/$projet" && "${PROPRE[@]}" uv export --locked --all-extras --all-groups \
        --no-emit-project --no-emit-local --format requirements-txt -o "$sortie/verrou.txt") \
        >"$sortie/audit.log" 2>&1; then
      pannes+=("$nom : export du verrou impossible — $sortie/audit.log")
    else
      (cd "$RACINE/$projet" && "${PROPRE[@]}" uvx "pip-audit@$PIP_AUDIT" -r "$sortie/verrou.txt" \
        --no-deps --disable-pip --progress-spinner off -f json -o "$sortie/audit.json") \
        >>"$sortie/audit.log" 2>&1
      if audit=$(jq -e '[.dependencies[].vulns[]] | length' "$sortie/audit.json" 2>/dev/null); then
        ta=$((ta + audit))
        non_audites=$(jq '[.dependencies[] | select(.skip_reason)] | length' "$sortie/audit.json")
      else
        audit=panne
        pannes+=("$nom : pip-audit n'a pas produit de rapport lisible — $sortie/audit.log")
      fi
    fi

    printf '%-10s %-18s %-26s %s\n' "$nom" "$bandit" "$audit" "$non_audites"
    tableau+=("| $nom | $bandit | $audit | $non_audites |")
  done <<<"$lignes"

  printf '%-10s %-18s %-26s\n' total "$th/$tm/$tl" "$ta"
  tableau+=("| **total** | **$th / $tm / $tl** | **$ta** | |")
  if [ ${#pannes[@]} -eq 0 ]; then
    printf '\nRapports produits pour chaque module : indicatif, jamais exigé. Détail : %s/<module>/\n' "$SORTIES"
  else
    printf '\nPANNE — %s rapport(s) manquant(s) ou incomplet(s) :\n' "${#pannes[@]}" >&2
    printf '  - %s\n' "${pannes[@]}" >&2
  fi
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
      echo "### Indicatif — constats par module et par outil (jamais exigé)"
      echo
      printf '%s\n' "${tableau[@]}"
      echo
      if [ ${#pannes[@]} -eq 0 ]; then echo "Rapports produits pour chaque module."
      else echo "**Panne** :"; printf -- '- %s\n' "${pannes[@]}"; fi
    } >>"$GITHUB_STEP_SUMMARY"
  fi
  [ ${#pannes[@]} -eq 0 ]
}

# --------------------------------------------------------------------------- #
#  Image : la PostgreSQL des bases jetables, lue dans les fixtures, tirée par empreinte
# --------------------------------------------------------------------------- #
# L'image n'est écrite qu'une fois : dans le harnais partagé des bases jetables.
FIXTURES_BASE=(
  database/harnais_tests/__init__.py
)

image() {
  local images
  images="$(cd "$RACINE" && "${PROPRE[@]}" uv run --no-project python -I - "${FIXTURES_BASE[@]}" <<'PY' | sort -u
import ast
import sys

for chemin in sys.argv[1:]:
    with open(chemin, encoding="utf-8") as fichier:
        arbre = ast.parse(fichier.read())
    for noeud in arbre.body:
        if isinstance(noeud, ast.Assign) and any(
            getattr(cible, "id", None) == "IMAGE_POSTGRES" for cible in noeud.targets
        ):
            print(ast.literal_eval(noeud.value))
PY
)"
  if [ "$(grep -c . <<<"$images")" -ne 1 ] || [[ $images != *@sha256:* ]]; then
    echo "Le harnais ne désigne pas une seule image par empreinte : ${images:-aucune}" >&2
    exit 1
  fi
  dire "image des bases jetables : docker pull $images"
  docker pull "$images"
}

# --------------------------------------------------------------------------- #
#  Préparer : l'environnement d'un module depuis son verrou (sur GitHub, avant verify)
# --------------------------------------------------------------------------- #
preparer() {
  local l nom lint projet lanceur options args couv prerequis exigences
  l="$(choisir_modules "$1")" || exit 2
  # shellcheck disable=SC2034  # exigences : lue pour que prerequis reste un seul champ
  IFS='|' read -r nom lint projet lanceur options args couv prerequis exigences <<<"$l"
  if [ "$prerequis" = database ]; then
    dire "$nom — prérequis database/ : uv sync --locked --inexact"
    (cd "$RACINE/database" && "${PROPRE[@]}" uv sync --locked --inexact) || exit 1
  fi
  if [ -z "$projet" ]; then echo "$nom : aucun environnement à préparer"; return 0; fi
  dire "$nom — (${projet}/) uv sync --locked --inexact $options"
  # shellcheck disable=SC2086  # options : mots simples, sans espace
  (cd "$RACINE/$projet" && "${PROPRE[@]}" uv sync --locked --inexact $options)
}

# --------------------------------------------------------------------------- #
#  Poste : verify, puis les harnais qui ne tournent que sur ce poste
# --------------------------------------------------------------------------- #

# Le DSN d'une étape du poste, lu dans verification/.env (sans mot de passe : il est
# dans ~/.pgpass) par cette étape seule.
dsn_du_poste() {  # dsn_du_poste <clé>
  local fichier="$RACINE/verification/.env"
  [ -r "$fichier" ] || return 1
  grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" "$fichier" | tail -n 1 |
    sed -E 's/^[^=]*=//; s/^[[:space:]]*//; s/[[:space:]]*$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/'
}

# L'étape d'équivalence, sur UNE instance EmbeddingGemma, levée puis arrêtée quoi
# qu'il arrive :
#   - le service (E3, étape 1, bloc F) : l'instance réencode les échantillons exportés
#     (09_…/mesures/equivalence/, hors dépôt) et les compare à leurs encodages ;
#   - l'API du modèle (E3, étape 2, bloc 03) : les 69 questions du jeu gelé, rejouées
#     par l'API, rendent les dix premières séries du run de référence. Seconde
#     exception de CONTRIBUTING.md : la base réelle, sous modele_api, en lecture
#     seule ; le DSN (APIMANGA_MODELE_DSN de verification/.env) n'est passé qu'à ce
#     test, qui échoue au lieu de sauter s'il ne peut pas comparer.
# Une vérification qui ne tourne pas est un échec : sans échantillon ou sans DSN,
# refus. Une seule instance à la fois : refus si une instance tourne déjà.
equivalence_du_service() {
  local module="$RACINE/09_service_embedding" api="$RACINE/11_api_modele" rc dsn
  compgen -G "$module/mesures/equivalence/encodage_*.json" >/dev/null ||
    echouer poste "équivalence du service : aucun échantillon exporté" \
      "(09_service_embedding/) uv run python -m service_embedding.equivalence exporter --encodage <n>"
  dsn="$(dsn_du_poste APIMANGA_MODELE_DSN)"
  [ -n "$dsn" ] ||
    echouer poste "équivalence de l'API : APIMANGA_MODELE_DSN absent de verification/.env" \
      "verification/.env.example"
  if docker ps --format '{{.Names}}' | grep -q '^tei-'; then
    echouer poste "équivalence du service" "une instance du service tourne déjà"
  fi
  (cd "$module" && "${PROPRE[@]}" uv run --locked python -m service_embedding.controles etat) ||
    echouer poste "équivalence du service" "mémoire sous les seuils"
  (cd "$api" && "${PROPRE[@]}" uv run --locked python -m api_modele.memoire) ||
    echouer poste "équivalence de l'API" "mémoire sous le seuil du service et de l'API"
  dire "poste — (09_service_embedding/) équivalence du service"
  (cd "$module" && "${PROPRE[@]}" docker compose --profile embeddinggemma up -d >/dev/null) ||
    echouer poste "équivalence du service" "docker compose --profile embeddinggemma up -d"
  for _ in $(seq 1 90); do curl -sf 127.0.0.1:8082/health >/dev/null && break; sleep 2; done
  (cd "$module" && "${PROPRE[@]}" uv run --locked --extra dev pytest -q -p no:cacheprovider \
    tests/test_equivalence.py -k service_reel -rs); rc=$?
  if [ "$rc" -eq 0 ]; then
    dire "poste — (11_api_modele/) équivalence de l'API : les 69 questions, sous modele_api"
    mkdir -p "$SORTIES/poste"
    (cd "$api" && "${PROPRE[@]}" API_MODELE_EGALITE_DSN="$dsn" API_MODELE_EGALITE_EXIGEE=1 \
      API_MODELE_EGALITE_SORTIE="$SORTIES/poste/egalite_api.json" \
      uv run --locked --extra dev pytest -q -p no:cacheprovider -s \
      tests/test_egalite_banc.py -rs); rc=$?
    [ "$rc" -eq 0 ] || rc=$((rc + 100))
  fi
  (cd "$module" && "${PROPRE[@]}" docker compose --profile embeddinggemma down >/dev/null 2>&1)
  [ "$rc" -lt 100 ] || echouer poste "équivalence de l'API (code $((rc - 100)))" \
    "(11_api_modele/) pytest tests/test_egalite_banc.py"
  [ "$rc" -eq 0 ] || echouer poste "équivalence du service (code $rc)" "pytest tests/test_equivalence.py"
}

# Les seuils de mémoire de l'équivalence, contrôlés avant toute chose : un passage ne
# doit pas échouer sur une condition connue dès la première seconde.
memoire_d_abord() {
  local module="$RACINE/09_service_embedding" etat
  dire "poste — mémoire, avant toute chose (seuils de l'équivalence)"
  if ! etat="$(cd "$module" && "${PROPRE[@]}" uv run --locked python -m service_embedding.controles etat)"; then
    printf '%s\n' "$etat"
    echouer poste "mémoire sous les seuils : rien n'a été lancé" \
      "libérer de la mémoire (5 Go disponibles, 4 Go de mémoire vidéo libre), puis make verify-poste"
  fi
  printf '%s\n' "$etat"
  # L'API du modèle s'ajoute au service pendant l'équivalence : son pic de démarrage
  # (11_api_modele/config/reglages.toml, [memoire]) s'ajoute au seuil du service.
  if ! etat="$(cd "$RACINE/11_api_modele" && "${PROPRE[@]}" uv run --locked python -m api_modele.memoire)"; then
    printf '%s\n' "$etat"
    echouer poste "mémoire sous le seuil du service et de l'API : rien n'a été lancé" \
      "libérer de la mémoire (6,6 Go disponibles), puis make verify-poste"
  fi
  printf '%s\n' "$etat"
}

# L'arbre courant, propre, a-t-il déjà un verdict vert complet ? (FORCER=1 : non.)
arbre_deja_vert() {
  local arbre_vert date_vert
  [ "${FORCER:-}" = 1 ] && return 1
  [ -f "$VERDICT" ] && arbre_propre || return 1
  IFS=$'\t' read -r arbre_vert date_vert <"$VERDICT"
  [ "$arbre_vert" = "$(arbre)" ] || return 1
  dire "poste — arbre ${arbre_vert:0:12} déjà vert ($date_vert) : make verify n'est pas rejoué (FORCER=1 pour le rejouer)"
}

poste() {
  memoire_d_abord
  arbre_deja_vert || TOUT=1 verify
  # Les essais des hooks (tâche statique) et l'intégration Compose du 02 font partie de
  # make verify : ils tournent aussi sur GitHub. Restent ici les harnais du poste seul.
  local cmd
  cmd=(bash outils/fidelite.sh)
  dire "poste — (database/) ${cmd[*]}"
  (cd "$RACINE/database" && "${PROPRE[@]}" "${cmd[@]}") || echouer poste "fidélité du schéma" "(database/) ${cmd[*]}"
  # La seule étape qui lit la base réelle : sous verif_ro, en lecture seule.
  cmd=(bash verification/etat_apimanga.sh)
  dire "poste — ${cmd[*]}"
  (cd "$RACINE" && "${PROPRE[@]}" "${cmd[@]}") || echouer poste "état d'apimanga" "${cmd[*]}"
  equivalence_du_service
  dire "poste — cohérence avec l'ensemble de règles de GitHub (gh)"
  REGLES=1 coherence || echouer poste "cohérence" "REGLES=1 bash verification/verifier.sh coherence"
  dire "poste — harnais : fidelite.sh, état d'apimanga, équivalence du service et de l'API, ensemble de règles passés"
}

# --------------------------------------------------------------------------- #
[ -n "$RUFF" ] || { echo "Version de Ruff introuvable dans .pre-commit-config.yaml" >&2; exit 2; }
mkdir -p "$SORTIES"
action="${1:-verify}"; shift || true
case $action in
  verify|poste|equivalence)
    volumes_debut
    trap terminer EXIT
    case $action in
      verify) verify "${1:-}" ;;
      poste) poste ;;
      equivalence) equivalence_du_service ;;
    esac ;;
  coherence) coherence && echo "cohérence : MODULES, graphe, matrice et modules à base jetable" \
    "se correspondent${REGLES:+, ensemble de règles compris}" ;;
  indicatif) indicatif "${1:-}" ;;
  preparer) preparer "${1:?Usage : $0 preparer MODULE}" ;;
  image) image ;;
  perimetre) perimetre ;;
  graphe) graphe && echo "graphe : ${#MODULES[@]} modules, tous dans ${GRAPHE#"$RACINE"/}, et eux seuls" ;;
  *) echo "Usage : $0 verify [MODULE] | indicatif [MODULE] | poste | equivalence | preparer MODULE | image | perimetre | graphe | coherence" >&2; exit 2 ;;
esac
