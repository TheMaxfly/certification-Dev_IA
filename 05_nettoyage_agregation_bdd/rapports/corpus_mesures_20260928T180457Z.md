# Corpus reconstruit — mesures (§8) et auto-référence (A3)

Horodatage : `20260928T180457Z` · PostgreSQL 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1) · `dbname=apimanga host=localhost port=5432 user=postgres` · session en lecture seule

## Longueur des critiques retenues (corps, sans le préfixe de titre)

| Critiques | Min | Médiane | p95 | Max | < 80 car. | < 200 car. |
|---:|---:|---:|---:|---:|---:|---:|
| 11 041 | 109 | 2 033 | 4 123 | 9 667 | 0 (0,00 %) | 7 (0,06 %) |

## Fragments par type de document

| Type | Documents | Fragments | Documents sans fragment |
|---|---:|---:|---:|
| kitsu_synopsis | 43 085 | 43 832 | 150 |
| ms_review | 11 041 | 28 108 | 0 |
| **total** | 54 126 | **71 940** | 150 |

## Taille estimée de l'index — flottants 32 bits, vecteurs seuls

| Dimensions | Octets | Mo |
|---:|---:|---:|
| 384 | 110 499 840 | 110,5 |
| 768 | 220 999 680 | 221,0 |
| 1024 | 294 666 240 | 294,7 |

Hors métadonnées et hors structure d'index (HNSW, IVF…) : un plancher, pas un devis.

## A3 — marqueurs d'auto-référence (mesurés, aucun filtrage)

Sur les 11 041 critiques retenues, corps SOURCE (avant masquage).

| Marqueur | Critiques | Taux |
|---|---:|---:|
| M1 pseudonyme d'un membre (brut, homonymes compris) | 46 | 0,42 % |
| M2 âge | 1 | 0,01 % |
| M3 lieu de vie | 1 | 0,01 % |
| M4 prénom | 1 | 0,01 % |
| M5 entourage / métier | 14 | 0,13 % |
| M6a collègue | 2 | 0,02 % |
| M6b merci à | 63 | 0,57 % |
| M6c chroniqueur | 2 | 0,02 % |
| M6d signature finale | 39 | 0,35 % |
| **au moins un de M2–M6** | **122** | **1,10 %** |

M1 compte des homonymes (personnages, mangakas) : les vraies références à un membre sont celles de la liste de masquage (D5). Les motifs M2–M6 sont des marqueurs, pas des preuves : leur taux de faux positifs n'est pas mesuré ici. Les exemples, qui contiennent du texte à juger, restent hors dépôt.
