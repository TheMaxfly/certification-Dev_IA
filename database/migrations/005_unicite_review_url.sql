-- 005 — Unicité de manga.ms_reviews_all.review_url.
--
-- POURQUOI CE FICHIER EXISTE. La promotion des critiques est un upsert de clé
-- `review_url` (cycle mensuel : mettre à jour l'existant, ajouter le nouveau,
-- ne rien supprimer). Or `ON CONFLICT (review_url)` exige une contrainte
-- d'unicité sur cette colonne, et la table n'en avait pas : sa PK est
-- `review_id`, une séquence — un identifiant technique qui ne dit rien de
-- l'identité d'une critique et ne peut donc pas servir de clé de rapprochement.
--
-- Sans cet index, la promotion ne peut pas être un upsert : elle insérerait
-- 11 052 doublons à chaque cycle, ou exigerait un DELETE préalable — soit
-- exactement ce que la politique du cycle mensuel interdit.
--
-- L'unicité est un FAIT de la source, vérifié avant d'être imposé :
--   - en base (2025-12) : 6 749 lignes, 6 749 review_url distincts, 0 NULL ;
--   - dans le snapshot 2026-07 : 11 052 lignes, 11 052 review_url distincts.
-- L'index ne contraint donc pas les données existantes ; il rend explicite une
-- règle déjà vraie, pour que le moteur puisse s'y appuyer.
--
-- Index PARTIEL (WHERE review_url IS NOT NULL), par cohérence avec les index
-- d'identité de 001 : aucune ligne n'a de review_url NULL aujourd'hui, mais un
-- UNIQUE ordinaire laisserait de toute façon passer les NULL multiples. Le
-- partiel dit exactement ce qu'on veut et reste petit.
--
-- Migration ADDITIVE (CREATE INDEX seul). Politique inchangée : pas de `down`,
-- checksum immuable une fois appliquée (cf. README).

CREATE UNIQUE INDEX IF NOT EXISTS ms_reviews_all_review_url_uniq
    ON manga.ms_reviews_all (review_url)
    WHERE review_url IS NOT NULL;

COMMENT ON INDEX manga.ms_reviews_all_review_url_uniq IS
    'Clé de rapprochement des critiques (upsert du cycle mensuel). review_id '
    'est une séquence : identifiant technique, pas une identité.';
