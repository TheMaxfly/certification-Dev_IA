"""Le découpage par phrases du corpus v2 (règle validée le 2026-10-09)."""

from __future__ import annotations

import random
import sys
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from corpus import phrases  # noqa: E402

R = phrases.charger_reglages()


def textes_des_segments(texte: str) -> list[str]:
    return [texte[a:b] for a, b in phrases.segments(texte, R.abreviations)]


def verifier_invariants(texte: str, reglages=R):
    fragments, bilan = phrases.decouper(texte, reglages)
    propre = texte.strip()
    assert phrases.reconstituer(texte, fragments) == propre, "le texte se reconstitue"
    for f in fragments:
        assert f.texte.strip(), "aucun fragment vide"
        assert f.texte == propre[f.debut : f.fin]
        assert len(f.texte) <= reglages.taille
        assert f.debut <= f.nouveau < f.fin
        assert not phrases.dans_un_mot(propre, f.debut), "aucune coupe dans un mot"
        assert not phrases.dans_un_mot(propre, f.fin), "aucune coupe dans un mot"
    return fragments, bilan


# --------------------------------------------------------------------------- #
#  Les réglages
# --------------------------------------------------------------------------- #


def test_les_reglages_valides_par_max():
    assert (R.nom, R.taille, R.recouvrement) == (
        "phrases_1200_recouvrement_200",
        1200,
        200,
    )
    assert {"m", "etc", "vol", "t", "e.g"} <= R.abreviations
    assert len(R.empreinte) == 64


def test_les_reglages_sont_lus_dans_la_configuration(tmp_path):
    texte = phrases.FICHIER_REGLAGES.read_text(encoding="utf-8")
    autre = tmp_path / "decoupage.toml"
    autre.write_text(
        texte.replace("taille = 1200", "taille = 40").replace(
            "recouvrement = 200", "recouvrement = 10"
        ),
        encoding="utf-8",
    )
    r = phrases.charger_reglages(autre)
    assert (r.taille, r.recouvrement) == (40, 10) and r.empreinte != R.empreinte
    fragments, _ = verifier_invariants("Une phrase assez longue. " * 6, r)
    assert len(fragments) > 1


# --------------------------------------------------------------------------- #
#  Les phrases
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "texte, attendu",
    [
        (
            "M. Dupont lit le vol. 3 du manga. Puis il dort.",
            ["M. Dupont lit le vol. 3 du manga. ", "Puis il dort."],
        ),
        (
            "J. K. Rowling écrit. Le T. 12 sort, etc. Fin",
            ["J. K. Rowling écrit. ", "Le T. 12 sort, etc. Fin"],
        ),
        ("cf. p. 45 et e.g. ceci. Puis", ["cf. p. 45 et e.g. ceci. ", "Puis"]),
    ],
)
def test_abreviations_et_initiales_ne_coupent_pas(texte, attendu):
    assert textes_des_segments(texte) == attendu


def test_ponctuation_japonaise():
    assert textes_des_segments("東京へ行く。それから！どこ？「はい」。") == [
        "東京へ行く。",
        "それから！",
        "どこ？",
        "「はい」。",
    ]


@pytest.mark.parametrize(
    "texte, attendu",
    [
        ("Il attend… Rien ne vient.", ["Il attend… ", "Rien ne vient."]),
        ("Il attend... Rien ne vient.", ["Il attend... ", "Rien ne vient."]),
        ("Il hésite... puis repart.", ["Il hésite... puis repart."]),
        ("Quoi ?! Non.", ["Quoi ?! ", "Non."]),
    ],
)
def test_points_de_suspension_et_suites(texte, attendu):
    assert textes_des_segments(texte) == attendu


@pytest.mark.parametrize(
    "texte, attendu",
    [
        (
            "« Pourquoi ? » demanda-t-il. Elle se tait.",
            ["« Pourquoi ? » demanda-t-il. ", "Elle se tait."],
        ),
        (
            "Il dit : « Je pars. » Elle répond.",
            ["Il dit : « Je pars. » ", "Elle répond."],
        ),
        ('He said "Go." Then left.', ['He said "Go." ', "Then left."]),
        ("Il dit. « Bonjour » répond-elle.", ["Il dit. ", "« Bonjour » répond-elle."]),
    ],
)
def test_guillemets(texte, attendu):
    assert textes_des_segments(texte) == attendu


def test_le_saut_de_ligne_finit_toujours_une_phrase():
    texte = "Résumé Manga Titre\nIl part. Elle reste"
    assert textes_des_segments(texte) == [
        "Résumé Manga Titre\n",
        "Il part. ",
        "Elle reste",
    ]


def test_les_segments_partitionnent_le_texte():
    texte = "Un. Deux ! Trois ?\nQuatre… cinq. Six"
    assert "".join(textes_des_segments(texte)) == texte


# --------------------------------------------------------------------------- #
#  Les fragments
# --------------------------------------------------------------------------- #


def test_un_texte_court_reste_un_seul_fragment():
    fragments, _ = verifier_invariants("  Une critique courte. Elle tient.  ")
    assert [f.texte for f in fragments] == ["Une critique courte. Elle tient."]


def test_texte_vide_ou_absent():
    assert phrases.decouper("", R)[0] == [] and phrases.decouper(None, R)[0] == []


def test_recouvrement_par_la_derniere_phrase_si_elle_tient():
    r = replace(R, taille=60, recouvrement=20)
    texte = (
        "Une première phrase plutôt longue ici. Courte. Une troisième phrase arrive."
    )
    fragments, bilan = verifier_invariants(texte, r)
    assert [f.texte for f in fragments] == [
        "Une première phrase plutôt longue ici. Courte.",
        "Courte. Une troisième phrase arrive.",
    ]
    assert fragments[1].nouveau > fragments[1].debut and bilan["recouvrement"] == 1


def test_pas_de_recouvrement_si_la_derniere_phrase_ne_tient_pas():
    r = replace(R, taille=60, recouvrement=10)
    texte = "Une première phrase plutôt longue ici. Courte mais pas assez. Fin."
    fragments, bilan = verifier_invariants(texte, r)
    assert all(f.nouveau == f.debut for f in fragments)
    assert bilan["sans_recouvrement_phrase_trop_longue"] >= 1


def test_phrase_plus_longue_que_le_maximum_coupee_a_la_ponctuation():
    r = replace(R, taille=50, recouvrement=10)
    texte = (
        "Un long passage qui continue, encore et encore ; sans jamais finir vraiment là"
    )
    fragments, bilan = verifier_invariants(texte, r)
    assert bilan["phrases_coupees"] == 1 and bilan["coupe_ponctuation"] >= 1
    assert fragments[0].texte.endswith((",", ";"))


def test_phrase_sans_ponctuation_coupee_a_une_espace():
    r = replace(R, taille=30, recouvrement=5)
    fragments, bilan = verifier_invariants("mot " * 30, r)
    assert bilan["coupe_espace"] >= 1 and bilan["coupe_milieu_de_mot"] == 0


def test_aucune_coupe_dans_un_mot_sur_des_textes_tires_au_hasard():
    tirage = random.Random(20261009)
    mots = [
        "manga",
        "héros,",
        "aventure.",
        "Puis",
        "« Quoi ? »",
        "vol.",
        "T.",
        "…",
        "東京。",
        "l'équipage",
        "pirates;",
        "Fin!",
        "\n",
        "J.",
        "K.",
        "etc.",
    ]
    r = replace(R, taille=80, recouvrement=25)
    for _ in range(300):
        texte = " ".join(tirage.choice(mots) for _ in range(tirage.randint(1, 120)))
        verifier_invariants(texte, r)


def test_identique_d_une_execution_a_l_autre():
    texte = "Une phrase. " * 300
    a, _ = phrases.decouper(texte, R)
    b, _ = phrases.decouper(texte, R)
    assert a == b and len(a) > 1


# --------------------------------------------------------------------------- #
#  La stratégie nommée
# --------------------------------------------------------------------------- #


def test_strategie_inscrite_une_fois_et_jamais_redefinie(base):
    with psycopg.connect(base) as cx:
        a = phrases.enregistrer_strategie(cx, R)
        assert phrases.enregistrer_strategie(cx, R) == a, "rejeu : la même ligne"
        assert cx.execute(
            "SELECT name, chunk_size, chunk_overlap, notes"
            " FROM bench.chunking_strategies WHERE chunking_id = %s",
            (a,),
        ).fetchone() == (R.nom, 1200, 200, phrases.notes(R))
        with pytest.raises(ValueError, match="autre nom"):
            phrases.enregistrer_strategie(cx, replace(R, recouvrement=150))
