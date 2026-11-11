"""L'étiquette : arbre propre, sur main, à une étiquette annotée."""

from __future__ import annotations

import pytest
from conftest import COMMIT_E4, E3, E4, Poste

from livraison import depot
from livraison.codes import Arret, Code
from livraison.outils import Outils


def test_l_etiquette_de_head_est_rendue(o: Outils):
    assert depot.exiger_etiquette(o) == depot.Etiquette(E4, COMMIT_E4)


@pytest.mark.parametrize(
    ("reglage", "motif"),
    [
        ({"statut_git": " M x.py\n"}, "arbre non propre"),
        ({"branche": "develop"}, "branche develop"),
        ({"branche": ""}, "détachée"),
        ({"head": None}, "aucune étiquette"),
    ],
)
def test_construire_et_deployer_refusent_hors_etiquette(
    o: Outils, poste: Poste, reglage, motif
):
    for cle, valeur in reglage.items():
        setattr(poste, cle, valeur)
    with pytest.raises(Arret, match=motif) as e:
        depot.exiger_etiquette(o)
    assert e.value.code == Code.PREREQUIS


def test_une_etiquette_legere_ou_inconnue_est_refusee(o: Outils, poste: Poste):
    poste.annotees.pop(E4)
    poste.legeres.add(E4)
    with pytest.raises(Arret, match="légère"):
        depot.exiger_etiquette(o)
    with pytest.raises(Arret, match="inconnue"):
        depot.etiquette_connue(o, "absente")
    with pytest.raises(Arret, match="inconnue"):
        depot.etiquette_connue(o, "")
    assert depot.etiquette_connue(o, E3).nom == E3


def test_un_fichier_a_une_reference(o: Outils, poste: Poste):
    assert "1.4.0" in (depot.fichier_a(o, E3, "11_api_modele/docker-compose.yml") or "")
    assert depot.fichier_a(o, E3, "absent") is None
    assert depot.etiquette_de_head(o) == E4
    poste.head = None
    assert depot.etiquette_de_head(o) is None
