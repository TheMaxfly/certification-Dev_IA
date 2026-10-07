# 09 — service d'embedding

Encodage vectoriel du corpus RAG par un **service préexistant**,
[Text Embeddings Inference](https://huggingface.co/docs/text-embeddings-inference)
(Hugging Face), installé par son image Docker officielle et configuré selon sa
documentation. Deux modèles, deux instances, deux ports :

| Instance | Modèle | Révision | Dimension | Précision | Port (HTTP et `/metrics`) |
|---|---|---|---:|---|---:|
| `bge-m3` | `BAAI/bge-m3` | `5617a9f6…` | 1 024 | float32 | 8081 |
| `embeddinggemma` | `google/embeddinggemma-300m` | `57c266a7…` | 768 | float32 | 8082 |

Un seul chemin d'encodage : fragments et questions passent par le service, et
par le client de ce module, qui applique les préfixes.

## Lancer une instance

Prérequis : Docker, le NVIDIA Container Toolkit, une carte Ampere 8.6 (l'image
est celle de cette architecture). Une instance à la fois : la carte de
développement a 6 Go.

```bash
cp .env.example .env        # puis y mettre HF_TOKEN (EmbeddingGemma est à accès contrôlé)
uv sync --extra dev
uv run python -m service_embedding.controles etat      # mémoire avant lancement
docker compose --profile bge-m3 up -d
curl -s 127.0.0.1:8081/info
docker compose --profile bge-m3 down
```

`etat` sort en erreur sous les seuils de lancement : **5 Go** de mémoire vive
disponible, **4 Go** de mémoire vidéo libre.

## Configuration

`compose.yml` porte l'image (épinglée **par digest**), le GPU, le cache des poids
et le port publié (sur `127.0.0.1` seulement : le service n'a pas
d'authentification). Tout le reste est dans `config/<instance>.env`, **commenté
ligne à ligne** (valeur, motif) : les variables du service sous leurs noms
documentés (`MODEL_ID`, `REVISION`, `DTYPE`, `MAX_BATCH_TOKENS`…), et les clés
`CLIENT_*` que lit le client (préfixes, taille de lot, dimension, longueurs).
Une seule source pour les deux côtés : le préfixe appliqué et la révision servie
ne peuvent pas diverger. Le jeton est dans `.env`, exclu du dépôt, et n'est
transmis qu'à l'instance EmbeddingGemma.

Préfixes, appliqués par le client (`service_embedding.client`) :

| | fragment | question |
|---|---|---|
| BGE-M3 | aucun | aucun |
| EmbeddingGemma | `title: none \| text: ` | `task: search result \| query: ` |

## Contrôles

```bash
export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'
uv run python -m service_embedding.controles instance bge-m3 --sortie mesures/controles_bge-m3.json
```

Sur l'instance lancée : ce que dit `/info` (modèle, révision, précision,
longueur maximale) confronté à la configuration ; dimension et norme des
vecteurs ; le même texte encodé deux fois ; une entrée trop longue, qui doit être
refusée ; le compteur `te_request_count` avant et après ; la longueur de chacun
des 66 290 fragments selon le tokenizer **du service**, préfixe compris ; la
mémoire de l'instance au repos. Le corpus est lu en lecture seule.

## Encoder le corpus

```bash
export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'
uv run python -m service_embedding.controles etat          # seuils avant lancement
docker compose --profile bge-m3 up -d
uv run python -m service_embedding.encodage bge-m3 --sortie mesures/encodage_bge-m3.json
docker compose --profile bge-m3 down                       # puis l'autre instance
```

Les fragments (`bench.corpus_chunks`) sont lus sur une connexion **en lecture
seule** ; les vecteurs vont dans la table du modèle (`bench.vecteurs_bge_m3`,
`bench.vecteurs_embeddinggemma`, migration `020`), et l'encodage est inscrit
dans `bench.encodages` : modèle, révision, précision, préfixes, image et digest
**tels que l'instance les sert** (`/info`, `docker inspect`), pas tels que la
configuration les annonce. L'encodeur refuse de démarrer si les deux diffèrent.

- **Reprise** : seuls les fragments sans vecteur sont envoyés ; l'écriture est
  validée tous les 32 lots. Un encodage interrompu reprend sous la même ligne de
  `bench.encodages`.
- **Rejeu** : sur un encodage complet, rien à envoyer, **rien d'écrit** — ni
  vecteur, ni mise à jour de `bench.encodages`.
- **Refus** avant toute écriture : image non épinglée par digest, table déjà
  remplie par un autre encodage, corpus modifié après un encodage terminé.
- **Surveillance** : mémoire vive (`anon` du cgroup) et vidéo de l'instance, swap
  écrit (`pswpout`, la colonne `so` de `vmstat`). Dix secondes de swap écrit
  d'affilée arrêtent l'encodage proprement (code 3) : on réduit `--lot`, on
  relance, la reprise fait le reste.

## Ce que la documentation ne dit pas, constaté sur 1.9.4

- **Le port Prometheus n'est jamais ouvert.** `--prometheus-port` (défaut 9000)
  est documenté, mais le code construit l'écouteur sans le lancer
  (`let (recorder, _) = prom_builder.build()`, `router/src/http/server.rs`). Les
  métriques ne sont servies que sur le port HTTP, à `/metrics`, et un compteur
  n'y apparaît qu'après sa première incrémentation.
- **float32 et longueur maximale.** Sans demi-précision, pas de Flash
  Attention : le préchauffage, à `MAX_BATCH_TOKENS` jetons en séquences de la
  longueur maximale, épuise les 6 Go pour BGE-M3 à 8 192 jetons. Son instance
  tourne donc à `MAX_BATCH_TOKENS=2048`, ce qui impose au service
  `AUTO_TRUNCATE=true` et ramène sa longueur maximale à 2 048 — quatre fois le
  plus long fragment (529). Rien n'est tronqué en silence : le client envoie
  `truncate: false`, et une entrée trop longue est refusée (HTTP 422).
- **GeLU.** Pour un modèle déclarant `hidden_act=gelu` (BGE-M3), le service
  utilise l'approximation tanh, et le journalise : écart « négligeable » selon
  l'éditeur, non nul face à sentence-transformers. EmbeddingGemma déclare
  lui-même `gelu_pytorch_tanh`.
- **Jeton.** Le service journalise le jeton sous une forme masquée qui en laisse
  voir les quatre premiers et les trois derniers caractères.

## Tests

```bash
uv run --extra dev pytest
```

Configuration (format, règles, accord avec `compose.yml`, aucun jeton dans les
fichiers versionnables) et client (préfixes, `normalize`, `truncate: false`,
refus d'une dimension inattendue), contre une doublure HTTP. L'encodeur, sur une
base PostgreSQL + pgvector **jetable** migrée par le runner du dépôt : écriture,
reprise après panne, rejeu qui n'écrit rien (vérifié par le `xmin` des lignes),
préfixes, refus. Un dernier test passe par l'instance BGE-M3 **réelle** quand
elle tourne (il saute sinon). Garde-fous de l'encodeur vérifiés par mutation.
