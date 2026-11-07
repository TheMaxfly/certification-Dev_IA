"""L'exporteur de qualité, contre une doublure de base : aucune base réelle."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from monitorage import exporteur
from monitorage.exporteur import LecteurMesure
from monitorage.metriques import Metriques

DSN = "postgresql://verif_lecture@hote-factice:5432/apimanga"
#: Le run cité par la dernière promotion de l'encodage en service (factice).
RUN_PROMU = "a3b7f0e0-0000-4000-8000-000000000001"
#: Un run plus récent du même encodage, d'une autre configuration (factice).
RUN_PLUS_RECENT = "b0000000-0000-4000-8000-000000000002"


class Resultat:
    def __init__(self, ligne: Any) -> None:
        self.ligne = ligne

    def fetchone(self) -> Any:
        return self.ligne


class BaseFactice:
    """Une connexion : la session, la dernière promotion, puis la mesure du run
    demandé (une par run) ; ou une panne."""

    def __init__(
        self,
        session: tuple[str, bool] = ("on", True),
        promotion: tuple[str | None, int] | None = (RUN_PROMU, 3),
        mesures: dict[str, tuple[float, float]] | None = None,
        panne: bool = False,
    ) -> None:
        self.session = session
        self.promotion = promotion
        self.mesures = (
            {RUN_PROMU: (0.5254, 1760000000.0)} if mesures is None else mesures
        )
        self.panne = panne
        self.requetes: list[str] = []
        self.runs: list[str] = []

    def __call__(self, dsn: str) -> BaseFactice:
        if self.panne:
            raise psycopg.OperationalError("injoignable : " + dsn)
        return self

    def __enter__(self) -> BaseFactice:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, requete: Any, parametres: tuple[Any, ...] = ()) -> Resultat:
        self.requetes.append(requete)
        if requete == exporteur.SQL_SESSION:
            return Resultat(self.session)
        if requete == exporteur.SQL_PROMOTION:
            return Resultat(self.promotion)
        assert requete == exporteur.SQL_MESURE
        self.runs.append(parametres[0])
        return Resultat(self.mesures.get(parametres[0]))


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
    assert base.requetes == [
        exporteur.SQL_SESSION,
        exporteur.SQL_PROMOTION,
        exporteur.SQL_MESURE,
    ]
    assert "bench.promotions" in exporteur.SQL_PROMOTION
    assert "bench.v_encodage_en_service" in exporteur.SQL_PROMOTION
    assert "p.decision = 'promu'" in exporteur.SQL_PROMOTION
    assert "bench.eval_mesures" in exporteur.SQL_MESURE
    assert "m.metrique = 'hit_rate' AND m.k = 10" in exporteur.SQL_MESURE
    assert "m.run_id = %s" in exporteur.SQL_MESURE
    # La dernière promotion (3 → 2 → 3 : la plus récente), sur la jointure de la vue ;
    # la mesure de référence : global, jeu v2, toutes les questions.
    assert "ORDER BY p.promotion_id DESC LIMIT 1" in exporteur.SQL_PROMOTION
    assert "ON p.encodage_id = s.encodage_id" in exporteur.SQL_PROMOTION
    for filtre in (
        "m.portee = 'global'",
        "m.jeu_version = 'v2'",
        "m.perimetre = 'toutes'",
    ):
        assert filtre in exporteur.SQL_MESURE, filtre


def test_un_run_plus_recent_d_une_autre_configuration_ne_change_pas_la_valeur():
    """La mesure du run cité par la promotion, pas la dernière de l'encodage : un rejeu
    ou une autre mesure, plus récents, ne changent pas le chiffre publié."""
    m = Metriques(lecture_base=True)
    base = BaseFactice(
        mesures={
            RUN_PROMU: (0.5254, 1760000000.0),
            RUN_PLUS_RECENT: (0.6102, 1770000000.0),
        }
    )
    mesure = LecteurMesure(DSN, m, connecter=base).lire()

    assert mesure is not None and mesure.hit_rate_10 == 0.5254
    assert base.runs == [RUN_PROMU]
    assert "modele_derniere_mesure_hit_rate_10 0.5254" in m.exposer().decode()


def test_une_promotion_sans_run_lit_la_reference_connue():
    reference = exporteur.REFERENCES[2]
    base = BaseFactice(promotion=(None, 2), mesures={reference: (0.5254, 1.7e9)})
    mesure = LecteurMesure(DSN, Metriques(lecture_base=True), connecter=base).lire()

    assert mesure is not None and base.runs == [reference]
    assert reference.startswith("28aa0e60")


def test_un_run_cite_l_emporte_sur_la_reference_connue():
    """L'encodage 2 promu de nouveau, avec un run : c'est ce run qui compte."""
    reference = exporteur.REFERENCES[2]
    base = BaseFactice(
        promotion=(RUN_PROMU, 2),
        mesures={RUN_PROMU: (0.53, 1.7e9), reference: (0.5254, 1.6e9)},
    )
    mesure = LecteurMesure(DSN, Metriques(lecture_base=True), connecter=base).lire()

    assert mesure is not None and mesure.hit_rate_10 == 0.53
    assert base.runs == [RUN_PROMU]


def test_une_mesure_publiee_disparait_quand_il_n_y_a_plus_rien_a_publier():
    """Une promotion sans référence, ou un run sans mesure, après une valeur publiée :
    les jauges disparaissent, plutôt qu'un chiffre périmé ; une base injoignable laisse
    la dernière valeur."""
    m = Metriques(lecture_base=True)
    assert LecteurMesure(DSN, m, connecter=BaseFactice()).lire() is not None
    assert "modele_derniere_mesure_hit_rate_10 0.5254" in m.exposer().decode()

    LecteurMesure(DSN, m, connecter=BaseFactice(panne=True)).lire()
    assert "modele_derniere_mesure_hit_rate_10 0.5254" in m.exposer().decode()

    LecteurMesure(DSN, m, connecter=BaseFactice(promotion=(None, 9))).lire()
    assert "modele_derniere_mesure" not in m.exposer().decode()


@pytest.mark.parametrize(
    "promotion", [None, (None, 9)], ids=["sans-promotion", "sans-run-ni-reference"]
)
def test_sans_run_a_lire_rien_n_est_publie(promotion: Any):
    m = Metriques(lecture_base=True)
    base = BaseFactice(promotion=promotion)

    assert LecteurMesure(DSN, m, connecter=base).lire() is None
    assert base.runs == [], "aucune mesure demandée"
    assert "modele_derniere_mesure_hit_rate_10" not in m.exposer().decode()


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
    assert LecteurMesure(DSN, m, connecter=BaseFactice(mesures={})).lire() is None
    assert "modele_derniere_mesure_hit_rate_10" not in m.exposer().decode()


def test_la_connexion_reelle_impose_la_lecture_seule(monkeypatch: Any):
    vus: dict[str, Any] = {}

    def connecter(dsn: str, **options: Any) -> None:
        vus.update(options, dsn=dsn)

    monkeypatch.setattr(exporteur.psycopg, "connect", connecter)
    exporteur.connecter_postgres(DSN)
    assert vus["options"] == "-c default_transaction_read_only=on"
    assert vus["connect_timeout"] == 5
