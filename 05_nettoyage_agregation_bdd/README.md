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

### Mesures (lecture seule)

Elles ne décident rien et n'écrivent rien en base : elles instruisent, et
produisent un rapport JSON horodaté dans `data/rapports/`.

```bash
# Par quel SCRIPT passent les concordances d'auteur de l'étage 1 (dette 22.3).
DATABASE_URL='postgresql://manga_api@localhost:5432/apimanga' \
    PYTHONPATH=src uv run python -m identity.mesure_formes_auteur
```

**Ce que ce chiffre veut dire.** Dénominateur : les séries dont la décision
courante est `method='exact_author'`, `status='auto'` — celles dont l'identité
existe parce que le signal auteur a tranché. « Forme latine » : `forme_norm`
sans idéogramme ni kana — c'est la **graphie** qui est mesurée, pas le tag
`langue` de Wikidata, les deux ne coïncidant pas.

Mesure du **2026-08-25** : **100,00 %** (1 058 / 1 058). Sans
`wd_auteurs_formes`, l'étage 1 tomberait à **21** séries. Ces valeurs
remplacent les chiffres indicatifs antérieurs (99,6 % et 4 séries), qui
venaient d'une requête de diagnostic non conservée — cf. ETAT §44.

## Makefile

```bash
make setup
make lint
make test
```
