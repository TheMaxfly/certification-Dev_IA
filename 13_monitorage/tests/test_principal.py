"""Le point d'entrée : la boucle, la mesure à la main, le refus de démarrer."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from conftest import DoublureService, echantillon

from monitorage import __main__ as principal_
from monitorage.configuration import charger
from monitorage.metriques import Metriques
from monitorage.sonde import Sonde


class SondeComptee(Sonde):
    def __init__(self, arret: threading.Event) -> None:
        self.passages = 0
        self.arret = arret

    def passage(self, origine: str = "sonde") -> dict[str, Any] | None:
        self.passages += 1
        self.arret.set()
        return {}


class LecteurCompte:
    def __init__(self) -> None:
        self.lectures = 0

    def lire(self) -> None:
        self.lectures += 1


def test_la_boucle_lance_la_sonde_et_la_lecture_puis_s_arrete():
    arret = threading.Event()
    sonde = SondeComptee(arret)
    lecteur = LecteurCompte()
    principal_.executer(
        charger({"SONDE_DELAI_INITIAL_S": "0"}),
        sonde,
        lecteur,  # type: ignore[arg-type]
        arret,
    )
    assert sonde.passages == 1
    assert lecteur.lectures == 1


def test_la_boucle_attend_le_delai_initial():
    arret = threading.Event()
    arret.set()
    sonde = SondeComptee(arret)
    principal_.executer(charger({}), sonde, None, arret)
    assert sonde.passages == 0


def preparer(
    monkeypatch: pytest.MonkeyPatch, doublure: DoublureService, tmp_path: Path
) -> Path:
    chemin = tmp_path / "encodage_3.json"
    chemin.write_text(json.dumps(echantillon(doublure.prefixe)), encoding="utf-8")
    journal = tmp_path / "journal.jsonl"
    monkeypatch.setenv("SONDE_HOTE_SERVICE", "127.0.0.1")
    monkeypatch.setenv("SONDE_ECHANTILLON", str(chemin))
    monkeypatch.setenv("SONDE_JOURNAL", str(journal))
    return journal


def test_mesurer_rend_le_passage_et_le_journalise(
    monkeypatch: pytest.MonkeyPatch,
    doublure: DoublureService,
    tmp_path: Path,
    capsys: Any,
):
    journal = preparer(monkeypatch, doublure, tmp_path)
    doublure.cosinus["texte secret 2"] = 0.9985

    assert principal_.principal(["mesurer"]) == 0

    ligne = json.loads(capsys.readouterr().out)
    assert ligne["origine"] == "mesure"
    assert [f["chunk_id"] for f in ligne["fragments_sous_seuil"]] == [102]
    assert json.loads(journal.read_text(encoding="utf-8")) == ligne


def test_mesurer_rend_1_si_le_service_est_en_panne(
    monkeypatch: pytest.MonkeyPatch, doublure: DoublureService, tmp_path: Path
):
    preparer(monkeypatch, doublure, tmp_path)
    doublure.panne = True
    assert principal_.principal(["mesurer"]) == 1


def test_un_argument_inconnu_est_refuse(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MONITORAGE_DSN_LECTURE", raising=False)
    assert principal_.principal(["autre"]) == 2


def test_une_configuration_fausse_refuse_de_demarrer_sans_montrer_le_dsn(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
):
    monkeypatch.setenv(
        "MONITORAGE_DSN_LECTURE", "postgresql://u:motdepassefactice@hote/base"
    )
    assert principal_.principal([]) == 2
    assert "motdepassefactice" not in capsys.readouterr().err


def test_le_demarrage_sert_les_metriques_puis_s_arrete(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SONDE_ECOUTE", "127.0.0.1:0")
    monkeypatch.setenv("MONITORAGE_LECTURE_BASE", "1")
    monkeypatch.setenv("MONITORAGE_DSN_LECTURE", "postgresql://u@hote/base")
    vus: dict[str, Any] = {}

    def executer(configuration: Any, sonde: Any, lecteur: Any, arret: Any) -> None:
        vus.update(lecteur=lecteur, arret=arret)

    monkeypatch.setattr(principal_, "executer", executer)
    assert principal_.principal([]) == 0
    assert vus["lecteur"] is not None
    assert isinstance(vus["arret"], threading.Event)


def test_les_metriques_du_processus_disent_si_la_base_est_lue():
    assert "modele_exporteur_base_activee 1.0" in (
        Metriques(lecture_base=True).exposer().decode()
    )
