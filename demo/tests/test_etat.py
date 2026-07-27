"""Écran d'état : exact quand la base répond, dégradé proprement sinon.

Aucun test ne touche `apimanga` : on vérifie le comportement sans DSN et avec
un DSN volontairement injoignable. Un écran d'état qui lève une exception en
pleine soutenance est un défaut plus grave qu'un chiffre manquant.
"""

from __future__ import annotations

import re

import pytest

from manga_pipeline import etat


@pytest.fixture
def sans_dsn(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def dsn_injoignable(monkeypatch):
    # Port fermé : psycopg échoue vite, le collecteur doit encaisser.
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://personne@127.0.0.1:1/base_inexistante"
    )


def test_collecte_complete_sans_dsn(sans_dsn):
    blocs = etat.collecter()
    assert len(blocs) == 5
    migrations = blocs[0]
    assert migrations.titre == "Migrations"
    # Le comptage des fichiers de migration ne dépend pas de la base.
    assert any(libelle == "dans le dépôt" for libelle, _, _ in migrations.lignes)
    assert "DATABASE_URL" in migrations.note


def test_collecte_ne_leve_jamais_avec_dsn_injoignable(dsn_injoignable):
    blocs = etat.collecter()
    assert len(blocs) == 5
    base = next(b for b in blocs if b.titre == "Base apimanga")
    assert base.note, "une base injoignable doit produire une note, pas une exception"


def test_bloc_raw_voit_les_snapshots():
    bloc = etat.bloc_raw()
    assert bloc.lignes, "les snapshots du raw doivent être listés"
    noms = {libelle for libelle, _, _ in bloc.lignes}
    assert {"2025-12", "2026-07"} <= noms


def test_bloc_lakehouse_liste_les_tables_gold():
    bloc = etat.bloc_lakehouse()
    libelles = {libelle for libelle, _, _ in bloc.lignes}
    assert "tables gold" in libelles


def test_bloc_git_donne_le_dernier_commit():
    bloc = etat.bloc_git()
    assert bloc.lignes or bloc.note


def test_la_requete_des_critiques_vise_le_referentiel():
    """Garde-fou du correctif : `ms_reviews` est la table HÉRITAGE du corpus
    RAG (3 187 docs). L'écran d'état doit compter `ms_reviews_all` (11 074),
    sans quoi il annonce un effondrement des critiques qui n'existe pas."""
    sql = dict(etat._REQUETES_VOLUMETRIE)["critiques (référentiel)"]
    assert re.search(r"\bmanga\.ms_reviews_all\b", sql)
    # Aucune référence à la table héritage (ms_reviews non suivi de _all).
    assert not re.search(r"\bmanga\.ms_reviews(?!_all)\b", sql)


@pytest.mark.parametrize("libelle,attendu", sorted(etat.COMPTES_REFERENCE.items()))
def test_les_comptes_affiches_valent_la_reference(libelle, attendu):
    """Les chiffres montrés au jury sont confrontés aux valeurs de contrôle.

    Test d'intégration : ignoré sans DATABASE_URL (CI sans base). Il ne fait
    que des SELECT count(*) — aucune écriture sur la base interrogée.
    """
    if not etat.dsn():
        pytest.skip("DATABASE_URL non définie : contrôle d'attendu non applicable")
    bloc = etat.bloc_base()
    if bloc.note:
        pytest.skip(f"base injoignable : {bloc.note}")
    valeurs = {lib: val for lib, val, _ in bloc.lignes}
    obtenu = int(valeurs[libelle].replace(" ", "").split("(")[0])
    assert obtenu == attendu, (
        f"{libelle} : {obtenu} affiché, {attendu} attendu. "
        "Soit la base a bougé (mettre à jour COMPTES_REFERENCE avec la raison), "
        "soit l'écran interroge la mauvaise table."
    )


def test_le_journal_des_decisions_ne_decroit_pas():
    """La cascade est append-only : le compte ne peut que monter."""
    if not etat.dsn():
        pytest.skip("DATABASE_URL non définie")
    bloc = etat.bloc_base()
    if bloc.note:
        pytest.skip(f"base injoignable : {bloc.note}")
    valeurs = {lib: val for lib, val, _ in bloc.lignes}
    obtenu = int(valeurs["décisions journalisées"].replace(" ", ""))
    assert obtenu >= etat.DECISIONS_MINIMUM, (
        f"{obtenu} décisions : le journal a DÉCRU sous le plancher "
        f"{etat.DECISIONS_MINIMUM}. Une décision a été supprimée — "
        "c'est une violation de l'append-only."
    )


def test_les_requetes_de_volumetrie_sont_en_lecture_seule():
    """Garde-fou : aucune requête de l'écran d'état ne peut modifier la base."""
    interdits = ("insert", "update", "delete", "truncate", "drop", "alter", "create")
    for _libelle, sql in etat._REQUETES_VOLUMETRIE:
        minuscule = sql.lower()
        assert minuscule.strip().startswith("select")
        assert not any(mot in minuscule for mot in interdits)
