-- 010 — Le juge écrit ses AVIS ailleurs que dans le journal des décisions.
--
-- POURQUOI CE FICHIER EXISTE. La cascade mécanique est terminée : 7 434
-- identités auto, 1 932 dossiers en needs_review, 5 304 orphelines hors
-- périmètre. Ce qui reste ne se tranche plus par jointure — il demande un
-- jugement. L'étage R soumet ces dossiers à un LLM.
--
-- LA DÉCISION D'ARCHITECTURE QUE CETTE TABLE MATÉRIALISE. Au run 1, le juge
-- est en RÉGIME AVIS-SEULEMENT : il n'écrit rien dans match_decision ni dans
-- work_identity. Une table séparée n'est donc pas un détail de rangement,
-- c'est le mécanisme qui rend la règle INFRANCHISSABLE : tant que le code de
-- l'étage R n'écrit que dans manga.llm_avis, aucun bug, aucune relecture
-- distraite et aucun copier-coller ne peut transformer un avis en décision.
-- Le droit d'écriture au journal est une politique du run 2, conditionnée aux
-- mesures d'étalonnage et à l'échantillon C3 — pas une facilité qu'on se
-- laisse dès maintenant.
--
-- Corollaire : la valeur 'llm_review' ajoutée au CHECK de match_decision.method
-- en 009 reste INERTE. Elle a été posée par anticipation (une migration au lieu
-- de deux) ; elle ne sera écrite qu'au run 2, si le droit d'écriture est
-- accordé. Une valeur autorisée mais jamais écrite est un contrat en attente,
-- pas une dette.
--
-- CE QUE CETTE MIGRATION NE FAIT PAS. Elle ne touche ni match_decision, ni
-- work_identity, ni v_match_current. La refonte de la vue — pour qu'elle sache
-- lire un avis promu — attendra le run 2 et sa propre migration. Une migration
-- qui prépare un futur incertain fabrique du code mort.

-- ---------------------------------------------------------------------------
-- manga.llm_avis — un avis par (série × candidat × run × phase)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manga.llm_avis (
    avis_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- La série jugée. FK vers le socle MS : un avis sur une série inexistante
    -- n'a pas de sens, et le CASCADE suit la politique des tables d'identité.
    series_id      integer NOT NULL
                   REFERENCES manga.ms_series_enriched(series_id)
                   ON DELETE CASCADE,

    -- Quel run, quelle phase. `run_ts` horodate le lot ; `phase` dit à quel
    -- titre l'avis a été rendu. Les trois phases ne se mélangent jamais dans
    -- une mesure : l'étalonnage mesure le JUGE, la file produit des avis à
    -- arbitrer, l'échantillon mesure la CASCADE.
    run_ts         timestamptz NOT NULL,
    phase          TEXT NOT NULL
                   CHECK (phase IN ('etalonnage', 'file', 'echantillon')),

    -- Le candidat soumis. Deux référentiels coexistent dans la cascade, donc
    -- le couple (type, id) plutôt qu'une colonne par référentiel : l'étage 3
    -- propose déjà des cibles des deux côtés dans un même dossier.
    candidat_type  TEXT NOT NULL CHECK (candidat_type IN ('qid', 'kitsu_id')),
    candidat_id    TEXT NOT NULL,

    -- Le contrat de sortie du juge.
    --   verdict     : les trois seules issues admises. 'undecidable' est une
    --                 réponse à part entière — un juge qui n'a pas de quoi
    --                 trancher doit pouvoir le dire plutôt que deviner.
    --   confiance   : deux niveaux seulement, volontairement. Une échelle fine
    --                 inviterait à lire des nuances que rien ne calibre ; deux
    --                 niveaux se mesurent (l'étalonnage exige zéro faux
    --                 'same_work' en confiance haute).
    --   justification : une phrase. Elle sert à l'humain qui arbitre, pas à la
    --                 machine — et une justification longue se lit moins.
    verdict        TEXT NOT NULL
                   CHECK (verdict IN ('same_work', 'different_work',
                                      'undecidable')),
    confiance      TEXT NOT NULL CHECK (confiance IN ('haute', 'moyenne')),
    justification  TEXT,

    -- Traçabilité du jugement. `prompt_version` est la clé de stabilité du
    -- run : le modèle ne prend aucun paramètre d'échantillonnage (les sorties
    -- structurées les excluent), donc ce qui rend deux runs comparables est le
    -- couple (modele, prompt_version) — pas une température.
    modele         TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    tokens_in      integer,
    tokens_out     integer,

    -- Marqueurs de mesure, posés à l'écriture et lus par les rapports.
    --   pre_validation_bandes : les 67 séries du seau adjacent portant un
    --     second signal (§26.4). Leur taux de same_work PRÉ-VALIDE la
    --     politique adoptée, avant son premier usage.
    --   dossier_partiel : les 52 collisions de l'étage 1, non re-dérivables
    --     (§28.1). Le drapeau DIT l'incomplétude au lieu de laisser croire à
    --     un dossier complet — un avis rendu sur un dossier partiel ne pèse
    --     pas autant qu'un autre, et doit rester distinguable.
    pre_validation_bandes boolean NOT NULL DEFAULT false,
    dossier_partiel       boolean NOT NULL DEFAULT false,

    created_at     timestamptz NOT NULL DEFAULT now(),

    -- Idempotence du lot : rejouer un run n'empile pas les avis. La clé porte
    -- run_ts et phase, donc un NOUVEAU run peut ré-émettre un avis sur le même
    -- couple — c'est voulu : deux runs sont deux mesures, pas une correction.
    CONSTRAINT llm_avis_unicite
        UNIQUE (series_id, candidat_type, candidat_id, run_ts, phase)
);

COMMENT ON TABLE manga.llm_avis IS
    'Avis du juge LLM (étage R). RÉGIME AVIS-SEULEMENT au run 1 : cette table '
    'est la SEULE destination d''écriture du juge ; match_decision et '
    'work_identity restent vierges de l''étage R. Le droit d''écriture au '
    'journal est une politique du run 2, conditionnée aux mesures.';

COMMENT ON COLUMN manga.llm_avis.phase IS
    'etalonnage = cas fabriqués depuis des identités sûres, juge mesuré en '
    'aveugle AVANT de lire la vraie file ; file = les needs_review à arbitrer ; '
    'echantillon = contrôle a posteriori des AUTO (échantillon C3). Les trois '
    'ne se mélangent jamais dans une mesure.';

COMMENT ON COLUMN manga.llm_avis.prompt_version IS
    'Clé de stabilité du run. Le modèle ne prend aucun paramètre '
    'd''échantillonnage (exclu par les sorties structurées) : deux runs sont '
    'comparables par (modele, prompt_version), pas par une température.';

COMMENT ON COLUMN manga.llm_avis.pre_validation_bandes IS
    'Les 67 séries du seau adjacent portant un second signal (§26.4). Leur '
    'taux de same_work pré-valide la politique des bandes AVANT son premier '
    'usage — sans rétroactivité sur les décisions journalisées.';

COMMENT ON COLUMN manga.llm_avis.dossier_partiel IS
    'Les 52 collisions de l''étage 1, non re-dérivables (§28.1). Un avis rendu '
    'sur un dossier incomplet doit rester distinguable d''un avis rendu sur un '
    'dossier complet.';

-- Lecture par série : le chemin de l'arbitrage humain (« montre-moi tous les
-- avis sur cette série »).
CREATE INDEX IF NOT EXISTS llm_avis_series_idx
    ON manga.llm_avis (series_id);

-- Lecture par lot : le chemin des rapports (« ventile la phase X du run Y »).
CREATE INDEX IF NOT EXISTS llm_avis_run_phase_idx
    ON manga.llm_avis (run_ts, phase);

-- Lecture par verdict : le chemin de la projection run 2 (« combien de
-- same_work en confiance haute ? »), qui est LA mesure fondant la décision
-- d'accorder ou non le droit d'écriture.
CREATE INDEX IF NOT EXISTS llm_avis_verdict_confiance_idx
    ON manga.llm_avis (verdict, confiance);
