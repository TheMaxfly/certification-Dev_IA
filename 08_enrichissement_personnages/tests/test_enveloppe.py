"""Controle 4 du paragraphe 10 : aucun compteur ne porte un nom generique."""

from pathlib import Path

import pytest

from commun.enveloppe import Enveloppe, NomDeCompteurGenerique, verifier_nom


@pytest.mark.parametrize("nom", ["items", "count", "total", "n", "data", "records"])
def test_les_noms_generiques_sont_refuses(nom: str) -> None:
    with pytest.raises(NomDeCompteurGenerique):
        verifier_nom(nom)


@pytest.mark.parametrize(
    "nom",
    [
        "liens_collectes",
        "personnages_distincts",
        "articles_traites",
        "series_du_perimetre",
    ],
)
def test_les_noms_qui_disent_ce_qu_ils_comptent_passent(nom: str) -> None:
    assert verifier_nom(nom) == nom


def test_incrementer_refuse_un_nom_generique() -> None:
    env = Enveloppe(source="anilist", partition="2026-09", date_run="r1")
    with pytest.raises(NomDeCompteurGenerique):
        env.incrementer("items")


def test_egalite_des_ensembles_perimetre_collectees_echecs() -> None:
    """Controle 2 du paragraphe 9 : aucun trou silencieux."""
    env = Enveloppe(source="anilist", partition="2026-09", date_run="r1")
    env.marquer_collectee(1)
    env.marquer_echec(2, "HTTP 404")
    env.verifier_egalite_ensembles([1, 2])
    with pytest.raises(AssertionError):
        env.verifier_egalite_ensembles([1, 2, 3])


def test_un_echec_sans_motif_est_refuse() -> None:
    env = Enveloppe(source="anilist", partition="2026-09", date_run="r1")
    with pytest.raises(ValueError):
        env.marquer_echec(1, "   ")


def test_reprise_preserve_l_ordre_et_saute_le_deja_fait(tmp_path: Path) -> None:
    env = Enveloppe(source="anilist", partition="2026-09", date_run="r1")
    env.marquer_collectee(10)
    env.marquer_echec(20, "HTTP 500")
    chemin = env.enregistrer(tmp_path / "e.json")
    repris = Enveloppe.charger(chemin)
    assert repris is not None
    assert repris.reste_a_faire([10, 20, 30, 40]) == [30, 40]


def test_charger_refuse_un_etat_au_compteur_generique(tmp_path: Path) -> None:
    chemin = tmp_path / "e.json"
    chemin.write_text(
        '{"source":"a","partition":"p","date_run":"r","compteurs":{"items":3}}',
        encoding="utf-8",
    )
    with pytest.raises(NomDeCompteurGenerique):
        Enveloppe.charger(chemin)
