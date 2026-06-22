# MangaVersion (Databricks notebook)

Notebook: `MangaVersion1.0 2025-12-15 16_12_43 (1).ipynb`

## Objectif

Filtre la table brute d’albums et conserve uniquement les entrées Manga, nettoie/typage des champs, écrit une table Delta *clean* puis exporte un CSV unique dans un Volume Unity Catalog.

## Prérequis (Databricks)

- Un cluster Databricks avec un runtime incluant Spark + Delta (standard DBR).
- Une table source existante : `default.albums_raw`.
- Accès en lecture/écriture au schéma `default` et au volume `workspace.default` (ou adaptez les chemins).

### Colonnes attendues dans `default.albums_raw`

- `titre` (string)
- `note` (string ou numeric)
- `nb_notes` (string ou numeric)
- `auteur` (string)
- `publisher` (string)
- `synopsis` (string)
- `categories` (string)

## Entrées / Sorties

- **Entrée** : `default.albums_raw`
- **Sortie** : `default.albums_manga_clean` (Delta, overwrite)
- **Export** :
  - Dossier: `/Volumes/workspace/default/exports/runs/<run_id>/albums_manga_clean_export`
  - Fichier final: `/Volumes/workspace/default/exports/runs/<run_id>/albums_manga_clean.csv`

## Paramètres (widgets)

Le notebook définit 2 widgets :

- `run_id` : identifiant de run (défaut: timestamp `YYYYMMDD_HHMMSS`)
- `export_base` : base d’export (défaut: `/Volumes/workspace/default/exports`)

## Exécution

1. Importez/ouvrez le notebook dans Databricks.
2. Attachez-le à un cluster.
3. Assurez-vous que `default.albums_raw` existe.
4. Lancez le notebook (optionnel: renseigner `run_id` et `export_base`).

Le notebook termine avec `dbutils.notebook.exit(final_csv)` et renvoie le chemin du CSV final.

## Environnement local (optionnel)

Le notebook utilise `spark`, `dbutils` et `display`, disponibles dans Databricks. Pour une exécution locale, il faut adapter le code (pas de `dbutils`), mais vous pouvez installer les dépendances minimales :

- `pip install -r requirements.txt`

## Pilotage par Job (Colab / local)

Le notebook renvoie le chemin du CSV final via `dbutils.notebook.exit(final_csv)`. Vous pouvez donc :

1. Lancer le notebook via l’API Jobs (en passant `run_id` et `export_base` en *base_parameters*).
2. Lire la valeur de retour (chemin `/Volumes/.../*.csv`).
3. Télécharger le fichier localement.

Dépendances côté client (runner local) :

- `pip install -r requirements-client.txt`

Configuration (recommandée via `.env`) :

- `cp .env.example .env` puis renseigner vos valeurs.

Variables d’environnement :

- `DATABRICKS_HOST` (ex: `https://adb-xxxxxxxxxxxx.xx.azuredatabricks.net`)
- `DATABRICKS_TOKEN`
- `DATABRICKS_JOB_ID` (si vous utilisez `run_databrick_job_export.py`)
- Optionnel (selon workspace) : `DATABRICKS_ENABLE_EXPERIMENTAL_FILES_API_CLIENT=true`

Exemple :

- `./.venv/bin/python scripts/run_notebook_job.py --notebook-path "/Workspace/Users/<you>/MangaVersion" --existing-cluster-id "<cluster_id>"`
- Pour lancer un Job existant (Jobs UI) et récupérer le `dbutils.notebook.exit(...)` :
  - `./.venv/bin/python run_databrick_job_export.py --job-id "<job_id>" --task-key "<task_key_si_multi_tasks>"`
  - Si le Job n’a qu’une seule task, `--task-key` n’est pas nécessaire. Sinon, récupérez `task_key` dans l’UI Jobs (onglet Tasks).

## Activer l’environnement

- Option Conda (recommandé si vous utilisez `environment.yml`) :
  - `conda env create -f environment.yml`
  - `conda activate mangaversion`

- `python3 -m venv .venv`
- `source .venv/bin/activate`
- `pip install -r requirements-client.txt`
- Optionnel (si vous voulez aussi PySpark/Delta en local) : `pip install -r requirements.txt`

## Publication GitHub

- Ne publiez pas `.env` (token Databricks) ni les fichiers exportés sous `exports/` : ils sont exclus via `.gitignore`.
