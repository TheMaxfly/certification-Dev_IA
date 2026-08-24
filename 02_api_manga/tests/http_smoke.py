"""Smoke test HTTP exécuté dans le Compose d'intégration.

La base visée est jetable, son schéma reconstruit par les 15 migrations de
`database/migrations/`, son contenu posé par `tests/fixtures/002_sample_data.sql`.
Chaque attendu chiffré ci-dessous se dérive de cette fixture et de la formule
de boost de PRODUCTION ; la dérivation est écrite en commentaire au-dessus de
l'assertion, jamais laissée au lecteur.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

BASE_URL = "http://api:8000"

# Clé jetable du harnais, posée par `compose.integration.yml`. Elle n'ouvre
# rien : elle ne vaut que pour la base temporaire de ce Compose.
API_KEY = os.environ["API_KEY"]
API_KEY_HEADER = "X-API-Key"

# Les onze routes de données, et les routes qui restent ouvertes. Ces deux
# listes sont le contrat d'accès du module, vérifié ici contre une API réelle
# et non contre un client de test en mémoire.
ROUTES_PROTEGEES = (
    "/kitsu/38",
    "/rag/preview",
    "/rag/export",
    "/rag/export/composition",
    f"/rag/doc/{quote_plus('kitsu:38')}",
    "/search?q=one",
    # 3b — catalogue, identité, couverture.
    "/series/736",
    "/series/736/volumes",
    "/series/736/reviews",
    "/identity/4242",
    "/coverage",
)
ROUTES_OUVERTES = ("/live", "/health", "/docs", "/openapi.json")

# Formule de boost de la vue `manga.rag_docs_scored` en production :
#   100 / trending_pos + 30 / popular_pos + 20 / top_pos
# La fixture pose trending_pos = 1, popular_pos = 2, top_pos = 4, donc :
#   100 / 1 + 30 / 2 + 20 / 4 = 100 + 15 + 5 = 120.0
BOOST_ATTENDU = 120.0

# Valeur qu'aurait le MÊME document sous l'ancienne formule 3/2/1, celle que
# transportait le DDL supprimé du module :
#   3 / 1 + 2 / 2 + 1 / 4 = 3 + 1 + 0.25 = 4.25
# Elle n'est pas attendue : elle sert de repoussoir au test discriminant.
BOOST_FORMULE_ABANDONNEE = 4.25

# La fixture produit un document par source du corpus : `kitsu_synopsis` via
# rag_kitsu_docs, `ms_review` via rag_reviews_docs, `ms_hybrid` via ms_kitsu_map
# croisé avec les deux précédents. Soit 3 lignes dans manga.rag_export_docs.
DOCS_ATTENDUS = {"kitsu:38", "ms_hybrid:736", "ms_review:1"}
SOURCES_ATTENDUES = {"kitsu_synopsis", "ms_hybrid", "ms_review"}


def _corps(brut: bytes, type_contenu: str) -> Any:
    """Décode le corps en JSON quand c'en est, en texte sinon.

    `/docs` et `/redoc` renvoient du HTML : les routes ouvertes ne sont pas
    toutes des routes de données, et une aide qui exigerait du JSON partout
    échouerait sur elles.
    """
    if not brut:
        return None
    if "application/json" in type_contenu:
        return json.loads(brut)
    return brut.decode("utf-8", errors="replace")


def appeler(path: str, *, cle: str | None = API_KEY) -> tuple[int, Any]:
    """Appelle l'API et renvoie (statut, corps), sans lever sur une erreur HTTP."""
    entetes = {API_KEY_HEADER: cle} if cle is not None else {}
    requete = Request(f"{BASE_URL}{path}", headers=entetes)  # noqa: S310
    try:
        with urlopen(requete, timeout=60) as response:  # noqa: S310
            return response.status, _corps(
                response.read(), response.headers.get("content-type", "")
            )
    except HTTPError as erreur:
        return erreur.code, _corps(
            erreur.read(), erreur.headers.get("content-type", "")
        )


def get_json(path: str) -> dict[str, Any]:
    statut, corps = appeler(path)
    assert statut == 200, f"{path} → HTTP {statut}"
    return corps


def statut_http(path: str) -> int:
    """Le statut seul, pour les cas où c'est LUI qu'on affirme (404, 200)."""
    statut, _ = appeler(path)
    return statut


def verifier_le_controle_d_acces() -> None:
    """Contrôles 3 et 4, exercés contre l'API réelle.

    Sans en-tête, avec une clé invalide, et avec la bonne clé à un caractère
    près : les onze routes de données doivent refuser. Les sondes et la
    documentation doivent répondre SANS clé — c'est une règle du module, pas
    une omission, et elle mérite donc d'être testée comme telle.
    """
    presque = API_KEY[:-1] + ("z" if API_KEY[-1] != "z" else "a")
    assert presque != API_KEY and len(presque) == len(API_KEY)

    for route in ROUTES_PROTEGEES:
        sans_cle, corps_sans = appeler(route, cle=None)
        assert sans_cle == 401, f"{route} sans clé → {sans_cle}, attendu 401"

        invalide, corps_invalide = appeler(
            route, cle="cle-inventee-de-la-bonne-taille-1234"
        )
        assert invalide == 401, f"{route} clé invalide → {invalide}"

        voisine, _ = appeler(route, cle=presque)
        assert voisine == 401, f"{route} clé à un caractère près → {voisine}"

        # Pas d'oracle : les deux refus sont indiscernables du dehors.
        assert corps_sans == corps_invalide, route

        autorise, _ = appeler(route)
        assert autorise == 200, f"{route} avec la bonne clé → {autorise}"

    for route in ROUTES_OUVERTES:
        statut, _ = appeler(route, cle=None)
        assert statut == 200, (
            f"{route} sans clé → {statut}, attendu 200 (route ouverte)"
        )


def verifier_les_curseurs_illisibles() -> None:
    """Contrôle 6 : un curseur illisible donne 422, jamais 500."""
    for curseur in ("!!!pas-du-base64!!!", "YWJ", "////", quote_plus("é" * 4)):
        statut, _ = appeler(f"/rag/export?cursor={curseur}")
        assert statut == 422, f"curseur {curseur!r} → {statut}, attendu 422"


def parcourir_l_export(limit: int = 1) -> tuple[list[str], int, float]:
    """Parcourt `/rag/export` par curseur jusqu'à épuisement.

    Renvoie les `doc_key` DANS L'ORDRE DE PARCOURS — et non un ensemble : c'est
    la liste qui permet de distinguer un document manquant d'un document rendu
    deux fois. Un parcours qui boucle est coupé net plutôt que laissé tourner.
    """
    doc_keys: list[str] = []
    cursor: str | None = None
    pages = 0
    depart = time.monotonic()

    while True:
        chemin = f"/rag/export?limit={limit}"
        if cursor is not None:
            chemin += f"&cursor={quote_plus(cursor)}"
        page = get_json(chemin)
        pages += 1
        doc_keys.extend(item["doc_key"] for item in page["items"])

        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert pages < 100_000, "parcours non convergent : le curseur n'avance pas"

    return doc_keys, pages, time.monotonic() - depart


def verifier_l_exhaustivite_de_l_export() -> None:
    """Contrôle 1, transposé au harnais : rien de perdu, rien de rendu deux fois.

    `limit=1` sur un corpus de 3 documents force un parcours à plusieurs pages
    et exerce la clôture par page vide (3 est un multiple exact de 1) — le seul
    endroit où une erreur de borne « > » / « >= » se verrait.
    """
    doc_keys, pages, _ = parcourir_l_export(limit=1)

    assert len(doc_keys) == len(set(doc_keys)), (
        f"doublons dans le parcours : {doc_keys}"
    )
    assert set(doc_keys) == DOCS_ATTENDUS, f"parcours incomplet : {set(doc_keys)}"
    # 3 documents à 1 par page, plus la page vide qui clôt le parcours.
    assert pages == len(DOCS_ATTENDUS) + 1, (
        f"{pages} pages, attendu {len(DOCS_ATTENDUS) + 1}"
    )
    # L'ordre est celui de `doc_key COLLATE "C"`, croissant.
    assert doc_keys == sorted(doc_keys), doc_keys


def verifier_le_texte_integral() -> None:
    """Contrôle 2 : l'export ne tronque pas, l'aperçu tronque.

    Les deux moitiés comptent. Un export qui rendrait 500 caractères serait un
    aperçu déguisé ; un aperçu qui rendrait tout aurait perdu son objet.
    """
    export = {
        item["doc_key"]: item for item in get_json("/rag/export?limit=200")["items"]
    }

    for doc_key in DOCS_ATTENDUS:
        complet = get_json(f"/rag/doc/{quote_plus(doc_key)}")
        assert export[doc_key]["doc_text"] == complet["doc_text"], doc_key

    apercu = {
        item["doc_key"]: item for item in get_json("/rag/preview?limit=200")["items"]
    }
    for doc_key, item in apercu.items():
        assert len(item["preview"]) <= 500, doc_key
        assert item["preview"] == export[doc_key]["doc_text"][:500], doc_key


def verifier_la_composition() -> None:
    """Contrôle 8 : le total et les décomptes par source s'accordent."""
    composition = get_json("/rag/export/composition")

    assert composition["total"] == len(DOCS_ATTENDUS)
    assert composition["total"] == sum(
        entree["documents"] for entree in composition["by_source"]
    )
    assert {
        entree["source"] for entree in composition["by_source"]
    } == SOURCES_ATTENDUES
    assert composition["measured_at"]


def verifier_que_l_export_ignore_offset() -> None:
    """`offset` est retiré : la page renvoyée est la première, pas la seconde."""
    premiere = get_json("/rag/export?limit=1")
    avec_offset = get_json("/rag/export?limit=1&offset=2")

    assert premiere["items"][0]["doc_key"] == avec_offset["items"][0]["doc_key"]


def verifier_formule_de_production(doc_key: str, boost: float) -> None:
    """Test discriminant : échoue si la formule 3/2/1 était rétablie.

    Le harnais teste le schéma reconstruit depuis `database/migrations/`. Si
    quelqu'un réintroduisait le DDL du module et sa pondération 3/2/1, la vue
    `rag_docs_scored` renverrait 4.25 au lieu de 120.0 pour ce document, et
    cette fonction serait le premier point à céder.
    """
    assert boost != BOOST_FORMULE_ABANDONNEE, (
        f"{doc_key} a un boost de {boost} : c'est la formule 3/2/1 abandonnée. "
        "La base testée n'est pas au schéma de production."
    )
    assert boost == BOOST_ATTENDU, (
        f"{doc_key} a un boost de {boost}, attendu {BOOST_ATTENDU} "
        "(100/1 + 30/2 + 20/4)."
    )


def main() -> None:
    verifier_le_controle_d_acces()
    verifier_les_curseurs_illisibles()

    assert get_json("/live") == {"status": "ok", "db": "not_checked"}
    assert get_json("/health") == {"status": "ok", "db": "ok"}

    # /kitsu/{id} lit manga.kitsu_series_core, peuplée d'une seule série.
    kitsu = get_json("/kitsu/38")
    assert kitsu["title_canonical"] == "One Piece"
    assert kitsu["slug"] == "one-piece"
    assert kitsu["rating_rank"] == 2

    # /rag/preview : les 3 documents de la fixture, un par source. Ce bloc
    # teste l'APERÇU — c'est lui qui a hérité du tri par pertinence et de la
    # troncature de l'ancien `/rag/export`.
    export = get_json("/rag/preview?limit=10&offset=0")
    assert export["total"] == len(DOCS_ATTENDUS)
    assert {item["doc_key"] for item in export["items"]} == DOCS_ATTENDUS
    assert {item["source"] for item in export["items"]} == SOURCES_ATTENDUES

    # Tri de l'aperçu : `boost_score DESC NULLS LAST, doc_key`. Les deux
    # documents porteurs des signaux hebdomadaires valent 120.0 ; l'égalité est
    # tranchée par doc_key, et 'kitsu:38' précède 'ms_hybrid:736'. La critique
    # n'a aucune position dans ses métadonnées, donc un boost nul, et ferme la
    # liste.
    assert [item["doc_key"] for item in export["items"]] == [
        "kitsu:38",
        "ms_hybrid:736",
        "ms_review:1",
    ]
    boosts = {item["doc_key"]: item["boost_score"] for item in export["items"]}
    verifier_formule_de_production("kitsu:38", boosts["kitsu:38"])
    verifier_formule_de_production("ms_hybrid:736", boosts["ms_hybrid:736"])
    assert boosts["ms_review:1"] == 0.0

    # /rag/doc : texte complet et métadonnées. Le document hybride est celui
    # qui prouve le plus : il n'existe que si ms_kitsu_map, rag_kitsu_docs et
    # rag_reviews_docs sont toutes les trois lues par la chaîne de vues.
    hybride = get_json(f"/rag/doc/{quote_plus('ms_hybrid:736')}")
    assert hybride["source"] == "ms_hybrid"
    assert hybride["metadata"]["match_method"] == "exact"
    assert hybride["metadata"]["series_id"] == 736
    assert hybride["metadata"]["kitsu_id"] == 38
    assert hybride["metadata"]["trending_pos"] == 1
    assert "REVIEWS_MS" in hybride["doc_text"]
    assert "SYNOPSIS_KITSU" in hybride["doc_text"]
    verifier_formule_de_production("ms_hybrid:736", hybride["boost_score"])

    critique = get_json(f"/rag/doc/{quote_plus('ms_review:1')}")
    assert critique["source"] == "ms_review"
    assert critique["metadata"]["series_id"] == 736
    assert "abordage" in critique["doc_text"]

    # /search : recherche plein texte sur manga.rag_export_docs.
    # « one piece » figure dans le document Kitsu et, par inclusion du synopsis,
    # dans l'hybride — pas dans la critique. Donc 2 résultats sur 3.
    recherche = get_json(f"/search?q={quote_plus('one piece')}&limit=5")
    assert recherche["query"] == "one piece"
    assert recherche["total"] == 2
    assert {item["doc_key"] for item in recherche["items"]} == {
        "kitsu:38",
        "ms_hybrid:736",
    }

    # « abordage » n'est écrit que dans la critique, que l'hybride recopie.
    # Cette assertion est la seule qui prouve que la source `ms_review` est
    # atteignable par la recherche, et non seulement par l'export.
    abordage = get_json(f"/search?q={quote_plus('abordage')}&limit=5")
    assert abordage["total"] == 2
    assert {item["doc_key"] for item in abordage["items"]} == {
        "ms_review:1",
        "ms_hybrid:736",
    }

    verifier_l_exhaustivite_de_l_export()
    verifier_le_texte_integral()
    verifier_la_composition()
    verifier_que_l_export_ignore_offset()
    verifier_le_catalogue()
    verifier_l_identite()
    verifier_la_couverture()


# ---------------------------------------------------------------------------
# 3b
# ---------------------------------------------------------------------------
def verifier_le_catalogue() -> None:
    """La fiche série, ses volumes, et LA BONNE TABLE DE CRITIQUES.

    La fixture pose 3 lignes dans `ms_reviews_all` et 1 seule dans
    `ms_reviews`. Un endpoint qui se tromperait de table renverrait 1 : c'est
    la seule assertion qui distingue les deux, puisque les deux tables portent
    les mêmes colonnes et qu'aucune erreur ne se produirait.
    """
    serie = get_json("/series/736")
    assert serie["series_id"] == 736
    assert serie["title"] == "One Piece"
    assert serie["work_uid"] == 4242

    # Aucune colonne de travail du rapprochement ne doit sortir par HTTP.
    for interdit in ("ms_title_norm_x", "_other_titles_list", "match_score"):
        assert interdit not in serie

    volumes = get_json("/series/736/volumes")
    assert volumes["total"] == 1
    assert volumes["items"][0]["volume_url"].endswith("tome-1.html")

    critiques = get_json("/series/736/reviews")
    assert critiques["total"] == 3, (
        f"{critiques['total']} critiques : l'endpoint lit `ms_reviews` "
        "(corpus RAG hérité) au lieu de `ms_reviews_all` (le référentiel)."
    )
    assert len(critiques["items"]) == 3

    # La date non analysable de la fixture (« jeu. ») reste NULL sans faire
    # échouer la ligne : `date_raw` conserve ce que la source affichait.
    par_titre = {item["title"]: item for item in critiques["items"]}
    assert par_titre["Le souffle tient"]["date"] is None
    assert par_titre["Le souffle tient"]["date_raw"] == "jeu."

    # Une série qui EXISTE mais n'a rien : 200 et une liste vide, jamais 404.
    vide = get_json("/series/999/reviews")
    assert vide["total"] == 0 and vide["items"] == []

    # Une série qui n'existe pas : 404. La distinction d'avec le cas précédent
    # est tout l'intérêt des deux assertions.
    assert statut_http("/series/99999999") == 404
    assert statut_http("/series/99999999/reviews") == 404


def verifier_l_identite() -> None:
    """Les identifiants croisés ET la provenance du lien.

    `v_match_current` est indexée par `series_id` : la route doit joindre
    `work_identity` pour répondre sur un `work_uid`.
    """
    identite = get_json("/identity/4242")
    assert identite["work_uid"] == 4242
    assert identite["series_id"] == 736
    assert identite["wikidata_qid"] == "Q173065"
    assert identite["kitsu_id"] == "38"
    assert identite["method"] == "exact"
    assert identite["status"] == "auto"

    assert statut_http("/identity/99999999") == 404


def verifier_la_couverture() -> None:
    """Chaque total se dérive de la fixture, pas d'un chiffre de production."""
    couverture = get_json("/coverage")
    totaux = couverture["totals"]

    # 736 et 999 posées par la fixture.
    assert totaux["series"] == 2
    assert totaux["volumes"] == 1
    # 3 dans le référentiel contre 1 dans le corpus hérité : l'écart est le
    # sujet même de 3b, et il doit être LISIBLE dans la réponse.
    assert totaux["reviews"] == 3
    assert totaux["reviews_rag_legacy"] == 1
    assert totaux["reviews"] > totaux["reviews_rag_legacy"]

    assert couverture["identity_by_method"] == [{"method": "exact", "decisions": 1}]
    assert "measured_at" in couverture


if __name__ == "__main__":
    main()
