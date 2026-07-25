# Rapport qualité lakehouse — 20260725T013334Z

> Couche **complémentaire consultative** (module 07 v2). Hors chemin critique : historise et alerte, ne bloque jamais. Une ⚠️ peut motiver un **NO-GO humain** au cycle mensuel.

- Version des jobs : `0.2.0`
- Seuils d'alerte (paramètres) : |Δ%| volumétrie ≥ **20.0 %** ; déficit par préfixe (`disparues`) ≥ **5**.

## Volumétrie par snapshot

| snapshot | grain | n | n préc. | Δ abs | Δ % |
| --- | --- | --- | --- | --- | --- |
| 2025-12 | critiques | 6749 |  |  |  |
| 2026-07 | critiques | 11052 | 6749 | 4303 | 63.76 |
| 2025-12 | series | 13211 |  |  |  |
| 2026-07 | series | 14652 | 13211 | 1441 | 10.91 |
| 2025-12 | volumes | 89188 |  |  |  |
| 2026-07 | volumes | 103811 | 89188 | 14623 | 16.4 |

- ⚠️ **ANOMALIE volumétrie** : critiques 6749 → 11052 (**+63.76 %**) au snapshot 2026-07.
  - Lecture : la croissance des **critiques** est la « croissance impossible » qui avait révélé la référence 2025-12 tronquée.

## Complétude par préfixe — déficits localisés

Le Δ **net** ment : un préfixe peut perdre des séries pendant que le total monte. Le détecteur est `disparues` (séries présentes en N-1, absentes en N).

| snapshot | préfixe | n séries | Δ net | disparues | nouvelles |
| --- | --- | --- | --- | --- | --- |
| 2026-07 | `di` | 46 | -1 | 9 | 9 |

- ⚠️ **ANOMALIE préfixe** : `di` perd **9** séries (Δ net seulement -1, masqué par 9 nouvelles) — le **trou de crawl « Di »**, détecté par système.

## Remplissage des champs (% non vide)

| snapshot | champ | grain | total | non vide | % | Δ points |
| --- | --- | --- | --- | --- | --- | --- |
| 2025-12 | review_body | critiques | 6749 | 3187 | 47.22 |  |
| 2026-07 | review_body | critiques | 11052 | 11051 | 99.99 | 52.77 |
| 2025-12 | series_genres | series | 13211 | 0 | 0.0 |  |
| 2026-07 | series_genres | series | 14652 | 12652 | 86.35 | 86.35 |
| 2025-12 | series_other_titles | series | 13211 | 9456 | 71.58 |  |
| 2026-07 | series_other_titles | series | 14652 | 10601 | 72.35 | 0.77 |
| 2025-12 | series_tags | series | 13211 | 0 | 0.0 |  |
| 2026-07 | series_tags | series | 14652 | 2270 | 15.49 | 15.49 |
| 2025-12 | volume_ean | volumes | 89188 | 0 | 0.0 |  |
| 2026-07 | volume_ean | volumes | 103811 | 64259 | 61.9 | 61.9 |

- ⚠️ **remplissage** : `review_body` bondit de **+52.77 points** (à 99.99 %) — le bug de sélecteur des critiques, quantifié d'un coup d'œil.

## Recouvrement entre snapshots

| clé | N-1 → N | n préc. | n cour. | communes | nouvelles | disparues |
| --- | --- | --- | --- | --- | --- | --- |
| review_url | 2025-12 → 2026-07 | 6749 | 11052 | 6727 | 4325 | 22 |
| series_id | 2025-12 → 2026-07 | 13211 | 14652 | 13192 | 1460 | 19 |
| volume_url | 2025-12 → 2026-07 | 89188 | 103811 | 88886 | 14925 | 302 |

- **302 volumes** et **22 critiques** non revus d'un mois à l'autre — listables, métrique de routine (la volumétrie de référence par snapshot est ce qui confronte les comptes en base : les 59 volumes que l'ELT historique perdait).

## Qualité EAN

| snapshot | volumes | renseignés | 13 chiffres | clé valide | uniques |
| --- | --- | --- | --- | --- | --- |
| 2025-12 | 89188 | 0 | 0 | 0 | 0 |
| 2026-07 | 103811 | 64259 | 63703 | 63627 | 63511 |

## Évolution de schéma (cas réel)

- `volume_ean` **n'existe que dans le snapshot 2026-07** ; l'ingestion des deux snapshots MS dans la même table bronze est passée par `mergeSchema` — la colonne apparue entre deux mois, gérée sans casse. Trace : `volume_ean` est à 0 % de remplissage en 2025-12 (colonne absente) puis renseignée en 2026-07.

---

_Rapport lu par la checklist du cycle mensuel (boucle C1 : extraction depuis le système big data). Généré à partir des tables gold._
