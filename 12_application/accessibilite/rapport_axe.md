# Audit d'accessibilité de l'application

- **Date** : 2026-11-09.
- **Outil** : axe-core 4.14.0, dans Chromium 156.0.8078.4.
- **Niveau visé** : WCAG 2.1 AA (étiquettes : wcag2a, wcag2aa, wcag21a, wcag21aa, best-practice).
- **Pages auditées** : 137, chaque écran et chaque cas dégradé, servis contre la doublure de l'API ; politique de sécurité du contenu active, `<details>` ouverts, formulaire soumis dans le navigateur.
- **Violations par gravité** : critical 0, serious 0, moderate 0, minor 0.
- **Violations de la politique de sécurité en console** : 0.

Un outil automatique ne couvre qu'une partie des critères, de l'ordre d'un tiers : le reste est relu à la main (README, « Accessibilité »).

## Défauts restants

Aucun.

## À revoir à la main

- `color-contrast`, sur `#ancien`, `#filtre-libraire`, `#filtre-role`, `#filtre-statut`, `#label`, `#mode`, `#mot_de_passe`, `#note`, `#profil`, `#pseudo`, `#q`, `#question`, `#role`, `#texte`, `#volume_url`, `.service-api > td:nth-child(2) > span`, `.service-base > td:nth-child(2) > span`, `.service-catalogue > td:nth-child(2) > span` : le chevron d'une liste déroulante ou l'icône d'un champ invalide est une image de fond : axe ne déduit pas la couleur du fond. Le couple de ces champs est fixé par `theme.css`, et son contraste calculé par un test (`test_wcag_1_4_3_…`). À relire à l'œil.

## Par page

| Page | Règles passées | Violées | À revoir | Non applicables |
|---|---:|---:|---:|---:|
| `a_propos` | 35 | 0 | 0 | 55 |
| `a_propos_sans_api` | 30 | 0 | 0 | 60 |
| `acces_refuse` | 30 | 0 | 0 | 60 |
| `accueil_apres_suppression` | 39 | 0 | 0 | 51 |
| `admin_cles` | 35 | 0 | 0 | 55 |
| `admin_cles_route_absente` | 35 | 0 | 0 | 55 |
| `admin_cles_testees` | 40 | 0 | 0 | 50 |
| `admin_comptes` | 42 | 0 | 1 | 48 |
| `admin_couts` | 36 | 0 | 0 | 54 |
| `admin_couts_plafond_atteint` | 40 | 0 | 0 | 50 |
| `admin_critique_retirer` | 32 | 0 | 0 | 58 |
| `admin_critiques` | 40 | 0 | 1 | 50 |
| `admin_critiques_vide` | 34 | 0 | 1 | 56 |
| `admin_refuse` | 42 | 0 | 1 | 48 |
| `admin_reglages` | 34 | 0 | 0 | 56 |
| `admin_reglages_api_absente` | 34 | 0 | 0 | 56 |
| `admin_reglages_indisponible` | 38 | 0 | 0 | 52 |
| `admin_seconde_page_et_avis` | 47 | 0 | 1 | 43 |
| `admin_services` | 37 | 0 | 1 | 53 |
| `admin_services_degrades` | 41 | 0 | 1 | 49 |
| `api_indisponible` | 34 | 0 | 0 | 56 |
| `aucun_profil` | 34 | 0 | 0 | 56 |
| `catalogue` | 38 | 0 | 1 | 52 |
| `catalogue_erreur_saisie` | 38 | 0 | 1 | 52 |
| `catalogue_indisponible` | 39 | 0 | 1 | 51 |
| `catalogue_resultats` | 38 | 0 | 1 | 52 |
| `catalogue_suggestions` | 38 | 0 | 1 | 52 |
| `catalogue_vide` | 38 | 0 | 1 | 52 |
| `cle_refusee` | 34 | 0 | 0 | 56 |
| `compte_supprimer` | 35 | 0 | 0 | 55 |
| `compte_supprimer_interdit` | 30 | 0 | 0 | 60 |
| `compte_supprimer_refuse` | 35 | 0 | 1 | 55 |
| `comptes_indisponibles` | 30 | 0 | 0 | 60 |
| `connexion` | 35 | 0 | 0 | 55 |
| `connexion_refusee` | 35 | 0 | 1 | 55 |
| `connexion_requise` | 30 | 0 | 0 | 60 |
| `conseiller_admin` | 36 | 0 | 1 | 54 |
| `conseiller_budget_atteint` | 39 | 0 | 0 | 51 |
| `conseiller_client` | 34 | 0 | 0 | 56 |
| `conseiller_debit_atteint` | 39 | 0 | 0 | 51 |
| `conseiller_libraire` | 36 | 0 | 1 | 54 |
| `conseiller_prerempli` | 34 | 0 | 0 | 56 |
| `conseiller_public` | 34 | 0 | 0 | 56 |
| `conseiller_quota_atteint` | 39 | 0 | 0 | 51 |
| `conseiller_quota_client_atteint` | 39 | 0 | 0 | 51 |
| `conversation_3_tours` | 39 | 0 | 0 | 51 |
| `conversation_5_tours` | 39 | 0 | 0 | 51 |
| `conversation_close` | 43 | 0 | 0 | 47 |
| `conversation_invalide` | 39 | 0 | 0 | 51 |
| `conversation_pleine` | 39 | 0 | 0 | 51 |
| `conversation_trop_longue` | 43 | 0 | 0 | 47 |
| `critique_acces_refuse` | 30 | 0 | 0 | 60 |
| `critique_modifier` | 35 | 0 | 1 | 55 |
| `critique_nouvelle` | 35 | 0 | 1 | 55 |
| `critique_nouvelle_refusee` | 35 | 0 | 1 | 55 |
| `critique_retirer` | 32 | 0 | 0 | 58 |
| `critiques_catalogue_indisponible` | 40 | 0 | 1 | 50 |
| `critiques_liste` | 40 | 0 | 1 | 50 |
| `critiques_vide` | 34 | 0 | 1 | 56 |
| `degrade_api_injoignable` | 39 | 0 | 0 | 51 |
| `degrade_base_indisponible` | 39 | 0 | 0 | 51 |
| `degrade_cle_invalide` | 39 | 0 | 0 | 51 |
| `degrade_corps_hors_contrat` | 39 | 0 | 0 | 51 |
| `degrade_corps_trop_volumineux` | 39 | 0 | 0 | 51 |
| `degrade_debit_depasse` | 39 | 0 | 0 | 51 |
| `degrade_debit_sans_entete` | 39 | 0 | 0 | 51 |
| `degrade_delai` | 39 | 0 | 0 | 51 |
| `degrade_encodage_en_service_change` | 39 | 0 | 0 | 51 |
| `degrade_entree_invalide` | 39 | 0 | 0 | 51 |
| `degrade_erreur_interne` | 39 | 0 | 0 | 51 |
| `degrade_fournisseur_corps_inattendu` | 39 | 0 | 0 | 51 |
| `degrade_fournisseur_delai` | 39 | 0 | 0 | 51 |
| `degrade_fournisseur_injoignable` | 39 | 0 | 0 | 51 |
| `degrade_fournisseur_longueur` | 39 | 0 | 0 | 51 |
| `degrade_fournisseur_refus_429` | 39 | 0 | 0 | 51 |
| `degrade_fournisseur_refus_429_sans_entete` | 39 | 0 | 0 | 51 |
| `degrade_fournisseur_refus_500` | 39 | 0 | 0 | 51 |
| `degrade_historique_invalide` | 39 | 0 | 0 | 51 |
| `degrade_profil_inconnu` | 39 | 0 | 0 | 51 |
| `degrade_profil_indisponible` | 39 | 0 | 0 | 51 |
| `degrade_question_trop_longue` | 39 | 0 | 0 | 51 |
| `degrade_reglage_non_expose` | 39 | 0 | 0 | 51 |
| `degrade_reponse_hors_contrat` | 39 | 0 | 0 | 51 |
| `degrade_service_embedding_incoherent` | 39 | 0 | 0 | 51 |
| `degrade_service_embedding_indisponible` | 39 | 0 | 0 | 51 |
| `degrade_sortie_demographie` | 39 | 0 | 0 | 51 |
| `degrade_sortie_disponibilite` | 39 | 0 | 0 | 51 |
| `degrade_sortie_fragment_inconnu` | 39 | 0 | 0 | 51 |
| `degrade_sortie_pseudonyme` | 39 | 0 | 0 | 51 |
| `degrade_sortie_refus_incoherent` | 39 | 0 | 0 | 51 |
| `degrade_sortie_schema` | 39 | 0 | 0 | 51 |
| `degrade_sortie_serie_inconnue` | 39 | 0 | 0 | 51 |
| `deux_modes` | 36 | 0 | 1 | 54 |
| `erreur_profil` | 36 | 0 | 1 | 54 |
| `erreur_question_longue` | 34 | 0 | 1 | 56 |
| `erreur_question_vide` | 34 | 0 | 1 | 56 |
| `erreur_temperature` | 36 | 0 | 1 | 54 |
| `formulaire_expire` | 30 | 0 | 0 | 60 |
| `inscription` | 36 | 0 | 0 | 54 |
| `inscription_bornee` | 41 | 0 | 0 | 49 |
| `inscription_refusee` | 36 | 0 | 1 | 54 |
| `introuvable_connecte` | 30 | 0 | 0 | 60 |
| `mes_listes_catalogue_indisponible` | 33 | 0 | 0 | 57 |
| `mes_listes_garnies` | 38 | 0 | 0 | 52 |
| `mes_listes_vides` | 31 | 0 | 0 | 59 |
| `mot_de_passe` | 35 | 0 | 0 | 55 |
| `mot_de_passe_change` | 35 | 0 | 0 | 55 |
| `mot_de_passe_initial` | 40 | 0 | 0 | 50 |
| `mot_de_passe_refuse` | 35 | 0 | 1 | 55 |
| `page_404` | 30 | 0 | 0 | 60 |
| `page_vide` | 34 | 0 | 0 | 56 |
| `refus_aucun_passage` | 36 | 0 | 0 | 54 |
| `refus_demonstration` | 39 | 0 | 1 | 51 |
| `refus_hors_catalogue` | 36 | 0 | 0 | 54 |
| `refus_inconnu` | 36 | 0 | 0 | 54 |
| `reponse` | 39 | 0 | 0 | 51 |
| `reponse_cartes_client` | 39 | 0 | 0 | 51 |
| `reponse_complement` | 40 | 0 | 1 | 50 |
| `reponse_demonstration` | 45 | 0 | 1 | 45 |
| `reponse_metadonnees_nulles` | 38 | 0 | 0 | 52 |
| `reponse_reformulee` | 39 | 0 | 0 | 51 |
| `reponse_refus_avec_pont` | 36 | 0 | 0 | 54 |
| `reponse_refus_debit` | 39 | 0 | 0 | 51 |
| `reponse_sans_carte_avec_pont` | 36 | 0 | 0 | 54 |
| `reponse_sans_passage` | 38 | 0 | 0 | 52 |
| `reponse_titres` | 39 | 0 | 0 | 51 |
| `serie` | 33 | 0 | 0 | 57 |
| `serie_avec_critiques` | 39 | 0 | 0 | 51 |
| `serie_critiques_indisponibles` | 33 | 0 | 0 | 57 |
| `serie_deux_resumes` | 35 | 0 | 0 | 55 |
| `serie_introuvable` | 30 | 0 | 0 | 60 |
| `serie_listes_client` | 33 | 0 | 0 | 57 |
| `serie_listes_client_etat` | 38 | 0 | 0 | 52 |
| `serie_listes_visiteur` | 33 | 0 | 0 | 57 |
| `serie_resume_kitsu` | 34 | 0 | 0 | 56 |
| `serie_sans_note` | 33 | 0 | 0 | 57 |
| `serie_sans_resume` | 33 | 0 | 0 | 57 |
