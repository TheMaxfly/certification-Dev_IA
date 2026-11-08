"""La recherche du catalogue, `GET /series` (contrat 0.5.0), et la publication du
module.

Ce qui est protégé ici : la normalisation appliquée à la saisie comme aux colonnes (une
seule table, liée en paramètres), les jokers de `LIKE` neutralisés, le classement et ce
qui a répondu, les bornes de la saisie (422, jamais une requête), le 503 neutre ; et la
publication du `docker-compose.yml` du module sur le bouclage seulement.

L'équivalence des deux chemins de la normalisation — Python sur la saisie, SQL sur les
colonnes — est éprouvée contre un vrai PostgreSQL par le témoin du harnais Compose
(`db_temoin_lecture_seule.py`, `verifier_la_normalisation`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg import OperationalError

from app import recherche
from app.database import get_pool
from app.main import create_app, search_series
from app.security import API_KEY_HEADER_NAME
from app.settings import ApiKey, Settings
from tests.faux_pool import FakePool

MODULE = Path(__file__).resolve().parent.parent
CLE = "cle-valide-de-test-0123456789abcdefghij"


# --------------------------------------------------------------------------- #
#  La normalisation
# --------------------------------------------------------------------------- #
def test_la_table_des_accents_a_deux_chaines_de_meme_longueur() -> None:
    de, vers = recherche.ACCENTS

    assert len(de) == len(vers) == 33
    assert vers.isascii() and vers == vers.lower()
    assert de == de.lower(), "minuscules seulement : `lower` passe avant la table"


@pytest.mark.parametrize(
    ("saisie", "attendu"),
    [("Shônen", "shonen"), ("Pīsu", "pisu"), ("ÉTÉ", "ete"), ("naruto", "naruto")],
)
def test_la_saisie_est_mise_en_minuscules_puis_sans_accent(
    saisie: str, attendu: str
) -> None:
    assert recherche.normaliser(saisie) == attendu


def test_les_jokers_de_like_de_la_saisie_sont_echappes() -> None:
    parametres = recherche.parametres("100%")

    assert parametres["exact"] == "100%", "l'égalité n'a pas de joker"
    assert parametres["debut"] == "100\\%%"
    assert parametres["contient"] == "%100\\%%"
    assert recherche.parametres("a_b\\c")["contient"] == "%a\\_b\\\\c%"


# --------------------------------------------------------------------------- #
#  La route, sur le pool simulé
# --------------------------------------------------------------------------- #
LIGNE = (
    736,
    "One Piece",
    "Manga",
    1997,
    "Eiichiro ODA",
    8.69,
    6936,
    "edite_en_france",
    110,
    "champ",
    "titre",
    None,
    2,  # le total, porté par chaque ligne (`count(*) OVER ()`)
)
ATTENDUS = {
    "de": recherche.ACCENTS[0],
    "vers": recherche.ACCENTS[1],
    "exact": "shonen",
    "debut": "shonen%",
    "contient": "%shonen%",
}


def test_les_parametres_lies_portent_la_saisie_normalisee_et_la_table() -> None:
    pool = FakePool([[LIGNE]])

    search_series(pool, q="  Shônen ", limit=5, offset=10)

    ((sql, params),) = pool.cursor.executions
    assert params == {**ATTENDUS, "limit": 5, "offset": 10}
    assert "Shônen" not in sql and "shonen" not in sql, "la saisie dans le SQL"
    assert recherche.ACCENTS[0] not in sql, "la table est liée, pas écrite"
    assert "translate(lower(" in sql
    assert "ESCAPE '\\'" in sql
    assert "count(*) OVER ()" in sql, "un seul parcours : le total avec la page"
    assert "manga.v_metadonnees_serie" in sql
    assert "ORDER BY rang, series_members_votes DESC NULLS LAST, series_id" in sql


def test_une_page_vide_au_dela_de_la_premiere_recompte_le_total() -> None:
    pool = FakePool([[], (7,)])

    reponse = search_series(pool, q="shonen", limit=20, offset=40)

    assert (reponse.total, reponse.items) == (7, [])
    (_, params_page), (sql_compte, params_compte) = pool.cursor.executions
    assert params_page == {**ATTENDUS, "limit": 20, "offset": 40}
    assert params_compte == ATTENDUS
    assert "SELECT count(*) FROM rangs" in sql_compte


def test_les_lignes_deviennent_des_resultats_avec_ce_qui_a_repondu() -> None:
    autre = (
        736,
        "One Piece",
        None,
        None,
        None,
        None,
        None,
        "inconnue",
        1,
        "repli_volumes_distincts",
        "autre_titre",
        "Wan Pīsu",
        2,
    )
    reponse = search_series(FakePool([[LIGNE, autre]]), q="one", limit=20)

    assert reponse.query == "one" and reponse.total == 2
    assert (reponse.limit, reponse.offset) == (20, 0)
    premier, second = reponse.items
    assert premier.model_dump() == {
        "series_id": 736,
        "title": "One Piece",
        "type": "Manga",
        "year": 1997,
        "auteurs": "Eiichiro ODA",
        "members_rating": 8.69,
        "members_votes": 6936,
        "disponibilite": "edite_en_france",
        "nb_volumes": 110,
        "nb_volumes_source": "champ",
        "correspondance": "titre",
        "titre_correspondant": None,
    }
    assert (second.correspondance, second.titre_correspondant) == (
        "autre_titre",
        "Wan Pīsu",
    )


def test_aucun_resultat_est_200_et_une_liste_vide_sans_second_parcours() -> None:
    pool = FakePool([[]])
    reponse = search_series(pool, q="zz", limit=20)

    assert reponse.total == 0 and reponse.items == []
    assert len(pool.cursor.executions) == 1


def test_des_espaces_seuls_sont_refuses_avant_toute_requete() -> None:
    pool = FakePool([])
    with pytest.raises(HTTPException) as erreur:
        search_series(pool, q="   ", limit=20)

    assert erreur.value.status_code == 422
    assert pool.cursor.executions == []


def test_une_erreur_de_base_est_un_503_neutre() -> None:
    with pytest.raises(HTTPException) as erreur:
        search_series(
            FakePool(error=OperationalError("hote secret")), q="naruto", limit=20
        )

    assert erreur.value.status_code == 503
    assert erreur.value.detail == "database unavailable"


@pytest.fixture
def client() -> TestClient:
    api = create_app(Settings(api_keys=(ApiKey(label="essai", secret=CLE),)))
    api.dependency_overrides[get_pool] = lambda: FakePool([[]])
    return TestClient(api)


@pytest.mark.parametrize(
    "requete",
    [
        "q=a",
        "q=" + "a" * 101,
        "q=naruto&limit=0",
        "q=naruto&limit=51",
        "q=naruto&offset=-1",
        "q=naruto&offset=1001",
        "",
    ],
    ids=[
        "1 caractère",
        "101",
        "limit 0",
        "limit 51",
        "offset -1",
        "offset 1001",
        "sans q",
    ],
)
def test_une_saisie_hors_bornes_est_un_422(client: TestClient, requete: str) -> None:
    reponse = client.get(f"/series?{requete}", headers={API_KEY_HEADER_NAME: CLE})

    assert reponse.status_code == 422


def test_la_recherche_est_protegee_et_rend_200_sur_une_saisie_valide(
    client: TestClient,
) -> None:
    assert client.get("/series?q=naruto").status_code == 401
    reponse = client.get("/series?q=naruto", headers={API_KEY_HEADER_NAME: CLE})

    assert reponse.status_code == 200
    assert reponse.json() == {
        "query": "naruto",
        "total": 0,
        "limit": 20,
        "offset": 0,
        "items": [],
    }


# --------------------------------------------------------------------------- #
#  La publication du module (D11)
# --------------------------------------------------------------------------- #
def test_le_compose_du_module_publie_sur_le_bouclage_seulement() -> None:
    texte = (MODULE / "docker-compose.yml").read_text(encoding="utf-8")
    publications: list[Any] = [
        ligne.strip().lstrip("- ").strip('"')
        for ligne in texte.splitlines()
        if ligne.strip().startswith("- ") and ligne.strip().endswith(':8000"')
    ]

    assert publications == ["127.0.0.1:8000:8000"]
    assert "APP_VERSION: ${APP_VERSION:-0.5.0}" in texte
