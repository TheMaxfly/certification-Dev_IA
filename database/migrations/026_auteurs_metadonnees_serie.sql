-- 026 — les auteurs d'une série, dans `manga.v_metadonnees_serie` (025).
--
-- POURQUOI
--
-- La route de réponse de l'API du modèle (module 11, E3 étape 3) donne au modèle de
-- langage les tomes, le statut, le public et la disponibilité sur la ligne de titre, pas
-- l'auteur : il n'arrive que si un passage le nomme, par hasard, alors que le catalogue
-- le possède. Sans lui, le modèle a affirmé une autrice commune à deux séries qui n'en
-- ont pas. Même remède : la vue le rend, la ligne de titre le portera.
--
-- LA RÈGLE, arrêtée sur les comptes d'`apimanga` (2026-11-03 : 14 670 séries).
--
-- `auteurs` : `series_scenariste` puis `series_dessinateur`, tels quels, joints par
--   « , » ; un même nom aux deux colonnes s'écrit une fois (11 443 séries) ; deux noms
--   différents, 2 444 ; un seul rempli, 500 ; aucun, 283 : NULL.
--   Un « inconnu » textuel vaut NULL, comme une valeur vide. Le catalogue n'en a aucun
--   aujourd'hui : chaque colonne porte un seul nom, sans séparateur.
--   `COLLECTIF` (33 séries) reste tel quel : il dit quelque chose, pas « on ne sait
--   pas ». Les suffixes du catalogue (« Kouji INADA (II) ») aussi : ils séparent deux
--   personnes.
--
-- LIMITES. La vue compare des graphies, pas des personnes. Sept paires de noms sont
-- croisées d'une série à l'autre (l'un scénariste ici, dessinateur là : leurs deux
-- lignes diffèrent) ; deux noms ont deux graphies (Carlo ZEN, Ryo SHIRAKOME), et les
-- romanisations (Koji, Kouji) ne se rapprochent pas.
--
-- PÉRIMÈTRE. `CREATE OR REPLACE VIEW` ajoute une colonne, la dernière, et garde les sept
-- autres, leurs règles (025), le propriétaire et les droits : `modele_ro` lit la colonne
-- par son droit sur la vue, et rien de plus. Aucune table, aucune donnée, aucun rôle,
-- aucun droit. L'élévation déclarée en 025 s'étend à ces deux colonnes de la fiche, que
-- `modele_ro` ne lit toujours pas directement.

CREATE OR REPLACE VIEW manga.v_metadonnees_serie AS
SELECT s.series_id,
       CASE
           WHEN v.ean_francais OR s.series_statuses @> '[{"pays": "français"}]'
               THEN 'edite_en_france'
           WHEN v.lignes > 0 AND s.series_type = 'Manga'
               THEN 'seulement_au_japon'
           ELSE 'inconnue'
       END AS disponibilite,
       greatest(v.tomes_parus, v.numeros_distincts)::integer AS nb_volumes,
       CASE
           WHEN abs(v.tomes_parus - v.numeros_distincts)
                > 0.2 * greatest(v.tomes_parus, v.numeros_distincts)
               THEN 'divergent'
           WHEN v.tomes_parus >= coalesce(v.numeros_distincts, 0) THEN 'champ'
           WHEN v.numeros_distincts IS NOT NULL THEN 'repli_volumes_distincts'
       END AS nb_volumes_source,
       CASE
           WHEN s.series_statuses @> '[{"statut": "Complète"}]' THEN 'termine'
           WHEN k.status = 'finished' THEN 'termine'
           WHEN k.status = 'current' THEN 'en_cours'
       END AS statut,
       coalesce(
           s.series_category_clean,
           CASE
               WHEN s.series_category IN (
                   'Seinen', 'Shonen', 'Shojo', 'Josei', 'Yaoi', 'Ecchi-Hentai',
                   'Shonen-Aï', 'Shojo-Aï', 'Yuri', 'Kodomo', 'Parodie'
               ) THEN s.series_category
               WHEN s.series_category = 'Sonyun' THEN 'Shonen'
               WHEN s.series_category = 'Sunjung' THEN 'Shojo'
               WHEN s.series_category = 'Chungnyun' THEN 'Seinen'
           END
       ) AS demographie,
       v.annee_fr,
       nullif(
           concat_ws(', ', a.scenariste, nullif(a.dessinateur, a.scenariste)),
           ''
       ) AS auteurs
  FROM manga.ms_series_enriched s
  CROSS JOIN LATERAL (
      SELECT CASE WHEN s.series_scenariste !~* '^\s*(inconnue?s?)?\s*$'
                  THEN s.series_scenariste END AS scenariste,
             CASE WHEN s.series_dessinateur !~* '^\s*(inconnue?s?)?\s*$'
                  THEN s.series_dessinateur END AS dessinateur
  ) a
  LEFT JOIN LATERAL (
      SELECT count(*) AS lignes,
             nullif(max(vol.volume_tomes_published), 0) AS tomes_parus,
             nullif(count(DISTINCT coalesce(vol.volume_number, 1)) FILTER (
                 WHERE coalesce(vol.volume_number, 1) NOT BETWEEN 1900 AND 2100
             ), 0) AS numeros_distincts,
             coalesce(bool_or(vol.volume_ean ~ '^(9782|97910)'), false) AS ean_francais,
             extract(YEAR FROM min(vol.volume_publication_date)
                 FILTER (WHERE vol.volume_ean ~ '^(9782|97910)'))::integer AS annee_fr
        FROM manga.ms_volumes_enriched vol
       WHERE vol.series_id = s.series_id
  ) v ON true
  LEFT JOIN manga.work_identity w ON w.series_id = s.series_id
  LEFT JOIN manga.kitsu_series_core k ON k.kitsu_id = w.kitsu_id::bigint;

COMMENT ON VIEW manga.v_metadonnees_serie IS
    'Les métadonnées d''une série pour la ligne de titre de l''API du modèle (module 11), '
    'calculées à la lecture ; lisible par modele_ro, qui ne lit pas les tables. '
    'disponibilite : edite_en_france si un volume porte un EAN français (9782, 97910) ou '
    'si la fiche porte le badge « français », seulement_au_japon pour un Manga à volumes '
    'sans cela, inconnue sinon ; édité ne veut pas dire en stock. '
    'nb_volumes : le plus grand du champ « tomes parus » et des numéros distincts, un '
    'volume sans numéro comptant pour le 1, toutes éditions mêlées ; un numéro entre 1900 '
    'et 2100 ne compte pas : c''est une année du titre, prise pour un numéro à la collecte. '
    'nb_volumes_source : divergent si les deux existent et s''écartent de plus de 20 % du '
    'plus grand, champ si le champ est au moins égal aux numéros, repli_volumes_distincts '
    'sinon ; toute source autre que champ se lit comme un minimum, « au moins N ». '
    'statut : termine sur le badge « Complète » ou Kitsu finished, en_cours sur Kitsu '
    'current sans badge ; le catalogue n''écrit jamais « en cours ». '
    'demographie : series_category_clean, sinon series_category s''il porte une valeur de '
    'la même liste. annee_fr : le premier volume à EAN français. '
    'auteurs : le scénariste puis le dessinateur, tels quels, joints par une virgule, un '
    'même nom une seule fois ; un « inconnu » textuel vaut absence ; COLLECTIF reste. '
    'NULL : absent. Migrations 025 et 026.';
