-- 025 — les métadonnées d'une série, lisibles par l'API du modèle : la vue
-- `manga.v_metadonnees_serie`.
--
-- POURQUOI UNE VUE
--
-- La route de réponse de l'API du modèle (module 11, E3 étape 3) empêche le modèle de
-- langage d'inventer des œuvres, pas d'affirmer des métadonnées : disponibilité, nombre
-- de tomes, statut, public. Elles ne sont ni dans les passages ni sur la ligne de titre,
-- alors que le catalogue en possède la plupart. La vue les rend par série, calculées à
-- la lecture, sans table ni donnée nouvelle ; `modele_ro` (023) ne lit ni les volumes ni
-- le reste de la fiche, et n'en a pas besoin.
--
-- LES RÈGLES, arrêtées sur les comptes d'`apimanga` (2026-11-02 : 14 670 séries,
-- 104 107 lignes de volumes). Une valeur absente est NULL.
--
-- `disponibilite` :
--   edite_en_france    au moins un volume porte un EAN français (préfixes 9782 et 97910,
--                      97911 est la Corée), ou la fiche porte le badge de pays
--                      « français » de `series_statuses` ;
--   seulement_au_japon la série est de type Manga, a des volumes, et rien de cela ;
--   inconnue           sinon : aucun volume, ou un manhwa, un manhua, une BD ou un comics
--                      sans édition française, qui n'est pas pour autant « au Japon ».
--   « Édité en France » ne veut pas dire « en stock » : le catalogue ne connaît pas les
--   stocks. `volume_editeur` et `volume_country` ne servent pas : le chargement n'y a mis
--   que des libellés de page. `volume_publication_date` non plus, seule : elle date toutes
--   les éditions, japonaises comprises.
--
-- `nb_volumes` : le plus grand de deux comptes, 0 valant absence pour le premier.
--   le champ    le plus grand « tomes parus » (`volume_tomes_published`) des volumes de la
--               série. Il ne dit pas de quelle édition il parle, et il est plus petit que
--               les numéros sur 112 séries (Ippo : 30 pour 136 ; Black Lagoon : 1 pour 13) ;
--   les numéros les numéros de volume distincts, un volume sans numéro comptant comme le
--               volume 1. Ils mêlent les éditions (Akira : 31 pour un champ à 6), comptent
--               un numéro qui est une année (Blue : 2018) et sous-comptent une série dont
--               le catalogue n'a qu'une partie des tomes.
--   Sur ces 112 séries, la vue s'écarte de la définition d du jeu d'évaluation (le champ,
--   sinon les numéros), qui reste celle du jeu gelé.
--
-- `nb_volumes_source` : ce que vaut ce nombre.
--   divergent               les deux comptes existent et s'écartent de plus de 20 % du
--                           plus grand ;
--   champ                   sinon, le champ est au moins égal aux numéros ;
--   repli_volumes_distincts sinon : les numéros sont plus grands, ou le champ est absent.
--   Toute source autre que `champ` se lit comme un minimum : « au moins N ».
--
-- `statut` :
--   termine  le badge « Complète » de `series_statuses`, français ou non ; sinon, le statut
--            Kitsu `finished` de la série rattachée par le moyeu ;
--   en_cours le statut Kitsu `current`, sans badge.
--   Le catalogue n'écrit que « Complète » : l'absence de badge ne veut pas dire « en
--   cours ». Le badge passe avant Kitsu quand ils divergent : Manga Insight lui donne
--   raison sur les 11 séries où il tranche (2026-11-02).
--
-- `demographie` : `series_category_clean`, sinon `series_category` quand il porte une
--   valeur de la même liste, les trois catégories coréennes ramenées comme elles le sont
--   dans la colonne propre (Sonyun → Shonen, Sunjung → Shojo, Chungnyun → Seinen). Le
--   libellé du catalogue, tel quel : « Yaoi » ou « Ecchi-Hentai » disent quelque chose
--   qu'un « autre » tairait. Une année dans `series_category` (décalage du chargement)
--   vaut absence.
--
-- `annee_fr` : l'année du premier volume à EAN français. Plus étroit que le
--   MIN(volume_publication_date) du jeu d'évaluation, qui prend aussi les éditions
--   japonaises quand la fiche en a.
--
-- PÉRIMÈTRE — une vue, un droit : `SELECT` sur la vue pour `modele_ro`. Aucun droit sur
-- les tables qu'elle lit, aucune table, aucune donnée, aucun rôle créés.
--
-- ÉLÉVATION DÉCLARÉE. La vue n'est pas `security_invoker` : elle lit les volumes, la
-- fiche, le moyeu et Kitsu avec les droits de son propriétaire. Par elle seule, `modele_ro`
-- voit les six valeurs, jamais les tables (même construction que `manga.v_pseudonymes`,
-- 024).
--
-- CE QUE 025 NE CHANGE PAS. `manga_ro` (012) et `verif_ro` (022) lisent tout `manga` par
-- leurs droits par défaut : la vue leur est lisible dès sa création, sans rien leur ouvrir
-- de neuf (ils lisent déjà ces tables).

CREATE VIEW manga.v_metadonnees_serie AS
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
           WHEN v.tomes_parus >= v.numeros_distincts THEN 'champ'
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
       v.annee_fr
  FROM manga.ms_series_enriched s
  LEFT JOIN LATERAL (
      SELECT count(*) AS lignes,
             nullif(max(vol.volume_tomes_published), 0) AS tomes_parus,
             nullif(count(DISTINCT coalesce(vol.volume_number, 1)), 0) AS numeros_distincts,
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
    'volume sans numéro comptant pour le 1, toutes éditions mêlées. '
    'nb_volumes_source : divergent si les deux existent et s''écartent de plus de 20 % du '
    'plus grand, champ si le champ est au moins égal aux numéros, repli_volumes_distincts '
    'sinon ; toute source autre que champ se lit comme un minimum, « au moins N ». '
    'statut : termine sur le badge « Complète » ou Kitsu finished, en_cours sur Kitsu '
    'current sans badge ; le catalogue n''écrit jamais « en cours ». '
    'demographie : series_category_clean, sinon series_category s''il porte une valeur de '
    'la même liste. annee_fr : le premier volume à EAN français. NULL : absent. '
    'Migration 025.';

GRANT SELECT ON manga.v_metadonnees_serie TO modele_ro;
