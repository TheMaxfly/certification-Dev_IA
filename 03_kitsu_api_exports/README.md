# ApiManga

Projet Python pour interroger l’API publique de [Kitsu](https://kitsu.io) et produire
des snapshots JSON normalisés ainsi qu'un export brut exhaustif utilisables pour
l'ingestion en base ou l'indexation RAG.

## État du run exhaustif — 2026-07-15

Le run `exports/full_catalog/20260714T152202Z/` est clôturé avec
`status=complete` et `last_error=null` sur le périmètre de production :

- catalogue : **62 768 / 62 768 mangas** ;
- mappings : **62 768 / 62 768 mangas**, 104 726 ressources ;
- staff : **62 768 / 62 768 mangas**, 53 183 ressources ;
- characters : collecte partielle conservée, mais abandonnée et hors production.

Le catalogue, les mappings et le staff n'ont donc pas à être relancés pour le
snapshot courant. Les commandes ci-dessous documentent la reproduction ou un futur
rafraîchissement autorisé.

## Prérequis

- Python 3.9+

## Installation

```bash
uv sync --locked
```

Le lockfile `uv.lock` garantit les versions réellement testées.

## Schéma des snapshots hebdomadaires

Chaque export a la forme:

- `meta`: `{category, source, endpoint, fetched_at, limit, offset}`
- `data`: liste d’objets avec les clés suivantes (toujours présentes, valeurs possibles `null`):
  - `id`, `slug`, `status`, `synopsis`
  - `titles`: `{canonical, en, ja}`
  - `authors`: `[{name, role}]`
  - `ratings`: `{average: float|null, rank: int|null}`
  - `popularity`: `{rank: int|null}`
  - `tags`: `{categories: [str], genres: [str]}`

Le collecteur exhaustif conserve en parallèle les ressources JSON:API brutes en
JSONL afin de ne perdre aucun attribut ou lien non présent dans ce schéma normalisé.

## Exports (runners)

Les runners **ne suppriment pas** les anciens exports: chaque exécution crée un dossier versionné par date/heure.

### Collecteur exhaustif du catalogue

`apimanga-full` collecte le catalogue manga Kitsu sans filtre, puis peut enrichir
chaque fiche avec les relations disponibles. Le run est append-only, dédupliqué et
reprenable grâce à `state.json`.

Le catalogue Kitsu annonçait **62 768 mangas le 2026-07-14**, soit au minimum 3 139
pages de 20 éléments. Il est préférable de procéder par étapes.

#### 1. Catalogue complet + images + top rated

```bash
uv run apimanga-full --catalog-only
```

Chaque fiche brute conserve notamment les titres, synopsis/description, statut,
dates, type, classements, compteurs de volumes et chapitres, fréquences de notes,
ainsi que toutes les tailles de `posterImage` et `coverImage`. Lorsque le catalogue
est terminé, le runner dérive automatiquement un `top_rated.json` complet, trié par
`ratingRank`.

Pour avancer par tranches de 4 000 mangas :

```bash
uv run apimanga-full --catalog-only --max-catalog-pages 200
uv run apimanga-full --catalog-only --max-catalog-pages 200 --resume
```

#### 2. Mappings et staff/auteurs

```bash
uv run apimanga-full --relations mappings,staff --resume
```

- `mappings` conserve les identifiants externes disponibles, notamment MAL, AniList
  et MangaUpdates ;
- `staff` conserve le rôle et la fiche complète de la personne incluse, donc aussi
  son image lorsqu'elle existe ;
- `characters` reste disponible techniquement, mais n'appartient pas au périmètre de
  production actuel.

Pour fractionner cette phase, la limite s'applique séparément à chaque relation :

```bash
uv run apimanga-full --relations mappings,staff \
  --max-relation-manga 500 --resume
```

#### 3. Chapitres, en phase séparée

```bash
uv run apimanga-full --relations chapters \
  --max-relation-manga 100 --resume
```

Les chapitres peuvent représenter beaucoup plus d'appels et de données que le
catalogue lui-même. Kitsu ne fournit pas de ressource `/volumes` individuelle :
seuls `volumeCount` sur la fiche manga et `volumeNumber` sur certains chapitres sont
disponibles. Le référentiel détaillé des volumes doit donc rester Manga Sanctuary.

Pour demander toutes les relations dans un seul run — commande potentiellement très
longue — utiliser `--relations all`.

Le débit par défaut est limité à deux requêtes par seconde. Les réponses 429 et 5xx
sont réessayées avec backoff, les 404 relationnelles sont enregistrées sans bloquer
le run, et toute autre erreur conserve le checkpoint avant l'arrêt.

Sorties principales :

```text
exports/full_catalog/<run_id>/
├── manga.ndjson
├── top_rated.json
├── state.json
├── manifest.json
├── errors.ndjson                 # seulement si nécessaire
└── relations/
    ├── mappings.ndjson
    ├── staff.ndjson
    ├── characters.ndjson
    └── chapters.ndjson
```

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

La validation contrôle aussi l'unicité des identifiants. Le collecteur ignore une
page répétée par l'API Kitsu et continue la pagination jusqu'au nombre de séries
uniques demandé ; il échoue explicitement si la pagination reste bloquée.

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
