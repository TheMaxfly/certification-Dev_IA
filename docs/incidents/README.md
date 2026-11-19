# Fiches d'incident

Une fiche par incident de l'application déployée : ce qui s'est passé, comment on l'a vu, la cause, le
correctif identifié par ses commits, la preuve avant et après, et ce qui reste à faire. Chaque fiche
suit le même plan — résumé, contexte et nature, chronologie, impact, détection, cause, correctif, preuve,
prévention, limites — et chaque chiffre y porte sa source ; la sévérité est dite dans la rubrique
« Impact ». Heures en UTC.

| Fiche | Nature | Sévérité | Date | Correctif | État | Ticket |
|---|---|---|---|---|---|---|
| [La panne de la base n'était pas détectée sans visiteur](2026-11-13_panne_de_base_non_detectee.md) | défaut réel, révélé par une panne **provoquée** en préproduction | **élevée** ; impact réel nul | 13 novembre 2026 | la sonde des dépendances, application 0.9.2 (`0c0fe11`) | corrigé, livré (`e5-incident`), rejoué en préproduction et en production le 17 novembre 2026 | #58 |
| [La santé jugée rouge à tort après une livraison](2026-11-11_sante_rouge_a_tort.md) | incident **réel, non provoqué** | **faible** | 11 et 12 novembre 2026 | le relevé postérieur au lever, outil de livraison 0.1.2 (`cb64759`) | corrigé ; quatre livraisons vertes depuis | #59 |

Chaque fiche a son ticket GitHub, étiqueté `Incident` et fermé, qui porte la recherche de la cause dans
l'outil de suivi.

## Sévérité

| Niveau | Ce qu'il recouvre |
|---|---|
| critique | service entièrement coupé, avec perte ou fuite de données, ou sans retour possible sans intervention lourde |
| élevée | fonction principale indisponible pour tous, sans contournement |
| moyenne | fonction secondaire touchée, ou contournement possible |
| faible | gêne sans effet sur le service rendu |

La sévérité se juge sur l'impact potentiel, ce que le défaut pouvait couper ; l'impact réel, ce qui a
été effectivement perdu ou touché, est dit à part. Elle ne se confond pas avec l'étiquette `severite`
des règles d'alerte du module 13 (`13_monitorage/grafana/provisioning/alerting/regles.yml`, valeurs
`critique` et `attention`), qui dit l'urgence de la réaction et non l'impact constaté.

Les mesures détaillées et les preuves (relevés, journaux, captures) sont conservées hors du dépôt ; les
fiches citent le rapport et les dossiers de preuves par leur nom, et les journaux de livraison versionnés
par leur chemin (`15_livraison/journal/`).
