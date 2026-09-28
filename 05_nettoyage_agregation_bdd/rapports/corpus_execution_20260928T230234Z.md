# Reconstruction du corpus — chargement

Horodatage : `20260928T230234Z` · durée 142.7 s · **contrôles verts — VALIDÉ**

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
| kitsu_synopsis | 37 049 |

- Masquage (D5) : 20 entrées sur 19 documents, 20 remplacements par `[membre]`
- Homonymes admis, tous retrouvés : 155
- Fragments : 66 290 ; plus long : 1200 caractères — aucune troncature
- **Sans fragment** (plancher de décembre, 50 car.) : {} — longueurs None. Introuvables au retrieval.

## Part Kitsu — raw de juillet (règle du 2026-09-29)

- `manga.ndjson` sha256 `9cfc3952827faa25c792d2d4bf97a8c5ade4db07a0ea044146474c4701113056` (conforme au manifeste)
- `relations/staff.ndjson` sha256 `53499f88f5f9ffab5f90784a9c064f24db79a1cc57a561be6cec1d4cd64da7c8` (conforme au manifeste)

| Clause | Œuvres |
|---|---:|
| œuvres du raw | 62 768 |
| écartées par le type (hors manga/manhwa/manhua) | 21 519 |
| — dont novel | 15 224 |
| — dont oneshot | 3 869 |
| — dont doujin | 1 473 |
| — dont oel | 953 |
| romans réadmis (rattachés au catalogue par la cascade) | 0 |
| écartées faute de synopsis | 4 200 |
| **retenues** | **37 049** |

Œuvres rattachées au catalogue par la cascade, par type : manga 6 887, manhwa 83, manhua 58. Le référentiel Kitsu de la cascade ne charge que manga, manhwa et manhua : un roman rattaché est impossible par construction, et le catalogue ne connaît aucun type roman.

- Ligne `Auteurs:` (K2) : 20 831 documents sur 37 049. Conséquence : une question du type « les mangas de Naoki Urasawa » devient trouvable par le texte Kitsu — F1 mesure aussi du lexical sur métadonnée.
- Positions de tendance et `boost_score` : retirés (K3).
- `source_citee` (K4) : 22 509 mentions ; les plus fréquentes : MU 5 550, ANN 732, MangaHelpers 667, M-U 521, Tapas 429, MangaDex 407, MangaUpdates 337, Tokyopop 301

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
| docs:kitsu_synopsis | 37049 | 37049 |
| docs:ms_review | 11041 | 11041 |
| fragments:kitsu | 38182 | 38182 |
| fragments:ms_review | 28108 | 28108 |
| qrels | 3 | 3 |
| retrieval_results | 68 | 68 |
| retrieval_results sans fragment | 43 | 43 |

## Contrôles du §7

| Contrôle | Valeur |
|---|---:|
| 7.1 manquants | 0 |
| 7.1 en trop | 0 |
| 7.4 fuites | 0 |
| 7.4 homonymes admis rencontrés | 155 |
| 7.4 coupures de fragment | 1 |
| 7.5 type hors règle | 0 |
| 7.5 ms_review mal rattachés | 0 |
| 7.5 kitsu mal rattachés | 0 |

Fuites et coupures, par `doc_key` et empreinte du pseudonyme :

- coupure de fragment : `ms_review:35662` · `0b291e34ec53bc5b`

## Empreintes de contenu (sans `chunk_id`)

| | Documents | Fragments |
|---|---|---|
| avant | `3a34c78565230d13a13366336a863c01` | `236f56f297049091fd9627d2699494c3` |
| après | `3a34c78565230d13a13366336a863c01` | `236f56f297049091fd9627d2699494c3` |
