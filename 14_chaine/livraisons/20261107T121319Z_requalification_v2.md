# Rapport de livraison — requalification_v2 — 20261107T121319Z_requalification_v2

**Code de sortie : 0 (PROMOTION_PROPOSEE)** — toutes les étapes tenues, règle tenue ; arrêt avant la promotion.

## Ce qui a été jugé

| Objet | Valeur |
|---|---|
| Environnement | préproduction, base `apimanga_preprod` |
| Dossier des échantillons | `09_service_embedding/mesures/equivalence_preprod` |
| Candidat | `14_chaine/candidats/requalification_v2.toml` — démonstration de la chaîne : requalifier l'encodage en service, en préproduction, jusqu'à la promotion |
| Commit | `597146c2c3ad4d00e665636546455eb839fd4db2` |
| Corpus | v2 |
| Encodage | 3 |
| Mesure | n° 8 ; run `2516e8fa724d40a7a9b275a14db337c7` (E3-chaine-preprod) |
| Résultat | global 31 / 59 ; reconnaissance 21 / 23 ; proposition 10 / 36 ; nDCG@10 0,3740 |
| Règle | `10_mesures_recherche/config/promotion_corpus_v2.toml`, commit `b6c081c81e2f48467ade875c90e43baceb298926` du 2026-10-10T02:50:54+02:00 |
| Verdict | promotion proposée |
| Conditions manquantes | — |
| Image de l'API | `sha256:4047b64df00c296aeabd5bcda43b2cd89af5473c8ba727a973cf26d4e1cbf0f0` |

## Les étapes

| N° | Étape | Statut | Durée (s) | Détail |
|---|---|---|---|---|
| 0 | Préalables | tenue | 0,1 | base : apimanga_preprod ; dossier : `09_service_embedding/mesures/equivalence_preprod` ; commit : `597146c2…` ; instance : embeddinggemma ; encodage en service : 2 |
| 1 | Tester les données | tenue | 108,7 | jeu : `31712b0d…` ; reconstruction à blanc : fragments 77 006, documents (ms_review 11 041, kitsu_synopsis 37 049, ms_synopsis 8 685) |
| 2 | Construire | non lancée | — | condition non remplie : le candidat déclare une nouvelle version |
| 3 | Encoder | non lancée | — | condition non remplie : le candidat demande un nouvel encodage |
| 4 | Tester le modèle | tenue | 48,0 | échantillon : `09_service_embedding/mesures/equivalence_preprod/encodage_3.json` ; verdict : equivalent ; cosinus minimal : 0,99999940 ; fragments comparés : 200 ; sous 0,999 : 0 |
| 5 | Évaluer | tenue | 41,0 | run : `2516e8fa…` ; expérience : E3-chaine-preprod ; global (atteignables) : hit_rate@5 0,4407, hit_rate@10 0,5254, mrr@10 0,3651, ndcg@10 0,3740 ; global (toutes) : hit_rate@5 0,4407, hit_rate@10 0,5254, mrr@10 0,3651, ndcg@10 0,3740 ; rejeu contre `a3b7f0e0` : identique |
| 6 | Valider | tenue | 2,0 | promotion proposée : global 31 / 59 ; reconnaissance 21 / 23 ; proposition 10 / 36 ; nDCG@10 0,3740 |
| 7 | Packaging | tenue | 0,8 | image : api-modele:1.3.1 ; empreinte : `sha256:4047b64d…` |

## Ensuite

commiter le rapport (14_chaine/livraisons/20261107T121319Z_requalification_v2.md et .json) — docs(chaine): rapport de livraison 20261107T121319Z_requalification_v2 — et le pousser ; puis, si Max promeut : make chaine-promouvoir LIVRAISON=20261107T121319Z_requalification_v2 MOTIF="…"
