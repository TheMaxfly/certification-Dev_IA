"""La route de réponse, `POST /v1/reponse` (spec 03, tableau D).

Sur l'API à profils factices (`conftest.api_reponse`) : le modèle est une doublure du
bouclage (`DoublureFournisseur`), le service d'embedding aussi, la base est jetable.
Aucun fournisseur réel n'est joint (garde `aucun_appel_hors_du_bouclage`). Noms de
membres factices seulement (`conftest.AUTEURS`).
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any

import httpx
import psycopg
import pytest
from conftest import (
    AUTEURS,
    CLES_FACTICES,
    DELAI_FACTICE_S,
    QUESTION,
    BaseApi,
    Doublure,
    DoublureFournisseur,
    Doublures,
    ServiceDoublure,
    dsn_du_role,
    entetes,
    environnement,
    inscrire_encodage,
    promouvoir,
    reglages_factices,
)
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from api_modele import recherche
from api_modele.application import creer_application
from api_modele.configuration import charger
from api_modele.contrat import DESTINATION
from api_modele.journal import FormatJson
from api_modele.modeles import Resultat
from api_modele.pseudonymes import empreinte
from api_modele.reponse import (
    PHRASE_AUCUN_PASSAGE,
    contexte_des_passages,
    titre_marque,
)

CONTRAT = json.loads(DESTINATION.read_text(encoding="utf-8"))
NOM = AUTEURS[0]  # un nom de membre factice, lu par la vue des pseudonymes


def repondre(api: TestClient, **champs: object) -> httpx.Response:
    return api.post(
        "/v1/reponse", json={"question": QUESTION, **champs}, headers=entetes()
    )


def conforme(r: httpx.Response) -> dict[str, Any]:
    """Le corps, validé contre le schéma que le contrat déclare pour son statut."""
    reponse = CONTRAT["paths"]["/v1/reponse"]["post"]["responses"][str(r.status_code)]
    schema = reponse["content"]["application/json"]["schema"]
    Draft202012Validator({**schema, "components": CONTRAT["components"]}).validate(
        r.json()
    )
    return r.json()


def appels(doublures: Doublures) -> list[dict[str, Any]]:
    return [r for r in doublures.recus() if r["chemin"] == "/v1/chat/completions"]


def encodees() -> list[str]:
    """Les textes que le service d'embedding a reçus à encoder."""
    return [t for r in Doublure.recus if r["route"] == "/embed" for t in r["inputs"]]


def historique(tours: int, taille: int = 10) -> list[dict[str, str]]:
    roles = ("libraire", "assistant")
    return [{"role": roles[i % 2], "contenu": "x" * taille} for i in range(tours)]


def sortie(**champs: object) -> str:
    base: dict[str, object] = {
        "refus": False,
        "motif_refus": None,
        "reponse": "Une aventure de pirates [S:1].",
        "series_citees": [{"serie_id": 1, "fragment_ids": [702]}],
    }
    return json.dumps(base | champs)


# --------------------------------------------------------------------------- #
#  La réponse et son contexte
# --------------------------------------------------------------------------- #
def test_une_reponse_avec_ses_sources_et_son_contexte(
    api_reponse, doublures, base_api: BaseApi
):
    DoublureFournisseur.usage = {"prompt_tokens": 100, "completion_tokens": 20}

    corps = conforme(repondre(api_reponse))

    assert (corps["refus"], corps["motif_refus"]) == (False, None)
    assert corps["series_citees"] == [
        {"serie_id": 1, "titre": "Série 1", "rang": 1, "fragment_ids": [702]}
    ]
    assert [s["serie_id"] for s in corps["series"]] == [1, 2]
    c = corps["contexte"]
    assert (c["profil"], c["fournisseur"], c["modele_langage"]) == (
        "factice-a",
        "mistral",
        "modele-a",
    )
    assert (c["corpus_id"], c["encodage_id"]) == ("v7", base_api.encodage_id)
    assert c["reglages"] == {
        "nombre_series": 5,
        "passages_par_serie": 2,
        "catalogue_seul": True,
    }
    assert c["reglages_appliques"] == {
        "temperature": 0.0,
        "longueur_max": 400,
        "mode": "strict",
        "top_p": 1.0,
        "reasoning_effort": None,
    }
    assert [a["role"] for a in c["appels"]] == ["reponse"]
    assert c["cout_estime_usd"] == pytest.approx((100 * 2 + 20 * 8) / 1e6)
    assert c["question_reformulee"] is None and c["reformulation_ignoree"] is None
    assert c["date_appel"].endswith("+00:00")
    assert c["version_api"] == "1.1.0"
    assert set(c["latences_ms"]) == {"recherche", "reformulation", "modele", "totale"}
    assert c["latences_ms"]["reformulation"] is None


def test_le_contexte_envoye_au_modele_est_celui_de_la_recherche(api_reponse, doublures):
    corps = conforme(repondre(api_reponse))

    (appel,) = appels(doublures)
    systeme, utilisateur = (m["content"] for m in appel["corps"]["messages"])
    assert systeme.startswith("Tu es un conseiller de lecture")
    # Les défauts de la route (5 séries × 2 passages) ; ici, deux séries au catalogue.
    attendu = contexte_des_passages(Resultat.model_validate(corps).series)
    assert utilisateur == f"Question : {QUESTION}\n\nPASSAGES :\n\n{attendu}"
    lignes = [ligne for ligne in attendu.splitlines() if ligne.startswith("[")]
    assert lignes == [
        "[S:1] Série 1 — critique de tome [F:702]",
        "[S:1] Série 1 — critique de tome [F:701]",
        "[S:2] Série 2 — synopsis [F:703]",
    ]
    textes = [p["extrait"] for s in corps["series"] for p in s["passages"]]
    assert all(t in utilisateur for t in textes)


def test_les_fragments_cites_sont_ceux_que_le_contexte_montre(api_reponse, doublures):
    # La doublure lit le contexte comme le modèle : elle ne rend que les identifiants
    # qu'il montre. Un contexte sans identifiants ne lui laisse rien à citer, et le
    # modèle réel en inventait (`fragment_inconnu`).
    DoublureFournisseur.lire_le_contexte = True

    corps = conforme(repondre(api_reponse))

    (citee,) = corps["series_citees"]
    (serie,) = (s for s in corps["series"] if s["serie_id"] == citee["serie_id"])
    envoyes = [p["fragment_id"] for p in serie["passages"]]
    assert envoyes and citee["fragment_ids"] == envoyes


@pytest.mark.parametrize(
    ("ecrit", "rendu"),
    [
        ("Une aventure [S:1].", "Une aventure Série 1 [S:1]."),
        ("Une aventure, Série 1 [S:1].", None),
        ("Une aventure, « *série 1* » [S:1].", None),
        ("Une aventure [S:1] *Série 1*.", None),
        ("Série 1 fait rire. [S:1]", "Série 1 fait rire. Série 1 [S:1]"),
        (
            "[S:1] puis [S:2], pas [S:99].",
            "Série 1 [S:1] puis Série 2 [S:2], pas [S:99].",
        ),
    ],
)
def test_le_titre_du_catalogue_accompagne_chaque_marque(
    api_reponse, doublures, ecrit, rendu
):
    # Ni juste avant ni juste après (casse, espaces, emphase, guillemets et
    # ponctuation ignorés) : la route l'insère devant ; une marque hors des séries
    # rendues reste telle quelle.
    DoublureFournisseur.contenu = sortie(reponse=ecrit)

    assert conforme(repondre(api_reponse))["reponse"] == (rendu or ecrit)


@pytest.mark.parametrize(
    ("ecrit", "titre"),
    [
        ("Une aventure [S:1].", False),
        ("Une aventure, « *série 1* » [S:1].", True),
        ("Une aventure [S:1] *Série 1*.", True),
        ("Série 1 fait rire. [S:1]", False),
        ("Série 1 [S:1], puis encore [S:1].", False),
        ("Série 1 fait rire, Série 2 [S:2].", None),
    ],
)
def test_le_titre_se_juge_comme_il_s_insere(ecrit, titre):
    # `titre_present` suit la règle de l'insertion : le titre juste avant ou juste
    # après chaque marque de la série ; une série que le texte ne marque pas n'est
    # pas jugée.
    assert titre_marque(1, "Série 1", ecrit) is titre


def test_les_reglages_demandes_arrivent_tels_quels(api_reponse, doublures):
    conforme(repondre(api_reponse, temperature=0.7, longueur_max=120))
    conforme(repondre(api_reponse, profil="factice-b"))

    a, b = (r["corps"] for r in appels(doublures))
    assert (a["temperature"], a["max_tokens"], a["top_p"]) == (0.7, 120, 1.0)
    assert "reasoning_effort" not in a
    assert (b["max_completion_tokens"], b["top_p"], b["reasoning_effort"]) == (
        400,
        1.0,
        "none",
    )
    assert "temperature" not in b  # déclarée absente par factice-b


def test_deux_profils_factices_deux_doublures(api_reponse, doublures):
    pour_a = conforme(repondre(api_reponse, profil="factice-a"))
    pour_b = conforme(repondre(api_reponse, profil="factice-b"))
    refus = repondre(api_reponse, profil="factice-b", temperature=0.5)

    assert [r["port"] for r in appels(doublures)] == [
        doublures.port_a,
        doublures.port_b,
    ]
    assert (pour_a["contexte"]["profil"], pour_b["contexte"]["profil"]) == (
        "factice-a",
        "factice-b",
    )
    assert pour_b["contexte"]["modele_langage"] == "modele-b"
    assert pour_b["contexte"]["statut_profil"] == "apercu"
    assert pour_b["contexte"]["reglages_absents"] == ["temperature"]
    assert pour_b["contexte"]["reglages_appliques"]["temperature"] is None
    assert refus.status_code == 422
    assert conforme(refus)["code"] == "reglage_non_expose"
    assert refus.json()["details"] == {"reglage": "temperature", "profil": "factice-b"}
    assert len(appels(doublures)) == 2  # le refus n'a pas appelé


# --------------------------------------------------------------------------- #
#  Validation de l'entrée : avant tout appel
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("champ", "valides", "invalides"),
    [
        ("temperature", (0.0, 1.0), (-0.1, 1.1)),
        ("longueur_max", (50, 1000), (49, 1001)),
        ("nombre_series", (1, 50), (0, 51)),
        ("passages_par_serie", (1, 5), (0, 6)),
    ],
)
def test_les_bornes_de_chaque_reglage(
    api_reponse, doublures, champ, valides, invalides
):
    for valeur in valides:
        assert repondre(api_reponse, **{champ: valeur}).status_code == 200, valeur
    for valeur in invalides:
        r = repondre(api_reponse, **{champ: valeur})
        assert r.status_code == 422, valeur
        assert conforme(r)["code"] == "entree_invalide"
        (fautif,) = r.json()["details"]["champs"]
        assert (fautif["champ"], fautif["raison"]) == (champ, "hors_bornes")
    assert len(appels(doublures)) == len(valides)


@pytest.mark.parametrize(
    ("tours", "motif"),
    [
        (historique(11), "trop_de_tours"),
        (
            [
                {"role": "libraire", "contenu": "x" * 2000},
                {"role": "assistant", "contenu": "x" * 1999},
                {"role": "libraire", "contenu": "x"},
                {"role": "assistant", "contenu": "x"},
            ],
            "trop_long",
        ),
        (historique(2)[::-1], "premier_tour_non_libraire"),
        (
            [
                {"role": "libraire", "contenu": "a"},
                {"role": "libraire", "contenu": "b"},
            ],
            "roles_non_alternes",
        ),
        (historique(3), "dernier_tour_non_assistant"),
    ],
)
def test_un_historique_invalide(api_reponse, doublures, tours, motif):
    r = repondre(api_reponse, historique=tours)

    assert r.status_code == 422
    assert conforme(r)["code"] == "historique_invalide"
    assert r.json()["details"] == {"motif": motif}
    assert appels(doublures) == []


@pytest.mark.parametrize(
    "tours",
    [
        historique(10),
        [
            {"role": "libraire", "contenu": "x" * 2000},
            {"role": "assistant", "contenu": "x" * 1998},
            {"role": "libraire", "contenu": "x"},
            {"role": "assistant", "contenu": "x"},
        ],
    ],
    ids=["10_tours", "4000_caracteres"],
)
def test_un_historique_a_ses_bornes_passe(api_reponse, tours):
    assert repondre(api_reponse, historique=tours).status_code == 200


@pytest.mark.parametrize(
    ("champs", "code"),
    [
        ({"question": "x" * 501}, "question_trop_longue"),
        ({"profil": "absent"}, "profil_inconnu"),
        ({"mode": "enrichi"}, "entree_invalide"),
        ({"inconnu": 1}, "entree_invalide"),
    ],
)
def test_les_autres_422(api_reponse, doublures, champs, code):
    r = repondre(api_reponse, **champs)

    assert r.status_code == 422
    assert conforme(r)["code"] == code
    assert appels(doublures) == []


def test_un_profil_indisponible(
    base_api: BaseApi, pgpass, service_doublure, doublures, tmp_path
):
    env = environnement(dsn_du_role(base_api.dsn_admin)) | {
        "CLE_FACTICE_A": CLES_FACTICES["CLE_FACTICE_A"]
    }
    reglages = replace(
        charger(env, reglages_factices(tmp_path, doublures)), pool_attente_s=2.0
    )
    app = creer_application(reglages, instance=service_doublure.instance, environ=env)
    with TestClient(app) as api:
        r = repondre(api, profil="factice-b")

    assert r.status_code == 422
    assert conforme(r)["code"] == "profil_indisponible"
    assert r.json()["details"] == {"profil": "factice-b"}


# --------------------------------------------------------------------------- #
#  La reformulation
# --------------------------------------------------------------------------- #
CONVERSATION = [
    {"role": "libraire", "contenu": "Vous auriez des mangas de pirates ?"},
    {"role": "assistant", "contenu": "Oui, une série d'aventure en mer."},
]
REFORMULEE = "Une aventure de pirates en mer convient-elle à un adolescent ?"


def test_sans_historique_un_seul_appel(api_reponse, doublures):
    corps = conforme(repondre(api_reponse))

    assert len(appels(doublures)) == 1
    assert corps["contexte"]["question_reformulee"] is None


def test_avec_historique_sans_reformuler_un_seul_appel(api_reponse, doublures):
    corps = conforme(repondre(api_reponse, historique=CONVERSATION, reformuler=False))

    (appel,) = appels(doublures)
    utilisateur = appel["corps"]["messages"][1]["content"]
    assert utilisateur.startswith(
        "Libraire : Vous auriez des mangas de pirates ?\n"
        "Assistant : Oui, une série d'aventure en mer.\n\nQuestion : "
    )
    assert corps["contexte"]["question_reformulee"] is None


def test_avec_historique_la_recherche_recoit_la_question_reformulee(
    api_reponse, doublures
):
    DoublureFournisseur.contenu_reformulation = json.dumps({"question": REFORMULEE})
    DoublureFournisseur.usage = {"prompt_tokens": 100, "completion_tokens": 20}

    corps = conforme(repondre(api_reponse, historique=CONVERSATION))

    reformulation, reponse = appels(doublures)
    assert reformulation["corps"]["messages"][0]["content"].startswith(
        "Voici les tours"
    )
    assert (
        reformulation["corps"]["temperature"],
        reformulation["corps"]["max_tokens"],
    ) == (
        0.0,
        150,
    )
    assert reformulation["corps"]["response_format"]["json_schema"]["schema"][
        "required"
    ] == ["question"]
    assert any(REFORMULEE in t for t in encodees())
    assert not any(QUESTION in t for t in encodees())
    assert f"Question : {REFORMULEE}" in reponse["corps"]["messages"][1]["content"]
    c = corps["contexte"]
    assert c["question_reformulee"] == REFORMULEE
    assert [a["role"] for a in c["appels"]] == ["reformulation", "reponse"]
    assert c["cout_estime_usd"] == pytest.approx(2 * (100 * 2 + 20 * 8) / 1e6)
    assert c["latences_ms"]["reformulation"] is not None


def test_une_reformulation_trop_longue_est_ignoree(api_reponse, doublures):
    DoublureFournisseur.contenu_reformulation = json.dumps({"question": "x" * 600})

    corps = conforme(repondre(api_reponse, historique=CONVERSATION))

    assert corps["contexte"]["reformulation_ignoree"] == "trop_longue"
    assert corps["contexte"]["question_reformulee"] is None
    assert any(QUESTION in t for t in encodees())
    assert len(appels(doublures)) == 2


def test_un_nom_de_membre_dans_la_question_reformulee(api_reponse, doublures):
    DoublureFournisseur.contenu_reformulation = json.dumps(
        {"question": f"Que pense {NOM} de cette série ?"}
    )

    r = repondre(api_reponse, historique=CONVERSATION)

    assert r.status_code == 503
    assert conforme(r)["code"] == "sortie_modele_invalide"
    assert r.json()["details"] == {"motif": "pseudonyme", "role": "reformulation"}
    assert encodees() == []  # la recherche n'a pas été appelée
    assert len(appels(doublures)) == 1


def test_une_reformulation_hors_schema(api_reponse, doublures):
    DoublureFournisseur.contenu_reformulation = json.dumps({"question": "  "})

    r = repondre(api_reponse, historique=CONVERSATION)

    assert r.status_code == 503
    assert r.json()["details"] == {"motif": "schema", "role": "reformulation"}


# --------------------------------------------------------------------------- #
#  Aucun passage : refus, sans appel
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("vide", ["aucune_serie", "series_sans_passage"])
def test_aucun_passage_refus_sans_appel(api_reponse, doublures, monkeypatch, vide):
    vraie = recherche.rechercher

    def sans_passage(*args: Any) -> Resultat:
        r = vraie(*args)
        if vide == "aucune_serie":
            return r.model_copy(update={"series": []})
        series = [s.model_copy(update={"passages": []}) for s in r.series]
        return r.model_copy(update={"series": series})

    monkeypatch.setattr(recherche, "rechercher", sans_passage)

    corps = conforme(repondre(api_reponse))

    assert (corps["refus"], corps["motif_refus"]) == (True, "aucun_passage")
    assert corps["reponse"] == PHRASE_AUCUN_PASSAGE
    assert corps["series_citees"] == []
    assert corps["contexte"]["appels"] == []
    assert corps["contexte"]["cout_estime_usd"] == 0
    assert corps["contexte"]["date_appel"] is None
    assert appels(doublures) == []


def test_un_refus_du_modele_est_un_200(api_reponse):
    DoublureFournisseur.contenu = sortie(
        refus=True, motif_refus="hors_catalogue", series_citees=[]
    )

    corps = conforme(repondre(api_reponse))

    assert (corps["refus"], corps["motif_refus"]) == (True, "hors_catalogue")
    assert corps["series_citees"] == []


# --------------------------------------------------------------------------- #
#  Les contrôles de sortie
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("contenu", "motif"),
    [
        ("pas du json", "schema"),
        (json.dumps({"refus": False, "reponse": "x"}), "schema"),
        (
            sortie(series_citees=[{"serie_id": 99, "fragment_ids": []}]),
            "serie_inconnue",
        ),
        (
            sortie(series_citees=[{"serie_id": 2, "fragment_ids": [702]}]),
            "fragment_inconnu",
        ),
        (sortie(refus=True, motif_refus="inconnu"), "refus_incoherent"),
        (sortie(refus=True, series_citees=[]), "refus_incoherent"),
        (sortie(series_citees=[]), "refus_incoherent"),
        (sortie(motif_refus="inconnu"), "refus_incoherent"),
        (sortie(reponse=f"Selon {NOM}, c'est la meilleure."), "pseudonyme"),
    ],
)
def test_une_sortie_hors_contrat(api_reponse, caplog, contenu, motif):
    caplog.set_level(logging.DEBUG, logger="api_modele")
    DoublureFournisseur.contenu = contenu

    r = repondre(api_reponse)

    assert r.status_code == 503
    assert conforme(r)["code"] == "sortie_modele_invalide"
    assert r.json()["details"] == {"motif": motif, "role": "reponse"}
    metriques = api_reponse.get("/metrics").text
    assert f'api_modele_sortie_invalide_total{{motif="{motif}"}} 1.0' in metriques
    lignes = "\n".join(FormatJson().format(e) for e in caplog.records)
    assert (
        NOM not in lignes and "meilleure" not in lignes and "pas du json" not in lignes
    )
    assert contenu not in r.text


# --------------------------------------------------------------------------- #
#  Les homonymes (§ 7 bis), sur une liste d'homonymes factice
# --------------------------------------------------------------------------- #
@pytest.fixture
def api_homonymes(base_api: BaseApi, pgpass, service_doublure, doublures, tmp_path):
    def fabriquer(admis: bool) -> TestClient:
        fichier = tmp_path / f"homonymes_{admis}.csv"
        lignes = ["doc_key,empreinte_pseudo,motif"]
        if admis:
            lignes.append(f"ms_review:1,{empreinte(NOM)},personnage")
        fichier.write_text("\n".join(lignes) + "\n", encoding="utf-8")
        env = environnement(dsn_du_role(base_api.dsn_admin)) | CLES_FACTICES
        reglages = replace(
            charger(env, reglages_factices(tmp_path, doublures)), pool_attente_s=2.0
        )
        app = creer_application(
            reglages, instance=service_doublure.instance, environ=env, homonymes=fichier
        )
        return TestClient(app)

    return fabriquer


@pytest.fixture
def nom_dans_un_passage(monkeypatch):
    """Le premier passage de la série 1 porte le nom (un personnage)."""
    vraie = recherche.rechercher

    def avec_le_nom(*args: Any) -> Resultat:
        r = vraie(*args)
        serie = r.series[0]
        premier = serie.passages[0].model_copy(
            update={"extrait": f"{NOM} est le héros de cette aventure."}
        )
        serie = serie.model_copy(update={"passages": [premier, *serie.passages[1:]]})
        return r.model_copy(update={"series": [serie, *r.series[1:]]})

    monkeypatch.setattr(recherche, "rechercher", avec_le_nom)


AVEC_LE_NOM = sortie(reponse=f"Le héros, {NOM}, grandit en mer [S:1].")


def test_un_homonyme_admis_present_dans_un_passage_envoye(
    api_homonymes, nom_dans_un_passage
):
    DoublureFournisseur.contenu = AVEC_LE_NOM
    with api_homonymes(admis=True) as api:
        assert repondre(api).status_code == 200


def test_le_meme_homonyme_absent_des_passages_de_la_requete(api_homonymes):
    DoublureFournisseur.contenu = AVEC_LE_NOM
    with api_homonymes(admis=True) as api:
        r = repondre(api)

    assert r.status_code == 503
    assert r.json()["details"]["motif"] == "pseudonyme"


def test_un_homonyme_admis_venu_de_la_question_de_la_requete(api_homonymes, doublures):
    # Décision 7 bis amendée (2026-10-30) : le nom est dans la question, dans aucun
    # passage ; admis pour un document au moins, il passe — dans la réponse comme dans
    # la question reformulée.
    DoublureFournisseur.contenu = AVEC_LE_NOM
    DoublureFournisseur.contenu_reformulation = json.dumps(
        {"question": f"Que deviens {NOM} dans cette série ?"}
    )
    question = f"Vous auriez l'histoire de {NOM} ?"
    with api_homonymes(admis=True) as api:
        assert repondre(api, question=question).status_code == 200
        assert (
            repondre(api, question=question, historique=CONVERSATION).status_code == 200
        )
    with api_homonymes(admis=False) as api:
        r = repondre(api, question=question)
    assert (r.status_code, r.json()["details"]["motif"]) == (503, "pseudonyme")


def test_un_nom_non_admis_present_dans_un_passage(api_homonymes, nom_dans_un_passage):
    DoublureFournisseur.contenu = AVEC_LE_NOM
    with api_homonymes(admis=False) as api:
        r = repondre(api)

    assert r.status_code == 503
    assert r.json()["details"]["motif"] == "pseudonyme"


# --------------------------------------------------------------------------- #
#  Les 503 de la route
# --------------------------------------------------------------------------- #
def test_le_fournisseur_refuse(api_reponse):
    DoublureFournisseur.statut = 500

    r = repondre(api_reponse)

    assert r.status_code == 503
    assert conforme(r)["code"] == "fournisseur_refus"
    assert r.json()["details"] == {
        "profil": "factice-a",
        "role": "reponse",
        "statut": 500,
    }


def test_le_fournisseur_est_trop_lent(api_reponse):
    DoublureFournisseur.retard_s = DELAI_FACTICE_S * 3

    r = repondre(api_reponse)

    assert r.status_code == 503
    assert conforme(r)["code"] == "fournisseur_delai"


def test_le_fournisseur_est_injoignable(api_reponse, doublures):
    for serveur in doublures.serveurs:
        serveur.shutdown()
        serveur.server_close()

    r = repondre(api_reponse)

    assert r.status_code == 503
    assert conforme(r)["code"] == "fournisseur_injoignable"


def test_la_recherche_ne_repond_pas(api_reponse, service_doublure: ServiceDoublure):
    service_doublure.arreter()

    r = repondre(api_reponse)

    assert r.status_code == 503
    assert conforme(r)["code"] == "service_embedding_indisponible"


def test_le_service_est_incoherent(api_reponse):
    Doublure.refus_embed = 500
    Doublure.info["model_sha"] = "0" * 40

    r = repondre(api_reponse)

    assert r.status_code == 503
    assert conforme(r)["code"] == "service_embedding_incoherent"


def test_la_base_ne_repond_pas(api_reponse):
    api_reponse.app.state.etat.pool.close()  # type: ignore[attr-defined]

    r = repondre(api_reponse)

    assert r.status_code == 503
    assert conforme(r)["code"] == "base_indisponible"


def test_l_encodage_a_change(api_reponse, base_api: BaseApi, service_doublure):
    with psycopg.connect(base_api.dsn_admin) as cx:
        autre = inscrire_encodage(cx, service_doublure.instance, outil_version="1.9.5")
        promouvoir(cx, autre)

    r = repondre(api_reponse)

    assert r.status_code == 503
    assert conforme(r)["code"] == "encodage_en_service_change"


# --------------------------------------------------------------------------- #
#  Traces, versionnage, équivalence
# --------------------------------------------------------------------------- #
def test_la_ligne_de_journal_de_la_requete(api_reponse, caplog):
    caplog.set_level(logging.INFO, logger="api_modele.requetes")
    DoublureFournisseur.contenu_reformulation = json.dumps({"question": REFORMULEE})

    repondre(api_reponse, historique=CONVERSATION, identite="comptoir-1")

    (ligne,) = [
        json.loads(FormatJson().format(e))
        for e in caplog.records
        if getattr(e, "champs", {}).get("route") == "/v1/reponse"
    ]
    suivi = ligne["reponse"]
    assert isinstance(suivi.pop("cout_estime_usd"), float)
    assert suivi == {
        "nb_tours_historique": 2,
        "taille_historique": sum(len(t["contenu"]) for t in CONVERSATION),
        "profil": "factice-a",
        "reformulation": "faite",
        "refus": False,
        "nb_series_citees": 1,
    }
    assert ligne["identite"] == "comptoir-1"
    texte = json.dumps(ligne, ensure_ascii=False)
    for absent in (
        QUESTION,
        REFORMULEE,
        "mangas de pirates",
        "Une aventure de pirates [",
    ):
        assert absent not in texte


def test_info_annonce_la_1_1_0_et_v2_n_existe_pas(api_reponse):
    assert (
        api_reponse.get("/v1/info", headers=entetes()).json()["version_api"] == "1.1.0"
    )
    assert (
        api_reponse.post("/v2/reponse", json={"question": QUESTION}).status_code == 404
    )


@pytest.mark.parametrize("question", [QUESTION, "une histoire de cuisine"])
def test_la_reponse_cite_ce_que_la_recherche_a_classe(api_reponse, question):
    """Équivalence, sur le harnais : mêmes réglages, mêmes séries que /v1/recherche."""
    DoublureFournisseur.contenu = sortie(
        refus=True, motif_refus="inconnu", series_citees=[]
    )
    reglages = {"nombre_series": 10, "passages_par_serie": 1, "catalogue_seul": True}

    attendu = api_reponse.post(
        "/v1/recherche", json={"question": question, **reglages}, headers=entetes()
    ).json()["series"]
    obtenu = conforme(
        api_reponse.post(
            "/v1/reponse", json={"question": question, **reglages}, headers=entetes()
        )
    )["series"]

    assert obtenu == attendu


def test_hors_catalogue_une_oeuvre_sans_serie_ne_peut_pas_etre_citee(
    api_reponse, doublures
):
    corps = conforme(repondre(api_reponse, catalogue_seul=False))
    DoublureFournisseur.contenu = sortie(
        series_citees=[{"serie_id": 60, "fragment_ids": [704]}]
    )
    cite = repondre(api_reponse, catalogue_seul=False)

    utilisateur = appels(doublures)[0]["corps"]["messages"][1]["content"]
    assert "[K:60] Kitsu Soixante — synopsis" in utilisateur
    assert any(s["serie_id"] is None for s in corps["series"])
    assert cite.status_code == 503
    assert cite.json()["details"]["motif"] == "serie_inconnue"


def test_un_prix_non_declare_rend_un_cout_nul(
    base_api: BaseApi, pgpass, service_doublure, doublures, tmp_path
):
    env = environnement(dsn_du_role(base_api.dsn_admin)) | CLES_FACTICES
    reglages = charger(env, reglages_factices(tmp_path, doublures))
    ml = reglages.modele_langage
    sans_prix = replace(ml, profils=tuple(replace(p, prix=None) for p in ml.profils))
    reglages = replace(reglages, modele_langage=sans_prix, pool_attente_s=2.0)
    app = creer_application(reglages, instance=service_doublure.instance, environ=env)
    with TestClient(app) as api:
        corps = conforme(repondre(api))

    assert corps["contexte"]["cout_estime_usd"] is None


def test_une_question_blanche_est_refusee(api_reponse, doublures):
    r = repondre(api_reponse, question="   ")

    assert r.status_code == 422
    assert r.json()["details"]["champs"] == [{"champ": "question", "raison": "vide"}]
    assert appels(doublures) == []


def test_un_historique_vide_vaut_sans_historique(api_reponse, doublures):
    corps = conforme(repondre(api_reponse, historique=[]))

    assert len(appels(doublures)) == 1
    assert corps["contexte"]["question_reformulee"] is None


def test_le_texte_de_la_question_au_journal_sur_reglage_seulement(
    base_api: BaseApi, pgpass, service_doublure, doublures, tmp_path, caplog
):
    caplog.set_level(logging.INFO, logger="api_modele.requetes")
    env = environnement(dsn_du_role(base_api.dsn_admin)) | CLES_FACTICES
    env["API_MODELE_JOURNAL_QUESTION"] = "vrai"
    reglages = replace(
        charger(env, reglages_factices(tmp_path, doublures)), pool_attente_s=2.0
    )
    app = creer_application(reglages, instance=service_doublure.instance, environ=env)
    with TestClient(app) as api:
        assert repondre(api).status_code == 200

    (ligne,) = [
        json.loads(FormatJson().format(e))
        for e in caplog.records
        if getattr(e, "champs", {}).get("route") == "/v1/reponse"
    ]
    assert ligne["question"] == QUESTION
    # La réponse du modèle, elle, n'y entre jamais.
    assert "Une aventure de pirates [S:1]" not in json.dumps(ligne, ensure_ascii=False)
