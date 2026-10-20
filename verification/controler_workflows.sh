#!/usr/bin/env bash
# Les règles des workflows de GitHub, que ni actionlint ni GitHub ne vérifient :
#
#   - chaque action est épinglée par son empreinte (40 caractères hexadécimaux), jamais
#     par une étiquette mobile ; l'exécuteur est une version figée, jamais `-latest` ;
#   - chaque workflow déclare `permissions:` au premier niveau, `contents: read`, et
#     n'accorde aucune écriture ;
#   - chaque récupération du dépôt (actions/checkout) porte
#     `persist-credentials: false` : le jeton ne reste pas sur le disque ;
#   - aucun échec n'est avalé : ni `continue-on-error`, ni `|| true`, hors de
#     `indicatif.yml`, dont c'est l'objet (constats jamais exigés).
#
#   bash verification/controler_workflows.sh [dossier]   (défaut : .github/workflows)
#
# Code 0 si toutes les règles tiennent ; sinon, chaque écart nommé, et code 1.
set -uo pipefail

racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dossier="${1:-$racine/.github/workflows}"
ecarts=()

shopt -s nullglob
workflows=("$dossier"/*.yml "$dossier"/*.yaml)
[ ${#workflows[@]} -gt 0 ] || { echo "workflows : aucun dans $dossier" >&2; exit 1; }

for w in "${workflows[@]}"; do
  nom="$(basename "$w")"
  # Actions épinglées par empreinte (une action locale, ./…, n'a pas de version).
  while IFS= read -r action; do
    case $action in
      ./*) ;;
      *@*) [[ ${action##*@} =~ ^[0-9a-f]{40}$ ]] ||
             ecarts+=("$nom : action non épinglée par empreinte : $action") ;;
      *) ecarts+=("$nom : action sans version : $action") ;;
    esac
  done < <(sed -nE 's/^[[:space:]-]*uses:[[:space:]]*([^[:space:]#]+).*/\1/p' "$w")
  # Exécuteur figé.
  while IFS= read -r l; do
    ecarts+=("$nom : exécuteur non figé, ligne ${l%%:*}")
  done < <(grep -nE '^[[:space:]]*runs-on:.*latest' "$w")
  # Permissions du workflow, en lecture seule.
  awk '/^permissions:/ { p = 1; next } p && /^[^[:space:]]/ { p = 0 }
       p && /contents:[[:space:]]*read/ { lu = 1 } END { exit !lu }' "$w" ||
    ecarts+=("$nom : pas de « permissions: contents: read » au premier niveau")
  grep -qE ':[[:space:]]*write([[:space:]]|$)|write-all' "$w" &&
    ecarts+=("$nom : une permission en écriture est accordée")
  # Le jeton de la récupération du dépôt ne reste pas sur le disque.
  recuperations="$(grep -cE '^[[:space:]-]*uses:[[:space:]]*actions/checkout@' "$w")"
  sans_jeton="$(grep -cE '^[[:space:]]*persist-credentials:[[:space:]]*false' "$w")"
  [ "$recuperations" -eq "$sans_jeton" ] ||
    ecarts+=("$nom : $recuperations récupération(s) du dépôt, $sans_jeton avec « persist-credentials: false »")
  # Aucun échec avalé, hors d'indicatif.yml.
  if [ "$nom" != indicatif.yml ]; then
    while IFS= read -r l; do
      ecarts+=("$nom : échec avalé, ligne ${l%%:*}")
    done < <(grep -nE 'continue-on-error|\|\|[[:space:]]*true' "$w")
  fi
done

if [ ${#ecarts[@]} -gt 0 ]; then
  echo "workflows : ${#ecarts[@]} écart(s) aux règles :" >&2
  printf '  - %s\n' "${ecarts[@]}" >&2
  exit 1
fi
echo "workflows : ${#workflows[@]} contrôlé(s) — actions épinglées, exécuteurs figés," \
  "lecture seule, jeton non persistant, aucun échec avalé"
