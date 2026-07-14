# Manga Sanctuary Scraper

Automatise la collecte des séries, tomes et critiques du site [manga-sanctuary.com](https://www.manga-sanctuary.com/bdd/series.html) à l’aide de Scrapy. Le dépôt ne contient que le code : les données collectées (~256 Mo) restent locales.

Manga Sanctuary est le **socle catalogue** du projet (`series_id`, `volume_url`) : ses exports alimentent `manga.ms_series_enriched` / `manga.ms_volumes_enriched`.

## Structure du projet

- `pyproject.toml` / `uv.lock` : configuration du projet gérée par [uv](https://github.com/astral-sh/uv) (Python 3.12).
- `requirements.txt` : alternative minimale pour `pip install`.
- `scripts/run_scrape.py` : **lanceur du crawl** — reprise sur interruption, arrêt sur blocage, promotion atomique du snapshot mensuel. Même pattern d’exécution que `01_scraping_manganews/scripts/run_scrape.py`.
- `manga_sanctuary/` : projet Scrapy généré par `scrapy startproject`. ⚠️ `scrapy.cfg` vit **ici**, pas à la racine du module : les commandes `scrapy` se lancent depuis ce dossier.
  - `manga_sanctuary/spiders/manga_sanctuary_volumes.py` : spider principal qui parcourt toutes les lettres, fiches séries, tomes et critiques staff.
  - `manga_sanctuary/spiders/_access.py` : arrête net le crawl sur 403/429/503 ou challenge anti-bot, **sans rien contourner**.
  - `manga_sanctuary/extensions.py` : écrit la raison de fermeture du crawl, que le lanceur relit pour décider de promouvoir ou non.
- `tests/` : suite **hors réseau**, sur fixtures HTML écrites à la main.
- `canari/` : test de non-régression des sélecteurs (2026-07) — rapport versionné, données locales.
- `data/raw/<AAAA-MM>/` : snapshots mensuels (`manga_sanctuary_volumes.jsonl`, `manga_sanctuary_reviews.jsonl`), **ignorés par Git** : le fichier volumes pèse ~244 Mo, au-delà de la limite de 100 Mo/fichier de GitHub.
- `data/runs/` : état des crawls en cours ou interrompus (JOBDIR, export partiel), ignoré par Git.

## Prérequis

- Python 3.12+
- `uv` (recommandé) ou `pip`

## Installation

```bash
# Créer l’environnement et installer les dépendances
uv sync
```

Sans `uv`, vous pouvez installer les dépendances minimales avec :

```bash
pip install -r requirements.txt
```

## Collecte

Le crawl complet représente ~89 000 volumes et ~11 h : il passe **toujours** par le
lanceur, jamais par un `scrapy crawl` nu. Le lanceur apporte les trois garanties
qu’un crawl de cette taille exige :

1. **reprise** — un `JOBDIR` par run ; une interruption se reprend sans re-crawler l’acquis ;
2. **arrêt sur blocage** — 403/429/503 ou challenge anti-bot ferment le crawl immédiatement ;
3. **promotion validée** — les exports sont écrits dans `data/runs/<run>/`, validés
   (JSON, unicité, plancher anti-régression face au snapshot précédent), puis
   remplacés atomiquement dans `data/raw/<AAAA-MM>/`. Un crawl bloqué ou tronqué
   **n’écrase jamais** le dernier snapshot valide.

```bash
# Crawl complet vers data/raw/<mois courant>/
uv run python scripts/run_scrape.py

# Reprendre un crawl interrompu (le chemin est rappelé à l’interruption)
uv run python scripts/run_scrape.py --resume data/runs/<run>

# Vérifier la chaîne de bout en bout sans rien promouvoir (quelques pages)
uv run python scripts/run_scrape.py --smoke 5
```

Options utiles : `--month AAAA-MM` (snapshot cible), `--min-volumes` / `--min-reviews`
(planchers de promotion), `--log-level`.

Les deux flux d’items (`VolumeItem`, `ReviewItem`) sont produits par le **même**
crawl et promus **ensemble** : ils sont routés vers leurs fichiers respectifs par le
lanceur. Le projet Scrapy ne définit **aucun** `FEEDS` implicite, pour qu’un crawl
bloqué ne puisse pas écraser un export valide avec un fichier partiel.

### Politesse

`ROBOTSTXT_OBEY = True`, AUTOTHROTTLE actif, et un User-Agent identifiable
(`manga-sanctuary-scraper/0.1`, surchargeable par `MANGA_SANCTUARY_USER_AGENT`).
Le robots.txt du site autorise `/bdd/` pour le groupe `*`, dont relève cet UA.
En cas de blocage, le crawl s’arrête : **ne rien contourner**, vérifier
l’autorisation et la politesse avant toute reprise.

## Tests

```bash
uv run --extra dev pytest tests/
```

La suite tourne **sans réseau**, sur des fixtures HTML écrites à la main qui
reproduisent les structures réelles du site (alias en `<li>` frères à label vide,
genres/tags, critiques en `<p>` *et* critiques en texte + `<br>`, EAN-13 présent ou
absent). Elle verrouille les cinq correctifs de sélecteurs validés par le canari,
ainsi que la reprise et la promotion du lanceur.

```bash
uv run scrapy list   # depuis manga_sanctuary/ uniquement
```
