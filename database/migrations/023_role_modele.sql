-- 023 — lecture de l'API du modèle : le rôle de groupe `modele_ro`.
--
-- POURQUOI UN TROISIÈME RÔLE DE LECTURE
--
-- L'API du modèle (module 11) lit, pour une question, les vecteurs de l'encodage en
-- service, les fragments, leur rattachement au catalogue et le titre des séries.
-- `manga_ro` (012, l'API de données) reste hors de `bench` ; `verif_ro` (022) lit tout
-- `manga` et tout `bench`, mesures et journal compris. Aucun ne convient : un rôle
-- distinct, au périmètre ÉNUMÉRÉ (E3, étape 2 ; décision de Max, 20 octobre 2026).
--
-- Même construction que 012 et 022 :
--   * `modele_ro`, rôle de GROUPE, `NOLOGIN`, sans secret : versionné ici ;
--   * le rôle de CONNEXION `modele_api`, qui porte un secret, est créé hors migration
--     par `database/outils/creer_role_modele.sh`.
--
-- PÉRIMÈTRE — `SELECT` seulement, objet par objet ; aucun `ON ALL TABLES`, aucun
-- `ALTER DEFAULT PRIVILEGES` : un objet créé plus tard reste illisible tant qu'une
-- migration ne l'accorde pas.
--   bench : corpus, corpus_docs, corpus_chunks, encodages, vecteurs_embeddinggemma,
--           v_encodage_en_service ;
--   manga : work_identity (series_id, kitsu_id), ms_series_enriched (series_id,
--           series_title) — PAR COLONNES : le rattachement et le titre, rien d'autre.
-- Exclus : eval_*, promotions, vecteurs_bge_m3, le reste de bench et de manga,
-- staging, public.schema_migrations, toute séquence, toute écriture.
--
-- ÉLÉVATION DÉCLARÉE. La vue `bench.v_encodage_en_service` (021) n'est pas
-- `security_invoker` : elle lit `bench.promotions` avec les droits de son
-- propriétaire. Par elle seule, `modele_ro` voit la DERNIÈRE décision `promu` ou
-- `retour_arriere` (identifiant, décision, date, auteur), jamais la table.
-- La table des vecteurs porte plusieurs encodages : l'API filtre par celui de la vue.

-- Rôle de groupe, gardé par un test d'existence : un rôle est un objet de cluster,
-- il a pu être créé par un passage antérieur sur une autre base du même cluster.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'modele_ro') THEN
        CREATE ROLE modele_ro NOLOGIN;
    END IF;
END
$$;

COMMENT ON ROLE modele_ro IS
    'Lecture seule de l''API du modèle (module 11) : corpus, fragments, vecteurs '
    'EmbeddingGemma, registre des encodages et encodage en service (bench) ; '
    'rattachement et titre des séries (manga, par colonnes). Rôle de groupe, '
    'NOLOGIN. Droits posés par la migration 023 ; distinct de manga_ro et verif_ro.';

-- `current_database()` : la migration s'applique aussi aux bases jetables des tests.
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO modele_ro', current_database());
END
$$;

GRANT USAGE ON SCHEMA bench, manga TO modele_ro;

GRANT SELECT ON
    bench.corpus,
    bench.corpus_docs,
    bench.corpus_chunks,
    bench.encodages,
    bench.vecteurs_embeddinggemma,
    bench.v_encodage_en_service
TO modele_ro;

GRANT SELECT (series_id, kitsu_id)     ON manga.work_identity      TO modele_ro;
GRANT SELECT (series_id, series_title) ON manga.ms_series_enriched TO modele_ro;
