"""Le dossier des échantillons d'équivalence (E3, étape 6, question 38).

Sans `EQUIVALENCE_DOSSIER`, le dossier de la production, comme avant. Avec elle,
`exporter` écrit et `comparer` lit dans le dossier donné, et rien n'est écrit dans
celui de la production. Une variable qui désigne le dossier de la production — chemin
absolu, relatif, lien, ou un dossier qui s'y trouve — est refusée avant toute
écriture. Le test du service réel, celui de `verify-poste`, lit toujours le dossier
de la production.

Aucune base, aucun service : l'échantillon et la comparaison sont des doublures, et
le module est replacé dans un dossier jetable — le vrai dossier de la production
n'est ni lu ni écrit.
"""

from __future__ import annotations

import contextlib
import json

import psycopg
import pytest
from test_equivalence import dossier_du_service_reel

from service_embedding import equivalence

CONFIG = equivalence.charger_config()
VARIABLE = equivalence.VARIABLE_DOSSIER


@pytest.fixture
def module_jetable(tmp_path, monkeypatch):
    """Le module 09 dans un dossier jetable ; rend (production, préproduction)."""
    racine = tmp_path / "09_service_embedding"
    racine.mkdir()
    monkeypatch.setattr(equivalence, "RACINE_MODULE", racine)
    monkeypatch.delenv(VARIABLE, raising=False)
    production = racine / CONFIG["export"]["dossier"]
    preproduction = racine / "mesures" / "equivalence_preprod"
    return production, preproduction


def fichiers(dossier):
    return sorted(p.name for p in dossier.iterdir()) if dossier.exists() else []


# --------------------------------------------------------------------------- #
#  Le dossier
# --------------------------------------------------------------------------- #


def test_sans_variable_le_dossier_et_le_chemin_d_aujourd_hui(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)
    production = equivalence.RACINE_MODULE / "mesures" / "equivalence"
    assert equivalence.dossier_export(CONFIG) == production
    assert equivalence.dossier_export(CONFIG, {VARIABLE: ""}) == production
    assert equivalence.chemin_export(CONFIG, 3) == production / "encodage_3.json"
    assert equivalence.chemin_export(
        CONFIG, 3, equivalence.dossier_export(CONFIG)
    ) == equivalence.chemin_export(CONFIG, 3)


def test_la_variable_donne_le_dossier(module_jetable):
    _, preproduction = module_jetable
    assert equivalence.dossier_export(CONFIG, {VARIABLE: str(preproduction)}) == (
        preproduction.resolve()
    )
    assert (
        equivalence.chemin_export(CONFIG, 4, preproduction)
        == preproduction / "encodage_4.json"
    )


def test_le_dossier_de_la_production_est_refuse_sous_toutes_ses_formes(
    module_jetable, tmp_path, monkeypatch
):
    production, _ = module_jetable
    production.mkdir(parents=True)
    lien = tmp_path / "lien_vers_la_production"
    lien.symlink_to(production, target_is_directory=True)
    monkeypatch.chdir(equivalence.RACINE_MODULE)
    for valeur in (
        str(production),  # absolu
        CONFIG["export"]["dossier"],  # relatif au dossier courant
        f"{CONFIG['export']['dossier']}/../equivalence",  # détour
        str(lien),  # lien
        str(production / "preprod"),  # un dossier qui s'y trouve
    ):
        with pytest.raises(equivalence.DossierRefuse, match="production"):
            equivalence.dossier_export(CONFIG, {VARIABLE: valeur})


# --------------------------------------------------------------------------- #
#  Les commandes
# --------------------------------------------------------------------------- #


@pytest.fixture
def sans_base(monkeypatch):
    """`exporter` sans base : la connexion et l'échantillon sont des doublures ;
    rend la liste des connexions demandées."""
    connexions = []

    @contextlib.contextmanager
    def connecter(dsn, **_options):
        connexions.append(dsn)
        yield None

    def echantillon(_cx, encodage_id, _config):
        return {"encodage_id": encodage_id, "modele": "BAAI/bge-m3"}, []

    monkeypatch.setattr(psycopg, "connect", connecter)
    monkeypatch.setattr(equivalence, "echantillon", echantillon)
    monkeypatch.setenv("DATABASE_URL", "postgresql://essai@localhost/apimanga_preprod")
    return connexions


def test_exporter_ecrit_dans_le_dossier_donne_et_rien_chez_la_production(
    module_jetable, sans_base, monkeypatch, capsys
):
    production, preproduction = module_jetable
    monkeypatch.setenv(VARIABLE, str(preproduction))
    assert equivalence.main(["exporter", "--encodage", "4"]) == 0
    assert fichiers(preproduction) == ["encodage_4.json"]
    assert fichiers(production) == [], "rien n'est écrit chez la production"
    assert json.loads((preproduction / "encodage_4.json").read_text("utf-8"))[
        "encodage"
    ] == {"encodage_id": 4, "modele": "BAAI/bge-m3"}
    assert capsys.readouterr().out.strip().endswith("encodage_4.json")


def test_comparer_lit_dans_le_dossier_donne(module_jetable, monkeypatch, capsys):
    production, preproduction = module_jetable
    for dossier, marque in ((production, "production"), (preproduction, "preprod")):
        dossier.mkdir(parents=True)
        (dossier / "encodage_3.json").write_text(
            json.dumps({"encodage": {"marque": marque}, "echantillon": []}), "utf-8"
        )
    monkeypatch.setattr(equivalence, "client_de", lambda _e: None)
    monkeypatch.setattr(
        equivalence,
        "comparer",
        lambda _client, export, _config: {
            "verdict": "equivalent",
            "lu": export["encodage"]["marque"],
        },
    )
    monkeypatch.setenv(VARIABLE, str(preproduction))
    assert equivalence.main(["comparer", "--encodage", "3"]) == 0
    assert json.loads(capsys.readouterr().out)["lu"] == "preprod"


def test_la_commande_refuse_le_dossier_de_la_production_avant_toute_ecriture(
    module_jetable, sans_base, monkeypatch, capsys
):
    production, _ = module_jetable
    production.mkdir(parents=True)
    monkeypatch.setenv(VARIABLE, str(production))
    for commande in ("exporter", "comparer"):
        assert equivalence.main([commande, "--encodage", "4"]) == 2
        assert "refus" in capsys.readouterr().err
    assert fichiers(production) == [], "rien n'est écrit"
    assert sans_base == [], "aucune connexion à la base"


# --------------------------------------------------------------------------- #
#  La vérification de la production ne se détourne pas
# --------------------------------------------------------------------------- #


def test_le_service_reel_lit_toujours_le_dossier_de_la_production(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(VARIABLE, str(tmp_path / "preprod"))
    assert dossier_du_service_reel() == equivalence.dossier_production(CONFIG)
    assert dossier_du_service_reel() != equivalence.dossier_export(CONFIG)
