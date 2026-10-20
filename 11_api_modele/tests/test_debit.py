"""Le débit : une fenêtre glissante d'une minute, par clé ; au-delà, 429 et le délai."""

from __future__ import annotations

from conftest import CLE_DEMONSTRATION, Horloge, entetes

from api_modele.debit import Limiteur


def test_le_limiteur_admet_jusqu_a_la_limite_puis_refuse():
    horloge = Horloge()
    limiteur = Limiteur(3, horloge)

    assert [limiteur.admettre("a") for _ in range(3)] == [None, None, None]
    assert limiteur.admettre("a") == 60


def test_la_fenetre_glisse():
    horloge = Horloge()
    limiteur = Limiteur(2, horloge)
    limiteur.admettre("a")
    horloge.t += 30
    limiteur.admettre("a")

    horloge.t += 10
    assert limiteur.admettre("a") == 20  # la première sort à t0 + 60
    horloge.t += 20
    assert limiteur.admettre("a") is None


def test_chaque_cle_a_sa_fenetre():
    limiteur = Limiteur(1, Horloge())

    assert limiteur.admettre("a") is None
    assert limiteur.admettre("b") is None
    assert limiteur.admettre("a") is not None


def test_le_delai_vaut_au_moins_une_seconde():
    horloge = Horloge()
    limiteur = Limiteur(1, horloge)
    limiteur.admettre("a")
    horloge.t += 59.9

    assert limiteur.admettre("a") == 1


def test_la_61e_requete_de_la_minute_est_refusee(api, horloge):
    for _ in range(60):
        assert api.get("/v1/info", headers=entetes()).status_code == 200

    r = api.get("/v1/info", headers=entetes())

    assert r.status_code == 429
    assert r.json()["code"] == "debit_depasse"
    assert r.json()["details"] == {"limite_par_minute": 60, "reessayer_dans_s": 60}
    assert r.headers["Retry-After"] == "60"
    # Une autre clé n'est pas touchée ; la même repasse quand la fenêtre a glissé.
    assert api.get("/v1/info", headers=entetes(CLE_DEMONSTRATION)).status_code == 200
    horloge.t += 60
    assert api.get("/v1/info", headers=entetes()).status_code == 200


def test_une_cle_fausse_n_est_pas_comptee(api):
    for _ in range(70):
        api.get("/v1/info", headers=entetes("z" * 40))

    assert api.get("/v1/info", headers=entetes()).status_code == 200


def test_le_refus_est_compte_par_nom_de_cle(api):
    for _ in range(61):
        api.get("/v1/info", headers=entetes())

    metriques = api.get("/metrics").text
    assert 'api_modele_debit_refuse_total{cle="application"} 1.0' in metriques
