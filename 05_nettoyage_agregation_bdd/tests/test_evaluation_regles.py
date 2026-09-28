"""`regle:` — les attendus de F9 et F10, dérivés par une règle SQL versionnée.

Deux contraintes (2026-09-29) : la règle n'emploie que des colonnes, jamais les
mots de la question ; elle plafonne son résultat. Les contrôles statiques sont
éprouvés ici sans base ; l'exécution, sur base JETABLE.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evaluation import regles  # noqa: E402

VALIDE = """
-- Les shonen les mieux notés, plafonnés à 20.
SELECT series_id
FROM manga.ms_series_enriched
WHERE series_category_clean = 'Shonen'
ORDER BY series_members_rating DESC NULLS LAST, series_id
LIMIT 20;
"""


def test_regle_valide():
    assert regles.controler(VALIDE) == 20


def test_une_colonne_egale_a_un_mot_de_la_question_est_permise():
    """« un bon shonen » filtré par `= 'Shonen'` : une colonne, pas une recherche."""
    assert regles.controler(VALIDE.replace("'Shonen'", "'LIKE; ~'")) == 20


@pytest.mark.parametrize(
    ("sql", "motif"),
    [
        (VALIDE.replace("= 'Shonen'", "ILIKE '%shonen%'"), "recherche textuelle"),
        (VALIDE.replace("= 'Shonen'", "~* 'shonen'"), "recherche textuelle"),
        (VALIDE.replace("= 'Shonen'", "LIKE 'S%'"), "recherche textuelle"),
        (
            VALIDE.replace(
                "WHERE series_category_clean = 'Shonen'",
                "WHERE to_tsvector(series_title) @@ to_tsquery('ninja')",
            ),
            "recherche textuelle",
        ),
        (
            VALIDE.replace(
                "FROM manga.ms_series_enriched",
                "FROM bench.corpus_docs",
            ),
            "lecture du corpus",
        ),
        (VALIDE.replace("LIMIT 20;", ""), "plafond absent"),
        (VALIDE.replace(", series_id", ""), "tri non total"),
        (VALIDE + " SELECT 1;", "une seule instruction"),
        ("DELETE FROM manga.ms_series_enriched", "un SELECT"),
    ],
)
def test_regle_refusee(sql, motif):
    with pytest.raises(regles.RegleInvalide, match=motif):
        regles.controler(sql)
