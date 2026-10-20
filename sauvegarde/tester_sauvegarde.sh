#!/usr/bin/env bash
# Essais de sauvegarde/sauvegarder.sh sur une arborescence jetable : un HOME factice,
# un dépôt factice, et des doublures de PostgreSQL, Docker, duplicity et gpg en tête
# du PATH. Rien ne touche le poste : ni base, ni conteneur, ni support.
#
#   bash sauvegarde/tester_sauvegarde.sh      → code 0 si tous les scénarios tiennent
#
#   - la configuration : absente, clé inconnue, chemin absolu ou qui remonte, aucun
#     FICHIER → refus ;
#   - la préparation, de bout en bout : objets copiés selon la configuration, .env
#     écartés, manifeste vérifiable, magasin MLflow copié et intègre ;
#   - arrêt au premier échec (une commande qui échoue) : refus, et pas de manifeste ;
#   - une chaîne d'aspect jeton dans les objets : refus ;
#   - une instance du service d'embedding qui tourne : refus ;
#   - la phrase secrète en variable (PASSPHRASE, SIGN_PASSPHRASE) : refus, sans
#     appeler duplicity.
set -uo pipefail

ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$(cd "$ICI/.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
echecs=0

# --- Le poste factice ----------------------------------------------------------- #
export HOME="$T/maison"
unset PASSPHRASE SIGN_PASSPHRASE HF_HOME AVEC_POIDS
DEPOT="$HOME/depot"
mkdir -p "$DEPOT/database/harnais_tests" "$DEPOT/10_mesures_recherche/mlflow/pieces/a" \
  "$DEPOT/module/resultats" "$DEPOT/module/raw" "$HOME/sauvegardes"
cp "$SOURCE/database/harnais_tests/__init__.py" "$DEPOT/database/harnais_tests/"
echo "une mesure" >"$DEPOT/module/resultats/r.json"
echo "un raw" >"$DEPOT/module/raw/f.ndjson"
echo "SECRET=x" >"$DEPOT/module/raw/.env"
echo "pièce" >"$DEPOT/10_mesures_recherche/mlflow/pieces/a/p.txt"
python3 - "$DEPOT/10_mesures_recherche/mlflow/mlflow.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("CREATE TABLE runs (run_uuid TEXT)")
c.execute("INSERT INTO runs VALUES ('r1')")
c.commit()
PY
echo "procédure" >"$HOME/sauvegardes/RESTAURATION.md"

# --- Les doublures ---------------------------------------------------------------- #
B="$T/doublures"
mkdir -p "$B"
cat >"$B/psql" <<'EOF'
#!/usr/bin/env bash
entree=""; [ -t 0 ] || entree="$(cat)"
case "$*" in
  *"SHOW server_version_num"*) echo 160004 ;;
  *"SELECT 1"*) echo 1 ;;
  *pg_database_size*) echo 1000 ;;
  *"version()"*) echo "PostgreSQL 16.4" ;;
  *)
    case $entree in
      *"format('SELECT"*) printf 'manga.t\t3\n' ;;
      *bench.corpus_docs*) printf 'bench.corpus_docs\tabc\n' ;;
    esac ;;
esac
EOF
cat >"$B/pg_dump" <<'EOF'
#!/usr/bin/env bash
[ "${1:-}" = --version ] && { echo "pg_dump (PostgreSQL) 16.4"; exit 0; }
while [ $# -gt 0 ]; do [ "$1" = -f ] && { echo dump >"$2"; exit 0; }; shift; done
EOF
cat >"$B/pg_dumpall" <<'EOF'
#!/usr/bin/env bash
[ -n "${PANNE_PG_DUMPALL:-}" ] && exit 1
while [ $# -gt 0 ]; do [ "$1" = -f ] && { echo "-- rôles" >"$2"; exit 0; }; shift; done
EOF
cat >"$B/docker" <<'EOF'
#!/usr/bin/env bash
[ "${1:-}" = ps ] && printf '%s\n' ${CONTENEURS:-}
exit 0
EOF
for outil in duplicity gpg; do
  printf '#!/usr/bin/env bash\necho "%s $*" >>"%s/appels"\n' "$outil" "$T" >"$B/$outil"
done
chmod +x "$B"/*
export PATH="$B:$PATH"

# --- Les essais ------------------------------------------------------------------- #
configurer() {  # configurer <lignes…> : la configuration du poste factice
  printf '%s\n' "$@" >"$T/config.env"
}
CONFIG_VALIDE=(DEPOT=depot "FICHIER=depot/module/resultats" "FICHIER=depot/module/raw")
sauvegarder() {  # entrée fermée : la doublure de psql lit la sienne
  SAUVEGARDE_ENV="$T/config.env" bash "$SOURCE/sauvegarde/sauvegarder.sh" "$@" \
    </dev/null >"$T/sortie" 2>&1
}
constat() {  # constat <libellé> <condition…>
  local libelle="$1"
  shift
  if "$@"; then
    echo "  ok    $libelle"
  else
    echo "  ÉCHEC $libelle"
    sed 's/^/        | /' "$T/sortie" | tail -8
    echecs=$((echecs + 1))
  fi
}
dit() { grep -qF -- "$1" "$T/sortie"; }
refuse() { ! sauvegarder "$@"; }

echo "Essai de la sauvegarde"

rm -f "$T/config.env"
constat "configuration absente : refus" refuse preparer
constat "configuration absente : le dit" dit "configuration absente"
configurer "${CONFIG_VALIDE[@]}" "CLE_INCONNUE=x"
constat "clé inconnue : refus" refuse preparer
constat "clé inconnue : la nomme" dit "clé inconnue « CLE_INCONNUE »"
configurer DEPOT=depot "FICHIER=/etc/passwd"
constat "chemin absolu : refus" refuse preparer
constat "chemin absolu : le dit" dit "doit être relatif"
configurer DEPOT=depot "FICHIER=depot/../../hors"
constat "chemin qui remonte : refus" refuse preparer
constat "chemin qui remonte : le dit" dit "remonte hors de"
configurer DEPOT=depot
constat "aucun FICHIER : refus" refuse preparer
constat "aucun FICHIER : le dit" dit "aucun FICHIER"

configurer "${CONFIG_VALIDE[@]}"
sauvegarder preparer
rc=$?
prep="$(tail -1 "$T/sortie")"
constat "préparation : réussie" [ "$rc" -eq 0 ]
constat "préparation : objets copiés" test -f "$prep/fichiers/depot/module/raw/f.ndjson"
constat "préparation : .env écarté" test ! -e "$prep/fichiers/depot/module/raw/.env"
constat "préparation : manifeste vérifiable" \
  bash -c "cd '$prep' && sha256sum --quiet --strict -c MANIFESTE.sha256"
constat "préparation : MLflow copié, intègre" python3 -c "
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
ok = c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
sys.exit(not (ok and c.execute('SELECT count(*) FROM runs').fetchone()[0] == 1))
" "$prep/mlflow/mlflow.db"
constat "préparation : objet hors configuration absent" \
  test ! -e "$prep/fichiers/depot/10_mesures_recherche/resultats"

rm -rf "$HOME/sauvegardes/preparation"
PANNE_PG_DUMPALL=1 sauvegarder preparer
rc=$?
constat "arrêt au premier échec : refus" [ "$rc" -ne 0 ]
constat "arrêt au premier échec : la commande nommée" dit "ÉCHEC : ligne"
constat "arrêt au premier échec : aucun manifeste" \
  bash -c "! compgen -G '$HOME/sauvegardes/preparation/*/MANIFESTE.sha256' >/dev/null"

echo "TOKEN=hf_$(printf 'Ab3%.0s' $(seq 1 11))x" >"$DEPOT/module/resultats/fuite.txt"
constat "chaîne d'aspect jeton dans un objet : refus" refuse preparer
constat "chaîne d'aspect jeton dans un objet : le dit" dit "aspect jeton"
rm -f "$DEPOT/module/resultats/fuite.txt"

CONTENEURS="tei-bge-m3" sauvegarder preparer
rc=$?
constat "une instance du service tourne : refus" [ "$rc" -ne 0 ]
constat "une instance du service tourne : le dit" dit "une instance du service d'embedding tourne"

: >"$T/appels"
PASSPHRASE=x sauvegarder envoyer "$prep" "$T/support"
rc=$?
constat "PASSPHRASE définie : refus" [ "$rc" -ne 0 ]
constat "PASSPHRASE définie : le dit" dit "PASSPHRASE ou SIGN_PASSPHRASE définie"
SIGN_PASSPHRASE=x sauvegarder relire "$prep" "$T/support"
rc=$?
constat "SIGN_PASSPHRASE définie : refus" [ "$rc" -ne 0 ]
constat "phrase secrète en variable : duplicity jamais appelé" test ! -s "$T/appels"

if [ "$echecs" -gt 0 ]; then
  echo "$echecs scénario(s) en échec"
  exit 1
fi
echo "Tous les scénarios tiennent."
