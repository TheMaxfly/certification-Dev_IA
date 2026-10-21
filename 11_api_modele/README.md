# 11 — API du modèle

Une API REST qui expose le modèle d'embedding en service : une question entre, une
liste de séries classées sort, avec les passages qui les justifient. Version **1.0.0**,
sous `/v1/`.

## Ce que fait l'API, et ce qu'elle ne fait pas

**Elle fait** :

- encoder une question par le service d'embedding (EmbeddingGemma, servi par Text
  Embeddings Inference), avec le préfixe de question du modèle et sans troncature ;
- classer les séries du catalogue. Le score d'une série est celui de son meilleur
  fragment, sur les vecteurs de **l'encodage en service** et de lui seul ;
- rendre, pour chaque série, ses meilleurs passages : le texte même des fragments du
  corpus ;
- dire dans chaque réponse ce qui a servi : réglages, modèle, révision, corpus,
  encodage, identifiant de requête, durée.

Le classement est **exactement celui que le banc de mesure a mesuré** (module 10,
mesure 8, corpus en service) : le code du banc est réutilisé, pas réécrit. Une étape
de `make verify-poste` le prouve. Elle rejoue par l'API les 69 questions du jeu
d'évaluation gelé ; pour chacune, les dix premières séries doivent égaler celles du
run de référence (69 / 69 le 2026-10-20, scores identiques au bit près).

**Elle ne fait pas** :

- **pas de réponse rédigée** : pas de modèle de langage ici (étape 3) ;
- pas d'interface (étape 4) ;
- **pas d'écriture en base** : rôle et sessions en lecture seule ;
- pas de choix du corpus, de l'encodage, de la révision ou des préfixes par le client.
  Un champ inconnu est refusé : le client ne doit pas croire qu'il règle ce qu'il ne
  règle pas ;
- pas de comptes, de rôles ni de jetons à expiration : une clé par application
  consommatrice. Les utilisateurs vivront dans l'application.

Le module 02 (API de données) est une autre API, inchangée : les données d'un côté,
l'inférence de l'autre.

## Architecture

```text
 application, démonstration                      (client HTTP, une clé chacune)
            │  HTTPS / HTTP local, en-tête X-API-Key
            ▼
 ┌──────────────────────────────┐  réseau interne  ┌───────────────────────────┐
 │ API du modèle (module 11)    │ ───────────────► │ service d'embedding       │
 │ FastAPI, Uvicorn, port 8010  │   /info, /embed  │ Text Embeddings Inference │
 │ classement du banc (10)      │   /tokenize      │ 1.9.4, EmbeddingGemma     │
 │ rattachement (05)            │                  │ (configuration du 09)     │
 └──────────────┬───────────────┘                  └───────────────────────────┘
                │ lecture seule, sous modele_api (membre de modele_ro, migration 023)
                ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ PostgreSQL apimanga — bench : corpus, fragments, vecteurs EmbeddingGemma,    │
 │ registre des encodages, encodage en service (vue) ; manga : rattachement et  │
 │ titre des séries — rien d'autre                                              │
 └──────────────────────────────────────────────────────────────────────────────┘
```

Ce que l'API réutilise, sans le recopier :

| Module | Quoi | Comment |
|---|---|---|
| 09 | le client du service d'embedding (préfixes, refus de troncature, confrontation de `/info` au registre des encodages) et la configuration de l'instance | dépendance locale `uv` |
| 10 | le classement : rattachement des fragments, score d'une série, départage, limitation au catalogue | paquet chargé par son chemin (`api_modele.banc`) |
| 05 | le rattachement (`evaluation/atteignabilite.py`) | chargé par le module 10, par son chemin |

Les versions dont le calcul dépend (numpy, pgvector, psycopg, typer) sont celles du
verrou du module 10 ; `tests/test_verrous.py` compare les deux verrous.

## Démarrer

**Le service d'abord, puis l'API.** Au démarrage, l'API contrôle sa cohérence. Au
moindre écart, elle **refuse de démarrer** (code 3 sous Uvicorn ; dans le Compose, le
conteneur s'arrête). Le motif est écrit au journal (`demarrage_refuse`). Elle refuse
si :

1. la base ne répond pas sous son rôle (`modele_api`) dans le délai (5 s) ;
2. aucun encodage n'est en service (vue `bench.v_encodage_en_service`), ou s'il n'est
   pas terminé, ou si son corpus n'est pas clos ;
3. l'encodage en service n'est pas du modèle, ou de la dimension, que sert l'instance
   configurée ;
4. le service ne répond pas, ou si son `/info` diffère de l'encodage en service :
   modèle, révision des poids, précision, version de l'outil, préfixe de question ;
5. une question de 500 caractères pourrait dépasser la longueur maximale annoncée par
   `/info` ;
6. les vecteurs de l'encodage en service ne se chargent pas tous (77 006 en 17 s le
   2026-10-20 ; pic de 1,5 à 1,8 Go de mémoire, puis 0,5 Go résidents).

Une configuration fautive (clé absente, DSN avec mot de passe, réglage hors bornes)
arrête l'API avant tout (code 2), tous les écarts nommés.

Prérequis communs :

- la base `apimanga` migrée jusqu'à `023` ;
- le rôle de connexion `modele_api`, créé par
  `MODELE_API_PASSWORD=… sh database/outils/creer_role_modele.sh` ;
- son mot de passe dans un fichier pgpass (mode 600), jamais dans le DSN ;
- les poids d'EmbeddingGemma à la révision épinglée, dans le cache Hugging Face de
  l'hôte. Le modèle est à accès contrôlé : la première fois, ils se téléchargent par le
  Compose du module 09, avec un jeton (`09_service_embedding/.env`).

### Avec le Compose (`docker-compose.yml`)

```bash
cd 11_api_modele
cp .env.example .env     # API_MODELE_CLES (une clé par consommateur), au besoin le reste
# Le pgpass du conteneur (ignoré par git), mode 600, une ligne :
#   host.docker.internal:5432:apimanga:modele_api:<mot de passe>
install -m 600 /dev/null .pgpass.conteneur && "${EDITOR:-nano}" .pgpass.conteneur
docker compose up -d --build     # le service d'embedding, puis l'API une fois le service sain
curl -s 127.0.0.1:8010/v1/sante  # {"statut": "ok", …}
docker compose down
```

- **Le service d'embedding** (`embedding`, conteneur `tei-api-modele`) :
  - l'image et la configuration sont celles du module 09 : Text Embeddings Inference
    1.9.4, épinglée par empreinte, et `config/embeddinggemma.env` ;
  - aucun port n'est publié, et le service n'a pas d'accès sortant (réseau `interne`,
    `HF_HUB_OFFLINE`) : il lit les poids dans le cache monté ;
  - son contrôle de santé interroge `/health`.
- **L'API** (`api`) :
  - l'image est construite depuis la racine du dépôt (`Dockerfile`, liste blanche
    `Dockerfile.dockerignore`), avec les dépendances du verrou ; elle tourne sous un
    utilisateur non root ;
  - elle attend que le service soit sain, puis contrôle sa cohérence ; son contrôle de
    santé interroge `/v1/sante` ;
  - elle publie le **seul** port du Compose : `127.0.0.1:8010` par défaut
    (`API_MODELE_ADRESSE_PUBLIEE`, `API_MODELE_PORT_PUBLIE`).
- **La base de l'hôte** est jointe par `host.docker.internal`, depuis le réseau
  `sortie` du Compose (sous-réseau fixe `172.30.11.0/24`). Le serveur PostgreSQL doit
  admettre `modele_api` depuis ce réseau. Il faut pour cela une ligne dans
  `pg_hba.conf`, puis un rechargement du serveur :

  ```text
  host  apimanga  modele_api  172.30.11.0/24  scram-sha-256
  ```

  Sans elle, l'API refuse de démarrer (« base injoignable »).
- **Mode processeur**, sans carte graphique ou pour préparer un serveur : un réglage du
  `.env`, `COMPOSE_FILE=docker-compose.yml:compose.cpu.yml`. Il remplace l'image du
  service par l'image processeur de la même version, et retire la réservation de
  carte. Le modèle, la révision et la précision ne changent pas.
- **Une seule instance du service à la fois** sur le poste. Ne pas lever ce Compose
  pendant un encodage ou une équivalence du module 09 : même carte, même mémoire. Les
  gardes de `verify-poste` et de la sauvegarde voient le conteneur `tei-api-modele`.

### Sans conteneur (développement, poste)

```bash
(cd ../09_service_embedding && docker compose --profile embeddinggemma up -d)  # le service
cp .env.example .env    # API_MODELE_CLES, API_MODELE_DSN (sans mot de passe : ~/.pgpass)
uv run --env-file .env python -m api_modele                                     # l'API
```

L'API écoute alors sur `127.0.0.1:8010` et joint le service sur l'hôte du module 09.

## Configuration

Un seul lecteur, `api_modele.configuration`, et deux sources :

- **`config/reglages.toml`**, versionné : le défaut, les bornes et le **motif** de
  chaque réglage. Une table sans motif empêche le démarrage ;
- **l'environnement** (`.env`, hors dépôt ; modèle : `.env.example`) : les secrets,
  les adresses, et les surcharges d'exploitation.

| Réglage | Défaut | Bornes | Surcharge | Motif (résumé ; texte entier dans `reglages.toml`) |
|---|---|---|---|---|
| `nombre_series` | 10 | 1 à 50 | — (contrat) | la profondeur mesurée par le banc (k = 10) ; au-delà de 50, la liste ne sert plus une réponse |
| `passages_par_serie` | 1 | 1 à 5 | — (contrat) | le meilleur fragment fait le score ; 5 justifient sans recopier le corpus |
| `catalogue_seul` | vrai | — | — (contrat) | le classement mesuré est limité au catalogue |
| longueur de la question | 500 caractères | — | — (contrat) | 39 + 4 × 500 + 2 = 2 041 jetons au pire, sous les 2 048 du modèle : une question admise n'est jamais refusée pour sa longueur |
| corps de requête | 16 Kio | — | — | refusé avant d'être lu au-delà |
| débit | 60 / min / clé | 1 à 100 000 | `API_MODELE_DEBIT_PAR_MINUTE` | une requête par seconde en moyenne : une clé seule ne sature ni le service ni la base |
| délai du service | 10 s | > 0 | — | le client du 09 attend 300 s, trop pour une API ; dépassé : 503 |
| délai de la base | 5 s | > 0 | — | `statement_timeout` de chaque transaction ; dépassé : 503 |
| délai du chargement | 120 s | > 0 | — | 77 006 vecteurs en 17 s au démarrage |
| pool de connexions | 1 à 5, attente 5 s | — | — | sous la borne du rôle (10 connexions) |
| réseaux de `/metrics` | le bouclage | — | `API_MODELE_METRIQUES_RESEAUX` | Prometheus sur le poste ; 404 ailleurs |
| question au journal | faux | — | `API_MODELE_JOURNAL_QUESTION` | une question peut porter une donnée personnelle |
| documentation interactive | faux | — | `API_MODELE_DOCUMENTATION` | Swagger UI charge deux fichiers d'un CDN (version exacte, empreinte d'intégrité) |
| instance du service | `embeddinggemma` | — | `CLIENT_HOTE` (son hôte) | l'instance du 09 qui sert l'encodage en service |
| table des vecteurs | `bench.vecteurs_embeddinggemma` | `bench.vecteurs_<modèle>` | — | celle de la mesure 8 |
| besoin de mémoire | 1,9 Go | — | — | le pic mesuré ; ajouté au seuil du service dans `verify-poste` (6,9 Go) |
| écoute | `127.0.0.1:8010` | port 1 à 65 535 | `API_MODELE_HOTE`, `API_MODELE_PORT` | l'adresse locale ; le conteneur écoute sur `0.0.0.0` |

Les réglages marqués « contrat » font partie du contrat `/v1/` : les changer est une
rupture (voir « Ajouter une route »). Variables de l'environnement :

| Variable | Rôle |
|---|---|
| `API_MODELE_CLES` | `nom:clé,nom:clé` — une clé par consommateur, 32 caractères au moins ; vide ou fautive : aucun accès, l'API ne démarre pas |
| `API_MODELE_DSN` | la base, sous `modele_api`, **sans mot de passe** (refusé s'il en porte un) |
| `API_MODELE_VERSION` | `v1`, la seule servie |
| `API_MODELE_DSN_CONTENEUR`, `API_MODELE_PGPASS`, `HOTE_UID`, `HOTE_GID`, `HF_HUB_CACHE_HOTE`, `API_MODELE_ADRESSE_PUBLIEE`, `API_MODELE_PORT_PUBLIE`, `COMPOSE_FILE` | le Compose, documentées dans `.env.example` |

## Sécurité

- **Clé d'accès** en en-tête `X-API-Key`, une par application consommatrice. Elle est
  comparée en temps constant (`secrets.compare_digest`), **toutes** les clés du
  trousseau à chaque requête, sans arrêt à la clé trouvée. Une clé absente ou fausse
  rend le même 401 : rien ne distingue les deux cas. Le **nom** de la clé entre au
  journal, jamais sa valeur. `/v1/sante` et le contrat se lisent sans clé.
- **Débit** par clé : 60 requêtes par fenêtre glissante d'une minute. Au-delà, 429 et
  `Retry-After`. Le refus est compté par nom de clé (`/metrics`).
- **Version dans l'adresse** : `/v1/`. Une rupture ouvre `/v2/`.
- **Où vivent les secrets** : clés et DSN dans `.env` ; mot de passe de la base dans un
  pgpass (`~/.pgpass`, ou `.pgpass.conteneur` monté en secret), mode 600. Les trois
  fichiers sont ignorés par git. Aucun secret dans le dépôt, le journal, une réponse
  ou une capture ; les hooks du dépôt refusent un jeton.
- **Moindre droit** en base : `modele_api` hérite de `modele_ro` (migration 023). Ce
  rôle n'a de `SELECT` que sur les tables et colonnes que l'API lit, et ses sessions
  sont en lecture seule.
- **Entrée bornée** : modèle strict (champ inconnu refusé, aucune conversion de type),
  question de 500 caractères au plus, corps de 16 Kio au plus.
- **Erreurs sans fuite** : jamais de trace de pile dans une réponse ; elle va au
  journal, sous l'identifiant de la requête. Toute réponse porte
  `X-Identifiant-Requete` et `Cache-Control: no-store`.
- **Conteneurs** : images épinglées par empreinte, utilisateur non root. Le service
  d'embedding est sur un réseau interne, sans port publié ni accès sortant. L'API
  publie sur l'adresse locale par défaut.

## Les routes

| Route | Clé | Rôle | Statuts |
|---|---|---|---|
| `POST /v1/recherche` | oui | une question → les séries classées, leurs passages, le contexte | 200, 401, 413, 422, 429, 503 |
| `GET /v1/info` | oui | le modèle, la révision, l'encodage et le corpus servis ; les réglages et leurs bornes | 200, 401, 429 |
| `GET /v1/sante` | non | base, service, encodage en service : `ok`, ou `degrade` (503). Des états seulement, ni version ni modèle | 200, 503 |
| `GET /v1/openapi.json` | non | le contrat, égal à `contrat/openapi_v1.json` (versionné) | 200 |
| `GET /v1/docs` | non (les routes protégées y demandent la clé, saisie dans « Authorize ») | Swagger UI, **sur réglage** (`API_MODELE_DOCUMENTATION=vrai`) ; sinon 404 | 200 |
| `GET /metrics` | non | Prometheus, aux seules adresses admises ; 404 ailleurs | 200, 404 |

Exemple (question inventée ; identifiants, titres, scores et extraits **fictifs**) :

```http
POST /v1/recherche HTTP/1.1
Host: 127.0.0.1:8010
X-API-Key: <clé de l'application>
Content-Type: application/json

{"question": "Une série où une brigade de cuisiniers s'affronte dans des concours, avec beaucoup d'humour",
 "nombre_series": 2, "passages_par_serie": 2, "catalogue_seul": true, "identite": "session-42"}
```

```http
HTTP/1.1 200 OK
Content-Type: application/json
Cache-Control: no-store
X-Identifiant-Requete: 7c0e6a52-3d1b-4f6e-9a7d-2b8f1c4e5a90

{
  "series": [
    {"rang": 1, "entite": "serie:101", "serie_id": 101, "kitsu_id": null,
     "titre": "Titre de série A", "score": 0.6412093187,
     "passages": [
       {"fragment_id": 500001, "doc_key": "ms_synopsis:101", "type_document": "resume_serie",
        "score": 0.6412093187, "extrait": "Dans un restaurant de quartier, une jeune cuisinière se lance dans des concours…"},
       {"fragment_id": 500017, "doc_key": "ms_review:9001", "type_document": "critique",
        "score": 0.5873310442, "extrait": "Les duels culinaires sont rythmés et l'humour fait mouche…"}]},
    {"rang": 2, "entite": "serie:202", "serie_id": 202, "kitsu_id": null,
     "titre": "Titre de série B", "score": 0.6127745390,
     "passages": [
       {"fragment_id": 600042, "doc_key": "kitsu:3003", "type_document": "synopsis_kitsu",
        "score": 0.6127745390, "extrait": "A young chef joins a cooking school where every class is a battle…"},
       {"fragment_id": 600043, "doc_key": "kitsu:3003", "type_document": "synopsis_kitsu",
        "score": 0.5410028876, "extrait": "…while trying to keep the family restaurant afloat."}]}
  ],
  "contexte": {
    "reglages": {"nombre_series": 2, "passages_par_serie": 2, "catalogue_seul": true},
    "modele": "google/embeddinggemma-300m",
    "revision": "57c266a740f537b4dc058e1b0cda161fd15afa75",
    "precision": "float32",
    "corpus_id": "v2", "encodage_id": 3,
    "identifiant_requete": "7c0e6a52-3d1b-4f6e-9a7d-2b8f1c4e5a90",
    "duree_ms": 84.7, "version_api": "1.0.0"
  }
}
```

- `entite` est la clé du banc : `serie:<id>`, ou `kitsu:<id>` pour une œuvre Kitsu
  hors catalogue (`catalogue_seul: false`, `serie_id` nul).
- `score` est le cosinus du meilleur fragment, non arrondi ; le premier passage porte
  le score de sa série.
- Les passages sont classés par score décroissant, puis par `fragment_id`.

`GET /v1/info` (avec la clé) :

```json
{"version_api": "1.0.0", "modele": "google/embeddinggemma-300m",
 "revision": "57c266a740f537b4dc058e1b0cda161fd15afa75", "precision": "float32", "dimension": 768,
 "outil": "text-embeddings-inference", "outil_version": "1.9.4",
 "corpus_id": "v2", "encodage_id": 3, "nb_fragments": 77006,
 "reglages": {"nombre_series": {"defaut": 10, "min": 1, "max": 50},
              "passages_par_serie": {"defaut": 1, "min": 1, "max": 5},
              "catalogue_seul": {"defaut": true}},
 "question": {"longueur_max_caracteres": 500}, "debit_par_minute": 60}
```

`GET /v1/sante` (sans clé) :
`{"statut": "ok", "base": "ok", "service_embedding": "ok", "encodage": "ok"}`.

## Les erreurs

Une seule forme, `{"code", "message", "details", "identifiant_requete"}`, et un code
stable par cas :

| Statut | `code` | Quand | `details` |
|---|---|---|---|
| 401 | `cle_invalide` | clé absente ou fausse | `null` |
| 404 | `route_inconnue` | route inconnue (dont `/v2/…`) | `null` |
| 405 | `methode_non_permise` | méthode non permise (en-tête `Allow`) | `null` |
| 413 | `corps_trop_volumineux` | corps au-delà de 16 Kio | `{"maximum_octets": 16384}` |
| 422 | `entree_invalide` | corps illisible ; champ manquant, inconnu, de mauvais type, hors bornes | `{"champs": [{"champ": "nombre_series", "raison": "hors_bornes", "bornes": [1, 50]}]}` |
| 422 | `question_trop_longue` | question au-delà de 500 caractères (avant tout appel au service), ou refusée par le service pour sa longueur | `{"longueur": 731, "maximum": 500, "unite": "caracteres"}` (ou `"jetons"`) |
| 429 | `debit_depasse` | débit dépassé pour la clé ; en-tête `Retry-After` | `{"limite_par_minute": 60, "reessayer_dans_s": 12}` |
| 503 | `service_embedding_indisponible` | service injoignable, lent ou en refus | `{"cause": "injoignable"}`, `{"cause": "delai_depasse", "delai_s": 10.0}` ou `{"cause": "refus"}` |
| 503 | `service_embedding_incoherent` | le service ne sert plus l'encodage en service (`/info` ≠ registre) | `null` |
| 503 | `base_indisponible` | base injoignable, pool épuisé, ou délai dépassé | `{"cause": "injoignable"}` ou `{"cause": "delai_depasse", "delai_s": 5.0}` |
| 503 | `encodage_en_service_change` | une promotion ou un retour arrière a eu lieu depuis le démarrage : redémarrer l'API | `{"charge": 3, "en_service": 4}` |
| 500 | `erreur_interne` | imprévu ; la trace est au journal, sous l'identifiant | `null` |

`/v1/sante` rend 503 avec ses états (`degrade`), pas cette forme.

## Limites déclarées

- **Une instance.** Le débit est compté en mémoire du processus : plusieurs instances
  derrière un répartiteur auraient chacune leur compte.
- **Pas de haute disponibilité.** Un seul service d'embedding, une seule base, aucune
  reprise automatique (`restart: "no"`). Si le service tombe, la recherche rend 503 et
  `/v1/sante` le dit.
- **Les vecteurs sont chargés au démarrage.** Une promotion d'encodage en cours de
  route donne 503 `encodage_en_service_change` jusqu'au redémarrage. Ce qui est servi
  n'est jamais un classement fait sur des vecteurs qui ne sont plus en service.
- **Mémoire** : 1,5 à 1,8 Go au chargement, 0,5 Go ensuite. Avec le service, 6,9 Go
  disponibles sont exigés par `verify-poste`.
- **`/metrics` dans le Compose** : vu de l'hôte, le client n'est pas le bouclage du
  conteneur. Admettre le réseau de Prometheus par `API_MODELE_METRIQUES_RESEAUX`.
- **La documentation interactive** charge Swagger UI depuis un CDN, à version exacte
  et sous empreinte d'intégrité. Elle est désactivée par défaut. Le contrat, lui, est
  toujours servi.

## Ajouter une route sans casser `/v1/`

`/v1/` ne reçoit que des **ajouts compatibles** :

- une route nouvelle ;
- un champ de sortie nouveau ;
- un champ d'entrée optionnel dont le défaut garde le comportement ;
- un code d'erreur nouveau sous un statut déjà déclaré.

Les sorties ne ferment pas leurs champs. Un client qui ignore les champs inconnus ne
voit donc pas la différence (`tests/test_versionnage.py`).

Pour ajouter une route :

1. **La déclarer** sous le préfixe (`f"{prefixe}/…"`, `application.py`) :
   - ses formes dans `modeles.py`, l'entrée stricte (`extra="forbid"`) ;
   - **tous** ses statuts dans `responses`, avec le modèle `Erreur` ;
   - la clé (`CleExigee`) si elle n'est pas publique.
2. **Un scénario par statut déclaré** dans `tests/test_contrat_codes.py`
   (`SCENARIOS`). Sans scénario, le test qui compare les scénarios au contrat échoue.
   Un statut produit sans être déclaré fait échouer la suite (fixture
   `aucun_statut_hors_contrat`).
3. **Régénérer le contrat** : `uv run python -m api_modele.contrat`, puis commiter
   `contrat/openapi_v1.json` avec la route. Le test d'égalité à l'octet refuse un
   contrat périmé.
4. Monter la version **mineure** du `pyproject.toml` (1.1.0) : la majeure reste celle
   de l'adresse.

**Toute rupture ouvre `/v2/`** : retirer ou renommer un champ, restreindre un type ou
une borne, changer un défaut, changer la définition du score ou du classement. Il faut
alors :

- ajouter `v2` aux versions servies (`configuration.VERSIONS_SERVIES`) ;
- produire son contrat, `contrat/openapi_v2.json`, à côté de celui de v1 ;
- garder `/v1/` tant que l'application n'a pas migré.

Une promotion d'encodage n'est pas une rupture : le contrat ne change pas, les
résultats si, et `contexte` le dit.

## Vérifier

```bash
make verify MODULE=11        # depuis la racine du dépôt
```

- Ruff ; puis les tests (base PostgreSQL jetable par le harnais partagé, doublure HTTP
  du service ; aucun ne demande le modèle réel) ; la couverture ;
- **l'essai d'intégration du Compose** (`compose.integration.yml`), sur le poste comme
  sur GitHub. Il lève le Compose du module : base jetable migrée, rôle par sa
  procédure, jeu d'essai, doublure du service, API réelle. Puis il joue `/v1/sante`,
  une recherche, `/v1/info` et un 401, et détruit le Compose ;
- les exigences du module neuf, bloquantes : Pyright strict sur `src/`, Bandit,
  pip-audit.

`make verify-poste` ajoute ce qui ne tourne que sur le poste, sous des rôles en lecture
seule :

- l'égalité avec le banc sur les 69 questions, avec le service et la base réels ;
- le contrôle des pseudonymes dans les passages rendus ;
- l'état de la base.

`make verify-mutations` rejoue les mutations du module (`verification/mutations/API-*`).
