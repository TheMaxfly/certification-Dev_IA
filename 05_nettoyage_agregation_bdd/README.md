# preparation_bdd — exploration JSON & CSV

Objectif : préparer un environnement Python simple pour explorer des fichiers `.json`, `.jsonl` et `.csv` (aperçu, schéma/colonnes, statistiques rapides).

## Prérequis

- Python 3.11+

## Installation

```bash
uv sync --all-extras
```

Optionnel : crée un fichier `.env` (voir `.env.example`) pour définir `DATA_DIR`.

## Exemples

```bash
uv run python -m preparation_bdd csv data/sample.csv --head 10
uv run python -m preparation_bdd json data/sample.jsonl --limit 5
```

## Notes Git (données / exports)

Le `.gitignore` ignore par défaut :

- `data/*` (sauf `data/sample.csv` et `data/sample.jsonl`)
- les exports volumineux en `exports/**/*.csv` et `exports/**/*.parquet`
- les sorties `out_ms_*`

Si tu veux versionner un fichier ignoré (ex: un KPI en JSON), utilise `git add -f <fichier>`.

## Notebooks

Notebooks disponibles dans `notebooks/` (exemples) :

- `notebooks/analyse_kitsutoprated_fixed_v2.ipynb`
- `notebooks/analyse_ms_volumes_step1_parquet_csv_jsonb_ready.ipynb`
- `notebooks/analyse_ms_reviews_step2_parquet_csv.ipynb`

Conseils :

- sélectionne le kernel Python de `.venv`
- si tu vois `PermissionError: ... ~/.jupyter` (sandbox/droits), définis `JUPYTER_CONFIG_DIR`, `JUPYTER_DATA_DIR` et `JUPYTER_RUNTIME_DIR` vers des dossiers du projet (voir `.env.example`), puis redémarre VS Code / le notebook
- certains notebooks exportent en CSV + Parquet ; les dépendances correspondantes
  sont installées par `uv sync --all-extras`

## Commandes

```bash
uv run python -m preparation_bdd --help
uv run python -m preparation_bdd csv --help
uv run python -m preparation_bdd json --help
uv run python src/identity/wikidata_dump.py --help
```

## Makefile

```bash
make setup
make lint
make test
```
