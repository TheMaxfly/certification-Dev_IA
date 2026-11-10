"""L'habillage Senpai (E4, lot 6) : les ressources vendues, la politique de sécurité du
contenu, l'espace de couleur de chaque page, le repère de page du menu.

Sans navigateur et sans base : les fichiers sont lus, les pages rendues par le client
de test (qui n'exécute aucun script). Les écrans d'un rôle connecté sont relus par
`test_balisage.py`, sur sa base jetable."""

from __future__ import annotations

import hashlib
import re

import pytest
from bs4 import BeautifulSoup

from application.app import GABARITS, STATIQUE
from application.enveloppe import POLITIQUE
from application.presentation import entree_du_menu

#: L'espace de chaque gabarit (spec 01 du lot 6, § 3) ; un gabarit absent de la table
#: hérite du défaut, `ciel` — c'est voulu : un gabarit neuf a une couleur sans la
#: déclarer.
ESPACES = {
    "index.html": "menthe",
    "catalogue.html": "ciel",
    "connexion.html": "ciel",
    "inscription.html": "ciel",
    "a_propos.html": "ciel",
    "erreur.html": "ciel",
    "serie.html": "sakura",
    "mot_de_passe.html": "yuzu",
    "listes.html": "yuzu",
    "compte_supprimer.html": "yuzu",
    "critiques.html": "glycine",
    "critique_formulaire.html": "glycine",
    "critique_retirer.html": "glycine",
    "admin_comptes.html": "brume",
    "admin_critiques.html": "brume",
    "admin_critique_retirer.html": "brume",
    "admin_reglages.html": "brume",
    "admin_cles.html": "brume",
    "admin_couts.html": "brume",
    "admin_services.html": "brume",
}
SIX = {"menthe", "ciel", "sakura", "yuzu", "glycine", "brume"}
#: Les fichiers de `statique/` écrits pour l'application : tout le reste est vendu, et
#: porte son empreinte.
PROPRES = {"EMPREINTES", "theme.css", "senpai.css"}
BLOC_ESPACE = re.compile(r"\{%\s*block\s+espace\s*%\}(\w+)\{%\s*endblock\s*%\}")


def soupe(reponse) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def espaces_du_corps(page: BeautifulSoup) -> list[str]:
    return [c for c in page.body.get("class", []) if c.startswith("esp-")]


# --------------------------------------------------------------------------- #
#  La politique de sécurité du contenu
# --------------------------------------------------------------------------- #
def test_la_politique_est_exactement_celle_du_lot_6():
    assert POLITIQUE == (
        "default-src 'none'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; script-src 'self'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )


@pytest.mark.parametrize(
    "chemin", ["/", "/catalogue", "/route-inconnue", "/statique/senpai.css"]
)
def test_l_en_tete_porte_la_politique_exacte(navigateur, chemin):
    assert navigateur.get(chemin).headers["Content-Security-Policy"] == POLITIQUE


# --------------------------------------------------------------------------- #
#  Les ressources : vendues, empreintées, locales
# --------------------------------------------------------------------------- #
def vendues() -> list[str]:
    return sorted(
        str(f.relative_to(STATIQUE))
        for f in STATIQUE.rglob("*")
        if f.is_file() and f.name not in PROPRES and not f.name.startswith("LICENCE-")
    )


def empreintes() -> dict[str, str]:
    lignes = (STATIQUE / "EMPREINTES").read_text(encoding="utf-8").splitlines()
    return {
        nom: empreinte
        for empreinte, nom in (
            ligne.split() for ligne in lignes if ligne and not ligne.startswith("#")
        )
    }


def test_chaque_ressource_vendue_a_son_empreinte_exacte():
    connues = empreintes()

    assert len(connues) >= 60, "Pico et les 59 fichiers du paquet Senpai"
    assert sorted(connues) == vendues()
    for nom, empreinte in connues.items():
        contenu = (STATIQUE / nom).read_bytes()
        assert hashlib.sha256(contenu).hexdigest() == empreinte, nom


def test_les_licences_des_ressources_vendues_sont_versees():
    ofl = (STATIQUE / "polices" / "LICENCE-OFL").read_text(encoding="utf-8")
    phosphor = (STATIQUE / "icones" / "LICENCE-phosphor").read_text(encoding="utf-8")

    assert "SIL OPEN FONT LICENSE Version 1.1" in ofl
    assert phosphor.startswith("MIT License")


def test_la_feuille_de_l_habillage_ne_charge_rien_d_externe():
    texte = (STATIQUE / "senpai.css").read_text(encoding="utf-8")

    assert "@import" not in texte
    urls = re.findall(r"url\(\s*[\"']?([^\"')]+)", texte)
    assert len(urls) > 20
    for url in urls:
        assert url.startswith("/statique/"), url
        assert (STATIQUE / url.removeprefix("/statique/")).is_file(), url


@pytest.mark.parametrize(
    "ressource",
    [
        "/statique/senpai.css",
        "/statique/polices/zen-maru-gothic-500.woff2",
        "/statique/lavis/lavis-menthe.webp",
        "/statique/images/logo-senpai.svg",
    ],
)
def test_les_ressources_de_l_habillage_sont_servies(navigateur, ressource):
    assert navigateur.get(ressource).status_code == 200


def test_les_svg_vendus_n_ont_ni_largeur_ni_hauteur_ni_script():
    fichiers = sorted(STATIQUE.glob("icones/*.svg")) + sorted(
        STATIQUE.glob("images/*.svg")
    )

    assert len(fichiers) == 43
    for fichier in fichiers:
        texte = fichier.read_text(encoding="utf-8")
        assert not re.search(r"\s(width|height)=", texte), fichier.name
        assert "<script" not in texte and not re.search(r"\son\w+=", texte)


def test_le_theme_reste_sans_largeur_fixe_la_bordure_est_dans_l_habillage():
    """Guide § 3 : `--pico-border-width: 2px` serait pris pour une largeur fixe."""
    assert "--pico-border-width" not in (STATIQUE / "theme.css").read_text("utf-8")
    assert "--pico-border-width:2px" in (STATIQUE / "senpai.css").read_text("utf-8")


# --------------------------------------------------------------------------- #
#  L'espace de chaque page
# --------------------------------------------------------------------------- #
def test_chaque_gabarit_declare_l_espace_de_la_table():
    gabarits = sorted(f for f in GABARITS.glob("*.html") if f.name != "base.html")

    assert len(gabarits) == len(ESPACES)
    for fichier in gabarits:
        blocs = BLOC_ESPACE.findall(fichier.read_text(encoding="utf-8"))
        declare = blocs[0] if blocs else "ciel"
        assert len(blocs) <= 1, fichier.name
        assert declare in SIX, fichier.name
        assert declare == ESPACES.get(fichier.name, "ciel"), fichier.name


def test_la_base_pose_l_espace_sur_le_corps_ciel_par_defaut():
    base = (GABARITS / "base.html").read_text(encoding="utf-8")

    assert '<body class="esp-{% block espace %}ciel{% endblock %}">' in base


@pytest.mark.parametrize(
    ("chemin", "espace"),
    [
        ("/", "menthe"),
        ("/a-propos", "ciel"),
        ("/catalogue", "ciel"),
        ("/route-inconnue", "ciel"),
    ],
)
def test_chaque_page_rendue_porte_son_espace(navigateur, chemin, espace):
    assert espaces_du_corps(soupe(navigateur.get(chemin))) == [f"esp-{espace}"]


def test_la_page_d_erreur_de_l_enveloppe_porte_un_espace(navigateur):
    """La page 413 est rendue sans requête, par l'enveloppe."""
    r = navigateur.post("/question", content=b"x" * 16385)

    assert r.status_code == 413
    assert espaces_du_corps(soupe(r)) == ["esp-ciel"]


# --------------------------------------------------------------------------- #
#  Le repère de page du menu
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("chemin", "entree"),
    [
        ("/", "/"),
        ("/question", "/"),
        ("/catalogue", "/catalogue"),
        ("/compte/listes", "/compte/listes"),
        ("/compte/mot-de-passe", "/compte/mot-de-passe"),
        ("/libraire/critiques", "/libraire/critiques"),
        ("/admin/comptes", "/admin/comptes"),
        ("/admin/couts", "/admin/comptes"),
        ("/admin/critiques/12/retirer", "/admin/comptes"),
        ("/series/745", None),
        ("/connexion", None),
        ("/a-propos", None),
        ("/administration", None),
    ],
)
def test_l_entree_du_menu_de_chaque_chemin(chemin, entree):
    assert entree_du_menu(chemin) == entree


@pytest.mark.parametrize(
    ("chemin", "courant"),
    [("/", ["/"]), ("/catalogue", ["/catalogue"]), ("/a-propos", [])],
)
def test_le_menu_marque_la_page_courante(navigateur, chemin, courant):
    page = soupe(navigateur.get(chemin))

    marques = page.select("header nav a[aria-current]")
    assert [a["href"] for a in marques] == courant
    assert all(a["aria-current"] == "page" for a in marques)


# --------------------------------------------------------------------------- #
#  Les restes (spec 03 du lot 6)
# --------------------------------------------------------------------------- #
def test_la_page_413_dit_un_texte_generique(navigateur):
    r = navigateur.post("/question", content=b"x" * 16385)

    texte = soupe(r).select_one("main").get_text()
    assert r.status_code == 413
    assert "La demande est trop volumineuse." in texte
    assert "Réduisez le texte envoyé et réessayez." in texte
    assert "critique" not in texte and "conversation" not in texte


def test_chaque_tableau_des_gabarits_est_dans_son_conteneur_defilant():
    vus = 0
    for fichier in sorted(GABARITS.rglob("*.html")):
        texte = fichier.read_text(encoding="utf-8")
        tableaux = texte.count("<table>")
        conteneurs = re.findall(
            r'<div class="defilant" tabindex="0" role="region"'
            r' aria-labelledby="([\w-]+)">\n<table>',
            texte,
        )
        assert len(conteneurs) == tableaux, fichier.name
        for identifiant in conteneurs:
            assert f'id="{identifiant}"' in texte, (fichier.name, identifiant)
        vus += tableaux
    assert vus == 8
    assert "body .defilant{overflow-x:auto}" in (STATIQUE / "senpai.css").read_text(
        "utf-8"
    )


def test_le_tableau_rendu_est_nomme_par_son_titre(navigateur):
    page = soupe(navigateur.get("/a-propos"))

    conteneur = page.select_one("div.defilant")
    assert conteneur.select_one(":scope > table") is not None
    assert conteneur["tabindex"] == "0" and conteneur["role"] == "region"
    nom = page.select_one(f"#{conteneur['aria-labelledby']}").get_text(strip=True)
    assert nom == "Les profils proposés"


def test_les_profils_indisponibles_portent_l_etiquette_de_statut_contrastee():
    gabarit = (GABARITS / "admin_reglages.html").read_text(encoding="utf-8")
    theme = (STATIQUE / "theme.css").read_text(encoding="utf-8")
    valeurs = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", theme))

    assert '<li class="badge statut ouvert">{{ ligne }}</li>' in gabarit
    # L'étiquette : l'encre sur le papier (section 15 de senpai.css).
    assert contraste(valeurs["--sp-encre"], valeurs["--sp-papier"]) >= 4.5


def contraste(a: str, b: str) -> float:
    def luminance(couleur: str) -> float:
        rvb = [int(couleur[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rvb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    clair, sombre = sorted((luminance(a), luminance(b)), reverse=True)
    return (clair + 0.05) / (sombre + 0.05)
