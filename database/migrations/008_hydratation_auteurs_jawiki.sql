-- 008 — Hydratation des auteurs Wikidata + sitelinks jawiki (étape D0).
--
-- Trois évolutions ADDITIVES, préalables à l'étage 1 de la cascade (matching
-- par titre, désambiguïsation auteur/année) :
--
--   1. manga.wd_pivot.wiki_ja   — 3e langue de sitelink, comme wiki_fr/wiki_en.
--   2. manga.wd_auteurs.auteur_lang — la langue du nom d'auteur retenu.
--   3. manga.wd_auteurs_formes  — TOUTES les formes d'un auteur (natif +
--      romanisation + alias), pour la désambiguïsation, comme les autres
--      tables *_formes.
--
-- Politique inchangée : additive, IF NOT EXISTS, aucune donnée chargée ici —
-- l'hydratation est le travail des modules identity/hydrater_auteurs et
-- identity/reparse_jawiki.

-- ---------------------------------------------------------------------------
-- 1. wd_pivot.wiki_ja — sitelink japonais, même famille que wiki_fr/wiki_en.
-- ---------------------------------------------------------------------------
-- Les 165 lots d'entités du 2026-07-14 portent déjà sitelinks['jawiki'] : c'est
-- un re-parse LOCAL, jamais un re-téléchargement. Manga Sanctuary étant un
-- catalogue VF de titres japonais, la couverture ja devrait dépasser en (44,6 %)
-- et fr (22,0 %) — hypothèse à confronter au chiffre.
ALTER TABLE manga.wd_pivot ADD COLUMN IF NOT EXISTS wiki_ja TEXT;

COMMENT ON COLUMN manga.wd_pivot.wiki_ja IS
    'Titre de la page jawiki (sitelink). Re-parsé localement des entités '
    '2026-07-14, jamais re-téléchargé. Même famille que wiki_fr/wiki_en.';

-- ---------------------------------------------------------------------------
-- 2. wd_auteurs.auteur_lang — la langue du nom retenu.
-- ---------------------------------------------------------------------------
-- Le nom est choisi par priorité ja > en > fr > premier disponible : le nom
-- natif est le meilleur disambiguateur contre le staff Kitsu. Stocker la langue
-- retenue rend le choix auditable et permet de comparer ce qui est comparable.
ALTER TABLE manga.wd_auteurs ADD COLUMN IF NOT EXISTS auteur_lang TEXT;

COMMENT ON COLUMN manga.wd_auteurs.auteur_lang IS
    'Langue du nom d''auteur retenu (ja|en|fr|…), pour audit du choix de nom.';

-- ---------------------------------------------------------------------------
-- 3. wd_auteurs_formes — toutes les formes d'un auteur, pour le matching.
-- ---------------------------------------------------------------------------
-- Un auteur porte souvent DEUX formes utiles : son nom natif (荒木飛呂彦) et sa
-- romanisation (Hirohiko Araki). Les DEUX servent la désambiguïsation — l'une
-- contre le staff natif de Kitsu, l'autre contre les catalogues latins. Stocker
-- seulement le nom retenu sur wd_auteurs perdrait la moitié du signal.
--
-- Grain = auteur (auteur_qid), PAS (œuvre, auteur) : les formes appartiennent à
-- l'auteur, pas au lien œuvre↔auteur. auteur_qid n'est pas un qid d'œuvre, donc
-- AUCUNE FK vers wd_pivot (ce serait une erreur de référence). Pour le reste,
-- structure et outillage IDENTIQUES à wd_formes / kitsu_formes : forme_norm par
-- identity.normaliser(), UNIQUE (auteur_qid, forme_norm), btree + GIN trigramme.
CREATE TABLE IF NOT EXISTS manga.wd_auteurs_formes (
    forme_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    auteur_qid TEXT NOT NULL,
    forme      TEXT NOT NULL,
    forme_norm TEXT NOT NULL,
    forme_type TEXT NOT NULL CHECK (forme_type IN ('label', 'alias')),
    langue     TEXT NULL,
    loaded_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (auteur_qid, forme_norm)
);

CREATE INDEX IF NOT EXISTS wd_auteurs_formes_forme_norm_idx
    ON manga.wd_auteurs_formes (forme_norm);
CREATE INDEX IF NOT EXISTS wd_auteurs_formes_forme_norm_trgm_idx
    ON manga.wd_auteurs_formes USING gin (forme_norm public.gin_trgm_ops);
CREATE INDEX IF NOT EXISTS wd_auteurs_formes_auteur_qid_idx
    ON manga.wd_auteurs_formes (auteur_qid);

COMMENT ON TABLE manga.wd_auteurs_formes IS
    'Formes des auteurs Wikidata (natif + romanisation + alias), grain auteur. '
    'auteur_qid n''est pas un qid d''œuvre : aucune FK vers wd_pivot. '
    'Même normalisation et même outillage d''index que wd_formes.';
