# Audit d'accessibilité de l'application

- **Date** : 2026-11-04.
- **Outil** : axe-core 4.14.0, dans Chromium 156.0.8078.4.
- **Niveau visé** : WCAG 2.1 AA (étiquettes : wcag2a, wcag2aa, wcag21a, wcag21aa, best-practice).
- **Pages auditées** : 61, chaque écran et chaque cas dégradé, servis contre la doublure de l'API ; politique de sécurité du contenu active, `<details>` ouverts, formulaire soumis dans le navigateur.
- **Violations par gravité** : critical 0, serious 0, moderate 0, minor 0.
- **Violations de la politique de sécurité en console** : 0.

Un outil automatique ne couvre qu'une partie des critères, de l'ordre d'un tiers : le reste est relu à la main (README, « Accessibilité »).

## Défauts restants

Aucun.

## À revoir à la main

- `color-contrast`, sur `#mode`, `#profil`, `#question` : le chevron d'une liste déroulante ou l'icône d'un champ invalide est une image de fond : axe ne déduit pas la couleur du fond. Le couple de ces champs est fixé par `theme.css`, et son contraste calculé par un test (`test_wcag_1_4_3_…`). À relire à l'œil.

## Par page

| Page | Règles passées | Violées | À revoir | Non applicables |
|---|---:|---:|---:|---:|
| `a_propos` | 28 | 0 | 0 | 62 |
| `a_propos_sans_api` | 23 | 0 | 0 | 67 |
| `api_indisponible` | 33 | 0 | 0 | 57 |
| `aucun_profil` | 33 | 0 | 0 | 57 |
| `cle_refusee` | 33 | 0 | 0 | 57 |
| `conversation_3_tours` | 40 | 0 | 1 | 50 |
| `conversation_5_tours` | 40 | 0 | 1 | 50 |
| `conversation_close` | 44 | 0 | 1 | 46 |
| `conversation_invalide` | 39 | 0 | 1 | 51 |
| `conversation_pleine` | 39 | 0 | 1 | 51 |
| `conversation_trop_longue` | 44 | 0 | 1 | 46 |
| `degrade_api_injoignable` | 38 | 0 | 0 | 52 |
| `degrade_base_indisponible` | 39 | 0 | 1 | 51 |
| `degrade_cle_invalide` | 39 | 0 | 1 | 51 |
| `degrade_corps_hors_contrat` | 39 | 0 | 1 | 51 |
| `degrade_corps_trop_volumineux` | 39 | 0 | 1 | 51 |
| `degrade_debit_depasse` | 39 | 0 | 1 | 51 |
| `degrade_debit_sans_entete` | 39 | 0 | 1 | 51 |
| `degrade_delai` | 39 | 0 | 1 | 51 |
| `degrade_encodage_en_service_change` | 39 | 0 | 1 | 51 |
| `degrade_entree_invalide` | 39 | 0 | 1 | 51 |
| `degrade_erreur_interne` | 39 | 0 | 1 | 51 |
| `degrade_fournisseur_corps_inattendu` | 39 | 0 | 1 | 51 |
| `degrade_fournisseur_delai` | 39 | 0 | 1 | 51 |
| `degrade_fournisseur_injoignable` | 39 | 0 | 1 | 51 |
| `degrade_fournisseur_longueur` | 39 | 0 | 1 | 51 |
| `degrade_fournisseur_refus_429` | 39 | 0 | 1 | 51 |
| `degrade_fournisseur_refus_429_sans_entete` | 39 | 0 | 1 | 51 |
| `degrade_fournisseur_refus_500` | 39 | 0 | 1 | 51 |
| `degrade_historique_invalide` | 39 | 0 | 1 | 51 |
| `degrade_profil_inconnu` | 39 | 0 | 1 | 51 |
| `degrade_profil_indisponible` | 39 | 0 | 1 | 51 |
| `degrade_question_trop_longue` | 39 | 0 | 1 | 51 |
| `degrade_reglage_non_expose` | 39 | 0 | 1 | 51 |
| `degrade_reponse_hors_contrat` | 39 | 0 | 1 | 51 |
| `degrade_service_embedding_incoherent` | 39 | 0 | 1 | 51 |
| `degrade_service_embedding_indisponible` | 39 | 0 | 1 | 51 |
| `degrade_sortie_demographie` | 39 | 0 | 1 | 51 |
| `degrade_sortie_disponibilite` | 39 | 0 | 1 | 51 |
| `degrade_sortie_fragment_inconnu` | 39 | 0 | 1 | 51 |
| `degrade_sortie_pseudonyme` | 39 | 0 | 1 | 51 |
| `degrade_sortie_refus_incoherent` | 39 | 0 | 1 | 51 |
| `degrade_sortie_schema` | 39 | 0 | 1 | 51 |
| `degrade_sortie_serie_inconnue` | 39 | 0 | 1 | 51 |
| `deux_modes` | 34 | 0 | 1 | 56 |
| `erreur_profil` | 34 | 0 | 1 | 56 |
| `erreur_question_longue` | 34 | 0 | 1 | 56 |
| `erreur_question_vide` | 34 | 0 | 1 | 56 |
| `erreur_temperature` | 34 | 0 | 1 | 56 |
| `page_404` | 23 | 0 | 0 | 67 |
| `page_vide` | 34 | 0 | 1 | 56 |
| `refus_aucun_passage` | 36 | 0 | 1 | 54 |
| `refus_hors_catalogue` | 37 | 0 | 1 | 53 |
| `refus_inconnu` | 37 | 0 | 1 | 53 |
| `reponse` | 40 | 0 | 1 | 50 |
| `reponse_complement` | 40 | 0 | 1 | 50 |
| `reponse_metadonnees_nulles` | 39 | 0 | 1 | 51 |
| `reponse_reformulee` | 40 | 0 | 1 | 50 |
| `reponse_refus_debit` | 40 | 0 | 1 | 50 |
| `reponse_sans_passage` | 39 | 0 | 1 | 51 |
| `reponse_titres` | 40 | 0 | 1 | 50 |
