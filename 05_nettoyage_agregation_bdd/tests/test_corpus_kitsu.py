"""La part Kitsu : gabarit de décembre, nettoyage, auteurs, sélection K1, manifeste.

Le gabarit est éprouvé sur un document RÉEL de décembre (`kitsu:4`, *Monster*),
recopié tel que `rag_kitsu_docs` le portait : le rafraîchissement change la
source, pas la forme.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import ecrire_run_kitsu

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from corpus import kitsu  # noqa: E402


def test_gabarit_identique_a_decembre():
    decembre = (
        "Titres: Monster | Monster | MONSTER\n"
        "Auteurs: Naoki Urasawa (Scénario & Dessin)\n"
        'Tags: ["Drama", "Mystery", "Psychological", "Seinen", "Thriller"]\n'
        "Synopsis: Dr. Kenzo Tenma is a renowned young brain surgeon."
    )
    assert (
        kitsu.texte(
            "Monster",
            {"en": "Monster", "ja_jp": "MONSTER"},
            [("Naoki Urasawa", "Scénario & Dessin")],
            ["Drama", "Mystery", "Psychological", "Seinen", "Thriller"],
            "Dr. Kenzo Tenma is a renowned young brain surgeon.",
        )
        == decembre
    )


def test_parties_vides_omises():
    assert kitsu.texte("Seul", {}, [], [], "Un synopsis.") == (
        "Titres: Seul\nTags: []\nSynopsis: Un synopsis."
    )


@pytest.mark.parametrize(
    ("titres", "attendu"),
    [
        ({"en": "A", "en_us": "B", "en_jp": "C"}, "A"),
        ({"en_us": "B", "en_jp": "C"}, "B"),
        ({"en_jp": "C"}, "C"),
        ({"en": "", "en_jp": "C"}, "C"),
        ({}, None),
    ],
)
def test_titre_anglais(titres, attendu):
    assert kitsu.titre_anglais(titres) == attendu


@pytest.mark.parametrize(
    ("brut", "texte", "sources"),
    [
        ("Un récit. (Source: MU)", "Un récit.", ["MU"]),
        ("Un récit.\n\nSource: MangaDex", "Un récit.", ["MangaDex"]),
        ("Un récit. [Source: ANN] Suite.", "Un récit. Suite.", ["ANN"]),
        ("Un (Source: MU) récit (source : MU).", "Un récit.", ["MU"]),
        ("Sans mention.", "Sans mention.", []),
        ("(Source: MU)", "", ["MU"]),
    ],
)
def test_nettoyer(brut, texte, sources):
    assert kitsu.nettoyer(brut) == (texte, sources)


def test_auteurs_roles_traduits_tries_et_filtres(tmp_path):
    run = ecrire_run_kitsu(
        tmp_path,
        [],
        {
            1: [
                ("Tsugumi Ohba", "Story"),
                ("Takeshi Obata", "Art"),
                ("Un Traducteur", "Translation"),
                ("Takeshi Obata", "Art"),
            ]
        },
    )
    assert kitsu.lire_staff(run / "relations/staff.ndjson") == {
        1: [("Takeshi Obata", "Dessin"), ("Tsugumi Ohba", "Scénario")]
    }


OEUVRES = [
    (1, "manga", "Un", {}, "Un synopsis assez long pour le corpus.", ["Action"], []),
    (2, "manhwa", "Deux", {}, "(Source: MU)", [], []),
    (3, "novel", "Trois", {}, "Un roman au synopsis.", [], []),
    (4, "novel", "Quatre", {}, "Un roman rattaché au catalogue.", [], []),
    (5, "doujin", "Cinq", {}, "Un doujin au synopsis.", [], []),
    (6, "oneshot", "Six", {}, "Un oneshot au synopsis.", [], []),
]


def test_selection_k1(tmp_path):
    run = ecrire_run_kitsu(tmp_path, OEUVRES)
    docs, b = kitsu.documents(run, rattachees={4, 5})
    assert [d["doc_key"] for d in docs] == ["kitsu:1", "kitsu:4"]
    assert b.oeuvres == 6 and b.retenues == 2
    assert b.exclues_sans_synopsis == 1  # 2 : sa seule phrase était la source
    assert dict(b.sur_rattachement_admises) == {"novel": 1}
    assert dict(b.exclues_sous_type) == {"novel": 1, "doujin": 1, "oneshot": 1}
    assert b.rattachees_par_sous_type == {"novel": 1, "doujin": 1}
    meta = json.loads(docs[0]["metadata_json"])
    assert meta == {
        "kitsu_id": 1,
        "popularity_rank": 10,
        "rating_rank": 20,
        "subtype": "manga",
        "tags": ["Action"],
        "title": "Un",
    }


def test_manifeste_verifie(tmp_path):
    run = ecrire_run_kitsu(tmp_path, OEUVRES)
    (run / "relations/staff.ndjson").write_text("{}\n", encoding="utf-8")
    with pytest.raises(kitsu.ErreurKitsu, match="staff.ndjson : sha256"):
        kitsu.documents(run, set())
    (run / "manifest.json").write_text(json.dumps({"files": []}), encoding="utf-8")
    with pytest.raises(kitsu.ErreurKitsu, match="n'est pas déclaré"):
        kitsu.verifier_manifeste(run)
