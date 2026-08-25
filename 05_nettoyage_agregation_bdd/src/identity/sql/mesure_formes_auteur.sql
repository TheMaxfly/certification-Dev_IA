-- Répartition par SCRIPT des formes d'auteur qui portent les concordances
-- de l'étage 1. Dette 22.3 : certification du chiffre « 99,6 %% ».
-- (les « %% » des commentaires sont doublés : psycopg lit tout le fichier,
--  commentaires compris, pour y chercher ses paramètres.)
--
-- LECTURE SEULE. Un seul SELECT, aucune table temporaire, aucune écriture.
--
-- CE QUE MESURE CETTE REQUÊTE
--
--   Dénominateur : les séries dont la décision COURANTE est
--     `method = 'exact_author'` et `status = 'auto'` — celles dont l'identité
--     existe PARCE QUE le signal auteur a tranché à l'étage 1. Population
--     persistée dans `v_match_current`, donc rejouable à l'identique.
--
--   Concordance d'auteur : un couple (série, forme Wikidata) tel que le nom
--     d'auteur MS normalisé égale `wd_auteurs_formes.forme_norm`, pour un
--     auteur rattaché au QID retenu. C'est le prédicat de `etage1_exact.sql`.
--
--   Forme latine : `forme_norm` ne contient AUCUN idéogramme ni kana. Une
--     romanisation étiquetée `ja` (« fukuchi tsubasa ») compte donc comme
--     latine : c'est la GRAPHIE qui est mesurée, pas la déclaration de langue
--     de Wikidata. Les deux ne coïncident pas, et l'argument porte sur la
--     graphie — l'appariement repose-t-il sur des formes occidentales ?
--
-- POURQUOI LES AUTEURS MS ARRIVENT EN PARAMÈTRE
--
-- La normalisation MS est faite par `identity.normaliser()`, en Python, comme
-- partout dans le chemin de décision. La ré-écrire en SQL ici rejouerait
-- exactement la faute que cette dette corrige : le « 99,6 %% » d'origine venait
-- d'une requête de diagnostic à normalisation SQL approchée. Les couples
-- (series_id, auteur_norm) sont donc calculés en Python et passés en tableaux.
--
-- CLASSE DE CARACTÈRES : blocs Unicode considérés comme non latins.
--     U+3040..U+309F  hiragana
--     U+30A0..U+30FF  katakana
--     U+31F0..U+31FF  katakana, extensions phonetiques
--     U+3400..U+4DBF  ideogrammes CJK, extension A
--     U+4E00..U+9FFF  ideogrammes CJK, bloc unifie
--     U+F900..U+FAFF  ideogrammes CJK de compatibilite
--     U+FF66..U+FF9F  katakana demi-chasse
-- Ce fichier est GÉNÉRÉ depuis ces bornes numériques ; `mesure_formes_auteur.py`
-- vérifie la classe sur des cas témoins avant toute mesure, pour qu'un accident
-- d'encodage se voie au lieu de fausser le chiffre.

WITH ms_auteur AS (
    SELECT series_id, auteur_norm
      FROM unnest(%(series_ids)s::bigint[], %(auteurs_norm)s::text[])
             AS t(series_id, auteur_norm)
),
decidees AS (
    SELECT v.series_id, v.wikidata_qid
      FROM manga.v_match_current v
     WHERE v.method = 'exact_author'
       AND v.status = 'auto'
       AND v.wikidata_qid IS NOT NULL
),
concordance AS (
    -- Une ligne par (série, forme) qui concorde réellement.
    SELECT DISTINCT
           d.series_id,
           waf.forme_norm,
           waf.langue,
           waf.forme_norm !~ '[぀-ゟ゠-ヿㇰ-ㇿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ]' AS latine
      FROM decidees d
      JOIN manga.wd_auteurs wa
        ON wa.qid = d.wikidata_qid
      JOIN manga.wd_auteurs_formes waf
        ON waf.auteur_qid = wa.auteur_qid
      JOIN ms_auteur m
        ON m.series_id = d.series_id
       AND m.auteur_norm = waf.forme_norm
),
concordance_d0 AS (
    -- CONTREFACTUEL : et si l'on n'avait QUE le nom d'auteur RETENU en D0
    -- (`wd_auteurs.auteur_norm`), sans la table des formes multiples ?
    -- C'est la seconde moitié de l'affirmation d'origine (« n'aurait matché
    -- que 4 séries au lieu de ~1 058 »), et elle se mesure ici même.
    SELECT DISTINCT d.series_id
      FROM decidees d
      JOIN manga.wd_auteurs wa
        ON wa.qid = d.wikidata_qid
       AND wa.auteur_norm IS NOT NULL
      JOIN ms_auteur m
        ON m.series_id = d.series_id
       AND m.auteur_norm = wa.auteur_norm
),
par_serie AS (
    SELECT series_id,
           bool_or(latine)      AS a_une_forme_latine,
           bool_or(NOT latine)  AS a_une_forme_non_latine,
           count(*)             AS n_formes
      FROM concordance
     GROUP BY series_id
)
SELECT
    (SELECT count(*) FROM decidees)                       AS series_decidees,
    (SELECT count(*) FROM par_serie)                      AS series_concordantes,
    (SELECT count(*) FROM concordance)                    AS couples_serie_forme,
    count(*) FILTER (WHERE a_une_forme_latine)            AS series_avec_latine,
    count(*) FILTER (WHERE NOT a_une_forme_latine)        AS series_sans_latine,
    count(*) FILTER (WHERE a_une_forme_latine
                       AND NOT a_une_forme_non_latine)    AS series_latine_seule,
    count(*) FILTER (WHERE a_une_forme_latine
                       AND a_une_forme_non_latine)        AS series_mixtes,
    (SELECT count(*) FROM concordance WHERE latine)       AS couples_latins,
    (SELECT count(*) FROM concordance WHERE NOT latine)   AS couples_non_latins,
    (SELECT count(*) FROM concordance_d0)                 AS series_d0_seul
  FROM par_serie;
