-- Promotion du staff Kitsu : staging.kitsu_staff -> manga.kitsu_staff.
--
-- Une table temporaire est fournie par le pilote Python AVANT ce script :
--   staff_norm (personne, personne_norm) — les noms distincts du staging,
--     normalisés par identity.normaliser(). JAMAIS de normalisation SQL ad hoc :
--     l'étage 2 compare cette colonne aux auteurs MS normalisés par la MÊME
--     fonction. Deux implémentations différentes renverraient « pas de match »
--     sur des noms identiques, en silence.
--
-- DEUX FILTRES, appliqués ici et pas au chargement :
--   1. subtype cible — via manga.kitsu_meta, qui ne contient que
--      {manga, manhwa, manhua}. Un light novel n'a rien à faire dans les
--      confirmateurs d'auteur de la cascade (15 224 d'entre eux attendent).
--   2. nom vide ou non normalisable — un nom qui se normalise en chaîne vide
--      ne peut rien confirmer ; il est écarté, et compté.
--
-- Sans paramètre : exécutable en un seul execute() psycopg.

INSERT INTO manga.kitsu_staff (kitsu_id, personne, personne_norm, role)
SELECT DISTINCT
    s.kitsu_id::bigint,
    s.personne,
    n.personne_norm,
    s.role
FROM staging.kitsu_staff s
JOIN staff_norm n ON n.personne = s.personne
-- Le filtre subtype : kitsu_meta est la cible, par construction.
JOIN manga.kitsu_meta m ON m.kitsu_id = s.kitsu_id::bigint
WHERE s.kitsu_id IS NOT NULL
  AND s.personne IS NOT NULL
  AND n.personne_norm <> ''
-- Idempotence : un rechargement ne duplique rien. La clé (kitsu_id,
-- personne_norm, role) laisse coexister un auteur crédité Story ET Art.
ON CONFLICT (kitsu_id, personne_norm, role) DO NOTHING;
