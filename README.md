# Manga Sanctuary Scraper

Automatise la collecte des séries, tomes et critiques du site [manga-sanctuary.com](https://www.manga-sanctuary.com/bdd/series.html) à l’aide de Scrapy. Le dépôt ne contient que le code et quelques exports tests : les extractions lourdes (plusieurs milliers de pages) sont prévues pour tourner sur une autre machine.

## Structure du projet

- `pyproject.toml` / `uv.lock` : configuration du projet gérée par [uv](https://github.com/astral-sh/uv) (Python 3.12).
- `requirements.txt` : alternative minimale pour `pip install`.
- `manga_sanctuary/` : projet Scrapy généré par `scrapy startproject`.
  - `manga_sanctuary/spiders/manga_sanctuary_volumes.py` : spider principal qui parcours toutes les lettres, fiches séries, tomes et critiques staff.
  - `manga_sanctuary/manga_sanctuary_volumes.jsonl` & `manga_sanctuary/manga_sanctuary_reviews.jsonl` : exemples de sorties JSON Lines (ignorés par Git).

## Prérequis

- Python 3.12+
- `uv` (recommandé) ou `pip`

## Installation

```bash
# Créer l’environnement et installer les dépendances
uv sync

# Activer l’environnement (optionnel, uv peut aussi lancer les commandes directement)
source .venv/bin/activate
```

Sans `uv`, vous pouvez installer les dépendances minimales avec :

```bash
pip install -r requirements.txt
```

## Commandes utiles

Lister les spiders disponibles :

```bash
uv run scrapy list
```

Lancer un crawl complet (à exécuter sur la machine lourde prévue) :

```bash
uv run scrapy crawl manga_sanctuary_volumes \
  -O manga_sanctuary/manga_sanctuary_volumes.jsonl \
  -s LOG_FILE=logs/manga_sanctuary_volumes.log
```

Pour un test rapide local sans surcharger la source, limitez le nombre de pages :

```bash
uv run scrapy crawl manga_sanctuary_volumes \
  -s CLOSESPIDER_PAGECOUNT=50 \
  -O manga_sanctuary/test_volumes.jsonl
```

Les fichiers d’état (`manga_sanctuary/crawls/`) et les exports `.jsonl` sont ignorés par Git pour garder le dépôt léger. Pensez à vérifier les résultats générés localement puis à transférer les exports produits par la machine lourde via un stockage externe avant publication GitHub.
