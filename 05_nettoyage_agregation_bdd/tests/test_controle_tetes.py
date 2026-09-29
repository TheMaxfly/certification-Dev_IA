"""Contrôle de couverture sur les têtes du catalogue.

Deux étages :
  - la LOGIQUE, sur base jetable, comme le reste de la suite ;
  - le CONTRÔLE PERMANENT sur la base réelle, en lecture seule, qui ne tourne
    que si `APIMANGA_DSN` est défini (précédent : module 08). Motif : le bloc 1
    avait prouvé la précision de la cascade, jamais son rappel là où l'échec
    est le plus visible.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from identity.controle_tetes import (
    EXCEPTIONS,
    TETES,
    ErreurControle,
    controler,
    lire_exceptions,
)

APIMANGA_DSN = os.environ.get("APIMANGA_DSN")
besoin_apimanga = pytest.mark.skipif(not APIMANGA_DSN, reason="APIMANGA_DSN non défini")


@pytest.fixture
def base_tetes(base) -> str:
    """Quatre séries ; deux ex æquo au rang 1, départagées par series_id."""
    with psycopg.connect(base, autocommit=True) as cx:
        for sid, titre, rang, kitsu_id in (
            (10, "Première", 1, "100"),
            (5, "Seconde", 1, None),
            (20, "Troisième", 3, None),
            (30, "Hors têtes", 4, None),
        ):
            cx.execute(
                "INSERT INTO manga.ms_series_enriched "
                "(series_id, series_title, series_popularity_rank) "
                "VALUES (%s, %s, %s)",
                (sid, titre, rang),
            )
            cx.execute(
                "INSERT INTO manga.work_identity (series_id, kitsu_id) VALUES (%s, %s)",
                (sid, kitsu_id),
            )
    return base


def test_une_tete_sans_kitsu_id_ni_raison_fait_echouer(base_tetes):
    with psycopg.connect(base_tetes) as cx:
        bilan = controler(cx, {}, n=3)
    assert [s for s, *_ in bilan.tetes] == [5, 10, 20], "départage par series_id"
    assert [s for s, *_ in bilan.non_documentees] == [5, 20]
    assert not bilan.ok


def test_une_raison_documentee_suffit(base_tetes):
    raisons = {5: "absente de Kitsu", 20: "absente de Kitsu"}
    with psycopg.connect(base_tetes) as cx:
        bilan = controler(cx, raisons, n=3)
    assert bilan.ok
    assert bilan.documentees == raisons


def test_une_raison_perimee_fait_echouer(base_tetes):
    """La série 10 a son kitsu_id, la 30 n'est pas une tête : le fichier ment."""
    raisons = {5: "r", 20: "r", 10: "r", 30: "r"}
    with psycopg.connect(base_tetes) as cx:
        bilan = controler(cx, raisons, n=3)
    assert set(bilan.perimees) == {10, 30}
    assert not bilan.ok


def test_le_fichier_refuse_une_raison_vide_ou_un_doublon(tmp_path):
    vide = tmp_path / "vide.csv"
    vide.write_text("series_id,raison,documente_le\n1,,2026-09-30\n")
    with pytest.raises(ErreurControle, match="raison vide"):
        lire_exceptions(vide)
    double = tmp_path / "double.csv"
    double.write_text("series_id,raison,documente_le\n1,a,x\n1,b,y\n")
    with pytest.raises(ErreurControle, match="deux fois"):
        lire_exceptions(double)


def test_le_fichier_versionne_se_lit():
    assert EXCEPTIONS.is_file()
    lire_exceptions()


@besoin_apimanga
def test_les_cinquante_tetes_du_catalogue_ont_un_kitsu_id_ou_une_raison():
    """LE contrôle permanent, sur la base réelle, en lecture seule."""
    with psycopg.connect(
        APIMANGA_DSN, options="-c default_transaction_read_only=on"
    ) as cx:
        bilan = controler(cx, lire_exceptions())
    assert len(bilan.tetes) == TETES
    assert bilan.ok, (
        "têtes sans kitsu_id ni raison documentée : "
        f"{bilan.non_documentees} ; raisons périmées : {bilan.perimees}"
    )
