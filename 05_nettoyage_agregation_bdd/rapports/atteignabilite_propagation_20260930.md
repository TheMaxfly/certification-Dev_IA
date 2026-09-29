# Atteignabilité — après la propagation du kitsu_id par identifiant

**2026-09-30.** Recalcul après l'étage `kitsu_propagation` (migration 018) :
1 168 séries identifiées par la cascade reçoivent le `kitsu_id` que leurs
identifiants MAL / AniList désignaient sans ambiguïté. Le corpus `bench` n'a
pas bougé ; seule la relation de ses documents Kitsu aux séries change.

Rapports de l'étage : `propagation_kitsu_dryrun_20260929T231831Z.md` (à blanc),
`propagation_kitsu_execution_20260929T231841Z.md` (exécution),
`propagation_kitsu_execution_20260929T231905Z.md` (rejeu, aucune écriture).
Sortie ci-dessous : `uv run python -m evaluation.atteignabilite`, session en
lecture seule.

## Avant / après

| | 2026-09-29 | 2026-09-30 | Écart |
|---|---:|---:|---:|
| atteintes par une critique | 3 456 | 3 456 | 0 |
| atteintes par un synopsis Kitsu, via la cascade | 6 595 | 7 738 | +1 143 |
| dont par les deux | 1 930 | 2 476 | +546 |
| **atteignables (union)** | **8 121 — 55,4 %** | **8 718 — 59,4 %** | **+597** |
| rattachées à Kitsu par la cascade | 7 028 | 8 196 | +1 168 |
| — sans synopsis atteignable | 433 | 458 | +25 |

Les 1 168 rattachements se répartissent en 1 143 entrées Kitsu qui ont un
synopsis au corpus et 25 qui n'en ont pas dans le snapshot de juillet (règle
K1). Des 1 143, 546 séries étaient déjà atteintes par une critique : le gain
net est de **+597**, l'attendu posé le 2026-09-30 (~8 718).

## Atteignabilité

**Définition.** Une série du catalogue est atteignable si au moins un **fragment** du corpus appartient à un document rattaché à elle : une critique par son `series_id`, ou un synopsis Kitsu dont le `kitsu_id` est rattaché à la série par `manga.work_identity`. La requête exacte :

```sql
WITH par_critique AS (
  SELECT DISTINCT d.series_id
  FROM bench.corpus_docs d
  WHERE d.source = 'ms_review'
    AND EXISTS (SELECT 1 FROM bench.corpus_chunks k WHERE k.doc_key = d.doc_key)
),
par_kitsu AS (
  SELECT DISTINCT w.series_id::bigint AS series_id
  FROM bench.corpus_docs d
  JOIN manga.work_identity w ON w.kitsu_id = d.kitsu_id::text
  WHERE d.source = 'kitsu_synopsis' AND w.series_id IS NOT NULL
    AND EXISTS (SELECT 1 FROM bench.corpus_chunks k WHERE k.doc_key = d.doc_key)
)
SELECT series_id FROM par_critique
UNION
SELECT series_id FROM par_kitsu
```

| | Séries |
|---|---:|
| Catalogue (`ms_series_enriched`) | 14 670 |
| atteintes par une critique | 3 456 |
| atteintes par un synopsis Kitsu, via la cascade | 7 738 |
| dont par les deux | 2 476 |
| **atteignables (union)** | **8 718 — 59,4 %** |
| au niveau document (sans exiger de fragment) | 8 718 |

**Rattachées à Kitsu, sans synopsis atteignable.** Séries que la cascade rattache à une œuvre Kitsu, mais qu'aucun synopsis du corpus n'atteint. La part Kitsu venant du raw de juillet, une œuvre rattachée n'y manque que faute de synopsis dans ce snapshot (règle K1) :

| | Séries |
|---|---:|
| rattachées à Kitsu par la cascade | 8 196 |
| **sans synopsis atteignable** | **458** |
| — sans document Kitsu au corpus (pas de synopsis dans le snapshot) | 458 |
| — document au corpus, sans fragment | 0 |

