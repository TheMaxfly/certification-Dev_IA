"""La sonde d'équivalence, contre une doublure du service : le code du 09 tel quel."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from conftest import DoublureService

from monitorage.configuration import Configuration
from monitorage.metriques import Metriques
from monitorage.sonde import NOM_JOURNAL, Sonde

COSINUS_BAS = 0.9985


def metriques() -> Metriques:
    return Metriques(lecture_base=False)


def lignes(configuration: Configuration) -> list[dict[str, Any]]:
    assert configuration.journal is not None
    if not configuration.journal.exists():
        return []
    texte = configuration.journal.read_text(encoding="utf-8")
    return [json.loads(ligne) for ligne in texte.splitlines()]


def test_aucune_jauge_avant_la_premiere_mesure():
    texte = metriques().exposer().decode()
    assert "modele_sonde_equivalence_cosinus_min" not in texte
    assert "modele_sonde_tranches_sous_seuil" not in texte
    assert "modele_derniere_mesure_hit_rate_10" not in texte
    # Les compteurs et l'état de l'exporteur existent, eux.
    assert 'modele_sonde_erreurs_total{cause="autre"} 0.0' in texte
    assert "modele_exporteur_base_activee 0.0" in texte


def test_un_passage_publie_les_jauges(
    doublure: DoublureService, configuration: Configuration
):
    m = metriques()
    ligne = Sonde(configuration, m, horloge=lambda: 1000.0).passage()

    assert ligne is not None
    texte = m.exposer().decode()
    assert "modele_sonde_equivalence_cosinus_min 1.0" in texte
    assert "modele_sonde_equivalence_cosinus_mediane 1.0" in texte
    assert "modele_sonde_tranches_sous_seuil 0.0" in texte
    assert "modele_sonde_derniere_execution_timestamp 1000.0" in texte
    assert "modele_sonde_duree_secondes " in texte
    assert 'modele_sonde_encodage{encodage="3"} 1.0' in texte


def test_le_code_du_module_09_parle_a_l_hote_passe_en_argument(
    doublure: DoublureService, configuration: Configuration
):
    Sonde(configuration, metriques()).passage()
    assert len(doublure.appels) == 1
    assert doublure.appels[0][0].startswith(doublure.prefixe)


def test_un_fragment_sous_le_seuil_est_compte_et_journalise_sans_son_texte(
    doublure: DoublureService, configuration: Configuration
):
    doublure.cosinus["texte secret 1"] = COSINUS_BAS
    m = metriques()
    Sonde(configuration, m).passage()

    texte = m.exposer().decode()
    assert f"modele_sonde_equivalence_cosinus_min {COSINUS_BAS}" in texte
    assert "modele_sonde_tranches_sous_seuil 1.0" in texte
    (ligne,) = lignes(configuration)
    assert ligne["cos_min"] == pytest.approx(COSINUS_BAS)
    assert ligne["tranches_sous_seuil"] == 1
    assert [f["chunk_id"] for f in ligne["fragments_sous_seuil"]] == [101]
    assert ligne["fragments_sous_seuil"][0]["cos"] == pytest.approx(COSINUS_BAS)
    assert "texte secret" not in json.dumps(ligne)


def test_une_ligne_par_passage(doublure: DoublureService, configuration: Configuration):
    sonde = Sonde(configuration, metriques())
    sonde.passage()
    sonde.passage(origine="mesure")
    assert [ligne["origine"] for ligne in lignes(configuration)] == ["sonde", "mesure"]


def test_le_seuil_est_lu_dans_la_configuration_du_module_09(
    doublure: DoublureService, configuration: Configuration, tmp_path: Path
):
    doublure.cosinus["texte secret 1"] = 0.998
    autre = tmp_path / "equivalence.toml"
    autre.write_text(
        "[echantillon]\ntranches = 2\npar_tranche = 2\ngraine = 1\n\n"
        "[seuils]\nequivalent = 0.99\nechec = 0.9\n\n"
        '[export]\ndossier = "mesures/equivalence"\n',
        encoding="utf-8",
    )
    m = metriques()
    Sonde(configuration, m, config_equivalence=autre).passage()
    assert "modele_sonde_tranches_sous_seuil 0.0" in m.exposer().decode()


def test_service_injoignable_compte_et_garde_les_jauges(
    doublure: DoublureService, configuration: Configuration
):
    m = metriques()
    sonde = Sonde(configuration, m, horloge=lambda: 1000.0)
    assert sonde.passage() is not None

    doublure.panne = True
    assert sonde.passage() is None

    texte = m.exposer().decode()
    assert 'modele_sonde_erreurs_total{cause="service_injoignable"} 1.0' in texte
    assert "modele_sonde_equivalence_cosinus_min 1.0" in texte
    assert len(lignes(configuration)) == 1


def test_echantillon_absent_compte(
    doublure: DoublureService, configuration: Configuration, tmp_path: Path
):
    m = metriques()
    absent = replace(configuration, echantillon=tmp_path / "absent.json")
    assert Sonde(absent, m).passage() is None
    assert 'modele_sonde_erreurs_total{cause="echantillon_absent"} 1.0' in (
        m.exposer().decode()
    )


def test_echantillon_illisible_compte_sous_autre(
    doublure: DoublureService, configuration: Configuration, tmp_path: Path
):
    illisible = tmp_path / "illisible.json"
    illisible.write_text("{pas du json", encoding="utf-8")
    m = metriques()
    assert Sonde(replace(configuration, echantillon=illisible), m).passage() is None
    assert 'modele_sonde_erreurs_total{cause="autre"} 1.0' in m.exposer().decode()


def test_un_service_non_conforme_compte_sous_autre(
    doublure: DoublureService, configuration: Configuration
):
    assert configuration.echantillon is not None
    export = json.loads(configuration.echantillon.read_text(encoding="utf-8"))
    export["encodage"]["revision"] = "une-autre-revision"
    configuration.echantillon.write_text(json.dumps(export), encoding="utf-8")
    m = metriques()
    assert Sonde(configuration, m).passage() is None
    assert 'modele_sonde_erreurs_total{cause="autre"} 1.0' in m.exposer().decode()


def test_un_journal_inaccessible_ne_fait_pas_tomber_la_sonde(
    doublure: DoublureService, configuration: Configuration, tmp_path: Path
):
    m = metriques()
    ferme = replace(configuration, journal=tmp_path / "absent" / "journal.jsonl")
    assert Sonde(ferme, m).passage() is not None
    texte = m.exposer().decode()
    assert 'modele_sonde_erreurs_total{cause="autre"} 1.0' in texte
    assert "modele_sonde_equivalence_cosinus_min 1.0" in texte


def test_le_journal_par_defaut_est_a_cote_de_l_echantillon_du_module_09(
    doublure: DoublureService,
    configuration: Configuration,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from monitorage import service

    equivalence = service.module("equivalence")
    monkeypatch.setattr(
        equivalence, "chemin_export", lambda _config, _n: tmp_path / "encodage_0.json"
    )
    Sonde(replace(configuration, journal=None), metriques()).passage()
    assert (tmp_path / NOM_JOURNAL).read_text(encoding="utf-8").count("\n") == 1


def test_le_seuil_du_module_09_n_est_pas_recopie_ici():
    sources = Path(__file__).resolve().parents[1] / "src" / "monitorage"
    for fichier in sources.glob("*.py"):
        assert "0.999" not in fichier.read_text(encoding="utf-8"), fichier.name
