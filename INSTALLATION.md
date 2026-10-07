# Installation et vérification reproductible

Ce dépôt distingue deux procédures qui n'ont ni le même but ni les mêmes
données. La procédure courte est la preuve d'installation attendue pour C4 ; la
procédure de production suppose l'accès licite aux sources et aux snapshots.

| | Vérification de l'installation | Installation de production |
|---|---|---|
| Objet | Prouver que le schéma, les droits et l'API sont conformes | Obtenir la base réelle |
| Données | Fixture réduite et versionnée | Snapshots issus des collectes |
| Durée | Quelques minutes | Environ 11 h pour le seul crawl Manga Sanctuary |
| Prérequis | Docker Engine avec le plugin Compose v2 (`docker compose`) | PostgreSQL 16 avec l'extension pgvector 0.6.0, Python 3.12, `uv`, `psql`, `createdb`, OpenSSL, accès aux sources et autorisations nécessaires |

Les commandes ci-dessous se lancent depuis la racine du dépôt, sauf mention
contraire. Aucun mot de passe ni aucune clé ne doit être ajouté à Git.

## 1. Vérification de l'installation — procédure C4

Cette procédure est autonome à partir du dépôt et de Docker. Elle crée une
PostgreSQL 16 jetable (image `pgvector/pgvector:0.6.0-pg16`, épinglée par digest), applique **toutes les migrations présentes dans
`database/migrations/`**, crée `manga_ro` puis `manga_api`, injecte une fixture,
prouve que le rôle applicatif ne peut pas écrire, démarre l'API et exécute les
appels HTTP de contrôle.

```bash
cd 02_api_manga
docker compose -f compose.integration.yml down -v --remove-orphans
docker compose -f compose.integration.yml run --rm --build smoke
docker compose -f compose.integration.yml down -v --remove-orphans
```

La deuxième commande doit sortir avec le code `0`. Le smoke contrôle notamment :

- `/live` et `/health`, dont la réponse saine est
  `{"status":"ok","db":"ok"}` pour `/health` ;
- un appel authentifié sur chaque route de données ;
- les refus `401` sans clé ou avec une clé incorrecte ;
- les objets SQL réellement reconstruits par les migrations ;
- le refus des écritures sous `manga_api`.

`down -v` est obligatoire après l'essai : la fixture n'est pas rejouable dans
une base déjà peuplée. Cette base en `tmpfs` est une preuve technique, pas un
snapshot de production.

## 2. Installation de production — schéma et accès

### 2.1 Vérifier les outils

Le dépôt ne fournit pas l'installation système de PostgreSQL, Docker ou `uv`,
qui dépend du système d'exploitation. Il vérifie seulement leur présence :

```bash
psql --version
createdb --version
python3 --version
uv --version
openssl version
docker --version
docker compose version
```

PostgreSQL 16 est la version de référence et Python 3.12 satisfait les
contraintes communes du runner, des chargeurs et de l'API. Les dépendances
Python sont résolues par les `pyproject.toml` et `uv.lock` propres aux modules.

La migration `020` crée l'extension **pgvector**, qui n'est pas livrée avec
PostgreSQL : son paquet doit être installé sur le serveur (Debian / Ubuntu :
`postgresql-16-pgvector`). La version de référence est **0.6.0** ; la commande
ci-dessous doit la rendre avant d'aller plus loin. `CREATE EXTENSION vector`
exige un superutilisateur — c'est le cas du rôle `postgres` utilisé ici.

```bash
psql --dbname=postgres --tuples-only --no-align --command \
  "SELECT default_version FROM pg_available_extensions WHERE name = 'vector'"
```

### 2.2 Créer la base et vérifier son propriétaire

La procédure utilise le rôle propriétaire `postgres`, déjà créé à
l'initialisation normale d'un cluster PostgreSQL. Son éventuel mot de passe doit
être fourni par la configuration PostgreSQL du poste. Avec `~/.pgpass`, le
champ base doit couvrir à la fois la base administrative `postgres` et la base
qui va être créée : utiliser `*`, ou écrire deux lignes. Par exemple, remplacer
les champs entre chevrons puis imposer les droits du fichier :

```text
<hôte>:<port>:*:postgres:<mot-de-passe-administrateur>
```

```bash
chmod 600 ~/.pgpass
```

Le secret lui-même n'apparaît dans aucune commande de cette procédure.

`MANGA_DB_NAME` permet d'éprouver la procédure sur un autre nom. En production,
ne pas le définir conserve le nom `apimanga`.

```bash
export PGHOST="${PGHOST:-localhost}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="postgres"
export MANGA_DB_OWNER="postgres"
export MANGA_DB_NAME="${MANGA_DB_NAME:-apimanga}"

test "$(psql --dbname=postgres --set=ON_ERROR_STOP=1 --tuples-only \
  --no-align --command \
  "SELECT rolname || '|' || rolcanlogin FROM pg_roles WHERE rolname = 'postgres'")" \
  = "postgres|true"

createdb --owner="$MANGA_DB_OWNER" "$MANGA_DB_NAME"

export DATABASE_URL="postgresql://$MANGA_DB_OWNER@$PGHOST:$PGPORT/$MANGA_DB_NAME"
psql "$DATABASE_URL" --set=ON_ERROR_STOP=1 --command \
  'SELECT current_database(), current_user;'
```

Le premier `test` doit sortir avec le code `0`, ce qui prouve que le rôle
`postgres` existe et peut se connecter. La dernière commande doit rendre le nom
choisi dans `MANGA_DB_NAME` et
`current_user = postgres`. `createdb` échoue volontairement si la base existe :
la procédure d'installation ne remplace jamais une base existante.

### 2.3 Appliquer et vérifier les migrations

Sur une base neuve, `000_baseline.sql` doit être **exécutée** comme les autres.
La commande `mark-applied 000` est réservée à l'ancienne base historique et ne
fait pas partie d'une installation neuve.

```bash
cd database
uv run python migrate.py up
uv run python migrate.py status
cd ..
```

Le statut doit montrer chaque fichier de `database/migrations/` en état
`appliquée` et terminer par `0 en attente`. Le runner choisit lui-même l'ordre
lexicographique et vérifie les empreintes SHA-256 : aucun numéro de migration
n'est à recopier dans la commande.

### 2.4 Choisir et conserver le mot de passe de `manga_api`

Le script `creer_role_lecture.sh` ne génère pas le mot de passe : il consomme la
valeur fournie dans `MANGA_API_PASSWORD`. Si l'utilisateur a déjà choisi une
valeur, il la place dans cette variable avant le bloc. Sinon, le bloc en génère
une seule fois et, point essentiel, la conserve dans le shell pour les étapes
suivantes.

```bash
MANGA_API_PASSWORD="${MANGA_API_PASSWORD:-$(openssl rand -hex 24)}"
export MANGA_API_PASSWORD

cd database
sh outils/creer_role_lecture.sh "$DATABASE_URL"
cd ..
```

Le relevé final doit indiquer `manga_api`, `peut_se_connecter = t`,
`superutilisateur = f`, `membre_de_manga_ro = t`, `usage_manga = t` et
`usage_staging = f`.

Le secret peut ensuite être rangé dans le fichier libpq. `PGPASSFILE` est
paramétrable afin qu'un essai jetable n'écrive pas dans le fichier personnel :

```bash
export PGPASSFILE="${PGPASSFILE:-$HOME/.pgpass}"
umask 077
touch "$PGPASSFILE"
printf '%s\n' \
  "$PGHOST:$PGPORT:$MANGA_DB_NAME:manga_api:$MANGA_API_PASSWORD" >> "$PGPASSFILE"
chmod 600 "$PGPASSFILE"

psql "postgresql://manga_api@$PGHOST:$PGPORT/$MANGA_DB_NAME" \
  --set=ON_ERROR_STOP=1 --command 'SELECT current_user;'
```

La dernière commande doit rendre `manga_api` sans demander de mot de passe.

### 2.5 Préparer l'environnement de l'API

La même valeur `MANGA_API_PASSWORD` devient `DB_PASSWORD`. La clé HTTP est elle
aussi générée une fois dans le shell puis écrite dans `.env`, qui est ignoré par
Git.

Le bloc suivant prépare le **lancement Docker** de l'API : le conteneur joint la
base installée sur l'hôte via `host.docker.internal`.

```bash
API_KEY="${API_KEY:-$(openssl rand -hex 20)}"
export API_KEY

cd 02_api_manga
umask 077
{
  printf '%s\n' 'APP_ENV=development' 'APP_NAME=API Manga' 'APP_VERSION=0.4.0'
  printf 'API_KEYS=installation:%s\n' "$API_KEY"
  printf '%s\n' 'DB_HOST=host.docker.internal'
  printf 'DB_PORT=%s\n' "$PGPORT"
  printf 'DB_NAME=%s\n' "$MANGA_DB_NAME"
  printf '%s\n' 'DB_USER=manga_api'
  printf 'DB_PASSWORD=%s\n' "$MANGA_API_PASSWORD"
  printf '%s\n' 'DB_CONNECT_TIMEOUT=5' 'DB_POOL_TIMEOUT=5'
  printf '%s\n' 'DB_POOL_MIN_SIZE=1' 'DB_POOL_MAX_SIZE=5'
} > .env
chmod 600 .env
```

Pour lancer `uvicorn` directement sur l'hôte, remplacer uniquement
`DB_HOST=host.docker.internal` par `DB_HOST=localhost`, puis utiliser la commande
locale du README du module 02.

### 2.6 Démarrer et vérifier l'API

```bash
docker compose up --build --detach

for tentative in $(seq 1 30); do
  curl --fail --silent http://localhost:8000/health && break
  sleep 1
done

curl --fail --silent http://localhost:8000/health
test "$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --header "X-API-Key: $API_KEY" http://localhost:8000/coverage)" = "200"
test "$(curl --silent --output /dev/null --write-out '%{http_code}' \
  http://localhost:8000/coverage)" = "401"
```

La première réponse doit être `{"status":"ok","db":"ok"}`. Les deux
commandes `test` doivent sortir avec le code `0`. Un simple `export API_KEYS=…`
ne suffit pas : `.env` doit aussi porter le mot de passe du rôle PostgreSQL.

## 3. Peuplement de production

Les migrations créent un schéma vide. La fixture de vérification ne doit jamais
être jouée dans la base réelle. Les snapshots volumineux sont volontairement
ignorés par Git ; il faut les obtenir auprès des sources, avec les autorisations
nécessaires, avant d'exécuter les chargeurs documentés :

| Source | Collecte | Chargement |
|---|---|---|
| Manga-News | `01_scraping_manganews/README.md` | `scripts/run_prod_import.py` dans le même module |
| Kitsu | `03_kitsu_api_exports/README.md` | `identity.charger_kitsu` et `identity.charger_kitsu_staff` |
| Manga Sanctuary | `04_scraping_manga_sanctuary/README.md` | `identity.charger_ms` |
| Wikidata | aide de `src/identity/wikidata_dump.py` | `identity.charger_wikidata` |
| Manga Insight | `identity.acquerir_mi` | `identity.charger_mi` |
| Genres | CSV versionnés dans `database/donnees/` | `identity.charger_genres` |

Le parcours détaillé et les vérifications de volume sont dans
`GUIDE_PIPELINE.md` et `database/README.md`. Le crawl Manga Sanctuary dure
environ 11 h ; Manga-News peut refuser l'accès par HTTP 403 et ne doit pas être
contourné.

Limite actuelle à ne pas masquer : les tables historiques du corpus API
(`kitsu_series_core`, `rag_kitsu_docs`, `rag_reviews_docs`, notamment) n'ont pas
encore de chargeur de production actuel couvrant une base neuve. La procédure C4
ci-dessus prouve leur structure et leur fonctionnement avec la fixture
versionnée ; reproduire leurs populations réelles suppose les données et le
processus historique. Aucune étape fictive n'est ajoutée ici.
