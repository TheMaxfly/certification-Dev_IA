"""E4, lot 7, spec 01 — l'apparence (AP1) : la couleur de chacun des six espaces,
l'intensité du lavis et la matière, réglées par l'administrateur.

Le modèle (liste blanche, défauts, dictionnaires de constantes, partages) ; la lecture
au rendu (une requête, une fois par requête, base absente ou injoignable → défauts) ;
l'écran `/admin/apparence` (groupes, cochés, qui et quand, avertissement, `POST`, refus,
rôles, retour à l'origine). Base jetable, doublure de l'API.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import psycopg
import pytest
from bs4 import BeautifulSoup
from conftest import HACHEUR_RAPIDE, Navigateur, journal_complet

from application import apparence as ap
from application import reglages_app
from application.app import creer_application
from application.config import Reglages

#: Une valeur piégée, posée en base par le test (jamais par l'interface).
PIEGE = 'sakura" onload="x'
#: Le réglage des essais : chaque espace prend la couleur du suivant, 100 %, aplat.
DECALE = {
    "apparence_menthe": "ciel",
    "apparence_ciel": "sakura",
    "apparence_sakura": "yuzu",
    "apparence_yuzu": "glycine",
    "apparence_glycine": "brume",
    "apparence_brume": "menthe",
    "apparence_intensite": "100",
    "apparence_matiere": "aplat",
}


def soupe(reponse: Any) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def classes(reponse: Any) -> str:
    return " ".join(soupe(reponse).body.get("class", []))


def poser(dsn: str, valeurs: dict[str, str]) -> None:
    with psycopg.connect(dsn) as cx:
        for cle, valeur in valeurs.items():
            cx.execute(
                "INSERT INTO application.reglages (cle, valeur) VALUES (%s, %s)"
                " ON CONFLICT (cle) DO UPDATE SET valeur = EXCLUDED.valeur",
                (cle, valeur),
            )


def en_base(dsn: str) -> dict[str, tuple[str, int | None]]:
    with psycopg.connect(dsn) as cx:
        lignes = cx.execute(
            "SELECT cle, valeur, modifie_par FROM application.reglages"
            " WHERE cle LIKE 'apparence_%'"
        ).fetchall()
    return {cle: (valeur, par) for cle, valeur, par in lignes}


def journal_apparence(dsn: str) -> int:
    with psycopg.connect(dsn) as cx:
        (n,) = cx.execute("SELECT count(*) FROM application.journal_actions").fetchone()
    return int(n)


def formulaire(**changes: str) -> dict[str, str]:
    return {**ap.DEFAUT.valeurs(), **changes}


def appliquer(client: Navigateur, **changes: str):
    return client.post(
        "/admin/apparence", data=formulaire(**changes), follow_redirects=False
    )


# --------------------------------------------------------------------------- #
#  Le modèle
# --------------------------------------------------------------------------- #
def test_les_listes_fermees_et_les_cles():
    assert (
        ap.ESPACES
        == ap.COULEURS
        == (
            "menthe",
            "ciel",
            "sakura",
            "yuzu",
            "glycine",
            "brume",
        )
    )
    assert ap.INTENSITES == ("60", "70", "85", "100")
    assert ap.MATIERES == ("aquarelle", "aplat")
    assert ap.CLES == (
        "apparence_menthe",
        "apparence_ciel",
        "apparence_sakura",
        "apparence_yuzu",
        "apparence_glycine",
        "apparence_brume",
        "apparence_intensite",
        "apparence_matiere",
    )
    assert set(ap.CLES) < set(reglages_app.CLES)
    assert set(ap.CLASSE_COULEUR) == set(ap.TOUCHE_COULEUR) == set(ap.COULEURS)
    assert set(ap.CLASSE_INTENSITE) == set(ap.INTENSITES)
    assert set(ap.CLASSE_MATIERE) == set(ap.MATIERES)


@pytest.mark.parametrize(
    ("cle", "valeur", "admise"),
    [
        ("apparence_ciel", "sakura", "sakura"),
        ("apparence_ciel", "rouge", None),
        ("apparence_ciel", "Sakura", None),
        ("apparence_ciel", "85", None),
        ("apparence_ciel", None, None),
        ("apparence_intensite", "60", "60"),
        ("apparence_intensite", "50", None),
        ("apparence_intensite", "menthe", None),
        ("apparence_matiere", "aplat", "aplat"),
        ("apparence_matiere", "gouache", None),
        ("profil_actif", "menthe", None),
    ],
)
def test_valider_contre_la_liste_de_la_cle(cle, valeur, admise):
    assert ap.valider(cle, valeur) == admise


def test_sans_reglage_le_defaut_est_l_origine():
    assert ap.depuis_reglages({}) == ap.DEFAUT
    assert dict(ap.DEFAUT.couleurs) == {e: e for e in ap.ESPACES}
    assert (ap.DEFAUT.intensite, ap.DEFAUT.matiere) == ("85", "aquarelle")
    assert ap.DEFAUT.valeurs() == {
        **{f"apparence_{e}": e for e in ap.ESPACES},
        "apparence_intensite": "85",
        "apparence_matiere": "aquarelle",
    }


def test_l_apparence_est_immuable():
    with pytest.raises(AttributeError):
        ap.DEFAUT.intensite = "60"  # type: ignore[misc]
    with pytest.raises(TypeError):
        ap.DEFAUT.couleurs["menthe"] = "yuzu"  # type: ignore[index]


def test_une_valeur_piegee_ne_vaut_que_le_defaut_de_sa_cle(caplog):
    with caplog.at_level(logging.INFO, logger="application"):
        lue = ap.depuis_reglages(
            {"apparence_ciel": PIEGE, "apparence_sakura": "menthe"}
        )

    assert lue.couleurs["ciel"] == "ciel"
    assert lue.couleurs["sakura"] == "menthe"
    journal = journal_complet(caplog)
    assert "apparence_valeur_ignoree" in journal
    assert "apparence_ciel" in journal
    assert PIEGE not in journal and "onload" not in journal


@pytest.mark.parametrize(
    ("espace", "attendu"),
    [
        ("menthe", "esp-ciel intensite-100 matiere-aplat"),
        ("ciel", "esp-sakura intensite-100 matiere-aplat"),
        ("brume", "esp-menthe intensite-100 matiere-aplat"),
        (" sakura\n", "esp-yuzu intensite-100 matiere-aplat"),
        ("inconnu", "esp-sakura intensite-100 matiere-aplat"),
    ],
)
def test_les_classes_par_dictionnaires(espace, attendu):
    assert ap.classes_body(ap.depuis_reglages(DECALE), espace) == attendu


def test_sans_reglage_une_seule_classe_celle_de_l_espace():
    for espace in ap.ESPACES:
        assert ap.classes_body(ap.DEFAUT, espace) == f"esp-{espace}"
    assert ap.classes_body(ap.DEFAUT, "") == "esp-ciel"


@pytest.mark.parametrize(
    ("intensite", "matiere", "suite"),
    [
        ("60", "aquarelle", " intensite-60"),
        ("70", "aquarelle", " intensite-70"),
        ("85", "aplat", " matiere-aplat"),
        ("100", "aplat", " intensite-100 matiere-aplat"),
    ],
)
def test_intensite_et_matiere(intensite, matiere, suite):
    reglee = ap.Apparence(intensite=intensite, matiere=matiere)
    assert ap.classes_body(reglee, "yuzu") == "esp-yuzu" + suite


def test_une_valeur_hostile_n_atteint_jamais_une_classe_ni_un_chemin():
    """Mutation E4P-02 : même une apparence construite sans validation (ce que le
    code ne fait pas) ne compose ni classe ni chemin depuis la valeur."""
    hostile = ap.Apparence(
        couleurs={**{e: e for e in ap.ESPACES}, "sakura": PIEGE},
        intensite='60" x="',
        matiere="</body>",
    )

    assert ap.classes_body(hostile, "sakura") == "esp-sakura"
    assert ap.classe_couleur(PIEGE, "sakura") == "esp-sakura"
    assert ap.touche(PIEGE) == "/statique/touches/touche-ciel.webp"
    assert ap.touche("yuzu") == "/statique/touches/touche-yuzu.webp"


def test_les_partages():
    assert ap.partages(ap.DEFAUT) == []
    assert ap.partages(ap.depuis_reglages(DECALE)) == []
    reglee = ap.depuis_reglages(
        {"apparence_ciel": "menthe", "apparence_sakura": "menthe"}
    )
    assert ap.partages(reglee) == [("menthe", ["menthe", "ciel", "sakura"])]
    tous = ap.depuis_reglages({f"apparence_{e}": "yuzu" for e in ap.ESPACES})
    assert ap.partages(tous) == [("yuzu", list(ap.ESPACES))]


# --------------------------------------------------------------------------- #
#  La lecture au rendu
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("chemin", ["/", "/catalogue", "/a-propos", "/route-inconnue"])
def test_sans_base_les_defauts_sans_evenement(navigateur, caplog, chemin):
    with caplog.at_level(logging.INFO, logger="application"):
        r = navigateur.get(chemin)

    assert classes(r) in {"esp-menthe", "esp-ciel"}
    assert "apparence" not in journal_complet(caplog)


def test_base_injoignable_les_defauts_et_la_page(
    doublure, reglages, port_ferme, caplog
):
    """Mutation E4P-04 : une base qui ne répond pas donne les défauts, jamais une
    erreur ; aucun événement propre à l'apparence."""
    port = port_ferme.rsplit(":", 1)[1]
    reglee = replace(
        reglages, base_dsn=f"postgresql://x@127.0.0.1:{port}/apimanga_essai"
    )
    app = creer_application(reglee, hacheur=HACHEUR_RAPIDE)
    with (
        caplog.at_level(logging.INFO, logger="application"),
        Navigateur(app, client=("127.0.0.1", 50000)) as client,
    ):
        accueil = client.get("/")
        catalogue = client.get("/catalogue")

    assert accueil.status_code == 200 and classes(accueil) == "esp-menthe"
    assert catalogue.status_code in {200, 503}
    assert classes(catalogue) == "esp-ciel"
    assert "apparence" not in journal_complet(caplog)


def test_la_lecture_une_requete_une_fois_par_requete(connecter, base, monkeypatch):
    poser(base, DECALE)
    lu = reglages_app.lire_plusieurs
    appels: list[tuple[str, ...]] = []

    async def compter(base_: Any, cles: tuple[str, ...]) -> Any:
        appels.append(cles)
        return await lu(base_, cles)

    monkeypatch.setattr(reglages_app, "lire_plusieurs", compter)
    client = connecter(None)
    appels.clear()
    r = client.get("/")

    assert classes(r) == "esp-ciel intensite-100 matiere-aplat"
    assert appels == [ap.CLES]
    appels.clear()
    client.get("/route-inconnue")
    assert appels == [ap.CLES]
    appels.clear()
    client.get("/sante")
    assert appels == []


def test_une_valeur_piegee_en_base_est_ignoree_et_journalisee_par_sa_cle(
    connecter, base, caplog
):
    poser(base, {"apparence_menthe": PIEGE, "apparence_intensite": "<b>"})
    client = connecter(None)
    with caplog.at_level(logging.INFO, logger="application"):
        r = client.get("/")

    assert r.status_code == 200
    assert classes(r) == "esp-menthe"
    assert "onload" not in r.text
    journal = journal_complet(caplog)
    assert "apparence_menthe" in journal and "apparence_intensite" in journal
    assert PIEGE not in journal and "<b>" not in journal


def test_apres_reglage_chaque_espace_rend_sa_couleur(connecter, base):
    poser(base, DECALE)
    visiteur = connecter(None)
    client = connecter("client")
    libraire = connecter("libraire")
    admin = connecter("admin")
    suite = " intensite-100 matiere-aplat"

    assert classes(visiteur.get("/")) == "esp-ciel" + suite
    assert classes(visiteur.get("/catalogue")) == "esp-sakura" + suite
    assert classes(visiteur.get("/series/101")) == "esp-yuzu" + suite
    assert classes(client.get("/compte/listes")) == "esp-glycine" + suite
    assert classes(libraire.get("/libraire/critiques")) == "esp-brume" + suite
    assert classes(admin.get("/admin/apparence")) == "esp-menthe" + suite
    # Les pages d'erreur (avec requête) suivent l'espace de leur gabarit (ciel).
    assert classes(visiteur.get("/route-inconnue")) == "esp-sakura" + suite
    assert classes(client.get("/admin/comptes")) == "esp-sakura" + suite


def test_l_erreur_de_l_enveloppe_reste_a_l_origine(connecter, base):
    """La page 413, rendue sans requête par l'enveloppe, ne lit pas la base."""
    poser(base, DECALE)
    r = connecter(None).post("/question", content=b"x" * 16385)

    assert r.status_code == 413
    assert classes(r) == "esp-ciel"


def test_la_metrique_de_l_apparence(connecter, base):
    poser(base, DECALE)
    client = connecter(None)
    client.get("/")
    texte = client.get("/metrics").text

    assert 'application_apparence_info{couleur="ciel",espace="menthe"} 1.0' in texte
    assert 'application_apparence_info{couleur="menthe",espace="menthe"} 0.0' in texte
    assert 'application_apparence_info{couleur="menthe",espace="brume"} 1.0' in texte


# --------------------------------------------------------------------------- #
#  L'écran /admin/apparence
# --------------------------------------------------------------------------- #
def coches(page: BeautifulSoup) -> dict[str, str]:
    return {
        r["name"]: r["value"]
        for r in page.select(".zone-apparence input[type=radio][checked]")
    }


def test_la_page_a_l_origine(admin):
    page = soupe(admin.get("/admin/apparence"))

    onglets = [a.get_text(strip=True) for a in page.select("nav.onglets a")]
    assert onglets[-1] == "Apparence" and len(onglets) == 7
    assert page.select_one("nav.onglets a[aria-current=page]")["href"] == (
        "/admin/apparence"
    )
    legendes = [g.get_text(strip=True) for g in page.select("form legend")]
    assert legendes == [
        "Conseiller",
        "Catalogue et comptes",
        "Fiche de série",
        "Mon compte et listes",
        "Espace libraire",
        "Administration",
        "Intensité du lavis",
        "Matière",
    ]
    assert coches(page) == ap.DEFAUT.valeurs()
    groupe = page.select("form fieldset")[0]
    assert [lab.get_text(strip=True) for lab in groupe.select("label")] == list(
        ap.COULEURS
    )
    # La pastille : la touche du pigment, décorative ; le nom est écrit.
    images = [i["href"] for i in groupe.select("svg image")]
    assert images == [ap.TOUCHE_COULEUR[c] for c in ap.COULEURS]
    assert all(s["aria-hidden"] == "true" for s in groupe.select("svg"))
    assert "Apparence d'origine" in page.get_text()
    assert page.select_one(".zone-apparence-partage") is None
    assert page.select_one('form[action="/admin/apparence/origine"] button')
    assert not page.select("[style], style")


def test_appliquer_ecrit_les_huit_cles_sans_journal(admin, base):
    avant = journal_apparence(base)
    r = appliquer(
        admin,
        apparence_menthe="yuzu",
        apparence_sakura="menthe",
        apparence_intensite="100",
        apparence_matiere="aplat",
    )

    assert r.status_code == 303
    assert r.headers["location"] == "/admin/apparence?etat=appliquee"
    lignes = en_base(base)
    assert len(lignes) == 8
    assert {c: v for c, (v, _) in lignes.items()} == formulaire(
        apparence_menthe="yuzu",
        apparence_sakura="menthe",
        apparence_intensite="100",
        apparence_matiere="aplat",
    )
    assert len({par for _, par in lignes.values()}) == 1
    assert journal_apparence(base) == avant
    page = admin.get(r.headers["location"])
    assert classes(page) == "esp-brume intensite-100 matiere-aplat"
    contenu = soupe(page)
    assert "Apparence appliquée." in contenu.get_text()
    assert "Choisi par Nom admin_essai_" in contenu.get_text()
    assert coches(contenu)["apparence_menthe"] == "yuzu"
    # Conseiller et « mon compte » partagent yuzu : l'avertissement.
    partage = contenu.select_one(".zone-apparence-partage [role=status]")
    assert partage is not None
    assert "Deux espaces partagent la couleur yuzu" in partage.get_text()


def test_la_meme_apparence_est_inchangee(admin, base):
    assert appliquer(admin).headers["location"].endswith("?etat=appliquee")
    r = appliquer(admin)

    assert r.headers["location"] == "/admin/apparence?etat=inchangee"
    assert "Apparence inchangée" in soupe(admin.get(r.headers["location"])).get_text()


def test_l_avertissement_des_couleurs_partagees(admin):
    """Mutation E4P-05 : un avertissement, pas un refus (décision 6)."""
    r = appliquer(admin, apparence_ciel="menthe", apparence_sakura="menthe")
    page = soupe(admin.get(r.headers["location"]))

    texte = page.select_one(".zone-apparence-partage [role=status]").get_text()
    assert "Trois espaces partagent la couleur menthe" in texte
    assert "Conseiller, Catalogue et comptes, Fiche de série" in texte
    assert coches(page)["apparence_sakura"] == "menthe"


@pytest.mark.parametrize(
    "fautif",
    [
        {"apparence_ciel": "rouge"},
        {"apparence_intensite": "50"},
        {"apparence_matiere": "gouache"},
    ],
)
def test_une_valeur_hors_liste_est_refusee_rien_n_est_ecrit(admin, base, fautif):
    """Mutation E4P-01 : hors liste → 422, rien d'écrit."""
    r = admin.post(
        "/admin/apparence", data={**formulaire(), **fautif}, follow_redirects=False
    )

    assert r.status_code == 422
    assert en_base(base) == {}
    page = soupe(r)
    (cle,) = fautif
    groupe = page.select_one(f"fieldset[aria-describedby='{cle}-erreur']")
    assert groupe is not None
    assert page.select_one(f"#{cle}-erreur").get_text(strip=True)
    assert len(page.select("fieldset[aria-describedby]")) == 1
    assert page.select_one(f"input[name='{cle}'][autofocus]") is not None


def test_un_champ_absent_est_refuse(admin, base):
    donnees = formulaire()
    del donnees["apparence_brume"]
    r = admin.post("/admin/apparence", data=donnees, follow_redirects=False)

    assert r.status_code == 422
    assert en_base(base) == {}


def test_sans_jeton_403_rien_n_est_ecrit(admin, base):
    admin.avec_jeton = False
    r = appliquer(admin, apparence_ciel="sakura")
    o = admin.post("/admin/apparence/origine", data={}, follow_redirects=False)

    assert r.status_code == o.status_code == 403
    assert en_base(base) == {}


@pytest.mark.parametrize("role", ["client", "libraire"])
def test_un_autre_role_recoit_403(
    connecter: Callable[[str | None], Navigateur], base, role
):
    """Mutation E4P-03 : la garde de rôle."""
    client = connecter(role)

    assert client.get("/admin/apparence").status_code == 403
    assert appliquer(client, apparence_ciel="sakura").status_code == 403
    origine = client.post("/admin/apparence/origine", data={})
    assert origine.status_code == 403
    assert en_base(base) == {}


def test_un_visiteur_va_a_la_connexion(connecter, base):
    client = connecter(None)
    r = client.get("/admin/apparence", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/connexion?retour=/admin/apparence"
    assert appliquer(client, apparence_ciel="sakura").status_code == 401
    assert en_base(base) == {}


def test_revenir_a_l_origine_ecrit_les_defauts(admin, base):
    appliquer(admin, apparence_menthe="yuzu", apparence_intensite="60")
    avant = journal_apparence(base)
    r = admin.post("/admin/apparence/origine", data={}, follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/admin/apparence?etat=origine"
    assert {c: v for c, (v, _) in en_base(base).items()} == ap.DEFAUT.valeurs()
    assert journal_apparence(base) == avant
    page = admin.get(r.headers["location"])
    assert classes(page) == "esp-brume"
    assert "Apparence d'origine rétablie." in soupe(page).get_text()
    assert classes(admin.get("/")) == "esp-menthe"


def test_base_indisponible_la_page_dit_503(doublure, reglages: Reglages, port_ferme):
    port = port_ferme.rsplit(":", 1)[1]
    reglee = replace(
        reglages, base_dsn=f"postgresql://x@127.0.0.1:{port}/apimanga_essai"
    )
    app = creer_application(reglee, hacheur=HACHEUR_RAPIDE)
    with Navigateur(app, client=("127.0.0.1", 50000)) as client:
        r = client.get("/admin/apparence")

    assert r.status_code == 503
    assert classes(r) == "esp-ciel"


def test_l_ecriture_sans_journal_refuse_une_cle_journalisee():
    with pytest.raises(ValueError, match="journalisée"):
        reglages_app._cle_sans_journal("profil_actif")  # noqa: SLF001
    with pytest.raises(ValueError, match="sans action"):
        reglages_app._cle_journalisee("apparence_ciel")  # noqa: SLF001
    with pytest.raises(ValueError, match="inconnue"):
        reglages_app._cle_connue("apparence_rouge")  # noqa: SLF001


def test_a_propos_dit_l_apparence(navigateur):
    texte = navigateur.get("/a-propos").text

    assert (
        "L'administrateur peut changer les couleurs de l'application ;"
        " ce réglage ne collecte rien." in texte.replace("&#39;", "'")
    )


@pytest.mark.parametrize("chemin", ["/admin/apparence", "/admin/apparence/origine"])
def test_un_formulaire_illisible_est_une_demande_illisible(admin, base, chemin):
    r = admin.post(
        chemin,
        content=b"illisible",
        headers={"content-type": "multipart/form-data; boundary=x"},
    )

    assert r.status_code == 400
    assert en_base(base) == {}
