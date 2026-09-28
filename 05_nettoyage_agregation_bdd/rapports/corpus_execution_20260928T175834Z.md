# Reconstruction du corpus — chargement

Horodatage : `20260928T175834Z` · durée 150.2 s · **contrôles verts — VALIDÉ**

## Environnement

- Serveur : PostgreSQL 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
- Connexion : `dbname=apimanga host=localhost port=5432 user=postgres`
- Raw du snapshot : `04 scraping manga sanctuary/data/raw/2026-07/manga sanctuary reviews.jsonl` — sha256 `f8cd721cf7b89be7570a75f32f22b61e0027e0029194d7fdb74d10062ea49ec1` (conforme au manifeste), 11 052 URL
- Listes D5 : `database/donnees`

## Sélection — lignes restantes après chaque clause

| Clause | Restent |
|---|---:|
| S1 source | 11 074 |
| S2 snapshot | 11 052 |
| S3 corps | 11 051 |
| S5 série | 11 051 |
| S6 doublons | 11 041 |

## Cible

| Source | Documents |
|---|---:|
| ms_review | 11 041 |
| kitsu_synopsis | 43 085 |

- Masquage (D5) : 20 entrées sur 19 documents, 20 remplacements par `[membre]`
- Homonymes admis, tous retrouvés : 119
- Fragments : 71 940 ; plus long : 1200 caractères — aucune troncature
- **Sans fragment** (plancher de décembre, 50 car.) : {'kitsu_synopsis': 150} — longueurs (30, 49). Introuvables au retrieval.

## Écritures

| Opération | Par source |
|---|---|
| docs retirés | — |
| docs modifiés | — |
| docs ajoutés | — |
| fragments retirés | — |
| fragments ajoutés | — |

## État de `bench` avant / après

| | Avant | Après |
|---|---:|---:|
| docs:kitsu_synopsis | 43085 | 43085 |
| docs:ms_review | 11041 | 11041 |
| fragments:kitsu | 43832 | 43832 |
| fragments:ms_review | 28108 | 28108 |
| qrels | 3 | 3 |
| retrieval_results | 325 | 325 |
| retrieval_results sans fragment | 0 | 0 |

## Contrôles du §7

| Contrôle | Valeur |
|---|---:|
| 7.1 manquants | 0 |
| 7.1 en trop | 0 |
| 7.4 fuites | 0 |
| 7.4 homonymes admis rencontrés | 119 |
| 7.4 coupures de fragment | 1 |
| 7.5 type hors règle | 0 |
| 7.5 ms_review mal rattachés | 0 |
| 7.5 kitsu mal rattachés | 0 |

Fuites et coupures, par `doc_key` et empreinte du pseudonyme :

- coupure de fragment : `ms_review:35662` · `0b291e34ec53bc5b`

## Empreintes de contenu (sans `chunk_id`)

| | Documents | Fragments |
|---|---|---|
| avant | `815be34659fdb0607f7e0289b9e09172` | `3663444f948cabc125880d0f34a45047` |
| après | `815be34659fdb0607f7e0289b9e09172` | `3663444f948cabc125880d0f34a45047` |
