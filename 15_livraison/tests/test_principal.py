"""La commande `livrer` : les sous-commandes, les codes, les refus affichés."""

from __future__ import annotations

import pytest
from conftest import E3, Poste

from livraison import __main__ as principal
from livraison.codes import SENS, Code
from livraison.outils import Outils


def test_chaque_code_a_son_sens():
    assert set(SENS) == set(Code)


def test_construire(o: Outils, ecran):
    assert principal.principal(["construire"], o) == 0
    assert ecran[0].startswith("Construction à l'étiquette e4-livraison")
    assert (o.constructions / "e4-livraison.json").is_file()
    assert sum(ligne.startswith("| `") for ligne in ecran) == 3


def test_construire_a_blanc_hors_etiquette(o: Outils, poste: Poste, ecran):
    poste.branche, poste.head = "feature/livraison", None
    assert principal.principal(["construire", "--a-blanc"], o) == 0
    assert "refuserait" in ecran[0] and "référence lue : HEAD" in ecran[0]
    poste.head = "e4-livraison"
    poste.branche = "main"
    ecran.clear()
    assert principal.principal(["construire", "--a-blanc"], o) == 0
    assert ecran[0] == "À blanc — construction à l'étiquette e4-livraison."
    assert not any(c.argv[:2] == ("docker", "build") for c in poste.appels)


def test_un_refus_est_affiche_avec_son_code(o: Outils, poste: Poste, ecran):
    poste.statut_git = " M x\n"
    assert principal.principal(["construire"], o) == int(Code.PREREQUIS)
    assert ecran[-1].startswith("Refus — code 20 (PREREQUIS) : arbre non propre")


@pytest.mark.parametrize(
    "arguments",
    [
        ["deployer", "--cible", "production"],
        ["deployer", "--cible", "staging", "--a-blanc"],
        ["sante", "--cible", "production"],
        ["arreter", "--cible", "production"],
        ["etat"],
    ],
)
def test_les_sous_commandes(o: Outils, arguments):
    assert principal.principal(arguments, o) in (0, 1)


def test_retour(o: Outils, ecran):
    code = principal.principal(
        ["retour", "--cible", "production", "--vers", E3, "--motif", "essai"], o
    )
    assert code == int(Code.SANTE_ROUGE)


def test_une_cible_inconnue_est_refusee_par_l_analyseur(o: Outils):
    with pytest.raises(SystemExit):
        principal.principal(["deployer", "--cible", "serveur"], o)
