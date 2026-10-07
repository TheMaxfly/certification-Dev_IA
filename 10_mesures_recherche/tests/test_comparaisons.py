"""Bootstrap apparié : des cas dont la réponse est connue d'avance."""

from __future__ import annotations

import numpy as np
import pytest

from mesures_recherche.comparaisons import bootstrap_apparie, comparer
from mesures_recherche.configuration import charger

NIVEAU = 1 - 0.05 / 4


def test_bras_identiques_ecart_nul_non_etabli():
    a = np.array([1.0, 0.0, 1.0, 0.5])
    r = bootstrap_apparie(a, a.copy(), 1000, 1, NIVEAU)
    assert r["ecart"] == 0.0 and r["ic_bas"] == r["ic_haut"] == 0.0
    assert r["verdict"] == "non établie" and r["p_bilateral"] == 1.0


def test_ecart_net_etabli():
    a, b = np.ones(59), np.zeros(59)
    r = bootstrap_apparie(a, b, 1000, 1, NIVEAU)
    assert r["ecart"] == 1.0 and r["ic_bas"] == 1.0 and r["verdict"] == "différence"


def test_meme_graine_meme_resultat():
    rng = np.random.default_rng(0)
    a, b = rng.random(59), rng.random(59)
    assert bootstrap_apparie(a, b, 500, 7, NIVEAU) == bootstrap_apparie(
        a, b, 500, 7, NIVEAU
    )


def test_questions_non_appariees_refusees():
    def resultat(n, ids):
        return {
            "mesure": n,
            "questions": [
                {"question_id": i, "metriques": {"hit_rate@10": 1.0, "ndcg@10": 1.0}}
                for i in ids
            ],
        }

    config = charger()
    resultats = {n: resultat(n, ["Q001", "Q002"]) for n in range(1, 7)}
    resultats[2] = resultat(2, ["Q001"])
    with pytest.raises(ValueError, match="1 contre 2"):
        comparer(resultats, config)
