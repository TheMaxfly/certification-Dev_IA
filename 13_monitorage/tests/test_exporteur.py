"""L'exporteur de qualité, contre une doublure de base : aucune base réelle."""

from __future__ import annotations

from typing import Any

import psycopg

from monitorage import exporteur
from monitorage.exporteur import LecteurMesure
from monitorage.metriques import Metriques

DSN = "postgresql://verif_lecture@hote-factice:5432/apimanga"


class Resultat:
    def __init__(self, ligne: Any) -> None:
        self.ligne = ligne

    def fetchone(self) -> Any:
        return self.ligne


class BaseFactice:
    """Une connexion : la session, puis la mesure ; ou une panne."""

    def __init__(
        self,
        session: tuple[str, bool] = ("on", True),
        mesure: tuple[float, float] | None = (0.5254, 1760000000.0),
        panne: bool = False,
    ) -> None:
        self.session = session
        self.mesure = mesure
        self.panne = panne
        self.requetes: list[str] = []

    def __call__(self, dsn: str) -> BaseFactice:
        if self.panne:
            raise psycopg.OperationalError("injoignable : " + dsn)
        return self

    def __enter__(self) -> BaseFactice:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, requete: Any) -> Resultat:
        self.requetes.append(requete)
        if requete == exporteur.SQL_SESSION:
            return Resultat(self.session)
        return Resultat(self.mesure)


def test_la_mesure_est_publiee():
    m = Metriques(lecture_base=True)
    base = BaseFactice()
    mesure = LecteurMesure(DSN, m, connecter=base).lire()

    assert mesure is not None
    texte = m.exposer().decode()
    assert "modele_derniere_mesure_hit_rate_10 0.5254" in texte
    assert "modele_derniere_mesure_timestamp 1.76e+09" in texte
    assert "modele_exporteur_base_activee 1.0" in texte


def test_la_mesure_vient_de_eval_mesures_pour_l_encodage_en_service():
    base = BaseFactice()
    LecteurMesure(DSN, Metriques(lecture_base=True), connecter=base).lire()
    requete = base.requetes[-1]
    assert "bench.eval_mesures" in requete
    assert "bench.v_encodage_en_service" in requete
    assert "m.metrique = 'hit_rate' AND m.k = 10" in requete


def test_une_session_en_ecriture_est_refusee():
    m = Metriques(lecture_base=True)
    base = BaseFactice(session=("off", True))
    assert LecteurMesure(DSN, m, connecter=base).lire() is None
    assert "modele_derniere_mesure_hit_rate_10" not in m.exposer().decode()
    assert 'modele_sonde_erreurs_total{cause="autre"} 1.0' in m.exposer().decode()


def test_une_session_hors_de_verif_ro_est_refusee():
    m = Metriques(lecture_base=True)
    base = BaseFactice(session=("on", False))
    assert LecteurMesure(DSN, m, connecter=base).lire() is None
    assert "modele_derniere_mesure_hit_rate_10" not in m.exposer().decode()


def test_une_base_injoignable_ne_fait_rien_tomber_et_ne_montre_pas_le_dsn(capsys: Any):
    m = Metriques(lecture_base=True)
    assert LecteurMesure(DSN, m, connecter=BaseFactice(panne=True)).lire() is None
    assert 'modele_sonde_erreurs_total{cause="autre"} 1.0' in m.exposer().decode()
    sortie = capsys.readouterr()
    assert "hote-factice" not in sortie.err + sortie.out
    assert "hote-factice" not in m.exposer().decode()


def test_sans_mesure_rien_n_est_publie():
    m = Metriques(lecture_base=True)
    assert LecteurMesure(DSN, m, connecter=BaseFactice(mesure=None)).lire() is None
    assert "modele_derniere_mesure_hit_rate_10" not in m.exposer().decode()


def test_la_connexion_reelle_impose_la_lecture_seule(monkeypatch: Any):
    vus: dict[str, Any] = {}

    def connecter(dsn: str, **options: Any) -> None:
        vus.update(options, dsn=dsn)

    monkeypatch.setattr(exporteur.psycopg, "connect", connecter)
    exporteur.connecter_postgres(DSN)
    assert vus["options"] == "-c default_transaction_read_only=on"
    assert vus["connect_timeout"] == 5
