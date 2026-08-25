from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from psycopg import OperationalError

from app.main import (
    decode_cursor,
    encode_cursor,
    get_coverage,
    get_identity,
    get_kitsu_core,
    get_series,
    get_series_reviews,
    get_series_volumes,
    health,
    live,
    rag_doc,
    rag_export,
    rag_export_composition,
    rag_preview,
    search,
)
from tests.faux_pool import FakePool


def test_live_does_not_require_database() -> None:
    response = live()

    assert response.model_dump() == {"status": "ok", "db": "not_checked"}


def test_health_reports_database_ready() -> None:
    response = health(FakePool([(1,)]))

    assert response.model_dump() == {"status": "ok", "db": "ok"}


def test_health_hides_database_error_details() -> None:
    pool = FakePool(error=OperationalError("secret hostname"))

    response = health(pool)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload == {"status": "degraded", "db": "error"}
    assert "secret" not in response.body.decode()


def test_kitsu_uses_a_bound_parameter() -> None:
    row = (38, "one-piece", "One Piece", "Pirates", 8.7, 12, 3)
    pool = FakePool([row])

    response = get_kitsu_core(pool, 38)

    assert response.title_canonical == "One Piece"
    assert pool.cursor.executions[0][1] == (38,)


def test_kitsu_returns_404_for_unknown_id() -> None:
    with pytest.raises(HTTPException) as error:
        get_kitsu_core(FakePool([None]), 999999)

    assert error.value.status_code == 404


def test_database_errors_become_a_neutral_503() -> None:
    with pytest.raises(HTTPException) as error:
        get_kitsu_core(FakePool(error=OperationalError("secret hostname")), 38)

    assert error.value.status_code == 503
    assert error.value.detail == "database unavailable"


def test_rag_preview_accepts_a_null_boost() -> None:
    rows = [("kitsu:38", "kitsu", None, "Titres: One Piece")]

    response = rag_preview(FakePool([(1,), rows]), limit=1, offset=0)

    assert response.items[0].boost_score == 0.0


def test_rag_preview_conserve_la_troncature_et_le_tri_par_pertinence() -> None:
    """L'aperçu garde le contrat de l'ancien `/rag/export` : c'est son objet."""
    rows = [("kitsu:38", "kitsu", 120.0, "Titres: One Piece")]
    pool = FakePool([(1,), rows])

    rag_preview(pool, limit=1, offset=0)

    sql = pool.cursor.executions[1][0]
    assert "left(doc_text, 500)" in sql
    assert "ORDER BY boost_score DESC NULLS LAST, doc_key" in sql


def test_rag_export_lit_le_texte_integral_sans_troncature() -> None:
    """Contrat décisif de l'export : aucune fonction de coupe dans le SQL."""
    rows = [("kitsu:38", "kitsu", 120.0, "x" * 10_000, {"kitsu_id": 38})]
    pool = FakePool([rows])

    response = rag_export(pool, limit=1, cursor=None)

    sql = pool.cursor.executions[0][0]
    assert "left(" not in sql
    assert "doc_text" in sql
    assert response.items[0].doc_text == "x" * 10_000


def test_rag_export_ordonne_en_collation_c() -> None:
    """Le prédicat et le tri portent la MÊME collation, sinon la borne dérive."""
    pool = FakePool([[]])

    rag_export(pool, limit=10, cursor=encode_cursor("kitsu:38"))

    sql, params = pool.cursor.executions[0]
    assert 'WHERE doc_key COLLATE "C" > %s' in sql
    assert 'ORDER BY doc_key COLLATE "C"' in sql
    # Le curseur est décodé puis LIÉ, jamais interpolé dans le SQL.
    assert params == ("kitsu:38", 10)
    assert "kitsu:38" not in sql


def test_rag_export_n_expose_plus_offset() -> None:
    """`offset` est retiré, pas déprécié : un export plafonné n'est pas un export."""
    import inspect

    assert "offset" not in inspect.signature(rag_export).parameters


def test_rag_export_ferme_le_parcours_sur_une_page_incomplete() -> None:
    rows = [("kitsu:38", "kitsu", 1.0, "texte", {})]

    response = rag_export(FakePool([rows]), limit=10, cursor=None)

    assert response.next_cursor is None


def test_rag_export_propose_un_curseur_sur_une_page_pleine() -> None:
    rows = [
        ("kitsu:38", "kitsu", 1.0, "texte", {}),
        ("kitsu:39", "kitsu", 1.0, "texte", {}),
    ]

    response = rag_export(FakePool([rows]), limit=2, cursor=None)

    assert response.next_cursor is not None
    assert decode_cursor(response.next_cursor) == "kitsu:39"


def test_le_curseur_fait_un_aller_retour_fidele() -> None:
    for doc_key in ("kitsu:1", "ms_review:6374", "ms_hybrid:8514"):
        assert decode_cursor(encode_cursor(doc_key)) == doc_key


@pytest.mark.parametrize(
    ("curseur", "cas"),
    [
        ("!!!pas-du-base64!!!", "hors alphabet base64"),
        ("YWJ", "tronqué : longueur non multiple de 4"),
        ("////", "base64 valide mais octets non décodables en UTF-8"),
        ("", "vide"),
        ("é" * 4, "non ASCII"),
        (encode_cursor("x" * 256), "clé plus longue que doc_key ne peut l'être"),
    ],
)
def test_un_curseur_illisible_donne_422_jamais_500(curseur: str, cas: str) -> None:
    with pytest.raises(HTTPException) as error:
        decode_cursor(curseur)

    assert error.value.status_code == 422, cas


def test_composition_totalise_ce_qu_elle_detaille() -> None:
    """Un seul GROUP BY : le total ne peut pas dater d'un autre instant."""
    rows = [("kitsu_synopsis", 43085), ("ms_hybrid", 5608), ("ms_review", 3187)]
    pool = FakePool([rows])

    response = rag_export_composition(pool)

    assert response.total == 51880
    assert response.total == sum(entry.documents for entry in response.by_source)
    assert [entry.source for entry in response.by_source] == [
        "kitsu_synopsis",
        "ms_hybrid",
        "ms_review",
    ]
    assert response.measured_at.tzinfo is not None


def test_rag_document_returns_metadata() -> None:
    row = (
        "kitsu:38",
        "kitsu",
        3.0,
        "Titres: One Piece",
        {"kitsu_id": 38},
    )

    response = rag_doc(FakePool([row]), "kitsu:38")

    assert response.metadata == {"kitsu_id": 38}


def test_search_strips_query_and_returns_results() -> None:
    rows = [("kitsu:38", "kitsu", 3.0, 0.8, "Titres: One Piece")]
    pool = FakePool([(1,), rows])

    response = search(pool, " one piece ", limit=10, offset=0)

    assert response.query == "one piece"
    assert pool.cursor.executions[0][1] == ("one piece",)


def test_search_rejects_whitespace_only_query() -> None:
    with pytest.raises(HTTPException) as error:
        search(FakePool(), "  ", limit=10, offset=0)

    assert error.value.status_code == 422


# ---------------------------------------------------------------------------
# 3b — catalogue, identité, couverture
# ---------------------------------------------------------------------------
# Ce que ces tests protègent en priorité n'est pas la forme des réponses : c'est
# la table LUE. `/series/{id}/reviews` doit interroger `ms_reviews_all` (11 074)
# et jamais `ms_reviews` (3 187, corpus RAG héritage). Une régression y serait
# invisible à l'œil — les deux tables ont les mêmes colonnes.
LIGNE_SERIE = (
    8514,
    14700,
    "Kingdom",
    "https://ms/8514",
    "Seinen",
    "Shonen",
    2006,
    ["Kingudamu"],
    "Hara",
    "Hara",
    "Young Jump",
    ["En cours"],
    ["action", "guerre"],
    ["antiquité"],
    "Synopsis MS",
    "Synopsis retenu",
    120,
    8.7,
    3400,
    9.1,
    12,
    77,
    77,
    8.4,
    2.0,
    10.0,
    None,
    None,
    3480,
    False,
    [{"code": "action", "label_fr": "Action", "type": "genre"}],
)


def test_series_expose_les_deux_champs_de_genres_sans_les_fusionner() -> None:
    """`genres_source` et `genres_enriched` répondent à deux questions.

    Les fusionner par COALESCE fabriquerait une définition de « genres »
    n'existant nulle part en base — l'API exposerait alors une donnée qu'elle
    a créée."""
    reponse = get_series(FakePool([LIGNE_SERIE]), 8514)

    assert reponse.genres_source == ["action", "guerre"]
    assert [g.code for g in reponse.genres_enriched] == ["action"]
    assert reponse.genres_enriched[0].label_fr == "Action"


def test_series_n_expose_aucune_colonne_interne() -> None:
    """Le rapprochement laisse des colonnes de travail dans la table ; aucune
    ne doit sortir par HTTP. Seul `needs_review` passe, comme drapeau."""
    champs = set(get_series(FakePool([LIGNE_SERIE]), 8514).model_dump())

    interdits = {
        "ms_title_norm_x",
        "ms_title_norm_y",
        "_other_titles_list",
        "matched_title_norm",
        "fuzzy_low_score",
        "title_too_short",
        "kitsu_id_collision",
        "review_reason",
        "match_method",
        "match_score",
        "tags_enriched",
        "kitsu_slug",
        "kitsu_title_canonical",
    }
    assert champs & interdits == set()
    assert "needs_review" in champs


def test_series_404_sur_identifiant_inconnu() -> None:
    with pytest.raises(HTTPException) as erreur:
        get_series(FakePool([None]), 999999999)

    assert erreur.value.status_code == 404


def test_reviews_lit_le_referentiel_complet_pas_le_corpus_rag() -> None:
    """LE test de non-régression de 3b, cité dans le README."""
    pool = FakePool([(1,), (11074,), []])

    get_series_reviews(pool, 8514, limit=50, offset=0)

    sql_execute = " ".join(sql for sql, _ in pool.cursor.executions)
    assert "manga.ms_reviews_all" in sql_execute
    assert "manga.ms_reviews " not in sql_execute.replace("ms_reviews_all", "")


@pytest.mark.parametrize(
    ("series_id", "review_id", "review_url"),
    [
        (736, 1, "https://www.manga-sanctuary.com/critique/1-tome-1.html"),
        (8514, 4, "https://www.manga-sanctuary.com/critique/4-serie.html"),
        (9999, 5, "https://www.manga-sanctuary.com/critique/5-serie.html"),
    ],
)
def test_reviews_aligne_review_url_sur_le_referentiel_pour_trois_series(
    series_id: int, review_id: int, review_url: str
) -> None:
    ligne = (
        review_id,
        1,
        "https://www.manga-sanctuary.com/tome-1.html",
        review_url,
        "Titre",
        8.0,
        "lecteur",
        None,
        "jeu.",
        "volume",
        "volume",
        "Corps",
    )
    reponse = get_series_reviews(
        FakePool([(1,), (1,), [ligne]]), series_id, limit=1, offset=0
    )

    assert reponse.items[0].review_url == review_url


def test_reviews_expose_exactement_la_liste_de_champs_arretee() -> None:
    ligne = (
        1,
        1,
        "https://www.manga-sanctuary.com/tome-1.html",
        "https://www.manga-sanctuary.com/critique/1-tome-1.html",
        "Titre",
        8.0,
        "lecteur",
        None,
        "jeu.",
        "volume",
        "volume",
        "Corps",
    )
    reponse = get_series_reviews(
        FakePool([(1,), (1,), [ligne]]), 736, limit=1, offset=0
    )

    assert set(reponse.items[0].model_dump()) == {
        "review_id",
        "volume_number",
        "volume_url",
        "review_url",
        "title",
        "score",
        "author",
        "date",
        "date_raw",
        "type",
        "grain",
        "body",
    }


def test_reviews_rend_200_et_une_liste_vide_sur_serie_sans_critique() -> None:
    """Une série sans critique EXISTE. Répondre 404 dirait le contraire."""
    reponse = get_series_reviews(FakePool([(1,), (0,), []]), 12, limit=50, offset=0)

    assert reponse.total == 0
    assert reponse.items == []


def test_reviews_404_quand_la_serie_n_existe_pas() -> None:
    """La distinction d'avec le test précédent est tout l'intérêt des deux."""
    with pytest.raises(HTTPException) as erreur:
        get_series_reviews(FakePool([None]), 999999999, limit=50, offset=0)

    assert erreur.value.status_code == 404


def test_volumes_lie_ses_parametres_et_ordonne_les_nuls_en_dernier() -> None:
    pool = FakePool([(1,), (3,), []])

    get_series_volumes(pool, 8514, limit=10, offset=5)

    sql_volumes, params = pool.cursor.executions[-1]
    assert params == (8514, 10, 5)
    assert "NULLS LAST" in sql_volumes


def test_volumes_404_quand_la_serie_n_existe_pas() -> None:
    with pytest.raises(HTTPException) as erreur:
        get_series_volumes(FakePool([None]), 999999999, limit=50, offset=0)

    assert erreur.value.status_code == 404


def test_identity_rend_200_sans_decision_enregistree() -> None:
    """5 304 œuvres n'ont aucune décision courante. Elles existent quand même."""
    ligne = (
        14682,
        406,
        "Titre",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )

    reponse = get_identity(FakePool([ligne]), 14682)

    assert reponse.work_uid == 14682
    assert reponse.method is None and reponse.score is None


def test_identity_passe_par_work_identity_et_pas_par_la_vue_seule() -> None:
    """`v_match_current` est indexée par `series_id`, pas par `work_uid` :
    la jointure doit exister, sans quoi la route ne peut pas répondre."""
    ligne = (
        14689,
        415,
        "Titre",
        None,
        "11349",
        None,
        None,
        None,
        None,
        "exact_kitsu",
        0.96,
        "auto",
        None,
        None,
    )
    pool = FakePool([ligne])

    get_identity(pool, 14689)

    sql_identity = pool.cursor.executions[0][0]
    assert "manga.work_identity" in sql_identity
    assert "manga.v_match_current" in sql_identity
    assert pool.cursor.executions[0][1] == (14689,)


def test_identity_404_sur_work_uid_inconnu() -> None:
    with pytest.raises(HTTPException) as erreur:
        get_identity(FakePool([None]), 999999999)

    assert erreur.value.status_code == 404


def test_coverage_distingue_le_referentiel_du_corpus_rag_heritage() -> None:
    pool = FakePool(
        [
            (14670, 104107, 11074, 3187),
            [("exact_kitsu", 3584)],
            (12652, 12952, 1694, 525),
        ]
    )

    reponse = get_coverage(pool)

    assert reponse.totals.reviews == 11074
    assert reponse.totals.reviews_rag_legacy == 3187
    assert reponse.genres.series_without_any_genre == 1694
    assert reponse.identity_by_method[0].method == "exact_kitsu"


def test_coverage_ne_code_en_dur_aucune_limite() -> None:
    """Les limites connues sont MESURÉES à l'appel. Un chiffre figé dans le
    code cesserait d'être vrai au premier recalcul, sans que rien ne le dise —
    c'est exactement le défaut corrigé par le recalcul de l'enrichissement."""
    pool = FakePool([(1, 2, 3, 4), [], (10, 20, 30, 40)])

    reponse = get_coverage(pool)

    assert reponse.genres.series_without_any_genre == 30
    assert reponse.genres.series_on_generic_lgbt_only == 40
