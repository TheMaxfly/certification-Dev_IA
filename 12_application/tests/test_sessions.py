"""Les sessions, le jeton CSRF, le compte courant et les gardes (E4, lot 1, spec 02).

L'application réelle — son `SessionMiddleware`, son enveloppe, ses pages d'erreur —
sur une base jetable, sous le rôle de test membre de `app_rw`. Les écrans de compte
arrivent à la spec 03 : des routes d'essai, ajoutées à l'application du test, ouvrent
et ferment une session et portent les gardes, par les fonctions mêmes que les écrans
emploieront.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Iterator
from dataclasses import replace
from typing import Annotated

import httpx
import psycopg
import pytest
from conftest import HACHEUR_RAPIDE, Navigateur, jeton_du_cookie, journal_complet
from fastapi import Depends, FastAPI, Request

from application import comptes, sessions
from application.app import creer_application
from application.base import Base
from application.comptes import Compte
from application.enveloppe import sans_cache
from application.metriques import Metriques
from application.sessions import NOM_COOKIE, retour_local

CLE = "k" * 48
Connecte = Annotated[Compte, Depends(sessions.exiger_connexion)]
Admin = Annotated[Compte, Depends(sessions.exiger_role("admin"))]
Libraire = Annotated[Compte, Depends(sessions.exiger_role("libraire", "admin"))]
MOT_DE_PASSE = "mot de passe factice"


def ajouter_routes(app: FastAPI) -> None:
    @app.post("/essai/ouvrir")
    async def ouvrir(request: Request) -> dict[str, object]:
        donnees = await request.form()
        sessions.exiger_jeton(request, donnees.get("jeton"))
        compte = await comptes.lire(request.app.state.base, int(str(donnees["compte"])))
        assert compte is not None
        sessions.ouvrir(request, compte)
        return {"ouverte": compte.id}

    @app.post("/essai/fermer")
    async def fermer(request: Request) -> dict[str, object]:
        sessions.exiger_jeton(request, (await request.form()).get("jeton"))
        sessions.fermer(request)
        return {"fermee": True}

    @app.get("/essai/page")
    async def page(request: Request) -> dict[str, object]:
        compte = await sessions.page(request)
        return {"compte": None if compte is None else compte.id}

    @app.get("/essai/deux-fois")
    async def deux_fois(request: Request) -> dict[str, object]:
        premier = await sessions.compte_courant(request)
        second = await sessions.page(request)
        return {"memes": premier is second}

    @app.post("/essai/protegee")
    async def protegee(compte: Connecte) -> dict[str, object]:
        return {"compte": compte.id}

    @app.get("/essai/lire")
    async def lire(request: Request) -> dict[str, object]:
        compte = await comptes.lire(request.app.state.base, 1)
        return {"compte": None if compte is None else compte.id}

    @app.get("/essai/connecte")
    async def connecte(compte: Connecte) -> dict[str, object]:
        return {"compte": compte.id}

    @app.get("/essai/admin")
    async def admin(compte: Admin) -> dict[str, object]:
        return {"compte": compte.id}

    @app.get("/essai/libraire")
    async def libraire(compte: Libraire) -> dict[str, object]:
        return {"compte": compte.id}


def creer_compte(
    dsn: str,
    pseudo: str,
    role: str = "client",
    *,
    doit_changer_mdp: bool = False,
) -> int:
    with psycopg.connect(dsn) as cx:
        (ident,) = cx.execute(
            "INSERT INTO application.comptes"
            " (pseudo, nom_affiche, role, hachage, doit_changer_mdp)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (
                pseudo,
                f"Nom {pseudo}",
                role,
                HACHEUR_RAPIDE.hacher(MOT_DE_PASSE),
                doit_changer_mdp,
            ),
        ).fetchone()
    return ident


def contenu_du_cookie(client: Navigateur) -> dict[str, object]:
    """Le contenu de la session ; vide si le cookie a été retiré (session vidée)."""
    valeur = client.cookies.get(NOM_COOKIE)
    if not valeur:
        return {}
    return json.loads(base64.b64decode(valeur.split(".")[0]))


@pytest.fixture
def appli(doublure, reglages, base_app) -> Iterator[tuple[FastAPI, Navigateur]]:
    """L'application et sa base jetable ; la doublure de l'API en mémoire."""
    avec_base = replace(reglages, base_dsn=base_app, cle_session=CLE)
    app = creer_application(
        avec_base,
        transport=httpx.ASGITransport(app=doublure.app),
        hacheur=HACHEUR_RAPIDE,
    )
    ajouter_routes(app)
    with Navigateur(
        app, client=("127.0.0.1", 50000), raise_server_exceptions=False
    ) as n:
        yield app, n


def ouvrir(client: Navigateur, ident: int) -> None:
    r = client.post("/essai/ouvrir", data={"compte": str(ident)})
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
#  Le cookie
# --------------------------------------------------------------------------- #
def test_un_visiteur_anonyme_recoit_un_cookie_qui_ne_porte_que_son_jeton(appli):
    _, client = appli
    r = client.get("/")
    entete = r.headers["set-cookie"]

    assert entete.startswith(f"{NOM_COOKIE}=")
    assert "httponly" in entete.lower() and "samesite=lax" in entete.lower()
    assert "secure" not in entete.lower(), "développement : en clair sur le bouclage"
    assert f"Max-Age={28800}" in entete
    assert set(contenu_du_cookie(client)) == {"csrf"}
    jeton = jeton_du_cookie(client)
    assert jeton and f'name="jeton" value="{jeton}"' in r.text


def test_hors_developpement_le_cookie_est_secure(doublure, reglages):
    preprod = replace(reglages, environnement="preproduction", cle_session=CLE)
    app = creer_application(
        preprod, transport=httpx.ASGITransport(app=doublure.app), hacheur=HACHEUR_RAPIDE
    )
    with Navigateur(app, client=("127.0.0.1", 50000)) as client:
        entete = client.get("/").headers["set-cookie"].lower()

    assert "secure" in entete and "httponly" in entete
    assert entete.startswith("conseiller_session_preproduction=")


def test_ouvrir_une_session_ne_porte_que_compte_generation_et_un_jeton_neuf(
    appli, base
):
    _, client = appli
    ident = creer_compte(base, "client_essai")
    client.get("/")
    avant = jeton_du_cookie(client)

    ouvrir(client, ident)

    contenu = contenu_du_cookie(client)
    assert set(contenu) == {"compte", "generation", "csrf"}
    assert contenu["compte"] == ident and contenu["generation"] == 0
    assert contenu["csrf"] != avant, "le jeton n'est pas renouvelé : fixation possible"
    assert "client_essai" not in json.dumps(contenu)


def test_fermer_la_session_vide_le_cookie(appli, base):
    _, client = appli
    ouvrir(client, creer_compte(base, "client_essai"))

    r = client.post("/essai/fermer", data={})

    assert r.status_code == 200
    assert client.get("/essai/page").json() == {"compte": None}


# --------------------------------------------------------------------------- #
#  Le compte courant, relu à chaque requête
# --------------------------------------------------------------------------- #
def test_le_compte_est_relu_et_journalise_par_son_identifiant(appli, base, caplog):
    _, client = appli
    ident = creer_compte(base, "client_essai")
    ouvrir(client, ident)

    with caplog.at_level(logging.INFO, logger="application"):
        assert client.get("/essai/page").json() == {"compte": ident}

    requetes = [r for r in caplog.records if r.getMessage() == "requete"]
    assert requetes and requetes[-1].champs["compte_id"] == ident
    assert "client_essai" not in journal_complet(caplog)


@pytest.mark.parametrize(
    "geste",
    [
        "UPDATE application.comptes SET generation = generation + 1 WHERE id = %s",
        "UPDATE application.comptes SET actif = false WHERE id = %s",
        "DELETE FROM application.comptes WHERE id = %s",
    ],
    ids=["génération changée", "compte désactivé", "compte supprimé"],
)
def test_une_session_perimee_est_videe_et_journalisee(appli, base, caplog, geste):
    _, client = appli
    ident = creer_compte(base, "client_essai")
    ouvrir(client, ident)
    with psycopg.connect(base) as cx:
        cx.execute(geste, (ident,))

    with caplog.at_level(logging.INFO, logger="application"):
        assert client.get("/essai/page").json() == {"compte": None}

    refus = [r for r in caplog.records if r.getMessage() == "session_refusee"]
    assert len(refus) == 1 and refus[0].champs["compte_id"] == ident
    assert "compte" not in contenu_du_cookie(client)
    assert client.get("/essai/page").json() == {"compte": None}


def test_le_compte_n_est_relu_qu_une_fois_par_requete(appli, base, monkeypatch):
    _, client = appli
    ouvrir(client, creer_compte(base, "client_essai"))
    lectures: list[int] = []
    lire = comptes.lire

    async def compter(base_: Base, ident: int) -> Compte | None:
        lectures.append(ident)
        return await lire(base_, ident)

    monkeypatch.setattr(comptes, "lire", compter)
    assert client.get("/essai/deux-fois").json() == {"memes": True}
    assert len(lectures) == 1


def test_base_injoignable_le_visiteur_est_anonyme_et_sa_session_gardee(
    appli, base, port_ferme
):
    app, client = appli
    ident = creer_compte(base, "client_essai")
    ouvrir(client, ident)
    en_service = app.state.base
    app.state.base = Base(
        f"postgresql://x@127.0.0.1:{port_ferme.rsplit(':', 1)[1]}/apimanga_essai",
        1.0,
        Metriques(),
    )

    assert client.get("/essai/page").json() == {"compte": None}
    assert client.get("/").status_code == 200, "le conseiller fonctionne sans la base"
    assert contenu_du_cookie(client)["compte"] == ident

    app.state.base = en_service
    assert client.get("/essai/page").json() == {"compte": ident}


def test_un_mot_de_passe_a_changer_renvoie_vers_son_changement(
    appli, base, monkeypatch
):
    _, client = appli
    ouvrir(
        client, creer_compte(base, "libraire_essai", "libraire", doit_changer_mdp=True)
    )

    for chemin in ("/", "/a-propos", "/essai/page"):
        r = client.get(chemin, follow_redirects=False)
        assert r.status_code == 303, chemin
        assert r.headers["location"] == "/compte/mot-de-passe"

    monkeypatch.setattr(
        sessions,
        "CHEMINS_AVANT_CHANGEMENT",
        sessions.CHEMINS_AVANT_CHANGEMENT | {"/essai/page"},
    )
    assert client.get("/essai/page", follow_redirects=False).status_code == 200


# --------------------------------------------------------------------------- #
#  Les gardes de route
# --------------------------------------------------------------------------- #
def test_un_anonyme_est_renvoye_vers_la_connexion_avec_son_chemin(appli):
    _, client = appli
    r = client.get("/essai/connecte?onglet=2", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/connexion?retour=/essai/connecte%3Fonglet%3D2"


def test_sans_base_une_page_protegee_dit_que_les_comptes_attendent(navigateur):
    ajouter_routes(navigateur.app)
    r = navigateur.get("/essai/connecte", follow_redirects=False)

    assert r.status_code == 503
    assert "Les comptes sont momentanément indisponibles" in r.text


@pytest.mark.parametrize(
    ("role", "chemin", "attendu"),
    [
        ("client", "/essai/admin", 403),
        ("libraire", "/essai/admin", 403),
        ("admin", "/essai/admin", 200),
        ("client", "/essai/libraire", 403),
        ("libraire", "/essai/libraire", 200),
        ("admin", "/essai/libraire", 200),
        ("client", "/essai/connecte", 200),
    ],
)
def test_une_route_reservee_refuse_les_autres_roles(appli, base, role, chemin, attendu):
    _, client = appli
    ouvrir(client, creer_compte(base, f"{role}_essai", role))

    r = client.get(chemin)

    assert r.status_code == attendu
    if attendu == 403:
        assert "Accès refusé" in r.text


def test_un_acces_refuse_est_journalise_sans_pseudo(appli, base, caplog):
    _, client = appli
    ident = creer_compte(base, "client_essai")
    ouvrir(client, ident)

    with caplog.at_level(logging.INFO, logger="application"):
        client.get("/essai/admin")

    refus = [r for r in caplog.records if r.getMessage() == "acces_refuse"]
    assert len(refus) == 1
    assert refus[0].champs == {
        "identifiant_requete": refus[0].champs["identifiant_requete"],
        "compte_id": ident,
        "route": "/essai/admin",
    }
    assert "client_essai" not in journal_complet(caplog)


# --------------------------------------------------------------------------- #
#  Le jeton CSRF
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "jeton", [None, "", "un-jeton-invente"], ids=["absent", "vide", "faux"]
)
def test_un_formulaire_sans_jeton_valide_est_refuse(appli, base, caplog, jeton):
    _, client = appli
    ident = creer_compte(base, "client_essai")
    client.get("/")
    donnees = {"compte": str(ident)} | ({} if jeton is None else {"jeton": jeton})
    client.avec_jeton = False

    with caplog.at_level(logging.INFO, logger="application"):
        r = client.post("/essai/ouvrir", data=donnees)

    assert r.status_code == 403
    assert "Ce formulaire a expiré, rechargez la page." in r.text
    assert "csrf_refuse" in caplog.messages
    assert "compte" not in contenu_du_cookie(client)


def test_le_jeton_d_une_autre_session_est_refuse(doublure, reglages, base_app):
    avec_base = replace(reglages, base_dsn=base_app, cle_session=CLE)
    app = creer_application(
        avec_base,
        transport=httpx.ASGITransport(app=doublure.app),
        hacheur=HACHEUR_RAPIDE,
    )
    ajouter_routes(app)
    with (
        Navigateur(app, client=("127.0.0.1", 50000)) as premier,
        Navigateur(app, client=("127.0.0.1", 50001)) as second,
    ):
        premier.get("/")
        second.get("/")
        etranger = jeton_du_cookie(premier)
        second.avec_jeton = False
        r = second.post("/essai/fermer", data={"jeton": etranger})

    assert etranger and r.status_code == 403


def test_le_conseiller_refuse_une_question_sans_jeton_avant_l_api(navigateur, doublure):
    navigateur.avec_jeton = False
    r = navigateur.post("/question", data={"question": "Une question factice ?"})

    assert r.status_code == 403
    assert "Ce formulaire a expiré" in r.text
    assert doublure.corps_recus == [], "l'API a été appelée sans jeton"


# --------------------------------------------------------------------------- #
#  Le chemin de retour, les pages sans cache
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [
        ("/admin/comptes?role=libraire", "/admin/comptes?role=libraire"),
        ("/", "/"),
        ("//exemple.invalid/x", "/"),
        ("/\\exemple.invalid", "/"),
        ("https://exemple.invalid/", "/"),
        ("javascript:alert(1)", "/"),
        ("/chemin\r\nEn-tete: x", "/"),
        ("", "/"),
        (None, "/"),
        (42, "/"),
    ],
)
def test_le_retour_n_accepte_qu_un_chemin_local(valeur, attendu):
    assert retour_local(valeur) == attendu


@pytest.mark.parametrize(
    ("chemin", "attendu"),
    [
        ("/connexion", True),
        ("/inscription", True),
        ("/deconnexion", True),
        ("/compte/mot-de-passe", True),
        ("/admin/comptes", True),
        ("/libraire/critiques", True),
        ("/", True),
        ("/a-propos", False),
        ("/statique/theme.css", False),
        (None, False),
    ],
)
def test_les_pages_de_compte_ne_vont_jamais_en_cache(chemin, attendu):
    assert sans_cache(chemin) is attendu


# --------------------------------------------------------------------------- #
#  Revue du bloc B : environnement, expiration, 401, 503, journal
# --------------------------------------------------------------------------- #
def test_une_session_d_un_autre_environnement_ne_vaut_pas(
    doublure, reglages, base_app, base
):
    """Même clé, même base : le cookie signé en développement n'ouvre rien ailleurs."""
    ident = creer_compte(base, "admin_essai", "admin")
    dev = replace(reglages, base_dsn=base_app, cle_session=CLE)
    preprod = replace(dev, environnement="preproduction")
    transport = httpx.ASGITransport(app=doublure.app)
    app_dev = creer_application(dev, transport=transport, hacheur=HACHEUR_RAPIDE)
    app_preprod = creer_application(
        preprod, transport=transport, hacheur=HACHEUR_RAPIDE
    )
    ajouter_routes(app_dev)
    ajouter_routes(app_preprod)
    with Navigateur(app_dev, client=("127.0.0.1", 50000)) as client:
        ouvrir(client, ident)
        valeur = client.cookies.get(NOM_COOKIE)
    with Navigateur(app_preprod, client=("127.0.0.1", 50000)) as autre:
        vu = autre.get(
            "/essai/page",
            headers={"Cookie": f"conseiller_session_preproduction={valeur}"},
        ).json()

    assert valeur and vu == {"compte": None}


def test_l_expiration_glisse_a_chaque_page(appli):
    _, client = appli
    premier = client.get("/")
    second = client.get("/")

    assert "set-cookie" in premier.headers and "set-cookie" in second.headers
    assert jeton_du_cookie(client) and second.text.count(jeton_du_cookie(client)) == 1


def test_un_formulaire_reserve_envoye_sans_session_recoit_la_page_401(appli):
    _, client = appli
    r = client.post("/essai/protegee", data={})

    assert r.status_code == 401
    assert "Connexion requise" in r.text


def test_une_base_perdue_en_cours_de_route_donne_503_jamais_500(appli, port_ferme):
    app, client = appli
    app.state.base = Base(
        f"postgresql://x@127.0.0.1:{port_ferme.rsplit(':', 1)[1]}/apimanga_essai",
        1.0,
        Metriques(),
    )
    r = client.get("/essai/lire")

    assert r.status_code == 503
    assert "Les comptes sont momentanément indisponibles" in r.text


def test_sante_dit_l_etat_de_la_base(appli, port_ferme):
    app, client = appli
    assert client.get("/sante").json()["base"] == "ok"

    app.state.base = Base(
        f"postgresql://x@127.0.0.1:{port_ferme.rsplit(':', 1)[1]}/apimanga_essai",
        1.0,
        Metriques(),
    )
    assert client.get("/sante").json()["base"] == "indisponible"


def test_un_anonyme_sur_une_route_reservee_est_renvoye_vers_la_connexion(appli):
    _, client = appli
    r = client.get("/essai/admin", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/connexion?retour=/essai/admin"


def test_le_refus_du_jeton_porte_le_compte_de_la_session(appli, base, caplog):
    _, client = appli
    ident = creer_compte(base, "client_essai")
    ouvrir(client, ident)
    client.avec_jeton = False

    with caplog.at_level(logging.INFO, logger="application"):
        client.post("/essai/fermer", data={"jeton": "faux"})

    refus = [r for r in caplog.records if r.getMessage() == "csrf_refuse"]
    assert len(refus) == 1 and refus[0].champs["compte_id"] == ident
    assert refus[0].champs["identifiant_requete"]


def test_la_base_perdue_se_journalise_avec_l_identifiant_de_la_requete(
    appli, port_ferme, caplog
):
    app, client = appli
    app.state.base = Base(
        f"postgresql://x@127.0.0.1:{port_ferme.rsplit(':', 1)[1]}/apimanga_essai",
        1.0,
        Metriques(),
    )
    with caplog.at_level(logging.INFO, logger="application"):
        r = client.get("/essai/lire")

    perdues = [x for x in caplog.records if x.getMessage() == "base_indisponible"]
    assert (
        perdues
        and perdues[0].champs["identifiant_requete"]
        == r.headers["x-identifiant-requete"]
    )
    assert "apimanga_essai" not in journal_complet(caplog)
