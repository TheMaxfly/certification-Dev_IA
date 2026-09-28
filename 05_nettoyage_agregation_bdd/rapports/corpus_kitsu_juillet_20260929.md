# Corpus — la part Kitsu reconstruite depuis le snapshot de juillet

**2026-09-29.** La règle du 28 imposait le raw 2026-07 manifesté (D2). Les
critiques l'ont suivi ; les synopsis Kitsu, eux, venaient de la table de
décembre 2025. Ce n'était pas une décision, c'était une incohérence : **même
snapshot pour toutes les sources**. Elle est levée.

Rapports d'exécution : `corpus_dryrun_20260928T225726Z.md`,
`corpus_execution_20260928T230009Z.md` (chargement),
`corpus_execution_20260928T230234Z.md` (rejeu), `corpus_a_blanc_20260928T230457Z.md`,
`corpus_mesures_20260928T230731Z.md` — horodatages UTC.

## Le gabarit de décembre, retrouvé exactement

Aucun code du dépôt ne construisait les documents Kitsu de décembre. Leur
gabarit a été reconstitué et vérifié par requête : **43 085 textes sur 43 085**.

```
Titres: <canonique> | <en ?? en_us ?? en_jp> | <ja_jp>
Auteurs: <nom> (<rôle>); …
Tags: ["…", …]                    genres ∪ catégories, triés
Synopsis: <synopsis sans ses mentions de source>
```

Le « nettoyage » de décembre retirait les mentions « (Source: MU) » : la même
règle, appliquée à juillet, reproduit 42 441 des 43 034 synopsis communs ; les
autres sont de vrais changements de texte chez Kitsu.

## La règle (validée le 2026-09-29)

| | Décision |
|---|---|
| Source | `03_…/full_catalog/20260714T152202Z` : `manga.ndjson` et `relations/staff.ndjson`, sha256 vérifiés contre `manifest.json` |
| **K1** | une œuvre entre si elle a un **synopsis** et si elle est **manga, manhwa ou manhua**. Une œuvre sans synopsis a un nom, déjà indexé dans le référentiel, et pas de description : le nom va à l'index lexical, la description au corpus. Romans exclus sauf rattachement au catalogue ; doujins exclus toujours |
| **K2** | ligne `Auteurs:` alimentée par le staff de juillet (Story & Art → Scénario & Dessin, Story → Scénario, Art → Dessin) |
| **K3** | positions de tendance et `boost_score` retirés : des instantanés de décembre, et deux snapshots ne se mêlent pas |
| **K4** | la mention de source retirée du texte est gardée en `metadata_json.source_citee` |

### K1 — ce que la mesure préalable devait dire des romans et des doujins

**Zéro roman, zéro doujin rattaché au catalogue par la cascade, et ce zéro est
structurel, pas empirique** : le référentiel Kitsu de la cascade (`kitsu_meta`,
`kitsu_formes`) ne charge que manga, manhwa et manhua. Indépendamment, le
catalogue Manga Sanctuary ne connaît aucun type roman (`series_type` : Manga
14 058, Manhwa 368, Manhua 218, BD 17, Comics 6). Aucun roman ne revient.

### Ce que chaque clause écarte

| Clause | Œuvres |
|---|---:|
| œuvres du raw de juillet | 62 768 |
| écartées par le type | 21 519 — roman 15 224, oneshot 3 869, doujin 1 473, oel 953 |
| romans réadmis (rattachés au catalogue) | 0 |
| écartées faute de synopsis | 4 200 — dont 433 rattachées au catalogue |
| **retenues** | **37 049** |

## Les chiffres du 28 n'ont pas bougé

La reconstruction ne touche que la part Kitsu. Vérifié au dry-run, puis au
chargement : sélection S1→S6 **11 074 → 11 052 → 11 051 → 11 051 → 11 041** ;
**11 041** critiques et **28 108** fragments de critiques inchangés, **aucune
écriture** sur `ms_review` ; masquage **20** remplacements ; **0** fuite ; la
même coupure de fragment (`ms_review:35662`).

## Chargement et contrôles

| | Résultat |
|---|---|
| Écritures | Kitsu : 16 649 documents retirés, 26 436 modifiés, 10 613 ajoutés ; 18 027 fragments retirés, 29 050 ajoutés |
| §7.1, §7.5 | 0 écart |
| §7.2 rejeu | **0 écriture** |
| §7.3 rechargement complet (annulé) | empreintes **identiques** |
| §7.4 non-fuite | **0** ; 155 homonymes admis (27 de critiques, 128 Kitsu) ; 1 coupure |

**Homonymes Kitsu régénérés** par `corpus.homonymes`, mécaniquement, par
provenance — un texte Kitsu n'est pas écrit par un membre : 92 → **128** (+60,
−24). Les 24 disparus correspondent à des documents sortis du corpus par K1.
Des 60 nouveaux, 25 viennent de la ligne `Auteurs:` : des noms de mangakas qui
coïncident avec deux pseudonymes courts.

**Banc de décembre** : 3 qrels survivent, 68 résultats sur 325 ; 43 ont perdu
leur fragment (`chunk_id` à NULL). L'état complet reste dans l'archive
`data/archives/bench_2025-12/`.

## Le corpus

| | Documents | Fragments |
|---|---:|---:|
| `ms_review` | 11 041 | 28 108 |
| `kitsu_synopsis` | 37 049 | 38 182 |
| **total** | **48 090** | **66 290** |

Aucun document sans fragment : un synopsis est désormais exigé. Index float32 :
101,8 / 203,6 / 271,5 Mo à 384 / 768 / 1 024 dimensions. Documents Kitsu d'œuvres
hors catalogue : **30 454** (37 258 avant) — la prémisse d'E7 tient, avec ce
chiffre.

**K2, à savoir en lisant les résultats** : la ligne `Auteurs:` est présente dans
**20 831** documents Kitsu. Une question comme « les mangas de Naoki Urasawa »
devient trouvable par le texte Kitsu, et plus seulement par le catalogue : F1
mesure aussi du lexical sur métadonnée. Ce n'est pas un défaut.

**K4** : `source_citee` porte 22 509 mentions ; « MU » désigne MangaUpdates
(6 408 sous MU, M-U, MangaUpdates). Son régime de licence sur les synopsis n'est
pas instruit : ligne ouverte au registre des traitements (v1.2, §7).

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
| atteintes par un synopsis Kitsu, via la cascade | 6 595 |
| dont par les deux | 1 930 |
| **atteignables (union)** | **8 121 — 55,4 %** |
| au niveau document (sans exiger de fragment) | 8 121 |

**Rattachées à Kitsu, sans synopsis atteignable.** Séries que la cascade rattache à une œuvre Kitsu, mais qu'aucun synopsis du corpus n'atteint. La part Kitsu venant du raw de juillet, une œuvre rattachée n'y manque que faute de synopsis dans ce snapshot (règle K1) :

| | Séries |
|---|---:|
| rattachées à Kitsu par la cascade | 7 028 |
| **sans synopsis atteignable** | **433** |
| — sans document Kitsu au corpus (pas de synopsis dans le snapshot) | 433 |
| — document au corpus, sans fragment | 0 |

### Gagnées, perdues

Le plafond annoncé (+859) était exact pour les **gains** : **859 séries**
deviennent atteignables. Mais **253 séries** cessent de l'être, soit un net de
**+606** (7 515 → 8 121). Cause vérifiée pour les 253 : chacune n'était atteinte
que par un document Kitsu de décembre **sans synopsis** — titres et tags seuls —
et son œuvre n'a toujours pas de synopsis en juillet. C'est l'effet direct de
K1 : le nom reste à l'index lexical, et n'a pas de description à mettre au
corpus.

L'ensemble « avant » se recalcule depuis la vue de décembre, toujours en base :

```sql
WITH critique AS (SELECT DISTINCT series_id FROM bench.corpus_docs
                  WHERE source = 'ms_review'),
avant_kitsu AS (
  SELECT DISTINCT w.series_id::bigint AS series_id
  FROM manga.rag_export_docs e
  JOIN manga.work_identity w ON w.kitsu_id = e.kitsu_id::text
  WHERE e.source = 'kitsu_synopsis' AND w.series_id IS NOT NULL
    AND length(e.doc_text) >= 50)          -- le plancher de découpage
SELECT series_id FROM critique UNION SELECT series_id FROM avant_kitsu
```

Les **433** séries rattachées sans synopsis atteignable sont exactement les
433 œuvres rattachées que le chargeur a écartées faute de synopsis : le compte
SQL et le bilan du chargeur coïncident.

## `regle:` — les attendus de F9 et F10

`evaluation.confirmer` accepte une cinquième voie : `regle: Qnnn`, qui exécute
`v1/regles/Qnnn.sql` en lecture seule, pour une question F9 ou F10 en
proposition. La règle ne peut ni chercher des mots (toute recherche textuelle
refusée), ni lire `bench`, ni omettre son plafond (`LIMIT n` sous un `ORDER BY`
qui départage par `series_id`). La liste des colonnes admises relève de la
relecture. Une règle en erreur met sa question à revoir sans interrompre les
autres.

## Ce que je n'ai pas pu établir

- la règle exacte de décembre pour le titre anglais : 1 390 cas ne suivent
  aucune des combinaisons testées ;
- le détail des 246 synopsis réécrits chez Kitsu entre décembre et juillet ;
- le régime de licence des synopsis MangaUpdates, et des autres sources
  citées ;
- si la liste des colonnes d'une règle SQL respecte la contrainte « genre,
  année, note, tomes, statut » : non vérifiable sans analyseur SQL, laissé à la
  relecture.
