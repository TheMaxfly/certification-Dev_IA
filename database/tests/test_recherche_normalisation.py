"""Migration 028 — la normalisation de la recherche, ses index trigrammes (E4, lot 2b).

`manga.normaliser` rend la forme comparée (minuscules, sans accent), immuable de bonne
foi (le dictionnaire nommé, indépendant du `search_path`) ; les quatre index GIN de
trigrammes portent ses valeurs, et une recherche « contient » sur le titre normalisé
les emploie (`EXPLAIN`, le parcours séquentiel interdit pour la session : sur quelques
lignes, le planificateur le préférerait — on éprouve que l'index sert, pas son coût).
"""

from __future__ import annotations

import json

import psycopg
import pytest
from conftest import harnais, migrate

FICHIER = migrate.MIGRATIONS_DIR / "028_recherche_normalisation.sql"
INDEX = {
    f"ms_series_enriched_norm_{colonne}_trgm_idx": expression
    for colonne, expression in (
        ("series_title", "series_title"),
        ("series_other_titles", "(series_other_titles)::text"),
        ("series_scenariste", "series_scenariste"),
        ("series_dessinateur", "series_dessinateur"),
    )
}


def test_028_ne_touche_ni_colonne_ni_donnee_ni_droit():
    code = "\n".join(
        ligne
        for ligne in FICHIER.read_text(encoding="utf-8").splitlines()
        if ligne.strip() and not ligne.lstrip().startswith("--")
    ).upper()

    for interdit in ("ALTER TABLE", "INSERT", "UPDATE", "DELETE", "DROP", "GRANT"):
        assert interdit not in code, interdit
    assert code.count("CREATE INDEX") == 4
    assert code.count("CREATE FUNCTION") == 1


def test_028_passe_sur_une_base_vide(conteneur_postgres):
    with harnais.base_vide(conteneur_postgres) as dsn:
        harnais.migrer(dsn)
        with psycopg.connect(dsn) as cx:
            (fonction,) = cx.execute(
                "SELECT to_regprocedure('manga.normaliser(text)')::text"
            ).fetchone()

    assert fonction == "manga.normaliser(text)"


@pytest.mark.parametrize(
    ("saisie", "attendu"),
    [
        ("Shônen", "shonen"),
        ("Pīsu", "pisu"),
        ("ÉTÉ", "ete"),
        ("Cœur", "coeur"),
        ("déjà VU", "deja vu"),
        (None, None),
    ],
)
def test_normaliser_minuscules_sans_accent(base_migree, saisie, attendu):
    with psycopg.connect(base_migree) as cx:
        (lu,) = cx.execute("SELECT manga.normaliser(%s)", (saisie,)).fetchone()

    assert lu == attendu


def test_normaliser_est_immuable_et_independante_du_chemin(base_migree):
    with psycopg.connect(base_migree) as cx:
        (volatilite, parallele, stricte, commentaire) = cx.execute(
            "SELECT provolatile, proparallel, proisstrict,"
            "       obj_description(oid, 'pg_proc')"
            "  FROM pg_proc WHERE oid = 'manga.normaliser(text)'::regprocedure"
        ).fetchone()
        cx.execute("SET search_path = pg_catalog")
        (lu,) = cx.execute("SELECT manga.normaliser('Shôjo')").fetchone()

    assert (volatilite, parallele, stricte) == ("i", "s", True)
    assert "IMMUTABLE" in commentaire and "028" in commentaire
    assert lu == "shojo", "le dictionnaire est nommé : le search_path n'y fait rien"


def test_les_quatre_index_existent_sur_l_expression_normalisee(base_migree):
    with psycopg.connect(base_migree) as cx:
        lus = {
            nom: (definition, commentaire)
            for nom, definition, commentaire in cx.execute(
                "SELECT i.indexname, i.indexdef,"
                "       obj_description(format('manga.%I', i.indexname)::regclass)"
                "  FROM pg_indexes i"
                " WHERE i.schemaname = 'manga'"
                r"   AND i.indexname LIKE 'ms\_series\_enriched\_norm\_%'"
            ).fetchall()
        }

    assert set(lus) == set(INDEX)
    for nom, expression in INDEX.items():
        definition, commentaire = lus[nom]
        assert "USING gin" in definition, nom
        assert f"manga.normaliser({expression})" in definition, nom
        assert "gin_trgm_ops" in definition, nom
        assert commentaire, nom


def plan(cx: psycopg.Connection, requete: str, params: tuple) -> list[dict]:
    """Les nœuds du plan, à plat."""
    (brut,) = cx.execute(f"EXPLAIN (FORMAT JSON) {requete}", params).fetchone()
    racine = (brut if isinstance(brut, list) else json.loads(brut))[0]["Plan"]
    noeuds, pile = [], [racine]
    while pile:
        noeud = pile.pop()
        noeuds.append(noeud)
        pile.extend(noeud.get("Plans", []))
    return noeuds


def test_une_recherche_contient_sur_le_titre_emploie_l_index(base_migree):
    with psycopg.connect(base_migree) as cx:
        cx.execute(
            "INSERT INTO manga.ms_series_enriched (series_id, series_title)"
            " SELECT 900000 + g, 'Série factice ' || g FROM generate_series(1, 200) g"
        )
        cx.execute("ANALYZE manga.ms_series_enriched")
        cx.execute("SET enable_seqscan = off")
        noeuds = plan(
            cx,
            "SELECT series_id FROM manga.ms_series_enriched"
            " WHERE manga.normaliser(series_title) LIKE %s",
            ("%factice 12%",),
        )
        (trouvees,) = cx.execute(
            "SELECT count(*) FROM manga.ms_series_enriched"
            " WHERE manga.normaliser(series_title) LIKE %s",
            ("%serie factice 12%",),
        ).fetchone()
        cx.rollback()

    assert any(
        n["Node Type"] == "Bitmap Index Scan"
        and n.get("Index Name") == "ms_series_enriched_norm_series_title_trgm_idx"
        for n in noeuds
    ), [n["Node Type"] for n in noeuds]
    assert trouvees == 11  # 12, 120 à 129


def test_la_ressemblance_trouve_une_faute_de_frappe(base_migree):
    with psycopg.connect(base_migree) as cx:
        (score_proche, score_loin) = cx.execute(
            "SELECT similarity(manga.normaliser('Naruto'), 'narutp'),"
            "       similarity(manga.normaliser('Berserk'), 'narutp')"
        ).fetchone()
        (ordre_des_noms,) = cx.execute(
            "SELECT word_similarity('urasawa naoki', manga.normaliser('Naoki URASAWA'))"
        ).fetchone()

    assert score_proche > 0.3 > score_loin
    assert ordre_des_noms == pytest.approx(1.0)
