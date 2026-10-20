"""Tirage stratifie de la reconnaissance : rejouable, et dans les deux strates.

Le perimetre est fabrique (rang = position) : on sait donc exactement quelles
series forment le premier et le dernier decile.
"""

import random

import pytest

from commun.echantillon import GRAINE, PART_STRATE, tirer
from commun.perimetre import Serie


def _perimetre(n: int) -> list[Serie]:
    """`n` series, la serie `i` au rang `i` : la tete est en debut de liste."""
    return [
        Serie(series_id=i, cle_source=f"cle-{i}", rang_popularite=i)
        for i in range(1, n + 1)
    ]


def test_meme_graine_meme_echantillon() -> None:
    """La mesure est rejouable : deux tirages identiques, a la serie pres."""
    series = _perimetre(400)
    assert tirer(series) == tirer(series)


def test_une_autre_graine_change_l_echantillon() -> None:
    """La graine est bien celle du tirage : la changer change la mesure."""
    series = _perimetre(400)
    assert tirer(series).tout != tirer(series, graine=GRAINE + 1).tout


def test_l_ordre_d_entree_n_influe_pas() -> None:
    """Le tri est reapplique : un perimetre melange rend le meme echantillon."""
    series = _perimetre(400)
    melange = list(series)
    random.Random(1).shuffle(melange)
    assert tirer(melange) == tirer(series)


def test_chaque_strate_est_tiree_dans_son_decile() -> None:
    series = _perimetre(400)
    e = tirer(series, par_strate=20)
    assert e.taille_strate_tete == e.taille_strate_queue == 40
    assert len(e.tete) == len(e.queue) == 20
    assert all(s.rang_popularite <= 40 for s in e.tete), "tete hors premier decile"
    assert all(s.rang_popularite > 360 for s in e.queue), "queue hors dernier decile"
    assert len({s.series_id for s in e.tout}) == 40, "aucune serie tiree deux fois"


def test_les_strates_sont_rendues_par_popularite() -> None:
    e = tirer(_perimetre(400))
    for strate in (e.tete, e.queue):
        rangs = [s.rang_popularite for s in strate]
        assert rangs == sorted(rangs)
    assert e.tout == [*e.tete, *e.queue]


def test_le_tirage_est_trace_avec_ses_parametres() -> None:
    e = tirer(_perimetre(400))
    assert (e.graine, e.part_strate) == (GRAINE, PART_STRATE)


def test_une_strate_ne_descend_jamais_sous_par_strate() -> None:
    """Sur un petit perimetre, le decile est plus petit que le tirage : la strate
    prend alors `par_strate` series, et le tirage les prend toutes."""
    series = _perimetre(50)
    e = tirer(series, par_strate=20)
    assert e.taille_strate_tete == e.taille_strate_queue == 20
    assert [s.series_id for s in e.tete] == list(range(1, 21))
    assert [s.series_id for s in e.queue] == list(range(31, 51))


def test_un_rang_en_doublon_est_departage_par_series_id() -> None:
    """Quatre rangs sont en doublon dans le catalogue : le departage tient."""
    series = [Serie(series_id=i, cle_source="x", rang_popularite=1) for i in range(4)]
    e = tirer(list(reversed(series)), par_strate=2, part_strate=0.5)
    assert {s.series_id for s in e.tete} == {0, 1}
    assert {s.series_id for s in e.queue} == {2, 3}


def test_un_perimetre_trop_petit_est_refuse() -> None:
    with pytest.raises(ValueError, match="perimetre trop petit"):
        tirer(_perimetre(39), par_strate=20)
