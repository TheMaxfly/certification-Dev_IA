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
import psycopg
import pytest
from bs4 import BeautifulSoup, Tag
from conftest import (
    ADRESSE_MEMOIRE,
    CLE_SESSION_ESSAI,
    ENVIRONNEMENT,
    HACHEUR_RAPIDE,
    Navigateur,
    bornes_larges,
    connecter_essai,
    creer_compte_essai,
    dsn_du_role,
    harnais,
)
from doublure_api import CLE_CATALOGUE_ATTENDUE, Doublure
from ecrans import (
    BORNES_SERREES,
    COUTS_ECRANS,
    ECRANS,
    MDP_INSCRIPTION,
    REGLAGE_ECRANS,
    Ecran,
    poser_apparence,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.app import GABARITS, STATIQUE, creer_application
from application.config import charger
from application.presentation import entree_du_menu

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


def inscrire(client: TestClient, pseudo: str) -> httpx.Response:
    return client.post(
        "/inscription",
        data={
            "pseudo": pseudo,
            "mot_de_passe": MDP_INSCRIPTION,
            "confirmation": MDP_INSCRIPTION,
            "retour": "/",
        },
        follow_redirects=False,
    )


def rendre_borne(application: FastAPI, ecran: Ecran, base: str) -> httpx.Response:
    """Un écran borné (E4, lot 2b) : sur son application, un premier passage, puis la
    page qui dépasse la borne."""
    with Navigateur(application, client=("127.0.0.1", 50100)) as client:
        if ecran.role is not None:
            pseudo = f"{ecran.role}_borne_balisage"
            creer_compte_essai(base, pseudo, ecran.role)
            connecter_essai(client, pseudo)
        if ecran.borne == "inscription":
            assert inscrire(client, "inscrit_borne_1").status_code == 303
            client.post("/deconnexion", data={})
            return inscrire(client, "inscrit_borne_2")
        rendre(client, ecran)
        return rendre(client, ecran)


@pytest.fixture(scope="module")
def base_balisage(conteneur, gabarit, fichier_pgpass) -> Iterator[str]:
    """Une base jetable pour le module : les écrans d'un rôle connecté (E4, lot 2b) ;
    son DSN sous le rôle de l'application."""
    with (
        harnais.base_depuis_gabarit(conteneur, gabarit) as dsn,
        pytest.MonkeyPatch.context() as patch,
    ):
        patch.setenv("PGPASSFILE", str(fichier_pgpass))
        with psycopg.connect(dsn) as cx:
            cx.execute(
                "INSERT INTO application.reglages (cle, valeur) VALUES (%s, %s)",
                REGLAGE_ECRANS,
            )
            cx.execute(
                "INSERT INTO application.couts_jour (jour, profil, mode, appels,"
                " cout_usd) VALUES ((now() AT TIME ZONE 'UTC')::date, %s, %s, %s, %s)",
                COUTS_ECRANS,
            )
        yield dsn_du_role(dsn)


@pytest.fixture(scope="module")
def pages(base_balisage: str) -> Iterator[list[Page]]:
    """Chaque écran, rendu une fois : les tests relisent le HTML."""
    doublure = Doublure()
    charges = bornes_larges(charger(ENVIRONNEMENT))
    reglages = replace(
        charges,
        adresse_api=ADRESSE_MEMOIRE,
        catalogue=replace(charges.catalogue, cle=CLE_CATALOGUE_ATTENDUE),
    )
    transport = httpx.ASGITransport(app=doublure.app)
    avec_api = creer_application(reglages, transport=transport, hacheur=HACHEUR_RAPIDE)
    delais = replace(reglages.delais, connexion_s=0.05, reponse_s=0.05)
    pressee = creer_application(
        replace(reglages, delais=delais), transport=transport, hacheur=HACHEUR_RAPIDE
    )
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        fermee = f"http://127.0.0.1:{prise.getsockname()[1]}"
    sans_api = creer_application(
        replace(reglages, adresse_api=fermee), hacheur=HACHEUR_RAPIDE
    )
    avec_base = creer_application(
        replace(reglages, base_dsn=base_balisage, cle_session=CLE_SESSION_ESSAI),
        transport=transport,
        hacheur=HACHEUR_RAPIDE,
    )
    rendues: list[Page] = []
    client_ = ("127.0.0.1", 50000)
    with (
        Navigateur(avec_api, client=client_) as client,
        Navigateur(sans_api, client=client_) as client_sans_api,
        Navigateur(pressee, client=client_) as client_presse,
        Navigateur(avec_base, client=client_) as client_base,
    ):
        par_role: dict[str, Navigateur] = {}
        for role in sorted({e.role for e in ECRANS if e.role is not None}):
            port = 50001 + len(par_role)
            connecte = Navigateur(avec_base, client=("127.0.0.1", port))
            creer_compte_essai(base_balisage, f"{role}_balisage", role)
            connecter_essai(connecte, f"{role}_balisage")
            par_role[role] = connecte
        del client_base
        for ecran in ECRANS:
            doublure.choisir(
                info=ecran.info, reponse=ecran.reponse, catalogue=ecran.catalogue
            )
            if ecran.neuve:
                neuve = creer_application(
                    reglages, transport=transport, hacheur=HACHEUR_RAPIDE
                )
                with Navigateur(neuve, client=client_) as client_neuf:
                    r = rendre(client_neuf, ecran)
            elif ecran.delai_court:
                r = rendre(client_presse, ecran)
            elif ecran.borne is not None:
                serree = replace(
                    reglages,
                    base_dsn=base_balisage,
                    cle_session=CLE_SESSION_ESSAI,
                    bornes=replace(reglages.bornes, **BORNES_SERREES[ecran.borne]),
                )
                application = creer_application(
                    serree, transport=transport, hacheur=HACHEUR_RAPIDE
                )
                r = rendre_borne(application, ecran, base_balisage)
            elif ecran.role is not None:
                if ecran.apparence:
                    poser_apparence(base_balisage, ecran.apparence)
                r = rendre(par_role[ecran.role], ecran)
                if ecran.apparence:
                    poser_apparence(base_balisage, ())
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
    assert statuts["catalogue"] == statuts["catalogue_resultats"] == 200
    assert statuts["catalogue_vide"] == statuts["serie"] == statuts["serie_sans_note"]
    assert statuts["catalogue_erreur_saisie"] == 422
    assert statuts["catalogue_indisponible"] == 503
    assert statuts["serie_introuvable"] == 404
    for nom in (
        "conseiller_quota_atteint",
        "conseiller_quota_client_atteint",
        "conseiller_debit_atteint",
        "inscription_bornee",
    ):
        assert statuts[nom] == 429, nom
    assert statuts["conseiller_budget_atteint"] == 503
    for nom in ("conseiller_quota_atteint", "conseiller_budget_atteint"):
        page = next(p for p in pages if p.ecran.nom == nom)
        assert page.entetes["Retry-After"].isdigit(), nom
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
    assert vues == 5, (
        "question vide, question longue, profil, température, saisie du catalogue"
    )
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
    """E4, lot 6 : une seule balise `<script>`, celle du script vendu, vide et
    différée ; rien d'autre, nulle part."""
    for page in pages:
        assert not page.soupe.select("style, [style]"), page.ecran.nom
        scripts = page.soupe.select("script")
        assert len(scripts) == 1, page.ecran.nom
        assert scripts[0].attrs == {"src": "/statique/attente.js", "defer": ""}
        assert not scripts[0].get_text(strip=True), page.ecran.nom
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
    # Les gabarits, et les SVG de la mascotte inclus en ligne (E4, lot 6).
    fichiers = [*GABARITS.rglob("*.html"), *GABARITS.rglob("*.svg")]
    assert len([f for f in fichiers if f.suffix == ".svg"]) == 5
    for fichier in fichiers:
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
        for tableau in page.soupe.select("#trace ~ .defilant > table"):
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


def test_nouvelle_conversation_est_une_action_du_conseiller_seulement(pages):
    """E4, lot 7 (spec 02) : le bouton quitte l'en-tête ; il est rendu dans le
    conseiller seulement, quand une conversation existe (une réponse, un encart, un
    historique) — jamais à l'accueil vierge, jamais sur une autre page."""
    vus = {"present": 0, "absent": 0}
    for page in pages:
        assert page.soupe.select_one('header form[action="/"]') is None, page.ecran.nom
        formulaire = page.soupe.select_one('main form[method="get"][action="/"]')
        zone = page.soupe.select_one("main > section[aria-live]")
        conversation = page.soupe.select_one('main form[action="/question"]') and (
            (zone is not None and zone.find(True) is not None)
            or any(
                h.get_text(strip=True) == "Conversation en cours"
                for h in page.soupe.select("main > section > h2")
            )
        )
        if not conversation:
            assert formulaire is None, page.ecran.nom
            vus["absent"] += 1
            continue
        vus["present"] += 1
        assert formulaire is not None, page.ecran.nom
        assert formulaire["class"] == ["zone-nouvelle-conversation"], page.ecran.nom
        bouton = formulaire.select_one("button")
        assert bouton.get_text(strip=True) == "Nouvelle conversation", page.ecran.nom
        # Sans attribut type : Pico n'élargit que button[type=submit].
        assert not bouton.has_attr("type"), page.ecran.nom
        # Aucun champ : l'historique ne le suit pas, la page d'arrivée est vide.
        assert not formulaire.select("input, select, textarea"), page.ecran.nom
    assert vus["present"] >= 20 and vus["absent"] >= 20, vus
    vierge = next(p for p in pages if p.ecran.nom == "page_vide")
    assert vierge.soupe.select_one('main form[method="get"][action="/"]') is None


def test_l_en_tete_porte_deux_navigations_nommees(pages):
    """E4, lot 7 (spec 02) : un seul `header`, deux `nav` nommés, aucun imbriqué — les
    pages, puis le compte ; « Mon compte » et la déconnexion dans la zone du compte."""
    for page in pages:
        entetes = page.soupe.select("header")
        assert len(entetes) == 1, page.ecran.nom
        navs = entetes[0].select("nav")
        assert [n.get("aria-label") for n in navs] == ["Pages", "Votre compte"]
        assert navs[1]["class"] == ["compte-zone"], page.ecran.nom
        assert not page.soupe.select("nav nav"), page.ecran.nom
        pages_ = [a["href"] for a in navs[0].select("a")]
        assert pages_[:2] == ["/", "/catalogue"], page.ecran.nom
        assert "/compte/mot-de-passe" not in pages_, page.ecran.nom
        assert not navs[0].select("form, span.compte"), page.ecran.nom
        compte = navs[1]
        role = page.ecran.role
        connecte = role is not None and compte.select_one("span.compte") is not None
        if connecte:
            assert [a["href"] for a in compte.select("a")] == ["/compte/mot-de-passe"]
            assert compte.select_one('form[action="/deconnexion"]') is not None
            attendu = {
                "client": ["/", "/catalogue", "/compte/listes"],
                "libraire": ["/", "/catalogue", "/libraire/critiques"],
                "admin": ["/", "/catalogue", "/admin/comptes"],
            }[role]
            assert pages_ == attendu, page.ecran.nom
        elif role is None:
            hrefs = [a["href"].split("?")[0] for a in compte.select("a")]
            assert hrefs == ["/connexion", "/inscription"], page.ecran.nom
            assert pages_ == ["/", "/catalogue"], page.ecran.nom


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


def test_wcag_1_3_1_2_4_8_la_pagination_du_catalogue_dit_la_page_courante(pages):
    resultats = next(p for p in pages if p.ecran.nom == "catalogue_resultats").soupe
    nav = resultats.select_one("main nav[aria-label]")

    assert nav.select_one("[aria-current=page]").get_text() == "Page 2 sur 3"
    assert {a.get_text() for a in nav.select("a")} == {
        "Page précédente",
        "Page suivante",
    }
    assert resultats.select_one("main form label[for=q]") is not None


def test_wcag_2_4_4_le_lien_vers_le_site_du_catalogue_se_dit_externe(pages):
    for nom in ("serie", "serie_sans_note"):
        fiche = next(p for p in pages if p.ecran.nom == nom).soupe
        externes = [
            a for a in fiche.select("main a") if a["href"].startswith("https://")
        ]
        assert externes, nom
        for lien in externes:
            assert "noopener" in lien["rel"], nom
            assert "(site externe)" in lien.get_text(), nom


def test_la_fiche_porte_ses_zones_nommees_pour_l_habillage(pages):
    fiche = next(p for p in pages if p.ecran.nom == "serie").soupe
    for zone in (
        "zone-titre",
        "zone-note",
        "zone-critiques-reseau",
        "zone-listes",
        "zone-synopsis",
    ):
        assert len(fiche.select(f"section.{zone}")) == 1, zone


@pytest.mark.parametrize(
    "nom",
    ["catalogue", "catalogue_resultats", "catalogue_vide", "catalogue_erreur_saisie"],
)
def test_le_groupe_de_recherche_ne_porte_que_le_champ_et_le_bouton(pages, nom):
    """Passage manuel du lot 2a : Pico aligne en ligne tout élément `role="search"` ;
    posé sur le formulaire entier, il écrasait l'étiquette, repoussait l'aide et faisait
    déborder la page. Faute de mesurer une largeur, la structure : le groupe ne contient
    que le champ et le bouton ; l'étiquette, l'aide et l'erreur sont hors du groupe ; le
    champ reste décrit par l'aide."""
    page = next(p for p in pages if p.ecran.nom == nom).soupe
    formulaire = page.select_one('main form[method=get][action="/catalogue"]')
    groupe = formulaire.select_one("[role=search]")

    assert formulaire.get("role") is None, "le formulaire entier en groupe"
    assert [e.name for e in groupe.find_all(recursive=False)] == ["input", "button"]
    assert groupe.select_one("label, small, p") is None
    assert formulaire.select_one('label[for="q"]').find_parent(role="search") is None
    aide = formulaire.select_one("#q-aide")
    assert aide is not None and aide.find_parent(role="search") is None
    assert "q-aide" in groupe.select_one("#q")["aria-describedby"].split()
    erreur = formulaire.select_one("#q-erreur")
    if erreur is not None:
        assert erreur.find_parent(role="search") is None


def test_les_ecrans_de_l_administration_rendent_l_etat_voulu(pages):
    """E4, lot 5 : chaque écran d'administration est bien celui qu'on croit."""
    vues = {p.ecran.nom: p for p in pages}
    assert vues["admin_reglages"].statut == 200
    assert vues["admin_reglages_indisponible"].soupe.select_one(
        ".zone-profil-actif-indisponible"
    )
    assert vues["admin_reglages_api_absente"].statut == 503
    assert vues["admin_cles"].soupe.select(".zone-cles tbody tr")
    jour = vues["admin_couts"].soupe.select_one(".zone-cout-jour").get_text()
    assert "plafond : ok" in jour
    atteint = vues["admin_couts_plafond_atteint"].soupe.select_one(".zone-cout-jour")
    assert "plafond : atteint" in atteint.get_text()
    assert vues["admin_couts"].soupe.select("progress") != []
    assert vues["admin_services"].soupe.select(".zone-services-encart") == []
    degrade = vues["admin_services_degrades"].soupe
    assert "Catalogue" in degrade.select_one(".zone-services-encart").get_text()
    for nom in ("admin_couts", "admin_services"):
        for tableau in vues[nom].soupe.select("main table"):
            assert tableau.select_one("caption").get_text(strip=True), nom
            assert all(th["scope"] == "col" for th in tableau.select("thead th")), nom


#: L'espace attendu d'une page rendue, selon son chemin (E4, lot 6) ; les pages
#: d'erreur sont `ciel`, quel que soit leur chemin.
ESPACE_DU_CHEMIN = (
    ("/admin/", "brume"),
    ("/series/", "sakura"),
    ("/catalogue", "ciel"),
    ("/a-propos", "ciel"),
    ("/inscription", "ciel"),
    ("/", "menthe"),
)


def test_chaque_ecran_porte_un_seul_espace_celui_de_sa_page(pages):
    """E4, lot 6 : la classe d'espace sur `<body>`, posée par chaque gabarit."""
    for page in pages:
        espaces = [c for c in page.soupe.body.get("class", []) if c.startswith("esp-")]
        assert len(espaces) == 1, page.ecran.nom
        chemin = page.ecran.chemin.split("?")[0]
        if page.soupe.select_one("main h1").get_text() in {
            "Page introuvable",
            "Série introuvable",
        }:
            continue
        if page.ecran.apparence:
            continue  # E4, lot 7 : la couleur réglée (test_apparence_des_ecrans_regles)
        attendu = next(e for p, e in ESPACE_DU_CHEMIN if chemin.startswith(p))
        if page.statut == 200:
            assert espaces == [f"esp-{attendu}"], page.ecran.nom


def test_le_menu_marque_la_page_courante_sur_chaque_ecran(pages):
    """E4, lot 6 : `aria-current="page"` sur le lien du menu de la page, et lui seul ;
    toute l'administration sous « Administration »."""
    vus = set()
    for page in pages:
        chemin = "/question" if page.ecran.question else page.ecran.chemin.split("?")[0]
        entree = entree_du_menu(chemin)
        hrefs = [a["href"] for a in page.soupe.select("header nav a")]
        marques = page.soupe.select("header nav a[aria-current]")
        attendu = [entree] if entree in hrefs else []
        assert [a["href"] for a in marques] == attendu, page.ecran.nom
        assert all(a["aria-current"] == "page" for a in marques), page.ecran.nom
        vus.update(attendu)
    assert {"/", "/catalogue", "/admin/comptes"} <= vus


def test_chaque_tableau_rendu_defile_dans_un_conteneur_nomme(pages):
    """E4, lot 6 (guide § 8) : sur un écran étroit, le tableau défile dans un conteneur
    focalisable, nommé par sa légende ou par le titre qui le précède."""
    vus = 0
    for page in pages:
        for tableau in page.soupe.select("table"):
            conteneur = tableau.parent
            assert conteneur.name == "div", page.ecran.nom
            assert "defilant" in conteneur["class"], page.ecran.nom
            assert conteneur["tabindex"] == "0", page.ecran.nom
            assert conteneur["role"] == "region", page.ecran.nom
            nom = page.soupe.select_one(f"#{conteneur['aria-labelledby']}")
            assert nom is not None and nom.get_text(strip=True), page.ecran.nom
            vus += 1
    assert vus >= 5


def test_les_profils_indisponibles_portent_l_etiquette_de_statut(pages):
    page = next(p for p in pages if p.ecran.nom == "admin_reglages_indisponible")
    zone = page.soupe.select_one(".zone-profils-indisponibles")

    if zone is not None:
        for ligne in zone.select("li"):
            assert ligne["class"] == ["badge", "statut", "ouvert"]


#: Les classes de `<body>` de chaque écran au lot 6 (relevées le 2026-11-10 sur
#: `d87f7bc`, avant le lot 7) : sans réglage, l'apparence ne change aucune d'elles.
CLASSES_DU_LOT_6: dict[str, tuple[str, ...]] = {
    "esp-brume": (
        "admin_cles",
        "admin_couts",
        "admin_couts_plafond_atteint",
        "admin_reglages",
        "admin_reglages_api_absente",
        "admin_reglages_indisponible",
        "admin_services",
        "admin_services_degrades",
    ),
    "esp-ciel": (
        "a_propos",
        "a_propos_sans_api",
        "catalogue",
        "catalogue_erreur_saisie",
        "catalogue_indisponible",
        "catalogue_resultats",
        "catalogue_suggestions",
        "catalogue_vide",
        "inscription_bornee",
        "page_404",
        "serie_introuvable",
    ),
    "esp-menthe": (
        "api_indisponible",
        "aucun_profil",
        "cle_refusee",
        "conseiller_admin",
        "conseiller_budget_atteint",
        "conseiller_client",
        "conseiller_debit_atteint",
        "conseiller_libraire",
        "conseiller_prerempli",
        "conseiller_public",
        "conseiller_quota_atteint",
        "conseiller_quota_client_atteint",
        "conversation_3_tours",
        "conversation_5_tours",
        "conversation_close",
        "conversation_invalide",
        "conversation_pleine",
        "conversation_trop_longue",
        "degrade_api_injoignable",
        "degrade_base_indisponible",
        "degrade_cle_invalide",
        "degrade_corps_hors_contrat",
        "degrade_corps_trop_volumineux",
        "degrade_debit_depasse",
        "degrade_debit_sans_entete",
        "degrade_delai",
        "degrade_encodage_en_service_change",
        "degrade_entree_invalide",
        "degrade_erreur_interne",
        "degrade_fournisseur_corps_inattendu",
        "degrade_fournisseur_delai",
        "degrade_fournisseur_injoignable",
        "degrade_fournisseur_longueur",
        "degrade_fournisseur_refus_429",
        "degrade_fournisseur_refus_429_sans_entete",
        "degrade_fournisseur_refus_500",
        "degrade_historique_invalide",
        "degrade_profil_inconnu",
        "degrade_profil_indisponible",
        "degrade_question_trop_longue",
        "degrade_reglage_non_expose",
        "degrade_reponse_hors_contrat",
        "degrade_service_embedding_incoherent",
        "degrade_service_embedding_indisponible",
        "degrade_sortie_demographie",
        "degrade_sortie_disponibilite",
        "degrade_sortie_fragment_inconnu",
        "degrade_sortie_pseudonyme",
        "degrade_sortie_refus_incoherent",
        "degrade_sortie_schema",
        "degrade_sortie_serie_inconnue",
        "deux_modes",
        "erreur_profil",
        "erreur_question_longue",
        "erreur_question_vide",
        "erreur_temperature",
        "page_vide",
        "refus_aucun_passage",
        "refus_demonstration",
        "refus_hors_catalogue",
        "refus_inconnu",
        "reponse",
        "reponse_cartes_client",
        "reponse_complement",
        "reponse_demonstration",
        "reponse_metadonnees_nulles",
        "reponse_reformulee",
        "reponse_refus_avec_pont",
        "reponse_refus_debit",
        "reponse_sans_carte_avec_pont",
        "reponse_sans_passage",
        "reponse_titres",
    ),
    "esp-sakura": (
        "serie",
        "serie_critiques_indisponibles",
        "serie_deux_resumes",
        "serie_listes_client",
        "serie_listes_visiteur",
        "serie_resume_kitsu",
        "serie_sans_note",
        "serie_sans_resume",
    ),
}


def test_sans_reglage_les_classes_du_corps_sont_celles_du_lot_6(pages):
    """E4, lot 7 (décision 3) : sans réglage, le rendu du lot 6 à l'identique — la
    même classe de `<body>`, et rien d'autre, sur chaque écran du catalogue."""
    attendues = {nom: c for c, noms in CLASSES_DU_LOT_6.items() for nom in noms}
    rendues = {
        p.ecran.nom: " ".join(p.soupe.body.get("class", []))
        for p in pages
        if p.ecran.nom in attendues
    }
    assert rendues == attendues
    nouveaux = {p.ecran.nom for p in pages} - set(attendues)
    assert all(
        n.startswith(("admin_apparence", "apparence_")) for n in nouveaux - SPEC_02
    )
    rendues_02 = {
        p.ecran.nom: " ".join(p.soupe.body.get("class", []))
        for p in pages
        if p.ecran.nom in SPEC_02
    }
    assert rendues_02 == {
        "entete_visiteur": "esp-ciel",
        "entete_client": "esp-ciel",
        "entete_admin": "esp-ciel",
        "conseiller_apres_reponse": "esp-menthe",
    }


#: Les écrans de l'en-tête (E4, lot 7, spec 02), sans réglage d'apparence.
SPEC_02 = {
    "entete_visiteur",
    "entete_client",
    "entete_admin",
    "conseiller_apres_reponse",
}


#: Les classes attendues des écrans réglés par `APPARENCE_ECRANS` : la couleur du
#: suivant, 60 %, l'aplat.
REGLEES = {
    "admin_apparence_reglee": "esp-menthe intensite-60 matiere-aplat",
    "apparence_conseiller": "esp-ciel intensite-60 matiere-aplat",
    "apparence_catalogue": "esp-sakura intensite-60 matiere-aplat",
    "apparence_fiche": "esp-yuzu intensite-60 matiere-aplat",
    "apparence_listes": "esp-glycine intensite-60 matiere-aplat",
    "apparence_libraire": "esp-brume intensite-60 matiere-aplat",
    "apparence_administration": "esp-menthe intensite-60 matiere-aplat",
    "admin_apparence": "esp-brume",
    "admin_apparence_partage": "esp-brume",
}


def test_apparence_des_ecrans_regles(pages):
    """E4, lot 7 : chaque espace rend la couleur réglée pour lui ; l'intensité et la
    matière, sur toutes les pages."""
    rendues = {
        p.ecran.nom: " ".join(p.soupe.body.get("class", []))
        for p in pages
        if p.ecran.nom in REGLEES
    }
    assert rendues == REGLEES
    statuts = {p.ecran.nom: p.statut for p in pages if p.ecran.nom in REGLEES}
    assert set(statuts.values()) == {200}, statuts
