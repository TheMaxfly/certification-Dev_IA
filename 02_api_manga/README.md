# ApiManga

API **FastAPI** (Bloc 1) connectée à **PostgreSQL** (schéma `manga`) pour exposer :
- des métadonnées **Kitsu** (synopsis, ranks, tags, auteurs…)
- un corpus **RAG-ready** (documents prêts à être exportés puis vectorisés, ex. FAISS)
- une **recherche plein texte** (Full-Text Search PostgreSQL) sur le corpus

Objectif : valider le **Bloc 1** (collecte → nettoyage/normalisation → stockage en base → exposition via API).

> Ce module est une API **en lecture seule** : il expose les données déjà chargées
> dans PostgreSQL. La collecte Kitsu est réalisée dans `03_kitsu_api_exports/` et le
> scraping Manga-News dans `01_scraping_manganews/`.

## Démarrage rapide

### En local avec `uv`

```bash
uv sync --all-groups
uv run uvicorn app.main:app --reload --env-file .env
```

### Avec Docker Compose

```bash
docker compose up --build
```

API : `http://localhost:8000`  
Swagger UI : `http://localhost:8000/docs`

## Prérequis

- Docker + Docker Compose (recommandé pour lancer l’API)
- Une base PostgreSQL accessible (local, WSL, serveur, futur cloud)
- Un fichier `.env` local est recommandé pour les paramètres de connexion ; le
  Compose possède des valeurs par défaut et démarre aussi sans ce fichier.

## Configuration

Copier `.env.example` vers `.env` puis compléter si besoin.

L’API lit les variables suivantes (avec valeurs par défaut si non définies) :

| Variable | Défaut | Description |
| --- | --- | --- |
| `DB_HOST` | `host.docker.internal` | Hôte PostgreSQL |
| `DB_PORT` | `5432` | Port PostgreSQL |
| `DB_NAME` | `apimanga` | Base de données |
| `DB_USER` | `manga_api` | Rôle de connexion — consultation seule |
| `DB_PASSWORD` | *(vide)* | Mot de passe du rôle |
| `DB_CONNECT_TIMEOUT` | `5` | Délai de connexion PostgreSQL (secondes) |
| `DB_POOL_TIMEOUT` | `5` | Attente maximale d'une connexion du pool |
| `DB_POOL_MIN_SIZE` | `1` | Nombre minimal de connexions du pool |
| `DB_POOL_MAX_SIZE` | `5` | Nombre maximal de connexions du pool |

Exemple de `.env` :

```bash
APP_ENV=development
APP_NAME=API Manga

DB_HOST=host.docker.internal
DB_PORT=5432
DB_NAME=apimanga
DB_USER=manga_api
DB_PASSWORD=
```

## Prérequis : le rôle de consultation

L'API se connecte sous **`manga_api`**, un rôle qui n'a **aucun droit
d'écriture** : il hérite ses privilèges de `manga_ro`, à qui la migration `012`
accorde `SELECT` sur le schéma `manga` et rien d'autre. Une API en lecture seule
qui se connecterait en superutilisateur ne serait en lecture seule que par
convention ; ici la base refuse l'écriture, quoi que fasse le code.

Le rôle doit exister avant le démarrage. Il n'est pas créé par une migration —
il porte un mot de passe, qui n'a pas sa place dans le dépôt :

```bash
cd ../database
DATABASE_URL='postgresql://…' uv run python migrate.py up      # crée manga_ro
MANGA_API_PASSWORD="$(openssl rand -base64 24)" \
  sh outils/creer_role_lecture.sh 'postgresql://postgres@…/apimanga'
```

Procédure complète, périmètre exact des droits et stockage du mot de passe :
**`../database/README.md`, section « Accès en consultation »**.

Si le rôle est absent, l'API démarre — le pool s'ouvre sans bloquer — mais
`/health` répond `503` avec `{"status":"degraded","db":"error"}` et les endpoints
de données `503 database unavailable`. Le journal du conteneur porte la cause
réelle :

```
FATAL:  password authentication failed for user "manga_api"
```

ou, si le rôle existe mais n'est pas membre de `manga_ro` :

```
ERROR:  permission denied for table kitsu_series_core
```

## Schéma de la base

**La source de vérité du schéma est `../database/migrations/`, et elle seule.**
Ce module ne transporte aucun DDL : il est en lecture seule et ne crée rien, ni au
démarrage ni ailleurs. Pour préparer une base, on joue les migrations :

```bash
cd ../database
DATABASE_URL='postgresql://…' uv run python migrate.py up
DATABASE_URL='postgresql://…' uv run python migrate.py status
```

Le Compose d'intégration applique ces mêmes migrations à une base jetable avant de
démarrer l'API (cf. « Validation du module »). Le contrat SQL du module n'est donc
plus un fichier à part susceptible de dériver : c'est le schéma de production lui-même
qui est testé.

## Périmètre & données

### Sources

- **Kitsu** (API)
  - `most_popular.json` : popularité globale (rank)
  - `top_publishing.json` : classement “publishing/rating” (rank)
  - `trending_weekly.json` : tendance hebdo (ordre d’apparition → rang calculé)
- **Manga Sanctuary** (scraping / exports)
  - séries / volumes / critiques (reviews)

### Données exposées côté RAG

Le corpus RAG est assemblé dans PostgreSQL via une chaîne de **vues**, que l'API
consomme par son extrémité :

```
rag_export_docs        ← ce que lit l'API (textes vides écartés)
  └─ rag_docs_scored   ← ajoute boost_score
      └─ rag_docs_all_v2
          ├─ rag_docs_all        → rag_kitsu_docs  (source « kitsu_synopsis »)
          │                      → rag_reviews_docs (source « ms_review »)
          └─ rag_ms_hybrid_docs  → ms_kitsu_map ⨯ les deux précédentes
                                   (source « ms_hybrid »)
```

Trois sources cohabitent donc dans le corpus servi par `/rag/export` et `/search` :
`kitsu_synopsis`, `ms_hybrid` et `ms_review`.

## Architecture

- **FastAPI** (conteneur Docker) → lit PostgreSQL en SQL (psycopg)
- **PostgreSQL** (hors conteneur ou conteneur séparé) → tables, index, vues du schéma `manga`

## Vérification

```bash
curl -s http://localhost:8000/live
curl -s http://localhost:8000/health
```

Réponse attendue :

```json
{"status":"ok","db":"ok"}
```

`/live` teste seulement le processus API. `/health` teste aussi PostgreSQL et renvoie
HTTP 503 avec une réponse neutralisée lorsque la base est indisponible.

## Endpoints

### 1) Vie du processus

`GET /live` — vérifie que le processus FastAPI répond, sans dépendre de PostgreSQL.

### 2) Santé / connexion DB

`GET /health` — vérifie que l’API répond et que PostgreSQL est joignable.

```bash
curl -s http://localhost:8000/health
```

### 3) Métadonnées Kitsu

`GET /kitsu/{kitsu_id}` — lit `manga.kitsu_series_core`.

```bash
curl -s http://localhost:8000/kitsu/38
```

Champs (exemple) :
`kitsu_id`, `slug`, `title_canonical`, `synopsis_clean`, `rating_average_10`, `rating_rank`, `popularity_rank`.

### 4) Export RAG (aperçu)

`GET /rag/export?limit=20&offset=0` — pagine `manga.rag_export_docs` et retourne un aperçu.

- `limit` : `1..200` (défaut `20`)
- `offset` : `>= 0` (défaut `0`)

```bash
curl -s "http://localhost:8000/rag/export?limit=3&offset=0"
```

### 5) Récupération d’un document complet

`GET /rag/doc/{doc_key}` — retourne `doc_text` complet + métadonnées (issu de `manga.rag_export_docs`).

```bash
curl -s "http://localhost:8000/rag/doc/kitsu:38" | head
```

### 6) Recherche plein texte (PostgreSQL FTS)

`GET /search?q=...&limit=10&offset=0` — recherche plein texte dans `manga.rag_export_docs` via `to_tsvector('simple', doc_text)` + `websearch_to_tsquery`.

- `q` : min `2` caractères
- `limit` : `1..50` (défaut `10`)
- `offset` : `>= 0`

```bash
curl -s "http://localhost:8000/search?q=one%20piece&limit=5" | head
curl -s "http://localhost:8000/search?q=shounen%20fantasy&limit=5" | head
```

## Modèle de scoring : `boost_score`

Le tri principal du corpus RAG combine :
- score texte (FTS) via `ts_rank_cd`
- score de boost basé sur les signaux hebdomadaires Kitsu : `trending_pos`, `popular_pos`, `top_pos`

La vue `manga.rag_docs_scored` calcule le boost. Les poids sont ceux de la base de
production :

| Signal | Poids | Contribution |
| --- | --- | --- |
| tendance (`trending_pos`) | 100 | `100 / position` |
| popularité (`popular_pos`) | 30 | `30 / position` |
| publication en cours (`top_pos`) | 20 | `20 / position` |

Un signal absent ne contribue pas. Plus la position est haute (`1`, `2`, `3`…), plus
le boost augmente : les contenus « chauds » remontent à l'export et à la recherche.
Exemple, pour un document en position 1 / 2 / 4 : `100/1 + 30/2 + 20/4 = 120`.

Un document sans aucun signal hebdomadaire — les critiques, notamment — a donc un
boost nul et ne remonte que par son score texte.

## Validation du module

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
docker compose config
docker compose build
docker compose -f compose.integration.yml run --rm --build smoke
docker compose -f compose.integration.yml down -v
```

Le Compose d'intégration démarre une PostgreSQL 16 temporaire (`tmpfs`, donc
réellement jetable), **applique les 12 migrations de `../database/migrations/`**,
injecte la fixture de test puis appelle réellement les six endpoints HTTP. Le schéma
vérifié est celui de la production : le smoke test contrôle notamment que la formule
de boost servie est bien celle du tableau ci-dessus, et échoue si une pondération
antérieure était rétablie.

`down -v` est nécessaire entre deux exécutions : la fixture n'est pas rejouable sur
une base déjà peuplée.
