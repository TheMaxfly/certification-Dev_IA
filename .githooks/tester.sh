#!/usr/bin/env bash
# Essai du hook pre-push sur un dépôt jetable et un distant nu : chaque scénario
# fait un VRAI push et vérifie qu'il passe ou qu'il est refusé.
#   bash .githooks/tester.sh      → code 0 si tous les scénarios tiennent
# Les mots interdits sont construits à l'exécution : ce fichier ne les contient pas.
set -uo pipefail

HOOKS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRAVAIL="$(mktemp -d)"
trap 'rm -rf "$TRAVAIL"' EXIT
NOM="Cl$(printf a)ude"
EDITEUR="Anthr$(printf o)pic"
CO="Co-Author$(printf e)d-By"
echecs=0

git init -q --bare -b main "$TRAVAIL/distant.git"
git clone -q "$TRAVAIL/distant.git" "$TRAVAIL/clone" 2>/dev/null
cd "$TRAVAIL/clone" || exit 1
git config user.name TheMaxfly
git config user.email max@example.invalid
git checkout -q -b main
mkdir -p .githooks
cp "$HOOKS/pre-push" .githooks/pre-push
printf 'tech.txt\n' >.githooks/mentions-techniques
printf 'docs/permis.md\n' >.githooks/docs-versionnes
printf 'secret.md\n/docs/*\n!/docs/modeles/\n' >.git/info/exclude
ln -sf ../../.githooks/pre-push .git/hooks/pre-push
git add .githooks && git commit -q -m "socle"

scenario() {  # scenario <attendu : passe|refuse> <libellé> <commande…>
    local attendu="$1" libelle="$2"
    shift 2
    "$@"
    if git push -q origin main 2>/dev/null; then obtenu=passe; else obtenu=refuse; fi
    if [ "$obtenu" = "$attendu" ]; then
        echo "  ok    $libelle ($obtenu)"
    else
        echo "  ÉCHEC $libelle : attendu $attendu, obtenu $obtenu"
        echecs=$((echecs + 1))
    fi
    git fetch -q origin && git reset -q --hard origin/main 2>/dev/null
}

ecrire() { mkdir -p "$(dirname "$1")"; printf '%s\n' "$2" >"$1"; git add -f "$1"; }

echo "Essai du hook pre-push"
scenario passe  "premier push, socle propre"        true
scenario passe  "commit ordinaire"                  eval 'ecrire a.txt "bonjour" && git commit -q -m "ajouter a"'
scenario refuse "co-auteur dans le message"         eval 'ecrire b.txt "x" && git commit -q -m "b" -m "$CO: $NOM <x@y>"'
scenario refuse "nom de l'outil dans un fichier"    eval 'ecrire c.md "rédigé avec $NOM Code" && git commit -q -m c'
scenario passe  "nom de l'éditeur, fichier technique" eval 'ecrire tech.txt "import ${EDITEUR,,}" && git commit -q -m tech'
scenario refuse "nom de l'éditeur, fichier ordinaire" eval 'ecrire d.py "import ${EDITEUR,,}" && git commit -q -m d'
scenario refuse "marqueur d'auteur, fichier technique" eval 'ecrire tech.txt "$CO: quelqu un" && git commit -q -m tech2'
scenario refuse "autre auteur"                      eval 'ecrire e.txt "x" && git commit -q -m e --author="Autre <a@b.invalid>"'
scenario refuse "fichier sous l'exclusion locale"   eval 'ecrire secret.md "x" && git commit -q -m secret'
scenario passe  "docs/ versionné exprès"            eval 'ecrire docs/permis.md "x" && git commit -q -m permis'
scenario passe  "docs/modeles/ réinclus"            eval 'ecrire docs/modeles/m.md "x" && git commit -q -m modele'
scenario refuse "autre fichier de docs/"            eval 'ecrire docs/autre.md "x" && git commit -q -m autre'
scenario refuse "exclusion locale absente"          eval 'ecrire f.txt "x" && git commit -q -m f && mv .git/info/exclude .git/info/exclude.bak'
mv .git/info/exclude.bak .git/info/exclude 2>/dev/null || true

if [ "$echecs" -gt 0 ]; then
    echo "$echecs scénario(s) en échec"
    exit 1
fi
echo "Tous les scénarios tiennent."
