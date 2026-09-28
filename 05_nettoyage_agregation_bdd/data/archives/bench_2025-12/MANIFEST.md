# Archive — le banc de décembre 2025, avant reconstruction du corpus

**Pourquoi elle existe.** Reconstruire le corpus remplace les documents
`ms_review` et `ms_hybrid` de `bench.corpus_docs`. Les clés étrangères de `bench`
sont en `ON DELETE CASCADE` : ce remplacement efface **154 des 157 qrels** et
**875 des 1 200 `retrieval_results`** — tout ce qui visait un document retiré.
Les qrels sont de toute façon à refaire au grain entité, mais ces résultats sont
la seule trace du banc de décembre. Ils sont archivés **avant** toute écriture.

**Capture : 2026-09-28**, depuis `apimanga` (PostgreSQL 16.15), en session
`default_transaction_read_only=on`.

## Contenu

| Fichier | Versionné | Rôle |
|---|---|---|
| `bench_2025-12.dump` | **non** (34 Mo) | `pg_dump` du schéma `bench` entier, structure et données, format custom |
| `queries.csv`, `qrels.csv`, `retrieval_results.csv`, `metrics.csv`, `embedding_runs.csv`, `faiss_indexes.csv`, `chunking_strategies.csv`, `embedding_models.csv` | oui | les tables d'évaluation, lisibles sans restauration, triées par clé |
| `empreintes.sql` | oui | la requête d'empreintes de contenu |

Le dump reste hors dépôt, comme le raw des modules 04 et 05. Il n'est
confrontable qu'à ce manifeste.

```
3bb117b7cee98f5cd77a9e7f4bcc0c74cb7b3c8ec02fb6c1b0c7107aa2be673a  bench_2025-12.dump   34 117 140 octets
```

Produit par :

```bash
pg_dump "$DSN" -Fc -n bench --no-owner --no-privileges -f bench_2025-12.dump
```

## Preuve de restauration

Restauré le 2026-09-28 dans un `postgres:16-alpine` jetable
(`pg_restore --no-owner --no-privileges --exit-on-error`), puis `empreintes.sql`
joué des deux côtés avec les mêmes paramètres de session fixes : **14 empreintes
identiques**.

| Table | Lignes | Empreinte (md5) |
|---|---:|---|
| `chunking_strategies` | 1 | `e725ee02d97cb46afcb58ec5d825bd4b` |
| `corpus_chunks` — kitsu | 43 832 | `39a915c47cc467576bad1c9dcd9cd045` |
| `corpus_chunks` — ms_hybrid | 10 062 | `0a54a1d23ea49000e2aece83398061a6` |
| `corpus_chunks` — ms_review | 9 855 | `02c37481092e7a93bb552f051a3bd99d` |
| `corpus_docs` — kitsu_synopsis | 43 085 | `bcc238b038e4a82c0b439fc792b85e8e` |
| `corpus_docs` — ms_hybrid | 5 608 | `476d5f2d04a401cdddec5a7005702fc3` |
| `corpus_docs` — ms_review | 3 187 | `69e4aad363d7acee464327ec45abc4dd` |
| `embedding_models` | 2 | `69952b386501d8b59d7bc4cbd6fdceae` |
| `embedding_runs` | 2 | `4ff48918f22d20ebc7deb0691c9e044d` |
| `faiss_indexes` | 2 | `e3b1070af9635efc062b963085ed750e` |
| `metrics` | 10 | `76ee3e1a2dd9fc4d5fd7b0d7faebcd2d` |
| `qrels` | 157 | `eeabd0f21d71f363d7593cc8b571664c` |
| `queries` | 24 | `fcbc6ac73e004b05a109d4b39c9fe4d9` |
| `retrieval_results` | 1 200 | `6d70a447566d73f5cb4694dcfcf4363d` |

Un premier essai sans paramètres fixes avait divergé sur les cinq tables à
`timestamptz` : `row::text` rend l'heure dans le fuseau de la session (Paris
d'un côté, UTC dans le conteneur). Le contenu était identique ; l'empreinte ne
l'était pas. D'où l'en-tête de `empreintes.sql`.

## Lire les clés de décembre

- `ms_review:N` — `N` est `manga.rag_reviews_docs.doc_id` (3 188 à 6 374), **pas**
  `ms_reviews_all.review_id` ni l'identifiant du site. Pour retrouver la
  critique : `rag_reviews_docs.doc_id → review_url`.
- `ms_hybrid:N` — `N` est le `series_id` Manga Sanctuary.
- `kitsu:N` — `N` est le `kitsu_id`.

Les CSV portent les `timestamptz` avec leur décalage explicite (`+01`).
