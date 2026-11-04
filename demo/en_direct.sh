#!/usr/bin/env bash
# La démonstration en direct : l'application, le Swagger de l'API et MLflow, d'une commande.
#
#   make demo-direct                 lance les trois, attend qu'ils répondent, donne les adresses
#   make demo-direct CONSTRUIRE=1    reconstruit d'abord les images (à faire avant le jour J)
#   make demo-direct-etat            dit ce qui répond
#   make demo-direct-arret           arrête les trois
#
# Lancer ne fait aucun appel au fournisseur du modèle de langage. MLflow lit une COPIE du
# magasin, refaite à chaque lancement : rien de ce qu'on clique en direct ne touche aux
# mesures. Les conteneurs recréés repartent de compteurs et d'un journal vides.
#
# Usage direct : en_direct.sh lancer|etat|arreter <commande compose…> (voir le Makefile).

set -u

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAGASIN="$RACINE/10_mesures_recherche/mlflow/mlflow.db"
TRAVAIL="${XDG_RUNTIME_DIR:-/tmp}/manga-demo-direct"
PID="$TRAVAIL/mlflow.pid"

APPLICATION=http://127.0.0.1:8020
API=http://127.0.0.1:8010
MLFLOW=http://127.0.0.1:5000
MEMOIRE_CONSEILLEE_MO=6900  # le seuil de verify-poste, service d'embedding et API compris

repond() {  # repond <adresse> : 0 si l'adresse rend un statut 200
  [ "$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$1")" = 200 ]
}

memoire_mo() { free -m | awk '/^Mem:/ {print $7}'; }

mlflow_pid() {  # le groupe de processus de MLflow, s'il tourne encore et si c'est bien lui
  local pid
  [ -r "$PID" ] || return 1
  pid="$(<"$PID")"
  [ -n "$pid" ] && grep -qa mlflow "/proc/$pid/cmdline" 2>/dev/null || return 1
  echo "$pid"
}

mlflow_lancer() {
  mlflow_pid >/dev/null && return 0
  if ss -ltn 2>/dev/null | grep -q '127\.0\.0\.1:5000\b'; then
    echo "Le port 5000 est déjà pris par un autre programme : MLflow n'est pas lancé." >&2
    return 1
  fi
  [ -r "$MAGASIN" ] || { echo "Magasin MLflow introuvable : $MAGASIN" >&2; return 1; }
  mkdir -p "$TRAVAIL" || return 1
  cp -f "$MAGASIN" "$TRAVAIL/mlflow.db" || return 1
  # Depuis le dossier de travail : ce que MLflow pourrait écrire reste hors du dépôt. Un
  # seul serveur et pas de file de tâches : sinon, 1,3 Go de mémoire de plus (2026-11-04).
  (
    cd "$TRAVAIL" || exit 1
    MLFLOW_DISABLE_AGENT_HINT=1 MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false \
      setsid uv run --no-sync --project "$RACINE/10_mesures_recherche" \
      mlflow ui --backend-store-uri "sqlite:///$TRAVAIL/mlflow.db" \
      --default-artifact-root "$TRAVAIL/pieces" --host 127.0.0.1 --port 5000 --workers 1 \
      >"$TRAVAIL/mlflow.log" 2>&1 </dev/null &
    echo $! >"$PID"
  )
}

mlflow_arreter() {
  local pid n=0
  pid="$(mlflow_pid)" || return 0
  kill -TERM -- "-$pid" 2>/dev/null
  while kill -0 -- "-$pid" 2>/dev/null && [ "$n" -lt 20 ]; do
    sleep 0.5
    n=$((n + 1))
  done
  kill -0 -- "-$pid" 2>/dev/null && kill -KILL -- "-$pid" 2>/dev/null
  rm -f "$PID"
}

attendre() {  # attendre <adresse> <secondes>
  local fin=$((SECONDS + $2))
  until repond "$1"; do
    [ "$SECONDS" -ge "$fin" ] && return 1
    sleep 1
  done
}

etat() {
  local rc=0 cible
  while read -r nom cible; do
    if repond "$cible"; then
      printf '  %-12s répond       %s\n' "$nom" "$cible"
    else
      printf '  %-12s NE RÉPOND PAS  %s\n' "$nom" "$cible"
      rc=1
    fi
  done <<EOF
Application $APPLICATION/sante
Swagger $API/v1/docs
MLflow $MLFLOW/health
EOF
  echo "  Mémoire disponible : $(memoire_mo) Mo"
  return "$rc"
}

lancer() {
  local autres disponible options=(up -d --wait --wait-timeout 300)
  autres="$(docker ps --format '{{.Names}}' | grep '^tei-' | grep -v '^tei-api-modele$')"
  if [ -n "$autres" ]; then
    echo "Une autre instance du service d'embedding tourne : $autres. Une seule à la fois." >&2
    return 1
  fi
  if ! docker ps --format '{{.Names}}' | grep -q '^tei-api-modele$'; then
    disponible="$(memoire_mo)"
    if [ "$disponible" -lt "$MEMOIRE_CONSEILLEE_MO" ]; then
      echo "Attention : $disponible Mo disponibles, $MEMOIRE_CONSEILLEE_MO conseillés." >&2
    fi
  fi
  [ -n "${CONSTRUIRE:-}" ] && options+=(--build)

  "$@" "${options[@]}" || { echo "Le Compose n'a pas démarré." >&2; return 1; }
  mlflow_lancer || return 1
  attendre "$MLFLOW/health" 60 || {
    echo "MLflow ne répond pas : voir $TRAVAIL/mlflow.log" >&2
    return 1
  }
  etat || return 1
  cat <<EOF

Démonstration prête.

  Application  $APPLICATION/
  Swagger API  $API/v1/docs   (bouton Authorize : une clé d'accès de l'API)
  MLflow       $MLFLOW/   (une copie du magasin : rien n'y est conservé)

  Payant : « Demander » dans l'application, POST /v1/reponse dans le Swagger.
  Gratuit : POST /v1/recherche, GET /v1/sante, GET /v1/info.
  Le Swagger affiche la clé en clair après « Execute ».

  Arrêt : make demo-direct-arret
EOF
}

arreter() {
  mlflow_arreter
  "$@" down
}

action="${1:-}"
shift || true
case "$action" in
  lancer) lancer "$@" ;;
  etat) etat ;;
  arreter) arreter "$@" ;;
  *)
    echo "Usage : en_direct.sh lancer|etat|arreter <commande compose…>" >&2
    exit 2
    ;;
esac
