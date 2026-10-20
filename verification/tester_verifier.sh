#!/usr/bin/env bash
# Essais du moteur de vérification (verifier.sh) sur un dépôt jetable et des modules
# factices : ce que les règles promettent, provoqué une à une.
#
#   bash verification/tester_verifier.sh      → code 0 si tous les scénarios tiennent
#
#   - environnement vidé : ni VIRTUAL_ENV, ni APIMANGA_DSN, ni DATABASE_URL hérités ;
#   - arrêt au premier échec, avec le module et la commande en cause ;
#   - plafond de tests sautés : au-delà, échec ; à hauteur, vert ;
#   - aucun test collecté : échec ;
#   - volumes Docker créés pendant la vérification : supprimés (si Docker répond) ;
#   - équivalence du service sans échantillon : échec (verify-poste) ;
#   - cohérence MODULES ↔ graphe ↔ matrice de verify.yml ;
#   - règles des workflows (controler_workflows.sh), chaque écart provoqué.
set -uo pipefail

ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$(cd "$ICI/.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
D="$T/depot"
echecs=0

mkdir -p "$D/verification"
cp "$ICI/verifier.sh" "$D/verification/verifier.sh"
cp "$SOURCE/.pre-commit-config.yaml" "$D/.pre-commit-config.yaml"
: >"$D/verification/sauts_attendus.tsv"

module() {  # module <nom> <corps des tests, Python> : un projet uv sans dépendance
  mkdir -p "$D/$1/tests"
  printf '[project]\nname = "%s"\nversion = "0"\nrequires-python = ">=3.12"\ndependencies = []\n\n[tool.uv]\npackage = false\n' \
    "essai-$1" >"$D/$1/pyproject.toml"
  [ -n "$2" ] && printf '%s\n' "$2" >"$D/$1/tests/test_essai.py"
  (cd "$D/$1" && uv lock -q --offline 2>/dev/null || uv lock -q) || {
    echo "  ÉCHEC préparation du module factice $1"; echecs=$((echecs + 1)); }
}
ligne() { printf '%s|%s|%s|unittest||||\n' "$1" "$1" "$1"; }  # une ligne de MODULES

verifier() {  # verifier <fichier MODULES> <arguments…> : sortie dans $T/sortie, code rendu
  VERIFY_RACINE="$D" VERIFY_MODULES="$1" VERIFY_SORTIES="$T/sorties" \
    VERIFY_SAUTS="$D/verification/sauts_attendus.tsv" \
    bash "$D/verification/verifier.sh" "${@:2}" >"$T/sortie" 2>&1
}

constat() {  # constat <libellé> <condition…>
  local libelle="$1"
  shift
  if "$@"; then
    echo "  ok    $libelle"
  else
    echo "  ÉCHEC $libelle"
    sed 's/^/        | /' "$T/sortie" | tail -15
    echecs=$((echecs + 1))
  fi
}
dit() { grep -qF -- "$1" "$T/sortie"; }

echo "Essai du moteur de vérification"

# --- Environnement vidé ------------------------------------------------------ #
module env_vide 'import os
import unittest


class Essai(unittest.TestCase):
    def test_rien_n_est_herite(self):
        # uv run pose le VIRTUAL_ENV du module : seul celui de l appelant est exclu.
        self.assertNotEqual(os.environ.get("VIRTUAL_ENV"), "/nulle/part")
        for v in ("APIMANGA_DSN", "DATABASE_URL", "SECRET_D_ESSAI"):
            self.assertNotIn(v, os.environ)'
ligne env_vide >"$T/m_env"
VIRTUAL_ENV=/nulle/part APIMANGA_DSN=postgresql://reelle DATABASE_URL=postgresql://reelle \
  SECRET_D_ESSAI=x verifier "$T/m_env" verify env_vide
rc=$?
constat "environnement vidé : rien d'hérité" [ "$rc" -eq 0 ]

# --- Arrêt au premier échec --------------------------------------------------- #
module rouge 'import unittest


class Essai(unittest.TestCase):
    def test_echoue(self):
        self.fail("voulu")'
module vert 'import unittest


class Essai(unittest.TestCase):
    def test_passe(self):
        pass'
{ ligne rouge; ligne vert; } >"$T/m_arret"
verifier "$T/m_arret" verify
rc=$?
constat "arrêt au premier échec : code non nul" [ "$rc" -ne 0 ]
constat "arrêt au premier échec : le module en cause nommé" dit "ÉCHEC — module rouge : tests"
constat "arrêt au premier échec : le module suivant n'est pas lancé" bash -c "! grep -qF '==> vert' '$T/sortie'"

# --- Plafond de sautés -------------------------------------------------------- #
module sautes 'import unittest


class Essai(unittest.TestCase):
    @unittest.skip("voulu")
    def test_un(self):
        pass

    @unittest.skip("voulu")
    def test_deux(self):
        pass

    def test_trois(self):
        pass'
ligne sautes >"$T/m_sautes"
printf 'sautes\t1\tessai\n' >"$D/verification/sauts_attendus.tsv"
verifier "$T/m_sautes" verify sautes
rc=$?
constat "plafond dépassé : échec" [ "$rc" -ne 0 ]
constat "plafond dépassé : le dit" dit "2 tests sautés pour un plafond de 1"
printf 'sautes\t2\tessai\n' >"$D/verification/sauts_attendus.tsv"
verifier "$T/m_sautes" verify sautes
rc=$?
constat "plafond tenu : vert" [ "$rc" -eq 0 ]
: >"$D/verification/sauts_attendus.tsv"

# --- Aucun test collecté ------------------------------------------------------ #
module vide ''
ligne vide >"$T/m_vide"
verifier "$T/m_vide" verify vide
rc=$?
constat "aucun test collecté : échec" [ "$rc" -ne 0 ]
constat "aucun test collecté : le dit" dit "aucun test collecté"

# --- Volumes Docker ------------------------------------------------------------ #
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  VOLUME="essai-verificateur-$$"
  module volume "import subprocess
import unittest


class Essai(unittest.TestCase):
    def test_laisse_un_volume(self):
        commande = [\"docker\", \"volume\", \"create\", \"$VOLUME\"]
        subprocess.run(commande, check=True, capture_output=True)"
  ligne volume >"$T/m_volume"
  verifier "$T/m_volume" verify volume
  rc=$?
  constat "volume créé pendant la vérification : vert" [ "$rc" -eq 0 ]
  constat "volume créé pendant la vérification : supprimé" \
    bash -c "! docker volume inspect '$VOLUME' >/dev/null 2>&1"
  docker volume rm "$VOLUME" >/dev/null 2>&1 || true
else
  echo "  (volumes : Docker indisponible, scénario non joué)"
fi

# --- Équivalence du service sans échantillon ----------------------------------- #
verifier "$T/m_env" equivalence
rc=$?
constat "équivalence sans échantillon : échec" [ "$rc" -ne 0 ]
constat "équivalence sans échantillon : le dit" dit "aucun échantillon"

# --- Cohérence MODULES ↔ graphe ↔ matrice ------------------------------------- #
{ ligne vert; ligne rouge; } >"$T/m_coherence"
printf 'vert\tvert/\nrouge\trouge/\nstatique\t(toujours)\n' >"$T/graphe.tsv"
echo "statique|||statique||||" >>"$T/m_coherence"
ecrire_workflow() {  # ecrire_workflow <modules de la matrice> <modules à base jetable>
  cat >"$T/verify.yml" <<EOF
env:
  MODULES_BASE_JETABLE: '[$2]'
jobs:
  module:
    strategy:
      matrix:
        module: [$1]
  statique:
    name: statique
EOF
}
coherence() {
  VERIFY_GRAPHE="$T/graphe.tsv" VERIFY_WORKFLOW="$T/verify.yml" verifier "$T/m_coherence" coherence
}
ecrire_workflow '"vert", "rouge"' ''
coherence
rc=$?
constat "cohérence : MODULES, graphe et matrice égaux" [ "$rc" -eq 0 ]
ecrire_workflow '"vert"' ''
coherence
rc=$?
constat "cohérence : un module absent de la matrice, refus" [ "$rc" -ne 0 ]
ecrire_workflow '"vert", "rouge", "autre"' ''
coherence
rc=$?
constat "cohérence : un module de la matrice inconnu, refus" [ "$rc" -ne 0 ]
printf 'vert\tvert/\nstatique\t(toujours)\n' >"$T/graphe.tsv"
ecrire_workflow '"vert", "rouge"' ''
coherence
rc=$?
constat "cohérence : un module absent du graphe, refus" [ "$rc" -ne 0 ]
printf 'vert\tvert/\nrouge\trouge/\nstatique\t(toujours)\n' >"$T/graphe.tsv"
mkdir -p "$D/rouge/tests" && echo "# charge harnais_tests" >"$D/rouge/tests/conftest.py"
ecrire_workflow '"vert", "rouge"' ''
coherence
rc=$?
constat "cohérence : un module à base jetable dont GitHub ne tire pas l'image, refus" [ "$rc" -ne 0 ]
ecrire_workflow '"vert", "rouge"' '"rouge"'
coherence
rc=$?
constat "cohérence : image tirée pour chaque module à base jetable" [ "$rc" -eq 0 ]

# --- Règles des workflows ------------------------------------------------------ #
echo "Essai des règles des workflows"
W="$T/workflows"
regle() {  # regle <attendu : passe|refuse> <libellé> <sed appliqué à verify.yml>
  rm -rf "$W" && cp -r "$SOURCE/.github/workflows" "$W"
  [ -n "$3" ] && sed -i -E "$3" "$W/verify.yml"
  if bash "$ICI/controler_workflows.sh" "$W" >"$T/sortie" 2>&1; then obtenu=passe; else obtenu=refuse; fi
  constat "$2 ($1)" [ "$obtenu" = "$1" ]
}
regle passe  "les workflows du dépôt" ''
regle refuse "action épinglée par étiquette" '0,/actions\/checkout@[0-9a-f]{40}/s//actions\/checkout@v4/'
regle refuse "exécuteur mobile" '0,/runs-on: ubuntu-24.04/s//runs-on: ubuntu-latest/'
regle refuse "permissions absentes" '/^permissions:/,/^  contents: read/d'
regle refuse "permission en écriture, à côté de la lecture" 's/^  contents: read/  contents: read\n  pull-requests: write/'
regle refuse "jeton persistant" '0,/persist-credentials: false/s//persist-credentials: true/'
regle refuse "échec avalé (continue-on-error)" '0,/timeout-minutes: 5/s//timeout-minutes: 5\n    continue-on-error: true/'
# shellcheck disable=SC2016  # « $MODULE » : le texte du workflow, pas une variable d'ici
regle refuse "échec avalé (|| true)" '0,/run: make verify MODULE="\$MODULE"/s//run: make verify MODULE="$MODULE" || true/'

if [ "$echecs" -gt 0 ]; then
  echo "$echecs scénario(s) en échec"
  exit 1
fi
echo "Tous les scénarios tiennent."
