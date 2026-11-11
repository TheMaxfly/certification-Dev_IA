-- 028 — la normalisation de la recherche du catalogue et ses index trigrammes (E4, lot 2b).
--
-- POURQUOI
--
-- La recherche de l'API data (module 02, `GET /series`) compare la saisie aux titres,
-- aux autres titres et aux auteurs « sans casse ni accent ». Au lot 2a, faute
-- d'extension, une table `translate` (deux chaînes liées en paramètres) faisait ce
-- travail, colonne par colonne et ligne par ligne. Ici :
--   * `unaccent` (contrib, disponible sur le serveur, non installée jusqu'ici) ;
--   * `pg_trgm` (déjà là depuis la 003 : `IF NOT EXISTS`) ;
--   * une fonction, `manga.normaliser(text)`, que les requêtes et les index partagent ;
--   * quatre index GIN de trigrammes sur ses valeurs : la recherche « contient »
--     (`LIKE '%…%'`) et la ressemblance (`similarity`, `word_similarity`, les
--     suggestions « Vouliez-vous dire… ? ») s'appuient dessus.
--
-- L'IMMUABILITÉ, DE BONNE FOI. `unaccent(text)` n'est que STABLE : elle lit son
-- dictionnaire par le chemin de recherche. Nommer le dictionnaire, qualifié de son
-- schéma (`'public.unaccent'::regdictionary`), et appeler la fonction qualifiée
-- (`public.unaccent`) rend le résultat indépendant du `search_path` de la session :
-- c'est ce qui permet de déclarer `manga.normaliser` IMMUTABLE et d'indexer ses
-- valeurs. Changer les règles du dictionnaire après coup demanderait de reconstruire
-- les quatre index (REINDEX), à écrire dans une migration.
--
-- Rien d'autre : aucune colonne, aucune donnée modifiée, aucun droit. Le rôle de
-- l'API data (`manga_api`, membre de `manga_ro`) appelle la fonction sans droit de plus
-- (`EXECUTE` est accordé à PUBLIC par défaut, comme pour toute fonction).
--
-- Appliquée en préproduction au lot 2b, avec la 029 ; en production au lot de
-- livraison continue (`verification/migrations_en_attente.tsv`). Le 02 fonctionne avec
-- et sans elle : il la détecte au démarrage et garde la table du lot 2a en repli.

CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;

CREATE FUNCTION manga.normaliser(text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
    AS $$ SELECT lower(public.unaccent('public.unaccent'::regdictionary, $1)) $$;

COMMENT ON FUNCTION manga.normaliser(text) IS
    'La forme comparée par la recherche du catalogue (API data, module 02) : minuscules, '
    'sans accent. IMMUTABLE parce que le dictionnaire est nommé et qualifié '
    '(public.unaccent) : le résultat ne dépend pas du search_path. Remplace la table '
    'translate du lot 2a (E4) ; migration 028.';

CREATE INDEX ms_series_enriched_norm_series_title_trgm_idx
    ON manga.ms_series_enriched
    USING gin (manga.normaliser(series_title) public.gin_trgm_ops);
CREATE INDEX ms_series_enriched_norm_series_other_titles_trgm_idx
    ON manga.ms_series_enriched
    USING gin (manga.normaliser(series_other_titles::text) public.gin_trgm_ops);
CREATE INDEX ms_series_enriched_norm_series_scenariste_trgm_idx
    ON manga.ms_series_enriched
    USING gin (manga.normaliser(series_scenariste) public.gin_trgm_ops);
CREATE INDEX ms_series_enriched_norm_series_dessinateur_trgm_idx
    ON manga.ms_series_enriched
    USING gin (manga.normaliser(series_dessinateur) public.gin_trgm_ops);

COMMENT ON INDEX manga.ms_series_enriched_norm_series_title_trgm_idx IS
    'Trigrammes du titre normalisé (manga.normaliser) : recherche et suggestions du 02.';
COMMENT ON INDEX manga.ms_series_enriched_norm_series_other_titles_trgm_idx IS
    'Trigrammes des autres titres normalisés (texte du jsonb) : recherche et suggestions '
    'du 02.';
COMMENT ON INDEX manga.ms_series_enriched_norm_series_scenariste_trgm_idx IS
    'Trigrammes du scénariste normalisé : recherche par auteur et suggestions du 02.';
COMMENT ON INDEX manga.ms_series_enriched_norm_series_dessinateur_trgm_idx IS
    'Trigrammes du dessinateur normalisé : recherche par auteur et suggestions du 02.';
