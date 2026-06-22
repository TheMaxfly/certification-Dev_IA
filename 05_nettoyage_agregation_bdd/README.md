# preparation_bdd — exploration JSON & CSV

Objectif : préparer un environnement Python simple pour explorer des fichiers `.json`, `.jsonl` et `.csv` (aperçu, schéma/colonnes, statistiques rapides).

## Prérequis

- Python 3.11+

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -U pip
```

### Option A (packaging)

```bash
python3 -m pip install -e ".[dev]"
```

### Option B (requirements)

```bash
python3 -m pip install -r requirements.txt
python3 -m pip install -r requirements-dev.txt
```

Optionnel : crée un fichier `.env` (voir `.env.example`) pour définir `DATA_DIR`.

## Exemples

```bash
python3 -m preparation_bdd csv data/sample.csv --head 10
python3 -m preparation_bdd json data/sample.jsonl --limit 5
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
- certains notebooks exportent en CSV + Parquet (installe `pyarrow` via `requirements-dev.txt` ou `pip install -e ".[dev]"`)

## Commandes

```bash
python3 -m preparation_bdd --help
python3 -m preparation_bdd csv --help
python3 -m preparation_bdd json --help
```

## Makefile

```bash
make setup
make lint
make test
```
