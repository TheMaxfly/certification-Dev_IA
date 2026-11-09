"""Les bornes du conseiller (E4, lot 2b, spec 02) : quota par session (visiteurs), quota
par compte client (029), plafond de débit de la clé publique, plafond de coût du jour,
deux clés d'API, inscription bornée — un test nommé par critère de V1, L6 et CL1
(préfixes `test_v1_`, `test_l6_`, `test_cl1_`), plus ceux des mécanismes.

L'horloge de l'application est injectée (`Horloge`) : la fenêtre glisse, le jour change,
sans attendre. Comptes et coûts sur une base jetable ; la doublure de l'API et du
catalogue en mémoire. Un refus n'appelle jamais l'API : le compteur de la doublure le
prouve.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import doublure_api as d
import httpx
import itsdangerous
import psycopg
import pytest
from bs4 import BeautifulSoup
from conftest import (
    CLE_SESSION_ESSAI,
    ENVIRONNEMENT,
    HACHEUR_RAPIDE,
    MDP_ESSAI,
    Navigateur,
    connecter_essai,
    creer_compte_essai,
    journal_complet,
)
from doublure_api import CLE_CATALOGUE_ATTENDUE
from fastapi import FastAPI

from application import quotas
from application.app import creer_application
from application.config import Reglages, charger
from application.sessions import NOM_COOKIE

QUESTION = "Un manga factice pour débuter ?"
#: Le 8 novembre 2026, 20:00:00 UTC.
DEBUT = datetime(2026, 11, 8, 20, 0, 0, tzinfo=UTC).timestamp()
CLE_COMPTES = "k" * 40


class Horloge:
    """L'horloge injectée : un instant qu'on avance à la main."""

    def __init__(self, instant: float = DEBUT) -> None:
        self.instant = instant

    def __call__(self) -> float:
        return self.instant

    def avancer(self, secondes: float) -> None:
        self.instant += secondes


def soupe(reponse) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def poser(client: Navigateur, question: str = QUESTION):
    return client.post("/question", data={"question": question, "historique": ""})


def configure(
    reglages: Reglages,
    base_app: str | None = None,
    cle_comptes: str | None = None,
    **bornes: float,
) -> Reglages:
    """Les bornes de la configuration (pas celles, écartées, des autres tests), sauf
    celles qu'on nomme ; la base et la clé des comptes au choix."""
    reelles = charger(ENVIRONNEMENT).bornes
    return replace(
        reglages,
        base_dsn=base_app,
        cle_session=CLE_SESSION_ESSAI,
        cle_api_comptes=cle_comptes,
        bornes=replace(reelles, **bornes),
        catalogue=replace(reglages.catalogue, cle=CLE_CATALOGUE_ATTENDUE),
    )


@pytest.fixture
def horloge() -> Horloge:
    return Horloge()


@pytest.fixture
def lancer(doublure, reglages, horloge) -> Iterator[Callable[..., FastAPI]]:
    """Démarre une application aux bornes de la configuration, à l'horloge injectée."""
    ouverts: list[Navigateur] = []

    def demarrer(**options) -> FastAPI:
        app = creer_application(
            configure(reglages, **options),
            transport=httpx.ASGITransport(app=doublure.app),
            hacheur=HACHEUR_RAPIDE,
            horloge=horloge,
        )
        cycle = Navigateur(app, client=("127.0.0.1", 50000))
        cycle.__enter__()
        ouverts.append(cycle)
        return app

    yield demarrer
    for cycle in ouverts:
        cycle.__exit__(None, None, None)


PORTS = iter(range(51000, 52000))


def visiteur(app: FastAPI) -> Navigateur:
    return Navigateur(
        app, client=("127.0.0.1", next(PORTS)), raise_server_exceptions=False
    )


def connecte(
    app: FastAPI, dsn: str, role: str, pseudo: str | None = None
) -> Navigateur:
    client = visiteur(app)
    pseudo = pseudo or f"{role}_bornes_{next(PORTS)}"
    creer_compte_essai(dsn, pseudo, role)
    connecter_essai(client, pseudo)
    return client


def reconnecte(app: FastAPI, pseudo: str) -> Navigateur:
    client = visiteur(app)
    connecter_essai(client, pseudo)
    return client


def questions_du_jour(dsn: str) -> list[tuple[str, int]]:
    with psycopg.connect(dsn) as cx:
        return [
            (str(jour), n)
            for jour, n in cx.execute(
                "SELECT jour, questions FROM application.questions_jour ORDER BY jour"
            ).fetchall()
        ]


def couts_du_jour(dsn: str) -> list[tuple[str, str, str, int, Decimal]]:
    with psycopg.connect(dsn) as cx:
        return [
            (str(jour), profil, mode, appels, cout)
            for jour, profil, mode, appels, cout in cx.execute(
                "SELECT jour, profil, mode, appels, cout_usd"
                " FROM application.couts_jour ORDER BY jour, profil, mode"
            ).fetchall()
        ]


def encart(reponse) -> str:
    return " ".join(soupe(reponse).select_one("[role=alert]").get_text().split())


def heure(instant: float) -> str:
    """L'heure telle que l'écran la dit, dans le fuseau du serveur."""
    return quotas.heure_locale(datetime.fromtimestamp(instant, UTC))


# --------------------------------------------------------------------------- #
#  V1 : le quota journalier des visiteurs (par session) — décision de Max du
#  2026-11-09 : 5 questions par jour pour un visiteur, 20 pour un client
# --------------------------------------------------------------------------- #
MINUIT = datetime(2026, 11, 9, tzinfo=UTC).timestamp()


def test_v1_un_visiteur_cinq_questions_par_jour_la_sixieme_jusqu_a_minuit(
    lancer, doublure, horloge
):
    app = lancer(debit_public_par_minute=1000)
    client = visiteur(app)

    statuts = []
    for _ in range(5):
        statuts.append(poser(client).status_code)
        horloge.avancer(600)
    r = poser(client, "La sixième question ?")

    assert statuts == [200] * 5
    assert r.status_code == 429
    assert r.headers["Retry-After"] == str(int(MINUIT - horloge()))
    assert "Vous avez posé vos 5 questions du jour" in encart(r)
    assert f"demain à {heure(MINUIT)}" in encart(r)
    assert "Un compte gratuit en donne 20 par jour." in encart(r)
    assert (
        soupe(r).select_one("[role=alert] h2").get_text()
        == "Quota de questions atteint"
    )
    assert (
        soupe(r).select_one("textarea#question").get_text() == "La sixième question ?"
    )
    assert len(doublure.corps_recus) == 5, "un refus n'appelle jamais l'API"


def test_v1_le_lendemain_le_quota_du_visiteur_repart(lancer, horloge):
    client = visiteur(lancer(debit_public_par_minute=1000))
    for _ in range(5):
        poser(client)
    assert poser(client).status_code == 429

    horloge.avancer(3 * 3600)  # 23:00 : le même jour
    assert poser(client).status_code == 429
    horloge.instant = MINUIT  # 00:00:00 UTC le 9
    assert poser(client).status_code == 200


def test_v1_ni_un_refus_ni_une_erreur_de_l_api_n_incrementent(lancer, doublure):
    client = visiteur(lancer(debit_public_par_minute=1000))
    doublure.choisir(reponse="fournisseur_injoignable")
    for _ in range(7):
        assert poser(client).status_code == 503
    doublure.choisir(reponse="nominale")

    assert [poser(client).status_code for _ in range(6)] == [200] * 5 + [429]
    assert [poser(client).status_code for _ in range(3)] == [429] * 3, "refus"
    assert len(doublure.corps_recus) == 12


def cookie_signe(contenu: dict[str, object]) -> str:
    """Un cookie de session signé de la clé de l'application (développement) : ce que
    seul le serveur sait écrire — l'essai éprouve la borne, pas la signature."""
    donnees = base64.b64encode(json.dumps(contenu).encode())
    signe = itsdangerous.TimestampSigner(f"developpement:{CLE_SESSION_ESSAI}")
    return signe.sign(donnees).decode()


def test_v1_un_cookie_a_dix_mille_horodatages_est_borne(lancer, doublure):
    client = visiteur(lancer(debit_public_par_minute=1000))
    jeton = "jeton-de-l-essai"
    client.cookies.set(
        NOM_COOKIE,
        cookie_signe({"csrf": jeton, "quota": [int(DEBUT)] * 10_000}),
        domain="testserver.local",
    )

    r = client.post(
        "/question", data={"question": QUESTION, "historique": "", "jeton": jeton}
    )
    renvoye = client.cookies.get(NOM_COOKIE)
    contenu = json.loads(base64.b64decode(renvoye.split(".")[0]))

    assert r.status_code == 429
    assert doublure.corps_recus == []
    assert contenu["quota"] == [int(DEBUT)] * 5
    assert len(renvoye) < 600


def test_v1_des_horodatages_illisibles_ou_d_un_autre_jour_sont_ecartes(lancer):
    client = visiteur(lancer(debit_public_par_minute=1000))
    jeton = "jeton-de-l-essai"
    illisibles = [
        "x",
        None,
        True,
        1.5,
        int(DEBUT) + 999,  # à venir
        int(DEBUT) - 86_400,  # la veille
        int(DEBUT) - 7200,  # le même jour, 18:00 : compte
    ]
    client.cookies.set(
        NOM_COOKIE,
        cookie_signe({"csrf": jeton, "quota": illisibles}),
        domain="testserver.local",
    )

    r = client.post(
        "/question", data={"question": QUESTION, "historique": "", "jeton": jeton}
    )

    assert r.status_code == 200
    contenu = json.loads(base64.b64decode(client.cookies[NOM_COOKIE].split(".")[0]))
    assert contenu["quota"] == [int(DEBUT) - 7200, int(DEBUT)]


# --------------------------------------------------------------------------- #
#  V1 : le quota journalier des comptes clients (029)
# --------------------------------------------------------------------------- #
def test_v1_un_client_vingt_questions_par_jour_la_vingt_et_unieme_jusqu_a_minuit(
    lancer, doublure, base_app, horloge
):
    app = lancer(base_app=base_app, debit_public_par_minute=1000)
    client = connecte(app, base_app, "client")

    statuts = [poser(client).status_code for _ in range(20)]
    horloge.avancer(3600)
    r = poser(client)

    assert statuts == [200] * 20
    assert r.status_code == 429
    assert r.headers["Retry-After"] == str(int(MINUIT - horloge()))
    assert "Vous avez posé vos 20 questions du jour" in encart(r)
    assert f"demain à {heure(MINUIT)}" in encart(r)
    assert len(doublure.corps_recus) == 20
    assert questions_du_jour(base_app) == [("2026-11-08", 20)]


def test_v1_un_compte_client_a_plus_de_questions_qu_un_visiteur():
    """La décision du 2026-11-09 : un compte donne plus de questions qu'aucun compte."""
    bornes = charger(ENVIRONNEMENT).bornes

    assert bornes.quota_client_questions_jour > bornes.quota_visiteur_questions_jour


def test_v1_le_lendemain_le_compteur_du_client_repart(lancer, base_app, horloge):
    client = connecte(
        lancer(base_app=base_app, debit_public_par_minute=1000), base_app, "client"
    )
    for _ in range(20):
        poser(client)
    assert poser(client).status_code == 429

    horloge.avancer(4 * 3600)  # 00:00:00 UTC le 9

    assert poser(client).status_code == 200
    assert questions_du_jour(base_app) == [("2026-11-08", 20), ("2026-11-09", 1)]


def test_v1_un_refus_de_l_api_n_incremente_pas_le_compte(lancer, doublure, base_app):
    client = connecte(lancer(base_app=base_app), base_app, "client")
    doublure.choisir(reponse="sortie_pseudonyme")

    assert poser(client).status_code == 503
    assert questions_du_jour(base_app) == []


def test_v1_le_quota_suit_le_compte_pas_le_cookie(lancer, base_app):
    app = lancer(base_app=base_app, debit_public_par_minute=1000)
    premier = connecte(app, base_app, "client", "client_suivi")
    for _ in range(20):
        poser(premier)

    autre = reconnecte(app, "client_suivi")  # un autre navigateur, un autre cookie

    assert poser(autre).status_code == 429


@pytest.mark.parametrize("role", ["libraire", "admin"])
def test_v1_libraires_et_admin_n_ont_pas_de_quota(lancer, base_app, role):
    client = connecte(
        lancer(base_app=base_app, debit_public_par_minute=1000), base_app, role
    )

    assert [poser(client).status_code for _ in range(22)] == [200] * 22
    assert questions_du_jour(base_app) == []


def test_v1_deconnecte_le_client_redevient_visiteur_sans_fuite(lancer, base_app):
    app = lancer(base_app=base_app, debit_public_par_minute=1000)
    client = connecte(app, base_app, "client", "client_deconnecte")
    for _ in range(20):
        poser(client)
    assert poser(client).status_code == 429

    client.post("/deconnexion", data={})

    assert [poser(client).status_code for _ in range(6)] == [200] * 5 + [429]
    assert "vos 5 questions du jour" in encart(poser(client)), "le quota du visiteur"
    assert questions_du_jour(base_app) == [("2026-11-08", 20)], "pas de fuite"


def test_v1_sans_base_un_client_redevient_visiteur(lancer, base_app, port_ferme):
    """Il n'y a pas de session connectée sans base (décision 11 du lot 1) : la base
    perdue, le compte est traité en visiteur — quota de la session, pas du compte."""
    avec_base = lancer(base_app=base_app, debit_public_par_minute=1000)
    client = connecte(avec_base, base_app, "client")
    port = port_ferme.rsplit(":", 1)[1]
    sans_base = lancer(
        base_app=f"host=127.0.0.1 port={port} dbname=essai user=essai",
        debit_public_par_minute=1000,
    )
    perdu = visiteur(sans_base)
    perdu.cookies = client.cookies

    statuts = [poser(perdu).status_code for _ in range(6)]

    assert statuts == [200] * 5 + [429]
    assert "vos 5 questions du jour" in encart(poser(perdu))


# --------------------------------------------------------------------------- #
#  V1 : le plafond global de débit de la clé publique
# --------------------------------------------------------------------------- #
def test_v1_dix_appels_publics_par_minute_le_onzieme_attend(
    lancer, doublure, base_app, horloge
):
    app = lancer(base_app=base_app, cle_comptes=CLE_COMPTES)
    premier, deuxieme, troisieme = visiteur(app), visiteur(app), visiteur(app)
    for client in (premier, deuxieme):  # deux visiteurs, cinq questions chacun
        for _ in range(5):
            assert poser(client).status_code == 200
            horloge.avancer(1)

    r = poser(troisieme)

    assert r.status_code == 429
    assert r.headers["Retry-After"] == "50"
    assert "très sollicité ; réessayez dans une minute" in encart(r)
    assert len(doublure.corps_recus) == 10
    libraire = connecte(app, base_app, "libraire")
    assert poser(libraire).status_code == 200, "la clé des comptes n'y est pas soumise"
    horloge.avancer(50)
    assert poser(troisieme).status_code == 200


def test_v1_sans_cle_des_comptes_le_libraire_compte_sous_le_plafond_public(
    lancer, base_app
):
    """Le repli : la clé publique sert aux deux, le libraire passe donc sous le même
    plafond que les visiteurs (README)."""
    app = lancer(base_app=base_app)
    for client in (visiteur(app), visiteur(app)):
        for _ in range(5):
            poser(client)

    assert poser(connecte(app, base_app, "libraire")).status_code == 429


# --------------------------------------------------------------------------- #
#  V1, L6 : le plafond de coût du jour, persisté
# --------------------------------------------------------------------------- #
def test_v1_le_cout_est_cumule_par_jour_profil_et_mode(lancer, base_app):
    client = visiteur(lancer(base_app=base_app))
    poser(client)
    poser(client)

    assert couts_du_jour(base_app) == [
        ("2026-11-08", "mistral-large-2512", "strict", 2, Decimal("0.000320"))
    ]


def test_l6_les_appels_enrichis_comptent_sous_le_mode_rendu(lancer, doublure, base_app):
    """Le profil et le mode renvoyés par l'API, pas ceux demandés : le libraire demande
    gpt-6-luna en enrichi ; l'API rend mistral-large-2512, en enrichi."""
    doublure.choisir(info="deux_modes", reponse="enrichi_avec_etiquette")
    libraire = connecte(lancer(base_app=base_app), base_app, "libraire")

    libraire.post(
        "/question",
        data={"question": QUESTION, "profil": "gpt-6-luna", "mode": "enrichi"},
    )

    assert doublure.corps_recus[-1]["profil"] == "gpt-6-luna"
    assert couts_du_jour(base_app) == [
        ("2026-11-08", "mistral-large-2512", "enrichi", 1, Decimal("0.000160"))
    ]


def ajouter_cout(base: str, jour: str, cout: str) -> None:
    with psycopg.connect(base) as cx:
        cx.execute(
            "INSERT INTO application.couts_jour (jour, profil, mode, appels, cout_usd)"
            " VALUES (%s, 'profil_factice', 'strict', 1, %s)",
            (jour, Decimal(cout)),
        )


@pytest.mark.parametrize("role", [None, "libraire"])
def test_v1_budget_du_jour_atteint_503_avant_l_appel(
    lancer, doublure, base_app, base, horloge, role
):
    ajouter_cout(base, "2026-11-08", "0.500000")
    app = lancer(base_app=base_app, cle_comptes=CLE_COMPTES)
    client = visiteur(app) if role is None else connecte(app, base_app, role)

    r = poser(client)

    assert r.status_code == 503
    minuit = datetime(2026, 11, 9, tzinfo=UTC).timestamp()
    assert r.headers["Retry-After"] == str(int(minuit - horloge()))
    assert "a atteint son budget du jour ; il reprend demain" in encart(r)
    assert doublure.corps_recus == []
    metriques = client.get("/metrics").text
    assert 'application_quota_refus_total{motif="cout"} 1.0' in metriques
    assert "application_cout_jour_usd 0.5" in metriques
    assert 'application_plafond_cout_etat{etat="atteint"} 1.0' in metriques


def test_v1_le_budget_d_hier_ne_compte_pas_aujourd_hui(lancer, base_app, base):
    ajouter_cout(base, "2026-11-07", "9.000000")

    assert poser(visiteur(lancer(base_app=base_app))).status_code == 200


def test_v1_a_minuit_utc_le_jour_change(lancer, base_app, horloge):
    horloge.instant = datetime(2026, 11, 8, 23, 59, 59, tzinfo=UTC).timestamp()
    client = visiteur(lancer(base_app=base_app))
    poser(client)
    horloge.avancer(1)
    poser(client)

    assert [ligne[0] for ligne in couts_du_jour(base_app)] == [
        "2026-11-08",
        "2026-11-09",
    ]


def test_v1_un_cout_absent_compte_zero_et_le_journal_le_dit(
    lancer, doublure, base_app, caplog
):
    doublure.choisir(reponse="cout_inconnu")
    caplog.set_level(logging.INFO, logger="application")

    poser(visiteur(lancer(base_app=base_app)))

    assert couts_du_jour(base_app) == [
        ("2026-11-08", "mistral-large-2512", "strict", 1, Decimal("0"))
    ]
    assert "cout_absent" in journal_complet(caplog)


def test_v1_sans_base_le_plafond_est_non_verifiable_et_l_appel_part(
    lancer, doublure, caplog
):
    caplog.set_level(logging.INFO, logger="application")
    client = visiteur(lancer())

    r = poser(client)

    assert r.status_code == 200
    assert len(doublure.corps_recus) == 1
    journal = journal_complet(caplog)
    assert "plafond_non_verifiable" in journal and "cout_non_compte" in journal
    assert 'application_plafond_cout_etat{etat="inconnu"} 1.0' in (
        client.get("/metrics").text
    )


# --------------------------------------------------------------------------- #
#  Les deux clés (décision 5)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("role", "attendue"),
    [(None, d.CLE_ATTENDUE), ("client", CLE_COMPTES), ("libraire", CLE_COMPTES)],
    ids=["visiteur", "client", "libraire"],
)
def test_la_cle_de_l_appel_selon_le_compte(lancer, doublure, base_app, role, attendue):
    app = lancer(base_app=base_app, cle_comptes=CLE_COMPTES)
    client = visiteur(app) if role is None else connecte(app, base_app, role)

    poser(client)

    assert doublure.cles_recues[-1] == attendue


def test_sans_cle_des_comptes_la_publique_sert_aux_deux(
    lancer, doublure, base_app, caplog
):
    caplog.set_level(logging.INFO, logger="application")
    app = lancer(base_app=base_app)

    poser(connecte(app, base_app, "libraire"))

    assert doublure.cles_recues[-1] == d.CLE_ATTENDUE
    assert "cle_comptes_absente" in journal_complet(caplog)
    assert visiteur(app).get("/sante").json()["cles_api"] == "une"


def test_avec_cle_des_comptes_la_sante_dit_deux(lancer, caplog):
    caplog.set_level(logging.INFO, logger="application")
    app = lancer(cle_comptes=CLE_COMPTES)

    assert visiteur(app).get("/sante").json()["cles_api"] == "deux"
    assert "cle_comptes_absente" not in journal_complet(caplog)


def test_aucune_cle_ni_question_au_journal(lancer, base_app, caplog):
    caplog.set_level(logging.DEBUG)
    app = lancer(base_app=base_app, cle_comptes=CLE_COMPTES)
    question = "Une question factice et reconnaissable, zéphyr ?"
    for client in (visiteur(app), connecte(app, base_app, "client")):
        poser(client, question)

    journal = journal_complet(caplog)
    assert "zéphyr" not in journal
    assert CLE_COMPTES not in journal and d.CLE_ATTENDUE not in journal


# --------------------------------------------------------------------------- #
#  CL1 : l'inscription bornée par session
# --------------------------------------------------------------------------- #
def inscrire(client: Navigateur, pseudo: str):
    return client.post(
        "/inscription",
        data={
            "pseudo": pseudo,
            "mot_de_passe": MDP_ESSAI,
            "confirmation": MDP_ESSAI,
            "retour": "/",
        },
        follow_redirects=False,
    )


def comptes_en_base(dsn: str) -> int:
    with psycopg.connect(dsn) as cx:
        return cx.execute("SELECT count(*) FROM application.comptes").fetchone()[0]


def test_cl1_trois_inscriptions_passent_la_quatrieme_est_refusee(
    lancer, base_app, horloge
):
    client = visiteur(lancer(base_app=base_app))
    statuts = []
    for i in range(3):
        statuts.append(inscrire(client, f"inscrit_borne_{i}").status_code)
        client.post("/deconnexion", data={})
        horloge.avancer(60)
    avant = comptes_en_base(base_app)

    r = inscrire(client, "inscrit_borne_3")

    assert statuts == [303] * 3
    assert r.status_code == 429
    assert r.headers["Retry-After"] == str(3600 - 180)
    page = soupe(r)
    assert page.select_one("[role=alert] h2").get_text() == "Trop de comptes créés"
    assert f"réessayez à {heure(DEBUT + 3600)}" in encart(r)
    assert page.select_one("#pseudo")["value"] == "inscrit_borne_3"
    assert comptes_en_base(base_app) == avant
    assert 'application_quota_refus_total{motif="inscription"} 1.0' in (
        client.get("/metrics").text
    )
    horloge.avancer(3600)
    assert inscrire(client, "inscrit_borne_3").status_code == 303


def test_cl1_une_inscription_refusee_par_la_saisie_ne_compte_pas(lancer, base_app):
    client = visiteur(lancer(base_app=base_app))
    for _ in range(5):
        assert inscrire(client, "x").status_code == 422

    assert inscrire(client, "inscrit_apres_refus").status_code == 303


# --------------------------------------------------------------------------- #
#  Les métriques des questions
# --------------------------------------------------------------------------- #
def test_les_questions_sont_comptees_par_mode_et_issue(lancer, doublure):
    client = visiteur(lancer(debit_public_par_minute=1000))
    poser(client)
    doublure.choisir(reponse="refus")
    poser(client)
    doublure.choisir(reponse="fournisseur_injoignable")
    poser(client)

    metriques = client.get("/metrics").text
    for issue in ("reponse", "refus", "api_erreur"):
        assert f'application_questions_total{{issue="{issue}",mode="strict"}} 1.0' in (
            metriques
        ), issue
    assert 'application_questions_total{issue="quota",mode="enrichi"} 0.0' in metriques
    for motif in ("session", "client", "debit", "cout", "inscription"):
        assert f'application_quota_refus_total{{motif="{motif}"}} 0.0' in metriques


def test_une_api_injoignable_compte_api_indisponible(lancer, monkeypatch):
    from application.client_api import ApiInjoignable

    app = lancer()

    async def coupee(_demande, cle=None):
        raise ApiInjoignable

    monkeypatch.setattr(app.state.vue.client, "reponse", coupee)
    client = visiteur(app)

    assert poser(client).status_code == 503
    assert (
        'application_questions_total{issue="api_indisponible",mode="strict"} 1.0'
        in client.get("/metrics").text
    )


def test_v1_la_base_perdue_apres_la_reponse_la_reponse_reste_affichee(
    lancer, base_app, monkeypatch, caplog
):
    """La base tombe entre la réponse et le compteur : la réponse payée s'affiche ;
    le journal dit que la question n'est pas comptée."""
    from application.base import BaseIndisponible

    caplog.set_level(logging.INFO, logger="application")
    client = connecte(lancer(base_app=base_app), base_app, "client")

    async def perdue(*_args):
        raise BaseIndisponible("interrompue")

    monkeypatch.setattr(quotas, "compter_question", perdue)
    r = poser(client)

    assert r.status_code == 200
    assert soupe(r).select_one("h3#serie-101") is not None
    assert "quota_non_compte" in journal_complet(caplog)
