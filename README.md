# ApiManga

API **FastAPI** (Bloc 1) connectée à **PostgreSQL** (schéma `manga`) pour exposer :
- des métadonnées **Kitsu** (synopsis, ranks, tags, auteurs…)
- un corpus **RAG-ready** (documents prêts à être exportés puis vectorisés, ex. FAISS)
- une **recherche plein texte** (Full-Text Search PostgreSQL) sur le corpus

Objectif : valider le **Bloc 1** (collecte → nettoyage/normalisation → stockage en base → exposition via API).

## Démarrage rapide

### En local (venv)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
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
- Un fichier `.env` (non versionné) contenant les paramètres de connexion

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
curl -s http://localhost:8000/health
```

Réponse attendue :

```json
{"status":"ok","db":"ok"}
```

## Endpoints

### 1) Santé / connexion DB

`GET /health` — vérifie que l’API répond et que PostgreSQL est joignable.

```bash
curl -s http://localhost:8000/health
```

### 2) Métadonnées Kitsu

`GET /kitsu/{kitsu_id}` — lit `manga.kitsu_series_core`.

```bash
curl -s http://localhost:8000/kitsu/38
```

Champs (exemple) :
`kitsu_id`, `slug`, `title_canonical`, `synopsis_clean`, `rating_average_10`, `rating_rank`, `popularity_rank`.

### 3) Export RAG (aperçu)

`GET /rag/export?limit=20&offset=0` — pagine `manga.rag_export_docs` et retourne un aperçu.

- `limit` : `1..200` (défaut `20`)
- `offset` : `>= 0` (défaut `0`)

```bash
curl -s "http://localhost:8000/rag/export?limit=3&offset=0"
```

### 4) Récupération d’un document complet

`GET /rag/doc/{doc_key}` — retourne `doc_text` complet + métadonnées (issu de `manga.rag_export_docs`).

```bash
curl -s "http://localhost:8000/rag/doc/kitsu:38" | head
```

### 5) Recherche plein texte (PostgreSQL FTS)

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
