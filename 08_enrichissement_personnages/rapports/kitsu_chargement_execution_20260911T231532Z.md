# Chargement Kitsu — rapport d'exécution

Statut : VALIDE

## 1. Environnement déclaré

| | |
|---|---|
| PostgreSQL | 16.15 (Ubuntu 24.04) |
| Collation base / ctype | `fr_FR.UTF-8` / `fr_FR.UTF-8` |
| `lc_time` serveur | `fr_FR.UTF-8` — **d'où le parsing des dates en Python**, jamais par `to_date` |
| `client_encoding` | UTF8 |
| Python | 3.12.3 |
| Variables `PG*` héritées | aucune |
| Migration | 015, appliquée — 16 appliquées, 0 en attente |

## 2. Périmètre et volumétrie

Raw : `03_kitsu_api_exports/.../relations/characters.ndjson`, **121 Mo**,
6 778 pages, empreinte conforme au manifeste.

| | |
|---|---:|
| **Durée** | **6,6 s** |
| **Pic mémoire** | **54 Mo** — moins de la moitié du fichier |

Le chargement est **en flux, ligne à ligne**. La seule structure qui croît avec
la source est l'ensemble des identifiants déjà vus, en O(personnages distincts)
et non en O(octets) : sans elle, un personnage lié à 12 œuvres verserait douze
fois ses formes. Le périmètre AniList étant cinq fois plus large, un chargeur
qui lirait le fichier d'un bloc passerait aujourd'hui et échouerait demain.

## 3. Chiffres de contrôle du §7 — **14 sur 14 conformes**

| # | Attendu | Mesuré | |
|---|---:|---:|---|
| 1 | 34 293 personnages | **34 293** | ✅ |
| 2 | 39 161 liens | **39 161** | ✅ |
| 5 | `role_source` à 100 % | **39 161 / 39 161**, 0 NULL | ✅ |
| 6 | `main` 9 469 | **principal 9 469** | ✅ |
| 7 | `supporting` 29 673 | **secondaire 29 673** | ✅ |
| 8 | `recurring`+`cameo` 19 | **récurrent + apparition 19** | ✅ |
| 9 | 29 108 descriptions | **29 108** | ✅ |
| 10 | 34 224 `mal_id` distincts | **34 224** | ✅ |
| 11 | alias (cf. écart motivé) | **9 868** | ✅ |
| 13 | 0 `canonical_name` avec CJK | **0** | ✅ |
| 14 | 7 sans caractère latin | **7** | ✅ |

**Formes : 105 048 au total** — 34 293 `canonical`, 60 887 `name_lang`,
9 868 `alias`. L'attendu de 9 868 ne portait que sur le troisième champ.

## 4. Écarts aux attendus

**§7.12 — formes `ja` : 26 585, attendu ~26 594. Écart de 9, favorable.**
Douze personnages portent la même forme normalisée sous deux clés de `names`
(`Easy`/`EASY`, ou une valeur romanisée rangée sous `ja_jp`). La contrainte
d'unicité n'en garde qu'une — à raison, c'est la même cible de recherche — et le
tri déterministe retient `en`. **Vérifié** : les huit formes perdues sont
toutes romanisées, sans aucun CJK. L'écart améliore la justesse du label.

Sur les 26 585 formes `ja` retenues, **26 401 portent du CJK et 184 sont
romanisées** : résidu du mauvais étiquetage de la source, conservé tel quel
plutôt que corrigé en silence.

**Règle B — zéro description classée `ja`, et c'est correct.** 74 descriptions
contiennent du CJK, aucune n'atteint le seuil de 30 % : ce sont des textes
anglais citant un nom japonais. La classe existe au schéma et reste vide pour
Kitsu. Répartition : **`en` 27 587 (94,8 %)**, **NULL 1 521 (5,2 %)**.

**Règle C.** CC BY-SA **6 092 (20,9 %)**, à instruire **1 342 (4,6 %)**,
aucune source citée **21 674 (74,5 %)**.

## 5. Six contrôles de fidélité du §9 — **tous verts**

| # | Contrôle | Résultat |
|---|---|---|
| 1 | Identifiants source : différence symétrique | **vide dans les deux sens** |
| 2 | Paires (œuvre, personnage) : différence symétrique | **vide dans les deux sens** |
| 3 | Empreinte du raw | **identique au manifeste** — `e712d139…ad88a` |
| 4 | Rejeu idempotent | **0 ligne insérée**, comptes et contenu inchangés |
| 5 | Intégrité référentielle | **0 orphelin** sur les trois tables filles |
| 6 | Sentinelle multi-sources | **0 ligne sans `source`** sur les quatre tables |

**Au-delà du §9 : le rechargement complet est reproductible.** Deux
`TRUNCATE` + rechargements successifs rendent des empreintes MD5 **identiques
au bit près** sur les quatre tables. C'est ce qui a fait apparaître une
non-détermination : le `forme_lang` retenu lors des 12 collisions dépendait de
l'ordre physique des lignes. Corrigé en ajoutant `forme_lang` au tri.

## 6. Mesures du §10

| | |
|---|---:|
| Séries MS atteintes | **1474** |
| Personnages liés à > 1 œuvre | **3 537**, maximum **12** |
| Formes ambiguës (même chaîne, plusieurs personnages) | **3 294** |
| **Graphies `ja` portées par plusieurs personnages** | **815** |
| Longueur médiane / p95 | 316 / 1 832 |
| Descriptions < 80 caractères | 4 461 (15,3 %) |

**La clé CJK reste un discriminant, pas un identifiant** : 815 graphies
japonaises désignent plusieurs personnages. Elle demeure bien meilleure que les
formes latines (3 294 ambiguës), mais une cascade bâtie sur elle seule
fusionnerait à tort. Elle doit être couplée à l'œuvre.

## 7. Ce que je n'ai pas pu établir

- **Le régime de licence des 21 674 descriptions sans provenance citée** : la
  règle C ne conclut pas, et le régime propre de Kitsu n'est pas instruit. À
  porter au registre C4 avec Wikipédia et les critiques signées.
- **La justesse du label `en` sur les 27 587 descriptions** : la règle repose
  sur des marqueurs lexicaux, non sur un modèle de langue. Elle ne distingue pas
  un texte anglais d'un texte dans une autre langue européenne sans marqueur.
- **Les 184 formes `ja` romanisées** : impossible de dire si la source s'est
  trompée de champ ou si l'œuvre n'a pas de titre japonais. Conservées telles
  quelles.
- **L'élargissement de la règle C** aux motifs `\bwiki\b` et `pedia`
  porterait la couverture de 81,9 % à 90,0 % des citations. Mesuré, proposé,
  **non appliqué** — la règle validée est celle du point C.
- **Le seuil au-delà duquel une provenance mérite d'être instruite** : 493
  provenances distinctes dont 63,9 % citées une seule fois. Arbitrage ouvert.
