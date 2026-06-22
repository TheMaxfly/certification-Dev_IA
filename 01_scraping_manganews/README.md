# Scraping Manga-News avec Scrapy

Projet de pour scraper le site Manga-News avec Scrapy. Nettoyer, Valider , CICD, et importer en base.



## Prerequis

- Python 3.10+

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Lancer le spider

```bash
scrapy crawl manga_news -O data.json
```

## Validation des JSONL (Great Expectations)

Validation + rapports GX (recommandee):
```bash
python3 scripts/run_all_validations_gx110.py
```

Avec backfill automatique avant validation:
```bash
python3 scripts/run_all_validations_gx110.py --do-backfill
```

Scripts unitaires:
```bash
python3 scripts/validate_manganews_series_gx110.py --file data/enriched/manganews_series.jsonl
python3 scripts/validate_populaires_gx110.py --file data/enriched/populaires.jsonl
```


## Pipeline complet (backfill + validation + import)

tout en une commande :
```bash
python3 scripts/run_pipeline_backfill_validate_import.py
```

Options utiles:
- `--no-backfill` : saute le backfill
- `--skip-import` : ne fait que la validation
- `--dsn` : override du DSN pour l'import

## Import en base (run_prod_import.py)

Prerequis:
- `.env` a la racine avec `POSTGRES_DSN=...`
- fichiers JSONL sources dans `data/enriched/` (manganews_series.jsonl, populaires.jsonl)

Usage:
```bash
python3 scripts/run_prod_import.py --dataset series
python3 scripts/run_prod_import.py --dataset populaires
```

Comportement par defaut:
- lance backfill + validations GX (via `scripts/run_pipeline_backfill_then_validate.py`)
- n'importe en base que si GX OK
- utilise les fichiers backfilled par defaut
- garde la staging et purge >30 jours

Options utiles:
- `--file` : importer un backfilled.jsonl specifique
- `--skip-gx` : sauter la validation GX (si deja faite)
- `--keep-days` : retention staging (defaut 30)
- `--dsn` : override du DSN

Note: `--file` n'affecte pas la validation GX (elle utilise les fichiers par defaut). Pour un fichier specifique, lance la validation au prealable puis `--skip-gx`.

## Structure

- `scrapy.cfg` : configuration du projet Scrapy
- `manga_news_scraper/settings.py` : reglages du projet
- `manga_news_scraper/spiders/manga_news.py` : spider principal (SitemapSpider)

## Notes

- Respecter les robots.txt et les conditions d'utilisation du site.
- Le spider actuel est un point de depart : adaptez `sitemap_rules` et les selecteurs CSS selon les pages cibles.
