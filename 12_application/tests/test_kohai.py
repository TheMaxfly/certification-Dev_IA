"""Kōhai, la mascotte (E4, lot 6) : la pose selon la page, les SVG en ligne, la pose
d'attente et le seul script de l'application.

Sans navigateur : le client de test n'exécute aucun script — ce que voit ici chaque test
est la page telle qu'elle est sans JavaScript. Ce que fait le script (la classe posée à
l'envoi, la phrase montrée) est vérifié dans Chromium par l'audit (`AUDIT_AXE=1`)."""

from __future__ import annotations

import hashlib
import re

import pytest
from bs4 import BeautifulSoup

from application.app import GABARITS, STATIQUE
from application.presentation import pose_de_kohai

QUESTION = "Un manga factice pour débuter ?"
DESSINS = {"bonjour", "cherche", "trouve", "perdu", "pause"}


def soupe(reponse) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def poser(navigateur, question: str = QUESTION):
    return navigateur.post(
        "/question",
        data={
            "question": question,
            "profil": "mistral-large-2512",
            "temperature": "0.0",
            "reformuler": "1",
            "historique": "",
        },
    )


def principal(page: BeautifulSoup):
    kohais = page.select(".kohai-principal")
    assert len(kohais) == 1
    return kohais[0]


def pose(page: BeautifulSoup) -> str:
    classes = principal(page)["class"]
    return next(c.removeprefix("k-anim-") for c in classes if c.startswith("k-anim-"))


# --------------------------------------------------------------------------- #
#  La pose selon la page
# --------------------------------------------------------------------------- #
def test_l_arrivee_a_chaque_accueil_et_rien_dans_la_session(navigateur):
    """Écart 16 (correction de l'orchestration) : la pose d'arrivée se joue à chaque
    chargement de l'accueil ; le cookie ne garde rien pour Kōhai."""
    for _ in range(3):
        assert pose(soupe(navigateur.get("/"))) == "arrivee"
    assert not [n for n in navigateur.cookies if "kohai" in n]


def test_une_reponse_qui_cite_une_serie_est_trouvee(navigateur):
    navigateur.get("/")
    page = soupe(poser(navigateur))

    assert page.select("section[aria-live] article > h3")
    assert pose(page) == "trouve"


@pytest.mark.parametrize("scenario", ["refus_aucun_passage", "refus_hors_catalogue"])
def test_un_refus_est_perdu(navigateur, doublure, scenario):
    navigateur.get("/")
    doublure.choisir(reponse=scenario)

    assert pose(soupe(poser(navigateur))) == "perdu"


@pytest.mark.parametrize("scenario", ["debit_depasse", "fournisseur_injoignable"])
def test_un_encart_est_perdu(navigateur, doublure, scenario):
    navigateur.get("/")
    doublure.choisir(reponse=scenario)
    page = soupe(poser(navigateur))

    assert page.select_one("[role=alert]") is not None
    assert pose(page) == "perdu"


def test_l_api_injoignable_est_perdue(demarrer, port_ferme):
    navigateur = demarrer(port_ferme, connexion_s=0.2)
    navigateur.get("/")
    r = poser(navigateur)

    assert r.status_code == 503
    assert pose(soupe(r)) == "perdu"


@pytest.mark.parametrize(
    ("refus", "cartes", "encart", "attendue"),
    [
        (False, False, False, "arrivee"),
        (False, True, False, "trouve"),
        (True, False, False, "perdu"),
        (False, True, True, "perdu"),
        (False, False, True, "perdu"),
    ],
)
def test_la_pose_par_contexte(refus, cartes, encart, attendue):
    class Affichage:
        pass

    affichage = Affichage()
    affichage.refus = refus  # type: ignore[attr-defined]
    affichage.cartes = ("carte",) if cartes else ()  # type: ignore[attr-defined]
    assert (
        pose_de_kohai(affichage, object() if encart else None)  # type: ignore[arg-type]
        == attendue
    )


# --------------------------------------------------------------------------- #
#  Décoratif, unique, en ligne
# --------------------------------------------------------------------------- #
def test_kohai_est_decoratif_et_le_principal_est_dans_le_titre(navigateur):
    page = soupe(navigateur.get("/"))

    for kohai in page.select("span.kohai"):
        assert kohai["aria-hidden"] == "true"
        svg = kohai.find("svg", recursive=False)
        assert svg is not None and "kohai" in svg["class"]
    assert principal(page).find_parent("h1") is not None
    assert page.select_one("main h1").get_text(strip=True) == "Conseiller de lecture"


def test_les_pages_hors_du_conseiller_n_ont_aucun_kohai_en_ligne(navigateur):
    for chemin in ("/a-propos", "/catalogue", "/series/101", "/route-inconnue"):
        assert not soupe(navigateur.get(chemin)).select(".kohai"), chemin


def test_les_svg_sont_ceux_du_paquet_sans_script_ni_evenement_ni_id():
    fichiers = sorted((GABARITS / "kohai").glob("*.svg"))

    assert {f.stem for f in fichiers} == DESSINS
    for fichier in fichiers:
        texte = fichier.read_text(encoding="utf-8")
        assert texte.startswith(f'<svg class="kohai k-{fichier.stem}" viewBox=')
        assert "<script" not in texte and not re.search(r"\son\w+=", texte)
        assert " id=" not in texte and " style=" not in texte
        assert "{{" not in texte and "{%" not in texte


# --------------------------------------------------------------------------- #
#  Le mouvement réduit (lu dans la feuille)
# --------------------------------------------------------------------------- #
def test_toute_animation_est_neutralisee_sous_le_mouvement_reduit():
    feuille = (STATIQUE / "senpai.css").read_text(encoding="utf-8")
    bloc = feuille[feuille.index("@media (prefers-reduced-motion: reduce)") :]
    bloc = bloc[: bloc.index("@media print")]

    assert re.search(r"@keyframes", feuille)
    assert "@view-transition{navigation:auto}" in feuille
    assert "@view-transition{navigation:none}" in bloc
    assert (
        "*,*::before,*::after{animation:none!important;transition:none!important}"
        in (bloc)
    )


def test_les_complements_ne_modifient_aucune_regle_du_paquet():
    """Décision anticipée de l'orchestration (10 novembre) : une section 16 ajoutée à la
    fin, rien d'autre — ce qui la précède est le paquet tel que versé."""
    feuille = (STATIQUE / "senpai.css").read_text(encoding="utf-8")
    debut = feuille.index("/* ---------- 16. Compléments du lot 6")

    avant = feuille[:debut].rstrip("\n") + "\n"
    assert hashlib.sha256(avant.encode()).hexdigest() == (
        "76636621a7d027b548fe0c634a4e78feb8fec6f418df989a082c8dc24ae65952"
    )


# --------------------------------------------------------------------------- #
#  La pose d'attente et le seul script
# --------------------------------------------------------------------------- #
def test_la_pose_d_attente_est_dans_la_page_cachee_par_la_feuille(navigateur):
    page = soupe(navigateur.get("/"))

    attente = page.select_one(".kohai-attente")
    assert attente["aria-live"] == "polite"
    assert attente.get_text(strip=True) == "Kōhai lit les critiques…"
    assert "k-anim-reflexion" in attente.select_one("span.kohai")["class"]
    assert "kohai-principal" not in attente.select_one("span.kohai")["class"]
    feuille = (STATIQUE / "senpai.css").read_text(encoding="utf-8")
    assert ".kohai-attente{display:none}" in feuille
    assert "body.en-attente .kohai-attente{display:flex;" in feuille


def test_le_formulaire_vise_par_le_script_existe(navigateur):
    page = soupe(navigateur.get("/"))

    assert len(page.select("form[action='/question']")) == 1


@pytest.mark.parametrize("chemin", ["/", "/a-propos", "/catalogue", "/route-inconnue"])
def test_le_seul_script_une_balise_vendue_differee_jamais_en_ligne(navigateur, chemin):
    page = soupe(navigateur.get(chemin))

    scripts = page.select("script")
    assert len(scripts) == 1
    assert scripts[0].attrs == {"src": "/statique/attente.js", "defer": ""}
    assert not scripts[0].get_text(strip=True)
    assert scripts[0].find_parent("head") is not None
    assert not [a for e in page.find_all(True) for a in e.attrs if a.startswith("on")]


def test_le_script_est_servi_empreinte_et_court(navigateur):
    r = navigateur.get("/statique/attente.js")
    texte = (STATIQUE / "attente.js").read_text(encoding="utf-8")
    empreintes = (STATIQUE / "EMPREINTES").read_text(encoding="utf-8")

    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert f"{hashlib.sha256(texte.encode()).hexdigest()}  attente.js" in empreintes
    assert len(texte.splitlines()) <= 20
    assert '"use strict";' in texte
    assert "fetch" not in texte and "XMLHttpRequest" not in texte
    assert "cookie" not in texte and "Storage" not in texte
