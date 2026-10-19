#!/usr/bin/env bash
# État d'apimanga, en lecture seule — une étape de `make verify-poste`.
#
#   bash verification/etat_apimanga.sh            contrôle l'état contre la référence
#   bash verification/etat_apimanga.sh --relever  écrit la référence (acte voulu, commité)
#
# La seule étape de la vérification qui lit la base réelle. Elle s'y connecte sous le
# rôle `verif_lecture`, membre de `verif_ro` (migration 022) : SELECT sur `manga`,
# `bench` et le suivi des migrations, rien d'autre, et des sessions en lecture seule.
# Le DSN est lu dans `verification/.env` (APIMANGA_VERIF_DSN, sans mot de passe : il
# est dans ~/.pgpass) par cette étape seule, et n'est passé qu'aux tests qu'elle nomme.
# L'identité de la session est contrôlée avant toute lecture : lecture seule, membre
# de verif_ro, pas superutilisateur — sinon, refus.
#
# Ce qu'elle vérifie, contre `verification/etat_apimanga.tsv` (versionné) :
#   - les empreintes des corpus v1 et v2 (documents, fragments, vecteurs, encodages),
#     de `eval_mesures`, de `eval_runs`, du journal des promotions ;
#   - le jeu v2, l'encodage en service, les corpus et leur clôture ;
#   - les migrations appliquées, égales aux fichiers (versions et sommes de contrôle).
# Puis le contrôle du module 09 (`service_embedding.verification`), et les 11 tests qui
# sautent ailleurs faute de base : 4 du 05, 3 du 08, 4 de `demo`. Ils doivent passer,
# aucun ne doit sauter.
#
# Une nouvelle mesure, une promotion ou une migration change l'état : on relève la
# référence (--relever) et on la commite avec ce qui l'a changée.
set -uo pipefail
export LC_ALL=C

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REFERENCE="$RACINE/verification/etat_apimanga.tsv"
FICHIER_ENV="$RACINE/verification/.env"
SORTIES="${VERIFY_SORTIES:-$RACINE/.verify}/poste"
mkdir -p "$SORTIES"

# Les tests qui ne tournent que sur la base réelle : <dossier> <variable du DSN> <ids…>
TESTS_05=(
  tests/test_controle_tetes.py::test_les_cinquante_tetes_du_catalogue_ont_un_kitsu_id_ou_une_raison
  tests/test_rattachement_apimanga.py::test_atteignables_identiques_avant_et_apres
  tests/test_rattachement_apimanga.py::test_chaque_fragment_a_une_entite_et_une_seule
  tests/test_rattachement_apimanga.py::test_entites_du_corpus
)
TESTS_08=(
  tests/test_chargeur_kitsu_fidelite.py::test_1_egalite_des_identifiants_source
  tests/test_chargeur_kitsu_fidelite.py::test_2_egalite_des_paires_oeuvre_personnage
  tests/test_perimetre_integration.py::test_volumetrie_des_deux_perimetres
)
TESTS_DEMO=(
  tests/test_etat.py::test_les_comptes_affiches_valent_la_reference
  tests/test_etat.py::test_le_journal_des_decisions_ne_decroit_pas
)
ATTENDUS_DEMO=4  # trois cas paramétrés et un test

echouer() { echo "état d'apimanga : $*" >&2; exit 1; }
dire() { printf '\n==> état d'"'"'apimanga — %s\n' "$*"; }

# --------------------------------------------------------------------------- #
#  Le DSN, lu dans verification/.env par cette étape seule
# --------------------------------------------------------------------------- #
[ -r "$FICHIER_ENV" ] || echouer "verification/.env absent : le créer depuis verification/.env.example"
DSN="$(grep -E '^[[:space:]]*(export[[:space:]]+)?APIMANGA_VERIF_DSN=' "$FICHIER_ENV" | tail -n 1 |
  sed -E 's/^[^=]*=//; s/^[[:space:]]*//; s/[[:space:]]*$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/')"
[ -n "$DSN" ] || echouer "APIMANGA_VERIF_DSN absent de verification/.env"
export PGOPTIONS="-c default_transaction_read_only=on"
PROPRE=(env -i HOME="$HOME" USER="${USER:-$(id -un)}" LANG=C.UTF-8 PATH="$PATH" PGOPTIONS="$PGOPTIONS")

sql() { psql "$DSN" -X -q -A -t -F $'\t' -v ON_ERROR_STOP=1 "$@"; }

# --------------------------------------------------------------------------- #
#  L'identité de la session, avant toute lecture
# --------------------------------------------------------------------------- #
dire "identité de la session"
identite="$(sql -c "SELECT current_user,
  current_setting('default_transaction_read_only'),
  pg_has_role(current_user, 'verif_ro', 'MEMBER'),
  (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)")" ||
  echouer "connexion impossible (rôle verif_lecture créé ? mot de passe dans ~/.pgpass ? migration 022 appliquée ?)"
IFS=$'\t' read -r role lecture_seule membre super <<<"$identite"
echo "rôle $role ; lecture seule : $lecture_seule ; membre de verif_ro : $membre ; superutilisateur : $super"
[ "$lecture_seule" = on ] && [ "$membre" = t ] && [ "$super" = f ] ||
  echouer "la session n'est pas une session de vérification en lecture seule : refus"

# --------------------------------------------------------------------------- #
#  L'état constaté : une ligne par objet (portée, objet, valeurs)
# --------------------------------------------------------------------------- #
constater() {
  local c
  for c in v1 v2; do
    sql -v corpus="$c" -f "$RACINE/database/outils/empreintes_corpus.sql" |
      awk -F'\t' -v c="$c" 'NF { if ($1 == "eval_mesures") { if (c == "v1") print "-\t" $0 } else print c "\t" $0 }'
  done
  sql <<'SQL'
SET TimeZone = 'UTC';
SET DateStyle = 'ISO, YMD';
SET extra_float_digits = 3;
SELECT '-', 'eval_runs', count(*), md5(string_agg(ROW(run_id, corpus_id, encodage_id,
  inscrit_le, note)::text, E'\n' ORDER BY run_id)) FROM bench.eval_runs;
SELECT '-', 'promotions', count(*), md5(string_agg(ROW(promotion_id, decide_le, decision,
  encodage_id, encodage_precedent_id, run_id, motif, decide_par, derogation)::text, E'\n'
  ORDER BY promotion_id)) FROM bench.promotions;
SELECT '-', 'corpus', corpus_id, nb_documents, nb_fragments, clos_le IS NOT NULL
  FROM bench.corpus ORDER BY corpus_id;
SELECT '-', 'encodage_en_service', encodage_id, corpus_id FROM bench.v_encodage_en_service;
SELECT '-', 'jeu', version, empreinte FROM bench.eval_jeux ORDER BY version;
SQL
  (cd "$RACINE/database" && "${PROPRE[@]}" APIMANGA_VERIF_DSN="$DSN" uv run --locked python - <<'PY'
import hashlib
import os

import psycopg

import migrate

fichiers = {m.version: m.checksum for m in migrate.decouvrir()}
with psycopg.connect(os.environ["APIMANGA_VERIF_DSN"]) as cx:
    base = dict(cx.execute("SELECT version, checksum FROM public.schema_migrations").fetchall())
lignes = "".join(f"{v}\t{c}\n" for v, c in sorted(base.items()))
print(f"-\tmigrations\t{len(base)}\t{hashlib.sha256(lignes.encode()).hexdigest()}")
print(f"-\tmigrations_egales_aux_fichiers\t{'oui' if base == fichiers else 'non'}")
PY
  )
}

dire "empreintes des corpus, des mesures, des promotions ; migrations"
debut=$SECONDS
constate="$(constater)" || echouer "relevé impossible"
printf '%s\n' "$constate" >"$SORTIES/etat_apimanga.constate.tsv"
echo "$(grep -c . <<<"$constate") lignes relevées en $((SECONDS - debut)) s"

if [ "${1:-}" = --relever ]; then
  {
    echo "# État de référence d'apimanga, relevé par verification/etat_apimanga.sh --relever."
    echo "# Contrôlé par l'étape « état d'apimanga » de make verify-poste. Une mesure, une"
    echo "# promotion ou une migration le change : relever, puis commiter avec elle."
    echo "# Relevé le $(date -u +%Y-%m-%dT%H:%M:%SZ)."
    echo "# portée	objet	valeurs"
    printf '%s\n' "$constate"
  } >"$REFERENCE"
  echo "référence écrite : ${REFERENCE#"$RACINE"/}"
  exit 0
fi

[ -r "$REFERENCE" ] || echouer "référence absente : bash verification/etat_apimanga.sh --relever"
if ! ecart="$(diff <(grep -v '^#' "$REFERENCE") <(printf '%s\n' "$constate"))"; then
  printf '%s\n' "$ecart" | sed 's/^</  référence :/; s/^>/  constaté  :/' >&2
  echouer "l'état diffère de la référence (une mesure, une promotion, une migration non relevée ?)"
fi
echo "conforme à la référence"

# --------------------------------------------------------------------------- #
#  Le contrôle du module 09, puis les tests qui ne tournent que sur la base réelle
# --------------------------------------------------------------------------- #
dire "contrôle du module 09 (corpus v1, encodages, jeu v2)"
(cd "$RACINE/09_service_embedding" && "${PROPRE[@]}" DATABASE_URL="$DSN" \
  uv run --locked python -m service_embedding.verification --sortie "$SORTIES/verification_09.json") ||
  echouer "le contrôle du module 09 n'est pas conforme"

lancer() {  # lancer <dossier> <variable du DSN> <attendus> <ids…>
  local dossier="$1" variable="$2" attendus="$3" junit tests sautes echecs
  shift 3
  junit="$SORTIES/etat_${dossier%%_*}.xml"
  dire "tests sur la base réelle : $dossier ($attendus)"
  (cd "$RACINE/$dossier" && "${PROPRE[@]}" "$variable=$DSN" \
    uv run --locked --extra dev pytest -p no:cacheprovider -q -rs --junitxml="$junit" "$@") ||
    echouer "$dossier : des tests échouent"
  attribut() { grep -o '<testsuite [^>]*' "$junit" | head -1 | grep -o " $1=\"[0-9]*\"" | grep -o '[0-9]*'; }
  tests=$(attribut tests); sautes=$(attribut skipped)
  echecs=$(( $(attribut failures) + $(attribut errors) ))
  [ "$tests" = "$attendus" ] && [ "$sautes" = 0 ] && [ "$echecs" = 0 ] ||
    echouer "$dossier : $tests tests, $sautes sautés, $echecs en échec ; attendus : $attendus, 0, 0"
}
lancer 05_nettoyage_agregation_bdd APIMANGA_DSN "${#TESTS_05[@]}" "${TESTS_05[@]}"
lancer 08_enrichissement_personnages APIMANGA_DSN "${#TESTS_08[@]}" "${TESTS_08[@]}"
lancer demo DATABASE_URL "$ATTENDUS_DEMO" "${TESTS_DEMO[@]}"

dire "conforme : état égal à la référence, contrôle du 09, 11 tests sur la base réelle"
