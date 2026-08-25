# ApiManga

API **FastAPI** (Bloc 1) connectée à **PostgreSQL** (schéma `manga`) pour exposer :
- des métadonnées **Kitsu** (synopsis, ranks, tags, auteurs…)
- un corpus **RAG-ready** (documents prêts à être exportés puis vectorisés, ex. FAISS)
- une **recherche plein texte** (Full-Text Search PostgreSQL) sur le corpus

Objectif : valider le **Bloc 1** (collecte → nettoyage/normalisation → stockage en base → exposition via API).

> Ce module est une API **en lecture seule** : il expose les données déjà chargées
> dans PostgreSQL. La collecte Kitsu est réalisée dans `03_kitsu_api_exports/` et le
> scraping Manga-News dans `01_scraping_manganews/`.

## Démarrage rapide

### En local avec `uv`

```bash
uv sync --all-groups
uv run uvicorn app.main:app --reload --env-file .env
```

### Avec Docker Compose

```bash
export API_KEYS="app_backend:$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-40)"
docker compose up --build
```

`API_KEYS` est **obligatoire** : sans elle l'API refuse de démarrer (cf.
« Autorisation : clé d'API »). Le Compose la transmet depuis l'environnement et
n'en contient aucune.

API : `http://localhost:8000`  
Swagger UI : `http://localhost:8000/docs` — ouvert, sans clé.

## Prérequis

- Docker + Docker Compose (recommandé pour lancer l’API)
- Une base PostgreSQL accessible (local, WSL, serveur, futur cloud)
- Un fichier `.env` local est recommandé pour les paramètres de connexion ; le
  Compose possède des valeurs par défaut et démarre aussi sans ce fichier.

## Configuration

Copier `.env.example` vers `.env` puis compléter si besoin.

L’API lit les variables suivantes (avec valeurs par défaut si non définies) :

| Variable | Défaut | Description |
| --- | --- | --- |
| `API_KEYS` | *(aucun)* | **Obligatoire.** Trousseau `nom:clé,…` — sans lui l'API refuse de démarrer |
| `LOG_LEVEL` | `INFO` | Niveau du journal applicatif |
| `DB_HOST` | `host.docker.internal` | Hôte PostgreSQL |
| `DB_PORT` | `5432` | Port PostgreSQL |
| `DB_NAME` | `apimanga` | Base de données |
| `DB_USER` | `manga_api` | Rôle de connexion — consultation seule |
| `DB_PASSWORD` | *(vide)* | Mot de passe du rôle |
| `DB_CONNECT_TIMEOUT` | `5` | Délai de connexion PostgreSQL (secondes) |
| `DB_POOL_TIMEOUT` | `5` | Attente maximale d'une connexion du pool |
| `DB_POOL_MIN_SIZE` | `1` | Nombre minimal de connexions du pool |
| `DB_POOL_MAX_SIZE` | `5` | Nombre maximal de connexions du pool |

Exemple de `.env` :

```bash
APP_ENV=development
APP_NAME=API Manga

API_KEYS=app_backend:une-cle-de-32-caracteres-minimum-ici

DB_HOST=host.docker.internal
DB_PORT=5432
DB_NAME=apimanga
DB_USER=manga_api
DB_PASSWORD=
```

## Autorisation : clé d'API

Les endpoints de **données** exigent un en-tête `X-API-Key`. La **documentation**
et les **sondes** restent ouvertes. La règle vaut **route par route**, sans
exception ni variante par famille de ressource :

| Routes | Clé exigée | Pourquoi |
| --- | --- | --- |
| `/live` · `/health` | **non** | sondes : un orchestrateur n'a pas à porter de secret pour demander si le processus vit |
| `/docs` · `/redoc` · `/openapi.json` | **non** | contrat : un contrat qu'on ne peut pas lire ne s'intègre pas |
| `/kitsu/{kitsu_id}` · `/search` | **oui** | données |
| `/rag/preview` · `/rag/export` · `/rag/export/composition` · `/rag/doc/{doc_key}` | **oui** | données |
| `/series/{series_id}` · `/series/{series_id}/volumes` · `/series/{series_id}/reviews` | **oui** | données |
| `/identity/{work_uid}` · `/coverage` | **oui** | données |

**C'est une règle, pas une omission : la documentation est ouverte, les données
sont fermées.** Un contrat qu'on ne peut pas lire ne s'intègre pas ; une donnée
qu'on peut lire sans s'annoncer n'est pas protégée. Les sondes restent ouvertes
pour la raison symétrique : un `/live` sous clé rend l'API impilotable par un
orchestrateur, qui n'a pas à porter de secret pour demander si le processus vit.

Ce tableau n'est pas maintenu à la main : `tests/test_openapi.py` le relit et
le confronte au schéma OpenAPI. Une route ajoutée sans y être inscrite fait
tomber la suite — **c'est exactement la dérive qui s'était produite**, les cinq
routes de catalogue ayant été livrées sans que la règle les mentionne.

### La clé nomme un consommateur, pas une personne

`API_KEYS` se lit `nom:clé`, plusieurs entrées séparées par des virgules :

```bash
API_KEYS=app_backend:<clé>,batch_rag:<autre clé>
```

Le nom désigne le **consommateur** — `app_backend`, `batch_rag` — jamais un rôle
humain. Le client final ne détient aucune clé : c'est le back-end qui la porte et
qui, lui, authentifie ses utilisateurs. Ce nom est ce qui apparaît dans le
journal ; il permet de répondre à « quel service a lu quoi », et de révoquer un
consommateur sans toucher aux autres.

Contraintes, vérifiées au démarrage : nom en `[a-z0-9_-]+` non vide, clé de
**32 caractères minimum**, noms et clés uniques.

### Défaillance fermée

Si `API_KEYS` est **absente, vide ou malformée**, l'application **refuse de
démarrer** :

```
ValueError: API_KEYS est absente : l'API refuse de démarrer sans trousseau.
```

Il n'existe aucun chemin par lequel l'API démarrerait en accès ouvert. Une API
de données qui démarre sans trousseau est une API publique : c'est un incident,
pas un mode dégradé. C'est aussi pourquoi `.env.example` porte `API_KEYS=` vide —
copié tel quel, il fait échouer le démarrage, ce qui est le comportement voulu.

### Obtenir et faire tourner une clé

```bash
# Générer une clé (40 caractères, alphabet URL-sûr)
openssl rand -base64 32 | tr -d '=+/' | cut -c1-40
```

Elle se pose dans `.env` (non versionné) ou dans le secret store de la
plateforme — **jamais dans le dépôt**, jamais dans `docker-compose.yml`.

**Rotation, sans interruption** — le format multi-entrées existe pour ça :

1. ajouter la nouvelle clé à côté de l'ancienne :
   `API_KEYS=app_backend:<ancienne>,app_backend_v2:<nouvelle>` ;
2. redémarrer l'API — les deux clés sont acceptées ;
3. basculer le consommateur sur la nouvelle clé ;
4. vérifier dans le journal que le libellé `app_backend` n'apparaît plus ;
5. retirer l'ancienne entrée et redémarrer.

Une clé compromise se révoque en la retirant d'`API_KEYS` et en redémarrant.

### Ce que le journal consigne

Le journal **ne contient jamais de clé** — ni valide, ni rejetée. Une clé
rejetée reste une clé : la journaliser la publierait à quiconque lit les
journaux. Il porte le libellé du consommateur, ou rien :

```
INFO  app.security: appel autorisé sur /rag/export/composition, consommateur « app_backend »
WARN  app.security: appel refusé sur /rag/export/composition : clé d'API inconnue
WARN  app.security: appel refusé sur /rag/export : en-tête X-API-Key absent
```

Le journal distingue les deux causes de refus ; **la réponse HTTP, non**. Les
deux cas renvoient un `401` au message identique, pour ne pas indiquer à un
appelant que sa clé a bien été lue puis rejetée — donc qu'elle a la bonne forme.

### Appeler l'API

```bash
curl -s -H "X-API-Key: $API_KEY" "http://localhost:8000/rag/export?limit=5"
curl -s "http://localhost:8000/health"      # ouvert, sans clé
```

## Prérequis : le rôle de consultation

L'API se connecte sous **`manga_api`**, un rôle qui n'a **aucun droit
d'écriture** : il hérite ses privilèges de `manga_ro`, à qui la migration `012`
accorde `SELECT` sur le schéma `manga` et rien d'autre. Une API en lecture seule
qui se connecterait en superutilisateur ne serait en lecture seule que par
convention ; ici la base refuse l'écriture, quoi que fasse le code.

Le rôle doit exister avant le démarrage. Il n'est pas créé par une migration —
il porte un mot de passe, qui n'a pas sa place dans le dépôt :

```bash
cd ../database
DATABASE_URL='postgresql://…' uv run python migrate.py up      # crée manga_ro
MANGA_API_PASSWORD="$(openssl rand -base64 24)" \
  sh outils/creer_role_lecture.sh 'postgresql://postgres@…/apimanga'
```

Procédure complète, périmètre exact des droits et stockage du mot de passe :
**`../database/README.md`, section « Accès en consultation »**.

Si le rôle est absent, l'API démarre — le pool s'ouvre sans bloquer — mais
`/health` répond `503` avec `{"status":"degraded","db":"error"}` et les endpoints
de données `503 database unavailable`. Le journal du conteneur porte la cause
réelle :

```
FATAL:  password authentication failed for user "manga_api"
```

ou, si le rôle existe mais n'est pas membre de `manga_ro` :

```
ERROR:  permission denied for table kitsu_series_core
```

## Schéma de la base

**La source de vérité du schéma est `../database/migrations/`, et elle seule.**
Ce module ne transporte aucun DDL : il est en lecture seule et ne crée rien, ni au
démarrage ni ailleurs. Pour préparer une base, on joue les migrations :

```bash
cd ../database
DATABASE_URL='postgresql://…' uv run python migrate.py up
DATABASE_URL='postgresql://…' uv run python migrate.py status
```

Le Compose d'intégration applique ces mêmes migrations à une base jetable avant de
démarrer l'API (cf. « Validation du module »). Le contrat SQL du module n'est donc
plus un fichier à part susceptible de dériver : c'est le schéma de production lui-même
qui est testé.

## Périmètre & données

### Sources

- **Kitsu** (API)
  - `most_popular.json` : popularité globale (rank)
  - `top_publishing.json` : classement “publishing/rating” (rank)
  - `trending_weekly.json` : tendance hebdo (ordre d’apparition → rang calculé)
- **Manga Sanctuary** (scraping / exports)
  - séries / volumes / critiques (reviews)

### `ms_reviews` et `ms_reviews_all` : deux tables, un piège

Deux tables de critiques coexistent dans le schéma `manga`. Confondre les deux
fait disparaître les deux tiers du corpus **sans qu'aucune erreur ne se
produise** — elles portent les mêmes colonnes.

| Table | Lignes | Ce que c'est |
|---|---:|---|
| `manga.ms_reviews_all` | **11 074** | le **référentiel complet** des critiques collectées |
| `manga.ms_reviews` | 3 187 | le corpus RAG **historique**, un sous-ensemble filtré pour la recherche |

`GET /series/{series_id}/reviews` lit **`ms_reviews_all`**. Le corpus RAG, lui,
garde `ms_reviews` et ses propres endpoints (`/rag/*`, `/search`) : lire une
table n'est pas reconstruire un corpus, et les deux besoins n'ont pas le même
filtre.

Pour rendre vérifiable la source d'une citation, les deux chemins de mise à
disposition exposent `review_url`, qui pointe la critique publiée à la source ;
ils n'exposent nulle part ni identifiant de membre ni URL de profil.

Un test unitaire vérifie la table interrogée
(`test_reviews_lit_le_referentiel_complet_pas_le_corpus_rag`), parce qu'une
régression y serait invisible à la relecture.

### Données exposées côté RAG

Le corpus RAG est assemblé dans PostgreSQL via une chaîne de **vues**, que l'API
consomme par son extrémité :

```
rag_export_docs        ← ce que lit l'API (textes vides écartés)
  └─ rag_docs_scored   ← ajoute boost_score
      └─ rag_docs_all_v2
          ├─ rag_docs_all        → rag_kitsu_docs  (source « kitsu_synopsis »)
          │                      → rag_reviews_docs (source « ms_review »)
          └─ rag_ms_hybrid_docs  → ms_kitsu_map ⨯ les deux précédentes
                                   (source « ms_hybrid »)
```

Trois sources cohabitent donc dans le corpus servi par `/rag/export` et `/search` :
`kitsu_synopsis`, `ms_hybrid` et `ms_review`.

### 7) Catalogue, identité et couverture

Cinq endpoints exposent ce que le projet a construit : le socle catalogue Manga
Sanctuary, le référentiel d'identité et le référentiel complet des critiques.
Tous sont protégés par `X-API-Key`, comme le reste des routes de données.

| Endpoint | Source | Sa raison d'être |
|---|---|---|
| `GET /series/{series_id}` | `ms_series_enriched` | sans elle, une application sait quoi conseiller mais pas quoi afficher |
| `GET /series/{series_id}/volumes` | `ms_volumes_enriched` | tomaison, EAN, format — ce qu'un libraire vend |
| `GET /series/{series_id}/reviews` | **`ms_reviews_all`** | le référentiel complet, 11 074 critiques |
| `GET /identity/{work_uid}` | `work_identity` ⨝ `v_match_current` | les identifiants croisés **et la provenance de chaque lien** |
| `GET /coverage` | agrégats | ce que le corpus contient, et ce qu'il ne contient pas |

```bash
curl -s -H "X-API-Key: $API_KEY" http://localhost:8000/series/8514
curl -s -H "X-API-Key: $API_KEY" "http://localhost:8000/series/8514/volumes?limit=20"
curl -s -H "X-API-Key: $API_KEY" "http://localhost:8000/series/8514/reviews?limit=20"
curl -s -H "X-API-Key: $API_KEY" http://localhost:8000/identity/14689
curl -s -H "X-API-Key: $API_KEY" http://localhost:8000/coverage
```

**Les genres viennent en deux champs, et aucun ne remplace l'autre.**
`genres_source` porte les libellés bruts de Manga Sanctuary (français, casse
d'origine, 12 652 séries) ; `genres_enriched` porte les codes normalisés du
référentiel, fusionnant les deux sources et leur hiérarchie (12 952 séries).
Aucun `COALESCE` n'est appliqué côté HTTP : il fabriquerait une définition de
« genres » qui n'existe nulle part en base. *L'API expose, elle ne crée pas.*
Chaque code normalisé est rendu avec son libellé d'affichage — le code est la
valeur stable, le libellé une commodité.

**Ce qui n'est pas exposé.** `ms_series_enriched` porte des colonnes de travail
du rapprochement (`ms_title_norm_x`, `_other_titles_list`, `matched_title_norm`,
`fuzzy_low_score`…) : ce sont des états intermédiaires d'un calcul, pas des
faits sur l'œuvre. Ce que le rapprochement a *conclu* se lit sur
`/identity/{work_uid}`. Sur la fiche, seul le drapeau `needs_review` subsiste,
pour qu'une application puisse signaler une donnée sous réserve sans second
appel. Le bloc Kitsu est réduit à `kitsu_id` : `/kitsu/{kitsu_id}` reste
l'endroit unique de ces métadonnées. Les tags dérivés ne sont pas exposés — ils
sont périmés depuis juin 2026, faute de référentiel de tags.

**404 ou liste vide.** Une série inexistante répond **404** ; une série qui
existe mais n'a aucun volume ni aucune critique répond **200 avec `items: []`**.
Confondre les deux dirait à un client qu'une série n'existe pas alors qu'elle
n'a simplement pas encore été commentée.

**Pagination `limit`/`offset`, et non un curseur** — le choix inverse de
`/rag/export`, pour la raison inverse. Un curseur protège d'un `OFFSET` profond
sur un corpus parcouru en entier ; ces collections-ci sont bornées par la série
qui les porte (la plus fournie compte 279 volumes). La profondeur est
structurellement faible, et un curseur opaque imposerait un protocole pour
parcourir dix lignes.

**Deux défauts de source sont exposés, pas masqués.** `volume.editeur` contient
le libellé `Mag. de prépublication` sur ses 104 050 lignes renseignées — le
sélecteur du module 04 a capturé une étiquette au lieu d'une valeur — et
`volume.status` lit `Complète Complète` sur 44 910 lignes. Les deux le disent
dans leur description OpenAPI. Un défaut visible peut être corrigé ; masqué, il
se transmet.

**`/coverage` affiche les limites connues**, mesurées à l'appel et jamais
codées en dur : 1 694 séries sans genre dans aucune source, 525 séries sur le
seul code générique `lgbt` faute d'appariement Kitsu fiable. Une limite mesurée
et affichée est une force ; découverte par un tiers, c'est une faute.

## Architecture

- **FastAPI** (conteneur Docker) → lit PostgreSQL en SQL (psycopg)
- **PostgreSQL** (hors conteneur ou conteneur séparé) → tables, index, vues du schéma `manga`

## Vérification

```bash
curl -s http://localhost:8000/live
curl -s http://localhost:8000/health
```

Réponse attendue :

```json
{"status":"ok","db":"ok"}
```

Ces deux sondes sont **ouvertes** : ni l'une ni l'autre n'exige de clé, un
orchestrateur n'ayant pas à porter de secret pour demander si le processus vit.

`/live` teste seulement le processus API. `/health` teste aussi PostgreSQL et renvoie
HTTP 503 avec une réponse neutralisée lorsque la base est indisponible.

## Endpoints

### 1) Vie du processus

`GET /live` — vérifie que le processus FastAPI répond, sans dépendre de PostgreSQL.

### 2) Santé / connexion DB

`GET /health` — vérifie que l’API répond et que PostgreSQL est joignable.

```bash
curl -s http://localhost:8000/health
```

### 3) Métadonnées Kitsu

`GET /kitsu/{kitsu_id}` — lit `manga.kitsu_series_core`.

```bash
curl -s -H "X-API-Key: $API_KEY" http://localhost:8000/kitsu/38
```

Champs (exemple) :
`kitsu_id`, `slug`, `title_canonical`, `synopsis_clean`, `rating_average_10`, `rating_rank`, `popularity_rank`.

### 4) Aperçu du corpus RAG — `GET /rag/preview`

`GET /rag/preview?limit=20&offset=0` — échantillon classé par pertinence métier.

- `limit` : `1..200` (défaut `20`)
- `offset` : `0..20000` (défaut `0`)
- tri : `boost_score DESC NULLS LAST, doc_key`

**Cet endpoint n'est ni exhaustif ni complet, par décision.** `doc_text` y est
coupé à 500 caractères et `offset` y est plafonné : le fond du corpus est
inatteignable ici. C'est ce qu'est un aperçu — il sert à regarder ce que
contient le corpus, classé par intérêt. Pour lire la totalité, voir
`/rag/export`.

```bash
curl -s -H "X-API-Key: $API_KEY" "http://localhost:8000/rag/preview?limit=3"
```

### 4 bis) Export exhaustif — `GET /rag/export`

`GET /rag/export?limit=200&cursor=<curseur>` — **la totalité** du corpus, en
**texte intégral**, paginée par **curseur**.

| | `/rag/preview` | `/rag/export` |
| --- | --- | --- |
| Texte | tronqué à 500 caractères | **intégral** |
| Ordre | `boost_score DESC NULLS LAST, doc_key` | `doc_key COLLATE "C"` croissant |
| Pagination | `offset`/`limit`, plafonnée | **curseur, sans limite de profondeur** |
| Exhaustivité | **non** — assumé | **oui** — vérifiée par les tests |

- `limit` : `1..200` (défaut `20`). Conservé bas à dessein : à texte intégral,
  une page pèse ~142 ko en moyenne et un document seul peut atteindre 148 ko.
- `cursor` : opaque, renvoyé dans `next_cursor`. À repasser tel quel.
- **`offset` a été retiré** — pas déprécié, retiré. Un `OFFSET` plafonné rend le
  fond du corpus inatteignable, et un `OFFSET` profond coûte un parcours complet
  à chaque page. Le paramètre est simplement ignoré s'il est fourni.

**Contrat de parcours.** Suivre `next_cursor` jusqu'à ce qu'il vaille `null` :
chaque document est alors vu **une fois et une seule**. Une page renvoyant moins
de `limit` éléments est la dernière ; si la taille du corpus est un multiple
exact de `limit`, une dernière page vide clôt le parcours.

```bash
CURSOR=""
while : ; do
  PAGE=$(curl -s -H "X-API-Key: $API_KEY" \
    "http://localhost:8000/rag/export?limit=200&cursor=$CURSOR")
  echo "$PAGE" | jq -c '.items[] | {doc_key, source}'
  CURSOR=$(echo "$PAGE" | jq -r '.next_cursor // empty')
  [ -z "$CURSOR" ] && break
done
```

Le curseur est le `doc_key` de la dernière ligne rendue, encodé en base64url.
Il est **opaque** : sa forme peut changer sans préavis, un client ne doit ni le
fabriquer ni l'interpréter. Un curseur illisible — hors alphabet base64, tronqué,
non décodable — donne un **`422`**, jamais un `500`.

L'ordre de parcours est `doc_key COLLATE "C"`, c'est-à-dire octet par octet.
Il **n'a aucune signification métier** : il ne sert qu'à garantir qu'aucun
document n'est sauté ni rendu deux fois. Une comparaison binaire est stable quelle
que soit la collation de la base ou la locale du système, là où un tri
linguistique peut changer entre deux versions d'ICU et faire silencieusement
dériver le parcours.

### 4 ter) Composition du corpus — `GET /rag/export/composition`

`GET /rag/export/composition` — total, décompte par source, horodatage de la
mesure.

```json
{
  "total": 51880,
  "by_source": [
    {"source": "kitsu_synopsis", "documents": 43085},
    {"source": "ms_hybrid", "documents": 5608},
    {"source": "ms_review", "documents": 3187}
  ],
  "measured_at": "2026-08-24T18:41:48.835429Z"
}
```

Cet endpoint existe **par conception, non par confort** : une pagination par
curseur ne peut renvoyer aucun total, et le `COUNT(*)` — mesuré à ~330 ms —
disparaît ainsi du coût de *chaque* page. Effet second assumé : la composition
du corpus devient explicite au lieu de rester tacite.

### 5) Récupération d’un document complet

`GET /rag/doc/{doc_key}` — retourne `doc_text` complet + métadonnées (issu de `manga.rag_export_docs`).

```bash
curl -s -H "X-API-Key: $API_KEY" "http://localhost:8000/rag/doc/kitsu:38" | head
```

### 6) Recherche plein texte (PostgreSQL FTS)

`GET /search?q=...&limit=10&offset=0` — recherche plein texte dans `manga.rag_export_docs` via `to_tsvector('simple', doc_text)` + `websearch_to_tsquery`.

- `q` : min `2` caractères
- `limit` : `1..50` (défaut `10`)
- `offset` : `>= 0`

```bash
curl -s -H "X-API-Key: $API_KEY" "http://localhost:8000/search?q=one%20piece&limit=5" | head
curl -s -H "X-API-Key: $API_KEY" "http://localhost:8000/search?q=shounen%20fantasy&limit=5" | head
```

> **La section 7) est ailleurs, à dessein.** Les cinq routes de catalogue —
> `/series/{series_id}`, `/series/{series_id}/volumes`,
> `/series/{series_id}/reviews`, `/identity/{work_uid}`, `/coverage` — sont
> documentées avec leurs sources sous
> [« 7) Catalogue, identité et couverture »](#7-catalogue-identité-et-couverture),
> parce que ce qu'elles exposent ne se lit qu'avec le périmètre en main. Elles
> sont protégées comme les autres routes de données.
>
> **Les 13 routes de l'API sont ainsi couvertes** : `/live`, `/health`, puis
> 1) à 7). Le contrat complet, machine-lisible, est dans
> [`openapi.json`](openapi.json), régénéré par
> `uv run python outils/exporter_openapi.py` et tenu à jour par la suite de
> tests.

## Modèle de scoring : `boost_score`

Le tri principal du corpus RAG combine :
- score texte (FTS) via `ts_rank_cd`
- score de boost basé sur les signaux hebdomadaires Kitsu : `trending_pos`, `popular_pos`, `top_pos`

La vue `manga.rag_docs_scored` calcule le boost. Les poids sont ceux de la base de
production :

| Signal | Poids | Contribution |
| --- | --- | --- |
| tendance (`trending_pos`) | 100 | `100 / position` |
| popularité (`popular_pos`) | 30 | `30 / position` |
| publication en cours (`top_pos`) | 20 | `20 / position` |

Un signal absent ne contribue pas. Plus la position est haute (`1`, `2`, `3`…), plus
le boost augmente : les contenus « chauds » remontent à l'export et à la recherche.
Exemple, pour un document en position 1 / 2 / 4 : `100/1 + 30/2 + 20/4 = 120`.

Un document sans aucun signal hebdomadaire — les critiques, notamment — a donc un
boost nul et ne remonte que par son score texte.

## Validation du module

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
docker compose config
docker compose build
docker compose -f compose.integration.yml run --rm --build smoke
docker compose -f compose.integration.yml down -v
```

Le Compose d'intégration démarre une PostgreSQL 16 temporaire (`tmpfs`, donc
réellement jetable), **applique les 15 migrations de `../database/migrations/`**,
injecte la fixture de test puis appelle réellement les endpoints HTTP. Le schéma
vérifié est celui de la production : le smoke test contrôle notamment que la formule
de boost servie est bien celle du tableau ci-dessus, et échoue si une pondération
antérieure était rétablie.

Le harnais couvre aussi le contrat d'accès : il vérifie que les **onze** routes
de données refusent un appel sans clé, avec une clé invalide et avec la bonne
clé à un caractère près ; que les sondes et la documentation répondent sans clé ; et
que le parcours complet de `/rag/export` par curseur rend chaque document une
fois et une seule, en texte intégral. Le service `api` du Compose reçoit une clé
jetable, propre au harnais.

`down -v` est nécessaire entre deux exécutions : la fixture n'est pas rejouable sur
une base déjà peuplée.

### Le contrat OpenAPI, versionné et validé

```bash
uv run python outils/exporter_openapi.py   # régénère openapi.json
uv run pytest tests/test_openapi.py        # le contrat, relu route par route
```

[`openapi.json`](openapi.json) est le schéma que sert `/openapi.json`, écrit
dans le dépôt. Une sortie volatile ne se relit pas à une date donnée et ne se
compare pas entre deux versions ; écrit ici, le contrat devient **opposable et
diffable** — un changement de route apparaît en revue au même titre que le code
qui le produit. Il n'est pas maintenu à la main : un test refuse tout écart avec
le schéma généré.

Ce que la suite vérifie sur le contrat, et qui ne se déduit pas du code :

- **conformité au standard**, par un validateur OpenAPI 3.1 **indépendant**
  (`openapi-spec-validator`, dépendance de dev — l'API ne l'embarque pas).
  « Généré par FastAPI » n'est pas une preuve : c'est le même générateur qui
  sert `/docs`, il ne peut pas s'auditer lui-même ;
- **les 13 routes déclarées = les 13 chemins du schéma**, égalité d'ensembles,
  `/live` et `/health` compris ;
- `summary`, `tags` et description **non vides sur 100 % des routes**, et
  aucun `summary` laissé à la génération automatique de FastAPI ;
- **chaque code de réponse levé est déclaré** — les 404 et 503 du code, le 401
  de l'autorisation, le 422 des paramètres validés ;
- **le tableau d'autorisation du README dit vrai**, route par route.
