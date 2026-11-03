"""Le client de l'API : erreurs typées, délais, clé jamais montrée."""

from __future__ import annotations

import asyncio
import logging

import doublure_api as d
import httpx
import pytest

from application.client_api import (
    ApiDelai,
    ApiInjoignable,
    ClientApi,
    ErreurApi,
    lire_retry_after,
)
from application.config import Delais
from application.metriques import Metriques

DELAIS = Delais(connexion_s=1.0, reponse_s=5.0, info_s=5.0, sante_s=5.0)
ADRESSE = "http://127.0.0.1:8010"


def client_sur(doublure: d.Doublure, delais: Delais = DELAIS) -> ClientApi:
    return ClientApi(
        ADRESSE,
        d.CLE_ATTENDUE,
        delais,
        Metriques(),
        httpx.ASGITransport(app=doublure.app),
    )


def jouer(coroutine):
    async def faire():
        return await coroutine

    return asyncio.run(faire())


def test_le_repr_ne_montre_pas_la_cle(doublure):
    assert d.CLE_ATTENDUE not in repr(client_sur(doublure))


def test_info_est_lue_et_la_cle_transmise(doublure):
    info = jouer(client_sur(doublure).info())

    assert info.version_api == "1.3.1"
    assert info.modele_langage.profil_actif == "mistral-large-2512"
    assert doublure.cles_recues == [d.CLE_ATTENDUE]


def test_un_champ_de_plus_de_l_api_est_ignore(doublure):
    doublure.choisir(reponse="complement")

    resultat = jouer(client_sur(doublure).reponse({"question": "x"}))

    assert resultat.corps.complement == "Complément factice."
    assert resultat.identifiant_api == "api-factice-entete"


@pytest.mark.parametrize(
    ("scenario", "statut", "code", "retry_after"),
    [
        ("debit_depasse", 429, "debit_depasse", 12),
        ("debit_sans_entete", 429, "debit_depasse", None),
        ("fournisseur_refus_429", 503, "fournisseur_refus", 7),
        ("fournisseur_refus_500", 503, "fournisseur_refus", None),
        ("erreur_interne", 500, "erreur_interne", None),
        ("corps_hors_contrat", 502, "corps_inattendu", None),
        ("reponse_hors_contrat", 200, "corps_inattendu", None),
    ],
)
def test_les_erreurs_sont_typees(doublure, scenario, statut, code, retry_after):
    doublure.choisir(reponse=scenario)

    with pytest.raises(ErreurApi) as exc:
        jouer(client_sur(doublure).reponse({"question": "x"}))

    assert (exc.value.statut, exc.value.code) == (statut, code)
    assert exc.value.retry_after == retry_after
    assert exc.value.identifiant_requete


def test_l_erreur_garde_ses_details(doublure):
    doublure.choisir(reponse="sortie_pseudonyme")

    with pytest.raises(ErreurApi) as exc:
        jouer(client_sur(doublure).reponse({"question": "x"}))

    assert exc.value.details == {"motif": "pseudonyme", "role": "reponse"}
    assert exc.value.identifiant_requete == "api-factice-0002"


@pytest.mark.parametrize(
    ("scenario", "attendu"),
    [("cle_invalide", "cle_invalide"), ("corps_hors_contrat", "corps_inattendu")],
)
def test_info_rend_ses_erreurs(doublure, scenario, attendu):
    doublure.choisir(info=scenario)

    with pytest.raises(ErreurApi) as exc:
        jouer(client_sur(doublure).info())

    assert exc.value.code == attendu


def test_sante_dit_ok_ou_non(doublure):
    client = client_sur(doublure)

    assert jouer(client.sante()) is True
    doublure.choisir(sante="degrade")
    assert jouer(client_sur(doublure).sante()) is False


def test_un_port_ferme_est_injoignable_et_sante_le_dit(port_ferme):
    metriques = Metriques()
    client = ClientApi(port_ferme, d.CLE_ATTENDUE, DELAIS, metriques)

    with pytest.raises(ApiInjoignable) as exc:
        jouer(client.info())

    assert exc.value.__cause__ is None
    assert d.CLE_ATTENDUE not in str(exc.value)
    assert (
        jouer(ClientApi(port_ferme, d.CLE_ATTENDUE, DELAIS, metriques).sante()) is False
    )
    sortie = metriques.exposer()[0].decode()
    assert 'application_api_erreurs_total{code="api_injoignable"} 2.0' in sortie


def test_un_delai_depasse_est_nomme(doublure, doublure_sur_port):
    doublure.choisir(reponse="delai")
    delais = Delais(connexion_s=0.2, reponse_s=0.2, info_s=0.2, sante_s=0.2)

    with pytest.raises(ApiDelai) as exc:
        jouer(
            ClientApi(doublure_sur_port, d.CLE_ATTENDUE, delais, Metriques()).reponse(
                {"question": "x"}
            )
        )

    assert exc.value.__cause__ is None


def test_le_delai_borne_aussi_le_transport_en_memoire(doublure):
    doublure.choisir(info="delai")
    delais = Delais(connexion_s=0.05, reponse_s=0.05, info_s=0.05, sante_s=0.05)

    with pytest.raises(ApiDelai):
        jouer(client_sur(doublure, delais).info())


class TransportCoupe(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("coupé", request=request)


def test_une_connexion_coupee_est_injoignable():
    client = ClientApi(ADRESSE, d.CLE_ATTENDUE, DELAIS, Metriques(), TransportCoupe())

    with pytest.raises(ApiInjoignable):
        jouer(client.info())


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [
        ("7", 7),
        (" 12 ", 12),
        (None, None),
        ("0", None),
        ("-3", None),
        ("1.5", None),
        ("Wed, 21 Oct", None),
    ],
)
def test_lire_retry_after(valeur, attendu):
    assert lire_retry_after(valeur) == attendu


def test_le_client_ne_journalise_ni_la_question_ni_la_reponse(doublure, caplog):
    caplog.set_level(logging.DEBUG)

    jouer(client_sur(doublure).reponse({"question": "Question factice secrète ?"}))

    assert "Question factice secrète" not in caplog.text
    assert "Passage factice" not in caplog.text
    assert d.CLE_ATTENDUE not in caplog.text
