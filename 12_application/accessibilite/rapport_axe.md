# Audit d'accessibilité de l'application

- **Date** : 2026-11-08.
- **Outil** : axe-core 4.14.0, dans Chromium 156.0.8078.4.
- **Niveau visé** : WCAG 2.1 AA (étiquettes : wcag2a, wcag2aa, wcag21a, wcag21aa, best-practice).
- **Pages auditées** : 80, chaque écran et chaque cas dégradé, servis contre la doublure de l'API ; politique de sécurité du contenu active, `<details>` ouverts, formulaire soumis dans le navigateur.
- **Violations par gravité** : critical 0, serious 0, moderate 0, minor 0.
- **Violations de la politique de sécurité en console** : 0.

Un outil automatique ne couvre qu'une partie des critères, de l'ordre d'un tiers : le reste est relu à la main (README, « Accessibilité »).

## Défauts restants

Aucun.

## À revoir à la main

- `color-contrast`, sur `#ancien`, `#filtre-role`, `#mode`, `#profil`, `#pseudo`, `#question`, `#role` : le chevron d'une liste déroulante ou l'icône d'un champ invalide est une image de fond : axe ne déduit pas la couleur du fond. Le couple de ces champs est fixé par `theme.css`, et son contraste calculé par un test (`test_wcag_1_4_3_…`). À relire à l'œil.

## Par page

| Page | Règles passées | Violées | À revoir | Non applicables |
|---|---:|---:|---:|---:|
| `a_propos` | 35 | 0 | 0 | 55 |
| `a_propos_sans_api` | 30 | 0 | 0 | 60 |
| `acces_refuse` | 30 | 0 | 0 | 60 |
| `admin_comptes` | 42 | 0 | 1 | 48 |
| `admin_refuse` | 42 | 0 | 1 | 48 |
| `admin_seconde_page_et_avis` | 47 | 0 | 1 | 43 |
| `api_indisponible` | 35 | 0 | 0 | 55 |
| `aucun_profil` | 35 | 0 | 0 | 55 |
| `cle_refusee` | 35 | 0 | 0 | 55 |
| `comptes_indisponibles` | 30 | 0 | 0 | 60 |
| `connexion` | 35 | 0 | 0 | 55 |
| `connexion_refusee` | 35 | 0 | 1 | 55 |
| `connexion_requise` | 30 | 0 | 0 | 60 |
| `conversation_3_tours` | 40 | 0 | 1 | 50 |
| `conversation_5_tours` | 40 | 0 | 1 | 50 |
| `conversation_close` | 44 | 0 | 1 | 46 |
| `conversation_invalide` | 41 | 0 | 1 | 49 |
| `conversation_pleine` | 41 | 0 | 1 | 49 |
| `conversation_trop_longue` | 44 | 0 | 1 | 46 |
| `critiques` | 30 | 0 | 0 | 60 |
| `degrade_api_injoignable` | 40 | 0 | 0 | 50 |
| `degrade_base_indisponible` | 41 | 0 | 1 | 49 |
| `degrade_cle_invalide` | 41 | 0 | 1 | 49 |
| `degrade_corps_hors_contrat` | 41 | 0 | 1 | 49 |
| `degrade_corps_trop_volumineux` | 41 | 0 | 1 | 49 |
| `degrade_debit_depasse` | 41 | 0 | 1 | 49 |
| `degrade_debit_sans_entete` | 41 | 0 | 1 | 49 |
| `degrade_delai` | 41 | 0 | 1 | 49 |
| `degrade_encodage_en_service_change` | 41 | 0 | 1 | 49 |
| `degrade_entree_invalide` | 41 | 0 | 1 | 49 |
| `degrade_erreur_interne` | 41 | 0 | 1 | 49 |
| `degrade_fournisseur_corps_inattendu` | 41 | 0 | 1 | 49 |
| `degrade_fournisseur_delai` | 41 | 0 | 1 | 49 |
| `degrade_fournisseur_injoignable` | 41 | 0 | 1 | 49 |
| `degrade_fournisseur_longueur` | 41 | 0 | 1 | 49 |
| `degrade_fournisseur_refus_429` | 41 | 0 | 1 | 49 |
| `degrade_fournisseur_refus_429_sans_entete` | 41 | 0 | 1 | 49 |
| `degrade_fournisseur_refus_500` | 41 | 0 | 1 | 49 |
| `degrade_historique_invalide` | 41 | 0 | 1 | 49 |
| `degrade_profil_inconnu` | 41 | 0 | 1 | 49 |
| `degrade_profil_indisponible` | 41 | 0 | 1 | 49 |
| `degrade_question_trop_longue` | 41 | 0 | 1 | 49 |
| `degrade_reglage_non_expose` | 41 | 0 | 1 | 49 |
| `degrade_reponse_hors_contrat` | 41 | 0 | 1 | 49 |
| `degrade_service_embedding_incoherent` | 41 | 0 | 1 | 49 |
| `degrade_service_embedding_indisponible` | 41 | 0 | 1 | 49 |
| `degrade_sortie_demographie` | 41 | 0 | 1 | 49 |
| `degrade_sortie_disponibilite` | 41 | 0 | 1 | 49 |
| `degrade_sortie_fragment_inconnu` | 41 | 0 | 1 | 49 |
| `degrade_sortie_pseudonyme` | 41 | 0 | 1 | 49 |
| `degrade_sortie_refus_incoherent` | 41 | 0 | 1 | 49 |
| `degrade_sortie_schema` | 41 | 0 | 1 | 49 |
| `degrade_sortie_serie_inconnue` | 41 | 0 | 1 | 49 |
| `deux_modes` | 36 | 0 | 1 | 54 |
| `erreur_profil` | 36 | 0 | 1 | 54 |
| `erreur_question_longue` | 36 | 0 | 1 | 54 |
| `erreur_question_vide` | 36 | 0 | 1 | 54 |
| `erreur_temperature` | 36 | 0 | 1 | 54 |
| `formulaire_expire` | 30 | 0 | 0 | 60 |
| `inscription` | 36 | 0 | 0 | 54 |
| `inscription_refusee` | 36 | 0 | 1 | 54 |
| `introuvable_connecte` | 30 | 0 | 0 | 60 |
| `mot_de_passe` | 35 | 0 | 0 | 55 |
| `mot_de_passe_change` | 40 | 0 | 0 | 50 |
| `mot_de_passe_initial` | 40 | 0 | 0 | 50 |
| `mot_de_passe_refuse` | 35 | 0 | 1 | 55 |
| `page_404` | 30 | 0 | 0 | 60 |
| `page_vide` | 36 | 0 | 1 | 54 |
| `refus_aucun_passage` | 37 | 0 | 1 | 53 |
| `refus_demonstration` | 39 | 0 | 1 | 51 |
| `refus_hors_catalogue` | 37 | 0 | 1 | 53 |
| `refus_inconnu` | 37 | 0 | 1 | 53 |
| `reponse` | 40 | 0 | 1 | 50 |
| `reponse_complement` | 40 | 0 | 1 | 50 |
| `reponse_demonstration` | 45 | 0 | 1 | 45 |
| `reponse_metadonnees_nulles` | 39 | 0 | 1 | 51 |
| `reponse_reformulee` | 40 | 0 | 1 | 50 |
| `reponse_refus_debit` | 40 | 0 | 1 | 50 |
| `reponse_sans_passage` | 39 | 0 | 1 | 51 |
| `reponse_titres` | 40 | 0 | 1 | 50 |
