# 11 — API du modèle

Une API REST qui expose le modèle d'embedding en service et, depuis la version
**1.1.0**, un modèle de langage choisi par profil ; depuis la **1.2.0**, chaque série
porte ses métadonnées, et depuis la **1.3.0** ses auteurs ; la **1.3.1** rend l'attente
d'un 429 du fournisseur (`Retry-After`) ; la **1.4.0** ajoute le **mode enrichi** de la
réponse (un complément « non vérifié », plus bas) ; la **1.5.0**, le **test des clés** des
fournisseurs pour l'administration de l'application (`POST /v1/verification`, non
facturé, borné). Deux routes métier, sous `/v1/` :

- `POST /v1/recherche` : une question entre, une liste de séries classées sort, avec
  les passages qui les justifient ;
- `POST /v1/reponse` : une question (et, au besoin, l'historique d'une courte
  conversation) entre ; un modèle de langage rédige en français une réponse appuyée sur
  les seuls passages de la recherche, ou refuse. La réponse porte ses sources, le
  profil et les réglages appliqués, les jetons, le coût et les latences.

## Ce que fait l'API, et ce qu'elle ne fait pas

**La recherche** :

- encode une question par le service d'embedding (EmbeddingGemma, servi par Text
  Embeddings Inference), avec le préfixe de question du modèle et sans troncature ;
- classe les séries du catalogue. Le score d'une série est celui de son meilleur
  fragment, sur les vecteurs de **l'encodage en service** et de lui seul ;
- rend, pour chaque série, ses meilleurs passages : le texte même des fragments du
  corpus ;
- dit dans chaque réponse ce qui a servi : réglages, modèle, révision, corpus,
  encodage, identifiant de requête, durée.

Le classement est **exactement celui que le banc de mesure a mesuré** (module 10,
mesure 8, corpus en service) : le code du banc est réutilisé, pas réécrit. Une étape
de `make verify-poste` le prouve. Elle rejoue par l'API les 69 questions du jeu
d'évaluation gelé ; pour chacune, les dix premières séries doivent égaler celles du
run de référence (69 / 69 le 2026-10-20, scores identiques au bit près).

**La réponse rédigée** :

- **mode strict** : la réponse n'affirme que ce que disent les passages rendus par la
  recherche, et ne cite que des séries de cette recherche et leurs passages — chacun lui
  est montré avec son identifiant, `[F:<fragment_id>]`, qu'il recopie dans
  `fragment_ids`. Ce que les passages ne disent pas, la réponse le dit ;
- **les métadonnées viennent du catalogue** : nombre de tomes, statut, public et
  disponibilité sont sur la ligne de titre de chaque passage, et priment sur ce qu'une
  critique dit, qui peut être ancienne (« Les métadonnées d'une série ») ;
- **chaque série citée porte son titre** : l'invite demande le titre devant
  `[S:<serie_id>]` ; quand le modèle l'omet (ni juste avant ni juste après la marque,
  casse, espaces, emphase, guillemets et ponctuation ignorés), la route insère le titre
  du catalogue devant elle, après les contrôles de sortie — le modèle propose, une
  règle dispose ;
- **le refus est une réponse normale** (200, `refus: true`) : aucun passage ne répond
  (`aucun_passage`), l'œuvre n'est pas au catalogue (`hors_catalogue`), ou le modèle ne
  peut pas trancher (`inconnu`). Quand la recherche ne rend aucun passage, la route
  refuse, en mode strict, **sans appeler le modèle** : c'est gratuit et déterministe
  (en mode enrichi, l'appel a lieu pour le complément, et la partie stricte doit
  refuser) ;
- **conversation courte** : l'historique (au plus 5 questions-réponses, 10 tours) vient
  avec chaque requête ; un premier appel au modèle en tire une question autonome, sans
  critère absent de la conversation ni critère d'un tour précédent auquel le dernier tour
  ne renvoie pas, qui part seule dans la recherche ;
- **sorties contrôlées** : la sortie du modèle est contrainte par un schéma JSON, puis
  relue par l'API — séries et fragments cités, cohérence du refus, **aucun nom de
  membre**, disponibilité et public que la ligne de titre ne contredit pas. Une sortie
  fautive n'est ni rendue ni journalisée.

**Elle ne fait pas** :

- pas de **réponse mêlée** : en mode enrichi (1.4.0), ce que le modèle sait s'ajoute
  à part, dans un complément étiqueté « non vérifié », jamais dans `reponse` ;
- pas de **mémoire côté API** : elle ne garde ni conversation, ni historique, ni
  question. L'application tient la conversation et l'efface ;
- pas de **bascule automatique** entre profils : changer de modèle est un geste de
  configuration, fait par une personne (« Changer de profil ») ;
- pas d'interface (étape 4) ; pas d'écriture en base : rôle et sessions en lecture
  seule ;
- pas de choix du corpus, de l'encodage, de la révision, des préfixes, de l'invite ni
  de la graine par le client. Un champ inconnu est refusé : le client ne doit pas croire
  qu'il règle ce qu'il ne règle pas ;
- pas de comptes, de rôles ni de jetons à expiration : une clé par application
  consommatrice. Les utilisateurs vivront dans l'application.

Le module 02 (API de données) est une autre API, inchangée : les données d'un côté,
l'inférence de l'autre.

## Architecture

```text
 application, démonstration                      (client HTTP, une clé chacune)
            │  HTTP local, en-tête X-API-Key
            ▼
 ┌──────────────────────────────┐  réseau interne  ┌───────────────────────────┐
 │ API du modèle (module 11)    │ ───────────────► │ service d'embedding       │
 │ FastAPI, Uvicorn, port 8010  │   /info, /embed  │ Text Embeddings Inference │
 │ /v1/recherche, /v1/reponse   │   /tokenize      │ 1.9.4, EmbeddingGemma     │
 │ classement du banc (10)      │                  │ (configuration du 09)     │
 │ rattachement (05)            │                  └───────────────────────────┘
 │ pseudonymes (05)             │
 └───────┬───────────────┬──────┘
         │               │ HTTPS, /v1/chat/completions, profil actif ou demandé
         │               ▼
         │   ┌──────────────────────────────────────────────────────────────┐
         │   │ fournisseur du modèle de langage :                           │
         │   │ Mistral (Union européenne) — profils mistral-large-*         │
         │   │ OpenAI (hors Union européenne par défaut) — profils gpt-6-*  │
         │   └──────────────────────────────────────────────────────────────┘
         │ lecture seule, sous modele_api (membre de modele_ro, migrations 023, 024)
         ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ PostgreSQL apimanga — bench : corpus, fragments, vecteurs EmbeddingGemma,    │
 │ registre des encodages, encodage en service (vue) ; manga : rattachement et  │
 │ titre des séries, vue des pseudonymes (les noms distincts) — rien d'autre    │
 └──────────────────────────────────────────────────────────────────────────────┘
```

Ce que l'API réutilise, sans le recopier :

| Module | Quoi | Comment |
|---|---|---|
| 09 | le client du service d'embedding (préfixes, refus de troncature, confrontation de `/info` au registre des encodages) et la configuration de l'instance | dépendance locale `uv` |
| 10 | le classement : rattachement des fragments, score d'une série, départage, limitation au catalogue | paquet chargé par son chemin (`api_modele.banc`) |
| 05 | le rattachement (`evaluation/atteignabilite.py`) ; la méthode des pseudonymes du corpus v2 (`corpus/pseudonymes.py` : mot entier, empreinte, listes versionnées) | chargés par leur chemin (le rattachement par le module 10) |

Les versions dont le calcul dépend (numpy, pgvector, psycopg, typer) sont celles du
verrou du module 10 ; `tests/test_verrous.py` compare les deux verrous. Le client HTTP
des fournisseurs est `httpx`, en requête directe : aucun paquet client de fournisseur.

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
   2026-10-20 ; pic de 1,5 à 1,8 Go de mémoire, puis 0,5 Go résidents) ;
7. la liste des pseudonymes ne se lit pas (vue `manga.v_pseudonymes` illisible ou vide),
   ou celle des homonymes admis (`database/donnees/corpus_homonymes_admis.csv`) ;
8. le contrôle d'exploitation, s'il est demandé (`API_MODELE_VERIFIER_MODELES=1`), ne
   trouve pas le modèle du profil actif chez son fournisseur.

Une configuration fautive (clé d'accès absente, DSN avec mot de passe, réglage hors
bornes, profil actif inconnu ou **sans clé**) arrête l'API avant tout (code 2), tous les
écarts nommés. Au démarrage, **aucun appel** n'est fait à un fournisseur du modèle de
langage (sauf le contrôle d'exploitation, sur demande).

Prérequis communs :

- la base `apimanga` migrée jusqu'à `024` (la vue des pseudonymes) ;
- le rôle de connexion `modele_api`, créé par
  `MODELE_API_PASSWORD=… sh database/outils/creer_role_modele.sh` ;
- son mot de passe dans un fichier pgpass (mode 600), jamais dans le DSN ;
- la clé du fournisseur du profil actif dans le `.env` (`MISTRAL_API_KEY` pour le profil
  par défaut) : sans elle, l'API ne démarre pas ;
- les poids d'EmbeddingGemma à la révision épinglée, dans le cache Hugging Face de
  l'hôte. Le modèle est à accès contrôlé : la première fois, ils se téléchargent par le
  Compose du module 09, avec un jeton (`09_service_embedding/.env`).

### Avec le Compose (`docker-compose.yml`)

```bash
cd 11_api_modele
cp .env.example .env     # API_MODELE_CLES, la clé du fournisseur du profil actif, au besoin le reste
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
    (`API_MODELE_ADRESSE_PUBLIEE`, `API_MODELE_PORT_PUBLIE`) ;
  - le Compose lui transmet les variables des fournisseurs, lues dans le `.env` (les
    clés, le profil actif, le plafond, le contrôle d'exploitation) ; aucune valeur n'est
    écrite dans le Compose. Elle joint le fournisseur par le réseau `sortie`.
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
cp .env.example .env    # API_MODELE_CLES, API_MODELE_DSN (sans mot de passe : ~/.pgpass), la clé du profil actif
uv run --env-file .env python -m api_modele                                     # l'API
```

L'API écoute alors sur `127.0.0.1:8010` et joint le service sur l'hôte du module 09.


## Les profils du modèle de langage

Un **profil** dit quel modèle rédige, chez quel fournisseur, à quelle adresse, avec
quelle variable de clé, quels réglages fixés et quels réglages absents. Les profils
sont déclarés dans `config/reglages.toml` (`[modele_langage.profils.<nom>]`), avec leur
motif ; **un seul réglage désigne le profil actif** (`profil_actif`, surchargeable par
`API_MODELE_PROFIL_ACTIF`). Une requête peut demander un autre profil déclaré (champ
`profil`).

| Profil | Fournisseur | Modèle | Statut | Hébergement | Prix (entrée / sortie, $ par million de jetons, relevés le 2026-10-26) | Réglages fixés | Réglages absents |
|---|---|---|---|---|---|---|---|
| `mistral-large-2512` (**actif**) | Mistral | `mistral-large-2512` (Large 3, daté) | stable | Union européenne | 0,50 / 1,50 | `top_p = 1` | aucun |
| `mistral-large-4` | Mistral | `mistral-large-4` | **aperçu** (modèle mouvant) | Union européenne | 1,36 / 4,18 | `top_p = 1`, `reasoning_effort = low` (sans effort fixé, le raisonnement épuisait la borne de sortie à l'appel d'essai ; `none` refusé par Mistral) ; `longueur_max` 2 000 (1 101 jetons de raisonnement mesurés le 31 octobre) ; sortie en morceaux typés (raisonnement, puis texte) | aucun |
| `gpt-6-luna` | OpenAI | `gpt-6-luna` | stable | hors Union européenne par défaut | 0,10 / 0,50 | `top_p = 1`, `reasoning_effort = none` | aucun (`temperature` 0,7 acceptée à l'appel d'essai) |
| `gpt-6-sol` | OpenAI | `gpt-6-sol` | stable | hors Union européenne par défaut | 2,00 / 10,00 | `top_p = 1`, `reasoning_effort = none` ; délai 45 s | aucun (`temperature` 0,7 acceptée à l'appel d'essai) |

- **Ordre de repli, à la main** : Large 3 (actif) → Large 4 si l'on veut le modèle de
  pointe ou si Large 3 est muet → Luna ou Sol si Mistral entier est injoignable.
- **Réglage absent** : un réglage qu'un profil ne prend pas est déclaré dans
  `reglages_absents`. Le demander rend **422 `reglage_non_expose`** (le réglage, le
  profil), jamais un silence ; non demandé, il n'est pas envoyé.
- **Profil indisponible** : la variable de sa clé est absente ou vide. Ce n'est pas
  fatal : `/v1/info` le dit (`disponible: false`), la métrique
  `api_modele_profil_disponible` aussi, et une requête qui le demande rend 422
  `profil_indisponible`.
- **Le profil actif sans clé empêche le démarrage** (configuration invalide, code 2,
  le message nomme la variable) : une API qui démarrerait sans pouvoir rédiger
  rendrait des 422 à toutes les requêtes sans le dire au démarrage.
- La disponibilité est **la présence de la clé** : au démarrage, aucun appel réseau à
  un fournisseur. Un identifiant de modèle retiré par le fournisseur se voit au premier
  appel (`fournisseur_refus`), ou par le contrôle d'exploitation lancé à la main
  (`API_MODELE_VERIFIER_MODELES=1` : `/v1/models` de chaque fournisseur, un identifiant
  absent rend son profil indisponible, le profil actif absent empêche le démarrage).
- **Un profil ne sert en démonstration qu'après sa mesure** (étape 3, spec de mesure) :
  les quatre profils sont mesurés et tiennent le critère (« Résultats de la mesure »).

## Changer de profil

Le repli est un **geste humain** : la personne qui exploite l'API (à ce stade, Max),
quand le profil actif ne répond plus ou qu'une mesure le demande. Il n'y a pas de
bascule automatique.

1. **Désigner le profil** : `API_MODELE_PROFIL_ACTIF=<profil>` dans le `.env` (ou
   l'environnement). Le profil doit être déclaré, et sa clé présente.
2. **Redémarrer l'API** : `docker compose up -d api` (Compose), ou relancer
   `uv run --env-file .env python -m api_modele`.
3. **Contrôler** : `GET /v1/info` (avec la clé) affiche le profil actif
   (`modele_langage.profil_actif`) ; la métrique `api_modele_profil_actif{profil=…}` vaut
   1 sur lui, 0 ailleurs.

**Durée mesurée** (2026-10-27, sur le poste, avec les doublures : l'essai du Compose,
son corpus d'essai et une doublure du fournisseur) : **2,1 à 2,2 s** du redémarrage de
l'API au contrôle (`/v1/info` et la métrique), procédure répétée trois fois dans chaque
sens. **En service** (2026-11-02, Compose, corpus v2, encodage en service, clés réelles,
`mistral-large-2512` vers `gpt-6-luna` et retour) : **15,9 s** à l'aller, **14,7 s** au
retour, de `docker compose up -d api` au contrôle par `/v1/info` ; une mesure par sens,
aucun appel au fournisseur. Le conteneur étant recréé, son journal repart de zéro :
relever `docker compose logs api` avant de changer de profil si on veut le garder.

**Revenir** : la même procédure, avec l'ancien profil (ou la variable retirée : le
profil actif de `reglages.toml`). Une requête peut aussi, sans changer le profil actif,
demander un profil déclaré par le champ `profil`.

## Configuration

Un seul lecteur, `api_modele.configuration`, et deux sources :

- **`config/reglages.toml`**, versionné : le défaut, les bornes et le **motif** de
  chaque réglage. Une table sans motif empêche le démarrage ;
- **l'environnement** (`.env`, hors dépôt ; modèle : `.env.example`) : les secrets,
  les adresses, et les surcharges d'exploitation.

### La recherche et l'API

| Réglage | Défaut | Bornes | Surcharge | Motif (résumé ; texte entier dans `reglages.toml`) |
|---|---|---|---|---|
| `nombre_series` | 10 | 1 à 50 | — (contrat) | la profondeur mesurée par le banc (k = 10) ; au-delà de 50, la liste ne sert plus une réponse |
| `passages_par_serie` | 1 | 1 à 5 | — (contrat) | le meilleur fragment fait le score ; 5 justifient sans recopier le corpus |
| `catalogue_seul` | vrai | — | — (contrat) | le classement mesuré est limité au catalogue |
| longueur de la question | 500 caractères | — | — (contrat) | 39 + 4 × 500 + 2 = 2 041 jetons au pire, sous les 2 048 du modèle : une question admise n'est jamais refusée pour sa longueur |
| corps de requête | 16 Kio | — | — | refusé avant d'être lu au-delà |
| débit | 60 / min / clé | 1 à 100 000 | `API_MODELE_DEBIT_PAR_MINUTE` | une requête par seconde en moyenne : une clé seule ne sature ni le service ni la base ; **commun aux deux routes** |
| délai du service | 10 s | > 0 | — | le client du 09 attend 300 s, trop pour une API ; dépassé : 503 |
| délai de la base | 5 s | > 0 | — | `statement_timeout` de chaque transaction ; dépassé : 503 |
| délai du chargement | 120 s | > 0 | — | 77 006 vecteurs en 17 s au démarrage |
| pool de connexions | 1 à 5, attente 5 s | — | — | sous la borne du rôle (10 connexions) |
| réseaux de `/metrics` | le bouclage | — | `API_MODELE_METRIQUES_RESEAUX` | Prometheus sur le poste ; 404 ailleurs |
| question au journal | faux | — | `API_MODELE_JOURNAL_QUESTION` | une question peut porter une donnée personnelle |
| documentation interactive | faux | — | `API_MODELE_DOCUMENTATION` | on l'allume pour la démontrer ou pour essayer les routes ; Swagger UI est servi par l'API, sans ressource réseau |
| instance du service | `embeddinggemma` | — | `CLIENT_HOTE` (son hôte) | l'instance du 09 qui sert l'encodage en service |
| table des vecteurs | `bench.vecteurs_embeddinggemma` | `bench.vecteurs_<modèle>` | — | celle de la mesure 8 |
| besoin de mémoire | 1,9 Go | — | — | le pic mesuré ; ajouté au seuil du service dans `verify-poste` (6,9 Go) |
| écoute | `127.0.0.1:8010` | port 1 à 65 535 | `API_MODELE_HOTE`, `API_MODELE_PORT` | l'adresse locale ; le conteneur écoute sur `0.0.0.0` |

### Le modèle de langage (`[modele_langage]`)

| Réglage | Défaut | Bornes | Surcharge | Motif (résumé) |
|---|---|---|---|---|
| `profil_actif` | `mistral-large-2512` | un profil déclaré, disponible | `API_MODELE_PROFIL_ACTIF` | un seul réglage désigne le modèle qui rédige ; en changer est un geste de configuration |
| `temperature` | 0 | 0 à 1 | — (champ de la requête) | 0 rend la même réponse deux fois, ce qu'un conseil sourcé veut ; 0,7 varie ; au-delà de 1 la réponse se dégrade |
| `longueur_max` | 400 jetons, ou celle du profil (`longueur_max` du profil : 2 000 pour `mistral-large-4`, 400 pour les autres) | 50 à 4 000 | — (champ de la requête) | lisibilité au comptoir et coût ; base de l'estimation ; 1 000 était la borne d'usage, 4 000 admet les profils qui raisonnent, mesurés avant tout usage ; une longueur demandée l'emporte sur celle du profil |
| `mode` | `strict` | `strict`, `enrichi` | — | `strict` : la réponse tirée des seuls passages ; `enrichi` (1.4.0) : la même, plus un complément « non vérifié » (plus bas) ; une valeur hors de la liste empêche le démarrage |
| `historique` | vide | 10 tours, 4 000 caractères (2 000 par tour) | — | cinq questions-réponses, borne franche ; ni gardé ni journalisé |
| `reformuler` | vrai | — | — (champ de la requête) | les suites de comptoir ne retrouvent rien seules ; désactivable pour la démonstration |
| recherche de la route | 5 séries × 2 passages, catalogue seul | bornes de la recherche | — (champs de la requête) | de quoi choisir sans recopier le corpus ; ceux de `/v1/recherche` (10 × 1) ne changent pas |
| `caracteres_par_jeton` | 3,8 | > 0 | — | estimation des jetons, sans `usage` du fournisseur ; recalée sur les quatre appels d'essai (3,86 à 4,02 selon le profil) : un majorant pour chacun |
| `plafond_run_usd` | 0,50 $ | > 0 | `COUT_PLAFOND_RUN` | plafond d'un run de mesure (le banc s'arrête avant de le dépasser) |
| délais | connexion 5 s, réponse 30 s (45 s pour Sol) | > 0 | — | au-delà : 503 `fournisseur_injoignable` / `fournisseur_delai` ; l'appel entier est borné |
| `appeler_fournisseurs` (démarrage) | faux | — | `API_MODELE_VERIFIER_MODELES` | contrôle d'exploitation, jamais par défaut, jamais dans `make verify` ni un test |

**Non exposés**, fixés par profil ou par l'API, avec leur motif : `top_p` (fixé à 1 :
Mistral conseille de régler l'un ou l'autre, et deux curseurs qui se compensent ne
démontrent rien) ; l'effort de raisonnement (par profil : `none` chez OpenAI, pas de
jetons de raisonnement ; `low` pour `mistral-large-4`, qui refuse `none`) ; les pénalités de répétition (absentes chez Mistral) ; le
format de sortie (le schéma JSON de la route) ; l'invite système (une seule, pour tous
les profils) ; la graine aléatoire (`temperature = 0` donne déjà le rejeu, et la graine
n'est pas garantie chez OpenAI). La reformulation tourne à température 0 et 150 jetons,
fixés.

### Variables de l'environnement (les noms ; jamais de valeur dans le dépôt)

| Variable | Rôle |
|---|---|
| `API_MODELE_CLES` | `nom:clé,nom:clé` — une clé par consommateur, 32 caractères au moins ; vide ou fautive : aucun accès, l'API ne démarre pas |
| `API_MODELE_DSN` | la base, sous `modele_api`, **sans mot de passe** (refusé s'il en porte un) |
| `MISTRAL_API_KEY`, `OPENAI_API_KEY` | les clés des fournisseurs, nommées par la `variable_cle` des profils ; celle du profil actif est exigée |
| `API_MODELE_PROFIL_ACTIF` | le profil qui rédige, s'il n'est pas celui du fichier |
| `COUT_PLAFOND_RUN` | le plafond de coût d'un run de mesure, en dollars |
| `API_MODELE_VERIFIER_MODELES` | `1` : le contrôle d'exploitation au démarrage (`/v1/models`) ; `0` par défaut |
| `API_MODELE_VERSION` | `v1`, la seule servie |
| `API_MODELE_DSN_CONTENEUR`, `API_MODELE_PGPASS`, `HOTE_UID`, `HOTE_GID`, `HF_HUB_CACHE_HOTE`, `API_MODELE_ADRESSE_PUBLIEE`, `API_MODELE_PORT_PUBLIE`, `COMPOSE_FILE` | le Compose, documentées dans `.env.example` |

## Les routes

| Route | Clé | Rôle | Statuts |
|---|---|---|---|
| `POST /v1/recherche` | oui | une question → les séries classées, leurs passages, le contexte | 200, 401, 413, 422, 429, 503 |
| `POST /v1/reponse` | oui | une question, l'historique → une réponse rédigée des seuls passages, ou un refus ; ses sources, son contexte | 200, 401, 413, 422, 429, 503 |
| `GET /v1/info` | oui | le modèle, la révision, l'encodage et le corpus servis ; les réglages et leurs bornes ; le modèle de langage, ses profils et leur disponibilité | 200, 401, 429 |
| `POST /v1/verification` | oui | **1.5.0** : par profil, la clé posée ou non (le nom de la variable, jamais sa valeur) et la réponse de la liste des modèles du fournisseur (non facturée) ; un rapport, sans effet sur les profils en service ; **un appel par minute et par clé** | 200, 401, 429 |
| `GET /v1/sante` | non | base, service, encodage en service : `ok`, ou `degrade` (503). Des états seulement, ni version ni modèle | 200, 503 |
| `GET /v1/openapi.json` | non | le contrat, égal à `contrat/openapi_v1.json` (versionné) | 200 |
| `GET /v1/docs` | non (les routes protégées y demandent la clé, saisie dans « Authorize ») | Swagger UI, **sur réglage** (`API_MODELE_DOCUMENTATION=vrai`) ; sinon 404 | 200 |
| `GET /metrics` | non | Prometheus, aux seules adresses admises ; 404 ailleurs | 200, 404 |

### `POST /v1/recherche`

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
     "metadonnees": {"disponibilite": "edite_en_france", "nb_volumes": 12, "nb_volumes_source": "champ", "statut": "termine", "demographie": "Shonen", "annee_fr": 2016, "auteurs": "Autrice A"},
     "passages": [
       {"fragment_id": 500001, "doc_key": "ms_synopsis:101", "type_document": "resume_serie",
        "score": 0.6412093187, "extrait": "Dans un restaurant de quartier, une jeune cuisinière se lance dans des concours…"},
       {"fragment_id": 500017, "doc_key": "ms_review:9001", "type_document": "critique",
        "score": 0.5873310442, "extrait": "Les duels culinaires sont rythmés et l'humour fait mouche…"}]},
    {"rang": 2, "entite": "serie:202", "serie_id": 202, "kitsu_id": null,
     "titre": "Titre de série B", "score": 0.6127745390,
     "metadonnees": {"disponibilite": "edite_en_france", "nb_volumes": 5, "nb_volumes_source": "repli_volumes_distincts", "statut": "en_cours", "demographie": "Seinen", "annee_fr": 2021, "auteurs": "Auteur B, Autrice C"},
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
    "duree_ms": 84.7, "version_api": "1.4.0"
  }
}
```

- `entite` est la clé du banc : `serie:<id>`, ou `kitsu:<id>` pour une œuvre Kitsu
  hors catalogue (`catalogue_seul: false`, `serie_id` nul).
- `score` est le cosinus du meilleur fragment, non arrondi ; le premier passage porte
  le score de sa série.
- Les passages sont classés par score décroissant, puis par `fragment_id`.
- `metadonnees` : ce que le catalogue dit de la série (« Les métadonnées d'une série ») ;
  six `null` pour une œuvre hors catalogue.


### `POST /v1/reponse`

Exemple (question inventée, hors du jeu d'évaluation ; séries du catalogue ; extraits
abrégés ; `kitsu_id`, révision, scores, latences et jetons **illustratifs**) :

```http
POST /v1/reponse HTTP/1.1
Host: 127.0.0.1:8010
X-API-Key: <clé de l'application>
Content-Type: application/json

{"question": "Et le premier, il est long ? Ça irait pour un ado de 13 ans ?",
 "historique": [
   {"role": "libraire", "contenu": "Vous auriez des mangas sur la cuisine ou la pâtisserie ?"},
   {"role": "assistant", "contenu": "Oui : Yakitate!! Japan, l'histoire d'un jeune boulanger, et Food wars !, un shōnen de cuisine en compétition."}
 ],
 "nombre_series": 2, "identite": "comptoir-1"}
```

```http
HTTP/1.1 200 OK
Content-Type: application/json
Cache-Control: no-store
X-Identifiant-Requete: 6f1c…

{
  "refus": false,
  "motif_refus": null,
  "reponse": "D'après les critiques, Yakitate!! Japan [S:1708] est une série de cuisine humoristique centrée sur la boulangerie, avec des concours et un ton léger qui convient à un jeune lecteur ; elle est terminée, en 26 tomes, et éditée en France. Food wars ! [S:15123] est plus orienté compétition et comporte des scènes de dégustation très suggestives, signalées par les critiques : à feuilleter avant de le proposer à un lecteur de 13 ans.",
  "series_citees": [
    {"serie_id": 1708, "titre": "Yakitate!! Japan", "rang": 1, "fragment_ids": [473803, 474337]},
    {"serie_id": 15123, "titre": "Food wars !", "rang": 2, "fragment_ids": [460377]}
  ],
  "series": [
    {"rang": 1, "entite": "serie:1708", "serie_id": 1708, "kitsu_id": 1234, "titre": "Yakitate!! Japan", "score": 0.6812,
     "metadonnees": {"disponibilite": "edite_en_france", "nb_volumes": 26, "nb_volumes_source": "champ", "statut": "termine", "demographie": "Shonen", "annee_fr": 2005, "auteurs": "Takashi HASHIGUCHI"},
     "passages": [
       {"fragment_id": 473803, "doc_key": "ms_review:…", "type_document": "critique", "score": 0.6812, "extrait": "…"},
       {"fragment_id": 474337, "doc_key": "ms_review:…", "type_document": "critique", "score": 0.6520, "extrait": "…"}]},
    {"rang": 2, "entite": "serie:15123", "serie_id": 15123, "kitsu_id": 5678, "titre": "Food wars !", "score": 0.6407,
     "metadonnees": {"disponibilite": "edite_en_france", "nb_volumes": 36, "nb_volumes_source": "champ", "statut": "termine", "demographie": "Shonen", "annee_fr": 2014, "auteurs": "Yûto TSUKUDA, Shun SAEKI"},
     "passages": [
       {"fragment_id": 460377, "doc_key": "ms_review:…", "type_document": "critique", "score": 0.6407, "extrait": "…"},
       {"fragment_id": 483381, "doc_key": "ms_review:…", "type_document": "critique", "score": 0.6101, "extrait": "…"}]}
  ],
  "contexte": {
    "reglages": {"nombre_series": 2, "passages_par_serie": 2, "catalogue_seul": true},
    "modele": "google/embeddinggemma-300m", "revision": "…", "precision": "float32",
    "corpus_id": "v2", "encodage_id": 3, "identifiant_requete": "6f1c…",
    "duree_ms": 3104.5, "version_api": "1.4.0",
    "profil": "mistral-large-2512", "fournisseur": "mistral",
    "modele_langage": "mistral-large-2512", "statut_profil": "stable",
    "date_appel": "2026-11-02T10:14:07+00:00",
    "reglages_appliques": {"temperature": 0.0, "longueur_max": 400, "mode": "strict",
                           "top_p": 1.0, "reasoning_effort": null},
    "reglages_absents": [],
    "question_reformulee": "Yakitate!! Japan est-il une série longue, et convient-il à un adolescent de 13 ans ?",
    "reformulation_ignoree": null,
    "appels": [
      {"role": "reformulation", "jetons_entree": 312, "jetons_sortie": 31, "jetons_raisonnement": 0,
       "estimes": false, "latence_ms": 640.2},
      {"role": "reponse", "jetons_entree": 1874, "jetons_sortie": 142, "jetons_raisonnement": 0,
       "estimes": false, "latence_ms": 2310.7}
    ],
    "cout_estime_usd": 0.001353,
    "latences_ms": {"recherche": 141.0, "reformulation": 640.2, "modele": 2310.7, "totale": 3104.5}
  }
}
```

**L'entrée** (stricte : un champ inconnu est refusé) :

| Champ | Défaut | Bornes | Rôle |
|---|---|---|---|
| `question` | — | 1 à 500 caractères, non vide | la question du libraire, dernier tour |
| `historique` | `[]` | 10 tours, 4 000 caractères, 2 000 par tour ; tours alternés, le premier du libraire, le dernier de l'assistant | les tours précédents, dans l'ordre |
| `reformuler` | vrai | — | sans effet sans historique |
| `profil` | le profil actif | un profil déclaré, disponible | le modèle qui rédige |
| `temperature` | 0 | 0 à 1 | variété de la réponse ; refusé si le profil ne l'expose pas |
| `longueur_max` | 400 | 50 à 4 000 | jetons de sortie au plus |
| `mode` | `strict` | `strict`, `enrichi` | `enrichi` : le complément « non vérifié » (plus bas) |
| `nombre_series`, `passages_par_serie`, `catalogue_seul` | 5, 2, vrai | bornes de la recherche | transmis à la recherche |
| `identite` | absent | 1 à 64, `[A-Za-z0-9._@:-]` | recopiée dans le journal |

**Lire `contexte`, champ par champ** :

- `reglages`, `modele`, `revision`, `precision`, `corpus_id`, `encodage_id`,
  `identifiant_requete`, `version_api` : ceux de la recherche (`modele` reste le modèle
  d'embedding) ; `duree_ms` : la requête entière ;
- `profil`, `fournisseur`, `modele_langage`, `statut_profil` : qui a rédigé
  (`apercu` : un modèle mouvant, dont les mesures portent la mention) ;
- `date_appel` : le dernier appel au modèle (UTC), `null` sans appel ;
- `reglages_appliques` : ce qui a été envoyé (`null` : non envoyé, réglage absent du
  profil) ; `reglages_absents` : ce que le profil ne prend pas ;
- `question_reformulee` : la question autonome partie dans la recherche ;
  `reformulation_ignoree = "trop_longue"` : plus de 500 caractères, la recherche s'est
  faite sur la question d'origine ;
- `appels` : un par appel qui a eu lieu (`reformulation`, `reponse`), avec ses jetons
  (`estimes: true` quand le fournisseur ne les a pas rendus), sa latence et son `statut`
  (`ok` ; `refus_debit` : refusé pour débit, aucun jeton, puis rejoué une fois) ;
- `cout_estime_usd` : la somme des appels (formule plus bas), `null` si un prix n'est
  pas déclaré, 0 sans appel ;
- `latences_ms` : la recherche, la reformulation, le modèle, le total, mesurés par l'API.

**Le refus est une réponse** : 200, `refus: true`, `motif_refus`, une phrase honnête,
`series_citees` vide. Exemple, en mode strict, sans appel au modèle (la recherche n'a
rendu aucun passage) :

```json
{"refus": true, "motif_refus": "aucun_passage",
 "reponse": "Les passages disponibles ne permettent pas de répondre à cette question.",
 "series_citees": [], "series": [], "contexte": {"appels": [], "cout_estime_usd": 0.0, "date_appel": null, "…": "…"}}
```

### Le mode enrichi (1.4.0) : le complément « non vérifié »

`"mode": "enrichi"` dans la demande (`strict` reste le défaut ; `/v1/info` publie
`reglages_reponse.mode.valeurs = ["strict", "enrichi"]`, et l'application montre alors son
sélecteur). **Un seul appel** : la même recherche, le même contexte, la même invite, plus
un bloc versionné à part (`config/invites/complement.md`) ; l'invite de base ne change pas
d'un mot (son empreinte est comparée par un test). Le schéma de sortie gagne deux champs,
bornés : `complement` (600 caractères au plus) et `complement_titres` (cinq titres au
plus, 200 caractères chacun au plus ; au-delà : 503 `schema`). La partie stricte
(`reponse`, `refus`, `motif_refus`, `series_citees`) est inchangée et contrôlée comme
en strict.

- **Ce qu'est le complément** : ce que le modèle ajoute de ses connaissances générales,
  après la réponse fondée sur les passages, quand il a quelque chose d'utile — souvent
  quand le strict refuse (une œuvre hors catalogue). Il est rendu dans `complement`, les
  titres qu'il nomme dans `complement_titres`, et `complement_etiquette` porte un texte
  fixe à afficher avec lui : « non vérifié — connaissances du modèle, sans source ».
- **Ce qu'il n'est pas** : une source. Aucun passage ne le fonde ; **il peut être faux**,
  en particulier sur une œuvre hors catalogue, que rien ne permet de vérifier — et la
  mesure l'a montré (ci-dessous : 5 compléments faux et 2 dangereux sur 10 lus). C'est
  pourquoi il est étiqueté, et pourquoi il ne remplace jamais la partie stricte.
- **Ce qui est contrôlé** (après les contrôles de la partie stricte ; chacun : 503
  `sortie_modele_invalide`, motif préfixé `complement_`, son compteur ; pas de second
  essai) : `complement_pseudonyme` (un nom de membre, par la même méthode que la réponse,
  homonymes compris) ; `complement_disponibilite` (une disponibilité en France affirmée
  sur un titre du catalogue, les formes et les réserves de la réponse : une négation,
  une question, une comparaison n'en sont pas) ; `complement_metadonnee` (un nombre de
  tomes, un statut, un public, un auteur affirmés sur un titre du catalogue : formes
  versionnées dans `config/formes_affirmations.toml`, conservatrices — mieux vaut un 503
  de trop qu'une affirmation fausse ; une négation, une question n'en sont pas, une
  comparaison si) ; `complement_marque` (un `[S:…]` ou `[F:…]`). Un **titre du
  catalogue** : celui d'une série du catalogue rendue par la recherche, ou un titre de
  `complement_titres` que le catalogue porte (comparés sans casse, sans accents ni
  signes : lettres et chiffres seuls). Une forme se rapporte au titre nommé le plus près
  avant elle dans sa phrase, sinon au premier nommé après elle dans la phrase, sinon au
  dernier nommé dans les phrases d'avant, sinon au seul titre de `complement_titres`.
- **Ce qui ne l'est pas** : la justesse d'une affirmation sur une œuvre hors catalogue ;
  une métadonnée dite sous une forme que la liste ne connaît pas ; un titre du catalogue
  que le modèle écrit autrement que le catalogue (au-delà de la casse, des accents et
  des signes) ; **un titre du catalogue que le texte nomme sans le mettre dans
  `complement_titres`, quand la recherche ne l'a pas rendu** : il n'est pas reconnu
  (l'invite demande de nommer au plus cinq titres, pas de les reporter).
- **Complément vide ou absent** : valide ; les trois champs sont alors **absents** de la
  réponse — comme en strict, où la réponse reste celle de 1.3.1 champ pour champ (un test
  la compare à une réponse capturée en 1.3.1, et la requête au fournisseur à la sienne,
  sous la même forme canonique : clés triées).
- **Sans passage** : en enrichi, l'appel a lieu quand même, avec un contexte
  vide de passages ; la partie stricte doit être `refus: true`, `motif_refus:
  aucun_passage`, sans série citée (sinon 503 `refus_incoherent`), et le complément suit
  les contrôles. En strict, aucun appel, comme avant.
- **Jamais dans l'historique** : l'application ne renvoie à l'API que `reponse` comme tour
  de l'assistant ; le complément n'entre pas dans la conversation (testé côté
  application).
- **Profils de repli** : non mesurés en enrichi ; le mode reste sélectionnable avec eux.
- **Un mode retiré de `reglages.toml`** (`mode.valeurs`) disparaît de `/v1/info`, donc de
  l'interface (l'application ne montre plus le sélecteur), mais **reste accepté par
  l'API** : le contrat 1.4.0 garde l'énumération `strict | enrichi`. Le couper vraiment
  demandera une nouvelle version du contrat.
- **Mesure** (2026-11-07, en préproduction, `mistral-large-2512`, les 26 questions du profil
  actif ; banc de la réponse, `--mode enrichi`, run `59004a46…` de l'expérience
  `E3-reponse-enrichi-preprod`) : **le critère de la partie stricte n'est pas tenu en
  enrichi** — refus correct F7 **3 / 5** (5 / 5 en strict) : une question F7 n'est plus
  refusée, une autre rend un 503 pour son complément ; hors catalogue 4 / 5 ; 0 série
  inventée ; 0 nom de membre. **7 sorties sur 26 coupées** à la longueur par défaut (400
  jetons) : réponse et complément la dépassent. Complément présent 14 fois sur 26 ; après
  un refus de la partie stricte, 5 fois sur 6. 3 sorties refusées pour leur complément (1
  disponibilité, 2 métadonnées). Coût : 0,055 $.
- **Lecture humaine** (Max, 10 compléments, les refus d'abord, vérifications en ligne) :
  **2 utiles, 1 inutile, 5 faux, 2 dangereux**. Les deux « dangereux » décrivent comme
  réelles des œuvres **inventées** par le jeu d'évaluation (questions F7) ; les « faux »
  inventent des titres, une édition française, un auteur — sur des œuvres hors catalogue,
  que la route ne peut pas contrôler.
- **D'où la règle** : le mode enrichi reste **sélectionnable**, mais **n'est pas proposé par
  défaut** — `strict` reste le défaut de `reglages.toml`, et l'application le présente
  sélectionné. Motif : sur un échantillon lu, le complément affirme comme vraies des œuvres
  qui n'existent pas ; l'étiquette « non vérifié » ne suffit pas à en faire le comportement
  par défaut d'un outil de libraire.

### Les métadonnées d'une série

Depuis la version 1.2.0, chaque série de `/v1/recherche` et de `/v1/reponse` porte un objet
`metadonnees` (les auteurs depuis la 1.3.0), et chaque passage envoyé au modèle est précédé de sa
**ligne de titre** :

```text
[S:<id>] <titre> — <type> — auteur : <noms | inconnu> — tomes : <N> (champ) | au moins <N> | inconnu — <terminé | en cours | statut inconnu> — public : <public | inconnu> — disponibilité : <édité en France | seulement au Japon | inconnue> [F:<id>]
```

**La source** : la vue `manga.v_metadonnees_serie` (migrations 025 et 026), calculée à la lecture sur
les volumes et la fiche de Manga Sanctuary, le moyeu d'identité et Kitsu, lue une fois par
série sous `modele_api`, qui ne lit pas ces tables.

| Champ | Règle | Séries (2026-11-02, sur 14 670) |
|---|---|---|
| `disponibilite` | `edite_en_france` : un volume à EAN français (`9782`, `97910`) ou le badge « français » de la fiche ; `seulement_au_japon` : un manga à volumes sans cela ; `inconnue` sinon | 7 827, 6 804, 39 |
| `nb_volumes` | le plus grand du champ « tomes parus » et des numéros de volume distincts ; un numéro entre 1900 et 2100 ne compte pas : la collecte prend pour numéro le premier nombre du titre | 14 665 ; absent : 5 |
| `nb_volumes_source` | `divergent` : les deux comptes s'écartent de plus de 20 % du plus grand ; `champ` : le champ est au moins égal aux numéros ; `repli_volumes_distincts` sinon | 468, 10 615, 3 582 |
| `statut` | `termine` : le badge « Complète », sinon Kitsu `finished` ; `en_cours` : Kitsu `current` sans badge ; le catalogue n'écrit jamais « en cours » | 11 876, 827 ; absent : 1 967 |
| `demographie` | `series_category_clean`, sinon `series_category` s'il porte une valeur de la même liste ; le libellé du catalogue, tel quel | 13 680 ; absent : 990 |
| `annee_fr` | l'année du premier volume à EAN français | 7 629 ; absent : 7 041 |
| `auteurs` | le scénariste puis le dessinateur, tels quels, joints par une virgule, un même nom une seule fois ; un « inconnu » textuel vaut absence | 14 387 (2026-11-03) ; absent : 283 |

**Ce que ces mots ne disent pas** :

- **« Édité en France » ne veut pas dire « en stock »** : au moins un volume y est paru. Le
  catalogue ne connaît pas les stocks.
- **« Seulement au Japon »** : aucune édition française *connue du catalogue* ; pas la preuve
  qu'il n'y en a pas (voir plus bas). Il ne dit pas non plus d'où vient la série.
- **« Inconnue »** : la réponse ne peut rien en dire.
- **« Au moins N »** : toute source autre que `champ` est un minimum. Les numéros mêlent les
  éditions et sous-comptent une série dont le catalogue n'a qu'une partie des tomes ; le
  champ ne dit pas de quelle édition il parle.
- **L'âge** : **il n'y a pas de donnée**. La réponse ne dit une série adaptée à un âge ou à
  une contrainte de contenu que si un passage le dit explicitement ; sinon, elle dit qu'elle
  ne peut pas le garantir et signale les thèmes adultes qu'un passage mentionne.
- **Les auteurs** : ceux de la ligne. Deux séries ont le même auteur si leurs lignes le disent,
  pas autrement. La ligne compare des graphies, pas des personnes (voir « Limites déclarées »).
  Seul un lecteur ne se nomme pas : ni membre, ni pseudonyme, ni signature.

### `GET /v1/info`, `GET /v1/sante`

`GET /v1/info` (avec la clé) — le modèle d'embedding servi, et le modèle de langage :

```json
{"version_api": "1.5.0", "modele": "google/embeddinggemma-300m",
 "revision": "57c266a740f537b4dc058e1b0cda161fd15afa75", "precision": "float32", "dimension": 768,
 "outil": "text-embeddings-inference", "outil_version": "1.9.4",
 "corpus_id": "v2", "encodage_id": 3, "nb_fragments": 77006,
 "reglages": {"nombre_series": {"defaut": 10, "min": 1, "max": 50},
              "passages_par_serie": {"defaut": 1, "min": 1, "max": 5},
              "catalogue_seul": {"defaut": true}},
 "question": {"longueur_max_caracteres": 500}, "debit_par_minute": 60,
 "modele_langage": {
   "profil_actif": "mistral-large-2512",
   "profils": [
     {"nom": "mistral-large-2512", "fournisseur": "mistral", "modele": "mistral-large-2512",
      "statut": "stable", "disponible": true, "reglages_absents": [], "variable_cle": "MISTRAL_API_KEY"},
     {"nom": "gpt-6-luna", "fournisseur": "openai", "modele": "gpt-6-luna",
      "statut": "stable", "disponible": false, "reglages_absents": [], "variable_cle": "OPENAI_API_KEY"},
     "…"],
   "reglages_reponse": {"temperature": {"defaut": 0.0, "min": 0.0, "max": 1.0},
                        "longueur_max": {"defaut": 400, "min": 50, "max": 4000},
                        "mode": {"defaut": "strict", "valeurs": ["strict"]},
                        "historique": {"tours_max": 10, "caracteres_max": 4000},
                        "reformuler": {"defaut": true},
                        "nombre_series": {"defaut": 5, "min": 1, "max": 50},
                        "passages_par_serie": {"defaut": 2, "min": 1, "max": 5},
                        "catalogue_seul": {"defaut": true}}}}
```

`variable_cle` est le **nom** de la variable de la clé, jamais sa valeur.

`GET /v1/sante` (sans clé) :
`{"statut": "ok", "base": "ok", "service_embedding": "ok", "encodage": "ok"}`. En 503,
l'exemple du contrat est exact : `{"statut": "degrade", "base": "injoignable",
"service_embedding": "ok", "encodage": "inconnu"}` (la base ne répond pas).

### `POST /v1/verification` (1.5.0) : le test des clés

Pour l'administration de l'application (E4, lot 5, story A3) : **par profil déclaré**, la
variable de sa clé est-elle posée, et la liste des modèles de son fournisseur la reçoit-elle ?

- **Ce qu'elle fait** : pour chaque profil, `configuree` — la variable nommée par
  `variable_cle` est posée et non vide dans l'environnement de l'API ; si elle l'est,
  `GET …/v1/models` chez le fournisseur (**non facturé**), **une fois par couple** (adresse,
  variable), les couples en parallèle ; `disponible` — la liste a répondu et contient le
  modèle ; `statut` — `ok`, `injoignable` (connexion refusée ou délai de
  `[modele_langage.delais] connexion_s` dépassé), le code HTTP rendu par le fournisseur,
  `corps_inattendu` (liste illisible), ou `absente` (clé non posée : **aucun appel**).
  Corps vide ; réponse 200 `{"verifie_le": "<UTC>", "profils": [{nom, fournisseur, modele,
  variable_cle, configuree, disponible, statut}]}`. Un fournisseur injoignable n'empêche pas
  la réponse : c'est un rapport, pas une sonde de santé. Journal `verification_cles` (adresse,
  nom de la variable, statut, profils) ; métrique `api_modele_verifications_total{issue}`
  (`ok` : tous les profils disponibles, `partiel`, `aucune`).
- **Sa borne** : la clé d'accès comme les autres routes (401 sans elle), **et un appel par
  minute et par clé** (un `Limiteur` à part du débit des routes) : au-delà, 429
  `debit_depasse`, `Retry-After` — le fournisseur n'est pas un service à sonder en boucle.
  `POST` et non `GET` : elle a un coût réseau et une borne.
- **Ce qu'elle ne fait pas** : elle **ne coupe rien** — la disponibilité des profils en
  service (`/v1/info`) ne change pas, même si un fournisseur refuse la clé (seul le contrôle
  d'exploitation du démarrage, `API_MODELE_VERIFIER_MODELES=1`, en retire) ; elle **n'affiche
  rien** d'une clé — ni valeur, ni préfixe, ni longueur (prouvé par test : des valeurs
  factices reconnaissables, absentes de la réponse, du journal et des métriques).

## Poser ou changer une clé

Une clé de fournisseur (ou une clé d'accès d'application) ne vit que dans le `.env` du
module, hors dépôt, jamais dans un fichier suivi :

1. **la variable** : celle que nomme `variable_cle` du profil (`MISTRAL_API_KEY`,
   `OPENAI_API_KEY`), ou `API_MODELE_CLES` pour les clés d'accès (`nom:clé,…`) ;
2. **la poser** dans `11_api_modele/.env`, sans l'afficher (ni `cat`, ni capture) ; une
   rotation remplace la valeur, l'ancienne est révoquée chez le fournisseur ensuite ;
3. **les hooks** du dépôt refusent une clé de fournisseur dans un commit et un push (sa forme,
   et la valeur du poste) : le `.env` reste ignoré par git ;
4. **redémarrer** l'API (la pile, ou le service seul) : les clés et la disponibilité des
   profils sont lues au démarrage ;
5. **vérifier** : `/v1/info` (le profil est-il `disponible` ?), puis `POST /v1/verification`
   (la liste du fournisseur reçoit-elle la clé ? `statut: "ok"`) — l'écran « Clés » de
   l'administration de l'application l'appelle.

## Les erreurs

Une seule forme, `{"code", "message", "details", "identifiant_requete"}`, et un code
stable par cas. Les codes de `/v1/reponse` sont énumérés dans le contrat, sous leur
statut ; les tests en produisent chacun (« Vérifier »).

| Statut | `code` | Quand | `details` |
|---|---|---|---|
| 401 | `cle_invalide` | clé absente ou fausse | `null` |
| 404 | `route_inconnue` | route inconnue (dont `/v2/…`) | `null` |
| 405 | `methode_non_permise` | méthode non permise (en-tête `Allow`) | `null` |
| 413 | `corps_trop_volumineux` | corps au-delà de 16 Kio | `{"maximum_octets": 16384}` |
| 422 | `entree_invalide` | corps illisible ; champ manquant, inconnu, de mauvais type, hors bornes | `{"champs": [{"champ": "nombre_series", "raison": "hors_bornes", "bornes": [1, 50]}]}` |
| 422 | `question_trop_longue` | question au-delà de 500 caractères (avant tout appel au service), ou refusée par le service pour sa longueur | `{"longueur": 731, "maximum": 500, "unite": "caracteres"}` (ou `"jetons"`) |
| 422 | `historique_invalide` | (réponse) plus de 10 tours, plus de 4 000 caractères, rôles non alternés, premier tour non libraire, dernier tour non assistant | `{"motif": "trop_de_tours"}` (`trop_long`, `roles_non_alternes`, `premier_tour_non_libraire`, `dernier_tour_non_assistant`) |
| 422 | `profil_inconnu` | (réponse) `profil` hors des profils déclarés | `{"profil": "…", "profils": [...]}` |
| 422 | `profil_indisponible` | (réponse) profil déclaré sans clé | `{"profil": "gpt-6-luna"}` |
| 422 | `reglage_non_expose` | (réponse) un réglage demandé que le profil déclare absent | `{"reglage": "temperature", "profil": "…"}` |
| 429 | `debit_depasse` | débit dépassé pour la clé ; en-tête `Retry-After` | `{"limite_par_minute": 60, "reessayer_dans_s": 12}` |
| 503 | `service_embedding_indisponible` | service injoignable, lent ou en refus | `{"cause": "injoignable"}`, `{"cause": "delai_depasse", "delai_s": 10.0}` ou `{"cause": "refus"}` |
| 503 | `service_embedding_incoherent` | le service ne sert plus l'encodage en service (`/info` ≠ registre) | `null` |
| 503 | `base_indisponible` | base injoignable, pool épuisé, ou délai dépassé | `{"cause": "injoignable"}` ou `{"cause": "delai_depasse", "delai_s": 5.0}` |
| 503 | `encodage_en_service_change` | une promotion ou un retour arrière a eu lieu depuis le démarrage : redémarrer l'API | `{"charge": 3, "en_service": 4}` |
| 503 | `fournisseur_injoignable` | (réponse) connexion refusée, trop lente ou coupée | `{"profil": "…", "role": "reponse"}` |
| 503 | `fournisseur_delai` | (réponse) le fournisseur n'a pas répondu dans le délai du profil | `{"profil": "…", "role": "reponse"}` |
| 503 | `fournisseur_refus` | (réponse) le fournisseur a refusé l'appel (statut hors 2xx, 429 compris), rendu un corps inattendu (`motif` : `corps_inattendu`), ou atteint la borne de longueur avant la fin de la sortie (`longueur_atteinte`) ; sur un 429, en-tête `Retry-After` | `{"profil": "…", "role": "reponse", "statut": 429, "code_fournisseur": "rate_limited"}` — **jamais le corps** |
| 503 | `sortie_modele_invalide` | (réponse) la sortie du modèle ne tient pas le contrat | `{"motif": "…", "role": "reponse"}` (ou `"reformulation"`) |
| 500 | `erreur_interne` | imprévu ; la trace est au journal, sous l'identifiant | `null` |

Les onze motifs de `sortie_modele_invalide`, contrôlés dans cet ordre (les quatre du
complément en mode enrichi seulement), chacun compté par
`api_modele_sortie_invalide_total{motif}` :

| Motif | Le modèle a rendu… |
|---|---|
| `schema` | un JSON invalide, ou hors du schéma (champ manquant, de trop, mal typé) |
| `serie_inconnue` | une série citée qui n'est pas dans la recherche |
| `fragment_inconnu` | un fragment cité qui n'est pas un passage de sa série |
| `refus_incoherent` | un refus qui cite des séries ou sans motif, ou une réponse qui n'en cite aucune ou porte un motif ; en mode enrichi sans passage, autre chose qu'un refus `aucun_passage` sans série citée |
| `pseudonyme` | un nom de membre, dans la réponse ou la question reformulée (règle des homonymes : « Sécurité et données ») |
| `disponibilite` | une série citée dite disponible, publiée, éditée, sortie ou commandable en France, alors que sa ligne dit `seulement_au_japon` ou `inconnue` |
| `demographie` | un public (shōnen, seinen, shōjo, josei) différent de celui de la ligne, quand elle porte l'un de ces quatre |
| `complement_pseudonyme` | (enrichi) un nom de membre dans le complément ou ses titres |
| `complement_disponibilite` | (enrichi) une disponibilité en France affirmée sur un titre du catalogue |
| `complement_metadonnee` | (enrichi) un nombre de tomes, un statut, un public ou un auteur affirmés sur un titre du catalogue |
| `complement_marque` | (enrichi) un `[S:…]` ou `[F:…]` dans le complément ou ses titres |

Pour `disponibilite` et `demographie`, `details.serie_id` dit la série contredite. Dans
la partie stricte, **formes affirmatives seulement**, liste versionnée
(`config/formes_affirmations.toml`) : une négation, une question, une comparaison ne sont
pas des affirmations, et une forme se rapporte à la marque `[S:<id>]` la plus proche avant
elle dans la phrase. Pas de contrôle sur les tomes ni le statut dans la partie stricte :
leurs formes sont trop variées ; la ligne et l'invite suffisent, la mesure le dit. Le
complément du mode enrichi, lui, les contrôle (règles plus haut).

**Pas de second essai**, ni sur une sortie invalide ni sur un refus du fournisseur : un
seul appel par rôle. L'erreur est visible et comptée ; un second essai cacherait un modèle
qui dérape, ce que la mesure doit voir, et doublerait le coût d'un échec. La sortie
fautive n'est ni rendue ni journalisée.

**Une seule exception, le refus de débit** : sur un 429 de type `rate_limited` (forme
relevée chez Mistral ; aucune chez OpenAI à ce jour, ses 429 restent de simples refus),
l'appel refusé est rejoué **une fois**, après `Retry-After`, sinon après 2 s, et tracé
dans `appels`. Refusé encore, ou `Retry-After` au-delà de 10 s : le 503
`fournisseur_refus` habituel. Un refus de débit n'est pas facturé et ne dit rien du
modèle ; avec un historique, la route enchaîne deux appels, et le libraire ne doit pas
voir une erreur pour si peu.

**Tout 503 qui enveloppe un 429 du fournisseur porte l'en-tête `Retry-After`**, qu'il y ait
eu un nouvel essai ou non : la valeur du fournisseur si elle est entière et d'au moins 1 s,
sinon 2 s, l'attente du nouvel essai. Le corps ne change pas. **Limite** : un 429 sans
rapport avec le débit (un quota épuisé chez OpenAI, par exemple) le porte aussi, et attendre
n'y changera rien ; `details.code_fournisseur` le distingue quand le fournisseur en rend un.

`/v1/sante` rend 503 avec ses états (`degrade`), pas cette forme.

## Sécurité et données

- **Clé d'accès** en en-tête `X-API-Key`, une par application consommatrice. Elle est
  comparée en temps constant (`secrets.compare_digest`), **toutes** les clés du
  trousseau à chaque requête, sans arrêt à la clé trouvée. Une clé absente ou fausse
  rend le même 401 : rien ne distingue les deux cas. Le **nom** de la clé entre au
  journal, jamais sa valeur. `/v1/sante` et le contrat se lisent sans clé.
- **Débit** par clé : 60 requêtes par fenêtre glissante d'une minute, **communes** à la
  recherche et à la réponse. Au-delà, 429 et `Retry-After`. Le refus est compté par nom
  de clé (`/metrics`). Le test des clés (`/v1/verification`, 1.5.0) a, en plus, sa borne
  à lui : **un appel par minute et par clé**.
- **Version dans l'adresse** : `/v1/`. Une rupture ouvre `/v2/`.
- **Où vivent les secrets** : clés d'accès, clés des fournisseurs et DSN dans `.env` ;
  mot de passe de la base dans un pgpass (`~/.pgpass`, ou `.pgpass.conteneur` monté en
  secret), mode 600. Les trois fichiers sont ignorés par git. Aucun secret dans le
  dépôt, le journal, une réponse ou une capture ; les hooks du dépôt refusent une clé de
  fournisseur (sa forme, et la valeur du poste) dans un commit et un push.
- **La clé d'un fournisseur** est lue dans l'environnement au moment de l'appel, posée
  dans l'en-tête `Authorization` et nulle part ailleurs : ni journalisée, ni rendue, ni
  dans une erreur ; `/v1/info` ne publie que le **nom** de la variable. Le client HTTP ne
  lit ni mandataire ni `.netrc` du poste et ne suit pas de redirection ; une adresse de
  profil est en `https`, sauf vers le bouclage (doublures des tests).
- **Moindre droit** en base : `modele_api` hérite de `modele_ro` (migrations 023, 024).
  Ce rôle n'a de `SELECT` que sur les tables, colonnes et vues que l'API lit, et ses
  sessions sont en lecture seule. Il lit les pseudonymes par la vue
  `manga.v_pseudonymes` (les noms distincts), **jamais** la table des critiques : l'API
  ne sait pas qui a écrit quelle critique.
- **Aucun nom de membre** dans une sortie : après chaque appel, la réponse et la
  question reformulée sont confrontées aux pseudonymes, par la méthode du corpus v2 (mot
  entier, casse ignorée). Un nom n'est admis que s'il figure dans un passage envoyé pour
  cette requête et que le couple (document, empreinte) est dans la liste des homonymes
  admis de l'étape 1 (`database/donnees/corpus_homonymes_admis.csv`, des empreintes,
  aucun nom), ou s'il figure dans la question de la requête et que son empreinte est
  admise pour un document au moins : le modèle peut répéter un nom de personnage qu'on
  lui a donné, dans un passage ou dans la question, il ne peut en produire aucun autre ;
  tout autre nom est un 503. La liste vit en mémoire ; seuls ses nombres sortent.
- **Ce qui part chez le fournisseur** : l'invite système, la question, l'historique et
  les passages de la recherche (le texte des critiques compris, tel qu'il est au corpus).
  **Ce qui n'en part pas** : l'identité (`identite`), la clé d'accès, rien de la base hors
  des passages envoyés. **Aucune conservation côté projet** : ni question, ni historique,
  ni réponse ne sont gardés ou journalisés par l'API ; seuls leurs nombres (tours,
  taille, séries citées) et les codes entrent au journal. La transmission à chaque
  fournisseur est inscrite au registre des traitements du projet.
- **Entrée bornée** : modèle strict (champ inconnu refusé, aucune conversion de type),
  question de 500 caractères au plus, historique borné, corps de 16 Kio au plus.
- **Erreurs sans fuite** : jamais de trace de pile dans une réponse ; elle va au
  journal, sous l'identifiant de la requête. Toute réponse porte
  `X-Identifiant-Requete` et `Cache-Control: no-store`. Un refus du fournisseur ne rend
  jamais son corps.
- **Conteneurs** : images épinglées par empreinte, utilisateur non root. Le service
  d'embedding est sur un réseau interne, sans port publié ni accès sortant. L'API
  publie sur l'adresse locale par défaut.

## Coût

Le coût estimé d'un appel, en dollars :

```text
coût = (jetons d'entrée × prix d'entrée + (jetons de sortie + jetons de raisonnement) × prix de sortie) / 1 000 000
```

- les prix sont ceux du profil (« Les profils »), relevés le 2026-10-26 ; un prix absent
  rend un coût `null` (« prix non déclaré ») ;
- les jetons sont ceux que rend le fournisseur (`usage`) ; sans `usage`, ils sont
  **estimés** par caractères (3,8 caractères par jeton, `estimes: true`) — recalée
  sur les quatre appels d'essai, de 2 % à 6 % au-dessus des jetons facturés ;
- une réponse coûte deux appels quand il y a reformulation ; `contexte.cout_estime_usd`
  les somme, et la métrique `api_modele_llm_cout_usd_total{profil}` les cumule ;
- ordre de grandeur, pour le pire cas de la route (≈ 3 500 jetons d'entrée, 400 de
  sortie) : 0,0024 $ sur `mistral-large-2512`, 0,0064 $ sur `mistral-large-4`,
  0,0006 $ sur `gpt-6-luna`, 0,011 $ sur `gpt-6-sol` ;
- **plafond** : un run de mesure estime son coût avant le premier appel et s'arrête s'il
  dépasserait `COUT_PLAFOND_RUN` (0,50 $ par défaut). L'API, elle, ne bloque pas une
  requête sur le plafond ; elle écrit l'estimation avant appel dans la trace de chaque
  appel.

## Le banc de mesure de la réponse

Le seul chemin d'un appel réel hors du service : à la main, commande par commande
(`src/api_modele/banc_reponse/`). Aucun test ne l'emploie contre un fournisseur.

```bash
B="uv run --env-file .env python -m api_modele.banc_reponse"
$B estimer mistral-large-2512 famille:F7,issue:hors_catalogue   # aucun appel
$B estimer-scenarios mistral-large-2512 <fichier des scénarios>.csv   # aucun appel
$B essai mistral-large-2512 "<question inventée>" [--temperature 0.7] [--longueur-max 1000]
DATABASE_URL='postgresql://postgres@localhost:5432/apimanga' \
  $B mesurer mistral-large-2512 famille:F7,issue:hors_catalogue,famille:F9,famille:F10 [--etape E]
# le mode enrichi : essai, estimation, mesure (la ligne eval_runs en préproduction)
$B estimer mistral-large-2512 famille:F7,issue:hors_catalogue --mode enrichi
$B essai mistral-large-2512 "<question inventée>" --mode enrichi
DATABASE_URL='postgresql://postgres@localhost:5432/apimanga_preprod' \
  $B mesurer mistral-large-2512 famille:F7,issue:hors_catalogue,famille:F9,famille:F10 \
  --mode enrichi --experience E3-reponse-enrichi-preprod [--etape E]
DATABASE_URL=… $B scenarios mistral-large-2512 <fichier des scénarios>.csv [--etape E]
$B relire <dossier du run>             # selon les règles d'aujourd'hui, aucun appel
$B etiqueter                           # les runs déjà faits, a posteriori
$B registre <profil>=<run_id> …        # une version par profil
```

- **Où il tourne** : l'API dans le processus (comme les tests et l'étape d'équivalence
  de `verify-poste`), sur la base réelle sous `modele_api`, avec le service
  d'embedding réel levé à part ; une clé de consommateur tirée pour le seul
  lancement ; `API_MODELE_VERIFIER_MODELES` doit valoir faux.
- **Le jeu** : le jeu gelé v2, désigné **par famille ou par issue**, jamais question
  par question ; empreinte `31712b0d…` vérifiée avant tout run.
- **Gardes** : arbre propre et `HEAD` poussé avant `mesurer` et `scenarios` ;
  estimation avant le premier appel (formule de « Coût », sortie au maximum), arrêt
  si elle dépasse `COUT_PLAFOND_RUN` ; coût réel cumulé (depuis `usage`, appels
  rejetés compris) après chaque question, arrêt au plafond (run `interrompu`, ce qui
  est mesuré est gardé) ; arrêt aussi si un fournisseur refuse ou ne répond pas. Une
  sortie coupée par la borne de longueur (`longueur_atteinte`) n'est pas un
  fournisseur en échec : la question compte en échec, le run continue.
- **Ce qu'il garde** : un run MLflow par profil (expérience `E3-reponse` par défaut,
  réglable par `--experience` ; magasin du
  module 10, écrit par l'interpréteur du module 10 : le module 11 n'installe pas
  MLflow) ; ses pièces ne portent que des identifiants et des nombres — par appel,
  sa latence (échecs compris) et la forme de `message.content` (son type ; en liste,
  le type et la taille de chaque morceau) ; pour une
  sortie refusée par la route, sa forme (refus, nombre de séries citées, motif de
  refus) et, pour un nom de membre, ses **empreintes** (dans le message envoyé ou
  non, homonyme admis ou non), jamais le nom ni le texte. La ligne
  `bench.eval_runs` du run, sous le rôle qui l'écrit pour le module 10
  (`DATABASE_URL`, `postgres`, mot de passe dans `~/.pgpass`). Les **textes** des
  réponses, contrôlés (aucun nom de membre), dans
  `05_nettoyage_agregation_bdd/data/rapports/mesures_reponse/<run>/reponses.md`, hors
  dépôt et hors MLflow, pour la lecture humaine.
- **MLflow, en plus** : chaque run joint en artefacts les deux invites et `reglages.toml`,
  leurs empreintes en paramètres (`invite_reponse_sha`, `invite_reformulation_sha`,
  `reglages_sha`), et porte ses étiquettes : `etape` (`--etape` de `mesurer` et
  `scenarios`, 32 caractères au plus, lettres, chiffres, « . », « _ », « - » ; `E3-06`
  par défaut, soit E3, étape 3, spec 06 ; gardée dans les paramètres du run, que
  `etiqueter` relit), `role` (`profil_actif`, `repli`),
  `invite_version` (le commit court qui a écrit les invites), `statut` (`complet`,
  `interrompu`), `verdict` (`tenu`, `non_tenu`, sauf les scénarios, sans critère),
  `cause_interruption`. Les runs déjà faits ont reçu leurs étiquettes a posteriori, pas
  d'artefacts : leurs invites sont celles de leur commit.
- **Le registre de modèles** `conseiller-manga-reponse` : une version par profil, liée au
  run qui l'a qualifiée ; *Production* pour `mistral-large-2512`, *Staging* pour
  `mistral-large-4`, *Archived* pour les deux profils OpenAI. **Il ne contrôle rien** : le
  profil actif reste celui de `reglages.toml` ; il rend la promotion visible. Les stades sont
  dépréciés par MLflow, encore acceptés à la version épinglée (3.17.0).
- **Critère, écrit avant la mesure** (`banc_reponse/mesure.py`, `CRITERE`), par
  profil : refus correct ≥ 4 / 5 sur F7 ; refus, ou mention « pas au catalogue »,
  ≥ 4 / 5 sur les cinq questions hors catalogue ; **zéro** série inventée ; **zéro**
  sortie invalide pour un nom de membre ; depuis la 1.2.0, **zéro** sortie invalide
  `disponibilite` et `demographie`. Pas de seuil sur le recouvrement (suites de
  8 mots de la réponse présentes telles quelles dans un passage) ni sur F9 / F10 : à
  la première mesure, on écrit le chiffre. `titre_present` : part des séries citées
  dont le titre est juste avant ou juste après chacune de leurs marques dans le texte
  **du modèle**, avant l'insertion de la route — la règle de l'insertion, appliquée à
  ce que le modèle fait seul (son texte reste en mémoire ; il n'est écrit, hors dépôt,
  que pour la lecture d'une sortie refusée sans nom de membre) ; une série
  citée que le texte ne marque pas est comptée à part (`citees_sans_marque`).
- **Deux lectures du critère, arrêtées après une mesure et écrites comme telles** (voir
  les résultats) : dire le titre demandé absent des passages vaut mention « pas au
  catalogue » ; une sortie `disponibilite` dont un passage envoyé dit une édition française
  (`catalogue_en_tort`) reste rejetée par la route, mais se compte à part, hors du zéro.
- **`relire <dossier>`** applique les règles d'aujourd'hui à un run mesuré, sans appel à un
  fournisseur : son verdict d'alors reste (`resume.json`, métriques d'origine, étiquette
  `verdict_a_la_mesure`), la relecture s'y ajoute (`relecture.json`, métriques `_relu`).
- **Codes de sortie** : 0 fait ; 2 refusé avant tout appel ; 3 run interrompu ; 4 run
  mesuré mais non enregistré (`reprendre <dossier>` rejoue l'enregistrement).
- **Le mode enrichi** (`--mode enrichi`, pour `essai`, `estimer`, `mesurer` ; `strict` par
  défaut, et la requête reste alors celle d'avant le mode, sans le champ `mode`) :
  - **l'estimation** compte l'invite avec le bloc du complément et, en enrichi, **un
    appel aussi pour une question sans passage** (question 33) ; elle dit combien de
    questions sont sans passage (`sans_passage`) ;
  - **le critère** porte sur la partie stricte, inchangé : un 503 `complement_*` n'y
    entre pas (la question compte comme une sortie invalide, et le refus correct qu'elle
    portait peut-être n'est pas compté) ; un nom de membre que le banc trouverait dans un
    complément **rendu** y entre, comme dans la réponse (le banc refait le contrôle des
    noms sur le complément et ses titres ; pour un 503 `complement_pseudonyme`, les
    empreintes du nom, jamais le nom) ;
  - **les métriques du complément** : `complement_present` (sur les questions),
    `complement_quand_refus` (sur `refus_rendus`), aussi pour F7 et pour les questions
    hors catalogue (`_F7`, `_hors_catalogue`) ; `complement_titres_moyen` (sur les
    compléments rendus ; absente sans complément) ; `sorties_invalides_complement`
    (chaque motif `complement_*` compté aussi à part) ; `complement_titre_non_declare` :
    les compléments dont le texte nomme un titre du catalogue absent de
    `complement_titres` ; `complement_titre_hors_controle` : parmi eux, ceux dont le
    titre n'est pas non plus celui d'une série rendue par la recherche — la limite du
    contrôle de la route (voir « Ce qui ne l'est pas »), comptée. Un titre est trouvé
    dans le texte par sa suite de mots (lettres et chiffres seuls, sans casse ni
    accents), le plus long d'abord, titres déclarés compris ; « déclaré » se juge par
    la clé de comparaison du catalogue. Les titres trouvés sont gardés par question.
    **Limites** : un titre court du catalogue peut être un mot ordinaire du texte
    (« Ciel », « Air », « 1945 » : faux positif) ; un titre écrit autrement que le
    catalogue (« Dragonball » pour « Dragon Ball ») n'est pas trouvé (faux négatif) :
    la lecture des titres trouvés tranche ;
  - **les textes** des compléments rendus (marqués quand ils suivent un refus), et pour
    une sortie refusée pour son complément, sa réponse et son complément s'ils ne portent
    aucun nom de membre, vont dans `reponses.md`, hors dépôt et hors MLflow, pour la
    lecture de Max ;
  - **l'estimation** ne compte pas le schéma de sortie (`response_format`), plus long en
    enrichi : ce que le fournisseur facture en entrée peut dépasser le compte des
    caractères envoyés ; le plafond du run (`COUT_PLAFOND_RUN`), sur le coût réel, garde ;
  - **l'enregistrement** : `--experience <nom>` choisit l'expérience MLflow (défaut
    `E3-reponse` ; `E3-reponse-enrichi-preprod` pour la mesure de l'étape 6 : l'expérience
    dit l'environnement) ; l'étiquette `mode` est posée sur chaque run ; la note de la
    ligne `bench.eval_runs` dit le mode. Cette ligne s'écrit dans la base de
    `DATABASE_URL` : pour la mesure de l'étape 6, la préproduction (`apimanga_preprod`),
    jamais la production ; l'API du banc, elle, lit sa base comme toujours, en lecture
    seule.

## Résultats de la mesure (2026-10-30 ; profil actif rejoué les 2026-11-02 et 03)

Jeu v2 gelé (`31712b0d…`), réglages par défaut (`temperature` 0, strict, 5 séries ×
2 passages, catalogue seul), sans historique ; un run MLflow par profil (expérience
`E3-reponse`). Nombres entiers sur 5 : cinq questions par famille ne départagent pas,
aucun test statistique n'est fait.

| Profil | Questions | Refus corrects F7 | Hors catalogue | Séries inventées | Sorties invalides | Coût réel | Latence du modèle, médiane | Critère |
|---|---|---|---|---|---|---|---|---|
| `mistral-large-2512` | 26 (F7, hors catalogue, F9, F10) | **5 / 5** | **5 / 5** | 0 | 0 | 0,041 $ | 3,6 s | **tenu** |
| `mistral-large-4` (aperçu ; `low`, 2 000 jetons) | 10 | **5 / 5** | **4 / 5** (une sortie coupée à la borne) | 0 | 0 | 0,081 $ | 7,8 s | **tenu** |
| `gpt-6-luna` | 10 | **4 / 5** | **5 / 5** | 0 | 0 | 0,003 $ | 1,7 s | **tenu** |
| `gpt-6-sol` | 10 | **5 / 5** | **5 / 5** | 0 | 0 | 0,058 $ | 1,5 s | **tenu** |

- **F9 et F10** (profil actif, première mesure, sans seuil) : au moins une série
  attendue parmi les citées pour 1 question sur 16 ; la recherche ne rend une série
  attendue dans ses dix premières que pour 2 questions sur 16 (le chiffre de la
  mesure 8, recalculé). Le titre du catalogue accompagne 32 des 34 séries citées dans
  le texte du modèle, avant l'insertion par la route.
- **Ce que la première mesure a corrigé** (profil actif, F7 à 2 / 5) : le modèle
  refusait les cinq questions F7, mais trois refus citaient des séries, ou reprenaient
  un nom de la question qui est aussi un pseudonyme. D'où trois corrections, chacune
  motivée par un relevé : en cas de refus, `series_citees` reste vide ; un homonyme
  admis peut venir de la question de la requête (« Sécurité et données ») ; deux ou
  trois séries au plus, deux phrases par série. Remesuré ensuite : 5 / 5.
- **Réglages confirmés par les appels d'essai** : `temperature` acceptée par les
  quatre profils (0 partout, 0,7 essayée chez OpenAI) : aucun réglage absent ;
  `reasoning_effort = none` **refusé** par Mistral pour `mistral-large-4` (admis :
  `low`, `medium`, `high`), d'où `low` ; à `low`, ce modèle raisonne de 530 à plus de
  2 000 jetons selon l'appel (1 101 puis 2 405 sur la même question), d'où sa
  `longueur_max` de 2 000 et une sortie sur dix coupée à la borne ; sa sortie arrive
  en morceaux typés. Chez Mistral comme chez OpenAI, les jetons de raisonnement sont
  comptés dans `completion_tokens` (vérifié chez Mistral ; chez OpenAI, aucun jeton de
  raisonnement à l'effort `none`).
- **Coût de toute la mesure** (appels d'essai, runs, scénarios, rejeux) : 314 appels,
  0,55 $.

### Le rejeu du profil actif, et la conversation (2026-11-02)

Deux phrases ajoutées aux invites après lecture des réponses : la réponse n'affirme
aucune métadonnée que les passages n'écrivent pas ; la reformulation n'ajoute aucun
critère absent de la conversation. **Seul le profil actif est remesuré : les trois profils
de repli restent mesurés sur l'invite d'avant**, sans le nouvel essai de la route. Une
phrase de plus, ajoutée à la reformulation après ce rejeu contre le report de critères
d'un tour sur le suivant, n'est pas mesurée.

| Profil | Questions | Refus corrects F7 | Hors catalogue | Séries inventées | Sorties invalides | Coût réel | Latence du modèle, médiane | Critère |
|---|---|---|---|---|---|---|---|---|
| `mistral-large-2512` | 26 (F7, hors catalogue, F9, F10) | **5 / 5** | **5 / 5** | 0 | 0 | 0,041 $ | 3,9 s | **tenu** |

- **Conversation** (5 scénarios, 19 tours, première mesure, sans seuil) :
  `reformulation_meme_serie` 6 / 14 ; `reformulation_fidele` 9 / 13 ; série attendue en tête
  de la recherche 5 fois sur 12, contre 8 sur 12 sur la question autonome écrite à la main.
  Des 7 échecs, 3 héritent d'une mauvaise série née au premier tour, fidèlement reprise par
  la reformulation ; 4 viennent de la recherche elle-même : même la question autonome ne met
  pas la série attendue en tête. Un seul run : à `temperature` 0, les réponses varient d'un
  run à l'autre.
- **Limites à cette date** : sur une série au catalogue sans édition française, une réponse
  affirmait encore une disponibilité en français : la disponibilité n'était pas construite,
  et une consigne d'invite ne suffit pas à l'éviter. Une reformulation peut encore reporter un
  critère d'un tour sur le suivant. Une réponse peut mal lire un passage qui écrit le bon
  fait pour un autre volume. Les refus de débit de Mistral reviennent en conversation (5 sur
  39 appels) : le nouvel essai de la route en a sauvé un, mais 2 requêtes sur 21 ont rendu
  le 503 ; le plafond du compte n'a pas été lu.

### Le rejeu sur les métadonnées (2026-11-03)

Ligne de titre complète, invite qui rend la ligne prioritaire, deux contrôles de sortie.
**Seul le profil actif est remesuré : les trois profils de repli restent mesurés sur l'invite
d'avant** (leurs refus en F7 ne dépendent pas de ces phrases).

| Profil | Questions | Refus corrects F7 | Hors catalogue | Séries inventées | Sorties invalides | Coût réel | Latence du modèle, médiane | Critère |
|---|---|---|---|---|---|---|---|---|
| `mistral-large-2512` | 26 | **5 / 5** | **3 / 5 à la mesure, 5 / 5 relu** | 0 | 1 `disponibilite`, catalogue en tort | 0,047 $ | 2,6 s | **non tenu à la mesure, tenu relu** |

- **Une interprétation rendue après la mesure, écrite comme telle.** Le critère dit « refus,
  ou mention pas au catalogue », sans dire quelles formes comptent. Sur deux questions, le
  modèle dit le titre demandé absent des passages, puis propose d'autres séries, sans refus :
  la lecture a été arrêtée après avoir vu le chiffre (3 / 5), pour que cette absence dite
  compte. **Un critère écrit avant dit aussi comment on le lit.**
- **La sortie rejetée** : le texte dit publiée en France une série dont le catalogue n'a
  qu'une ligne japonaise, alors que deux fragments citent l'éditeur français. La règle a
  joué, le catalogue a tort, le libraire reçoit un 503 ; l'arbitrage reviendra au libraire
  à l'interface (étape 4).
- **Lecture des 16 réponses F9 et F10 contre leur ligne** : dans les 15 rendues, tomes 0,
  statut 0, disponibilité 0 ; public, 1 contradiction implicite (une série proposée sur une
  demande de seinen, ligne Shonen). F9 / F10 : une série attendue citée pour 1 question sur
  15 ; dans les dix premières de la recherche pour 2 sur 15.
- **Conversation** (5 scénarios, 19 tours, sans seuil) : `reformulation_fidele` 9 / 10 ;
  `reformulation_meme_serie` 3 / 14 (6 / 14 au rejeu d'avant : trois tours basculent, entre
  deux runs qui ne diffèrent pas que par la ligne : l'invite de reformulation a changé aussi) ;
  série attendue en tête 4 sur 12, contre 8 sur 12 sur la question autonome. Aucune
  contradiction de la ligne sur 19 tours, aucune sortie rejetée. **Les deux cas lus comme
  faux au rejeu d'avant sont justes** : disponibilité en français, série terminée.
- **Refus de débit** : 4 sur 37 appels, tous sur un tour, que le nouvel essai de la route n'a
  pas sauvé (rendu à la troisième tentative du banc). 0,041 $.

### Le rejeu sur les auteurs (2026-11-03)

L'auteur sur la ligne de titre, une phrase d'invite, pas de contrôle de sortie. **Seules les
conversations sont rejouées** : l'auteur n'est pas en jeu dans les 26 questions du run 1, qui
restent mesurées sur l'invite d'avant.

| Conversation (5 scénarios, 19 tours, sans seuil) | Rejeu d'avant | Ce rejeu |
|---|---|---|
| `reformulation_fidele` | 9 / 10 | **10 / 12** |
| `reformulation_meme_serie` | 3 / 14 | **5 / 14** |
| Série attendue en tête, question reformulée | 4 / 12 | **4 / 12** |
| Série attendue en tête, question autonome | 8 / 12 | 8 / 12 |
| Contradictions de la ligne | 0 | **0** |
| Sorties rejetées | 0 | **0** |
| Coût réel | 0,041 $ | 0,043 $ |

- **Le cas qui a motivé la colonne** : au rejeu d'avant, la réponse affirmait une même autrice
  pour deux séries qui n'en ont pas. Elle ne l'affirme plus : elle dit l'autrice de la série
  précédente absente des passages, ce qui est vrai (sixième de la recherche, seules les cinq
  premières sont envoyées), et le seul auteur qu'elle nomme sur les 19 tours est celui de sa
  ligne. **La série attendue n'est pas trouvée pour autant** : voir « Limites déclarées ».
- **Un run de chaque, sans conclusion sur les deux métriques** : la reformulation dépend aussi de
  la réponse du tour d'avant, et celle-ci a changé avec l'invite.
- **Refus de débit** : 6 sur 41 appels, sur deux tours que le nouvel essai de la route n'a pas
  sauvés (rendus à la deuxième et à la troisième tentative du banc).

## Les traces

- **Journal** : une ligne JSON par requête (identifiant, route, statut, code, durée,
  nom de la clé ; pour la réponse : profil, tours et taille de l'historique,
  reformulation `faite` / `ignoree` / `sans`, refus, séries citées, coût, le mode dans
  les réglages et, en enrichi, si un complément a été rendu) et une ligne
  par appel au modèle (`appel_modele` : profil, fournisseur, modèle demandé et rendu,
  statut, réglages, jetons, coût, estimation avant appel, latence, date, nom de la
  variable de clé). Jamais un texte, jamais une clé.
- **Métriques** (`/metrics`, réseau interne) : en plus de celles de l'étape 2,
  `api_modele_llm_appels_total{profil, role, statut, mode}` (`mode` : `strict` ou
  `enrichi`, depuis 1.4.0),
  `api_modele_llm_jetons_total{profil, role, type}`,
  `api_modele_llm_cout_usd_total{profil}`, `api_modele_llm_latence_secondes{profil,
  role}`, `api_modele_profil_actif{profil}` (le profil **par défaut** : une demande peut
  en choisir un autre), `api_modele_profil_disponible{profil}`,
  `api_modele_sortie_invalide_total{motif}`, `api_modele_reponses_total{refus, motif}`
  (les réponses rendues, `motif` valant `aucun` hors d'un refus). Les séries du modèle de
  langage sont créées à 0 au démarrage, pour chaque profil déclaré : `increase()` voit
  ainsi le premier appel. La méthode HTTP de `api_modele_requetes_total` est une liste
  fermée (`GET`, `POST`, `HEAD`, `OPTIONS`, `autre`).

## Limites déclarées

- **Le profil actif est plafonné par le compte Mistral à 15 requêtes par minute (2026-11-03), soit
  environ 7 tours de conversation : au-delà, un 503 après un seul nouvel essai.** Ce 503 rend
  l'attente à l'appelant (`Retry-After`) ; il n'y a pas de file d'attente (épreuve E4).
- **Une ligne fausse prime sur un passage juste.** C'est le revers de la priorité de la
  ligne : une série dite terminée sur le badge d'une édition (136 numéros au catalogue), un
  « seulement au Japon » faute de ligne française. Une réponse qui suit le passage rend alors
  un 503 : ligne et passages contradictoires, l'arbitrage reviendra au libraire (étape 4).
- **« Au moins N » n'est pas toujours repris** : la réponse peut écrire le nombre sec, ou mal
  en raisonner (« au moins 9 », donc « moins de 10 »). Le mot technique « (champ) » de la ligne
  peut passer dans le texte. Les valeurs aberrantes du champ restent (1 624, 176).
- **« Du même auteur » dépend de la recherche, qui ne connaît pas les auteurs.** La réponse ne
  compare que les lignes des cinq séries envoyées, et dit sinon ce qu'elle ne sait pas : une
  autre série du même auteur n'arrive que par hasard.
- **La ligne compare des graphies, pas des personnes.** Scénariste puis dessinateur, dans
  l'ordre du catalogue : sept paires de noms sont croisées d'une série à l'autre, et leurs
  lignes diffèrent. `COLLECTIF` (33 séries) reste tel quel : deux séries `COLLECTIF` ont le même
  auteur pour elle. Deux noms ont deux graphies, et les romanisations ne se rapprochent pas.
  Aucun contrôle de sortie sur les auteurs : leurs formes sont trop variées.
- **Dix séries ont un auteur dont le nom contient celui d'un membre** (2026-11-03). Si la réponse
  écrit ce nom, hors d'un homonyme admis, le contrôle la rejette (503) : le refus de trop, jamais
  la fuite.
- **Une reformulation peut encore reporter** un critère, ou une métadonnée, d'un tour sur le
  suivant.
- **Les contrôles de sortie ne voient que des formes.** Un public seulement sous-entendu, ou
  affirmé sur une ligne « inconnu » ou portant un autre libellé (`Yuri`, `Yaoi`), passe.
- **Une instance.** Le débit est compté en mémoire du processus : plusieurs instances
  derrière un répartiteur auraient chacune leur compte.
- **Pas de haute disponibilité.** Un seul service d'embedding, une seule base, aucune
  reprise automatique (`restart: "no"`), aucune bascule entre fournisseurs. Si le
  service tombe, la recherche et la réponse rendent 503 et `/v1/sante` le dit ; si le
  fournisseur tombe, la réponse rend 503 `fournisseur_*`, et le repli est un geste
  (« Changer de profil »).
- **Un modèle en aperçu peut changer** (`mistral-large-4`) : ses mesures portent la
  mention « aperçu » et peuvent ne pas se rejouer.
- **L'estimation de coût est à ± 30 %** tant qu'elle n'est pas recalée sur des appels
  réels.
- **Les refus du fournisseur ne sont pas réessayés**, sauf le refus de débit, une fois :
  une limite qui dure rend donc encore un 503 au libraire. Le plafond du compte n'a pas
  été lu.
- **Le réseau sortant du conteneur de l'API n'est pas restreint aux deux
  fournisseurs** : l'API est sur le réseau `sortie` du Compose (non interne) pour joindre
  la base de l'hôte et les fournisseurs, et Docker Compose ne sait pas restreindre une
  sortie à des noms d'hôte. Ce qui doit sortir : `api.mistral.ai` et `api.openai.com`
  (HTTPS), et la base de l'hôte. Proposition, non faite : un pare-feu de l'hôte (chaîne
  `DOCKER-USER`) ou un mandataire sortant à liste d'hôtes. Le service d'embedding, lui,
  est sur le réseau `interne`, sans accès sortant.
- **Les vecteurs et la liste des pseudonymes sont chargés au démarrage.** Une promotion
  d'encodage en cours de route donne 503 `encodage_en_service_change` jusqu'au
  redémarrage ; un pseudonyme nouveau n'est connu qu'au redémarrage.
- **Mémoire** : 1,5 à 1,8 Go au chargement, 0,5 Go ensuite. Avec le service, 6,9 Go
  disponibles sont exigés par `verify-poste`.
- **`/metrics` dans le Compose** : vu de l'hôte, le client n'est pas le bouclage du
  conteneur. Admettre le réseau de Prometheus par `API_MODELE_METRIQUES_RESEAUX`.
- **La documentation interactive** sert Swagger UI 5.33.1 depuis l'API : les deux
  fichiers sont vendus dans `src/api_modele/statique/`, avec leurs empreintes et leur
  licence (Apache 2.0). La page ne charge rien d'ailleurs et fonctionne sans réseau.
  Elle est désactivée par défaut. Le contrat, lui, est toujours servi.

## Ajouter un profil, ou un fournisseur

**Un profil, sans toucher au code** (un fournisseur déjà connu : Mistral ou OpenAI) :

1. une table `[modele_langage.profils.<nom>]` dans `config/reglages.toml` : `fournisseur`,
   `modele`, `adresse` (en `https`, finissant par `/v1/chat/completions`), `variable_cle`
   (le **nom** de la variable de sa clé), `reglages_fixes` (`top_p`,
   `reasoning_effort`), `reglages_absents`, `prix_usd_par_million` (facultatif),
   `delai_s` (facultatif), `statut` (`stable` ou `apercu`), et son **motif** ;
2. la clé dans le `.env`, sous ce nom de variable ;
3. un essai : redémarrer l'API (une configuration fautive est refusée, tous les écarts
   nommés), `/v1/info` montre le profil et sa disponibilité, puis un appel d'essai, coût
   affiché avant, et la mesure du profil avant de le servir en démonstration.

**Un fournisseur nouveau** : un adaptateur dans `src/api_modele/fournisseurs.py`
(`ADAPTATEURS`), avec, en commentaire, la page officielle lue et sa date, et son nom dans
`configuration.FOURNISSEURS`. Il doit savoir :

- répondre sur `/v1/chat/completions`, à `model`, `messages` (système, utilisateur),
  `temperature`, `top_p`, et un format de sortie en schéma JSON strict
  (`response_format`, `json_schema`) — sinon ses écarts vivent dans son adaptateur ;
- dire le champ qui borne les jetons de sortie, où `usage` range les jetons de
  raisonnement, et s'ils sont comptés dans les jetons de sortie ;
- rendre la sortie dans `choices[0].message.content`, et `usage.prompt_tokens`,
  `usage.completion_tokens` ; chez Mistral, un modèle qui raisonne rend `content` en
  **morceaux typés** (page « Reasoning », docs.mistral.ai/capabilities/reasoning, lue le
  2026-10-31) : l'adaptateur met bout à bout les morceaux `text` et ignore les morceaux
  `thinking` (le raisonnement) ;
- authentifier par `Authorization: Bearer` ; lister ses modèles sur `/v1/models` (pour le
  contrôle d'exploitation).

Les invites ne s'écrivent pas pour un fournisseur : une seule, pour tous les profils.
Puis les tests à doublure de la couche (`tests/test_fournisseurs.py`), une ligne au
registre des traitements, et un appel d'essai.

## Ajouter une route sans casser `/v1/`

`/v1/` ne reçoit que des **ajouts compatibles** :

- une route nouvelle ;
- un champ de sortie nouveau ;
- un champ d'entrée optionnel dont le défaut garde le comportement ;
- un code d'erreur nouveau sous un statut déjà déclaré.

Les sorties ne ferment pas leurs champs. Un client qui ignore les champs inconnus ne
voit donc pas la différence (`tests/test_versionnage.py`). Le contrat livré en 1.0.0 est
gardé à côté du contrat courant (`contrat/openapi_v1.0.0.json`) : un test vérifie que
ses routes, ses codes et ses champs tiennent toujours.

Pour ajouter une route :

1. **La déclarer** sous le préfixe (`f"{prefixe}/…"`, `application.py`) :
   - ses formes dans `modeles.py`, l'entrée stricte (`extra="forbid"`) ;
   - **tous** ses statuts dans `responses`, avec le modèle `Erreur`, et, pour un statut
     qui en a plusieurs, ses codes d'erreur nommés entre accents graves dans la
     description ;
   - la clé (`CleExigee`) si elle n'est pas publique.
2. **Un scénario par case** dans `tests/test_contrat_codes.py` (`SCENARIOS`) : la
   matrice statut × code est lue dans le contrat ; une case sans scénario fait échouer
   le test qui compare les deux. Un statut, ou un code, produit sans être déclaré fait
   échouer la suite (fixture `aucun_statut_hors_contrat`).
3. **Régénérer le contrat** : `uv run python -m api_modele.contrat`, puis commiter
   `contrat/openapi_v1.json` avec la route. Le test d'égalité à l'octet refuse un
   contrat périmé.
4. Monter la version **mineure** du `pyproject.toml` (1.1.0 pour `/v1/reponse`, 1.2.0 pour
   `metadonnees`, 1.3.0 pour `auteurs`, 1.4.0 pour le mode enrichi : une valeur
   d'énumération d'entrée et trois champs facultatifs ; contrat figé à côté,
   `contrat/openapi_v1.3.1.json`, comparé par un test ; 1.5.0 pour
   `/v1/verification`, une route et deux schémas, sur `contrat/openapi_v1.4.0.json`
   figé) : la
   majeure reste celle de l'adresse. Un en-tête documenté, sans champ ni route, monte la
   corrective (1.3.1 pour `Retry-After`).

**Toute rupture ouvre `/v2/`** : retirer ou renommer un champ, restreindre un type ou
une borne, changer un défaut, changer la définition du score ou du classement. Il faut
alors :

- ajouter `v2` aux versions servies (`configuration.VERSIONS_SERVIES`) ;
- produire son contrat, `contrat/openapi_v2.json`, à côté de celui de v1 ;
- garder `/v1/` tant que l'application n'a pas migré.

Une promotion d'encodage n'est pas une rupture : le contrat ne change pas, les
résultats si, et `contexte` le dit. Un changement de profil non plus.

## Vérifier

```bash
make verify MODULE=11        # depuis la racine du dépôt
```

- Ruff ; puis les tests (base PostgreSQL jetable par le harnais partagé, doublures HTTP
  du service d'embedding et des fournisseurs ; **aucun ne joint un fournisseur réel** :
  une garde fait échouer toute requête hors du bouclage) ; la couverture. Ils couvrent
  notamment :
  - le contrat case par case : chaque statut déclaré, et chaque code d'erreur que le
    contrat énumère, produit par un scénario au corps validé ; aucun statut ni code non
    déclaré produit ; la compatibilité avec le contrat 1.0.0 figé ;
  - la définition de fini de la route : « aucun nom de membre » (réponse, question
    reformulée, homonymes), deux profils factices (par la configuration, par le champ
    `profil`), aucune valeur de clé dans `reglages.toml` ni `.env.example` (motifs des
    hooks rejoués), la clé absente de toutes les sorties, journal, métriques et
    `contexte` concordants ;
- **l'essai d'intégration du Compose** (`compose.integration.yml`), sur le poste comme
  sur GitHub. Il lève le Compose du module : base jetable migrée, rôle par sa
  procédure, jeu d'essai, doublure du service, **doublure du fournisseur** (sur le
  bouclage du conteneur de l'API, avec un profil d'essai et des clés factices), API
  réelle. Puis il joue `/v1/sante`, une recherche, `/v1/info` (profils et
  disponibilité), un 401, deux réponses (sans puis avec historique), et détruit le
  Compose ;
- les exigences du module neuf, bloquantes : Pyright strict sur `src/`, Bandit,
  pip-audit.

`make verify-poste` ajoute ce qui ne tourne que sur le poste, sous des rôles en lecture
seule :

- l'égalité avec le banc sur les 69 questions, avec le service et la base réels ;
- le contrôle des pseudonymes dans les passages rendus ;
- l'état de la base.

`make verify-mutations` rejoue les mutations du module (`verification/mutations/API-*`) :
chacune fausse une garde, et son test doit rougir.
