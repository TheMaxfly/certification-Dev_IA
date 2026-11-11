"""E4, lot 5, spec 02 — le test des clés des fournisseurs :
`Fournisseurs.controler_cles` et la route `POST /v1/verification` (contrat 1.5.0, D10).

Aucun fournisseur réel : `GET /v1/models` est servi par la doublure du bouclage
(`DoublureFournisseur`, deux adresses) ; la garde du conftest fait échouer tout
appel hors du bouclage. Les clés des profils factices (`CLE_A`, `CLE_B`) sont
reconnaissables : elles ne doivent paraître ni dans une réponse, ni au journal, ni aux
métriques.
"""

from __future__ import annotations

import logging
import re
import socket
from dataclasses import replace

import pytest
from conftest import (
    CLE_A,
    CLE_B,
    CLE_DEMONSTRATION,
    CLES_FACTICES,
    DoublureFournisseur,
    Doublures,
    entetes,
)
from test_fournisseurs import jouer, modele_langage

from api_modele.fournisseurs import Controle, Fournisseurs
from api_modele.journal import FormatJson

CHAMPS = {
    "nom",
    "fournisseur",
    "modele",
    "variable_cle",
    "configuree",
    "disponible",
    "statut",
}


def controler(couche: Fournisseurs):
    return couche.controler_cles()


def par_nom(controles: list[Controle]) -> dict[str, Controle]:
    return {c.nom: c for c in controles}


# --------------------------------------------------------------------------- #
#  La méthode : un rapport, sans effet sur l'état
# --------------------------------------------------------------------------- #
def test_les_cles_posees_et_le_modele_present_rendent_ok(tmp_path, doublures):
    DoublureFournisseur.modeles = ["modele-a", "modele-b"]
    ml = modele_langage(tmp_path, doublures)

    vus = par_nom(jouer(ml, controler))

    assert set(vus) == {"factice-a", "factice-b"}
    a = vus["factice-a"]
    assert (a.configuree, a.disponible, a.statut) == (True, True, "ok")
    assert (a.fournisseur, a.modele, a.variable_cle) == (
        "mistral",
        "modele-a",
        "CLE_FACTICE_A",
    )
    assert sorted(r["chemin"] for r in doublures.recus()) == ["/v1/models"] * 2
    assert {r["autorisation"] for r in doublures.recus()} == {
        f"Bearer {CLE_A}",
        f"Bearer {CLE_B}",
    }


def test_un_modele_absent_de_la_liste_rend_le_profil_non_disponible(
    tmp_path, doublures
):
    DoublureFournisseur.modeles = ["modele-a"]
    ml = modele_langage(tmp_path, doublures)

    vus = par_nom(jouer(ml, controler))

    b = vus["factice-b"]
    assert (b.configuree, b.disponible, b.statut) == (True, False, "ok")


@pytest.mark.parametrize("statut", [401, 500])
def test_un_refus_du_fournisseur_rend_son_code(tmp_path, doublures, statut):
    DoublureFournisseur.statut_modeles = statut
    DoublureFournisseur.modeles = ["modele-a", "modele-b"]
    ml = modele_langage(tmp_path, doublures)

    vus = par_nom(jouer(ml, controler))

    assert {(c.disponible, c.statut) for c in vus.values()} == {(False, str(statut))}


def test_un_corps_illisible_rend_corps_inattendu(tmp_path, doublures, monkeypatch):
    def illisible(self: DoublureFournisseur) -> None:
        self._noter(None)
        self._envoyer(200, b"pas du json")

    monkeypatch.setattr(DoublureFournisseur, "do_GET", illisible)
    ml = modele_langage(tmp_path, doublures)

    vus = par_nom(jouer(ml, controler))

    assert {c.statut for c in vus.values()} == {"corps_inattendu"}


def test_un_fournisseur_injoignable_rend_injoignable(tmp_path, doublures):
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        port = prise.getsockname()[1]
    ml = modele_langage(tmp_path, doublures)
    fermes = tuple(
        replace(p, adresse=f"http://127.0.0.1:{port}/v1/chat/completions")
        for p in ml.profils
    )

    vus = par_nom(jouer(replace(ml, profils=fermes), controler))

    assert {(c.configuree, c.disponible, c.statut) for c in vus.values()} == {
        (True, False, "injoignable")
    }


def test_un_fournisseur_trop_lent_rend_injoignable(tmp_path, doublures):
    DoublureFournisseur.modeles = ["modele-a", "modele-b"]
    DoublureFournisseur.retard_modeles_s = 1.0  # le délai factice est de 0,2 s
    ml = modele_langage(tmp_path, doublures)

    vus = par_nom(jouer(ml, controler))

    assert {c.statut for c in vus.values()} == {"injoignable"}


@pytest.mark.parametrize("valeur", [None, "", "   "], ids=["absente", "vide", "blanc"])
def test_une_cle_non_posee_est_absente_et_n_appelle_pas(tmp_path, doublures, valeur):
    DoublureFournisseur.modeles = ["modele-a", "modele-b"]
    ml = modele_langage(tmp_path, doublures)
    environ = {"CLE_FACTICE_A": CLE_A}
    if valeur is not None:
        environ["CLE_FACTICE_B"] = valeur

    vus = par_nom(jouer(ml, controler, environ=environ))

    b = vus["factice-b"]
    assert (b.configuree, b.disponible, b.statut) == (False, False, "absente")
    assert doublures.recus(doublures.port_b) == []
    assert len(doublures.recus(doublures.port_a)) == 1


def test_un_seul_appel_par_couple_adresse_et_variable(tmp_path, doublures):
    DoublureFournisseur.modeles = ["modele-a", "modele-b"]
    ml = modele_langage(tmp_path, doublures)
    a, b = ml.profils
    jumeau = replace(b, adresse=a.adresse, variable_cle=a.variable_cle)

    vus = par_nom(jouer(replace(ml, profils=(a, jumeau)), controler))

    assert len(doublures.recus()) == 1
    assert vus["factice-b"].disponible is True


def test_le_controle_ne_change_pas_les_profils_en_service(tmp_path, doublures):
    DoublureFournisseur.modeles = []  # aucun modèle : tout serait « indisponible »
    ml = modele_langage(tmp_path, doublures)

    async def scenario(couche: Fournisseurs) -> tuple[list[Controle], dict[str, bool]]:
        avant = dict(couche.profils)
        controles = await couche.controler_cles()
        assert couche.profils == avant
        return controles, {n: p.disponible for n, p in couche.profils.items()}

    controles, disponibles = jouer(ml, scenario)

    assert {c.disponible for c in controles} == {False}
    assert disponibles == {"factice-a": True, "factice-b": True}


def test_le_journal_nomme_la_variable_jamais_la_valeur(tmp_path, doublures, caplog):
    caplog.set_level(logging.INFO, logger="api_modele")
    DoublureFournisseur.modeles = ["modele-a", "modele-b"]
    ml = modele_langage(tmp_path, doublures)

    jouer(ml, controler)

    lignes = [
        FormatJson().format(r)
        for r in caplog.records
        if r.getMessage() == "verification_cles"
    ]
    assert len(lignes) == 2
    texte = "\n".join(lignes)
    assert "CLE_FACTICE_A" in texte and "CLE_FACTICE_B" in texte
    assert CLE_A not in texte and CLE_B not in texte


# --------------------------------------------------------------------------- #
#  La route
# --------------------------------------------------------------------------- #
def verifier(api_reponse, cle: str | None = None):
    return api_reponse.post(
        "/v1/verification", headers=entetes(cle) if cle else entetes()
    )


def test_la_route_exige_la_cle(api_reponse, doublures):
    r = api_reponse.post("/v1/verification")

    assert r.status_code == 401
    assert r.json()["code"] == "cle_invalide"
    assert doublures.recus() == []


def test_la_route_rend_chaque_profil_et_sa_date(api_reponse, doublures):
    DoublureFournisseur.modeles = ["modele-a"]

    r = verifier(api_reponse)

    assert r.status_code == 200
    corps = r.json()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", corps["verifie_le"])
    assert [p["nom"] for p in corps["profils"]] == ["factice-a", "factice-b"]
    assert all(set(p) == CHAMPS for p in corps["profils"])
    a, b = corps["profils"]
    assert (a["configuree"], a["disponible"], a["statut"]) == (True, True, "ok")
    assert (b["configuree"], b["disponible"], b["statut"]) == (True, False, "ok")
    assert a["variable_cle"] == "CLE_FACTICE_A"


def test_la_route_est_bornee_a_un_appel_par_minute_et_par_cle(
    api_reponse, doublures, horloge
):
    DoublureFournisseur.modeles = ["modele-a", "modele-b"]
    assert verifier(api_reponse).status_code == 200

    horloge.t += 59
    trop_tot = verifier(api_reponse)
    # Une autre clé a son propre compteur.
    autre = verifier(api_reponse, CLE_DEMONSTRATION)
    horloge.t += 1
    apres = verifier(api_reponse)

    assert trop_tot.status_code == 429
    assert trop_tot.json()["code"] == "debit_depasse"
    assert trop_tot.json()["details"]["limite_par_minute"] == 1
    assert trop_tot.headers["Retry-After"] == "1"
    assert autre.status_code == 200
    assert apres.status_code == 200
    # Le refus n'appelle aucun fournisseur : 3 vérifications admises × 2 adresses.
    assert len(doublures.recus()) == 6


def test_un_fournisseur_injoignable_rend_quand_meme_un_rapport(api_reponse, doublures):
    for serveur in doublures.serveurs:
        serveur.shutdown()
        serveur.server_close()

    r = verifier(api_reponse)

    assert r.status_code == 200
    assert {p["statut"] for p in r.json()["profils"]} == {"injoignable"}


@pytest.mark.parametrize(
    ("modeles", "issue"),
    [(["modele-a", "modele-b"], "ok"), (["modele-a"], "partiel"), ([], "aucune")],
)
def test_la_metrique_compte_l_issue(api_reponse, doublures, modeles, issue):
    DoublureFournisseur.modeles = modeles

    verifier(api_reponse)
    texte = api_reponse.get("/metrics").text

    assert f'api_modele_verifications_total{{issue="{issue}"}} 1.0' in texte


def test_aucune_valeur_de_cle_dans_la_reponse_le_journal_ni_les_metriques(
    api_reponse, doublures, caplog
):
    caplog.set_level(logging.INFO, logger="api_modele")
    DoublureFournisseur.modeles = ["modele-a", "modele-b"]

    reponse = verifier(api_reponse).text
    metriques = api_reponse.get("/metrics").text
    journal = "\n".join(FormatJson().format(r) for r in caplog.records)

    for valeur in CLES_FACTICES.values():
        assert valeur not in reponse
        assert valeur not in metriques
        assert valeur not in journal
    assert isinstance(doublures, Doublures)
