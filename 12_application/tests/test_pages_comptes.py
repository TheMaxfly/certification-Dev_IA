"""Les écrans des comptes (E4, lot 1, spec 03) sur une base jetable : un test nommé par
critère d'acceptation des stories V4, CL1, CL6, L1, A1 (préfixes `test_v4_`,
`test_cl1_`, `test_cl6_`, `test_l1_`, `test_a1_`), les parcours d'intégration du § 7
(`test_parcours_`), le balisage de chaque page neuve et le cas dégradé (base absente,
une route après l'autre).

L'application réelle, sous le rôle de test membre de `app_rw` ; la doublure de l'API en
mémoire ; un hacheur aux paramètres réduits. Pseudos et mots de passe factices.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import replace

import httpx
import psycopg
import pytest
from bs4 import BeautifulSoup
from conftest import HACHEUR_RAPIDE, Navigateur, jeton_du_cookie, journal_complet
from fastapi import FastAPI

from application.app import creer_application
from application.sessions import NOM_COOKIE

CLE = "c" * 48
MDP = "mot de passe factice 1"
NOUVEAU = "mot de passe factice 2"
CLIENT = ("127.0.0.1", 50000)


@pytest.fixture
def appli(doublure, reglages, base_app) -> Iterator[tuple[FastAPI, Navigateur]]:
    avec_base = replace(reglages, base_dsn=base_app, cle_session=CLE)
    app = creer_application(
        avec_base,
        transport=httpx.ASGITransport(app=doublure.app),
        hacheur=HACHEUR_RAPIDE,
    )
    with Navigateur(app, client=CLIENT, raise_server_exceptions=False) as client:
        yield app, client


def second_client(app: FastAPI) -> Navigateur:
    """Un second navigateur sur l'application déjà démarrée par le premier : sans son
    propre cycle de vie (l'ouvrir en `with` arrêterait l'application à sa sortie)."""
    return Navigateur(app, client=("127.0.0.1", 50001), raise_server_exceptions=False)


def creer_compte(
    dsn: str, pseudo: str, role: str = "client", *, doit_changer: bool = False
) -> int:
    with psycopg.connect(dsn) as cx:
        (ident,) = cx.execute(
            "INSERT INTO application.comptes"
            " (pseudo, nom_affiche, role, hachage, doit_changer_mdp)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (pseudo, f"Nom {pseudo}", role, HACHEUR_RAPIDE.hacher(MDP), doit_changer),
        ).fetchone()
    return ident


def connecter(client: Navigateur, pseudo: str, mot_de_passe: str = MDP, **champs):
    return client.post(
        "/connexion",
        data={"pseudo": pseudo, "mot_de_passe": mot_de_passe, **champs},
        follow_redirects=False,
    )


def inscrire(client: Navigateur, pseudo: str, mot_de_passe: str = MDP, **champs):
    donnees = {
        "pseudo": pseudo,
        "mot_de_passe": mot_de_passe,
        "confirmation": mot_de_passe,
    }
    return client.post("/inscription", data=donnees | champs, follow_redirects=False)


def soupe(reponse) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def menu(reponse) -> list[str]:
    nav = soupe(reponse).select_one("header nav")
    assert nav is not None
    return [e.get_text(strip=True) for e in nav.select("a, button")]


def ligne(dsn: str, requete: str, params=()) -> tuple:
    with psycopg.connect(dsn) as cx:
        return cx.execute(requete, params).fetchone()


def etat_des_comptes(dsn: str) -> tuple:
    """Ce qu'un refus ne doit pas changer : les comptes (état, génération, empreinte)
    et le journal des actions."""
    with psycopg.connect(dsn) as cx:
        comptes = cx.execute(
            "SELECT id, pseudo, role, actif, generation, hachage, doit_changer_mdp"
            " FROM application.comptes ORDER BY id"
        ).fetchall()
        (journal,) = cx.execute(
            "SELECT count(*) FROM application.journal_actions"
        ).fetchone()
    return comptes, journal


def liens_du_menu(reponse) -> dict[str, str]:
    nav = soupe(reponse).select_one("header nav")
    assert nav is not None
    return {a.get_text(strip=True): a["href"] for a in nav.select("a")}


def un_seul_focus(page: BeautifulSoup) -> str:
    """L'élément qui prend le focus au chargement : il n'y en a qu'un."""
    focus = page.select("[autofocus]")
    assert len(focus) == 1, [str(e)[:60] for e in focus]
    return focus[0]["id"]


# --------------------------------------------------------------------------- #
#  V4 — créer un compte client
# --------------------------------------------------------------------------- #
def test_v4_l_inscription_ne_demande_que_pseudo_et_mot_de_passe(appli, base):
    _, client = appli
    page = soupe(client.get("/inscription"))
    champs = {e["name"] for e in page.select("main form input")}

    assert champs == {"jeton", "retour", "pseudo", "mot_de_passe", "confirmation"}
    assert inscrire(client, "Client_Essai").status_code == 303
    assert ligne(base, "SELECT pseudo, nom_affiche, role FROM application.comptes") == (
        "client_essai",
        "client_essai",
        "client",
    )


def test_v4_un_pseudo_deja_pris_est_refuse_par_un_message_nomme(appli, base):
    _, client = appli
    creer_compte(base, "client_essai")
    r = inscrire(client, "client_essai")

    assert r.status_code == 422
    assert (
        soupe(r).select_one("#pseudo-erreur").get_text() == "Ce pseudo est déjà pris."
    )


def test_v4_les_regles_de_longueur_sont_affichees(appli):
    _, client = appli
    page = soupe(client.get("/inscription"))

    assert "De 3 à 30 caractères" in page.select_one("#pseudo-aide").get_text()
    assert "De 12 à 128 caractères" in page.select_one("#mot_de_passe-aide").get_text()


@pytest.mark.parametrize(
    ("champs", "champ_fautif", "attendu"),
    [
        ({"pseudo": "ab"}, "pseudo", "de 3 à 30 caractères"),
        ({"pseudo": "avec espace"}, "pseudo", "de 3 à 30 caractères"),
        (
            {"mot_de_passe": "court", "confirmation": "court"},
            "mot_de_passe",
            "de 12 à 128",
        ),
        ({"confirmation": "autre chose"}, "confirmation", "ne correspondent pas"),
    ],
    ids=["pseudo court", "pseudo hors forme", "mot de passe court", "confirmation"],
)
def test_v4_une_regle_non_tenue_est_dite_au_champ(
    appli, base, champs, champ_fautif, attendu
):
    _, client = appli
    donnees = {
        "pseudo": "client_essai",
        "mot_de_passe": MDP,
        "confirmation": MDP,
    } | champs
    r = client.post("/inscription", data=donnees)
    page = soupe(r)

    assert r.status_code == 422
    assert attendu in page.select_one(f"#{champ_fautif}-erreur").get_text()
    champ = page.select_one(f"#{champ_fautif}")
    assert champ["aria-invalid"] == "true" and "autofocus" in champ.attrs
    assert f"{champ_fautif}-erreur" in champ["aria-describedby"].split()
    assert ligne(base, "SELECT count(*) FROM application.comptes") == (0,)


def test_v4_le_mot_de_passe_est_hache_par_argon2id(appli, base):
    _, client = appli
    inscrire(client, "client_essai")
    (empreinte,) = ligne(base, "SELECT hachage FROM application.comptes")

    assert empreinte.startswith("$argon2id$") and MDP not in empreinte


def test_v4_une_ligne_dit_ce_qui_est_garde_avec_renvoi_vers_a_propos(appli):
    _, client = appli
    page = soupe(client.get("/inscription"))
    texte = page.select_one("main").get_text()
    lien = page.select_one('main a[href="/a-propos#comptes"]')

    assert (
        "Ce que nous gardons : votre pseudo et une empreinte de votre mot de passe,"
        " pour vos listes ; le détail dans « à propos »." in texte
    )
    assert "rien d'autre" not in texte
    assert lien is not None
    a_propos = soupe(client.get("/a-propos"))
    assert a_propos.select_one("#comptes") is not None
    assert "Aucun courriel" in a_propos.get_text()


def test_v4_apres_inscription_connecte_et_renvoye_a_la_page_d_origine(appli):
    _, client = appli
    page = soupe(client.get("/inscription?retour=/a-propos"))
    assert page.select_one("input[name=retour]")["value"] == "/a-propos"

    r = inscrire(client, "client_essai", retour="/a-propos")

    assert r.status_code == 303 and r.headers["location"] == "/a-propos"
    assert "Mon compte" in menu(client.get("/"))


def test_v4_le_retour_n_ouvre_jamais_un_autre_site(appli):
    _, client = appli
    r = inscrire(client, "client_essai", retour="//exemple.invalid/")

    assert r.headers["location"] == "/"


# --------------------------------------------------------------------------- #
#  CL1 — connexion, déconnexion
# --------------------------------------------------------------------------- #
def test_cl1_la_connexion_pose_un_cookie_signe_a_expiration(appli, base):
    _, client = appli
    creer_compte(base, "client_essai")
    r = connecter(client, "client_essai")
    cookie = r.headers["set-cookie"].lower()

    assert r.status_code == 303
    assert (
        "httponly" in cookie and "samesite=lax" in cookie and "max-age=28800" in cookie
    )
    valeur = client.cookies.get(NOM_COOKIE)
    assert valeur and valeur.count(".") == 2, "valeur.horodatage.signature"


def test_cl1_la_deconnexion_invalide_le_cookie(appli, base):
    _, client = appli
    creer_compte(base, "client_essai")
    connecter(client, "client_essai")
    copie = client.cookies.get(NOM_COOKIE)

    r = client.post("/deconnexion", data={}, follow_redirects=False)

    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "Connexion" in menu(client.get("/"))
    rejoue = client.get("/", headers={"Cookie": f"{NOM_COOKIE}={copie}"})
    assert "Connexion" in menu(rejoue), "la copie du cookie vaut encore"


def test_cl1_des_echecs_repetes_verrouillent_le_compte(appli, base):
    _, client = appli
    creer_compte(base, "client_essai")
    for i in range(5):
        assert connecter(client, "client_essai", f"faux {i}").status_code == 401

    assert connecter(client, "client_essai").status_code == 401, (
        "le verrou ne tient pas"
    )
    assert ligne(base, "SELECT verrou_jusqu_a > now() FROM application.comptes") == (
        True,
    )


def test_cl1_le_message_est_le_meme_que_le_pseudo_existe_ou_non(appli, base):
    _, client = appli
    creer_compte(base, "client_essai")
    creer_compte(base, "inactif_essai")
    creer_compte(base, "verrou_essai")
    with psycopg.connect(base) as cx:
        cx.execute(
            "UPDATE application.comptes SET actif = false WHERE pseudo ="
            " 'inactif_essai'"
        )
        cx.execute(
            "UPDATE application.comptes SET verrou_jusqu_a = now() + interval '1 hour'"
            " WHERE pseudo = 'verrou_essai'"
        )

    reponses = [
        connecter(client, "inconnu_essai"),
        connecter(client, "client_essai", "faux"),
        connecter(client, "inactif_essai"),
        connecter(client, "verrou_essai"),
    ]

    assert {r.status_code for r in reponses} == {401}
    messages = {soupe(r).select_one("#pseudo-erreur").get_text() for r in reponses}
    assert messages == {
        "Pseudo ou mot de passe incorrect. Après 5 essais manqués, la connexion est"
        " suspendue 15 minutes. Un compte désactivé ne se connecte plus : adressez-vous"
        " à l'administrateur de la librairie."
    }, "la phrase fixe est la même dans les quatre cas"
    # La page entière est la même, aux valeurs près (le pseudo rendu, le jeton).
    pages = set()
    for reponse in reponses:
        main = soupe(reponse).select_one("main")
        for champ in main.select("input[name=jeton], input#pseudo"):
            champ["value"] = "…"
        pages.add(str(main))
    assert len(pages) == 1


def test_cl1_l_erreur_de_connexion_est_au_champ_qui_prend_le_focus(appli, base):
    _, client = appli
    page = soupe(connecter(client, "inconnu_essai"))
    pseudo = page.select_one("#pseudo")

    assert un_seul_focus(page) == "pseudo"
    assert pseudo["aria-invalid"] == "true"
    assert "pseudo-erreur" in pseudo["aria-describedby"].split()


@pytest.mark.parametrize(
    "pseudo", ["client\x00essai", "\x00", "c" * 5_000], ids=["nul", "nul seul", "long"]
)
def test_cl1_un_pseudo_hors_forme_est_un_refus_comme_un_autre(appli, base, pseudo):
    _, client = appli
    creer_compte(base, "client_essai")

    connexion = connecter(client, pseudo)
    inscription = inscrire(client, pseudo)

    assert connexion.status_code == 401
    assert "Pseudo ou mot de passe incorrect." in connexion.text
    assert "\x00" not in connexion.text
    assert inscription.status_code == 422
    assert soupe(inscription).select_one("#pseudo-erreur") is not None
    assert ligne(base, "SELECT count(*) FROM application.comptes") == (1,)


# --------------------------------------------------------------------------- #
#  CL6 — changer son mot de passe
# --------------------------------------------------------------------------- #
def changer(client: Navigateur, ancien: str = MDP, nouveau: str = NOUVEAU, **champs):
    donnees = {"ancien": ancien, "nouveau": nouveau, "confirmation": nouveau} | champs
    return client.post("/compte/mot-de-passe", data=donnees)


def test_cl6_l_ancien_mot_de_passe_est_exige(appli, base):
    _, client = appli
    creer_compte(base, "client_essai")
    connecter(client, "client_essai")
    r = changer(client, ancien="pas le bon")

    assert r.status_code == 422
    assert soupe(r).select_one("#ancien-erreur").get_text() == (
        "Le mot de passe actuel est incorrect."
    )
    assert connecter(second_client(appli[0]), "client_essai").status_code == 303


@pytest.mark.parametrize(
    ("champs", "champ_fautif"),
    [
        ({"nouveau": "court", "confirmation": "court"}, "nouveau"),
        ({"confirmation": "autre chose"}, "confirmation"),
    ],
    ids=["nouveau trop court", "confirmation différente"],
)
def test_cl6_le_nouveau_suit_les_memes_regles_et_se_confirme(
    appli, base, champs, champ_fautif
):
    _, client = appli
    creer_compte(base, "client_essai")
    connecter(client, "client_essai")
    r = changer(client, **champs)

    assert r.status_code == 422
    assert soupe(r).select_one(f"#{champ_fautif}-erreur") is not None


def test_cl6_un_ancien_devine_sans_fin_pose_le_verrou(appli, base, caplog):
    _, client = appli
    ident = creer_compte(base, "client_essai")
    connecter(client, "client_essai")
    with caplog.at_level(logging.INFO, logger="application"):
        refus = [changer(client, ancien=f"faux {i}") for i in range(5)]
        pendant = changer(client)

    assert {r.status_code for r in refus} == {422}
    assert pendant.status_code == 422
    assert "Trop d'essais" in soupe(pendant).select_one("#ancien-erreur").get_text()
    assert ligne(
        base,
        "SELECT verrou_jusqu_a > now(), hachage = %s FROM application.comptes",
        (ligne(base, "SELECT hachage FROM application.comptes")[0],),
    ) == (True, True)
    assert connecter(second_client(appli[0]), "client_essai").status_code == 401
    evenements = [r.getMessage() for r in caplog.records]
    assert evenements.count("mot_de_passe_refuse") == 6
    assert "verrou_pose" in evenements
    assert "mot_de_passe_change" not in evenements
    assert ligne(
        base,
        "SELECT count(*) FROM application.journal_actions WHERE objet_id = %s"
        " AND action = 'mot_de_passe_change'",
        (ident,),
    ) == (0,)


def test_cl6_l_avis_du_changement_prend_le_focus(appli, base):
    _, client = appli
    creer_compte(base, "client_essai")
    connecter(client, "client_essai")
    page = soupe(changer(client))

    assert un_seul_focus(page) == "avis"
    assert page.select_one("#avis [role=status]") is not None
    assert page.select_one("#avis")["tabindex"] == "-1"


@pytest.mark.parametrize("initial", [False, True], ids=["choisi", "initial"])
def test_cl6_un_changement_reussi_se_voit_et_le_formulaire_s_efface(
    appli, base, initial
):
    """Passage manuel du lot 1 : après le changement, rien ne changeait à l'œil (l'avis
    remplaçait l'avis, au-dessus du même formulaire vide)."""
    _, client = appli
    creer_compte(base, "libraire_essai", "libraire", doit_changer=initial)
    connecter(client, "libraire_essai")
    page = soupe(changer(client))
    encart = page.select_one("main article#avis")

    assert encart is not None
    assert "Mot de passe changé et enregistré." in (
        encart.select_one("[role=status]").get_text()
    )
    assert encart.select_one('a[href="/"]').get_text() == "Continuer"
    assert page.select_one("main form") is None, "le formulaire vide reste affiché"
    assert un_seul_focus(page) == "avis"
    assert client.get("/", follow_redirects=False).status_code == 200


@pytest.mark.parametrize("role", ["client", "libraire", "admin"])
def test_cl6_le_changement_vaut_pour_les_trois_roles(appli, base, role):
    _, client = appli
    creer_compte(base, f"{role}_essai", role)
    connecter(client, f"{role}_essai")
    r = changer(client)

    assert r.status_code == 200 and "Mot de passe changé" in r.text
    client.post("/deconnexion", data={})
    assert connecter(client, f"{role}_essai", NOUVEAU).status_code == 303


def test_cl6_le_changement_est_journalise_sans_secret(appli, base, caplog):
    _, client = appli
    ident = creer_compte(base, "client_essai")
    connecter(client, "client_essai")
    with caplog.at_level(logging.INFO, logger="application"):
        changer(client)

    assert ligne(
        base,
        "SELECT par_compte_id, action, objet_id, details FROM"
        " application.journal_actions",
    ) == (ident, "mot_de_passe_change", ident, {})
    texte = journal_complet(caplog)
    assert "mot_de_passe_change" in texte
    for secret in (MDP, NOUVEAU, "client_essai"):
        assert secret not in texte


def test_cl6_le_changement_coupe_les_autres_sessions(appli, base):
    app, client = appli
    creer_compte(base, "client_essai")
    connecter(client, "client_essai")
    autre = second_client(app)
    connecter(autre, "client_essai")
    assert "Mon compte" in menu(autre.get("/"))

    changer(client)

    assert "Connexion" in menu(autre.get("/")), "l'autre session vit encore"
    assert "Mon compte" in menu(client.get("/")), "la session courante est tombée"


# --------------------------------------------------------------------------- #
#  L1 — connexion libraire
# --------------------------------------------------------------------------- #
def test_l1_le_libraire_se_connecte_par_le_meme_mecanisme(appli, base):
    _, client = appli
    creer_compte(base, "libraire_essai", "libraire")
    assert connecter(client, "libraire_essai").status_code == 303


@pytest.mark.parametrize(
    ("role", "voit"), [("client", False), ("libraire", True), ("admin", False)]
)
def test_l1_mes_critiques_est_au_libraire_seul(appli, base, role, voit):
    """Réécrit au lot 3 : « Mes critiques » n'est plus « bientôt disponible » ; elle est
    au libraire seul — l'administrateur modère, il n'écrit pas (E4, lot 3, décision
    2)."""
    _, client = appli
    creer_compte(base, f"{role}_essai", role)
    connecter(client, f"{role}_essai")

    assert ("Mes critiques" in menu(client.get("/"))) is voit
    r = client.get("/libraire/critiques")
    if voit:
        assert r.status_code == 200
        assert soupe(r).select_one("main h1").get_text() == "Mes critiques"
        assert "Vous n'avez pas encore écrit de critique." in soupe(r).get_text()
    else:
        assert r.status_code == 403


# --------------------------------------------------------------------------- #
#  A1 — gérer les comptes
# --------------------------------------------------------------------------- #
def creer_par_admin(client: Navigateur, pseudo: str, role: str = "libraire", **champs):
    mot_de_passe = champs.pop("mot_de_passe", MDP)
    donnees = {"pseudo": pseudo, "nom_affiche": "Libraire 1", "role": role}
    donnees |= {"mot_de_passe": mot_de_passe, "confirmation": mot_de_passe} | champs
    return client.post("/admin/comptes", data=donnees, follow_redirects=False)


@pytest.mark.parametrize("role", ["libraire", "admin"])
def test_a1_un_admin_cree_un_compte_a_mot_de_passe_initial_a_changer(appli, base, role):
    app, client = appli
    admin = creer_compte(base, "admin_essai", "admin")
    connecter(client, "admin_essai")
    r = creer_par_admin(client, "nouveau_essai", role)

    assert r.status_code == 303 and r.headers["location"] == "/admin/comptes?etat=cree"
    assert ligne(
        base,
        "SELECT role, nom_affiche, doit_changer_mdp FROM application.comptes"
        " WHERE pseudo = 'nouveau_essai'",
    ) == (role, "Libraire 1", True)
    assert ligne(
        base,
        "SELECT par_compte_id, action FROM application.journal_actions"
        " WHERE objet_id = (SELECT id FROM application.comptes WHERE pseudo ="
        " 'nouveau_essai')",
    ) == (admin, "compte_cree")
    nouveau = second_client(app)
    premiere = connecter(nouveau, "nouveau_essai")
    assert premiere.headers["location"] == "/compte/mot-de-passe"


@pytest.mark.parametrize(
    ("champs", "champ_fautif"),
    [
        ({"pseudo": "ab"}, "pseudo"),
        ({"nom_affiche": ""}, "nom_affiche"),
        ({"nom_affiche": "n" * 61}, "nom_affiche"),
        ({"nom_affiche": "Nom\x00nul"}, "nom_affiche"),
        ({"role": "client"}, "role"),
        ({"role": ""}, "role"),
        ({"mot_de_passe": "court"}, "mot_de_passe"),
        ({"confirmation": "autre chose"}, "confirmation"),
    ],
    ids=[
        "pseudo",
        "nom vide",
        "nom trop long",
        "nom à octet nul",
        "rôle client",
        "rôle absent",
        "mot de passe court",
        "confirmation",
    ],
)
def test_a1_une_creation_hors_regle_est_dite_au_champ(
    appli, base, champs, champ_fautif
):
    _, client = appli
    creer_compte(base, "admin_essai", "admin")
    connecter(client, "admin_essai")
    r = creer_par_admin(client, champs.pop("pseudo", "nouveau_essai"), **champs)

    page = soupe(r)
    assert r.status_code == 422
    assert page.select_one(f"#{champ_fautif}-erreur") is not None
    assert un_seul_focus(page) == champ_fautif
    assert page.select_one(f"#{champ_fautif}")["aria-invalid"] == "true"
    assert ligne(base, "SELECT count(*) FROM application.comptes") == (1,)


def test_a1_un_pseudo_pris_a_la_creation_est_dit(appli, base):
    _, client = appli
    creer_compte(base, "admin_essai", "admin")
    creer_compte(base, "pris_essai")
    connecter(client, "admin_essai")
    r = creer_par_admin(client, "pris_essai")

    assert r.status_code == 422
    assert (
        soupe(r).select_one("#pseudo-erreur").get_text() == "Ce pseudo est déjà pris."
    )


def test_a1_un_role_hors_liste_est_refuse_par_un_message_nomme(appli, base):
    _, client = appli
    creer_compte(base, "admin_essai", "admin")
    connecter(client, "admin_essai")
    r = creer_par_admin(client, "nouveau_essai", role="client")

    assert r.status_code == 422
    assert soupe(r).select_one("#role-erreur").get_text() == (
        "Choisissez un rôle de la liste."
    )
    assert ligne(
        base, "SELECT count(*) FROM application.comptes WHERE pseudo = 'nouveau_essai'"
    ) == (0,)


def test_a1_l_avis_d_une_action_prend_le_focus(appli, base):
    _, client = appli
    creer_compte(base, "admin_essai", "admin")
    connecter(client, "admin_essai")
    r = creer_par_admin(client, "nouveau_essai")
    page = soupe(client.get(r.headers["location"]))

    assert un_seul_focus(page) == "avis"
    assert "Compte créé" in page.select_one("#avis [role=status]").get_text()


def test_a1_reactiver_un_compte_actif_ne_change_rien_et_le_dit(appli, base):
    _, client = appli
    creer_compte(base, "admin_essai", "admin")
    libraire = creer_compte(base, "libraire_essai", "libraire")
    connecter(client, "admin_essai")
    avant = etat_des_comptes(base)

    r = client.post(
        f"/admin/comptes/{libraire}/reactiver", data={}, follow_redirects=False
    )

    assert r.headers["location"] == "/admin/comptes?etat=inchange"
    assert "rien n'a changé" in soupe(client.get(r.headers["location"])).get_text()
    assert etat_des_comptes(base) == avant, "un changement sans effet est journalisé"


@pytest.mark.parametrize(
    "ident",
    ["abc", "1e3", "-1", "9" * 30, "١٢"],
    ids=["lettres", "exposant", "négatif", "trop long", "chiffres arabes"],
)
def test_a1_un_identifiant_hors_forme_est_une_page_introuvable(appli, base, ident):
    _, client = appli
    creer_compte(base, "admin_essai", "admin")
    connecter(client, "admin_essai")
    avant = etat_des_comptes(base)

    r = client.post(f"/admin/comptes/{ident}/desactiver", data={})

    assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")
    assert etat_des_comptes(base) == avant


def test_a1_un_identifiant_hors_forme_n_est_lu_qu_apres_le_jeton_et_le_role(
    appli, base
):
    _, client = appli
    assert client.post("/admin/comptes/abc/desactiver", data={}).status_code == 401
    creer_compte(base, "client_essai")
    connecter(client, "client_essai")
    assert client.post("/admin/comptes/abc/desactiver", data={}).status_code == 403
    client.avec_jeton = False
    r = client.post("/admin/comptes/abc/desactiver", data={})
    assert r.status_code == 403 and "Ce formulaire a expiré" in r.text


def test_a1_sans_jeton_aucune_action_d_administration_ne_passe(appli, base):
    _, client = appli
    creer_compte(base, "admin_essai", "admin")
    libraire = creer_compte(base, "libraire_essai", "libraire")
    connecter(client, "admin_essai")
    client.post(f"/admin/comptes/{libraire}/desactiver", data={})
    avant = etat_des_comptes(base)
    client.avec_jeton = False

    for chemin, donnees in (
        (
            "/admin/comptes",
            {
                "pseudo": "nouveau_essai",
                "nom_affiche": "Nom",
                "role": "admin",
                "mot_de_passe": MDP,
                "confirmation": MDP,
            },
        ),
        (f"/admin/comptes/{libraire}/reactiver", {}),
        (f"/admin/comptes/{libraire}/desactiver", {}),
    ):
        r = client.post(chemin, data=donnees, follow_redirects=False)
        assert r.status_code == 403 and "Ce formulaire a expiré" in r.text, chemin
    for faux in ("faux", "", jeton_du_cookie(client) + "x"):
        r = client.post(f"/admin/comptes/{libraire}/reactiver", data={"jeton": faux})
        assert r.status_code == 403, faux
    assert etat_des_comptes(base) == avant


def test_a1_desactiver_coupe_les_sessions_et_refuse_la_connexion(appli, base):
    app, client = appli
    creer_compte(base, "admin_essai", "admin")
    libraire = creer_compte(base, "libraire_essai", "libraire")
    connecter(client, "admin_essai")
    autre = second_client(app)
    connecter(autre, "libraire_essai")
    assert "Mes critiques" in menu(autre.get("/"))

    r = client.post(
        f"/admin/comptes/{libraire}/desactiver", data={}, follow_redirects=False
    )

    assert r.headers["location"] == "/admin/comptes?etat=desactive"
    assert "Connexion" in menu(autre.get("/")), "la session du désactivé vit encore"
    assert connecter(autre, "libraire_essai").status_code == 401
    client.post(f"/admin/comptes/{libraire}/reactiver", data={})
    assert connecter(autre, "libraire_essai").status_code == 303
    actions = [
        a
        for (a,) in psycopg.connect(base).execute(
            "SELECT action FROM application.journal_actions ORDER BY id"
        )
    ]
    assert actions[-2:] == ["compte_desactive", "compte_reactive"]


def test_a1_un_admin_ne_peut_pas_se_desactiver_lui_meme(appli, base):
    _, client = appli
    admin = creer_compte(base, "admin_essai", "admin")
    connecter(client, "admin_essai")
    page = soupe(client.get("/admin/comptes"))
    r = client.post(
        f"/admin/comptes/{admin}/desactiver", data={}, follow_redirects=False
    )

    assert page.select_one(f'form[action="/admin/comptes/{admin}/desactiver"]') is None
    assert r.headers["location"] == "/admin/comptes?etat=soi"
    assert "propre compte" in client.get(r.headers["location"]).text
    assert ligne(base, "SELECT actif FROM application.comptes") == (True,)


def test_a1_un_compte_inconnu_est_dit(appli, base):
    _, client = appli
    creer_compte(base, "admin_essai", "admin")
    connecter(client, "admin_essai")
    r = client.post("/admin/comptes/999999/desactiver", data={})

    assert "Ce compte n'existe pas." in soupe(r).get_text()


def test_a1_aucune_route_ne_supprime_un_compte(appli, base):
    _, client = appli
    libraire = creer_compte(base, "libraire_essai", "libraire")
    creer_compte(base, "admin_essai", "admin")
    connecter(client, "admin_essai")

    for methode, chemin in (
        ("POST", f"/admin/comptes/{libraire}/supprimer"),
        ("DELETE", f"/admin/comptes/{libraire}"),
    ):
        assert client.request(methode, chemin).status_code in (404, 405)
    assert ligne(base, "SELECT count(*) FROM application.comptes") == (2,)


@pytest.mark.parametrize("role", ["client", "libraire"])
def test_a1_un_non_admin_n_accede_a_aucune_page_d_administration(appli, base, role):
    _, client = appli
    cible = creer_compte(base, "cible_essai")
    creer_compte(base, f"{role}_essai", role)
    connecter(client, f"{role}_essai")

    avant = etat_des_comptes(base)

    assert client.get("/admin/comptes").status_code == 403
    assert creer_par_admin(client, "nouveau_essai", "admin").status_code == 403
    assert client.post(f"/admin/comptes/{cible}/desactiver", data={}).status_code == 403
    assert client.post(f"/admin/comptes/{cible}/reactiver", data={}).status_code == 403
    assert "Comptes" not in menu(client.get("/"))
    assert etat_des_comptes(base) == avant


def test_a1_un_anonyme_est_renvoye_vers_la_connexion(appli):
    _, client = appli
    r = client.get("/admin/comptes", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/connexion?retour=/admin/comptes"
    assert client.post("/admin/comptes", data={}).status_code == 401


def test_a1_la_liste_se_filtre_par_role_et_se_pagine(appli, base):
    _, client = appli
    creer_compte(base, "admin_essai", "admin")
    for i in range(3):
        creer_compte(base, f"libraire_{i}", "libraire")
    creer_compte(base, "client_essai")
    connecter(client, "admin_essai")

    tous = soupe(client.get("/admin/comptes"))
    libraires = soupe(client.get("/admin/comptes?role=libraire"))
    inconnu = soupe(client.get("/admin/comptes?role=nimporte&page=abc"))

    assert len(tous.select("tbody tr")) == 5
    assert [td.get_text() for td in libraires.select("tbody tr td:first-child")] == [
        "libraire_0",
        "libraire_1",
        "libraire_2",
    ]
    assert len(inconnu.select("tbody tr")) == 5


def test_a1_au_dela_d_une_page_la_liste_se_parcourt(doublure, reglages, base_app, base):
    petite = replace(
        reglages,
        base_dsn=base_app,
        cle_session=CLE,
        comptes=replace(reglages.comptes, par_page=2),
    )
    app = creer_application(
        petite, transport=httpx.ASGITransport(app=doublure.app), hacheur=HACHEUR_RAPIDE
    )
    creer_compte(base, "admin_essai", "admin")
    for i in range(4):
        creer_compte(base, f"libraire_{i}", "libraire")
    with Navigateur(app, client=CLIENT) as client:
        connecter(client, "admin_essai")
        premiere = soupe(client.get("/admin/comptes"))
        deuxieme = soupe(client.get("/admin/comptes?page=2&role=libraire"))
        vide = soupe(client.get("/admin/comptes?role=client"))
        loin = client.get("/admin/comptes?page=" + "9" * 40)
        zero = client.get("/admin/comptes?page=0")

    assert "Page 1 sur 3" in premiere.get_text()
    assert premiere.select_one('a[href="/admin/comptes?page=2"]') is not None
    assert (
        deuxieme.select_one('a[href="/admin/comptes?page=1&role=libraire"]') is not None
    )
    assert "Aucun compte." in vide.get_text()
    assert loin.status_code == 200 and "Page 3 sur 3" in soupe(loin).get_text()
    assert len(soupe(loin).select("tbody tr")) == 1
    assert "Page 1 sur 3" in soupe(zero).get_text()


# --------------------------------------------------------------------------- #
#  Les parcours d'intégration (§ 7)
# --------------------------------------------------------------------------- #
def test_parcours_inscription_deconnexion_connexion(appli):
    _, client = appli
    assert inscrire(client, "client_essai").status_code == 303
    assert client.post("/deconnexion", data={}).status_code == 200
    assert "Connexion" in menu(client.get("/"))
    assert connecter(client, "client_essai").headers["location"] == "/"
    assert (
        "Connecté : client_essai"
        in soupe(client.get("/")).select_one("header nav").get_text()
    )


def test_parcours_cinq_echecs_verrou_message_identique_fin_du_verrou(appli, base):
    _, client = appli
    creer_compte(base, "client_essai")
    textes = {
        soupe(connecter(client, "client_essai", f"faux {i}"))
        .select_one("main")
        .get_text()
        for i in range(5)
    }
    pendant = connecter(client, "client_essai")

    assert len(textes) == 1 and pendant.status_code == 401
    with psycopg.connect(base) as cx:
        cx.execute(
            "UPDATE application.comptes SET verrou_jusqu_a = now() - interval '1"
            " second'"
        )
    assert connecter(client, "client_essai").status_code == 303


def test_parcours_un_formulaire_sans_jeton_est_refuse(appli, base):
    _, client = appli
    creer_compte(base, "client_essai")
    connecter(client, "client_essai")
    avant = etat_des_comptes(base)
    client.avec_jeton = False

    for chemin in (
        "/connexion",
        "/inscription",
        "/deconnexion",
        "/compte/mot-de-passe",
    ):
        r = client.post(
            chemin,
            data={
                "pseudo": "client_essai",
                "mot_de_passe": MDP,
                "confirmation": MDP,
                "ancien": MDP,
                "nouveau": NOUVEAU,
            },
        )
        assert r.status_code == 403 and "Ce formulaire a expiré" in r.text, chemin
    assert etat_des_comptes(base) == avant
    client.avec_jeton = True
    assert "Mon compte" in menu(client.get("/")), "la session a survécu aux refus"


def test_parcours_un_connecte_qui_ouvre_la_connexion_revient_au_conseiller(appli, base):
    _, client = appli
    creer_compte(base, "client_essai")
    connecter(client, "client_essai")

    for chemin in ("/connexion", "/inscription"):
        r = client.get(chemin, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/", chemin


def test_parcours_le_journal_ne_porte_aucun_secret(appli, base, caplog):
    """Inscription, échecs, connexion, changement de mot de passe : ni mot de passe, ni
    empreinte, ni pseudo, ni cookie, ni jeton dans une ligne du journal."""
    _, client = appli
    with caplog.at_level(logging.INFO, logger="application"):
        inscrire(client, "client_essai")
        jetons = [jeton_du_cookie(client)]
        client.post("/deconnexion", data={})
        for i in range(2):
            connecter(client, "client_essai", f"faux {i}")
        connecter(client, "client_essai")
        jetons.append(jeton_du_cookie(client))
        changer(client)
        cookie = client.cookies.get(NOM_COOKIE)
        jetons.append(jeton_du_cookie(client))
    (empreinte,) = ligne(base, "SELECT hachage FROM application.comptes")
    texte = journal_complet(caplog)

    evenements = [r.getMessage() for r in caplog.records]
    for nom in ("inscription", "deconnexion", "connexion_refusee", "connexion_reussie"):
        assert nom in evenements, nom
    for secret in (MDP, NOUVEAU, "faux 0", empreinte, "client_essai", cookie, *jetons):
        assert secret and secret not in texte, "un secret est au journal"


# --------------------------------------------------------------------------- #
#  Le balisage des pages neuves
# --------------------------------------------------------------------------- #
def pages_neuves(client: Navigateur, base: str) -> dict[str, BeautifulSoup]:
    rendues = {
        "inscription": client.get("/inscription"),
        "connexion": client.get("/connexion"),
        "connexion_refusee": connecter(client, "inconnu_essai"),
        "inscription_refusee": inscrire(client, "ab"),
        "formulaire_expire": client.post(
            "/connexion", data={"pseudo": "x", "jeton": "faux"}
        ),
    }
    creer_compte(base, "client_essai")
    connecter(client, "client_essai")
    rendues["acces_refuse"] = client.get("/admin/comptes")
    client.post("/deconnexion", data={})
    creer_compte(base, "admin_essai", "admin")
    connecter(client, "admin_essai")
    rendues |= {
        "mot_de_passe": client.get("/compte/mot-de-passe"),
        "mot_de_passe_refuse": changer(client, ancien="faux"),
        "admin_comptes": client.get("/admin/comptes"),
        "admin_refuse": creer_par_admin(client, "ab"),
    }
    return {nom: soupe(r) for nom, r in rendues.items()}


AUTOCOMPLETE = {
    ("inscription", "pseudo"): "username",
    ("inscription", "mot_de_passe"): "new-password",
    ("inscription", "confirmation"): "new-password",
    ("connexion", "pseudo"): "username",
    ("connexion", "mot_de_passe"): "current-password",
    ("mot_de_passe", "ancien"): "current-password",
    ("mot_de_passe", "nouveau"): "new-password",
    ("mot_de_passe", "confirmation"): "new-password",
    ("admin_comptes", "pseudo"): "off",
    ("admin_comptes", "nom_affiche"): "off",
    ("admin_comptes", "mot_de_passe"): "new-password",
}


def test_balisage_chaque_champ_a_son_etiquette_et_son_autocomplete(appli, base):
    _, client = appli
    pages = pages_neuves(client, base)

    for nom, page in pages.items():
        for champ in page.select("main input:not([type=hidden]), main select"):
            assert page.select_one(f'label[for="{champ["id"]}"]') is not None, (
                nom,
                champ,
            )
    for (nom, champ), attendu in AUTOCOMPLETE.items():
        assert pages[nom].select_one(f"main #{champ}")["autocomplete"] == attendu, (
            nom,
            champ,
        )


def test_balisage_les_erreurs_sont_reliees_a_leur_champ(appli, base):
    _, client = appli
    pages = pages_neuves(client, base)

    for nom in (
        "connexion_refusee",
        "inscription_refusee",
        "mot_de_passe_refuse",
        "admin_refuse",
    ):
        fautifs = pages[nom].select("main [aria-invalid=true]")
        assert fautifs, nom
        for champ in fautifs:
            for ident in champ["aria-describedby"].split():
                assert pages[nom].select_one(f"#{ident}") is not None, (nom, ident)


def test_balisage_titres_dans_l_ordre_et_lien_d_evitement(appli, base):
    _, client = appli
    for nom, page in pages_neuves(client, base).items():
        niveaux = [int(h.name[1]) for h in page.select("main h1, main h2, main h3")]
        assert niveaux and niveaux[0] == 1, nom
        assert all(b - a <= 1 for a, b in zip(niveaux, niveaux[1:], strict=False)), nom
        assert page.select_one('a[href="#contenu"]') is not None, nom
        assert page.select_one("main#contenu") is not None, nom
        assert page.select_one("html")["lang"] == "fr", nom


def test_balisage_les_pages_d_erreur_neuves(appli, base, navigateur):
    _, client = appli
    pages = pages_neuves(client, base)
    pages["comptes_indisponibles"] = soupe(navigateur.get("/connexion"))

    assert pages["acces_refuse"].select_one("h1").get_text() == "Accès refusé"
    assert pages["formulaire_expire"].select_one("h1").get_text() == "Formulaire expiré"
    assert pages["comptes_indisponibles"].select_one("h1").get_text() == (
        "Comptes indisponibles"
    )
    for nom in ("acces_refuse", "formulaire_expire", "comptes_indisponibles"):
        assert pages[nom].select_one('a[href="#contenu"]') is not None, nom
        assert pages[nom].select_one('main a[href="/"]') is not None, nom


def test_balisage_la_deconnexion_est_un_bouton_de_formulaire_avec_jeton(appli, base):
    _, client = appli
    creer_compte(base, "client_essai")
    connecter(client, "client_essai")
    formulaire = soupe(client.get("/")).select_one('header form[action="/deconnexion"]')

    assert formulaire is not None and formulaire["method"] == "post"
    assert formulaire.select_one("button") is not None
    assert formulaire.select_one("input[name=jeton]")["value"] == jeton_du_cookie(
        client
    )


def test_balisage_la_mention_ia_reste_sur_chaque_page(appli, base):
    _, client = appli
    for nom, page in pages_neuves(client, base).items():
        assert "Vous parlez à une IA." in page.get_text(), nom


# --------------------------------------------------------------------------- #
#  Le cas dégradé : base absente, une route après l'autre
# --------------------------------------------------------------------------- #
ROUTES = [
    ("GET", "/inscription"),
    ("POST", "/inscription"),
    ("GET", "/connexion"),
    ("POST", "/connexion"),
    ("GET", "/compte/mot-de-passe"),
    ("POST", "/compte/mot-de-passe"),
    ("GET", "/admin/comptes"),
    ("POST", "/admin/comptes"),
    ("POST", "/admin/comptes/1/desactiver"),
    ("POST", "/admin/comptes/1/reactiver"),
    ("GET", "/libraire/critiques"),
    # E4, lot 3 : les critiques et la modération, sans base.
    ("GET", "/libraire/critiques/nouvelle?series_id=101"),
    ("POST", "/libraire/critiques/nouvelle"),
    ("GET", "/libraire/critiques/1/modifier"),
    ("POST", "/libraire/critiques/1/retirer"),
    ("GET", "/admin/critiques"),
    ("POST", "/admin/critiques/1/retirer"),
]


@pytest.mark.parametrize(("methode", "chemin"), ROUTES)
def test_sans_base_chaque_page_de_compte_dit_que_les_comptes_attendent(
    navigateur, methode, chemin
):
    r = (
        navigateur.get(chemin, follow_redirects=False)
        if methode == "GET"
        else navigateur.post(chemin, data={}, follow_redirects=False)
    )

    assert r.status_code == 503, (methode, chemin)
    assert "Les comptes sont momentanément indisponibles" in r.text
    assert r.headers["cache-control"] == "no-store"
    assert navigateur.get("/").status_code == 200, "le conseiller reste accessible"


def test_un_corps_de_formulaire_illisible_est_une_demande_illisible(appli):
    _, client = appli
    r = client.post(
        "/connexion",
        content=b"illisible",
        headers={"content-type": "multipart/form-data; boundary=x"},
    )

    assert r.status_code == 400


def test_un_mot_de_passe_initial_est_annonce_a_changer(appli, base):
    _, client = appli
    creer_compte(base, "libraire_essai", "libraire", doit_changer=True)
    connecter(client, "libraire_essai")

    page = soupe(client.get("/compte/mot-de-passe"))
    assert "mot de passe initial" in page.select_one("[role=status]").get_text()
    assert client.get("/", follow_redirects=False).status_code == 303
    changer(client)
    assert client.get("/", follow_redirects=False).status_code == 200


# --------------------------------------------------------------------------- #
#  Le menu : par rôle, et ses liens ramènent à la page d'où l'on vient
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("role", "attendus", "absents"),
    [
        (None, {"Conseiller", "Connexion", "Créer un compte"}, {"Mon compte"}),
        ("client", {"Mon compte", "Déconnexion"}, {"Mes critiques", "Comptes"}),
        ("libraire", {"Mon compte", "Mes critiques"}, {"Comptes", "Connexion"}),
        ("admin", {"Mon compte", "Comptes", "Critiques"}, {"Mes critiques"}),
    ],
    ids=["anonyme", "client", "libraire", "admin"],
)
def test_menu_par_role(appli, base, role, attendus, absents):
    _, client = appli
    if role is not None:
        creer_compte(base, f"{role}_essai", role)
        connecter(client, f"{role}_essai")
    entrees = set(menu(client.get("/")))

    assert attendus <= entrees
    assert not absents & entrees


def test_menu_les_liens_de_connexion_ramenent_a_la_page_affichee(appli):
    _, client = appli

    assert liens_du_menu(client.get("/"))["Connexion"] == "/connexion"
    a_propos = liens_du_menu(client.get("/a-propos"))
    assert a_propos["Connexion"] == "/connexion?retour=/a-propos"
    assert a_propos["Créer un compte"] == "/inscription?retour=/a-propos"
    depuis = liens_du_menu(client.get("/connexion?retour=/a-propos"))
    assert depuis["Créer un compte"] == "/inscription?retour=/a-propos"
    assert (
        liens_du_menu(client.get("/connexion?retour=//ailleurs.invalid"))[
            "Créer un compte"
        ]
        == "/inscription"
    )
    assert liens_du_menu(client.post("/question", data={}))["Connexion"] == "/connexion"


def test_une_page_d_erreur_garde_le_menu_du_compte_connecte(appli, base):
    _, client = appli
    creer_compte(base, "client_essai")
    connecter(client, "client_essai")

    introuvable = client.get("/nimporte")
    client.avec_jeton = False
    expire = client.post("/compte/mot-de-passe", data={})

    for reponse in (introuvable, expire):
        assert reponse.status_code in (403, 404)
        assert "Mon compte" in menu(reponse), reponse.status_code
        formulaire = soupe(reponse).select_one('header form[action="/deconnexion"]')
        assert formulaire.select_one("input[name=jeton]")["value"] == jeton_du_cookie(
            client
        )


def test_a_propos_dit_le_seuil_et_la_duree_du_verrou_et_le_nom_affiche(appli):
    _, client = appli
    texte = soupe(client.get("/a-propos")).select_one("#comptes").get_text()

    assert "15 minutes après 5 erreurs" in texte
    assert "nom affiché" in texte
    assert (
        "Vous déconnecter ferme toutes vos sessions ouvertes, sur tous vos appareils."
        in texte
    )
