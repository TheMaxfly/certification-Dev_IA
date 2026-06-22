# ApiManga

Projet Python pour interroger l’API publique de [Kitsu](https://kitsu.io) et produire des exports JSON “manga snapshots” (schéma strict identique) utilisables pour ingestion (DB) ou indexation RAG.

## Prérequis

- Python 3.9+

## Installation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Note: le projet utilise un layout `src/`, donc les exemples ci‑dessous passent `PYTHONPATH=src`.

## Schéma JSON (identique partout)

Chaque export a la forme:

- `meta`: `{category, source, endpoint, fetched_at, limit, offset}`
- `data`: liste d’objets avec les clés suivantes (toujours présentes, valeurs possibles `null`):
  - `id`, `slug`, `status`, `synopsis`
  - `titles`: `{canonical, en, ja}`
  - `authors`: `[{name, role}]`
  - `ratings`: `{average: float|null, rank: int|null}`
  - `popularity`: `{rank: int|null}`
  - `tags`: `{categories: [str], genres: [str]}`

## Exports (runners)

Les runners **ne suppriment pas** les anciens exports: chaque exécution crée un dossier versionné par date/heure.

### Runner 1: Trending / Publishing / Most popular

Génère (par défaut) :

- `trending_weekly` (top 20)
- `top_publishing` (top 100)
- `most_popular` (top 100)

```bash
PYTHONPATH=src python3 -m api_manga.run_exports
```

Sortie:

- `exports/runs/<run_id>/trending_weekly.json`
- `exports/runs/<run_id>/top_publishing.json`
- `exports/runs/<run_id>/most_popular.json`
- `exports/runs/LATEST` contient le dernier `run_id`

Options utiles:

```bash
PYTHONPATH=src python3 -m api_manga.run_exports --trending-limit 20 --publishing-limit 100 --popular-limit 100
PYTHONPATH=src python3 -m api_manga.run_exports --no-authors        # plus rapide
PYTHONPATH=src python3 -m api_manga.run_exports --no-validate       # ignore la validation
```

### Runner 2: Top rated (base “générale”)

Génère `top_rated.json`. Par défaut `--rated-limit 0` signifie “tout disponible” et l’export est **reprise/résumable**.

```bash
PYTHONPATH=src python3 -m api_manga.run_top_rated --rated-limit 0
```

Sortie:

- `exports/top_rated/<run_id>/top_rated.json` (créé uniquement quand l’export est terminé)
- `exports/top_rated/<run_id>/top_rated.state.json` + `top_rated.ndjson` pendant la collecte
- `exports/top_rated/LATEST` contient le dernier `run_id`

Pour avancer par “tranches” (recommandé):

```bash
PYTHONPATH=src python3 -m api_manga.run_top_rated --rated-limit 0 --max-pages 200
```

## Validation des exports

Valider des fichiers existants:

```bash
PYTHONPATH=src python3 -m api_manga.validate_fixtures --strict exports/runs/<run_id>/*.json
```

Générer + valider en une commande:

```bash
PYTHONPATH=src python3 -m api_manga.validate_fixtures --generate --strict
```

## CLI

La CLI existe toujours pour:

- `--slug` : affiche un résumé d’un manga
- `--tag` : liste des mangas pour une catégorie
- `--trending`, `--publishing`, `--top-rated`, `--popular` : export JSON (voir `--out-dir`)

Exemple:

```bash
PYTHONPATH=src python3 -m api_manga.cli --trending --limit 20 --out-dir exports
```

## Notes

- `exports/` est ignoré par Git (`.gitignore`). Les exports sont considérés comme des artefacts générés.
