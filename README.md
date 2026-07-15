# Certification Dev IA

Depot principal du projet de certification Dev IA : chaine de collecte, nettoyage,
stockage, exposition API et evaluation RAG pour un assistant de recommandation
manga.

Le workflow de reference est dans `workflow_certification_dev_ia.drawio`.

## Objectif

Construire une chaine Data / IA reproductible autour de donnees manga :

1. collecter des donnees depuis plusieurs sources ;
2. nettoyer, normaliser et valider les jeux de donnees ;
3. charger les donnees dans PostgreSQL ;
4. exposer les metadonnees et le corpus RAG-ready via une API ;
5. evaluer les embeddings, la recherche vectorielle et les reponses LLM.

## Workflow

```text
Sources externes
  -> collecte Scrapy / API
  -> nettoyage, enrichissement, validation
  -> PostgreSQL schema manga
  -> FastAPI + recherche plein texte
  -> corpus RAG-ready
  -> benchmark embeddings / LLM
```

Le depot est organise par etapes numerotees.

## Structure du depot

| Dossier | Role | Etat |
| --- | --- | --- |
| `01_scraping_manganews/` | Scraping Manga-News avec Scrapy, validation Great Expectations et import PostgreSQL. | Collecte 2026-07 terminee : 11 717 series et 50 populaires. |
| `02_api_manga/` | API FastAPI en lecture seule sur PostgreSQL : metadonnees, corpus RAG et recherche plein texte. | MVP teste, avec Compose d'integration. |
| `03_kitsu_api_exports/` | Client Kitsu et collecteur exhaustif reprenable. | Catalogue, mappings et staff termines ; characters hors perimetre actuel. |
| `04_scraping_manga_sanctuary/` | Scraping Manga Sanctuary : series, tomes et critiques staff. | Snapshot 2026-07 promu : 103 811 volumes et 11 052 critiques. |
| `05_nettoyage_agregation_bdd/` | Nettoyage, normalisation, identite multi-source et preparation PostgreSQL. | Transition des notebooks vers le package `identity`. |
| `06_benchmark_embeddings_llm/` | Benchmark embeddings, FAISS, recall@K, MRR et evaluation LLM. | Scripts experimentaux relies au schema `bench`. |
| `07_databricks_manga_export/` | Notebook/runner Databricks : table brute albums vers Delta clean puis CSV. | Flux transverse documente. |
| `database/` | Migrations PostgreSQL partagees et versionnees. | Migrations `001/002` appliquees a `apimanga`. |

## Sources de donnees

### Manga-News

Source scrapee avec Scrapy dans `01_scraping_manganews/`.

Sorties principales :

- `data/enriched/manganews_series.jsonl`
- `data/enriched/populaires.jsonl`
- versions backfilled utilisees pour validation et import PostgreSQL

La chaine Manga-News contient :

- nettoyage et enrichissement des champs ;
- construction de `rag_text` ;
- flags de qualite pour Great Expectations ;
- validation critique / warning ;
- staging PostgreSQL ;
- upsert final ;
- audit des imports.

Le snapshot courant contient **11 717 series** et **50 entrees populaires**. Le
snapshot du 31 decembre 2025 reste archive localement avec son manifeste SHA-256.

### Kitsu

Source API publique dans `03_kitsu_api_exports/`.

Le run exhaustif `exports/full_catalog/20260714T152202Z/` est termine sur le
perimetre utile :

- catalogue : **62 768 mangas** ;
- mappings externes : **104 726 ressources** ;
- staff : **53 183 ressources** ;
- `top_rated.json` derive du catalogue complet.

La relation `characters` est conservee partiellement mais n'est ni reprise ni
utilisee dans le pipeline actuel. Le collecteur stocke les JSONL bruts, un
`state.json` et un manifeste afin de permettre une reprise fiable.

### Manga Sanctuary

Source scrapee avec Scrapy dans `04_scraping_manga_sanctuary/`.

Donnees ciblees :

- metadonnees series ;
- tomes / editions ;
- notes membres et experts ;
- critiques staff ;
- synopsis series et tomes.

Le snapshot `data/raw/2026-07/` a ete promu apres validation : **103 811 volumes**,
**14 652 series distinctes** et **11 052 critiques**. Les donnees restent locales
et ignorees par Git.

### Databricks

Flux transverse dans `07_databricks_manga_export/`.

Objectif :

- lire `default.albums_raw` ;
- filtrer les entrees Manga ;
- nettoyer et typer les champs ;
- ecrire une table Delta clean ;
- exporter un CSV dans un Volume Unity Catalog.

## Nettoyage et validation

Le nettoyage est reparti entre plusieurs niveaux.

### `01_scraping_manganews`

Pipeline le plus complet :

- normalisation des textes, slugs et listes ;
- parsing origine / annee ;
- version de schema et version d'enrichissement ;
- timestamp `scraped_at` ;
- construction et controle du texte RAG ;
- validation Great Expectations ;
- import bloque si la validation critique echoue.

Commandes utiles :

```bash
cd 01_scraping_manganews
uv run python scripts/run_all_validations_gx110.py --do-backfill
uv run python scripts/run_prod_import.py --dataset series
uv run python scripts/run_prod_import.py --dataset populaires
```

### `05_nettoyage_agregation_bdd`

Espace de preparation pour explorer, nettoyer et agreger JSON, JSONL et CSV.

Commandes utiles :

```bash
cd 05_nettoyage_agregation_bdd
uv run python -m preparation_bdd json data/sample.jsonl --limit 5
uv run python -m preparation_bdd csv data/sample.csv --head 10
uv run python src/identity/wikidata_dump.py --help
```

Exports Kitsu prepares dans `Preparation_weekly/export/` :

- `kitsu_series_core.csv`
- `kitsu_weekly_snapshot.csv`
- `kitsu_series_authors.csv`
- `kitsu_rag_documents.csv`

## Stockage PostgreSQL

La source de verite du DDL partage est `database/migrations/`. Les migrations
`001_socle_identite.sql` et `002_staging_referentiels.sql` ont ete appliquees a la
base `apimanga` le 15 juillet 2026. Elles creent les structures d'identite et de
staging ; les donnees collectees ne sont pas encore chargees dans ces nouvelles
tables. Toute evolution passe par une migration `003` ou suivante.

Les donnees nettoyees alimentent principalement le schema `manga`.

Tables et vues utilisees par l'API :

- `manga.kitsu_series_core`
- `manga.kitsu_weekly_snapshot`
- `manga.kitsu_series_authors`
- `manga.rag_docs_scored`
- `manga.rag_export_docs`

Les benchmarks utilisent le schema `bench` :

- `bench.corpus_docs`
- `bench.corpus_chunks`
- `bench.embedding_models`
- `bench.embedding_runs`
- `bench.faiss_indexes`
- `bench.queries`
- `bench.qrels`
- `bench.retrieval_results`
- `bench.metrics`

## API

L'API est dans `02_api_manga/`.

Demarrage local :

```bash
cd 02_api_manga
uv sync --all-groups
uv run uvicorn app.main:app --reload --env-file .env
```

Demarrage Docker :

```bash
cd 02_api_manga
docker compose up --build
```

Endpoints principaux :

- `GET /health`
- `GET /kitsu/{kitsu_id}`
- `GET /rag/export`
- `GET /rag/doc/{doc_key}`
- `GET /search?q=...`

## Benchmarks RAG / LLM

Le module `06_benchmark_embeddings_llm/` compare des modeles d'embedding et
des modeles LLM pour la recommandation manga.

Modeles d'embedding documentes :

- `paraphrase-multilingual-MiniLM-L12-v2`
- `intfloat/multilingual-e5-small`

Metriques :

- recall@K ;
- MRR ;
- entity_hit@K / entity_recall@K pour les reponses LLM ;
- validite des citations ;
- latence.

## Reproductibilite

Chaque module contient son propre `README.md`, ses dependances et ses commandes
d'execution. Les artefacts lourds et secrets locaux restent ignores par Git :

- fichiers `.env` ;
- exports volumineux ;
- checkpoints de crawl ;
- sorties FAISS ;
- fichiers generes Databricks.

Pour une demonstration de certification, executer en priorite :

1. verification des snapshots Manga-News, Kitsu et Manga Sanctuary ;
2. verification des migrations avec `database/migrate.py status` ;
3. chargement controle du staging et construction du referentiel d'identite ;
4. lancement de l'API ;
5. verification `/live`, `/health`, `/rag/export` et `/search` ;
6. benchmark embeddings sur le schema `bench`.

## Points de vigilance

- Les notebooks de nettoyage Kitsu et Manga Sanctuary contiennent encore une
  partie importante de la logique metier ; leur extraction vers des modules Python
  et une CLI reste en cours.
- La centralisation PostgreSQL est amorcee dans `database/migrations/`, mais les
  anciennes tables `ms_*`, `mn_*`, les vues RAG et le schema `bench` restent a
  migrer progressivement sans modifier `001/002`.
- Le renommage des dossiers et du package `api_manga` est gele jusqu'a une fenetre
  atomique dediee, avant l'orchestrateur.
- Les rapports de validation doivent etre regeneres apres chaque collecte
  importante pour garder des preuves a jour.
