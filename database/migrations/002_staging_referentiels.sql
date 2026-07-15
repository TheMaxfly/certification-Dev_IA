-- 002 — Tables de staging des référentiels externes.
--
-- Pattern maison, délibéré : le staging est une zone d'atterrissage brute.
--   - TEXT partout : un fichier source ne doit jamais faire échouer un
--     chargement sur une question de type. Le typage se fait à la PROMOTION
--     (INSERT SELECT vers les tables du schéma manga), là où l'on peut décider
--     quoi faire d'une valeur aberrante.
--   - Aucune contrainte forte, aucune FK, aucun index : ces tables sont
--     tronquées et rechargées à chaque cycle mensuel.
--   - Deux colonnes techniques partout : loaded_at et source_file, pour savoir
--     quel fichier a produit quelle ligne.
--
-- Les colonnes sont calquées sur les en-têtes RÉELS des fichiers sources, pas
-- sur leur documentation.

-- ---------------------------------------------------------------------------
-- Pivot Wikidata — colonnes = en-têtes exacts des CSV de wikidata_dump.py
-- ---------------------------------------------------------------------------

-- wd_pivot.csv : qid,mal_id,anilist_id
CREATE TABLE IF NOT EXISTS staging.wd_pivot (
    qid         TEXT,
    mal_id      TEXT,
    anilist_id  TEXT,
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    source_file TEXT
);

-- wd_entities.csv : qid,label_principal,annee,mal_id,anilist_id,ann_id,wiki_fr,wiki_en
CREATE TABLE IF NOT EXISTS staging.wd_entities (
    qid             TEXT,
    label_principal TEXT,
    annee           TEXT,
    mal_id          TEXT,
    anilist_id      TEXT,
    ann_id          TEXT,
    wiki_fr         TEXT,
    wiki_en         TEXT,
    loaded_at       timestamptz NOT NULL DEFAULT now(),
    source_file     TEXT
);

-- wd_formes.csv : qid,forme_normalisee,forme_originale,langue,type
CREATE TABLE IF NOT EXISTS staging.wd_formes (
    qid              TEXT,
    forme_normalisee TEXT,
    forme_originale  TEXT,
    langue           TEXT,
    type             TEXT,
    loaded_at        timestamptz NOT NULL DEFAULT now(),
    source_file      TEXT
);

-- wd_auteurs.csv : qid,auteur_qid
CREATE TABLE IF NOT EXISTS staging.wd_auteurs (
    qid         TEXT,
    auteur_qid  TEXT,
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    source_file TEXT
);

-- ---------------------------------------------------------------------------
-- Formes Kitsu
-- ---------------------------------------------------------------------------
-- subtype est CONSERVÉ en staging et filtré à la promotion : 34,3 % du
-- catalogue Kitsu est hors cible (dont 15 224 light novels). Ne garder que
-- manga/manhwa/manhua ; exclure novel/oneshot/doujin/oel. Filtrer ici ferait
-- perdre la trace de ce qui a été écarté, et donc la mesure du filtre.
CREATE TABLE IF NOT EXISTS staging.kitsu_formes (
    kitsu_id         TEXT,
    forme_normalisee TEXT,
    forme_originale  TEXT,
    langue           TEXT,
    type             TEXT,
    subtype          TEXT,
    loaded_at        timestamptz NOT NULL DEFAULT now(),
    source_file      TEXT
);

-- ---------------------------------------------------------------------------
-- Manga Insight — un seul parquet (59 062 x 43), DEUX populations
-- ---------------------------------------------------------------------------
-- La partition se lit dans les taux de remplissage, et se décide sur
-- « Original Url » : vide => population A (grain sortie/volume, 48 900 lignes),
-- rempli => population B (grain série, 10 162 lignes, issue du crawl
-- Manga-News). Mesuré sur le fichier réel, pas déduit de la doc.
--
-- Chaque table ne porte que les colonnes effectivement alimentées pour sa
-- population (taux > 0 %). Repères utiles :
--   - « Ean » : 96,5 % en A, 0 % en B — l'EAN appartient au grain sortie.
--   - A utilise « Titre », B utilise « Title » : deux colonnes distinctes.
--   - « Unnamed: 19 » est vide à 100 % dans tout le fichier : seule colonne
--     des 43 écartée des deux tables.
--   - « Dessin » / « Scénario » (3,2 % en A) sont conservés bien
--     qu'inutilisables comme disambiguateur : le staging n'arbitre pas.
--
-- Règle de nommage appliquée aux en-têtes : minuscules, accents retirés,
-- séparateurs -> « _ », et préfixe « _ » du parquet -> « meta_ ».

-- Correspondance colonne parquet -> colonne SQL (mi_sorties) :
--   'Titre VO'                             -> titre_vo
--   'Éditeur VF'                           -> editeur_vf
--   'Éditeur VO'                           -> editeur_vo
--   'Type'                                 -> type
--   'Genre 1'                              -> genre_1
--   'Genre 2'                              -> genre_2
--   'Statut VF'                            -> statut_vf
--   'Statut VO'                            -> statut_vo
--   'Pays'                                 -> pays
--   "Année pays d'origine"                 -> annee_pays_d_origine
--   'Date sortie France'                   -> date_sortie_france
--   '_catégorie'                           -> meta_categorie
--   '_fichier'                             -> meta_fichier
--   '_année_fichier'                       -> meta_annee_fichier
--   '_mois_fichier'                        -> meta_mois_fichier
--   'Date sortie France - année'           -> date_sortie_france_annee
--   'Date sortie France - mois'            -> date_sortie_france_mois
--   'Tomes VF'                             -> tomes_vf
--   'Tomes VO'                             -> tomes_vo
--   'Unnamed: 0'                           -> unnamed_0
--   'Titre'                                -> titre
--   'Ean'                                  -> ean
--   '_nouveauté'                           -> meta_nouveaute
--   '_nouvelle_édition'                    -> meta_nouvelle_edition
--   '_coffret'                             -> meta_coffret
--   '_collector'                           -> meta_collector
--   '_type_titre'                          -> meta_type_titre
--   '_type_source'                         -> meta_type_source
--   '_doublon_éditeur'                     -> meta_doublon_editeur
--   '_éditeurs_doublons'                   -> meta_editeurs_doublons
--   'Dessin'                               -> dessin
--   'Scénario'                             -> scenario
CREATE TABLE IF NOT EXISTS staging.mi_sorties (
    titre_vo                 TEXT,
    editeur_vf               TEXT,
    editeur_vo               TEXT,
    type                     TEXT,
    genre_1                  TEXT,
    genre_2                  TEXT,
    statut_vf                TEXT,
    statut_vo                TEXT,
    pays                     TEXT,
    annee_pays_d_origine     TEXT,
    date_sortie_france       TEXT,
    meta_categorie           TEXT,
    meta_fichier             TEXT,
    meta_annee_fichier       TEXT,
    meta_mois_fichier        TEXT,
    date_sortie_france_annee TEXT,
    date_sortie_france_mois  TEXT,
    tomes_vf                 TEXT,
    tomes_vo                 TEXT,
    unnamed_0                TEXT,
    titre                    TEXT,
    ean                      TEXT,
    meta_nouveaute           TEXT,
    meta_nouvelle_edition    TEXT,
    meta_coffret             TEXT,
    meta_collector           TEXT,
    meta_type_titre          TEXT,
    meta_type_source         TEXT,
    meta_doublon_editeur     TEXT,
    meta_editeurs_doublons   TEXT,
    dessin                   TEXT,
    scenario                 TEXT,
    loaded_at                timestamptz NOT NULL DEFAULT now(),
    source_file              TEXT
);

-- Correspondance colonne parquet -> colonne SQL (mi_series) :
--   'Original Url'                         -> original_url
--   'Adresse'                              -> adresse
--   'Code HTTP'                            -> code_http
--   'Title'                                -> title
--   'Titre VO'                             -> titre_vo
--   'Adresse.1'                            -> adresse_1
--   'Titre traduit'                        -> titre_traduit
--   'Éditeur VF'                           -> editeur_vf
--   'Éditeur VO'                           -> editeur_vo
--   'Type'                                 -> type
--   'Genre 1'                              -> genre_1
--   'Genre 2'                              -> genre_2
--   'Prépublication '                      -> prepublication
--   'Nombre tomes VF'                      -> nombre_tomes_vf
--   'Nombre tomes VO'                      -> nombre_tomes_vo
--   'Statut VF'                            -> statut_vf
--   'Statut VO'                            -> statut_vo
--   'Pays'                                 -> pays
--   "Année pays d'origine"                 -> annee_pays_d_origine
--   'Date sortie France'                   -> date_sortie_france
--   'Année'                                -> annee
--   '_catégorie'                           -> meta_categorie
--   '_fichier'                             -> meta_fichier
--   'Date sortie France - année'           -> date_sortie_france_annee
--   'Date sortie France - mois'            -> date_sortie_france_mois
--   'Tomes VF'                             -> tomes_vf
--   'Tomes VO'                             -> tomes_vo
--   '_nouveauté'                           -> meta_nouveaute
--   '_nouvelle_édition'                    -> meta_nouvelle_edition
--   '_coffret'                             -> meta_coffret
--   '_collector'                           -> meta_collector
--   '_type_titre'                          -> meta_type_titre
--   '_type_source'                         -> meta_type_source
--   '_doublon_éditeur'                     -> meta_doublon_editeur
--   '_éditeurs_doublons'                   -> meta_editeurs_doublons
CREATE TABLE IF NOT EXISTS staging.mi_series (
    original_url             TEXT,
    adresse                  TEXT,
    code_http                TEXT,
    title                    TEXT,
    titre_vo                 TEXT,
    adresse_1                TEXT,
    titre_traduit            TEXT,
    editeur_vf               TEXT,
    editeur_vo               TEXT,
    type                     TEXT,
    genre_1                  TEXT,
    genre_2                  TEXT,
    prepublication           TEXT,
    nombre_tomes_vf          TEXT,
    nombre_tomes_vo          TEXT,
    statut_vf                TEXT,
    statut_vo                TEXT,
    pays                     TEXT,
    annee_pays_d_origine     TEXT,
    date_sortie_france       TEXT,
    annee                    TEXT,
    meta_categorie           TEXT,
    meta_fichier             TEXT,
    date_sortie_france_annee TEXT,
    date_sortie_france_mois  TEXT,
    tomes_vf                 TEXT,
    tomes_vo                 TEXT,
    meta_nouveaute           TEXT,
    meta_nouvelle_edition    TEXT,
    meta_coffret             TEXT,
    meta_collector           TEXT,
    meta_type_titre          TEXT,
    meta_type_source         TEXT,
    meta_doublon_editeur     TEXT,
    meta_editeurs_doublons   TEXT,
    loaded_at                timestamptz NOT NULL DEFAULT now(),
    source_file              TEXT
);

