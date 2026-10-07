"""Lecture des métriques et règle des seuils de mémoire."""

from __future__ import annotations

from service_embedding.controles import compteur, compteurs_declares
from service_embedding.mesures import seuils_tenus

METRIQUES = """\
# TYPE te_request_count counter
te_request_count{method="batch"} 3
te_request_count{method="single"} 2
# TYPE te_request_success counter
te_request_success{method="batch"} 3
# TYPE te_queue_size gauge
te_queue_size 0
"""


def test_compteur_somme_toutes_les_etiquettes():
    assert compteur(METRIQUES, "te_request_count") == 5.0


def test_compteur_absent_rend_none_et_non_zero():
    # Un compteur absent n'est pas un compteur nul : le contrôle doit échouer.
    assert compteur(METRIQUES, "te_inexistant") is None


def test_compteur_ne_confond_pas_un_prefixe():
    assert compteur(METRIQUES, "te_request") is None


def test_compteurs_declares_ignore_les_jauges():
    assert compteurs_declares(METRIQUES) == ["te_request_count", "te_request_success"]


def test_seuils():
    # 5 Go de mémoire vive (abaissé de 6 le 2026-10-07), 4 Go de mémoire vidéo.
    tenus = {"ram_disponible": 5 * 10**9, "vram_libre": 4 * 10**9}
    assert seuils_tenus(tenus) == []
    sous = {"ram_disponible": 4_900_000_000, "vram_libre": 3_900_000_000}
    ecarts = seuils_tenus(sous)
    assert len(ecarts) == 2
    assert "4.90 Go < 5 Go" in ecarts[0]
