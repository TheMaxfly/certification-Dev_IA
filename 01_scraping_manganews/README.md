# Scraping Manga-News

Collecte des fiches séries et des classements populaires de Manga-News pour le
pipeline de nettoyage, PostgreSQL et RAG du projet.

## État vérifié le 14 juillet 2026

- Les pages publiques existent toujours : index des séries A-Z, fiches série et
  page des mangas populaires.
- Les informations utiles sont toujours visibles : titre, auteurs, éditeurs,
  type, genres, origine, résumé, points forts et nombre de volumes.
- L'ancien User-Agent imitant Chrome et les requêtes `curl` reçoivent `HTTP 403`
  avec le challenge Cloudflare « Just a moment... ». Avec le nouveau User-Agent
  identifiable `manga-news-scraper/0.2`, les smoke tests Scrapy reçoivent
  `HTTP 200`.
- Les tests réels ont extrait une fiche complète et les 50 mangas populaires,
  répartis dans 5 catégories, sans titre, URL, image ou volume manquant. Le
  hub expose bien 27 liens (`#` et `A-Z`) et le listing `A` contient 874 liens
  de fiches détectés. Le crawl complet des milliers de fiches n'avait pas été
  lancé lors de cette vérification initiale.
- Les crawls autorisés du 14 juillet 2026 sont maintenant terminés et validés :
  **11 717 séries** et **50 entrées populaires**, sans URL dupliquée. Les validations
  Great Expectations critique et warning réussissent pour les deux datasets.
- Le snapshot précédent du 31 décembre 2025 (11 415 séries et 50 populaires) reste
  archivé dans `data/archive/2025-12-31/`.

`robots.txt` n'interdit que `/flarumprivate/`, mais cela ne constitue pas une
autorisation de réutiliser la base ou les textes. Les CGU protègent les contenus
et limitent leur usage. Obtenir l'accord de Manga-News avant une nouvelle
collecte complète et avant l'indexation des résumés dans un produit. Ne pas
ajouter de mécanisme destiné à contourner Cloudflare.

## Installation

Depuis ce dossier :

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[postgres,quality]'
```

Avec `uv` :

```bash
uv sync --all-extras
```

## Vérifications locales

Les tests du parseur utilisent de petits fragments HTML et ne contactent pas le
site :

```bash
uv run python -m unittest discover -s tests -v
```

Le smoke test réseau ne remplace jamais les exports existants :

```bash
uv run python scripts/run_scrape.py --dataset series --smoke
uv run python scripts/run_scrape.py --dataset populaires --smoke
```

Ces commandes doivent réussir tant que le User-Agent identifiable reste accepté.
Si Cloudflare renvoie de nouveau `403`, elles échouent explicitement avec
`manganews_access_blocked_http_403`. Un échec silencieux à zéro ligne n'est plus
considéré comme un succès.

## Crawl complet, uniquement avec autorisation

Utiliser un User-Agent identifiable contenant un contact convenu avec le site :

```bash
export MANGANEWS_USER_AGENT='mon-projet-manga/1.0 (contact: email@example.org)'
uv run python scripts/run_scrape.py --dataset series
uv run python scripts/run_scrape.py --dataset populaires
```

Le lanceur conserve le crawl et la file de requêtes dans `data/runs/`, vérifie
que Scrapy s'est réellement arrêté avec la raison `finished`, déduplique les URL,
puis remplace l'export canonique de façon atomique uniquement si le seuil minimal
est atteint :

- séries : 10 000 lignes ;
- populaires : 40 lignes.

Quand un ancien snapshot existe, le seuil automatique interdit aussi une baisse
de plus de 5 % du nombre de lignes. Le seuil reste ajustable avec `--min-items`.
Les destinations par défaut sont
`data/enriched/manganews_series.jsonl` et
`data/enriched/populaires.jsonl`.

En cas d'interruption, le lanceur affiche la commande exacte de reprise. La reprise
relit les index A-Z dans un `JOBDIR` neuf et ignore les URL déjà présentes dans le
JSONL partiel ; elle ne dépend donc pas du curseur interne de la file Scrapy qui peut
manquer après un arrêt brutal. La commande a la forme suivante :

```bash
uv run python scripts/run_scrape.py --dataset series \
  --resume data/runs/series-AAAAMMJJTHHMMSSZ-xxxx
```

Pour un diagnostic manuel, toujours écrire hors des exports canoniques :

```bash
uv run scrapy crawl manganews_series \
  -a detail_url=https://www.manga-news.com/index.php/serie/Manga \
  -O /tmp/manganews_series_smoke.jsonl
```

Il n'existe pas de `JOBDIR` global. Chaque nouveau crawl et chaque tentative de
reprise reçoivent une file neuve. `--resume` conserve le JSONL du même crawl et
reconstruit uniquement les requêtes encore manquantes depuis les index du site.

Le snapshot du 31 décembre 2025 est conservé localement dans
`data/archive/2025-12-31/`. Son manifeste versionné contient les volumes et les
empreintes SHA-256 ; les JSONL eux-mêmes restent ignorés par Git.

## Validation des JSONL

```bash
uv run python scripts/run_all_validations_gx110.py
```

Avec backfill automatique :

```bash
uv run python scripts/run_all_validations_gx110.py --do-backfill
```

Validations unitaires :

```bash
uv run python scripts/validate_manganews_series_gx110.py \
  --file data/enriched/manganews_series.jsonl
uv run python scripts/validate_populaires_gx110.py \
  --file data/enriched/populaires.jsonl
```

## Import PostgreSQL

Le DSN doit venir de `.env`, `POSTGRES_DSN` ou `APIMANGA_DSN`. Aucun mot de
passe n'est défini dans le code.

```bash
uv run python scripts/run_prod_import.py --dataset series
uv run python scripts/run_prod_import.py --dataset populaires
```

## Fichiers principaux

- `src/manga_news_scraper/spiders/manganews_series.py` : index A-Z et fiches ;
- `src/manga_news_scraper/spiders/manganews_populaires.py` : classements ;
- `src/manga_news_scraper/spiders/_access.py` : détection 403/429/503 et
  challenges Cloudflare ;
- `scripts/run_scrape.py` : reprise, validation, seuil et promotion atomique ;
- `src/manga_news_scraper/settings.py` : débit, robots.txt et configuration ;
- `tests/test_manganews_spiders.py` : non-régression du parseur.
