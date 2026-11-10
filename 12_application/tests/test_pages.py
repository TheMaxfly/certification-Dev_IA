"""Les pages du socle : `/`, `/a-propos`, `/sante`, `/metrics`, les pages d'erreur."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import doublure_api as d
import pytest
from bs4 import BeautifulSoup
from conftest import ENVIRONNEMENT, application_sur, journal_complet
from fastapi.testclient import TestClient

import application
from application.app import STATIQUE, creer_application
from application.config import charger
from application.enveloppe import POLITIQUE

PAGES = ("/", "/a-propos", "/route-inconnue")


def soupe(reponse) -> BeautifulSoup:
    return BeautifulSoup(reponse.text, "html.parser")


def options_du_selecteur(reponse) -> list[str]:
    return [o["value"] for o in soupe(reponse).select("select#profil option")]


# --------------------------------------------------------------------------- #
#  La page principale, pilotée par /v1/info
# --------------------------------------------------------------------------- #
def test_la_page_dit_le_profil_actif_et_son_modele(navigateur):
    r = navigateur.get("/")

    assert r.status_code == 200
    assert (
        "Service disponible — profil actif : mistral-large-2512"
        " (modèle modele-de-mistral-large-2512)." in r.text
    )


def test_mistral_large_4_n_est_jamais_propose(libraire):
    options = options_du_selecteur(libraire.get("/"))

    assert "mistral-large-4" not in options
    assert options == ["mistral-large-2512", "gpt-6-luna", "gpt-6-sol"]


def test_chaque_option_dit_son_fournisseur_et_son_hebergement(libraire):
    textes = [
        o.get_text() for o in soupe(libraire.get("/")).select("select#profil option")
    ]

    assert textes[0] == ("mistral-large-2512 — Mistral, Union européenne (actif)")
    assert textes[1] == "gpt-6-luna — OpenAI, hors Union européenne par défaut"


def test_le_profil_actif_est_choisi_par_defaut(libraire):
    choisi = soupe(libraire.get("/")).select_one("select#profil option[selected]")

    assert choisi["value"] == "mistral-large-2512"


def test_un_profil_indisponible_n_est_pas_propose(libraire, doublure):
    doublure.choisir(info="profil_indisponible")

    assert options_du_selecteur(libraire.get("/")) == ["mistral-large-2512"]


@pytest.mark.parametrize(
    ("scenario", "attendu"),
    [("large_4_actif", "mistral-large-2512"), ("actif_indisponible", "gpt-6-luna")],
)
def test_un_actif_exclu_ou_indisponible_cede_au_premier_propose(
    libraire, doublure, scenario, attendu
):
    doublure.choisir(info=scenario)

    choisi = soupe(libraire.get("/")).select_one("select#profil option[selected]")

    assert choisi["value"] == attendu


def test_sans_profil_proposable_le_formulaire_est_inactif(navigateur, doublure):
    doublure.choisir(info="aucun_profil")

    page = soupe(navigateur.get("/"))

    assert page.select_one("select#profil") is None
    message = page.find(string=re.compile("Aucun profil de modèle de langage"))
    assert message is not None and message.find_parent("details") is None
    assert page.select_one("button[type=submit]").has_attr("disabled")


def test_un_fournisseur_inconnu_est_dit_inconnu(libraire, doublure):
    doublure.choisir(info="fournisseur_inconnu")

    page = soupe(libraire.get("/"))

    assert "autre — fournisseur-x, inconnu" in page.get_text()


@pytest.mark.parametrize(
    ("scenario", "selecteur"), [("nominale", False), ("deux_modes", True)]
)
def test_le_selecteur_de_mode_suit_le_contrat(libraire, doublure, scenario, selecteur):
    doublure.choisir(info=scenario)

    assert (soupe(libraire.get("/")).select_one("select#mode") is not None) is selecteur


def test_les_positions_de_temperature_sont_proposees(libraire):
    radios = soupe(libraire.get("/")).select("input[name=temperature]")

    assert [r["value"] for r in radios] == ["0.0", "0.3", "0.7"]
    assert [r.has_attr("checked") for r in radios] == [True, False, False]


def test_une_position_hors_des_bornes_de_l_api_n_est_pas_proposee(libraire, doublure):
    doublure.choisir(info="temperature_bornee")

    radios = soupe(libraire.get("/")).select("input[name=temperature]")

    assert [r["value"] for r in radios] == ["0.0", "0.3"]


def test_la_reformulation_est_cochee_par_defaut(libraire):
    assert soupe(libraire.get("/")).select_one("#reformuler").has_attr("checked")


def test_les_bornes_publiees_l_emportent_et_l_ecart_est_journalise(
    navigateur, doublure, caplog
):
    caplog.set_level(logging.INFO, logger="application")
    doublure.choisir(info="bornes_differentes")

    page = soupe(navigateur.get("/"))
    navigateur.get("/")

    assert page.select_one("textarea#question")["maxlength"] == "400"
    assert "400 caractères au plus." in page.get_text()
    ecarts = [
        r.champs["borne"] for r in caplog.records if r.getMessage() == "ecart_bornes"
    ]
    assert sorted(ecarts) == [
        "caracteres_max",
        "echanges",
        "question_caracteres_max",
        "tours_max",
    ], "une fois par valeur"


def test_les_bornes_egales_ne_journalisent_rien(navigateur, caplog):
    caplog.set_level(logging.INFO, logger="application")

    navigateur.get("/")

    assert not [r for r in caplog.records if r.getMessage() == "ecart_bornes"]


# --------------------------------------------------------------------------- #
#  L'API qui ne répond pas, ou mal
# --------------------------------------------------------------------------- #
def test_api_injoignable_l_application_demarre_et_le_dit(demarrer, port_ferme):
    navigateur = demarrer(port_ferme, connexion_s=0.2)

    page = navigateur.get("/")

    assert page.status_code == 200
    assert "Le service de recommandation ne répond pas." in page.text
    assert "API indisponible" in page.text
    assert soupe(page).select_one("select#profil") is None
    assert soupe(page).select_one("textarea#question") is not None
    assert navigateur.get("/sante").json() == {
        "application": "ok",
        "api": "indisponible",
        "base": "non_configuree",
        "catalogue": "non_configure",
        "cles_api": "une",
        "profil_actif": "defaut",
        "version": application.__version__,
    }
    sortie = navigateur.get("/metrics").text
    assert 'application_api_info_etat{etat="ko"} 1.0' in sortie
    assert 'application_api_info_etat{etat="ok"} 0.0' in sortie
    assert "application_api_sante_ok 0.0" in sortie
    assert "Liste inconnue" in navigateur.get("/a-propos").text


def test_api_trop_lente_au_demarrage_l_application_sert_quand_meme(
    doublure, doublure_sur_port, demarrer
):
    doublure.choisir(info="delai", sante="delai")
    navigateur = demarrer(doublure_sur_port, connexion_s=0.2, info_s=0.2, sante_s=0.2)

    assert "ne répond pas" in navigateur.get("/").text
    assert navigateur.get("/sante").json()["api"] == "indisponible"


def test_une_cle_refusee_est_dite_sans_la_cle(navigateur, doublure):
    doublure.choisir(info="cle_invalide")

    page = navigateur.get("/")

    assert "refuse l'accès de l'application" in soupe(page).get_text()
    assert d.CLE_ATTENDUE not in page.text
    assert "API indisponible" in page.text


def test_un_debit_depasse_garde_la_derniere_liste(libraire, doublure):
    libraire.get("/")
    doublure.choisir(info="debit_depasse")

    page = libraire.get("/")

    assert "Service disponible" in page.text
    assert options_du_selecteur(page) == [
        "mistral-large-2512",
        "gpt-6-luna",
        "gpt-6-sol",
    ]


def test_un_debit_depasse_sans_liste_connue_dit_ne_repond_pas(doublure, reglages):
    doublure.choisir(info="debit_depasse")
    application_, _ = application_sur(doublure, reglages)

    with TestClient(application_, client=("127.0.0.1", 50000)) as navigateur:
        assert "ne répond pas" in navigateur.get("/").text


def test_un_corps_hors_contrat_dit_ne_repond_pas_et_retombe_sur_la_liste(
    navigateur, doublure
):
    navigateur.get("/")
    doublure.choisir(info="corps_hors_contrat")

    assert "ne répond pas" in navigateur.get("/").text


def test_le_service_revient_quand_l_api_revient(navigateur, doublure):
    doublure.choisir(info="cle_invalide")
    navigateur.get("/")
    doublure.choisir(info="nominale")

    assert "Service disponible" in navigateur.get("/").text
    assert 'application_api_info_etat{etat="ok"} 1.0' in navigateur.get("/metrics").text


# --------------------------------------------------------------------------- #
#  /a-propos, /sante, /metrics
# --------------------------------------------------------------------------- #
def test_a_propos_nomme_chaque_profil_propose_et_son_hebergement(navigateur):
    page = soupe(navigateur.get("/a-propos"))
    lignes = [
        [c.get_text(strip=True) for c in tr.select("td")]
        for tr in page.select("tbody tr")
    ]

    assert lignes == [
        ["mistral-large-2512 (actif)", "Mistral", "Union européenne"],
        ["gpt-6-luna", "OpenAI", "hors Union européenne par défaut"],
        ["gpt-6-sol", "OpenAI", "hors Union européenne par défaut"],
    ]
    assert "mistral-large-4" not in page.get_text()
    assert "registre des traitements" in page.get_text()
    assert "Pico CSS (MIT)" in page.get_text()


def test_sante_ok(navigateur):
    r = navigateur.get("/sante")

    assert r.status_code == 200
    assert r.json() == {
        "application": "ok",
        "api": "ok",
        "base": "non_configuree",
        "catalogue": "non_configure",
        "cles_api": "une",
        "profil_actif": "defaut",
        "version": application.__version__,
    }


def test_sante_reste_200_quand_l_api_est_degradee(navigateur, doublure):
    doublure.choisir(sante="degrade")

    r = navigateur.get("/sante")

    assert r.status_code == 200 and r.json()["api"] == "indisponible"


def test_metrics_apres_une_sequence_connue(navigateur):
    navigateur.get("/")
    navigateur.get("/a-propos")
    navigateur.get("/route-inconnue")

    sortie = navigateur.get("/metrics").text

    assert 'application_requetes_total{route="/",statut="200"} 1.0' in sortie
    assert 'application_requetes_total{route="/a-propos",statut="200"} 1.0' in sortie
    assert 'application_requetes_total{route="inconnue",statut="404"} 1.0' in sortie
    assert 'application_latence_secondes_count{route="/"} 1.0' in sortie
    # Le démarrage, puis `/` et `/a-propos`.
    assert (
        'application_api_appels_total{route_api="/v1/info",statut="200"} 3.0' in sortie
    )
    assert 'application_api_info_etat{etat="ok"} 1.0' in sortie
    assert 'application_api_info_etat{etat="ko"} 0.0' in sortie
    assert 'application_api_info_etat{etat="cle"} 0.0' in sortie
    assert "application_api_sante_ok 1.0" in sortie
    assert "application_api_disponible" not in sortie


def test_metrics_compte_les_erreurs_de_l_api_par_code(navigateur, doublure):
    doublure.choisir(info="cle_invalide")
    navigateur.get("/")

    sortie = navigateur.get("/metrics").text

    assert 'application_api_erreurs_total{code="cle_invalide"} 1.0' in sortie
    assert 'application_api_info_etat{etat="cle"} 1.0' in sortie
    assert 'application_api_info_etat{etat="ok"} 0.0' in sortie


def test_les_feuilles_de_style_comptent_sous_statique(navigateur):
    assert navigateur.get("/statique/theme.css").status_code == 200
    navigateur.get("/statique/absente.css")
    navigateur.get("/route-inconnue")

    sortie = navigateur.get("/metrics").text

    assert 'application_requetes_total{route="statique",statut="200"} 1.0' in sortie
    assert 'application_requetes_total{route="statique",statut="404"} 1.0' in sortie
    assert 'application_requetes_total{route="inconnue",statut="404"} 1.0' in sortie


def test_la_sante_de_l_api_est_lue_au_demarrage_puis_a_chaque_sante(
    navigateur, doublure
):
    assert "application_api_sante_ok 1.0" in navigateur.get("/metrics").text

    doublure.choisir(sante="degrade")
    assert navigateur.get("/sante").json()["api"] == "indisponible"
    assert "application_api_sante_ok 0.0" in navigateur.get("/metrics").text


def test_metrics_n_est_servi_qu_au_bouclage(doublure, reglages):
    application_, _ = application_sur(doublure, reglages)

    with TestClient(application_, client=("192.0.2.1", 50000)) as navigateur:
        assert navigateur.get("/metrics").status_code == 404


def test_un_client_sans_adresse_n_a_pas_les_metrics(doublure, reglages):
    application_, _ = application_sur(doublure, reglages)

    with TestClient(application_, client=("pas-une-adresse", 50000)) as navigateur:
        assert navigateur.get("/metrics").status_code == 404


# --------------------------------------------------------------------------- #
#  Pages d'erreur
# --------------------------------------------------------------------------- #
def test_un_formulaire_illisible_rend_la_page_400(navigateur):
    r = navigateur.post(
        "/question",
        content=b"illisible",
        headers={"content-type": "multipart/form-data; boundary=x"},
    )

    assert r.status_code == 400
    assert "Demande illisible" in r.text
    assert "Erreur de l" not in r.text


def test_un_ecart_aux_bornes_publiees_se_journalise_des_le_demarrage(
    doublure, reglages, caplog
):
    caplog.set_level(logging.INFO, logger="application")
    doublure.choisir(info="bornes_differentes")
    application_, _ = application_sur(doublure, reglages)

    with TestClient(application_, client=("127.0.0.1", 50000)):
        pass

    assert [r for r in caplog.records if r.getMessage() == "ecart_bornes"]


def test_le_defaut_de_reformulation_est_celui_de_l_api(libraire, doublure, monkeypatch):
    corps = d.info()
    corps["modele_langage"]["reglages_reponse"]["reformuler"]["defaut"] = False
    monkeypatch.setitem(d.SCENARIOS_INFO, "nominale", lambda: d.Rendu(200, corps))

    assert not soupe(libraire.get("/")).select_one("#reformuler").has_attr("checked")


def test_une_route_inconnue_rend_la_page_404_avec_son_identifiant(navigateur):
    r = navigateur.get("/route-inconnue")

    assert r.status_code == 404
    assert "Page introuvable" in r.text
    assert f"Identifiant : {r.headers['X-Identifiant-Requete']}" in r.text
    assert "Vous parlez à une IA." in r.text


def test_une_methode_non_permise_rend_la_page_405(navigateur):
    r = navigateur.delete("/")

    assert r.status_code == 405
    assert "Action non permise" in r.text
    assert r.headers["Allow"]


@pytest.mark.parametrize("en_tete", [True, False])
def test_un_corps_trop_grand_rend_la_page_413(navigateur, en_tete):
    corps = b"x" * 16385

    if en_tete:
        r = navigateur.post("/question", content=corps)
    else:
        r = navigateur.post("/question", content=iter([corps]))

    assert r.status_code == 413
    assert "Demande trop volumineuse" in r.text
    assert 'application_requetes_total{route="inconnue",statut="413"} 1.0' in (
        navigateur.get("/metrics").text
    )


def test_un_content_length_illisible_rend_la_page_413(navigateur):
    r = navigateur.post(
        "/question", content=b"x", headers={"content-length": "beaucoup"}
    )

    assert r.status_code == 413


def test_une_erreur_imprevue_rend_la_page_500_sans_trace(navigateur, caplog):
    application_ = navigateur.app

    def casser():
        raise RuntimeError("détail interne factice")

    application_.state.vue.limites = casser

    r = navigateur.get("/")

    assert r.status_code == 500
    assert "Erreur de l'application" in soupe(r).get_text()
    assert "détail interne" not in r.text
    assert "Traceback" not in r.text
    assert any(rec.getMessage() == "erreur_interne" for rec in caplog.records)


def test_une_erreur_apres_le_debut_de_la_reponse_remonte(doublure, reglages):
    application_, _ = application_sur(doublure, reglages)

    @application_.get("/essai-coupe")
    async def coupe():
        from starlette.responses import StreamingResponse

        async def morceaux():
            yield b"debut"
            raise RuntimeError("coupé")

        return StreamingResponse(morceaux())

    with (
        TestClient(application_, client=("127.0.0.1", 50000)) as navigateur,
        pytest.raises(RuntimeError),
    ):
        navigateur.get("/essai-coupe")


# --------------------------------------------------------------------------- #
#  Sécurité : en-têtes, hors ligne, ressources vendues, secrets
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("chemin", [*PAGES, "/sante", "/statique/theme.css"])
def test_les_en_tetes_de_securite_sont_sur_chaque_reponse(navigateur, chemin):
    entetes = navigateur.get(chemin).headers

    assert entetes["Content-Security-Policy"] == POLITIQUE
    assert entetes["X-Content-Type-Options"] == "nosniff"
    assert entetes["Referrer-Policy"] == "no-referrer"
    assert entetes["X-Identifiant-Requete"]


def test_la_politique_n_admet_que_l_application_elle_meme():
    """E4, lot 6 : `font-src` et `script-src` à `'self'` seul (les polices et le script
    vendus) ; ni `'unsafe-inline'`, ni hôte externe, nulle part."""
    directives = dict(d_.split(" ", 1) for d_ in POLITIQUE.split("; "))

    assert directives["default-src"] == "'none'"
    assert directives["script-src"] == "'self'"
    assert directives["font-src"] == "'self'"
    assert "unsafe" not in POLITIQUE and "http" not in POLITIQUE
    assert directives["style-src"] == "'self'"
    assert directives["img-src"] == "'self' data:"
    assert directives["form-action"] == "'self'"
    assert directives["base-uri"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"


def test_les_pages_de_conversation_ne_vont_pas_en_cache(navigateur):
    assert navigateur.get("/").headers["Cache-Control"] == "no-store"
    assert navigateur.post("/question", data={}).headers["Cache-Control"] == "no-store"
    assert "Cache-Control" not in navigateur.get("/a-propos").headers


@pytest.mark.parametrize("chemin", PAGES)
def test_aucune_ressource_externe_ni_script_dans_le_html(navigateur, chemin):
    page = soupe(navigateur.get(chemin))

    assert not page.select("script")
    for balise, attribut in (("link", "href"), ("img", "src"), ("a", "href")):
        for element in page.select(balise):
            valeur = element.get(attribut, "")
            assert not re.match(r"(https?:)?//", valeur), valeur
    for feuille in page.select("link[rel=stylesheet]"):
        assert navigateur.get(feuille["href"]).status_code == 200


def test_la_feuille_vendue_ne_charge_rien_d_externe():
    texte = (STATIQUE / "pico.classless.min.css").read_text(encoding="utf-8")

    assert "@import" not in texte
    urls = re.findall(r"url\(\s*[\"']?([^\"')]+)", texte)
    assert urls and all(u.startswith("data:image/svg+xml") for u in urls)


def test_les_empreintes_des_ressources_vendues_sont_exactes():
    lignes = [
        ligne.split()
        for ligne in (STATIQUE / "EMPREINTES").read_text(encoding="utf-8").splitlines()
        if ligne and not ligne.startswith("#")
    ]

    assert lignes
    for empreinte, nom in lignes:
        contenu = (STATIQUE / nom).read_bytes()
        assert hashlib.sha256(contenu).hexdigest() == empreinte, nom
    assert (
        (STATIQUE / "LICENCE-pico")
        .read_text(encoding="utf-8")
        .startswith("MIT License")
    )


def test_le_theme_ne_porte_que_des_variables():
    texte = (STATIQUE / "theme.css").read_text(encoding="utf-8")
    sans_commentaires = re.sub(r"/\*.*?\*/", "", texte, flags=re.S)
    corps = sans_commentaires[
        sans_commentaires.index("{") + 1 : sans_commentaires.index("}")
    ]

    declarations = [x.strip() for x in corps.split(";") if x.strip()]
    assert declarations and all(x.startswith("--") for x in declarations)
    assert sans_commentaires.count("{") == 1


def contraste(a: str, b: str) -> float:
    def luminance(couleur: str) -> float:
        rvb = [int(couleur[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rvb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    clair, sombre = sorted((luminance(a), luminance(b)), reverse=True)
    return (clair + 0.05) / (sombre + 0.05)


@pytest.mark.parametrize(
    ("texte", "fond"),
    [
        ("--pico-color", "--pico-background-color"),
        ("--pico-muted-color", "--pico-background-color"),
        ("--pico-h1-color", "--pico-background-color"),
        ("--pico-h2-color", "--pico-background-color"),
        ("--pico-h3-color", "--pico-background-color"),
        ("--pico-primary", "--pico-background-color"),
        ("--pico-primary-hover", "--pico-background-color"),
        ("--pico-primary-inverse", "--pico-primary-background"),
        ("--pico-primary-inverse", "--pico-primary-hover-background"),
        ("--pico-color", "--pico-card-background-color"),
        ("--pico-form-element-color", "--pico-form-element-background-color"),
    ],
)
def test_wcag_1_4_3_contraste_des_couleurs_du_theme(texte, fond):
    feuille = (STATIQUE / "theme.css").read_text(encoding="utf-8")
    valeurs = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", feuille))

    assert contraste(valeurs[texte], valeurs[fond]) >= 4.5, (texte, fond)


@pytest.mark.parametrize(
    ("element", "fond"),
    [
        ("--pico-form-element-border-color", "--pico-form-element-background-color"),
        ("--pico-primary-focus", "--pico-background-color"),
        ("--pico-primary-border", "--pico-background-color"),
    ],
)
def test_wcag_1_4_11_les_bordures_et_le_focus_se_distinguent(element, fond):
    feuille = (STATIQUE / "theme.css").read_text(encoding="utf-8")
    valeurs = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", feuille))

    assert contraste(valeurs[element], valeurs[fond]) >= 3.0, (element, fond)


def test_la_cle_n_apparait_nulle_part(navigateur, caplog):
    caplog.set_level(logging.DEBUG)
    corps = [navigateur.get(c).text for c in (*PAGES, "/sante", "/metrics")]

    assert not any(d.CLE_ATTENDUE in texte for texte in corps)
    assert d.CLE_ATTENDUE not in journal_complet(caplog)


def test_les_gabarits_echappent_le_texte_venu_de_l_api(libraire, doublure, monkeypatch):
    doublure.choisir(info="fournisseur_inconnu")
    monkeypatch.setitem(
        d.SCENARIOS_INFO,
        "fournisseur_inconnu",
        lambda: d.Rendu(
            200,
            d.info(
                [
                    d.profil("mistral-large-2512"),
                    d.profil("<script>x</script>", "mistral"),
                ]
            ),
        ),
    )

    page = libraire.get("/")

    assert "<script>x" not in page.text
    assert "&lt;script&gt;x" in page.text


def test_aucun_gabarit_ne_marque_un_texte_comme_sur():
    gabarits = Path(application.__file__).parent / "gabarits"

    fichiers = list(gabarits.rglob("*.html"))
    assert fichiers
    for fichier in fichiers:
        texte = fichier.read_text(encoding="utf-8")
        assert "|safe" not in texte.replace(" ", ""), fichier.name
        assert "style=" not in texte, fichier.name
        assert "onclick" not in texte, fichier.name


def test_l_application_se_construit_sans_transport():
    assert creer_application(charger(ENVIRONNEMENT)).title == "Conseiller de lecture"
