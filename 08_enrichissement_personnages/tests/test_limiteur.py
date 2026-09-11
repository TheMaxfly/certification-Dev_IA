"""Controle 3 du paragraphe 10 : la cadence se verifie sans reseau."""

import pytest

from commun.limiteur import Limiteur


class _Horloge:
    """Horloge et mise en attente simulees : aucune attente reelle en test."""

    def __init__(self) -> None:
        self.t = 0.0
        self.attentes: list[float] = []

    def maintenant(self) -> float:
        return self.t

    def dormir(self, secondes: float) -> None:
        self.attentes.append(secondes)
        self.t += secondes


def test_la_premiere_requete_ne_patiente_pas() -> None:
    h = _Horloge()
    lim = Limiteur(1.0, horloge=h.maintenant, dormir=h.dormir)
    assert lim.attendre() == 0.0
    assert h.attentes == []


def test_les_requetes_suivantes_respectent_l_intervalle() -> None:
    h = _Horloge()
    lim = Limiteur(2.0, horloge=h.maintenant, dormir=h.dormir)
    lim.attendre()
    assert lim.attendre() == pytest.approx(2.0)
    assert lim.attendre() == pytest.approx(2.0)
    assert lim.requetes_autorisees == 3
    assert lim.attente_cumulee_s == pytest.approx(4.0)


def test_un_appel_tardif_ne_patiente_pas() -> None:
    h = _Horloge()
    lim = Limiteur(1.0, horloge=h.maintenant, dormir=h.dormir)
    lim.attendre()
    h.t += 10.0
    assert lim.attendre() == 0.0


def test_retry_after_prend_le_pas_sur_la_cadence() -> None:
    """maxlag et Retry-After : on attend et on reessaie, jamais on ne force."""
    h = _Horloge()
    lim = Limiteur(1.0, horloge=h.maintenant, dormir=h.dormir)
    lim.attendre()
    lim.signaler_attente(30.0)
    assert lim.attendre() == pytest.approx(30.0)


def test_un_signal_plus_court_ne_raccourcit_pas_l_attente() -> None:
    h = _Horloge()
    lim = Limiteur(5.0, horloge=h.maintenant, dormir=h.dormir)
    lim.attendre()
    lim.signaler_attente(1.0)
    assert lim.attendre() == pytest.approx(5.0)


def test_intervalle_negatif_refuse() -> None:
    with pytest.raises(ValueError):
        Limiteur(-1.0)
