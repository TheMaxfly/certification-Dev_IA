# Fiches d'incident

Une fiche par incident de l'application déployée : ce qui s'est passé, comment on l'a vu, la cause, le
correctif identifié par ses commits, la preuve avant et après, et ce qui reste à faire. Chaque fiche
suit le même plan — résumé, contexte et nature, chronologie, impact, détection, cause, correctif, preuve,
prévention, limites — et chaque chiffre y porte sa source. Heures en UTC.

| Fiche | Nature | Date | Correctif | État |
|---|---|---|---|---|
| [La panne de la base n'était pas détectée sans visiteur](2026-11-13_panne_de_base_non_detectee.md) | défaut réel, révélé par une panne **provoquée** en préproduction | 13 novembre 2026 | la sonde des dépendances, application 0.9.2 (`0c0fe11`) | corrigé, livré (`e5-incident`), rejoué en préproduction et en production le 17 novembre 2026 |
| [La santé jugée rouge à tort après une livraison](2026-11-11_sante_rouge_a_tort.md) | incident **réel, non provoqué** | 11 et 12 novembre 2026 | le relevé postérieur au lever, outil de livraison 0.1.2 (`cb64759`) | corrigé ; quatre livraisons vertes depuis |

Les mesures détaillées et les preuves (relevés, journaux, captures) sont conservées hors du dépôt ; les
fiches citent le rapport et les dossiers de preuves par leur nom, et les journaux de livraison versionnés
par leur chemin (`15_livraison/journal/`).
