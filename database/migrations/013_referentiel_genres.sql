-- 013 — le référentiel de genres : des CODES d'un côté, des LIBELLÉS de l'autre.
--
-- CE QUE CETTE MIGRATION RÉPARE, INDIRECTEMENT
--
-- `ms_series_enriched.series_genres` porte 99 libellés bruts de Manga Sanctuary,
-- en français, à la casse incohérente (`romance` mais `Tranche de vie`), avec
-- des doublons de concept (`SF` / `science fiction` / `anticipation`) et des
-- non-genres (`Manga`, `Recueil`, `Art Book`). `series_genres_enriched` en
-- porte 55 autres, en anglais, venus de Kitsu. Les deux vocabulaires ne
-- partagent que CINQ libellés exacts — `Ecchi`, `Fantasy`, `Mecha`, `Samurai`,
-- `Vampire` — des emprunts que le français n'a pas traduits. Fusionner ces deux
-- colonnes telles quelles produirait un vocabulaire bilingue de ~145 valeurs où
-- `romance` et `Romance` seraient deux genres sans lien.
--
-- D'où la décision : les colonnes dérivées porteront des CODES, jamais des
-- libellés, et un référentiel séparé portera les libellés.
--
-- POURQUOI DEUX TABLES ET PAS UNE
--
-- `genre_ref` répond « que veut dire ce code, et comment l'écrire à l'écran ».
-- `genre_mapping` répond « qu'a dit la source, et qu'en avons-nous fait ». Les
-- deux questions n'ont ni la même durée de vie ni le même auteur : le premier
-- est un choix de produit, stable ; le second est un journal d'arbitrage qui
-- s'allonge à chaque libellé nouveau apparu chez une source. Les mêler
-- obligerait à toucher au produit pour enregistrer une observation.
--
-- CE QUE `code` EST, ET N'EST PAS
--
-- Un identifiant : ASCII minuscule, snake_case, sans accent. `science_fiction`,
-- `tranche_de_vie`. Ce n'est PAS du français — c'est une clé stable qui survit
-- à un changement de libellé d'affichage, et qui ne casse pas un index, une URL
-- ou un export CSV. Le français vit dans `label_fr`, obligatoire parce que
-- c'est la langue d'affichage initiale ; `label_en` et `label_ja` existent dès
-- maintenant, facultatifs, pour que l'ajout d'une langue ne soit pas une
-- migration de plus.
--
-- L'AXE FORMAT — préfixe `format_`
--
-- Manga Sanctuary mêle des genres et des FORMATS dans le même champ :
-- `Histoires courtes` (471 séries), `Manga Gekiga`, `Roman graphique`,
-- `Carnet De Croquis` ; Kitsu y ajoute `Doujinshi`. Ce ne sont pas des genres,
-- mais les exclure perdrait un signal que les lecteurs utilisent. Ils reçoivent
-- donc des codes préfixés `format_`, et un `ordre` ≥ 900 qui les range après
-- tous les genres. La convention est portée par le CODE lui-même plutôt que par
-- une colonne `nature` : un filtre `code NOT LIKE 'format\_%'` suffit, et il
-- reste vrai dans un export où la table de référence n'est pas jointe.
--
-- CE QUE CETTE MIGRATION NE FAIT PAS
--
-- Elle ne touche à AUCUNE colonne de `ms_series_enriched`, et surtout pas aux
-- `*_enriched`. Poser le référentiel et appliquer la correspondance sont deux
-- gestes distincts : le premier est réversible d'un `DROP TABLE`, le second
-- réécrit 14 670 lignes. Le second attend que l'humain ait relu les 23 lignes
-- `inconnu` du rapport d'arbitrage.
--
-- Les DONNÉES ne sont pas ici non plus. Le dépôt sépare le schéma (migrations,
-- jouées une fois, checksum vérifié) des données de référence (CSV versionnés,
-- chargés par un CLI idempotent, rejouables). Les 72 codes et les 154 lignes de
-- correspondance vivent dans `database/donnees/genre_ref.csv` et
-- `genre_mapping.csv`, chargés par `identity.charger_genres`.

-- ---------------------------------------------------------------------------
-- manga.genre_ref — un code, ses libellés
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manga.genre_ref (
    -- Le code est la clé primaire, pas un identifiant de substitution : il est
    -- déjà stable, lisible dans un plan de requête, et il n'y a aucune raison
    -- d'ajouter un entier entre lui et ses références.
    code     text PRIMARY KEY
             CHECK (code ~ '^[a-z][a-z0-9_]*$'),

    -- La langue d'affichage initiale. NOT NULL : un code sans libellé français
    -- serait invisible à l'écran, donc inutilisable.
    label_fr text NOT NULL CHECK (label_fr <> ''),

    -- Présentes et facultatives : la place est réservée, le remplissage viendra
    -- (le japonais est renseigné là où il est certain, vide ailleurs — jamais
    -- une traduction devinée).
    label_en text,
    label_ja text,

    -- Ordre d'affichage. Il reflète le poids du code dans le catalogue, de
    -- sorte qu'une liste déroulante non triée reste utile ; les `format_*`
    -- commencent à 900. Espacé de 10 pour qu'une insertion n'oblige pas à
    -- renuméroter.
    ordre    int
);

COMMENT ON TABLE manga.genre_ref IS
    'Référentiel des genres : un code neutre, ses libellés par langue. '
    'Les codes préfixés « format_ » ne sont pas des genres mais des formats '
    'éditoriaux, rangés après par la colonne ordre.';

-- ---------------------------------------------------------------------------
-- manga.genre_mapping — ce que la source a dit, ce que nous en avons fait
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manga.genre_mapping (
    -- La source, parce qu'un même libellé ne veut pas dire la même chose des
    -- deux côtés : `Fantasy` chez MS cohabite avec `fantastique`, ce qui n'est
    -- pas le cas chez Kitsu.
    source       text NOT NULL CHECK (source IN ('ms', 'kitsu')),

    -- Le libellé TEL QU'OBSERVÉ, casse comprise. Ni normalisé, ni corrigé :
    -- c'est la clé de rapprochement avec la donnée réelle, et « Ecole » n'est
    -- pas « École ». La faute d'accent de la source est la valeur juste ici.
    libelle_brut text NOT NULL,

    code         text REFERENCES manga.genre_ref(code),

    -- Trois statuts, et trois seulement :
    --   mappe   — traduit ou consolidé vers un code ;
    --   exclu   — ce n'est pas un genre (`Manga`, `Recueil`, `Tout public`) ;
    --   inconnu — aucune décision possible sans arbitrage humain. C'est une
    --             réponse à part entière, pas un trou : un libellé ambigu
    --             rangé de force sous un code fabrique une donnée fausse que
    --             plus rien ne signale.
    statut       text NOT NULL CHECK (statut IN ('mappe', 'exclu', 'inconnu')),

    -- Obligatoire en pratique pour `inconnu` et pour toute consolidation : la
    -- note dit POURQUOI. Elle n'est pas contrainte au niveau SQL parce qu'une
    -- ligne évidente (`romance` -> `romance`) n'a rien à justifier.
    note         text,

    PRIMARY KEY (source, libelle_brut),

    -- L'INVARIANT CENTRAL : un libellé est mappé si et seulement s'il porte un
    -- code. Écrit comme une égalité de booléens plutôt qu'en deux CHECK
    -- séparés — la double implication est le contrat, et une seule expression
    -- le rend indivisible. Il ferme les deux dérives symétriques : un `mappe`
    -- sans code (une décision annoncée mais pas prise) et un `exclu` ou
    -- `inconnu` qui porterait un code (une décision prise en douce sous un
    -- statut qui prétend le contraire).
    CHECK ((statut = 'mappe') = (code IS NOT NULL))
);

COMMENT ON TABLE manga.genre_mapping IS
    'Correspondance libellé brut -> code, une ligne par libellé distinct '
    'observé (99 pour ms, 55 pour kitsu). Journal d''arbitrage : le statut '
    'inconnu est une décision, pas une absence de décision.';

-- Le sens de lecture le plus fréquent est « quels libellés portent ce code »
-- (contrôle de couverture, rapport d'arbitrage). La PK sert l'autre sens.
CREATE INDEX IF NOT EXISTS idx_genre_mapping_code
    ON manga.genre_mapping (code);

-- 012 a posé un `ALTER DEFAULT PRIVILEGES` qui couvre déjà ces deux tables —
-- mais SEULEMENT parce que la migration est jouée par `postgres`, le rôle qui
-- a exécuté cet ALTER. Ce GRANT explicite rend le droit vrai quel que soit le
-- rôle qui rejoue les migrations, sur une base neuve comme sur `apimanga`. Il
-- est redondant dans le cas nominal ; il n'est pas gratuit dans les autres.
-- SELECT seulement, comme en 012.
GRANT SELECT ON manga.genre_ref, manga.genre_mapping TO manga_ro;
