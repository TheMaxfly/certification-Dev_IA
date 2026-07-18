"""Hydratation des noms d'auteurs (D0-1), sans réseau.

Le brut wbgetentities est fabriqué ici : `charger` ne lit que des fichiers, si
bien que toute la chaîne de décision (choix du nom, normalisation, formes) est
exerçable hors réseau. Seul `extraire` appelle Wikidata, et il n'est pas testé
ici — c'est de l'I/O, pas de la logique.

Les cas couverts sont ceux qui font le choix du nom : priorité ja > en > fr,
repli sur une langue non prévue, auteur sans aucun label, et le fait qu'un
auteur porté par DEUX œuvres soit nommé sur ses deux lignes.
"""

from __future__ import annotations

import json

import psycopg
import pytest
from conftest import lire


def _ecrire_brut(dossier, entites: dict) -> None:
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "auteurs_000000.json").write_text(
        json.dumps({"entities": entites}, ensure_ascii=False), encoding="utf-8"
    )


def _labels(**par_langue) -> dict:
    return {
        langue: {"language": langue, "value": valeur}
        for langue, valeur in par_langue.items()
    }


# --------------------------------------------------------------------------- #
# Fonctions pures : le choix du nom
# --------------------------------------------------------------------------- #
def test_le_natif_prime_sur_en_et_fr():
    from identity.hydrater_auteurs import choisir_nom

    labels = _labels(ja="荒木飛呂彦", en="Hirohiko Araki", fr="Hirohiko Araki")
    assert choisir_nom(labels) == ("荒木飛呂彦", "ja")


def test_en_choisi_faute_de_ja():
    from identity.hydrater_auteurs import choisir_nom

    assert choisir_nom(_labels(en="Jane Doe", fr="Jane Doe")) == ("Jane Doe", "en")


def test_fr_choisi_en_dernier_recours_des_trois():
    from identity.hydrater_auteurs import choisir_nom

    assert choisir_nom(_labels(fr="Jean Dupont")) == ("Jean Dupont", "fr")


def test_repli_sur_une_langue_non_prevue():
    from identity.hydrater_auteurs import choisir_nom

    nom, langue = choisir_nom(_labels(ko="김철수"))
    assert (nom, langue) == ("김철수", "ko")


def test_aucun_label_ne_donne_aucun_nom():
    from identity.hydrater_auteurs import choisir_nom

    assert choisir_nom({}) == (None, None)


def test_les_formes_couvrent_labels_et_alias():
    from identity.hydrater_auteurs import formes_d_un_auteur

    entite = {
        "labels": _labels(ja="荒木飛呂彦", en="Hirohiko Araki"),
        "aliases": {"en": [{"language": "en", "value": "Araki Hirohiko"}]},
    }
    formes = formes_d_un_auteur(entite)
    assert ("荒木飛呂彦", "label", "ja") in formes
    assert ("Hirohiko Araki", "label", "en") in formes
    assert ("Araki Hirohiko", "alias", "en") in formes


# --------------------------------------------------------------------------- #
# Chargement en base
# --------------------------------------------------------------------------- #
@pytest.fixture
def hydrate(base, tmp_path):
    """Sème deux œuvres et trois auteurs, puis charge un brut fabriqué."""
    with psycopg.connect(base, autocommit=True) as cx:
        for qid in ("Q900", "Q901"):
            cx.execute("INSERT INTO manga.wd_pivot (qid) VALUES (%s)", (qid,))
        # A1 porte les DEUX œuvres : une seule entité, deux lignes à nommer.
        for qid, auteur in (
            ("Q900", "QA1"),
            ("Q901", "QA1"),
            ("Q900", "QA2"),
            ("Q901", "QA3"),
        ):
            cx.execute(
                "INSERT INTO manga.wd_auteurs (qid, auteur_qid) VALUES (%s, %s)",
                (qid, auteur),
            )

    dossier = tmp_path / "auteurs"
    _ecrire_brut(
        dossier,
        {
            "QA1": {
                "id": "QA1",
                "labels": _labels(ja="荒木飛呂彦", en="Hirohiko Araki"),
                "aliases": {"en": [{"language": "en", "value": "Araki Hirohiko"}]},
            },
            "QA2": {"id": "QA2", "labels": _labels(en="Jane Doe")},
            # sans aucun label : reste NULL, jamais un nom inventé
            "QA3": {"id": "QA3", "labels": {}},
        },
    )
    from identity import hydrater_auteurs

    hydrater_auteurs.charger(source=dossier)
    return base, dossier


def test_le_nom_natif_est_retenu_et_sa_langue_stockee(hydrate):
    dsn, _ = hydrate
    lignes = lire(
        dsn,
        "SELECT DISTINCT auteur, auteur_lang FROM manga.wd_auteurs "
        "WHERE auteur_qid='QA1'",
    )
    assert lignes == [("荒木飛呂彦", "ja")]


def test_un_auteur_de_deux_oeuvres_est_nomme_sur_ses_deux_lignes(hydrate):
    dsn, _ = hydrate
    lignes = lire(
        dsn,
        "SELECT qid, auteur FROM manga.wd_auteurs WHERE auteur_qid='QA1' ORDER BY qid",
    )
    assert lignes == [("Q900", "荒木飛呂彦"), ("Q901", "荒木飛呂彦")]


def test_auteur_sans_label_reste_null(hydrate):
    dsn, _ = hydrate
    ligne = lire(
        dsn,
        "SELECT auteur, auteur_norm, auteur_lang FROM manga.wd_auteurs "
        "WHERE auteur_qid='QA3'",
    )[0]
    assert ligne == (None, None, None)


def test_auteur_norm_vient_de_normaliser(hydrate):
    dsn, _ = hydrate
    from identity.wikidata_dump import normaliser

    ligne = lire(
        dsn,
        "SELECT auteur, auteur_norm FROM manga.wd_auteurs WHERE auteur_qid='QA2'",
    )[0]
    assert ligne == ("Jane Doe", normaliser("Jane Doe"))


def test_les_formes_gardent_natif_et_romanisation(hydrate):
    dsn, _ = hydrate
    formes = lire(
        dsn,
        "SELECT forme, forme_type, langue FROM manga.wd_auteurs_formes "
        "WHERE auteur_qid='QA1' ORDER BY forme",
    )
    valeurs = {f for f, _, _ in formes}
    assert "荒木飛呂彦" in valeurs and "Hirohiko Araki" in valeurs
    assert "Araki Hirohiko" in valeurs
    assert ("Araki Hirohiko", "alias", "en") in formes


def test_rejeu_ne_change_rien(hydrate):
    dsn, dossier = hydrate
    from identity import hydrater_auteurs

    avant = lire(
        dsn,
        "SELECT (SELECT count(auteur) FROM manga.wd_auteurs), "
        "(SELECT count(*) FROM manga.wd_auteurs_formes)",
    )[0]
    hydrater_auteurs.charger(source=dossier)
    apres = lire(
        dsn,
        "SELECT (SELECT count(auteur) FROM manga.wd_auteurs), "
        "(SELECT count(*) FROM manga.wd_auteurs_formes)",
    )[0]
    # 3 lignes nommées (QA1 sur deux œuvres, QA2 ; QA3 sans label reste NULL)
    # et 4 formes (QA1 : natif + romanisation + alias ; QA2 : un label).
    assert avant == apres == (3, 4)


def test_brut_absent_refuse(base, tmp_path):
    from identity.hydrater_auteurs import ErreurHydratation, charger

    vide = tmp_path / "vide"
    vide.mkdir()
    with pytest.raises(ErreurHydratation):
        charger(source=vide)
