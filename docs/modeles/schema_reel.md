# Schema reel — inventaire extrait de la base

> Extrait de `apimanga` le 2026-07-28 par `extraire_schema.py` (lecture seule : uniquement des SELECT sur `information_schema` et `pg_catalog`).

Ce fichier est la **source** des planches de `modele_donnees.drawio` et la **preuve de fidelite** : rien n'y est saisi a la main, tout vient de la base. Une table dessinee qui ne figurerait pas ici serait une invention.

## Chiffres

| Element | Nombre |
| --- | --- |
| Tables `manga` | 30 |
| Tables `staging` | 11 |
| Vues (`manga`) | 8 |
| Colonnes (total) | 647 |
| Cles etrangeres | 18 |
| Index (dont UNIQUE) | 96 |
| Contraintes declarees | 67 |

## Confrontation base <-> migrations `000` a `011`

- tables `manga` + `staging` **declarees par les migrations** : **41**
- tables `manga` + `staging` **presentes en base** : **41**

**Aucun ecart.** Chaque table de la base est creee par une migration du depot, et chaque table declaree existe en base. Le depot sait reconstruire ce qu'il decrit.

### Origine de chaque table

| Migration | Tables creees |
| --- | --- |
| `000_baseline.sql` | `manga.kitsu_series_authors`, `manga.kitsu_series_authors_stg`, `manga.kitsu_series_core`, `manga.kitsu_series_core_stage`, `manga.kitsu_series_core_stg`, `manga.kitsu_weekly_snapshot`, `manga.kitsu_weekly_snapshot_stg`, `manga.ms_kitsu_ambiguous`, `manga.ms_kitsu_map`, `manga.ms_reviews`, `manga.ms_reviews_all`, `manga.ms_series_enriched`, `manga.ms_volumes_enriched`, `manga.rag_kitsu_docs`, `manga.rag_reviews_docs` |
| `001_socle_identite.sql` | `manga.match_decision`, `manga.volume_identity`, `manga.work_identity` |
| `002_staging_referentiels.sql` | `staging.kitsu_formes`, `staging.mi_series`, `staging.mi_sorties`, `staging.wd_auteurs`, `staging.wd_entities`, `staging.wd_formes`, `staging.wd_pivot` |
| `003_evolution_ms.sql` | `manga.ms_formes` |
| `004_staging_ms.sql` | `staging.ms_reviews`, `staging.ms_volumes` |
| `006_referentiels.sql` | `manga.kitsu_formes`, `manga.kitsu_mappings`, `manga.wd_auteurs`, `manga.wd_formes`, `manga.wd_pivot`, `staging.kitsu_mappings` |
| `007_referentiel_mi.sql` | `manga.mi_series`, `manga.mi_sorties` |
| `008_hydratation_auteurs_jawiki.sql` | `manga.wd_auteurs_formes` |
| `009_referentiel_kitsu_staff_meta.sql` | `manga.kitsu_meta`, `manga.kitsu_staff`, `staging.kitsu_staff` |
| `010_avis_llm.sql` | `manga.llm_avis` |

## Vues

| Vue | Definie sur |
| --- | --- |
| `manga.kitsu_rag_docs_v` | manga.kitsu_series_authors, manga.kitsu_series_core, manga.kitsu_weekly_snapshot |
| `manga.rag_docs_all` | manga.rag_kitsu_docs, manga.rag_reviews_docs |
| `manga.rag_docs_all_v2` | manga.rag_docs_all, manga.rag_ms_hybrid_docs |
| `manga.rag_docs_scored` | manga.rag_docs_all_v2 |
| `manga.rag_export_docs` | manga.rag_docs_scored |
| `manga.rag_ms_hybrid_docs` | manga.ms_kitsu_map, manga.rag_kitsu_docs, manga.rag_reviews_docs |
| `manga.v_match_current` | manga.match_decision |
| `manga.v_mi_ean_multiples` | manga.mi_sorties |

## Cles etrangeres (toutes)

| Source | Colonnes | Cible | Colonnes cible |
| --- | --- | --- | --- |
| `manga.kitsu_series_authors` | kitsu_id | `manga.kitsu_series_core` | kitsu_id |
| `manga.kitsu_weekly_snapshot` | kitsu_id | `manga.kitsu_series_core` | kitsu_id |
| `manga.llm_avis` | series_id | `manga.ms_series_enriched` | series_id |
| `manga.ms_formes` | series_id | `manga.ms_series_enriched` | series_id |
| `manga.ms_kitsu_ambiguous` | series_id | `manga.ms_series_enriched` | series_id |
| `manga.ms_kitsu_map` | series_id | `manga.ms_series_enriched` | series_id |
| `manga.ms_reviews` | series_id | `manga.ms_series_enriched` | series_id |
| `manga.ms_reviews` | volume_url | `manga.ms_volumes_enriched` | volume_url |
| `manga.ms_reviews_all` | series_id | `manga.ms_series_enriched` | series_id |
| `manga.ms_reviews_all` | volume_url | `manga.ms_volumes_enriched` | volume_url |
| `manga.ms_series_enriched` | work_uid | `manga.work_identity` | work_uid |
| `manga.ms_volumes_enriched` | series_id | `manga.ms_series_enriched` | series_id |
| `manga.rag_kitsu_docs` | kitsu_id | `manga.kitsu_series_core` | kitsu_id |
| `manga.rag_reviews_docs` | series_id | `manga.ms_series_enriched` | series_id |
| `manga.rag_reviews_docs` | volume_url | `manga.ms_volumes_enriched` | volume_url |
| `manga.volume_identity` | work_uid | `manga.work_identity` | work_uid |
| `manga.wd_auteurs` | qid | `manga.wd_pivot` | qid |
| `manga.wd_formes` | qid | `manga.wd_pivot` | qid |

## Detail par table

### `manga.kitsu_formes`

155003 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `forme_id` | int8 | NON |  |
| `kitsu_id` | int8 | NON |  |
| `forme` | text | NON |  |
| `forme_norm` | text | NON |  |
| `forme_type` | text | NON |  |
| `langue` | text | oui |  |
| `subtype` | text | NON |  |
| `loaded_at` | timestamptz | NON | now() |

- **PK** : `CREATE UNIQUE INDEX kitsu_formes_pkey ON manga.kitsu_formes USING btree (forme_id)`
- **UNIQUE** : `CREATE UNIQUE INDEX kitsu_formes_kitsu_id_forme_norm_key ON manga.kitsu_formes USING btree (kitsu_id, forme_norm)`
- **CHECK** `kitsu_formes_forme_type_check` : `CHECK ((forme_type = ANY (ARRAY['canonical'::text, 'title'::text, 'abbreviated'::text])))`
- **CHECK** `kitsu_formes_subtype_check` : `CHECK ((subtype = ANY (ARRAY['manga'::text, 'manhwa'::text, 'manhua'::text])))`

### `manga.kitsu_mappings`

74866 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `kitsu_id` | int8 | NON |  |
| `external_site` | text | NON |  |
| `external_id` | text | NON |  |
| `loaded_at` | timestamptz | NON | now() |

- **UNIQUE** : `CREATE UNIQUE INDEX kitsu_mappings_kitsu_id_external_site_external_id_key ON manga.kitsu_mappings USING btree (kitsu_id, external_site, external_id)`

### `manga.kitsu_meta`

41249 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `kitsu_id` | int8 | NON |  |
| `annee` | int4 | oui |  |
| `subtype` | text | NON |  |
| `loaded_at` | timestamptz | NON | now() |

- **PK** : `CREATE UNIQUE INDEX kitsu_meta_pkey ON manga.kitsu_meta USING btree (kitsu_id)`
- **CHECK** `kitsu_meta_subtype_check` : `CHECK ((subtype = ANY (ARRAY['manga'::text, 'manhwa'::text, 'manhua'::text])))`

### `manga.kitsu_series_authors`

172 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `kitsu_id` | int8 | NON |  |
| `author_name` | text | NON |  |
| `author_role` | text | NON |  |

- **PK** : `CREATE UNIQUE INDEX kitsu_series_authors_pkey ON manga.kitsu_series_authors USING btree (kitsu_id, author_name, author_role)`
- **FK** : (kitsu_id) -> `manga.kitsu_series_core`(kitsu_id)

### `manga.kitsu_series_authors_stg`

172 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `kitsu_id` | text | oui |  |
| `author_name` | text | oui |  |
| `author_role` | text | oui |  |


### `manga.kitsu_series_core`

43085 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `kitsu_id` | int8 | NON |  |
| `slug` | text | oui |  |
| `status` | text | oui |  |
| `title_canonical` | text | oui |  |
| `title_en` | text | oui |  |
| `title_ja` | text | oui |  |
| `title_norm_primary` | text | oui |  |
| `title_norm_canonical` | text | oui |  |
| `title_norm_en` | text | oui |  |
| `title_norm_ja` | text | oui |  |
| `synopsis_clean` | text | oui |  |
| `rating_average_10` | float8 | oui |  |
| `rating_rank` | int8 | oui |  |
| `popularity_rank` | int8 | oui |  |
| `categories_json` | jsonb | oui |  |
| `genres_json` | jsonb | oui |  |
| `tags_all_json` | jsonb | oui |  |

- **PK** : `CREATE UNIQUE INDEX kitsu_series_core_pkey ON manga.kitsu_series_core USING btree (kitsu_id)`

### `manga.kitsu_series_core_stage`

43085 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `kitsu_id` | text | oui |  |
| `slug` | text | oui |  |
| `status` | text | oui |  |
| `title_canonical` | text | oui |  |
| `title_en` | text | oui |  |
| `title_ja` | text | oui |  |
| `title_norm_primary` | text | oui |  |
| `title_norm_canonical` | text | oui |  |
| `title_norm_en` | text | oui |  |
| `title_norm_ja` | text | oui |  |
| `synopsis_clean` | text | oui |  |
| `rating_average_10` | text | oui |  |
| `rating_rank` | text | oui |  |
| `popularity_rank` | text | oui |  |
| `categories_json` | text | oui |  |
| `genres_json` | text | oui |  |
| `tags_all_json` | text | oui |  |


### `manga.kitsu_series_core_stg`

43085 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `kitsu_id` | text | oui |  |
| `slug` | text | oui |  |
| `status` | text | oui |  |
| `title_canonical` | text | oui |  |
| `title_en` | text | oui |  |
| `title_ja` | text | oui |  |
| `title_norm_primary` | text | oui |  |
| `title_norm_canonical` | text | oui |  |
| `title_norm_en` | text | oui |  |
| `title_norm_ja` | text | oui |  |
| `synopsis_clean` | text | oui |  |
| `rating_average_10` | text | oui |  |
| `rating_rank` | text | oui |  |
| `popularity_rank` | text | oui |  |
| `categories_json` | text | oui |  |
| `genres_json` | text | oui |  |
| `tags_all_json` | text | oui |  |


### `manga.kitsu_staff`

30678 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `staff_row_id` | int8 | NON |  |
| `kitsu_id` | int8 | NON |  |
| `personne` | text | NON |  |
| `personne_norm` | text | NON |  |
| `role` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |

- **PK** : `CREATE UNIQUE INDEX kitsu_staff_pkey ON manga.kitsu_staff USING btree (staff_row_id)`
- **UNIQUE** : `CREATE UNIQUE INDEX kitsu_staff_kitsu_id_personne_norm_role_key ON manga.kitsu_staff USING btree (kitsu_id, personne_norm, role)`

### `manga.kitsu_weekly_snapshot`

190 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `list_name` | text | NON |  |
| `fetched_at_ts` | timestamptz | NON |  |
| `kitsu_id` | int8 | NON |  |
| `position` | int4 | NON |  |
| `list_rank` | int4 | oui |  |
| `trend_rank` | int4 | oui |  |
| `endpoint` | text | oui |  |

- **PK** : `CREATE UNIQUE INDEX kitsu_weekly_snapshot_pkey ON manga.kitsu_weekly_snapshot USING btree (list_name, fetched_at_ts, kitsu_id)`
- **FK** : (kitsu_id) -> `manga.kitsu_series_core`(kitsu_id)
- **CHECK** `kitsu_weekly_snapshot_position_positive` : `CHECK (("position" > 0))`
- **CHECK** `kitsu_weekly_snapshot_trend_rank_rule` : `CHECK ((((list_name <> 'trending_weekly'::text) AND (trend_rank IS NULL)) OR ((list_name = 'trending_weekly'::text) AND (trend_rank = "position"))))`

### `manga.kitsu_weekly_snapshot_stg`

190 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `list_name` | text | oui |  |
| `fetched_at_ts` | text | oui |  |
| `endpoint` | text | oui |  |
| `kitsu_id` | text | oui |  |
| `position` | text | oui |  |
| `list_rank` | text | oui |  |
| `trend_rank` | text | oui |  |


### `manga.llm_avis`

2759 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `avis_id` | int8 | NON |  |
| `series_id` | int4 | NON |  |
| `run_ts` | timestamptz | NON |  |
| `phase` | text | NON |  |
| `candidat_type` | text | NON |  |
| `candidat_id` | text | NON |  |
| `verdict` | text | NON |  |
| `confiance` | text | NON |  |
| `justification` | text | oui |  |
| `modele` | text | NON |  |
| `prompt_version` | text | NON |  |
| `tokens_in` | int4 | oui |  |
| `tokens_out` | int4 | oui |  |
| `pre_validation_bandes` | bool | NON | false |
| `dossier_partiel` | bool | NON | false |
| `created_at` | timestamptz | NON | now() |

- **PK** : `CREATE UNIQUE INDEX llm_avis_pkey ON manga.llm_avis USING btree (avis_id)`
- **UNIQUE** : `CREATE UNIQUE INDEX llm_avis_unicite ON manga.llm_avis USING btree (series_id, candidat_type, candidat_id, run_ts, phase)`
- **FK** : (series_id) -> `manga.ms_series_enriched`(series_id)
- **CHECK** `llm_avis_candidat_type_check` : `CHECK ((candidat_type = ANY (ARRAY['qid'::text, 'kitsu_id'::text])))`
- **CHECK** `llm_avis_confiance_check` : `CHECK ((confiance = ANY (ARRAY['haute'::text, 'moyenne'::text])))`
- **CHECK** `llm_avis_phase_check` : `CHECK ((phase = ANY (ARRAY['etalonnage'::text, 'file'::text, 'echantillon'::text])))`
- **CHECK** `llm_avis_verdict_check` : `CHECK ((verdict = ANY (ARRAY['same_work'::text, 'different_work'::text, 'undecidable'::text])))`

### `manga.match_decision`

10347 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `decision_id` | int8 | NON |  |
| `series_id` | int4 | NON |  |
| `wikidata_qid` | text | oui |  |
| `method` | text | NON |  |
| `score` | float4 | oui |  |
| `status` | text | NON |  |
| `decided_at` | timestamptz | NON | now() |
| `decided_by` | text | NON | 'pipeline'::text |
| `details` | jsonb | oui |  |

- **PK** : `CREATE UNIQUE INDEX match_decision_pkey ON manga.match_decision USING btree (decision_id)`
- **CHECK** `match_decision_method_check` : `CHECK ((method = ANY (ARRAY['kitsu_bridge'::text, 'exact'::text, 'exact_author'::text, 'exact_kitsu'::text, 'exact_kitsu_author'::text, 'trgm'::text, 'embedding'::text, 'llm_review'::text, 'human_review'::text, 'manual'::text])))`
- **CHECK** `match_decision_status_check` : `CHECK ((status = ANY (ARRAY['auto'::text, 'validated'::text, 'rejected'::text, 'needs_review'::text])))`

### `manga.mi_series`

10162 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `serie_id` | int8 | NON |  |
| `original_url` | text | oui |  |
| `adresse` | text | oui |  |
| `adresse_1` | text | oui |  |
| `code_http` | int4 | oui |  |
| `title` | text | oui |  |
| `titre_vo` | text | oui |  |
| `titre_traduit` | text | oui |  |
| `editeur_vf` | text | oui |  |
| `editeur_vo` | text | oui |  |
| `type` | text | oui |  |
| `genre_1` | text | oui |  |
| `genre_2` | text | oui |  |
| `prepublication` | text | oui |  |
| `nombre_tomes_vf` | text | oui |  |
| `nombre_tomes_vo` | text | oui |  |
| `statut_vf` | text | oui |  |
| `statut_vo` | text | oui |  |
| `pays` | text | oui |  |
| `annee_pays_d_origine` | int4 | oui |  |
| `annee` | int4 | oui |  |
| `date_sortie_france` | date | oui |  |
| `date_sortie_france_raw` | text | oui |  |
| `date_sortie_france_annee` | int4 | oui |  |
| `date_sortie_france_mois` | int4 | oui |  |
| `tomes_vf` | int4 | oui |  |
| `tomes_vo` | int4 | oui |  |
| `meta_categorie` | text | oui |  |
| `meta_fichier` | text | oui |  |
| `meta_nouveaute` | bool | oui |  |
| `meta_nouvelle_edition` | bool | oui |  |
| `meta_coffret` | bool | oui |  |
| `meta_collector` | bool | oui |  |
| `meta_type_titre` | text | oui |  |
| `meta_type_source` | text | oui |  |
| `meta_doublon_editeur` | bool | oui |  |
| `meta_editeurs_doublons` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |
| `source_file` | text | oui |  |

- **PK** : `CREATE UNIQUE INDEX mi_series_pkey ON manga.mi_series USING btree (serie_id)`

### `manga.mi_sorties`

48900 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `sortie_id` | int8 | NON |  |
| `ean` | text | oui |  |
| `ean_valide` | bool | oui |  |
| `titre` | text | oui |  |
| `titre_vo` | text | oui |  |
| `editeur_vf` | text | oui |  |
| `editeur_vo` | text | oui |  |
| `type` | text | oui |  |
| `genre_1` | text | oui |  |
| `genre_2` | text | oui |  |
| `statut_vf` | text | oui |  |
| `statut_vo` | text | oui |  |
| `pays` | text | oui |  |
| `annee_pays_d_origine` | int4 | oui |  |
| `date_sortie_france` | date | oui |  |
| `date_sortie_france_raw` | text | oui |  |
| `date_sortie_france_annee` | int4 | oui |  |
| `date_sortie_france_mois` | int4 | oui |  |
| `tomes_vf` | int4 | oui |  |
| `tomes_vo` | int4 | oui |  |
| `dessin` | text | oui |  |
| `scenario` | text | oui |  |
| `unnamed_0` | text | oui |  |
| `meta_categorie` | text | oui |  |
| `meta_fichier` | text | oui |  |
| `meta_annee_fichier` | text | oui |  |
| `meta_mois_fichier` | text | oui |  |
| `meta_nouveaute` | bool | oui |  |
| `meta_nouvelle_edition` | bool | oui |  |
| `meta_coffret` | bool | oui |  |
| `meta_collector` | bool | oui |  |
| `meta_type_titre` | text | oui |  |
| `meta_type_source` | text | oui |  |
| `meta_doublon_editeur` | bool | oui |  |
| `meta_editeurs_doublons` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |
| `source_file` | text | oui |  |

- **PK** : `CREATE UNIQUE INDEX mi_sorties_pkey ON manga.mi_sorties USING btree (sortie_id)`

### `manga.ms_formes`

31904 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `forme_id` | int8 | NON |  |
| `series_id` | int8 | NON |  |
| `forme` | text | NON |  |
| `forme_norm` | text | NON |  |
| `forme_type` | text | NON |  |
| `source` | text | NON | 'ms'::text |
| `langue` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |

- **PK** : `CREATE UNIQUE INDEX ms_formes_pkey ON manga.ms_formes USING btree (forme_id)`
- **UNIQUE** : `CREATE UNIQUE INDEX ms_formes_series_id_forme_norm_source_key ON manga.ms_formes USING btree (series_id, forme_norm, source)`
- **FK** : (series_id) -> `manga.ms_series_enriched`(series_id)
- **CHECK** `ms_formes_forme_type_check` : `CHECK ((forme_type = ANY (ARRAY['title'::text, 'alias'::text])))`

### `manga.ms_kitsu_ambiguous`

264 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `series_id` | int8 | NON |  |
| `ms_title_main` | text | oui |  |
| `ms_title_norm` | text | oui |  |
| `n_exact_candidates` | int4 | oui |  |

- **PK** : `CREATE UNIQUE INDEX ms_kitsu_ambiguous_pkey ON manga.ms_kitsu_ambiguous USING btree (series_id)`
- **FK** : (series_id) -> `manga.ms_series_enriched`(series_id)

### `manga.ms_kitsu_map`

5608 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `series_id` | int8 | NON |  |
| `kitsu_id` | int8 | oui |  |
| `match_method` | text | oui |  |
| `match_score` | float8 | oui |  |
| `matched_title_norm` | text | oui |  |
| `ms_title` | text | oui |  |
| `ms_title_norm` | text | oui |  |

- **PK** : `CREATE UNIQUE INDEX ms_kitsu_map_pkey ON manga.ms_kitsu_map USING btree (series_id)`
- **FK** : (series_id) -> `manga.ms_series_enriched`(series_id)

### `manga.ms_reviews`

3187 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `review_id` | int8 | NON | nextval('manga.ms_reviews_review_id_s... |
| `series_id` | int8 | oui |  |
| `series_title` | text | oui |  |
| `series_url` | text | oui |  |
| `volume_number` | int4 | oui |  |
| `volume_url` | text | oui |  |
| `review_url` | text | oui |  |
| `review_title` | text | oui |  |
| `review_score` | float8 | oui |  |
| `review_author` | text | oui |  |
| `review_date_raw` | text | oui |  |
| `review_date_iso` | date | oui |  |
| `review_type` | text | oui |  |
| `review_body` | text | oui |  |
| `source_line` | int8 | oui |  |
| `review_date_parse_ok` | bool | oui |  |

- **PK** : `CREATE UNIQUE INDEX ms_reviews_pkey ON manga.ms_reviews USING btree (review_id)`
- **FK** : (series_id) -> `manga.ms_series_enriched`(series_id)
- **FK** : (volume_url) -> `manga.ms_volumes_enriched`(volume_url)

### `manga.ms_reviews_all`

11074 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `review_id` | int8 | NON | nextval('manga.ms_reviews_all_review_... |
| `series_id` | int8 | oui |  |
| `series_title` | text | oui |  |
| `series_url` | text | oui |  |
| `volume_number` | int4 | oui |  |
| `volume_url` | text | oui |  |
| `review_url` | text | oui |  |
| `review_title` | text | oui |  |
| `review_score` | float8 | oui |  |
| `review_author` | text | oui |  |
| `review_date_raw` | text | oui |  |
| `review_date_iso` | date | oui |  |
| `review_type` | text | oui |  |
| `review_body` | text | oui |  |
| `source_line` | int8 | oui |  |
| `review_date_parse_ok` | bool | oui |  |
| `rag_text` | text | oui |  |
| `rag_len` | int4 | oui |  |
| `rag_ready` | bool | oui |  |
| `review_grain` | text | NON | 'volume'::text |

- **PK** : `CREATE UNIQUE INDEX ms_reviews_all_pkey ON manga.ms_reviews_all USING btree (review_id)`
- **UNIQUE** *(partiel)* : `CREATE UNIQUE INDEX ms_reviews_all_review_url_uniq ON manga.ms_reviews_all USING btree (review_url) WHERE (review_url IS NOT NULL)`
- **FK** : (series_id) -> `manga.ms_series_enriched`(series_id)
- **FK** : (volume_url) -> `manga.ms_volumes_enriched`(volume_url)
- **CHECK** `ms_reviews_all_review_grain_check` : `CHECK ((review_grain = ANY (ARRAY['volume'::text, 'serie'::text])))`

### `manga.ms_series_enriched`

14670 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `series_id` | int8 | NON |  |
| `series_url` | text | oui |  |
| `series_title` | text | oui |  |
| `series_type` | text | oui |  |
| `series_category` | text | oui |  |
| `series_year` | int4 | oui |  |
| `series_other_titles` | jsonb | oui |  |
| `series_genres` | jsonb | oui |  |
| `series_tags` | jsonb | oui |  |
| `series_statuses` | jsonb | oui |  |
| `series_related_works` | jsonb | oui |  |
| `series_dessinateur` | text | oui |  |
| `series_scenariste` | text | oui |  |
| `series_mag_prepub` | text | oui |  |
| `series_popularity_rank` | int8 | oui |  |
| `series_members_rating` | float8 | oui |  |
| `series_members_votes` | int8 | oui |  |
| `series_experts_rating` | float8 | oui |  |
| `series_experts_votes` | int8 | oui |  |
| `series_synopsis` | text | oui |  |
| `series_synopsis_enriched` | text | oui |  |
| `series_category_year_guess` | int4 | oui |  |
| `series_category_clean` | text | oui |  |
| `series_category_is_allowed` | bool | oui |  |
| `series_volume_count` | int8 | oui |  |
| `series_review_count` | int8 | oui |  |
| `series_score_mean` | float8 | oui |  |
| `series_score_median` | float8 | oui |  |
| `series_score_min` | float8 | oui |  |
| `series_score_max` | float8 | oui |  |
| `series_with_body_count` | int8 | oui |  |
| `series_with_date_count` | int8 | oui |  |
| `series_with_body_pct` | float8 | oui |  |
| `series_with_date_pct` | float8 | oui |  |
| `series_first_review_date_iso` | date | oui |  |
| `series_last_review_date_iso` | date | oui |  |
| `ms_title_main` | text | oui |  |
| `ms_title_norm` | text | oui |  |
| `matched_title_norm` | text | oui |  |
| `ms_title` | text | oui |  |
| `kitsu_id` | int8 | oui |  |
| `match_method` | text | oui |  |
| `match_score` | float8 | oui |  |
| `kitsu_id_ms_count` | int4 | oui |  |
| `kitsu_id_collision` | bool | oui |  |
| `fuzzy_low_score` | bool | oui |  |
| `ms_title_norm_len` | int4 | oui |  |
| `title_too_short` | bool | oui |  |
| `needs_review` | bool | oui |  |
| `review_reason` | text | oui |  |
| `kitsu_slug` | text | oui |  |
| `kitsu_status` | text | oui |  |
| `kitsu_title_canonical` | text | oui |  |
| `kitsu_title_en` | text | oui |  |
| `kitsu_title_ja` | text | oui |  |
| `kitsu_title_norm_primary` | text | oui |  |
| `kitsu_title_norm_canonical` | text | oui |  |
| `kitsu_title_norm_en` | text | oui |  |
| `kitsu_title_norm_ja` | text | oui |  |
| `kitsu_synopsis_clean` | text | oui |  |
| `kitsu_rating_average_10` | float8 | oui |  |
| `kitsu_rating_rank` | int8 | oui |  |
| `kitsu_popularity_rank` | int8 | oui |  |
| `kitsu_categories_json` | jsonb | oui |  |
| `kitsu_genres_json` | jsonb | oui |  |
| `kitsu_tags_all_json` | jsonb | oui |  |
| `series_tags_enriched` | jsonb | oui |  |
| `series_genres_enriched` | jsonb | oui |  |
| `ms_title_norm_x` | text | oui |  |
| `ms_title_norm_y` | text | oui |  |
| `_other_titles_list` | text | oui |  |
| `kitsu_kitsu_id` | int8 | oui |  |
| `work_uid` | int8 | oui |  |

- **PK** : `CREATE UNIQUE INDEX ms_series_enriched_pkey ON manga.ms_series_enriched USING btree (series_id)`
- **FK** : (work_uid) -> `manga.work_identity`(work_uid)

### `manga.ms_volumes_enriched`

104107 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `volume_url` | text | NON |  |
| `volume_title` | text | oui |  |
| `volume_number` | int4 | oui |  |
| `volume_publication_date` | date | oui |  |
| `volume_dessinateur` | text | oui |  |
| `volume_scenariste` | text | oui |  |
| `volume_editeur` | text | oui |  |
| `volume_format` | text | oui |  |
| `volume_pages` | int4 | oui |  |
| `volume_country` | text | oui |  |
| `volume_status` | text | oui |  |
| `volume_tomes_published` | int4 | oui |  |
| `volume_tomes_total` | int4 | oui |  |
| `volume_members_votes` | int8 | oui |  |
| `volume_experts_rating` | float8 | oui |  |
| `volume_experts_votes` | int8 | oui |  |
| `volume_synopsis` | text | oui |  |
| `series_id` | int8 | oui |  |
| `review_count` | int4 | oui |  |
| `score_mean` | float8 | oui |  |
| `score_median` | float8 | oui |  |
| `score_min` | float8 | oui |  |
| `score_max` | float8 | oui |  |
| `with_body_count` | int4 | oui |  |
| `with_date_count` | int4 | oui |  |
| `with_body_pct` | float8 | oui |  |
| `with_date_pct` | float8 | oui |  |
| `first_review_date_iso` | date | oui |  |
| `last_review_date_iso` | date | oui |  |
| `volume_ean` | text | oui |  |

- **PK** : `CREATE UNIQUE INDEX ms_volumes_enriched_pkey ON manga.ms_volumes_enriched USING btree (volume_url)`
- **FK** : (series_id) -> `manga.ms_series_enriched`(series_id)

### `manga.rag_kitsu_docs`

43085 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `kitsu_id` | int8 | NON |  |
| `doc_text` | text | NON |  |
| `tags_all_json` | jsonb | oui |  |
| `trending_pos` | int4 | oui |  |
| `popular_pos` | int4 | oui |  |
| `top_pos` | int4 | oui |  |
| `updated_at` | timestamptz | NON | now() |

- **PK** : `CREATE UNIQUE INDEX rag_kitsu_docs_pkey ON manga.rag_kitsu_docs USING btree (kitsu_id)`
- **FK** : (kitsu_id) -> `manga.kitsu_series_core`(kitsu_id)

### `manga.rag_reviews_docs`

3187 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `doc_id` | int8 | NON | nextval('manga.rag_reviews_docs_doc_i... |
| `volume_url` | text | oui |  |
| `series_id` | int8 | oui |  |
| `review_url` | text | oui |  |
| `rag_text` | text | oui |  |
| `rag_len` | int4 | oui |  |
| `rag_ready` | bool | oui |  |

- **PK** : `CREATE UNIQUE INDEX rag_reviews_docs_pkey ON manga.rag_reviews_docs USING btree (doc_id)`
- **FK** : (series_id) -> `manga.ms_series_enriched`(series_id)
- **FK** : (volume_url) -> `manga.ms_volumes_enriched`(volume_url)

### `manga.volume_identity`

104107 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `volume_url` | text | NON |  |
| `work_uid` | int8 | oui |  |
| `isbn13` | bpchar | oui |  |
| `isbn13_valide` | bool | oui |  |
| `created_at` | timestamptz | NON | now() |
| `updated_at` | timestamptz | NON | now() |

- **PK** : `CREATE UNIQUE INDEX volume_identity_pkey ON manga.volume_identity USING btree (volume_url)`
- **FK** : (work_uid) -> `manga.work_identity`(work_uid)
- **CHECK** `volume_identity_isbn13_check` : `CHECK ((isbn13 ~ '^[0-9]{13}$'::text))`

### `manga.wd_auteurs`

5453 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `qid` | text | NON |  |
| `auteur_qid` | text | NON |  |
| `auteur` | text | oui |  |
| `auteur_norm` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |
| `auteur_lang` | text | oui |  |

- **PK** : `CREATE UNIQUE INDEX wd_auteurs_pkey ON manga.wd_auteurs USING btree (qid, auteur_qid)`
- **FK** : (qid) -> `manga.wd_pivot`(qid)

### `manga.wd_auteurs_formes`

9090 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `forme_id` | int8 | NON |  |
| `auteur_qid` | text | NON |  |
| `forme` | text | NON |  |
| `forme_norm` | text | NON |  |
| `forme_type` | text | NON |  |
| `langue` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |

- **PK** : `CREATE UNIQUE INDEX wd_auteurs_formes_pkey ON manga.wd_auteurs_formes USING btree (forme_id)`
- **UNIQUE** : `CREATE UNIQUE INDEX wd_auteurs_formes_auteur_qid_forme_norm_key ON manga.wd_auteurs_formes USING btree (auteur_qid, forme_norm)`
- **CHECK** `wd_auteurs_formes_forme_type_check` : `CHECK ((forme_type = ANY (ARRAY['label'::text, 'alias'::text])))`

### `manga.wd_formes`

26103 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `forme_id` | int8 | NON |  |
| `qid` | text | NON |  |
| `forme` | text | NON |  |
| `forme_norm` | text | NON |  |
| `forme_type` | text | NON |  |
| `langue` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |

- **PK** : `CREATE UNIQUE INDEX wd_formes_pkey ON manga.wd_formes USING btree (forme_id)`
- **UNIQUE** : `CREATE UNIQUE INDEX wd_formes_qid_forme_norm_key ON manga.wd_formes USING btree (qid, forme_norm)`
- **FK** : (qid) -> `manga.wd_pivot`(qid)
- **CHECK** `wd_formes_forme_type_check` : `CHECK ((forme_type = ANY (ARRAY['label'::text, 'alias'::text])))`

### `manga.wd_pivot`

8214 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `qid` | text | NON |  |
| `label_principal` | text | oui |  |
| `annee` | int4 | oui |  |
| `mal_id` | text | oui |  |
| `anilist_id` | text | oui |  |
| `ann_id` | text | oui |  |
| `wiki_fr` | text | oui |  |
| `wiki_en` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |
| `updated_at` | timestamptz | NON | now() |
| `wiki_ja` | text | oui |  |

- **PK** : `CREATE UNIQUE INDEX wd_pivot_pkey ON manga.wd_pivot USING btree (qid)`

### `manga.work_identity`

14670 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `work_uid` | int8 | NON |  |
| `series_id` | int4 | oui |  |
| `wikidata_qid` | text | oui |  |
| `kitsu_id` | text | oui |  |
| `mal_id` | text | oui |  |
| `anilist_id` | text | oui |  |
| `madb_id` | text | oui |  |
| `disponibilite` | text | oui |  |
| `created_at` | timestamptz | NON | now() |
| `updated_at` | timestamptz | NON | now() |

- **PK** : `CREATE UNIQUE INDEX work_identity_pkey ON manga.work_identity USING btree (work_uid)`
- **UNIQUE** *(partiel)* : `CREATE UNIQUE INDEX work_identity_anilist_id_uniq ON manga.work_identity USING btree (anilist_id) WHERE (anilist_id IS NOT NULL)`
- **UNIQUE** *(partiel)* : `CREATE UNIQUE INDEX work_identity_kitsu_id_uniq ON manga.work_identity USING btree (kitsu_id) WHERE (kitsu_id IS NOT NULL)`
- **UNIQUE** *(partiel)* : `CREATE UNIQUE INDEX work_identity_madb_id_uniq ON manga.work_identity USING btree (madb_id) WHERE (madb_id IS NOT NULL)`
- **UNIQUE** *(partiel)* : `CREATE UNIQUE INDEX work_identity_mal_id_uniq ON manga.work_identity USING btree (mal_id) WHERE (mal_id IS NOT NULL)`
- **UNIQUE** *(partiel)* : `CREATE UNIQUE INDEX work_identity_series_id_uniq ON manga.work_identity USING btree (series_id) WHERE (series_id IS NOT NULL)`
- **UNIQUE** *(partiel)* : `CREATE UNIQUE INDEX work_identity_wikidata_qid_uniq ON manga.work_identity USING btree (wikidata_qid) WHERE (wikidata_qid IS NOT NULL)`
- **CHECK** `work_identity_disponibilite_check` : `CHECK ((disponibilite = ANY (ARRAY['vf_disponible'::text, 'vf_epuisee'::text, 'vo_seulement'::text, 'non_licencie'::text])))`

### `staging.kitsu_formes`

0 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `kitsu_id` | text | oui |  |
| `forme_normalisee` | text | oui |  |
| `forme_originale` | text | oui |  |
| `langue` | text | oui |  |
| `type` | text | oui |  |
| `subtype` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |
| `source_file` | text | oui |  |


### `staging.kitsu_mappings`

104726 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `kitsu_id` | text | oui |  |
| `external_site` | text | oui |  |
| `external_id` | text | oui |  |
| `mapping_id` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |
| `source_file` | text | oui |  |


### `staging.kitsu_staff`

53183 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `kitsu_id` | text | oui |  |
| `personne_id` | text | oui |  |
| `personne` | text | oui |  |
| `role` | text | oui |  |
| `staff_id` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |
| `source_file` | text | oui |  |


### `staging.mi_series`

0 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `original_url` | text | oui |  |
| `adresse` | text | oui |  |
| `code_http` | text | oui |  |
| `title` | text | oui |  |
| `titre_vo` | text | oui |  |
| `adresse_1` | text | oui |  |
| `titre_traduit` | text | oui |  |
| `editeur_vf` | text | oui |  |
| `editeur_vo` | text | oui |  |
| `type` | text | oui |  |
| `genre_1` | text | oui |  |
| `genre_2` | text | oui |  |
| `prepublication` | text | oui |  |
| `nombre_tomes_vf` | text | oui |  |
| `nombre_tomes_vo` | text | oui |  |
| `statut_vf` | text | oui |  |
| `statut_vo` | text | oui |  |
| `pays` | text | oui |  |
| `annee_pays_d_origine` | text | oui |  |
| `date_sortie_france` | text | oui |  |
| `annee` | text | oui |  |
| `meta_categorie` | text | oui |  |
| `meta_fichier` | text | oui |  |
| `date_sortie_france_annee` | text | oui |  |
| `date_sortie_france_mois` | text | oui |  |
| `tomes_vf` | text | oui |  |
| `tomes_vo` | text | oui |  |
| `meta_nouveaute` | text | oui |  |
| `meta_nouvelle_edition` | text | oui |  |
| `meta_coffret` | text | oui |  |
| `meta_collector` | text | oui |  |
| `meta_type_titre` | text | oui |  |
| `meta_type_source` | text | oui |  |
| `meta_doublon_editeur` | text | oui |  |
| `meta_editeurs_doublons` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |
| `source_file` | text | oui |  |


### `staging.mi_sorties`

0 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `titre_vo` | text | oui |  |
| `editeur_vf` | text | oui |  |
| `editeur_vo` | text | oui |  |
| `type` | text | oui |  |
| `genre_1` | text | oui |  |
| `genre_2` | text | oui |  |
| `statut_vf` | text | oui |  |
| `statut_vo` | text | oui |  |
| `pays` | text | oui |  |
| `annee_pays_d_origine` | text | oui |  |
| `date_sortie_france` | text | oui |  |
| `meta_categorie` | text | oui |  |
| `meta_fichier` | text | oui |  |
| `meta_annee_fichier` | text | oui |  |
| `meta_mois_fichier` | text | oui |  |
| `date_sortie_france_annee` | text | oui |  |
| `date_sortie_france_mois` | text | oui |  |
| `tomes_vf` | text | oui |  |
| `tomes_vo` | text | oui |  |
| `unnamed_0` | text | oui |  |
| `titre` | text | oui |  |
| `ean` | text | oui |  |
| `meta_nouveaute` | text | oui |  |
| `meta_nouvelle_edition` | text | oui |  |
| `meta_coffret` | text | oui |  |
| `meta_collector` | text | oui |  |
| `meta_type_titre` | text | oui |  |
| `meta_type_source` | text | oui |  |
| `meta_doublon_editeur` | text | oui |  |
| `meta_editeurs_doublons` | text | oui |  |
| `dessin` | text | oui |  |
| `scenario` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |
| `source_file` | text | oui |  |


### `staging.ms_reviews`

11052 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `series_id` | text | oui |  |
| `series_title` | text | oui |  |
| `series_url` | text | oui |  |
| `volume_number` | text | oui |  |
| `volume_url` | text | oui |  |
| `review_url` | text | oui |  |
| `review_title` | text | oui |  |
| `review_score` | text | oui |  |
| `review_author` | text | oui |  |
| `review_date` | text | oui |  |
| `review_type` | text | oui |  |
| `review_body` | text | oui |  |
| `review_date_iso` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |
| `source_file` | text | oui |  |


### `staging.ms_volumes`

103811 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `series_id` | text | oui |  |
| `series_url` | text | oui |  |
| `series_title` | text | oui |  |
| `series_type` | text | oui |  |
| `series_category` | text | oui |  |
| `series_year` | text | oui |  |
| `series_other_titles` | text | oui |  |
| `series_dessinateur` | text | oui |  |
| `series_scenariste` | text | oui |  |
| `series_genres` | text | oui |  |
| `series_tags` | text | oui |  |
| `series_mag_prepub` | text | oui |  |
| `series_statuses` | text | oui |  |
| `series_popularity_rank` | text | oui |  |
| `series_members_rating` | text | oui |  |
| `series_members_votes` | text | oui |  |
| `series_experts_rating` | text | oui |  |
| `series_experts_votes` | text | oui |  |
| `series_synopsis` | text | oui |  |
| `series_related_works` | text | oui |  |
| `volume_url` | text | oui |  |
| `volume_title` | text | oui |  |
| `volume_number` | text | oui |  |
| `volume_publication_date` | text | oui |  |
| `volume_dessinateur` | text | oui |  |
| `volume_scenariste` | text | oui |  |
| `volume_editeur` | text | oui |  |
| `volume_ean` | text | oui |  |
| `volume_format` | text | oui |  |
| `volume_pages` | text | oui |  |
| `volume_country` | text | oui |  |
| `volume_status` | text | oui |  |
| `volume_tomes_published` | text | oui |  |
| `volume_tomes_total` | text | oui |  |
| `volume_members_rating` | text | oui |  |
| `volume_members_votes` | text | oui |  |
| `volume_experts_rating` | text | oui |  |
| `volume_experts_votes` | text | oui |  |
| `volume_synopsis` | text | oui |  |
| `volume_publication_date_iso` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |
| `source_file` | text | oui |  |


### `staging.wd_auteurs`

0 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `qid` | text | oui |  |
| `auteur_qid` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |
| `source_file` | text | oui |  |


### `staging.wd_entities`

0 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `qid` | text | oui |  |
| `label_principal` | text | oui |  |
| `annee` | text | oui |  |
| `mal_id` | text | oui |  |
| `anilist_id` | text | oui |  |
| `ann_id` | text | oui |  |
| `wiki_fr` | text | oui |  |
| `wiki_en` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |
| `source_file` | text | oui |  |


### `staging.wd_formes`

0 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `qid` | text | oui |  |
| `forme_normalisee` | text | oui |  |
| `forme_originale` | text | oui |  |
| `langue` | text | oui |  |
| `type` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |
| `source_file` | text | oui |  |


### `staging.wd_pivot`

0 lignes.

| Colonne | Type | Null | Defaut |
| --- | --- | --- | --- |
| `qid` | text | oui |  |
| `mal_id` | text | oui |  |
| `anilist_id` | text | oui |  |
| `loaded_at` | timestamptz | NON | now() |
| `source_file` | text | oui |  |


