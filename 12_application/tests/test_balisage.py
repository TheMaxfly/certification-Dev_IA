"""Le balisage de chaque écran et de chaque cas : poste et GitHub, sans navigateur.

Chaque test nomme son critère WCAG dans son identifiant, pour que le rapport les liste
par une commande. Un outil automatique ne couvre qu'une partie des critères : le reste
est relu à la main, et dit tel quel au README."""

from __future__ import annotations

import hashlib
import re
import socket
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup, Tag
from conftest import ADRESSE_MEMOIRE, ENVIRONNEMENT
from doublure_api import Doublure
from ecrans import ECRANS, Ecran
from fastapi.testclient import TestClient

from application.app import GABARITS, STATIQUE, creer_application
from application.config import charger

ACCESSIBILITE = Path(__file__).resolve().parents[1] / "accessibilite"
TITRES = ("h1", "h2", "h3", "h4", "h5", "h6")


@dataclass(frozen=True)
class Page:
    ecran: Ecran
    statut: int
    entetes: httpx.Headers
    soupe: BeautifulSoup


def rendre(client: TestClient, ecran: Ecran) -> httpx.Response:
    if ecran.question is None:
        return client.get(ecran.chemin)
    donnees = {
        "question": ecran.question,
        "profil": ecran.profil,
        "temperature": ecran.temperature,
        "reformuler": "1",
        "historique": ecran.historique,
    }
    if ecran.demonstration:
        donnees["mode_demonstration"] = "1"
    return client.post("/question", data=donnees)


@pytest.fixture(scope="module")
def pages() -> Iterator[list[Page]]:
    """Chaque écran, rendu une fois : les tests relisent le HTML."""
    doublure = Doublure()
    reglages = replace(charger(ENVIRONNEMENT), adresse_api=ADRESSE_MEMOIRE)
    transport = httpx.ASGITransport(app=doublure.app)
    avec_api = creer_application(reglages, transport=transport)
    delais = replace(reglages.delais, connexion_s=0.05, reponse_s=0.05)
    pressee = creer_application(replace(reglages, delais=delais), transport=transport)
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        fermee = f"http://127.0.0.1:{prise.getsockname()[1]}"
    sans_api = creer_application(replace(reglages, adresse_api=fermee))
    rendues: list[Page] = []
    client_ = ("127.0.0.1", 50000)
    with (
        TestClient(avec_api, client=client_) as client,
        TestClient(sans_api, client=client_) as client_sans_api,
        TestClient(pressee, client=client_) as client_presse,
    ):
        for ecran in ECRANS:
            doublure.choisir(info=ecran.info, reponse=ecran.reponse)
            if ecran.neuve:
                neuve = creer_application(reglages, transport=transport)
                with TestClient(neuve, client=client_) as client_neuf:
                    r = rendre(client_neuf, ecran)
            elif ecran.delai_court:
                r = rendre(client_presse, ecran)
            else:
                r = rendre(client_sans_api if ecran.api_fermee else client, ecran)
            rendues.append(
                Page(
                    ecran,
                    r.status_code,
                    r.headers,
                    BeautifulSoup(r.text, "html.parser"),
                )
            )
    assert not doublure.ecarts, doublure.ecarts
    yield rendues


def principales(pages: list[Page]) -> list[Page]:
    """Les écrans de la page principale."""
    return [p for p in pages if p.ecran.chemin == "/"]


def test_le_catalogue_couvre_chaque_cas_et_rend_ce_qui_est_attendu(pages):
    assert len(pages) == len({p.ecran.nom for p in pages}) >= 50
    statuts = {p.ecran.nom: p.statut for p in pages}
    assert statuts["page_vide"] == statuts["reponse"] == statuts["a_propos"] == 200
    assert statuts["page_404"] == 404
    assert statuts["conversation_invalide"] == statuts["erreur_question_vide"] == 422
    assert statuts["degrade_debit_depasse"] == 429
    assert statuts["degrade_base_indisponible"] == 503
    assert statuts["degrade_api_injoignable"] == statuts["degrade_delai"] == 503
    cle = next(p for p in pages if p.ecran.nom == "cle_refusee").soupe
    assert "refuse l'accès" in cle.get_text()
    assert not cle.select_one("button[type=submit]").has_attr("disabled")


def test_wcag_3_1_1_la_langue_de_la_page_est_dite(pages):
    for page in pages:
        assert page.soupe.html["lang"] == "fr", page.ecran.nom


def test_wcag_2_4_2_chaque_page_a_un_titre(pages):
    for page in pages:
        assert page.soupe.title.get_text(strip=True), page.ecran.nom


def test_wcag_1_3_1_2_4_6_un_seul_h1_et_aucun_saut_de_niveau(pages):
    for page in pages:
        niveaux = [int(t.name[1]) for t in page.soupe.find_all(TITRES)]
        assert niveaux.count(1) == 1 and niveaux[0] == 1, page.ecran.nom
        for avant, apres in zip(niveaux, niveaux[1:], strict=False):
            assert apres <= avant + 1, page.ecran.nom
        for titre in page.soupe.find_all(TITRES):
            assert titre.get_text(strip=True), page.ecran.nom


def test_wcag_1_3_1_3_3_2_4_1_2_chaque_champ_a_son_etiquette(pages):
    for page in pages:
        champs = page.soupe.select("textarea, select, input:not([type=hidden])")
        for champ in champs:
            etiquette = page.soupe.select_one(f'label[for="{champ["id"]}"]')
            assert etiquette is not None, (page.ecran.nom, champ["id"])
            assert etiquette.get_text(strip=True), (page.ecran.nom, champ["id"])


def test_wcag_1_3_1_les_boutons_radio_sont_dans_un_fieldset_avec_legende(pages):
    vus = 0
    for page in pages:
        for radio in page.soupe.select("input[type=radio]"):
            groupe = radio.find_parent("fieldset")
            assert groupe is not None and groupe.legend is not None, page.ecran.nom
            assert groupe.legend.get_text(strip=True), page.ecran.nom
            vus += 1
    assert vus


def test_wcag_2_4_1_le_lien_d_evitement_est_le_premier_focalisable(pages):
    for page in pages:
        premier = page.soupe.body.find(["a", "button", "input", "select", "textarea"])
        assert premier.name == "a" and premier["href"] == "#contenu", page.ecran.nom
        assert page.soupe.select_one("main#contenu") is not None, page.ecran.nom


def test_wcag_3_3_1_les_erreurs_de_champ_sont_liees(pages):
    vues = 0
    for page in pages:
        for champ in page.soupe.select("[aria-invalid=true]"):
            lies = champ["aria-describedby"].split()
            assert any(i.endswith("-erreur") for i in lies), page.ecran.nom
            for identifiant in lies:
                assert page.soupe.select_one(f"#{identifiant}"), page.ecran.nom
            vues += 1
    assert vues == 4, "question vide, question longue, profil, température"
    fautive = next(p for p in pages if p.ecran.nom == "erreur_temperature")
    assert fautive.soupe.select_one("fieldset")["aria-describedby"] == (
        "temperature-erreur"
    )
    assert fautive.soupe.select_one("#temperature-erreur") is not None


def test_wcag_4_1_3_les_encarts_ont_le_role_alert(pages):
    for page in pages:
        if page.ecran.nom.startswith("degrade_") or page.ecran.nom in (
            "conversation_invalide",
            "conversation_pleine",
            "conversation_close",
            "conversation_trop_longue",
        ):
            assert page.soupe.select_one("[role=alert]") is not None, page.ecran.nom


def test_wcag_4_1_3_la_zone_de_reponse_porte_aria_live(pages):
    for page in principales(pages):
        assert page.soupe.select_one("section[aria-live=polite]"), page.ecran.nom


def test_wcag_1_1_1_toute_image_a_un_texte_de_remplacement(pages):
    for page in pages:
        for image in page.soupe.select("img"):
            assert image.has_attr("alt"), page.ecran.nom


def test_wcag_1_3_1_ni_style_en_ligne_ni_script(pages):
    for page in pages:
        assert not page.soupe.select("script, [style]"), page.ecran.nom
        for element in page.soupe.find_all(True):
            assert not [a for a in element.attrs if a.startswith("on")], page.ecran.nom
        assert "default-src 'none'" in page.entetes["Content-Security-Policy"]


def test_wcag_1_3_2_l_ordre_du_document_est_l_ordre_de_lecture(pages):
    for page in principales(pages):
        main: Tag = page.soupe.main
        ordre = list(main.descendants)
        formulaire = ordre.index(main.select_one("form"))
        zone = ordre.index(main.select_one("section[aria-live=polite]"))
        etat = ordre.index(main.select_one("h1").find_next("p"))
        assert etat < formulaire < zone, page.ecran.nom
        liste = main.select_one("ol")
        if liste is not None:
            assert etat < ordre.index(liste) < formulaire, page.ecran.nom


def test_wcag_2_4_4_les_liens_ont_un_texte_et_les_ancres_une_cible(pages):
    vues = 0
    for page in pages:
        for lien in page.soupe.select("a"):
            texte = lien.get_text(strip=True).casefold()
            assert texte and texte not in ("ici", "lien", "cliquez ici"), page.ecran.nom
            if lien["href"].startswith("#"):
                assert page.soupe.select_one(lien["href"]), (
                    page.ecran.nom,
                    lien["href"],
                )
                vues += 1
    assert vues > len(pages)


def test_wcag_2_4_3_le_focus_est_prevu_sur_le_titre_ou_le_champ_fautif(pages):
    for page in pages:
        foyers = page.soupe.select("[autofocus]")
        assert len(foyers) <= 1, page.ecran.nom
        for foyer in foyers:
            if foyer.name == "h2":
                assert foyer["tabindex"] == "-1", page.ecran.nom
    reponse = next(p for p in pages if p.ecran.nom == "reponse")
    assert reponse.soupe.select_one("[autofocus]").get_text() == "Réponse"
    degrade = next(p for p in pages if p.ecran.nom == "degrade_base_indisponible")
    titre = degrade.soupe.select_one("[autofocus]")
    assert titre["aria-describedby"] == "encart-texte"
    assert degrade.soupe.select_one("#encart-texte").get_text(strip=True)


def test_wcag_2_4_7_un_style_de_focus_visible_existe():
    feuille = (STATIQUE / "pico.classless.min.css").read_text(encoding="utf-8")

    regles = re.findall(r"[^{}]*:focus-visible[^{}]*\{([^}]*)\}", feuille)
    assert regles and any(r.strip() for r in regles)


def test_wcag_1_4_4_aucune_largeur_fixe_sur_les_conteneurs_de_texte():
    theme = (STATIQUE / "theme.css").read_text(encoding="utf-8")

    assert not re.search(r"\bwidth\s*:\s*\d+px", theme)
    for fichier in GABARITS.rglob("*.html"):
        texte = fichier.read_text(encoding="utf-8")
        assert not re.search(r"\s(width|height)=", texte), fichier.name


def test_wcag_1_4_1_chaque_etat_a_un_texte(pages):
    for page in pages:
        for encart in page.soupe.select("[role=alert]"):
            assert encart.get_text(strip=True), page.ecran.nom
        for fautif in page.soupe.select("[aria-invalid=true]"):
            lie = page.soupe.select_one(f"#{fautif['aria-describedby'].split()[-1]}")
            assert lie.get_text(strip=True), page.ecran.nom


def test_wcag_1_3_1_le_classement_a_des_en_tetes_de_colonnes(pages):
    vus = 0
    for page in pages:
        for tableau in page.soupe.select("#trace ~ table"):
            entetes = tableau.select("thead th")
            assert len(entetes) == 5, page.ecran.nom
            for entete in entetes:
                assert entete["scope"] == "col", page.ecran.nom
                assert entete.get_text(strip=True), page.ecran.nom
            for ligne in tableau.select("tbody tr"):
                assert len(ligne.select("td")) == 5, page.ecran.nom
            vus += 1
    assert vus == 1, "la réponse en mode démonstration ; le refus n'a aucune série"


def test_wcag_4_1_2_chaque_details_a_un_resume_signifiant(pages):
    for page in pages:
        for details in page.soupe.select("details"):
            resume = details.find("summary", recursive=False)
            assert resume is not None, page.ecran.nom
            assert resume.get_text(strip=True), page.ecran.nom
    demonstration = next(p for p in pages if p.ecran.nom == "reponse_demonstration")
    resumes = [
        d.summary.get_text(strip=True)
        for d in demonstration.soupe.select("#trace ~ details")
    ]
    assert resumes == [
        "Série factice A : passages non cités (1)",
        "Série factice C : passages non cités (1)",
        "Série factice D : passages non cités (1)",
        "Série factice E : passages non cités (1)",
    ]


def test_sans_le_mode_aucun_ecran_ne_porte_score_ni_rang_ni_section(pages):
    """Le seul mot permis : celui de l'étiquette de la case, qu'il faut bien lire."""
    etiquette = charger(ENVIRONNEMENT).textes["demonstration_etiquette"]
    for page in pages:
        if page.ecran.demonstration:
            continue
        html = str(page.soupe).replace(etiquette, "").lower()
        assert "score" not in html, page.ecran.nom
        assert not re.search(r"\brang\b", html), page.ecran.nom
        assert "trace de la recherche" not in html, page.ecran.nom
        assert page.soupe.select_one("#trace") is None, page.ecran.nom


def test_nouvelle_conversation_est_un_bouton_en_tete_de_chaque_ecran(pages):
    for page in pages:
        formulaire = page.soupe.select_one("header form")
        assert formulaire is not None, page.ecran.nom
        assert formulaire["method"] == "get", page.ecran.nom
        assert formulaire["action"] == "/", page.ecran.nom
        bouton = formulaire.select_one("button")
        assert bouton.get_text(strip=True) == "Nouvelle conversation", page.ecran.nom
        # Sans attribut type : Pico n'élargit que button[type=submit].
        assert not bouton.has_attr("type"), page.ecran.nom
        # Aucun champ : l'historique ne le suit pas, la page d'arrivée est vide.
        assert not formulaire.select("input, select, textarea"), page.ecran.nom


def test_les_positions_de_temperature_ne_parlent_pas_de_mesure(pages):
    for page in pages:
        for groupe in page.soupe.select("fieldset"):
            assert "mesur" not in groupe.get_text().lower(), page.ecran.nom


def test_la_mention_ia_est_sur_chaque_ecran(pages):
    for page in pages:
        texte = page.soupe.get_text()
        assert "Vous parlez à une IA." in page.soupe.header.get_text(), page.ecran.nom
        assert "elles peuvent être incomplètes" in texte, page.ecran.nom


def test_axe_est_vendu_avec_son_empreinte_et_sa_licence():
    lignes = [
        ligne.split()
        for ligne in (ACCESSIBILITE / "EMPREINTES")
        .read_text(encoding="utf-8")
        .splitlines()
        if ligne and not ligne.startswith("#")
    ]

    assert lignes == [[lignes[0][0], "axe.min.js"]]
    contenu = (ACCESSIBILITE / "axe.min.js").read_bytes()
    assert hashlib.sha256(contenu).hexdigest() == lignes[0][0]
    assert contenu.startswith(b"/*! axe v4.14.0")
    assert "Mozilla Public License" in (ACCESSIBILITE / "LICENCE-axe").read_text(
        encoding="utf-8"
    )
