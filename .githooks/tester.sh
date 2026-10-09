#!/usr/bin/env bash
# Essai des hooks pre-push et pre-commit sur des dépôts jetables et un distant
# nu : chaque scénario fait un VRAI push (ou un VRAI commit) et vérifie qu'il
# passe ou qu'il est refusé.
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

# Fusions créées par GitHub : le committer prend l'identité technique de GitHub.
cote() {  # cote <n> : une branche latérale propre, d'un commit, à fusionner dans main
    git checkout -q -b "cote$1" && ecrire "cote$1.txt" "côté $1" && git commit -q -m "côté $1" &&
        git checkout -q main
}
par_github() {  # par_github <adresse> <commande git…> : committer GitHub, à cette adresse
    local adresse="$1"
    shift
    GIT_COMMITTER_NAME=GitHub GIT_COMMITTER_EMAIL="$adresse" git "$@"
}
scenario passe  "fusion de GitHub"                  eval 'cote 1 && par_github noreply@github.com merge -q --no-ff cote1 -m "Merge pull request #1"'
scenario refuse "commit ordinaire validé par GitHub" eval 'ecrire g.txt "x" && par_github noreply@github.com commit -q -m g'
scenario refuse "fusion de GitHub, auteur étranger" eval 'cote 2 && GIT_AUTHOR_NAME=Autre par_github noreply@github.com merge -q --no-ff cote2 -m "Merge pull request #2"'
scenario refuse "fusion de GitHub, ligne fautive"   eval 'cote 3 && git merge -q --no-ff --no-commit cote3 >/dev/null 2>&1 && ecrire h.md "rédigé avec $NOM Code" && par_github noreply@github.com commit -q -m "Merge pull request #3"'
scenario refuse "fusion validée par GitHub, autre adresse" eval 'cote 4 && par_github github@example.invalid merge -q --no-ff cote4 -m "Merge pull request #4"'
scenario refuse "fusion à trois parents validée par GitHub" eval 'cote 5 && cote 6 && par_github noreply@github.com merge -q --no-ff cote5 cote6 -m "Merge pull requests #5 et #6" >/dev/null'
# Le nom de profil GitHub de TheMaxfly n'est admis qu'en auteur d'une fusion créée par GitHub.
scenario passe  "fusion de GitHub d'auteur MaxFlavigny" eval 'cote 7 && GIT_AUTHOR_NAME=MaxFlavigny par_github noreply@github.com merge -q --no-ff cote7 -m "Merge pull request #7"'
scenario refuse "commit ordinaire d'auteur MaxFlavigny" eval 'ecrire m.txt "x" && git commit -q -m m --author="MaxFlavigny <max@example.invalid>"'
scenario refuse "fusion locale d'auteur MaxFlavigny"  eval 'cote 8 && GIT_AUTHOR_NAME=MaxFlavigny git merge -q --no-ff cote8 -m "fusion locale"'

# --------------------------------------------------------------------------- #
# Hook pre-commit — sur un SECOND clone : le premier a besoin de commits fautifs
# pour éprouver le pre-push, que le pre-commit refuserait. Chaque scénario
# tente un VRAI commit et vérifie qu'il passe ou qu'il est refusé.
# --------------------------------------------------------------------------- #
git clone -q "$TRAVAIL/distant.git" "$TRAVAIL/clone2" 2>/dev/null
cd "$TRAVAIL/clone2" || exit 1
git config user.name TheMaxfly
git config user.email max@example.invalid
cp "$HOOKS/pre-commit" .githooks/pre-commit
printf 'secret.md\n/docs/*\n!/docs/modeles/\n' >.git/info/exclude
ln -sf ../../.githooks/pre-commit .git/hooks/pre-commit
# Jeton du poste, dans un HOME jetable : le hook le lit sans jamais l'afficher.
export HOME="$TRAVAIL/maison"
mkdir -p "$HOME/.cache/huggingface"
printf 'jeton-du-poste-pour-l-essai-0042\n' >"$HOME/.cache/huggingface/token"
ASPECT="hf_$(printf 'Ab3%.0s' $(seq 1 11))x"  # hf_ + 34 caractères
git add .githooks && git commit -q -m "socle du pre-commit" 2>/dev/null &&
    git push -q origin HEAD:main 2>/dev/null
git branch -q -u origin/main

essai() {  # essai <attendu : passe|refuse> <libellé> <commande…>
    # Un refus ne compte que s'il vient du hook (son message) : un commit qui
    # échoue pour une autre raison (rien à valider…) est une erreur d'essai.
    local attendu="$1" libelle="$2" sortie
    shift 2
    if sortie=$("$@" 2>&1); then
        obtenu=passe
    elif grep -q "COMMIT REFUSÉ" <<<"$sortie"; then
        obtenu=refuse
    else
        obtenu="erreur d'essai"
    fi
    if [ "$obtenu" = "$attendu" ]; then
        echo "  ok    $libelle ($obtenu)"
    else
        echo "  ÉCHEC $libelle : attendu $attendu, obtenu $obtenu"
        echecs=$((echecs + 1))
    fi
    git reset -q --hard origin/main && git clean -fdq
}

echo "Essai du hook pre-commit"
essai passe  "commit ordinaire"                    eval 'ecrire pc_a.txt "bonjour" && git commit -q -m a'
essai refuse "nom de l'outil dans un fichier"      eval 'ecrire pc_c.md "rédigé avec $NOM Code" && git commit -q -m c'
essai passe  "nom de l'éditeur, fichier technique" eval 'ecrire tech.txt "from ${EDITEUR,,} import client" && git commit -q -m t'
essai refuse "nom de l'éditeur, fichier ordinaire" eval 'ecrire pc_d.py "import ${EDITEUR,,}" && git commit -q -m d'
essai refuse "marqueur d'auteur, fichier technique" eval 'ecrire tech.txt "$CO: quelqu un" && git commit -q -m t2'
essai refuse "fichier sous l'exclusion locale"     eval 'ecrire secret.md "x" && git commit -q -m s'
essai passe  "docs/ versionné exprès"              eval 'ecrire docs/permis.md "contenu du second essai" && git commit -q -m p'
essai refuse "autre fichier de docs/"              eval 'ecrire docs/autre.md "x" && git commit -q -m o'
essai refuse "jeton du poste en clair"             eval 'ecrire e.env "TOKEN=$(cat "$HOME/.cache/huggingface/token")" && git commit -q -m e'
essai refuse "chaîne d'aspect jeton"               eval 'ecrire f.env "TOKEN=$ASPECT" && git commit -q -m f'
# La ligne fautive est dans un commit NON POUSSÉ (posé sans le hook, comme le
# serait un commit d'avant son activation) ; l'amender avec un ajout propre
# doit être refusé — c'est le cas qu'un contrôle sur le seul écart à HEAD laisse
# passer.
essai refuse "amendement d'un commit fautif non poussé" eval '
    rm .git/hooks/pre-commit &&
    ecrire g.md "rédigé avec $NOM Code" && git commit -q -m g &&
    ln -sf ../../.githooks/pre-commit .git/hooks/pre-commit &&
    ecrire h.txt "propre" && git commit -q --amend -m "g et h"'
essai passe  "amendement d'un commit propre non poussé" eval '
    ecrire i.txt "propre" && git commit -q -m i &&
    ecrire j.txt "propre aussi" && git commit -q --amend -m "i et j"'
essai refuse "exclusion locale absente"            eval '
    mv .git/info/exclude .git/info/exclude.bak &&
    ecrire k.txt "x" && git commit -q -m k'
mv .git/info/exclude.bak .git/info/exclude 2>/dev/null || true

if [ "$echecs" -gt 0 ]; then
    echo "$echecs scénario(s) en échec"
    exit 1
fi
echo "Tous les scénarios tiennent."
