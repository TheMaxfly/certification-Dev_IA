#!/usr/bin/env bash
# Sauvegarde de ce qui vit hors du dépôt certification-Dev_IA (spec des sauvegardes,
# 2026-10-08 ; versionnée le 2026-10-20).
#
#   sauvegarder.sh preparer                       bloc B : copies cohérentes et manifeste, en local
#   sauvegarder.sh envoyer  <préparation> <dest>  bloc C a : envoi chiffré (duplicity, gpg symétrique)
#   sauvegarder.sh relire   <préparation> <dest>  bloc C b : restauration depuis la destination,
#                                                 contrôle des empreintes du manifeste
#   sauvegarder.sh tout     <dest>                preparer, envoyer, relire, enchaînés
#   sauvegarder.sh essai-restauration <relecture> bloc C c, d : bases dans un PostgreSQL jetable,
#                                                 magasin MLflow ; source contre restauré
#   sauvegarder.sh nettoyer                       conteneur d'essai et répertoires temporaires
#
# <dest> : le point de montage du support extérieur (ex. /media/<utilisateur>/<disque>).
#
# CE QUI DÉPEND DU POSTE n'est pas ici : la liste des objets sauvegardés, et les
# chemins, se lisent dans un fichier de configuration hors dépôt, ignoré par git —
# `sauvegarde/.env` par défaut, SAUVEGARDE_ENV pour un autre (modèle :
# `sauvegarde/.env.example`). Clés admises, une par ligne (« # » : commentaire) :
#   FICHIER=<chemin relatif à $HOME>      un objet sauvegardé ; autant de lignes que d'objets
#   DEPOT=<chemin relatif à $HOME>        le dépôt (défaut : certification-Dev_IA)
#   RACINE_SAUVEGARDES=<relatif à $HOME>  préparation, relecture, état (défaut : sauvegardes)
#   RESTAURATION=<relatif à $HOME>        la procédure copiée en clair sur le support
#                                         (défaut : <RACINE_SAUVEGARDES>/RESTAURATION.md)
#   BASES=<base> <base>…                  défaut : apimanga manganews
# Une clé inconnue, une valeur vide, aucun FICHIER, un chemin absolu ou qui remonte
# (« .. ») : refus.
#
# AVEC_POIDS=1 : ajoute les poids des modèles d'embedding, aux révisions épinglées dans
# 09_service_embedding/config/*.env (cache Hugging Face, ≈ 3,6 Go). Jamais par défaut :
# ils se retéléchargent (spec des sauvegardes, « ce qui se retélécharge »).
#
# La phrase secrète est demandée au clavier par duplicity : jamais en argument, en
# variable d'environnement ni dans un fichier. Le script refuse de tourner si
# PASSPHRASE ou SIGN_PASSPHRASE sont définies.
#
# Aucun secret ici. Les accès PostgreSQL passent par ~/.pgpass (rôle postgres, une
# ligne par base ; -w : jamais d'invite). Les fichiers .env, jetons et clés d'API ne
# sont pas sauvegardés : ils se recréent (voir RESTAURATION.md).
#
# Sort en erreur au premier échec.
set -Eeuo pipefail   # -E : le piège ERR vaut aussi dans les fonctions
trap 'echo "ÉCHEC : ligne $LINENO, commande : $BASH_COMMAND" >&2' ERR
umask 077

# --------------------------------------------------------------------------- #
#  Réglages — ce qui dépend du poste se lit dans la configuration (lire_config)
# --------------------------------------------------------------------------- #
ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${SAUVEGARDE_ENV:-$ICI/.env}"
PG_HOTE=localhost
PG_PORT=5432
PG_ROLE=postgres
BASES=(apimanga manganews)
BASE_DES_ROLES=apimanga             # base par laquelle pg_dumpall lit les rôles
SOUS_DOSSIER_DEST="sauvegardes-certification"
CONTENEUR_ESSAI=sauvegarde-essai
PORT_ESSAI=55432
FICHIERS=()   # chemins RELATIFS à $HOME (l'arborescence est conservée) : la configuration
EXCLUSIONS=(--exclude='__pycache__/' --exclude='.venv/' --exclude='.env' --exclude='.env.*' --exclude='.ipynb_checkpoints/')

# --------------------------------------------------------------------------- #
pg() { PGOPTIONS='-c default_transaction_read_only=on' psql -w -X -A -t -v ON_ERROR_STOP=1 \
       -h "$PG_HOTE" -p "$PG_PORT" -U "$PG_ROLE" "$@"; }
essai() { psql -w -X -A -t -v ON_ERROR_STOP=1 -h 127.0.0.1 -p "$PORT_ESSAI" -U postgres "$@"; }
dire() { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
echouer() { echo "ÉCHEC : $*" >&2; exit 1; }

relatif() {  # relatif <clé> <valeur> : un chemin sous $HOME, sans « .. »
  case $2 in
    "") echouer "configuration : $1 vide" ;;
    /* | "~"*) echouer "configuration : $1 doit être relatif à \$HOME : $2" ;;
    ..|../*|*/..|*/../*) echouer "configuration : $1 remonte hors de \$HOME : $2" ;;
  esac
}

# La configuration du poste : lue ligne à ligne, jamais exécutée.
lire_config() {
  local ligne cle valeur
  [ -r "$CONFIG" ] || echouer "configuration absente : $CONFIG (modèle : $ICI/.env.example)"
  while IFS= read -r ligne || [ -n "$ligne" ]; do
    ligne="${ligne%$'\r'}"
    case $ligne in "" | \#*) continue ;; esac
    cle="${ligne%%=*}"; valeur="${ligne#*=}"
    [ "$cle" != "$ligne" ] || echouer "configuration : ligne sans « = » : $ligne"
    case $cle in
      FICHIER) relatif "$cle" "$valeur"; FICHIERS+=("$valeur") ;;
      DEPOT) relatif "$cle" "$valeur"; DEPOT="$HOME/$valeur" ;;
      RACINE_SAUVEGARDES) relatif "$cle" "$valeur"; RACINE="$HOME/$valeur" ;;
      RESTAURATION) relatif "$cle" "$valeur"; RESTAURATION="$HOME/$valeur" ;;
      BASES) [ -n "$valeur" ] || echouer "configuration : BASES vide"
             read -r -a BASES <<<"$valeur" ;;
      *) echouer "configuration : clé inconnue « $cle »" ;;
    esac
  done <"$CONFIG"
  [ ${#FICHIERS[@]} -gt 0 ] || echouer "configuration : aucun FICHIER à sauvegarder"
  DEPOT="${DEPOT:-$HOME/certification-Dev_IA}"
  RACINE="${RACINE:-$HOME/sauvegardes}"
  RESTAURATION="${RESTAURATION:-$RACINE/RESTAURATION.md}"
  BASE_DES_ROLES="${BASES[0]}"
  MLFLOW_DB="$DEPOT/10_mesures_recherche/mlflow/mlflow.db"
  MLFLOW_PIECES="$DEPOT/10_mesures_recherche/mlflow/pieces"
  JEU_DOSSIER="$DEPOT/database/donnees/jeu_evaluation/v2"
  # L'image du conteneur d'essai : celle des bases jetables des tests, lue dans le
  # harnais (son empreinte n'est écrite qu'une fois).
  IMAGE_ESSAI="$(awk -F'"' '/^IMAGE_POSTGRES = \(/ { e = 1; next }
    e && /"/ { i = i $2 } e && /^\)/ { print i; exit }' "$DEPOT/database/harnais_tests/__init__.py")"
}

# Paramètres de session fixés pour toute empreinte de contenu (le texte d'une ligne
# dépend du fuseau, du format des dates et des flottants) ; tris sur clés texte en
# COLLATE "C" : la base restaurée n'a pas forcément la locale de la source (fr_FR).
SESSION_FIXE="SET TimeZone='UTC'; SET DateStyle='ISO, YMD'; SET extra_float_digits=1;"

# Une ligne « schéma.table<TAB>lignes » par table, triée.
SQL_COMPTES="SELECT format('SELECT %L || chr(9) || count(*) FROM %I.%I', n.nspname || '.' || c.relname, n.nspname, c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema')
  AND n.nspname NOT LIKE 'pg_toast%' ORDER BY n.nspname, c.relname \\gexec"

# Empreintes de contenu des tables du banc (apimanga), par clé primaire.
SQL_EMPREINTES="$SESSION_FIXE
SELECT 'bench.corpus_docs' || chr(9) || md5(string_agg(md5(t::text), '' ORDER BY doc_key COLLATE \"C\")) FROM bench.corpus_docs t;
SELECT 'bench.corpus_chunks' || chr(9) || md5(string_agg(md5(t::text), '' ORDER BY chunk_id)) FROM bench.corpus_chunks t;
SELECT 'bench.vecteurs_bge_m3' || chr(9) || md5(string_agg(md5(t::text), '' ORDER BY chunk_id)) FROM bench.vecteurs_bge_m3 t;
SELECT 'bench.vecteurs_embeddinggemma' || chr(9) || md5(string_agg(md5(t::text), '' ORDER BY chunk_id)) FROM bench.vecteurs_embeddinggemma t;
SELECT 'bench.encodages' || chr(9) || md5(string_agg(md5(t::text), '' ORDER BY encodage_id)) FROM bench.encodages t;
SELECT 'bench.eval_jeux' || chr(9) || md5(string_agg(md5(t::text), '' ORDER BY version COLLATE \"C\")) FROM bench.eval_jeux t;
SELECT 'bench.eval_questions' || chr(9) || md5(string_agg(md5(t::text), '' ORDER BY jeu_version COLLATE \"C\", question_id COLLATE \"C\")) FROM bench.eval_questions t;
SELECT 'bench.eval_attendus' || chr(9) || md5(string_agg(md5(t::text), '' ORDER BY jeu_version COLLATE \"C\", question_id COLLATE \"C\", series_id)) FROM bench.eval_attendus t;
SELECT 'bench.eval_mesures' || chr(9) || md5(string_agg(md5(t::text), '' ORDER BY mesure_id)) FROM bench.eval_mesures t;
SELECT 'public.schema_migrations' || chr(9) || md5(string_agg(version || ':' || checksum, ',' ORDER BY version COLLATE \"C\")) FROM public.schema_migrations;"

verifier_outils() {
  for t in "$@"; do command -v "$t" >/dev/null || echouer "outil absent : $t (ne rien installer : le signaler)"; done
}

garde_phrase() {
  [ -z "${PASSPHRASE:-}" ] && [ -z "${SIGN_PASSPHRASE:-}" ] ||
    echouer "PASSPHRASE ou SIGN_PASSPHRASE définie : la phrase secrète se saisit au clavier, jamais par variable"
}

options_duplicity() {
  mkdir -p "$RACINE/.duplicity" "$RACINE/.tmp" "$RACINE/.gnupg"
  chmod 700 "$RACINE/.gnupg"
  # Tout l'état local de duplicity et de gpg reste sous $RACINE.
  printf '%s\n' --archive-dir "$RACINE/.duplicity" --tempdir "$RACINE/.tmp" \
    "--gpg-options=--homedir=$RACINE/.gnupg" --volsize 200   # « = » : la valeur commence par --
}

espace_libre() { df -B1 --output=avail "$1" | tail -1 | tr -d ' '; }

# Les poids, à la demande : le dossier ENTIER du modèle dans le cache Hugging Face
# (blobs, refs, snapshots — les snapshots sont des liens vers les blobs, que rsync -a
# conserve tels quels). Chaque dossier ne doit porter QUE la révision épinglée.
ajouter_poids() {
  [ "${AVEC_POIDS:-0}" = 1 ] || return 0
  local hub="${HF_HUB_CACHE:-$HOME/.cache/huggingface/hub}" conf id rev dossier
  for conf in "$DEPOT"/09_service_embedding/config/*.env; do
    id=$(sed -n 's/^MODEL_ID=//p' "$conf")
    rev=$(sed -n 's/^REVISION=//p' "$conf")
    [ -n "$id" ] && [ -n "$rev" ] || echouer "$conf : MODEL_ID ou REVISION absent"
    dossier="$hub/models--${id//\//--}"
    [ "${dossier#"$HOME"/}" != "$dossier" ] || echouer "$dossier n'est pas sous $HOME"
    [ "$(ls "$dossier/snapshots" 2>/dev/null)" = "$rev" ] ||
      echouer "$dossier : la seule révision attendue est $rev"
    FICHIERS+=("${dossier#"$HOME"/}")
    dire "poids : $id @ ${rev:0:8} ($(du -sbL "$dossier/snapshots/$rev" | cut -f1) octets)"
  done
}

# --------------------------------------------------------------------------- #
#  Bloc B — préparer
# --------------------------------------------------------------------------- #
preparer() {
  verifier_outils pg_dump pg_dumpall psql python3 rsync sha256sum docker
  ajouter_poids
  if docker ps --format '{{.Names}}' | grep -qE '^tei-'; then
    echouer "une instance du service d'embedding tourne : pas de sauvegarde pendant une mesure"
  fi
  local v_serveur v_client
  v_serveur=$(pg -d "${BASES[0]}" -c "SHOW server_version_num")
  v_client=$(pg_dump --version | grep -oE '[0-9]+\.[0-9]+' | head -1 | awk -F. '{printf "%d%04d", $1, $2}')
  [ "$v_client" -ge "$v_serveur" ] || echouer "pg_dump ($v_client) plus ancien que le serveur ($v_serveur)"
  for b in "${BASES[@]}"; do pg -d "$b" -c "SELECT 1" >/dev/null || echouer "accès refusé à $b (ligne ~/.pgpass ?)"; done

  local date_utc prep besoin
  date_utc=$(date -u +%Y%m%dT%H%M%SZ)
  prep="$RACINE/preparation/$date_utc"
  besoin=$( { (cd "$HOME" && du -sb "${FICHIERS[@]}" "${MLFLOW_DB#"$HOME"/}" "${MLFLOW_PIECES#"$HOME"/}" | awk '{s+=$1} END {print s}');
              for b in "${BASES[@]}"; do pg -d "$b" -c "SELECT pg_database_size(current_database())"; done; } | awk '{s+=$1} END {print s}')
  mkdir -p "$RACINE/preparation"
  [ "$(espace_libre "$RACINE/preparation")" -gt $((besoin * 2)) ] ||
    echouer "espace insuffisant pour la préparation : besoin estimé $((besoin * 2)) octets"
  mkdir -p "$prep"/{bases,mlflow,fichiers}
  dire "préparation : $prep (volume estimé ${besoin} octets)"

  # Bases : format personnalisé, une base par fichier ; pg_dump ouvre lui-même une
  # transaction en lecture seule. Comptes et empreintes lus juste après, en lecture
  # seule aussi (aucune écriture attendue pendant la sauvegarde).
  for b in "${BASES[@]}"; do
    dire "dump $b"
    PGOPTIONS='-c default_transaction_read_only=on' pg_dump -w -h "$PG_HOTE" -p "$PG_PORT" -U "$PG_ROLE" \
      -Fc -f "$prep/bases/$b.dump" "$b"
    pg -d "$b" <<< "$SQL_COMPTES" | sed '/^$/d' | LC_ALL=C sort > "$prep/bases/$b.comptes.tsv"
  done
  pg -d apimanga <<< "$SQL_EMPREINTES" | sed '/^$/d;/^SET$/d' > "$prep/bases/apimanga.empreintes.tsv"
  dire "rôles et droits (pg_dumpall --roles-only)"
  pg_dumpall -w -h "$PG_HOTE" -p "$PG_PORT" -U "$PG_ROLE" -l "$BASE_DES_ROLES" --roles-only \
    -f "$prep/bases/roles.sql"

  # MLflow : copie cohérente par l'API de sauvegarde de SQLite (source ouverte en
  # lecture seule), puis contrôle d'intégrité de la copie ; pièces copiées à part.
  dire "MLflow"
  python3 - "$MLFLOW_DB" "$prep/mlflow/mlflow.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close(); src.close()
if ok != "ok":
    sys.exit(f"integrity_check de la copie : {ok}")
PY
  rsync -a "$MLFLOW_PIECES/" "$prep/mlflow/pieces/"

  dire "fichiers (${#FICHIERS[@]} objets)"
  (cd "$HOME" && rsync -aR "${EXCLUSIONS[@]}" "${FICHIERS[@]}" "$prep/fichiers/")

  # Aucun secret dans la préparation.
  if find "$prep" -name '.env' -o -name '.env.*' | grep -q .; then echouer "un fichier .env est dans la préparation"; fi
  if grep -rqE 'hf_[A-Za-z0-9]{34}' "$prep/fichiers" "$prep/mlflow" 2>/dev/null; then
    echouer "une chaîne d'aspect jeton Hugging Face est dans la préparation"
  fi
  local jeton_hf="${HF_HOME:-$HOME/.cache/huggingface}/token"
  if [ -s "$jeton_hf" ] && grep -rqF -f "$jeton_hf" "$prep/fichiers" "$prep/mlflow" 2>/dev/null; then
    echouer "le jeton Hugging Face du poste est dans la préparation"
  fi

  manifeste "$prep" "$v_serveur"
  chmod -R go-rwx "$prep"
  dire "préparation terminée"
  echo "$prep"
}

# Les liens symboliques (snapshots du cache Hugging Face) : « chemin<TAB>cible »,
# triés. Le sha256 ne voit que les fichiers réguliers ; cette liste voit les liens.
liste_liens() {
  (cd "$1" && find . -type l -print0 | LC_ALL=C sort -z |
    while IFS= read -r -d '' l; do printf '%s\t%s\n' "$l" "$(readlink "$l")"; done)
}

manifeste() {
  local prep="$1" v_serveur="$2"
  (cd "$prep" && find . -type f ! -name 'MANIFESTE.*' -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) \
    > "$prep/MANIFESTE.sha256"
  liste_liens "$prep" > "$prep/MANIFESTE.liens"
  {
    echo "# Manifeste — sauvegarde $(basename "$prep")"
    echo
    echo "- Date (UTC) : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "- Serveur PostgreSQL : $(pg -d "${BASES[0]}" -c 'SELECT version()')"
    echo "- pg_dump : $(pg_dump --version)"
    echo "- Fichiers : $(wc -l < "$prep/MANIFESTE.sha256") ; octets : $(du -sb "$prep" | cut -f1)"
    echo "- Liens symboliques : $(wc -l < "$prep/MANIFESTE.liens") (chemin et cible : \`MANIFESTE.liens\`)"
    echo
    echo "## Bases"
    echo
    echo "| Base | Taille du dump (octets) | SHA-256 | Tables |"
    echo "|---|---:|---|---:|"
    for b in "${BASES[@]}"; do
      echo "| $b | $(stat -c %s "$prep/bases/$b.dump") | \`$(sha256sum "$prep/bases/$b.dump" | cut -c1-64)\` | $(wc -l < "$prep/bases/$b.comptes.tsv") |"
    done
    echo
    echo "Comptes par table : \`bases/<base>.comptes.tsv\`. Empreintes de contenu du banc (session"
    echo "UTC, ISO, extra_float_digits=1) : \`bases/apimanga.empreintes.tsv\`. Rôles : \`bases/roles.sql\`"
    echo "(empreintes de mots de passe : ce fichier ne quitte la machine que chiffré)."
    echo
    echo "## Objets de fichiers"
    echo
    # shellcheck disable=SC2016  # les accents graves : du Markdown, pas une substitution
    (cd "$HOME" && for f in "${FICHIERS[@]}"; do
       printf -- '- `%s` — %s octets, %s fichiers\n' "$f" "$(du -sb "$f" | cut -f1)" "$(find "$f" -type f | wc -l)"
     done)
    echo
    echo "Toutes les empreintes : \`MANIFESTE.sha256\` (sha256sum -c). Les liens : \`MANIFESTE.liens\`."
  } > "$prep/MANIFESTE.md"
}

# --------------------------------------------------------------------------- #
#  Bloc C a, b — envoyer, relire
# --------------------------------------------------------------------------- #
cible() { echo "file://$2/$SOUS_DOSSIER_DEST/$(basename "$1")"; }

envoyer() {
  local prep="${1:?préparation}" dest="${2:?destination}"
  verifier_outils duplicity gpg
  garde_phrase
  [ -f "$prep/MANIFESTE.sha256" ] || echouer "pas de manifeste dans $prep"
  mountpoint -q "$dest" || echouer "$dest n'est pas un point de montage"
  [ "$(espace_libre "$dest")" -gt "$(du -sb "$prep" | cut -f1)" ] || echouer "espace insuffisant sur $dest"
  mkdir -p "$dest/$SOUS_DOSSIER_DEST"
  local opts; mapfile -t opts < <(options_duplicity)
  dire "envoi chiffré vers $(cible "$prep" "$dest") — la phrase secrète est demandée au clavier"
  duplicity full --name "certif-$(basename "$prep")" "${opts[@]}" "$prep" "$(cible "$prep" "$dest")"
  # En clair sur le support : la procédure, le script et le manifeste (sans secret).
  [ -r "$RESTAURATION" ] || echouer "procédure de restauration introuvable : $RESTAURATION"
  cp "$RESTAURATION" "$(readlink -f "$0")" "$dest/$SOUS_DOSSIER_DEST/"
  cp "$prep/MANIFESTE.md" "$dest/$SOUS_DOSSIER_DEST/MANIFESTE_$(basename "$prep").md"
  cp "$prep/MANIFESTE.sha256" "$dest/$SOUS_DOSSIER_DEST/MANIFESTE_$(basename "$prep").sha256"
  cp "$prep/MANIFESTE.liens" "$dest/$SOUS_DOSSIER_DEST/MANIFESTE_$(basename "$prep").liens"
  dire "envoi terminé"
}

relire() {
  local prep="${1:?préparation}" dest="${2:?destination}"
  verifier_outils duplicity gpg sha256sum
  garde_phrase
  local rel
  rel="$RACINE/relecture/$(basename "$prep")"
  [ ! -e "$rel" ] || echouer "$rel existe déjà"
  mkdir -p "$RACINE/relecture"
  local opts; mapfile -t opts < <(options_duplicity)
  # Le manifeste de référence : celui de la préparation, sinon sa copie en clair sur le support.
  local man="$prep/MANIFESTE.sha256"
  [ -f "$man" ] || man="$dest/$SOUS_DOSSIER_DEST/MANIFESTE_$(basename "$prep").sha256"
  [ -f "$man" ] || echouer "aucun manifeste pour $(basename "$prep")"
  dire "relecture depuis $(cible "$prep" "$dest") — la phrase secrète est demandée au clavier"
  duplicity restore --name "certif-$(basename "$prep")" "${opts[@]}" "$(cible "$prep" "$dest")" "$rel"
  # Les fichiers relus contre le manifeste de la préparation.
  (cd "$rel" && sha256sum --quiet --strict -c "$man") || echouer "empreintes relues ≠ manifeste"
  cmp -s "$rel/MANIFESTE.sha256" "$man" || echouer "le manifeste relu diffère"
  # Les liens relus contre la liste de la préparation (ou sa copie sur le support).
  local liens="${man%.sha256}.liens" n l
  n=$(wc -l < "$man")
  if [ -f "$liens" ]; then
    liste_liens "$rel" | cmp -s - "$liens" || echouer "liens relus ≠ liste du manifeste"
    cmp -s "$rel/MANIFESTE.liens" "$liens" || echouer "la liste de liens relue diffère"
    l=$(wc -l < "$liens")
    dire "relecture conforme : $n fichiers, empreintes égales au manifeste ; $l liens, cibles égales"
  else
    # Sauvegarde antérieure au 2026-10-10 : aucune liste de liens n'a été faite.
    dire "relecture conforme : $n fichiers, empreintes égales au manifeste ; liens NON contrôlés (pas de liste)"
  fi
  echo "$rel"
}

# --------------------------------------------------------------------------- #
#  Bloc C c, d — essai de restauration (depuis la relecture, jamais la préparation)
# --------------------------------------------------------------------------- #
essai_restauration() {
  local rel="${1:?relecture}"
  verifier_outils docker psql pg_restore createdb python3 diff
  [ -f "$rel/MANIFESTE.sha256" ] || echouer "pas de manifeste dans $rel"
  docker rm -fv "$CONTENEUR_ESSAI" >/dev/null 2>&1 || true
  dire "conteneur jetable $IMAGE_ESSAI"
  [[ $IMAGE_ESSAI == *@sha256:* ]] || echouer "image d'essai non épinglée : ${IMAGE_ESSAI:-absente}"
  docker run -d --name "$CONTENEUR_ESSAI" -e POSTGRES_HOST_AUTH_METHOD=trust \
    -p "127.0.0.1:$PORT_ESSAI:5432" "$IMAGE_ESSAI" >/dev/null
  for _ in $(seq 1 60); do essai -d postgres -c 'SELECT 1' >/dev/null 2>&1 && break; sleep 1; done
  essai -d postgres -c 'SELECT 1' >/dev/null || echouer "le conteneur d'essai ne répond pas"

  dire "rôles"
  # Le rôle postgres existe déjà dans l'image : seule cette erreur est admise.
  local err; err=$(psql -w -X -q -h 127.0.0.1 -p "$PORT_ESSAI" -U postgres -d postgres -f "$rel/bases/roles.sql" 2>&1 >/dev/null || true)
  if printf '%s\n' "$err" | grep -E 'ERROR|ERREUR' | grep -vqE 'role "postgres" already exists|le rôle « postgres » existe déjà'; then
    echouer "restauration des rôles : $(printf '%s' "$err" | grep -E 'ERROR|ERREUR' | head -3)"
  fi

  for b in "${BASES[@]}"; do
    dire "restauration de $b"
    createdb -w -h 127.0.0.1 -p "$PORT_ESSAI" -U postgres -T template0 "$b"
    pg_restore -w -h 127.0.0.1 -p "$PORT_ESSAI" -U postgres -d "$b" --exit-on-error -j 4 "$rel/bases/$b.dump"
    essai -d "$b" <<< "$SQL_COMPTES" | sed '/^$/d' | LC_ALL=C sort > "$RACINE/.tmp/$b.comptes.restaure.tsv"
    diff -q "$rel/bases/$b.comptes.tsv" "$RACINE/.tmp/$b.comptes.restaure.tsv" >/dev/null ||
      echouer "$b : nombre de lignes source ≠ restauré ($(diff "$rel/bases/$b.comptes.tsv" "$RACINE/.tmp/$b.comptes.restaure.tsv" | head -4 | tr '\n' ' '))"
    dire "$b : $(wc -l < "$rel/bases/$b.comptes.tsv") tables, nombre de lignes égal table par table"
  done
  essai -d apimanga <<< "$SQL_EMPREINTES" | sed '/^$/d;/^SET$/d' > "$RACINE/.tmp/apimanga.empreintes.restaure.tsv"
  diff -q "$rel/bases/apimanga.empreintes.tsv" "$RACINE/.tmp/apimanga.empreintes.restaure.tsv" >/dev/null ||
    echouer "apimanga : empreintes de contenu source ≠ restauré"
  dire "apimanga : empreintes de contenu du banc égales ($(wc -l < "$rel/bases/apimanga.empreintes.tsv") tables)"

  # Le jeu v2 : empreinte des fichiers versionnés contre celle de la base RESTAURÉE.
  python3 - "$JEU_DOSSIER" <<PY
import hashlib, subprocess, sys
from pathlib import Path
d = Path(sys.argv[1])
lignes = sorted((str(f.relative_to(d)), hashlib.sha256(f.read_bytes()).hexdigest()) for f in d.rglob("*") if f.is_file())
fichiers = hashlib.sha256("".join(f"{c}\t{h}\n" for c, h in lignes).encode()).hexdigest()
base = subprocess.run(["psql", "-w", "-X", "-A", "-t", "-h", "127.0.0.1", "-p", "$PORT_ESSAI", "-U", "postgres",
                       "-d", "apimanga", "-c", "SELECT empreinte FROM bench.eval_jeux WHERE version = 'v2'"],
                      capture_output=True, text=True, check=True).stdout.strip()
print(f"jeu v2 : fichiers {fichiers} ; base restaurée {base}", file=sys.stderr)
sys.exit(0 if fichiers == base else "empreinte du jeu v2 : fichiers ≠ base restaurée")
PY

  dire "MLflow"
  python3 - "$rel/mlflow/mlflow.db" "$MLFLOW_DB" <<'PY'
import sqlite3, sys
r = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
o = sqlite3.connect(f"file:{sys.argv[2]}?mode=ro", uri=True)
ok = r.execute("PRAGMA integrity_check").fetchone()[0]
q = "SELECT e.name, r.status, count(*) FROM runs r JOIN experiments e USING (experiment_id) GROUP BY 1, 2 ORDER BY 1, 2"
m = "SELECT run_uuid, key, value, step FROM latest_metrics ORDER BY 1, 2"
p = "SELECT run_uuid, key, value FROM params ORDER BY 1, 2"
enfants = r.execute("SELECT count(*) FROM tags WHERE key = 'mlflow.parentRunId'").fetchone()[0]
egal = {n: r.execute(s).fetchall() == o.execute(s).fetchall() for n, s in (("runs", q), ("metriques", m), ("parametres", p))}
print(f"MLflow restauré : integrity_check {ok} ; runs par expérience {r.execute(q).fetchall()} ; "
      f"runs enfants {enfants} ; égal à l'origine {egal}", file=sys.stderr)
sys.exit(0 if ok == "ok" and all(egal.values()) else "MLflow restauré ≠ magasin d'origine")
PY
  docker rm -fv "$CONTENEUR_ESSAI" >/dev/null
  dire "essai de restauration conforme ; conteneur détruit"
}

nettoyer() {
  docker rm -fv "$CONTENEUR_ESSAI" >/dev/null 2>&1 || true
  rm -rf -- "${RACINE:?}/relecture" "${RACINE:?}/.tmp"
  dire "conteneur d'essai et répertoires temporaires supprimés (la préparation est conservée)"
}

case "${1:-}" in
  preparer|envoyer|relire|tout|essai-restauration|nettoyer) lire_config ;;
esac
case "${1:-}" in
  preparer) preparer ;;
  envoyer) envoyer "${2:-}" "${3:-}" ;;
  relire) relire "${2:-}" "${3:-}" ;;
  tout) p=$(preparer); envoyer "$p" "${2:?destination}"; relire "$p" "$2" ;;
  essai-restauration) essai_restauration "${2:-}" ;;
  nettoyer) nettoyer ;;
  *) sed -n '2,14p' "$0"; exit 2 ;;
esac
