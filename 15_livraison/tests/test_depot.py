"""L'étiquette : arbre propre, sur main, à une étiquette annotée."""

from __future__ import annotations

import pytest
from conftest import COMMIT_E4, E3, E4, Poste

from livraison import depot
from livraison.codes import Arret, Code
from livraison.outils import Commande, Outils, Sortie


def test_l_etiquette_de_head_est_rendue(o: Outils):
    assert depot.exiger_etiquette(o) == depot.Etiquette(E4, COMMIT_E4)


@pytest.mark.parametrize(
    ("reglage", "motif"),
    [
        ({"statut_git": " M x.py\n"}, "arbre non propre"),
        (
            {"statut_git": "?? 15_livraison/journal/a.md\n M 12_application/x.py\n"},
            "premier fichier en cause : 12_application/x.py",
        ),
        ({"statut_git": "?? 15_livraison/journal\n"}, "15_livraison/journal ;"),
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


@pytest.mark.parametrize(
    ("statut", "en_cause"),
    [
        ("", None),
        ("\n", None),
        (" M 15_livraison/journal/index.md\n?? 15_livraison/journal/x.md\n", None),
        ('?? "15_livraison/journal/é.md"\n', None),
        ("R  15_livraison/journal/a.md -> 15_livraison/journal/b.md\n", None),
        ("R  15_livraison/journal/a.md -> 15_livraison/b.md\n", "15_livraison/b.md"),
        ("R  15_livraison/a.md -> 15_livraison/journal/a.md\n", "15_livraison/a.md"),
        (" M 15_livraison/journal.md\n", "15_livraison/journal.md"),
        (" M 15_livraison/src/livraison/x.py\n", "15_livraison/src/livraison/x.py"),
    ],
)
def test_seuls_les_journaux_sont_admis(statut: str, en_cause: str | None):
    assert depot.hors_journal(statut) == en_cause


def test_l_arbre_aux_seuls_journaux_modifies_est_propre(o: Outils, poste: Poste):
    poste.statut_git = (
        " M 15_livraison/journal/index.md\n?? 15_livraison/journal/x.md\n"
    )
    assert depot.exiger_etiquette(o) == depot.Etiquette(E4, COMMIT_E4)


def test_un_statut_illisible_est_un_refus(o: Outils, poste: Poste):
    def lanceur(c: Commande) -> Sortie:
        return Sortie(128, stderr="pas un dépôt") if c.nom == "git.status" else poste(c)

    o.lanceur = lanceur
    with pytest.raises(Arret, match="git status a échoué") as e:
        depot.exiger_etiquette(o)
    assert e.value.code == Code.PREREQUIS
