# 09 — service d'embedding

Encodage vectoriel du corpus RAG par un **service préexistant**, Text Embeddings
Inference (Hugging Face), installé par son image Docker officielle et configuré selon
sa documentation : [documentation de Text Embeddings Inference](https://huggingface.co/docs/text-embeddings-inference).
Deux modèles, deux instances, deux ports :

| Instance | Modèle | Révision | Dimension | Précision | Port (API et `/metrics`) |
|---|---|---|---:|---|---:|
| `bge-m3` | `BAAI/bge-m3` | `5617a9f6…` | 1 024 | float32 | 8081 |
| `embeddinggemma` | `google/embeddinggemma-300m` | `57c266a7…` | 768 | float32 | 8082 |

Un seul chemin d'encodage : fragments et questions passent par le service, et par le
client de ce module, qui applique les préfixes.

## Sommaire

1. [Gestion des accès : jeton, licence, ports, absence d'authentification](#1-gestion-des-accès)
2. [Installation pas à pas, prérequis compris](#2-installation)
3. [Procédure de test : contrôles, commandes, résultats attendus](#3-procédure-de-test)
4. [Dépendances et interconnexions](#4-dépendances-et-interconnexions)
5. [Données impliquées](#5-données-impliquées)
6. [Monitorage : adresse des métriques et compteurs](#6-monitorage)
7. [Référence : configuration, encodage du corpus, constats sur la version 1.9.4](#7-référence)

## 1. Gestion des accès

### 1.1 Jeton Hugging Face et licence

| Modèle | Accès | Ce qu'il faut |
|---|---|---|
| BGE-M3 | libre (licence MIT, selon la fiche du modèle) | aucun jeton |
| EmbeddingGemma | **contrôlé** (conditions d'utilisation de Gemma) | accepter la licence, puis un jeton |

Pour EmbeddingGemma :

1. Se connecter à Hugging Face avec le compte qui émettra le jeton, et accepter la
   licence sur la [page du modèle EmbeddingGemma sur Hugging Face](https://huggingface.co/google/embeddinggemma-300m).
2. Créer un jeton en **lecture**. Un jeton « à grain fin » doit avoir le droit de lire
   les dépôts publics à accès contrôlé ; sans lui, le téléchargement échoue
   (`GatedRepoError`, HTTP 403).
3. Le mettre dans `.env` : `cp .env.example .env`, puis renseigner `HF_TOKEN=`.

Où vit le jeton :

- **`.env` seulement**, exclu du dépôt ; `.env.example` (suivi) n'en contient aucun.
- `compose.yml` ne le transmet qu'à l'instance `embeddinggemma`.
- Le service le journalise sous une forme masquée (quatre premiers et trois derniers
  caractères visibles) ; jamais en clair.
- Les hooks du dépôt (`pre-commit`, `pre-push`) refusent un commit ou un push qui
  contiendrait le jeton du poste, ou une chaîne d'aspect jeton.

### 1.2 Ports

| Instance | Adresse publiée | Ce qui y répond |
|---|---|---|
| `bge-m3` | `127.0.0.1:8081` | `/embed`, `/tokenize`, `/info`, `/health`, `/metrics` |
| `embeddinggemma` | `127.0.0.1:8082` | idem |

- Le port Prometheus documenté (`PROMETHEUS_PORT`, 9000 par défaut) n'est **pas
  ouvert** par la version 1.9.4 : les métriques sont sur le port de l'API (voir la
  [section 7.3](#73-constats-sur-la-version-194)).
- Dans le conteneur, le service écoute sur `0.0.0.0` (`HOSTNAME`) ; c'est la
  publication par Docker qui limite l'accès à la machine.

### 1.3 Absence d'authentification : ce qu'elle impose

Le service peut exiger une clé (`API_KEY`, en-tête `Authorization: Bearer …`), mais
**aucune n'est configurée** : selon son aide, « par défaut, le serveur répond à toute
requête ». En conséquence :

- **Publication sur `127.0.0.1` seulement**, jamais sur `0.0.0.0` ni sur une adresse
  du réseau. Un test (`test_ports_de_compose_identiques_a_la_configuration`) vérifie
  que `compose.yml` publie chaque port sur `127.0.0.1`.
- **Tout processus et tout utilisateur de la machine** peut encoder du texte, lire
  `/info` et lire `/metrics`. La machine de développement est mono-utilisateur ; sur
  une machine partagée, ce montage ne conviendrait pas.
- **Exposer le service au-delà de la machine** demanderait au moins une clé
  (`API_KEY`) et un mandataire TLS devant le service. Ce n'est pas fait.
- Seuls des textes publics y passent (voir la [section 5](#5-données-impliquées)).

## 2. Installation

### 2.1 Prérequis

| Élément | Version de référence (2026-10-07) | Vérification |
|---|---|---|
| Carte NVIDIA | RTX 3060, 6 Go, architecture Ampere 8.6 | `nvidia-smi` |
| Pilote NVIDIA | 595.84 ; la documentation du service demande un pilote compatible CUDA 12.2 ou plus | `nvidia-smi` (en-tête) |
| Docker Engine et Compose v2 | Docker 29.6.2 | `docker compose version` |
| NVIDIA Container Toolkit | 1.20.1 | `nvidia-ctk --version` ; `docker info` liste le runtime `nvidia` |
| `uv` et Python | Python 3.12 | `uv --version` |
| PostgreSQL et pgvector (pour encoder le corpus) | PostgreSQL 16, pgvector 0.6.0, migration `020` appliquée | voir la [section 2.1 du guide d'installation du dépôt](../INSTALLATION.md#21-vérifier-les-outils) |

L'image est celle de l'architecture 8.6 (étiquette `86-1.9.4`). Pour une autre carte,
choisir l'étiquette correspondante dans la
[table des cartes prises en charge de Text Embeddings Inference](https://huggingface.co/docs/text-embeddings-inference/supported_models),
et l'épingler par digest.

### 2.2 Étapes

1. **Pilote** : `nvidia-smi` doit afficher la carte et une version de CUDA ≥ 12.2.
2. **NVIDIA Container Toolkit** (droits administrateur), selon le
   [guide d'installation du NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) ;
   sur Ubuntu :

   ```bash
   curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
     | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
   curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
     | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
     | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
   sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
   sudo nvidia-ctk runtime configure --runtime=docker
   sudo systemctl restart docker
   ```

   Vérification : `docker run --rm --gpus all ubuntu nvidia-smi` affiche la carte.
3. **Jeton** : licence acceptée et `.env` rempli (voir la [section 1.1](#11-jeton-hugging-face-et-licence)).
4. **Dépendances Python du module** : `uv sync --extra dev`.
5. **Mémoire avant lancement** :

   ```bash
   uv run python -m service_embedding.controles etat
   ```

   Sort en erreur sous les seuils : **5 Go** de mémoire vive disponible, **4 Go** de
   mémoire vidéo libre. **Une instance à la fois** : la carte a 6 Go.
6. **Lancer une instance** ; au premier lancement, le service télécharge les poids à
   la révision épinglée dans le cache Hugging Face de l'hôte :

   ```bash
   docker compose --profile bge-m3 up -d
   until curl -sf 127.0.0.1:8081/health; do sleep 2; done
   curl -s 127.0.0.1:8081/info      # modèle, révision, float32, longueur maximale
   ```

7. **Arrêter** : `docker compose --profile bge-m3 down`, avant de lancer l'autre
   instance (`--profile embeddinggemma`, port 8082).

## 3. Procédure de test

| Contrôle | Commande | Résultat attendu |
|---|---|---|
| Tests du module (47) | `uv run --extra dev pytest` | tous verts ; une base PostgreSQL + pgvector **jetable** est lancée par Docker ; le test qui passe par l'instance BGE-M3 réelle saute si elle ne tourne pas |
| Mémoire avant lancement | `uv run python -m service_embedding.controles etat` | code 0 et `"seuils_non_tenus": []` |
| Contrôle d'une instance lancée | `uv run python -m service_embedding.controles instance bge-m3 --sortie mesures/controles_bge-m3.json` (avec `DATABASE_URL`) | voir le tableau suivant |
| Après l'encodage du corpus | `uv run python -m service_embedding.verification --sortie mesures/verification.json` (avec `DATABASE_URL`) | code 0, chaque ligne « OK » |

Le contrôle d'une instance **rend toujours le code 0** : c'est le fichier JSON qu'on
lit.

| Clé du JSON | Attendu (BGE-M3 / EmbeddingGemma) |
|---|---|
| `conformite` | toutes les valeurs à `true` : modèle, révision, précision, longueur maximale, longueur qui couvre le corpus |
| `vecteurs` | `dimensions` = `[1024]` / `[768]` ; `normes_conformes` = `true` (norme 1 à 1e-3 près) |
| `determinisme` | `identiques` = `true` (le même texte rend le même vecteur, au bit) |
| `au_dela_longueur_maximale` | `refuse` = `true` (HTTP 422, rien n'est tronqué en silence) |
| `metriques` | `requetes_apres` − `requetes_avant` = 5 |
| `troncature` | `fragments` = 66 290 ; `max` = 529 / 586 jetons ; `au_dela_maximale` = 0 |

`verification` contrôle : 66 290 vecteurs par table, aucun vecteur nul, de dimension
fausse ou de norme hors 1 ± 1e-3 ; aucun fragment sans vecteur ni vecteur sans
fragment ; deux encodages terminés dans `bench.encodages` ; corpus 48 090 documents /
66 290 fragments ; empreinte du jeu d'évaluation v2 `31712b0d…`.

**Équivalence avec l'implémentation de référence** (contrôle ponctuel du
2026-10-07, hors dépôt) : sentence-transformers 5.7.0 en float32 sur la carte, sur
200 fragments et 69 questions. Cosinus minimal service ↔ référence : BGE-M3 0,99989
(fragments) et 0,99997 (questions) ; EmbeddingGemma 0,99948 et 0,9999996. Voir la
[section 7.3](#73-constats-sur-la-version-194) pour l'écart propre à certaines
longueurs.

## 4. Dépendances et interconnexions

| De | Vers | Par | Ce qui passe |
|---|---|---|---|
| Client (`service_embedding.client`) | instance du service | HTTP, `127.0.0.1:8081` ou `:8082` | textes préfixés → vecteurs normalisés |
| Encodeur (`service_embedding.encodage`) | PostgreSQL, base `apimanga` | `psycopg` | lit `bench.corpus_chunks` (session en lecture seule) ; écrit `bench.vecteurs_bge_m3`, `bench.vecteurs_embeddinggemma`, `bench.encodages` |
| Module `10_mesures_recherche` | client de ce module | import Python | questions du jeu d'évaluation, rôle « requête » |
| Instance, au démarrage | Hugging Face Hub | HTTPS, sortant | téléchargement des poids à la révision épinglée vers le cache monté ; jeton pour EmbeddingGemma |
| Docker | carte graphique | NVIDIA Container Toolkit | accès du conteneur à la carte |

- **PostgreSQL et pgvector** : les tables de vecteurs sont typées `vector(1024)` et
  `vector(768)` (pgvector 0.6.0, migration `020`), sans index approximatif, sans
  suppression en cascade depuis `bench.corpus_chunks`.
- **Hugging Face au démarrage** : les poids viennent du cache de l'hôte
  (`HF_HUB_CACHE_HOTE`, par défaut `~/.cache/huggingface/hub`), monté dans le
  conteneur. Un démarrage **hors ligne** n'a pas été éprouvé.
- **Image** : `ghcr.io/huggingface/text-embeddings-inference:86-1.9.4`, épinglée par
  digest dans `compose.yml`.
- **Python** : `psycopg` ; pour les tests, `pytest`, `pytest-cov`, `pyyaml`.

## 5. Données impliquées

| Donnée | Origine | Volume | Où elle va |
|---|---|---:|---|
| Fragments du corpus RAG | `bench.corpus_chunks` : critiques Manga Sanctuary et synopsis Kitsu, textes publics, sans pseudonyme d'auteur | 66 290 fragments (48 090 documents) | envoyés à l'instance locale ; vecteurs écrits dans PostgreSQL, sur la même machine |
| Questions du jeu d'évaluation v2 | module 10 | 69 | envoyées à l'instance locale, une par requête ; vecteurs gardés en mémoire, jamais écrits |
| Vecteurs | ce module | 66 290 par modèle | `bench.vecteurs_bge_m3`, `bench.vecteurs_embeddinggemma` |
| Relevés de mémoire et de contrôle | ce module | — | `mesures/`, exclu du dépôt |

**Rien de ces données ne sort de la machine** : le client parle au service par
l'interface locale, le service à PostgreSQL local. La seule connexion sortante
attendue est le téléchargement des poids vers Hugging Face au démarrage. Le service
n'exporte de traces que si `OTLP_ENDPOINT` est défini : il ne l'est pas. L'absence de
toute autre connexion sortante n'a pas été vérifiée par une capture réseau.

## 6. Monitorage

### 6.1 Adresses

| Instance | Métriques (format Prometheus, texte) | Santé |
|---|---|---|
| `bge-m3` | `http://127.0.0.1:8081/metrics` | `http://127.0.0.1:8081/health` |
| `embeddinggemma` | `http://127.0.0.1:8082/metrics` | `http://127.0.0.1:8082/health` |

- Un compteur n'apparaît qu'après sa **première incrémentation** : juste après le
  démarrage, `/metrics` est vide.
- Les compteurs sont **remis à zéro** à chaque démarrage de l'instance.
- Aucun Prometheus ni Grafana n'est installé : les métriques se lisent à la demande
  (`curl`), ou par le client (`ClientService.metriques()`).

### 6.2 Ce que mesure chaque compteur

Relevé sur la version 1.9.4 le 2026-10-07, après une séquence connue : deux requêtes
d'encodage réussies (deux textes, puis un), une requête refusée (texte trop long), un
appel à `/tokenize`.

| Métrique | Type | Ce qu'elle mesure | Valeur après la séquence |
|---|---|---|---:|
| `te_request_count{method="batch"}` | compteur | requêtes d'encodage reçues (`/embed`), réussies ou non ; `/tokenize` n'est pas compté | 3 |
| `te_request_success{method="batch"}` | compteur | requêtes d'encodage réussies | 2 |
| `te_request_failure{err="tokenization"}` | compteur | requêtes refusées, par cause (ici : texte au-delà de la longueur maximale) | 1 |
| `te_embed_count` | compteur | textes soumis à l'encodage (une requête peut en porter plusieurs) | 4 |
| `te_embed_success` | compteur | textes encodés | 3 |
| `te_queue_size` | jauge | textes en attente dans la file du service, à l'instant de la lecture | 0 |
| `te_request_duration` | histogramme (s) | durée totale d'une requête réussie | 2 requêtes |
| `te_request_tokenization_duration` | histogramme (s) | part de la tokenisation, par requête | 2 |
| `te_request_queue_duration` | histogramme (s) | attente dans la file, par requête | 2 |
| `te_request_inference_duration` | histogramme (s) | calcul du modèle, par requête | 2 |
| `te_embed_duration`, `te_embed_tokenization_duration`, `te_embed_queue_duration`, `te_embed_inference_duration` | histogrammes (s) | les mêmes durées, par texte | 3 textes |
| `te_request_input_length` | histogramme (jetons) | longueur de chaque texte soumis, en jetons | 3 textes, 22 jetons |
| `te_batch_next_size` | histogramme | nombre de textes dans chaque lot formé par le service | — |
| `te_batch_next_tokens` | histogramme | nombre de jetons dans chaque lot formé par le service | 22 jetons au total |

Un histogramme expose `_bucket`, `_sum` et `_count` : la moyenne d'une durée est
`_sum / _count`.

### 6.3 Mémoire

- Avant chaque lancement : `uv run python -m service_embedding.controles etat`.
- Pendant un encodage, l'encodeur surveille la mémoire vive du conteneur (cgroup), la
  mémoire vidéo (`nvidia-smi`) et le swap écrit ; dix secondes de swap écrit
  d'affilée l'arrêtent proprement (code 3).

## 7. Référence

### 7.1 Configuration

`compose.yml` porte l'image (épinglée **par digest**), le GPU, le cache des poids et
le port publié. Tout le reste est dans `config/<instance>.env`, **commenté ligne à
ligne** (valeur, motif) : les variables du service sous leurs noms documentés
(`MODEL_ID`, `REVISION`, `DTYPE`, `MAX_BATCH_TOKENS`…), et les clés `CLIENT_*` que lit
le client (préfixes, taille de lot, dimension, longueurs). Une seule source pour les
deux côtés : le préfixe appliqué et la révision servie ne peuvent pas diverger.

Préfixes, appliqués par le client (`service_embedding.client`) :

| Modèle | Fragment | Question |
|---|---|---|
| BGE-M3 | aucun | aucun |
| EmbeddingGemma | `title: none \| text: ` | `task: search result \| query: ` |

### 7.2 Encoder le corpus

```bash
export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'
uv run python -m service_embedding.controles etat          # seuils avant lancement
docker compose --profile bge-m3 up -d
uv run python -m service_embedding.encodage bge-m3 --sortie mesures/encodage_bge-m3.json
docker compose --profile bge-m3 down                       # puis l'autre instance
```

Les fragments (`bench.corpus_chunks`) sont lus sur une connexion **en lecture
seule** ; les vecteurs vont dans la table du modèle, et l'encodage est inscrit dans
`bench.encodages` : modèle, révision, précision, préfixes, image et digest **tels que
l'instance les sert** (`/info`, `docker inspect`), pas tels que la configuration les
annonce. L'encodeur refuse de démarrer si les deux diffèrent.

- **Reprise** : seuls les fragments sans vecteur sont envoyés ; l'écriture est
  validée tous les 32 lots. Un encodage interrompu reprend sous la même ligne de
  `bench.encodages`.
- **Rejeu** : sur un encodage complet, rien à envoyer, **rien d'écrit**.
- **Refus** avant toute écriture : image non épinglée par digest, table déjà remplie
  par un autre encodage, corpus modifié après un encodage terminé.

### 7.3 Constats sur la version 1.9.4

- **Le port Prometheus n'est jamais ouvert.** `--prometheus-port` (9000 par défaut)
  est documenté, mais le code construit l'écouteur sans le lancer
  (`let (recorder, _) = prom_builder.build()`, `router/src/http/server.rs`). Les
  métriques ne sont servies que sur le port de l'API, à `/metrics`.
- **float32 et longueur maximale.** Sans demi-précision, pas de Flash Attention : le
  préchauffage, à `MAX_BATCH_TOKENS` jetons en séquences de la longueur maximale,
  épuise les 6 Go pour BGE-M3 à 8 192 jetons. Son instance tourne donc à
  `MAX_BATCH_TOKENS=2048`, ce qui impose au service `AUTO_TRUNCATE=true` et ramène sa
  longueur maximale à 2 048 — près de quatre fois le plus long fragment (529). Rien
  n'est tronqué en silence : le client envoie `truncate: false`, et une entrée trop
  longue est refusée (HTTP 422).
- **GeLU.** Pour un modèle déclarant `hidden_act=gelu` (BGE-M3), le service utilise
  l'approximation tanh, et le journalise. EmbeddingGemma déclare lui-même
  `gelu_pytorch_tanh`.
- **Jeton.** Le service journalise le jeton sous une forme masquée (quatre premiers et
  trois derniers caractères).
- **Écart à la référence selon la longueur, pour EmbeddingGemma** (constaté le
  2026-10-07) : les fragments de **289, 321, 353, 385 et 449 jetons** — forme 32 k + 1
  au-delà de 256 — s'écartent de sentence-transformers. Sur le corpus : 663 fragments
  (1,0 %), cosinus jusqu'à 0,99804 ; les 403 autres longueurs, vérifiées sur un
  fragment chacune, restent au-dessus de 0,99998. Ni le lot, ni la tokenisation n'en
  sont la cause ; le mécanisme n'est pas
  identifié. Effet sur les classements de recherche mesurés : nul (69 listes
  identiques en remplaçant ces vecteurs par ceux de la référence).
