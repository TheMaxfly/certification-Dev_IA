# =====================================================================
#  SOUTENANCE — séquence de démonstration (API Manga, exécution locale)
#  À charger AVANT le passage :   source demo_soutenance/commandes.sh
#  Puis un geste = une frappe :   g1  g2  g3  g4  g5  g6  g7
#
#  Chaque geste AFFICHE la commande qu'il exécute, puis l'exécute. Ce que
#  le jury lit est exactement ce qui tourne : la chaîne montrée est celle
#  passée à `eval`, jamais une reformulation — un écart entre les deux se
#  verrait et ruinerait la preuve.
# =====================================================================

DEMO_DIR="${DEMO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)}"
BASE="${BASE:-http://127.0.0.1:8000}"
# La clé est lue dans le .env de démonstration : elle n'est écrite nulle part
# ailleurs, et surtout pas dans ce fichier — qui, lui, peut être versionné.
CLE="$(grep '^API_KEYS=' "$DEMO_DIR/.env" | cut -d: -f2-)"
export PGPASSFILE="${PGPASSFILE:-$HOME/.pgpass}"
PSQL_RO="postgresql://manga_api@localhost:5432/apimanga"
# Les journaux d'uvicorn vont dans un FICHIER, jamais dans le terminal de la
# démonstration : sans cela, chaque appel afficherait sa ligne « INFO: GET
# /series/11455 » au milieu des réponses et brouillerait l'écran.
API_LOG="${API_LOG:-/tmp/api-soutenance.log}"

# --- Mise en scène ----------------------------------------------------
# `titre` annonce l'objectif AVANT la commande : le jury sait ce qu'il doit
# regarder au lieu de le deviner après coup.
titre() {
  printf '\n\033[7m %-70s \033[0m\n' "$1"
  [ -n "${2:-}" ] && printf '\033[2m %s\033[0m\n' "$2"
  printf '\n'
}

# `cmd` montre la commande, puis l'exécute. Le `$CLE` reste LITTÉRAL à
# l'affichage — 48 caractères aléatoires en pleine ligne nuiraient à la
# lecture, et le nom de la variable dit mieux ce qui se passe.
cmd() {
  printf '\033[1;36m  $ %s\033[0m\n' "$1"
  eval "$1"
  printf '\n'
}

# Filtre d'affichage commun aux gestes 4, 5 et 6. La réponse complète de
# /series/{id} fait 56 lignes, dont deux synopsis de 1 200 caractères : elle
# défilerait hors de l'écran et le jury ne verrait rien. On montre les six
# champs qui portent la démonstration.
VUE_SERIE='{series_id, title, kitsu_id, work_uid, genres_source: (.genres_source|join(", ")), genres_enriched: ([.genres_enriched[].code]|join(", "))}'


# --- 1. LE CONTRAT ------------------------------------------- 20 s ---
# Prouve : 13 points de terminaison, chacun résumé ; 11 sous cadenas,
# les 2 sondes ouvertes. Critères 1, 2, 3 de C5.
g1() {
  titre "GESTE 1 · LE CONTRAT — la documentation est servie par l'API" \
        "Critères 1, 2, 3 de C5 : couverture, autorisation, standard OpenAPI"
  # Trois comptages COURTS plutôt qu'un filtre illisible : une commande que
  # le jury ne peut pas lire ne prouve rien. Chacune tient sur une ligne et
  # se comprend sans connaître jq.
  printf '\033[2m  Combien de routes ?\033[0m\n'
  cmd "curl -s $BASE/openapi.json | jq '.paths | length'"
  printf '\033[2m  Combien sont protégées par une clé ?\033[0m\n'
  cmd "curl -s $BASE/openapi.json | jq '[.paths[][] | select(.security)] | length'"
  printf '\033[2m  Lesquelles sont ouvertes ?\033[0m\n'
  cmd "curl -s $BASE/openapi.json | jq -r '.paths | to_entries[] | select(.value.get.security == null) | .key'"
  cmd "xdg-open $BASE/docs >/dev/null 2>&1 &"
}

# --- 2. LE REFUS, PUIS L'ACCÈS -------------------------------- 20 s ---
# Prouve : l'API restreint l'accès aux données. Critère 4 de C5.
# Même route, même seconde, une seule différence : l'en-tête X-API-Key.
g2() {
  titre "GESTE 2 · LE REFUS, PUIS L'ACCÈS — même route, une seule différence" \
        "Critère 4 de C5 : l'API restreint l'accès aux données"
  printf '\033[1m  ── SANS clé ──\033[0m\n'
  cmd "curl -si $BASE/rag/export/composition | head -1"
  cmd "curl -s $BASE/rag/export/composition | jq -c ."
  printf '\033[1m  ── AVEC la clé, même route ──\033[0m\n'
  cmd "curl -si -H \"X-API-Key: \$CLE\" $BASE/rag/export/composition | head -1"
  cmd "curl -s -H \"X-API-Key: \$CLE\" $BASE/rag/export/composition | jq '{total, by_source: [.by_source[] | \"\(.source): \(.documents)\"]}'"
}

# --- 3. LE TÉMOIN DE LECTURE SEULE ---------------------------- 30 s ---
# Prouve : le second moyen de mise à disposition — l'accès SQL direct — est
# incapable d'écrire. Refus du SERVEUR (SQLSTATE 42501, aclcheck_error), pas
# un garde-fou applicatif : c'est PostgreSQL qui tranche, sous le rôle même
# dont l'API se sert.
g3() {
  titre "GESTE 3 · LE TÉMOIN DE LECTURE SEULE — le second moyen d'accès" \
        "Spécifications C5 : API REST *et* accès direct à la base de données"
  cmd "psql \"\$PSQL_RO\" -tAc 'SELECT current_user;'"
  cmd "psql \"\$PSQL_RO\" -tAc \"SELECT 'lecture OK, ' || count(*) || ' séries' FROM manga.ms_series_enriched;\""
  printf '\033[1m  ── et maintenant, une écriture ──\033[0m\n'
  cmd "psql \"\$PSQL_RO\" -c '\\set VERBOSITY verbose' -c \"UPDATE manga.ms_series_enriched SET series_title='piraté' WHERE series_id=11455;\" 2>&1 | grep -E 'ERROR|EMPLACEMENT|LOCATION'"
}

# --- 4. LE GESTE CENTRAL -------------------------------------- 40 s ---
# Prouve : une série non appariée à KITSU — la source d'enrichissement —
# porte quand même ses genres. kitsu_id null · work_uid 19599 ·
# fantastique, aventure → surnaturel, aventure.  0 → 7 487 séries.
g4() {
  titre "GESTE 4 · /series/11455 — non appariée à Kitsu, et pourtant des genres" \
        "JoJo's Bizarre Adventure : Jojolion — le correctif en un écran : 0 → 7 487"
  cmd "curl -s -H \"X-API-Key: \$CLE\" $BASE/series/11455 | jq '$VUE_SERIE'"
}

# --- 5. LES DEUX NORMALISATIONS ------------------------------- 30 s ---
# Prouve : deux opérations DIFFÉRENTES sur la même colonne.
#   746   consolidation : thriller, Suspense, policier → thriller
#   11455 traduction non littérale : fantastique → surnaturel
g5() {
  titre "GESTE 5 · LES DEUX NORMALISATIONS — consolider n'est pas traduire" \
        "746 Monster : trois libellés, un code · 11455 Jojolion : le faux ami"
  printf '\033[1m  ── 746 Monster · CONSOLIDATION ──\033[0m\n'
  cmd "curl -s -H \"X-API-Key: \$CLE\" $BASE/series/746 | jq '$VUE_SERIE'"
  printf '\033[1m  ── 11455 Jojolion · TRADUCTION NON LITTÉRALE ──\033[0m\n'
  cmd "curl -s -H \"X-API-Key: \$CLE\" $BASE/series/11455 | jq '$VUE_SERIE'"
}

# --- 5 bis. LA TABLE D'ARBITRAGE (à la demande) -----------------------
# Le « pourquoi », si le jury le demande. Chaque ligne a été tranchée à la
# main et porte sa note. Une correspondance par similarité de chaîne aurait
# rapproché « fantastique » de « Fantasy » — l'inverse du bon résultat.
g5bis() {
  titre "GESTE 5 bis · LA TABLE D'ARBITRAGE — libellé par libellé" \
        "Pourquoi une table écrite à la main plutôt qu'une similarité de chaîne"
  cmd "psql \"\$PSQL_RO\" -P pager=off -c \"SELECT source, libelle_brut AS libelle_source, code AS code_normalise FROM manga.genre_mapping WHERE lower(libelle_brut) IN ('fantastique','fantasy','supernatural','thriller','suspense','policier') ORDER BY code, source, libelle_brut;\""
  printf '\033[1m  ── la justification de la ligne « ms / fantastique » ──\033[0m\n'
  cmd "psql \"\$PSQL_RO\" -tAc \"SELECT note FROM manga.genre_mapping WHERE source='ms' AND libelle_brut='fantastique';\""
}

# --- 6. FACULTATIF — UN TITRE CHOISI PAR L'EXAMINATEUR -------- 40 s ---
# OFFERT, JAMAIS IMPOSÉ. Si personne ne demande, passer au geste 7.
# Trouve l'identifiant À PARTIR DU TITRE (SQL en lecture seule), puis sert la
# fiche par l'API. Les quatre réponses possibles sont dans README.md.
g6() {
  titre "GESTE 6 · UN TITRE DE VOTRE CHOIX — de son nom à sa fiche" \
        "SQL en lecture seule pour trouver l'identifiant, puis l'API pour la fiche"
  cmd "\$DEMO_DIR/titre.sh \"${1:-One Piece}\""
}

# --- 7. LA COUVERTURE ----------------------------------------- 30 s ---
# Prouve : l'outil montre aussi ce qu'il ne sait pas. Les totaux ET les
# limites — 1 694 séries sans aucun genre, 5 304 orphelines d'identité.
# Les deux limites sont CALCULÉES à l'appel, jamais écrites en dur.
g7() {
  titre "GESTE 7 · LA COUVERTURE — ce que le référentiel sait, et ce qu'il ignore" \
        "Les limites sont calculées à chaque appel, jamais écrites en dur"
  printf '\033[2m  Ce que le référentiel contient\033[0m\n'
  cmd "curl -s -H \"X-API-Key: \$CLE\" $BASE/coverage | jq '{totaux: .totals, genres: .genres}'"
  printf '\033[2m  Et les séries qu\x27aucun rapprochement n\x27a pu identifier\033[0m\n'
  cmd "curl -s -H \"X-API-Key: \$CLE\" $BASE/coverage | jq '.totals.series - ([.identity_by_method[].decisions] | add)'"
}

# --- Le journal de l'API (à la demande) ------------------------------
# Prouve, si le jury creuse la sécurité : le refus est JOURNALISÉ, et le
# journal ne contient JAMAIS la clé — le libellé du consommateur quand elle
# est valide, rien quand elle ne l'est pas.
logs() {
  titre "EN RÉSERVE · LE JOURNAL — tracé, et sans jamais écrire la clé"
  cmd "tail -n 12 \"\$API_LOG\""
}

# --- Contrôles de mise en route --------------------------------------
# À lancer AVANT le jury. Doit afficher « ok » partout.
verif() {
  printf '  API           : %s\n' "$(curl -s "$BASE/health" 2>/dev/null || echo INJOIGNABLE)"
  printf '  clé de démo   : %s (%s caractères)\n' "${CLE:0:16}…" "${#CLE}"
  printf '  base en RO    : %s\n' "$(psql "$PSQL_RO" -tAc 'SELECT current_user;' 2>&1)"
  printf '  jq            : %s\n' "$(jq --version 2>&1)"
  printf '  journal API   : %s\n' "$API_LOG"
}

printf 'Séquence chargée.  Gestes : g1 g2 g3 g4 g5 [g5bis] g6 g7   ·   en réserve : logs   ·   contrôle : verif\n'
