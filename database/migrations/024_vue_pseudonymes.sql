-- 024 — les pseudonymes, lisibles par l'API du modèle : la vue `manga.v_pseudonymes`.
--
-- POURQUOI UNE VUE
--
-- La route de réponse de l'API du modèle (module 11, E3 étape 3) contrôle chaque sortie
-- du modèle de langage : aucun nom de membre (décision du 26 octobre 2026, § 7 bis du
-- document de décisions de l'étape). Il lui faut la liste des pseudonymes ; `modele_ro`
-- (023) ne lit pas `manga.ms_reviews_all`, et ne doit pas la lire : il pourrait relier
-- un nom à ses critiques. La vue rend les noms DISTINCTS, rien d'autre : l'API voit les
-- pseudonymes, jamais qui a écrit quelle critique.
--
-- Écartés : le droit sur la colonne `review_author` de la table (le lien nom → critique
-- resterait lisible) ; un fichier de noms hors dépôt (une pièce de plus à protéger et à
-- déployer).
--
-- PÉRIMÈTRE — une vue, un droit : `SELECT` sur la vue pour `modele_ro`. Aucun droit sur
-- la table, aucune colonne de plus, aucun rôle créé.
--
-- ÉLÉVATION DÉCLARÉE. La vue n'est pas `security_invoker` : elle lit
-- `manga.ms_reviews_all` avec les droits de son propriétaire. Par elle seule,
-- `modele_ro` voit les noms distincts, jamais la table (même construction que
-- `bench.v_encodage_en_service`, 021, déclarée par 023).
--
-- CE QUE 024 NE CHANGE PAS. `manga_ro` (012) et `verif_ro` (022) lisent tout `manga`
-- par leurs droits par défaut : la vue leur est lisible dès sa création, sans rien leur
-- ouvrir de neuf (ils lisent déjà la table).

CREATE VIEW manga.v_pseudonymes AS
SELECT DISTINCT review_author
  FROM manga.ms_reviews_all
 WHERE review_author IS NOT NULL;

COMMENT ON VIEW manga.v_pseudonymes IS
    'Les pseudonymes distincts des auteurs de critiques, pour le contrôle « aucun nom '
    'de membre » de l''API du modèle (module 11) ; lisible par modele_ro, qui ne lit '
    'pas la table. Migration 024.';

GRANT SELECT ON manga.v_pseudonymes TO modele_ro;
