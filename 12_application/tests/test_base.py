"""L'accès à la base de l'application (E4, lot 1, spec 02, § 5) : paresseux, borné,
absent ou injoignable sans bloquer l'application, état exposé par une métrique."""

from __future__ import annotations

import asyncio
import logging

import pytest
from conftest import journal_complet

from application.base import Base, BaseIndisponible
from application.metriques import Metriques


def etat_expose(metriques: Metriques) -> dict[str, float]:
    sortie = metriques.exposer()[0].decode()
    return {
        etat: float(ligne.rsplit(" ", 1)[1])
        for etat in ("ok", "indisponible", "non_configuree")
        for ligne in sortie.splitlines()
        if ligne.startswith(f'application_base_etat{{etat="{etat}"}}')
    }


def test_sans_dsn_la_base_n_est_pas_configuree_et_le_dit():
    metriques = Metriques()
    base = Base(None, 1.0, metriques)

    async def essai() -> None:
        with pytest.raises(BaseIndisponible):
            async with base.transaction():
                pass  # pragma: no cover — jamais atteint
        assert await base.sonder() == "non_configuree"

    asyncio.run(essai())
    assert not base.configuree
    assert etat_expose(metriques) == {"ok": 0, "indisponible": 0, "non_configuree": 1}


def test_une_base_injoignable_leve_une_erreur_nommee_et_se_journalise(
    port_ferme, caplog
):
    metriques = Metriques()
    dsn = f"postgresql://essai@127.0.0.1:{port_ferme.rsplit(':', 1)[1]}/apimanga_essai"
    base = Base(dsn, 1.0, metriques)

    async def essai() -> str:
        with pytest.raises(BaseIndisponible):
            async with base.transaction():
                pass  # pragma: no cover — jamais atteint
        return await base.sonder()

    with caplog.at_level(logging.INFO, logger="application"):
        etat = asyncio.run(essai())

    assert base.configuree and etat == "indisponible"
    assert etat_expose(metriques)["indisponible"] == 1
    assert "base_indisponible" in caplog.messages
    assert dsn not in journal_complet(caplog), "le DSN est apparu au journal"


def test_une_base_en_service_repond_et_le_dit(base_app):
    metriques = Metriques()
    base = Base(base_app, 2.0, metriques)

    async def essai() -> tuple[str, int]:
        async with base.transaction() as cx:
            curseur = await cx.execute("SELECT count(*) FROM application.comptes")
            ligne = await curseur.fetchone()
        assert ligne is not None
        return await base.sonder(), ligne[0]

    etat, comptes = asyncio.run(essai())
    assert (etat, comptes) == ("ok", 0)
    assert etat_expose(metriques) == {"ok": 1, "indisponible": 0, "non_configuree": 0}


def test_une_connexion_coupee_en_cours_de_requete_est_une_base_indisponible(
    base_app, caplog
):
    base = Base(base_app, 2.0, Metriques())

    async def essai() -> None:
        with pytest.raises(BaseIndisponible):
            async with base.transaction() as cx:
                await cx.execute("SELECT pg_terminate_backend(pg_backend_pid())")

    with caplog.at_level(logging.INFO, logger="application"):
        asyncio.run(essai())

    assert base.etat == "indisponible"
    assert "base_indisponible" in caplog.messages


def test_une_transaction_en_erreur_est_annulee(base_app):
    base = Base(base_app, 2.0, Metriques())

    async def essai() -> int:
        with pytest.raises(ZeroDivisionError):
            async with base.transaction() as cx:
                await cx.execute(
                    "INSERT INTO application.reglages (cle, valeur)"
                    " VALUES ('essai', 'annulee')"
                )
                raise ZeroDivisionError
        async with base.transaction() as cx:
            curseur = await cx.execute("SELECT count(*) FROM application.reglages")
            ligne = await curseur.fetchone()
        assert ligne is not None
        return ligne[0]

    assert asyncio.run(essai()) == 0
