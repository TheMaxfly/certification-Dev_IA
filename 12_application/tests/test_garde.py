"""La garde réseau : aucune requête ne sort du bouclage pendant la suite."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import httpx
import pytest
from conftest import ADRESSE_MEMOIRE, BOUCLAGE


def test_la_garde_couvre_chaque_test_de_la_suite(request):
    sans_garde = [
        item.nodeid
        for item in request.session.items
        if "aucun_appel_hors_du_bouclage" not in getattr(item, "fixturenames", ())
    ]

    assert request.session.items
    assert sans_garde == []


def test_la_garde_bloque_une_requete_hors_du_bouclage(aucun_appel_hors_du_bouclage):
    async def tenter() -> None:
        async with httpx.AsyncClient() as client:
            await client.get("https://api.mistral.ai/v1/models")

    with pytest.raises(AssertionError, match="hors du bouclage : api.mistral.ai"):
        asyncio.run(tenter())

    assert aucun_appel_hors_du_bouclage == ["api.mistral.ai"]
    aucun_appel_hors_du_bouclage.clear()  # la garde a fait son office


def test_l_adresse_de_la_doublure_est_locale(doublure_sur_port, reglages):
    assert urlsplit(doublure_sur_port).hostname in BOUCLAGE
    assert urlsplit(ADRESSE_MEMOIRE).hostname in BOUCLAGE
    assert urlsplit(reglages.adresse_api).hostname in BOUCLAGE
