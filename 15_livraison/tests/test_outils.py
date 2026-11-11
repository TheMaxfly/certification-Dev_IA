"""Les utilitaires : le bouclage seul, l'affichage sans chemin personnel, le JSON lu."""

from __future__ import annotations

import pytest

from livraison import deployer
from livraison.codes import Arret
from livraison.outils import (
    Commande,
    Outils,
    Sortie,
    commande_affichee,
    environnement_propre,
    exiger_bouclage,
    json_objet,
    liste,
    objet,
)


def test_le_bouclage_seul():
    exiger_bouclage("http://127.0.0.1:8020/sante")
    with pytest.raises(Arret, match="hors du bouclage"):
        exiger_bouclage("http://192.0.2.1:8020/sante")


def test_l_environnement_vide_garde_les_ajouts(monkeypatch):
    monkeypatch.setenv("API_MODELE_CLES", "x")
    env = environnement_propre({"DATABASE_URL": "postgresql://postgres@localhost/x"})
    assert "API_MODELE_CLES" not in env
    assert env["DATABASE_URL"].endswith("/x") and env["LC_ALL"] == "C.UTF-8"


def test_la_commande_affichee_masque_les_chemins(tmp_path):
    c = Commande(
        "x", ("docker", "-f", str(tmp_path / "a.yml")), env={"API_MODELE_PGPASS": "/p"}
    )
    assert (
        commande_affichee(c, tmp_path) == "API_MODELE_PGPASS=<chemin> docker -f a.yml"
    )


def test_le_json_lu():
    assert objet([1]) == {} and liste({}) == [] and json_objet("") == {}
    assert json_objet("[1]") == {} and json_objet("{") == {}
    assert json_objet('{"a": 1}') == {"a": 1}


def test_des_migrations_illisibles_s_ecrivent_comme_telles(o: Outils):
    o.lanceur = lambda c: Sortie(1, "", "refus")
    assert "illisible" in deployer.texte_migrations(o, "apimanga")
