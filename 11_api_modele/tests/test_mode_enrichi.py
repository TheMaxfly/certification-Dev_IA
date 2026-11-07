"""Le mode enrichi (E3, étape 6, spec 06, sections B à D) : un complément « non
vérifié » ajouté à la réponse stricte, contrôlé ; en strict, rien ne change.

Doublures seulement (fournisseur et service d'embedding sur le bouclage, base
jetable) : aucun appel réel.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from conftest import (
    AUTEURS,
    QUESTION,
    BaseApi,
    DoublureFournisseur,
    Doublures,
    entetes,
)
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from api_modele import recherche
from api_modele.complement import ETIQUETTE
from api_modele.contrat import DESTINATION
from api_modele.journal import FormatJson
from api_modele.modeles import SCHEMA_SORTIE, Resultat
from api_modele.reponse import INVITE_COMPLEMENT, INVITE_REPONSE

CONTRAT = json.loads(DESTINATION.read_text(encoding="utf-8"))
REFERENCES = Path(__file__).parent / "references"
NOM = AUTEURS[0]  # un nom de membre factice, lu par la vue des pseudonymes
#: L'invite de base, inchangée d'un mot depuis 1.3.1 (spec 06, B.4).
EMPREINTE_INVITE = "91bf8e7a3441cd1eeb2f4ceb7cdb71c7e39971e1b655cafb480cdff8e705ab02"
CHAMPS_DU_COMPLEMENT = ("complement", "complement_titres", "complement_etiquette")
#: Les champs qui changent d'une exécution à l'autre, ou avec la version.
VARIABLES = ("identifiant_requete", "duree_ms", "date_appel", "latences_ms")


def repondre(api: TestClient, **champs: object) -> httpx.Response:
    return api.post(
        "/v1/reponse", json={"question": QUESTION, **champs}, headers=entetes()
    )


def conforme(r: httpx.Response) -> dict[str, Any]:
    reponse = CONTRAT["paths"]["/v1/reponse"]["post"]["responses"][str(r.status_code)]
    schema = reponse["content"]["application/json"]["schema"]
    Draft202012Validator({**schema, "components": CONTRAT["components"]}).validate(
        r.json()
    )
    return r.json()


def appels(doublures: Doublures) -> list[dict[str, Any]]:
    return [r for r in doublures.recus() if r["chemin"] == "/v1/chat/completions"]


def enrichie(**champs: object) -> str:
    """Une sortie du mode enrichi : la réponse stricte habituelle, et un complément."""
    base: dict[str, object] = {
        "refus": False,
        "motif_refus": None,
        "reponse": "Une aventure de pirates [S:1].",
        "series_citees": [{"serie_id": 1, "fragment_ids": [702]}],
        "complement": "*Titre inventé* est un bon choix pour prolonger.",
        "complement_titres": ["Titre inventé"],
    }
    return json.dumps(base | champs)


def neutre(corps: dict[str, Any]) -> dict[str, Any]:
    """Le corps, ses champs variables (durées, identifiant, date) et la version
    neutralisés."""
    contexte = dict(corps["contexte"])
    for champ in (*VARIABLES, "version_api"):
        contexte.pop(champ, None)
    contexte["appels"] = [
        {k: v for k, v in a.items() if k != "latence_ms"} for a in contexte["appels"]
    ]
    return corps | {"contexte": contexte}


@pytest.fixture
def sans_passage(monkeypatch: pytest.MonkeyPatch) -> None:
    vraie = recherche.rechercher

    def vide(*args: Any) -> Resultat:
        return vraie(*args).model_copy(update={"series": []})

    monkeypatch.setattr(recherche, "rechercher", vide)


# --------------------------------------------------------------------------- #
#  En strict, rien ne change (C.5)
# --------------------------------------------------------------------------- #
def test_en_strict_la_requete_au_fournisseur_est_celle_de_la_1_3_1(
    api_reponse, doublures
):
    DoublureFournisseur.usage = {"prompt_tokens": 100, "completion_tokens": 20}

    repondre(api_reponse)

    (recu,) = appels(doublures)
    attendu = (REFERENCES / "strict_1_3_1_requete.json").read_text(encoding="utf-8")
    ecrit = json.dumps(recu["corps"], ensure_ascii=False, indent=1, sort_keys=True)
    assert ecrit + "\n" == attendu, "octet pour octet"
    schema = recu["corps"]["response_format"]["json_schema"]["schema"]
    assert schema == SCHEMA_SORTIE and "complement" not in schema["properties"]


def test_en_strict_la_reponse_est_celle_de_la_1_3_1(api_reponse, doublures):
    DoublureFournisseur.usage = {"prompt_tokens": 100, "completion_tokens": 20}

    r = repondre(api_reponse)

    reference = json.loads((REFERENCES / "strict_1_3_1_reponse.json").read_bytes())
    corps = conforme(r)
    assert neutre(corps) == neutre(reference)
    assert list(corps) == list(reference), "mêmes champs, même ordre"
    for champ in CHAMPS_DU_COMPLEMENT:
        assert champ not in corps


def test_en_strict_une_sortie_avec_complement_est_hors_schema(api_reponse):
    DoublureFournisseur.contenu = enrichie()

    r = repondre(api_reponse)

    assert r.status_code == 503
    assert conforme(r)["details"]["motif"] == "schema"


# --------------------------------------------------------------------------- #
#  En enrichi : une seule invite, deux schémas (B.3, B.4)
# --------------------------------------------------------------------------- #
def test_en_enrichi_l_invite_est_la_base_plus_le_bloc_et_le_schema_s_etend(
    api_reponse, doublures
):
    DoublureFournisseur.contenu = enrichie()

    conforme(repondre(api_reponse, mode="enrichi"))

    (recu,) = appels(doublures)
    systeme = recu["corps"]["messages"][0]["content"]
    assert systeme == f"{INVITE_REPONSE}\n\n{INVITE_COMPLEMENT}"
    assert (
        hashlib.sha256(
            (Path(DESTINATION).parents[1] / "config/invites/reponse.md").read_bytes()
        ).hexdigest()
        == EMPREINTE_INVITE
    ), "l'invite de base ne change pas d'un mot"
    schema = recu["corps"]["response_format"]["json_schema"]["schema"]
    assert {"complement", "complement_titres"} <= set(schema["required"])
    assert schema["properties"]["complement"]["maxLength"] == 600
    assert schema["properties"]["complement_titres"]["maxItems"] == 5
    for champ in ("refus", "motif_refus", "reponse", "series_citees"):
        assert schema["properties"][champ] == SCHEMA_SORTIE["properties"][champ]


def test_un_complement_hors_catalogue_est_rendu_avec_son_etiquette(api_reponse):
    DoublureFournisseur.contenu = enrichie()

    corps = conforme(repondre(api_reponse, mode="enrichi"))

    assert corps["complement"].startswith("*Titre inventé* est un bon choix")
    assert corps["complement_titres"] == ["Titre inventé"]
    assert corps["complement_etiquette"] == ETIQUETTE
    assert ETIQUETTE == "non vérifié — connaissances du modèle, sans source"
    assert corps["reponse"] == "Une aventure de pirates Série 1 [S:1]."
    assert corps["contexte"]["reglages_appliques"]["mode"] == "enrichi"


@pytest.mark.parametrize(
    "champs",
    [
        {"complement": None, "complement_titres": None},
        {"complement": "", "complement_titres": []},
        {"complement": "   ", "complement_titres": ["Un titre seul"]},
    ],
    ids=["nuls", "vides", "blanc"],
)
def test_un_complement_vide_ou_absent_ne_rend_rien(api_reponse, champs):
    DoublureFournisseur.contenu = enrichie(**champs)

    corps = conforme(repondre(api_reponse, mode="enrichi"))

    for champ in CHAMPS_DU_COMPLEMENT:
        assert champ not in corps


def test_un_complement_absent_de_la_sortie_est_valide(api_reponse):
    sortie = json.loads(enrichie())
    for champ in ("complement", "complement_titres"):
        sortie.pop(champ)
    DoublureFournisseur.contenu = json.dumps(sortie)

    corps = conforme(repondre(api_reponse, mode="enrichi"))

    assert "complement" not in corps and "complement_etiquette" not in corps


# --------------------------------------------------------------------------- #
#  Les contrôles du complément (C.6, E2)
# --------------------------------------------------------------------------- #
def ajouter_au_catalogue(dsn_admin: str, serie_id: int, titre: str) -> None:
    with psycopg.connect(dsn_admin) as cx:
        cx.execute(
            "INSERT INTO manga.ms_series_enriched (series_id, series_title)"
            " VALUES (%s, %s)",
            (serie_id, titre),
        )


@pytest.mark.parametrize(
    ("complement", "titres", "motif"),
    [
        (f"Les lecteurs comme {NOM} l'aiment aussi.", [], "complement_pseudonyme"),
        ("À lire aussi :", [f"Le titre de {NOM}"], "complement_pseudonyme"),
        ("Série 2 est disponible en France.", [], "complement_disponibilite"),
        (
            "Série 2 existe en français, en poche.",
            ["Série 2"],
            "complement_disponibilite",
        ),
        ("Série 2 compte 12 tomes.", [], "complement_metadonnee"),
        ("Série 1 est terminée depuis longtemps.", [], "complement_metadonnee"),
        ("Série 2, écrite par un grand nom, plaira.", [], "complement_metadonnee"),
        ("Série 2 est un seinen sombre.", [], "complement_metadonnee"),
        (
            "Les lecteurs de Série 2 apprécient ses 27 tomes.",
            [],
            "complement_metadonnee",
        ),
        ("Serie 2 compte 12 tomes.", ["Serie 2"], "complement_metadonnee"),
        ("Lisez aussi Série 2 [S:2].", ["Série 2"], "complement_marque"),
        ("Un détail [F:702] utile.", [], "complement_marque"),
        ("Lisez aussi Autre titre.", ["Autre [S:9] titre"], "complement_marque"),
    ],
    ids=[
        "pseudonyme",
        "pseudonyme-titre",
        "disponibilite",
        "disponibilite-titre",
        "tomes",
        "statut",
        "auteur",
        "public",
        "comparaison-metadonnee",
        "titre-sans-accent",
        "marque-serie",
        "marque-fragment",
        "marque-titre",
    ],
)
def test_un_complement_fautif_est_un_503_nomme(api_reponse, complement, titres, motif):
    DoublureFournisseur.contenu = enrichie(
        complement=complement, complement_titres=titres
    )

    r = repondre(api_reponse, mode="enrichi")

    assert r.status_code == 503
    corps = conforme(r)
    assert corps["code"] == "sortie_modele_invalide"
    assert corps["details"]["motif"] == motif
    assert complement not in r.text, "la sortie fautive n'est pas rendue"
    texte = api_reponse.get("/metrics").text
    assert f'api_modele_sortie_invalide_total{{motif="{motif}"}} 1.0' in texte


def test_un_titre_du_catalogue_hors_de_la_recherche_est_controle(
    api_reponse, base_api: BaseApi
):
    """« en 12 tomes » sur un titre du catalogue que la recherche n'a pas rendu : le
    catalogue le reconnaît (casse et signes mis à part) ; refusé."""
    ajouter_au_catalogue(base_api.dsn_admin, 3, "Le Titre du Catalogue !")
    DoublureFournisseur.contenu = enrichie(
        complement="le titre du catalogue est sorti en 12 tomes.",
        complement_titres=["Le titre du catalogue"],
    )

    r = repondre(api_reponse, mode="enrichi")

    assert r.status_code == 503
    assert conforme(r)["details"]["motif"] == "complement_metadonnee"


@pytest.mark.parametrize(
    "complement",
    [
        "Titre inventé compte 12 tomes, disponible en France.",
        "Série 2 n'est pas disponible en France, à vérifier.",
        "Série 2 ne compte pas douze tomes.",
        "Est-ce que Série 2 est terminée ?",
    ],
    ids=["hors-catalogue", "negation", "negation-tomes", "question"],
)
def test_ce_que_le_controle_laisse_passer(api_reponse, complement):
    """Hors catalogue, le complément peut se tromper (l'étiquette le dit) ; une
    négation, une question ne sont pas des affirmations."""
    DoublureFournisseur.contenu = enrichie(
        complement=complement, complement_titres=["Titre inventé"]
    )

    corps = conforme(repondre(api_reponse, mode="enrichi"))

    assert corps["complement"] == complement


def test_la_partie_stricte_reste_controlee_en_enrichi(api_reponse):
    DoublureFournisseur.contenu = enrichie(
        series_citees=[{"serie_id": 99, "fragment_ids": [702]}]
    )

    r = repondre(api_reponse, mode="enrichi")

    assert r.status_code == 503
    assert conforme(r)["details"]["motif"] == "serie_inconnue"


# --------------------------------------------------------------------------- #
#  Recherche sans passage (question 33)
# --------------------------------------------------------------------------- #
def test_sans_passage_en_enrichi_l_appel_a_lieu_et_la_partie_stricte_refuse(
    api_reponse, doublures, sans_passage
):
    DoublureFournisseur.contenu = enrichie(
        refus=True,
        motif_refus="aucun_passage",
        reponse="Les passages ne disent rien de cela.",
        series_citees=[],
        complement="Titre inventé pourrait convenir.",
    )

    corps = conforme(repondre(api_reponse, mode="enrichi"))

    (recu,) = appels(doublures)
    assert "PASSAGES :\n\n(aucun passage)" in recu["corps"]["messages"][-1]["content"]
    assert (corps["refus"], corps["motif_refus"]) == (True, "aucun_passage")
    assert corps["series_citees"] == [] and corps["series"] == []
    assert corps["complement"] == "Titre inventé pourrait convenir."
    assert corps["complement_etiquette"] == ETIQUETTE
    assert [a["role"] for a in corps["contexte"]["appels"]] == ["reponse"]


@pytest.mark.parametrize(
    "champs",
    [
        {},
        {"refus": True, "motif_refus": "hors_catalogue", "series_citees": []},
        {"refus": True, "motif_refus": None, "series_citees": []},
    ],
    ids=["pas-de-refus", "autre-motif", "sans-motif"],
)
def test_sans_passage_une_partie_stricte_qui_ne_refuse_pas_est_un_503(
    api_reponse, sans_passage, champs
):
    DoublureFournisseur.contenu = enrichie(**champs)

    r = repondre(api_reponse, mode="enrichi")

    assert r.status_code == 503
    assert conforme(r)["details"]["motif"] == "refus_incoherent"


def test_sans_passage_en_strict_aucun_appel(api_reponse, doublures, sans_passage):
    corps = conforme(repondre(api_reponse))

    assert (corps["refus"], corps["motif_refus"]) == (True, "aucun_passage")
    assert appels(doublures) == []
    assert "complement" not in corps


# --------------------------------------------------------------------------- #
#  Traces (C.8) et /v1/info
# --------------------------------------------------------------------------- #
def test_le_mode_etiquette_les_appels_et_entre_au_journal(api_reponse, caplog):
    caplog.set_level(logging.INFO, logger="api_modele.requetes")
    DoublureFournisseur.contenu = enrichie()

    repondre(api_reponse, mode="enrichi")
    repondre(api_reponse)

    lignes = [
        json.loads(FormatJson().format(e))
        for e in caplog.records
        if getattr(e, "champs", {}).get("route") == "/v1/reponse"
    ]
    assert [ligne["reglages"]["mode"] for ligne in lignes] == ["enrichi", "strict"]
    assert lignes[0]["reponse"]["complement"] is True
    assert "complement" not in lignes[1]["reponse"], "en strict, la ligne de 1.3.1"
    texte = json.dumps(lignes, ensure_ascii=False)
    assert "Titre inventé" not in texte, "ni le complément ni ses titres au journal"
    metriques = api_reponse.get("/metrics").text
    for mode in ("enrichi", "strict"):
        etiquettes = f'mode="{mode}",profil="factice-a",role="reponse",statut="ok"'
        assert f"api_modele_llm_appels_total{{{etiquettes}}} 1.0" in metriques


def test_info_publie_les_deux_modes(api_reponse):
    info = api_reponse.get("/v1/info", headers=entetes()).json()

    mode = info["modele_langage"]["reglages_reponse"]["mode"]
    assert mode == {"defaut": "strict", "valeurs": ["strict", "enrichi"]}


# --------------------------------------------------------------------------- #
#  La règle du titre visé, et la base en défaut
# --------------------------------------------------------------------------- #
def test_le_titre_vise_par_une_forme():
    from api_modele import complement as c

    catalogue = {c.cle("Autre Titre")}
    # La phrase d'avant nomme le titre : « il » s'y rapporte.
    assert (
        c.fautif("Lisez Autre Titre. Il est terminé.", ["Autre Titre"], [], catalogue)
        == "complement_metadonnee"
    )
    # Aucun titre nommé : le seul titre du complément.
    assert (
        c.fautif("Douze tomes, un régal.", ["Autre Titre"], [], catalogue)
        == "complement_metadonnee"
    )
    # Un titre contenu dans un autre ne compte pas : le plus long, hors catalogue.
    assert (
        c.fautif(
            "One Piece Party est en 3 tomes.", ["One Piece Party"], ["One Piece"], set()
        )
        is None
    )
    # Un titre vide ne se cherche pas.
    assert c.fautif("Rien à dire.", [], [""], set()) is None


@pytest.mark.parametrize(
    ("panne", "cause"),
    [
        (psycopg.errors.QueryCanceled, "delai_depasse"),
        (psycopg.OperationalError, "injoignable"),
    ],
    ids=["delai", "injoignable"],
)
def test_la_base_en_defaut_pendant_le_controle_du_complement(
    api_reponse, monkeypatch, panne, cause
):
    from api_modele import base

    def tomber(*_args: object) -> set[str]:
        raise panne("panne factice")

    monkeypatch.setattr(base, "lire_titres_du_catalogue", tomber)
    DoublureFournisseur.contenu = enrichie()

    r = repondre(api_reponse, mode="enrichi")

    assert r.status_code == 503
    corps = conforme(r)
    assert (corps["code"], corps["details"]["cause"]) == ("base_indisponible", cause)


def test_un_titre_du_catalogue_ecrit_sans_accent_est_reconnu(
    api_reponse, base_api: BaseApi
):
    ajouter_au_catalogue(base_api.dsn_admin, 4, "Été éternel")
    DoublureFournisseur.contenu = enrichie(
        complement="Ete eternel est termine.", complement_titres=["Ete eternel"]
    )

    r = repondre(api_reponse, mode="enrichi")

    assert r.status_code == 503
    assert conforme(r)["details"]["motif"] == "complement_metadonnee"


@pytest.mark.parametrize(
    "champs",
    [
        {"complement": "x" * 601},
        {"complement_titres": [f"Titre {i}" for i in range(6)]},
        {"complement_titres": ["x" * 201]},
    ],
    ids=["complement-601", "six-titres", "titre-201"],
)
def test_les_bornes_du_complement(api_reponse, champs):
    DoublureFournisseur.contenu = enrichie(**champs)

    r = repondre(api_reponse, mode="enrichi")

    assert r.status_code == 503
    assert conforme(r)["details"]["motif"] == "schema"


def test_sans_titre_le_complement_ne_lit_pas_la_base(api_reponse, monkeypatch):
    from api_modele import base

    def tomber(*_args: object) -> set[str]:
        raise psycopg.OperationalError("panne factice")

    monkeypatch.setattr(base, "lire_titres_du_catalogue", tomber)
    DoublureFournisseur.contenu = enrichie(
        complement="Un ton proche, à essayer.", complement_titres=[]
    )

    corps = conforme(repondre(api_reponse, mode="enrichi"))

    assert corps["complement"] == "Un ton proche, à essayer."
    assert corps["complement_titres"] == []


def test_les_listes_des_modes_sont_les_memes_partout():
    from typing import get_args

    from api_modele import configuration, metriques, modeles

    assert get_args(modeles.Mode) == configuration.MODES_CONNUS == metriques.MODES
    reglages = configuration.charger(
        {
            "API_MODELE_CLES": "application:" + "a" * 40,
            "API_MODELE_DSN": "postgresql://modele_api@localhost/apimanga",
            "MISTRAL_API_KEY": "factice",
        }
    )
    assert reglages.modele_langage.reponse.modes == configuration.MODES_CONNUS


def test_des_titres_sans_lettre_ni_chiffre_ne_lisent_rien():
    from typing import cast

    from api_modele import base

    assert base.lire_titres_du_catalogue(cast(Any, None), ["!!!", "—"]) == set()
