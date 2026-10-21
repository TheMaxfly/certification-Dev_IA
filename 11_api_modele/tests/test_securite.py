"""L'accès : une clé par consommateur, en temps constant ; son nom au journal, jamais
sa valeur ; puis le débit de cette clé."""

from __future__ import annotations

import logging

from conftest import CLE_APPLICATION, CLE_DEMONSTRATION, entetes

from api_modele.configuration import Cle
from api_modele.securite import consommateur

CLES = (Cle("application", CLE_APPLICATION), Cle("demonstration", CLE_DEMONSTRATION))


def test_consommateur_rend_le_nom_de_la_cle_presentee():
    assert consommateur(CLES, CLE_DEMONSTRATION) == "demonstration"
    assert consommateur(CLES, CLE_APPLICATION) == "application"


def test_consommateur_refuse_une_cle_fausse_vide_ou_non_ascii():
    assert consommateur(CLES, "x" * 40) is None
    assert consommateur(CLES, "") is None
    assert consommateur(CLES, "é" * 40) is None


def test_consommateur_parcourt_toutes_les_cles_sans_s_arreter(monkeypatch):
    """Pas de `break` : la durée ne dépend pas du rang de la clé trouvée."""
    import api_modele.securite as securite

    appels: list[bytes] = []
    vraie = securite.secrets.compare_digest

    def compter(a: bytes, b: bytes) -> bool:
        appels.append(b)
        return vraie(a, b)

    monkeypatch.setattr(securite.secrets, "compare_digest", compter)
    consommateur(CLES, CLE_APPLICATION)

    assert len(appels) == len(CLES)


def test_sans_cle_401(api):
    r = api.get("/v1/info")

    assert r.status_code == 401
    assert r.json()["code"] == "cle_invalide"


def test_cle_fausse_401_meme_message_que_sans_cle(api):
    sans = api.get("/v1/info").json()
    fausse = api.get("/v1/info", headers=entetes("z" * 40)).json()

    assert fausse["code"] == sans["code"] == "cle_invalide"
    assert fausse["message"] == sans["message"]


def test_bonne_cle_200(api):
    assert api.get("/v1/info", headers=entetes()).status_code == 200
    assert api.get("/v1/info", headers=entetes(CLE_DEMONSTRATION)).status_code == 200


def test_le_nom_de_la_cle_entre_au_journal_jamais_sa_valeur(api, caplog):
    with caplog.at_level(logging.INFO, logger="api_modele"):
        api.get("/v1/info", headers=entetes(CLE_DEMONSTRATION))
        api.get("/v1/info", headers=entetes("z" * 40))

    lignes = [r.champs for r in caplog.records if r.getMessage() == "requete"]
    assert [ligne["cle"] for ligne in lignes] == ["demonstration", None]
    tout = " ".join(str(r.__dict__) for r in caplog.records)
    assert CLE_DEMONSTRATION not in tout
    assert "z" * 40 not in tout


def test_la_sante_ne_demande_pas_de_cle(api):
    assert api.get("/v1/sante").status_code == 200
