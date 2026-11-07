"""`python -m chaine` : les sous-commandes, leurs codes de sortie, leurs refus."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import CANDIDATS, Monde
from test_environnement import connexions, etat_conforme

from chaine import __main__ as principal
from chaine import deployer
from chaine.codes import Code
from chaine.environnement import Sortie

REFUS = "14_chaine/candidats/refus_bgem3_v2.toml"


def test_le_passage_a_blanc_rend_0_sans_rien_lancer(monde: Monde):
    code = principal.principal(
        ["lancer", REFUS, "--a-blanc"], monde.environ, monde.outils
    )
    assert code == 0 and monde.lanceur.lancees == []
    assert any("Passage à blanc" in ligne for ligne in monde.ecran)


def test_lancer_rend_le_code_nomme_du_passage(monde: Monde):
    code = principal.principal(["lancer", REFUS], monde.environ, monde.outils)
    assert code == Code.REFUS_REGLE
    assert list(monde.outils.livraisons.glob("*_refus_bgem3_v2.json"))


def test_un_refus_s_affiche_avec_son_code(monde: Monde):
    code = principal.principal(
        ["lancer", REFUS, "--a-blanc"], {"CHAINE_BASE": "apimanga"}, monde.outils
    )
    assert code == Code.PREREQUIS
    assert any("Refus — code 20 (PREREQUIS)" in ligne for ligne in monde.ecran)


def test_preprod_cree_la_preproduction_et_l_ecrit_au_journal(
    monde: Monde, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(deployer, "JOURNAL", tmp_path / "journal.jsonl")
    monde.lanceur.reponses |= connexions() | etat_conforme()
    assert principal.principal(["preprod"], monde.environ, monde.outils) == 0
    assert "Préproduction prête : apimanga_preprod" in monde.ecran[-1]
    assert '"geste": "preprod"' in (tmp_path / "journal.jsonl").read_text("utf-8")


def test_un_refus_de_la_copie_montre_un_extrait_de_sa_sortie(monde: Monde):
    monde.lanceur.reponses |= (
        connexions()
        | etat_conforme()
        | {"pg_dump": Sortie(1, "", "pg_dump: error: postgresql://u:secret@h/x")}
    )
    assert principal.principal(["preprod"], monde.environ, monde.outils) == 20
    assert "secret" not in "\n".join(monde.ecran)
    assert "[masqué]" in monde.ecran[-1]


@pytest.mark.parametrize(
    "arguments",
    [
        ["promouvoir", "20261107T030000Z_absent", "--motif", "m"],
        ["revenir", "--motif", ""],
        ["etat"],
    ],
)
def test_les_gestes_de_max_passent_par_le_deployeur(monde: Monde, arguments):
    monde.lanceur.reponses["10.journal.etat"] = Sortie(1, "", "erreur")
    assert principal.principal(arguments, monde.environ, monde.outils) == 20


def test_les_candidats_reels_sont_dans_le_module():
    assert sorted(p.name for p in CANDIDATS.glob("*.toml")) == [
        "refus_bgem3_v2.toml",
        "requalification_v2.toml",
    ]
