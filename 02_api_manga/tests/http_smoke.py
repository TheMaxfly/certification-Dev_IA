"""Smoke test HTTP exécuté dans le Compose d'intégration.

La base visée est jetable, son schéma reconstruit par les 12 migrations de
`database/migrations/`, son contenu posé par `tests/fixtures/002_sample_data.sql`.
Chaque attendu chiffré ci-dessous se dérive de cette fixture et de la formule
de boost de PRODUCTION ; la dérivation est écrite en commentaire au-dessus de
l'assertion, jamais laissée au lecteur.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote_plus
from urllib.request import urlopen

BASE_URL = "http://api:8000"

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


def get_json(path: str) -> dict[str, Any]:
    with urlopen(f"{BASE_URL}{path}", timeout=10) as response:  # noqa: S310
        assert response.status == 200, f"{path} → HTTP {response.status}"
        return json.load(response)


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
    assert get_json("/live") == {"status": "ok", "db": "not_checked"}
    assert get_json("/health") == {"status": "ok", "db": "ok"}

    # /kitsu/{id} lit manga.kitsu_series_core, peuplée d'une seule série.
    kitsu = get_json("/kitsu/38")
    assert kitsu["title_canonical"] == "One Piece"
    assert kitsu["slug"] == "one-piece"
    assert kitsu["rating_rank"] == 2

    # /rag/export : les 3 documents de la fixture, un par source.
    export = get_json("/rag/export?limit=10&offset=0")
    assert export["total"] == len(DOCS_ATTENDUS)
    assert {item["doc_key"] for item in export["items"]} == DOCS_ATTENDUS
    assert {item["source"] for item in export["items"]} == SOURCES_ATTENDUES

    # Tri de l'endpoint : `boost_score DESC NULLS LAST, doc_key`. Les deux
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


if __name__ == "__main__":
    main()
