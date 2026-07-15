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
| `DB_USER` | `postgres` | Utilisateur |
| `DB_PASSWORD` | *(vide)* | Mot de passe |
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
DB_USER=postgres
DB_PASSWORD=
```

## Initialisation SQL

La source de vérité du schéma de production est désormais le dossier racine
`../database/migrations/`. L'API reste en lecture seule et ne doit pas créer les
tables de production au démarrage.

Le script `sql/001_api_schema.sql` est conservé temporairement comme contrat SQL et
bootstrap isolé du Compose d'intégration. Il crée uniquement les objets minimaux
nécessaires au smoke test ; il ne remplace pas les migrations racine pour une base
réelle.

```bash
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  -f sql/001_api_schema.sql
```

Le script ne supprime aucune donnée. Le boost documenté utilise le snapshot le plus
récent de chaque liste avec les poids suivants : tendance `3 / position`, popularité
`2 / position`, publication en cours `1 / position`.

## Périmètre & données

### Sources

- **Kitsu** (API)
  - `most_popular.json` : popularité globale (rank)
  - `top_publishing.json` : classement “publishing/rating” (rank)
  - `trending_weekly.json` : tendance hebdo (ordre d’apparition → rang calculé)
- **Manga Sanctuary** (scraping / exports)
  - séries / volumes / critiques (reviews)

### Données exposées côté RAG

Le corpus RAG est assemblé dans PostgreSQL via des **vues** :
- `manga.rag_docs_scored` : documents + score de boost (tendance/popularité/top)
- `manga.rag_export_docs` : vue “export final” (filtrage des textes vides)

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

La vue `manga.rag_docs_scored` calcule le boost : plus la position est haute (`1`, `2`, `3`…), plus le boost augmente ; ce boost permet de remonter les contenus “chauds / tendance” lors de l’export et des recherches.

## Validation du module

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
docker compose config
docker compose build
docker compose -f compose.integration.yml up \
  --build --abort-on-container-exit --exit-code-from smoke
docker compose -f compose.integration.yml down
```

Le Compose d'intégration démarre une PostgreSQL 16 temporaire, rejoue le SQL,
injecte une fixture puis appelle réellement tous les endpoints HTTP principaux.
