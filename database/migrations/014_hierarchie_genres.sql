-- 014 — la hiérarchie des genres, et la nature des codes.
--
-- CE QUE LA HIÉRARCHIE SERT
--
-- Une série cataloguée `yaoi` est mieux documentée qu'une série cataloguée
-- `lgbt` : la source a été plus précise. Sans hiérarchie, un filtre sur le code
-- générique ne la trouve PAS — le filtre grossier rate les séries les mieux
-- décrites, ce qui est exactement l'inverse de ce qu'on attend. `parent` répare
-- cela : au recalcul, une série portant un code fin reçoit aussi ses ancêtres.
-- La dérivation se fait à l'ÉCRITURE (dans `identity.enrichir`), pas à la
-- lecture : une requête de filtrage n'a pas à connaître l'arbre, et un index
-- GIN sur la colonne dérivée reste utilisable.
--
-- POURQUOI LA CLÉ ÉTRANGÈRE EST `DEFERRABLE`
--
-- `parent` référence `genre_ref(code)` : la table se référence elle-même. Le
-- chargeur écrit les 73 codes en UN seul `INSERT ... SELECT unnest(...)`, et
-- rien n'ordonne les lignes d'un `unnest` — si `yaoi` est inséré avant `lgbt`,
-- une FK immédiate refuse la ligne. Trois issues possibles : trier le CSV par
-- profondeur (fragile, et un tri de fichier n'est pas un contrat), charger en
-- deux passes (du code pour un problème de contrainte), ou différer la
-- vérification à la fin de la transaction. La troisième est la seule qui décrit
-- le vrai invariant : ce n'est pas l'ORDRE d'écriture qui doit être correct,
-- c'est l'ÉTAT à la validation.
--
-- CE QUE `type` DIT, ET CE QUE LE CODE NE PEUT PAS DIRE SEUL
--
-- 013 distinguait les formats par un préfixe `format_` : lisible, et vrai même
-- dans un export où la table n'est pas jointe. Mais un préfixe ne se contrôle
-- pas — rien n'empêche un code `format_x` d'être créé sans intention, ni un
-- format d'arriver sans préfixe. `type` rend la nature déclarative et
-- contrôlable par un CHECK. Le préfixe est conservé : les deux se valident
-- mutuellement, et un test le vérifie.
--
-- PRÉCISION SUR LE VOCABULAIRE CIBLE. Les 8 codes `format_*` ne sont PAS hors
-- des sources : chacun est atteint depuis un libellé brut réel (`Histoires
-- courtes` -> `format_histoires_courtes`, `Doujinshi` -> `format_doujinshi`).
-- Ils portent donc bien une ligne de `genre_mapping`. Le seul code du
-- référentiel qui n'en porte aucune est `adulte`, introduit par 014 comme
-- parent commun de `ecchi` / `erotique` / `hentai` : lui seul appartient au
-- vocabulaire cible sans être le reflet d'un libellé observé. La règle qui
-- remplace « tout code a une correspondance » est donc : **un code sans
-- correspondance doit être le parent d'au moins un code mappé** — sinon c'est
-- du vocabulaire mort. Elle est tenue par un test, pas par une contrainte : elle
-- porte sur deux tables dont l'une se charge après l'autre.
--
-- CE QUE CETTE MIGRATION NE FAIT PAS. Elle ne touche pas à
-- `ms_series_enriched`. Poser l'arbre et recalculer les colonnes dérivées sont
-- deux gestes distincts ; le second est le travail d'`identity.enrichir`.

-- ---------------------------------------------------------------------------
-- 1. La nature du code
-- ---------------------------------------------------------------------------
ALTER TABLE manga.genre_ref
    ADD COLUMN IF NOT EXISTS type text NOT NULL DEFAULT 'genre';

-- Ajouté séparément : sur une table déjà peuplée, la colonne prend d'abord son
-- DEFAULT sur toutes les lignes, et le CHECK ne peut valider qu'ensuite.
ALTER TABLE manga.genre_ref
    DROP CONSTRAINT IF EXISTS genre_ref_type_check;
ALTER TABLE manga.genre_ref
    ADD CONSTRAINT genre_ref_type_check CHECK (type IN ('genre', 'format'));

-- Rattrapage des lignes DÉJÀ chargées : sur une base neuve, `genre_ref` est
-- vide au moment où 014 s'applique et cet UPDATE ne touche rien — c'est le CSV
-- qui portera la valeur. Sur `apimanga`, les 72 codes de 013 sont là et les 8
-- formats doivent basculer sans attendre un rechargement. Les deux chemins
-- convergent, et l'ordre dans lequel on les emprunte n'a pas d'importance.
UPDATE manga.genre_ref SET type = 'format'
 WHERE code LIKE 'format\_%' AND type <> 'format';

COMMENT ON COLUMN manga.genre_ref.type IS
    'genre | format. Redondant avec le préfixe « format_ » du code, et c''est '
    'voulu : le préfixe reste lisible sans jointure, le type reste contrôlable '
    'par une contrainte. Un test vérifie qu''ils ne divergent jamais.';

-- ---------------------------------------------------------------------------
-- 2. La parenté
-- ---------------------------------------------------------------------------
ALTER TABLE manga.genre_ref
    ADD COLUMN IF NOT EXISTS parent text;

ALTER TABLE manga.genre_ref
    DROP CONSTRAINT IF EXISTS genre_ref_parent_fkey;
ALTER TABLE manga.genre_ref
    ADD CONSTRAINT genre_ref_parent_fkey
        FOREIGN KEY (parent) REFERENCES manga.genre_ref(code)
        DEFERRABLE INITIALLY DEFERRED;

-- Le cycle de longueur 1 est le seul qu'une contrainte de ligne sait voir :
-- `a -> b -> a` demande de parcourir l'arbre, ce qu'un CHECK ne peut pas faire
-- (il ne lit que la ligne courante). Le reste — absence de cycle, profondeur
-- <= 3 — est tenu par un test sur le contenu réel, seul endroit où la question
-- se pose vraiment.
ALTER TABLE manga.genre_ref
    DROP CONSTRAINT IF EXISTS genre_ref_parent_pas_soi_meme;
ALTER TABLE manga.genre_ref
    ADD CONSTRAINT genre_ref_parent_pas_soi_meme
        CHECK (parent IS NULL OR parent <> code);

COMMENT ON COLUMN manga.genre_ref.parent IS
    'Code plus général. Au recalcul, une série portant ce code reçoit aussi '
    'ses ancêtres, transitivement : un filtre sur le générique doit trouver '
    'les séries que la source a décrites plus finement.';

-- Le sens de lecture de la dérivation est « quels sont mes ancêtres », donc du
-- code vers son parent — mais la récursion descendante (« qui sont mes
-- enfants ») est celle des contrôles de couverture.
CREATE INDEX IF NOT EXISTS idx_genre_ref_parent ON manga.genre_ref (parent);
