"""Propagation du kitsu_id par identifiant, de bout en bout sur base JETABLE.

Ce que ces tests protègent : une règle qui décide sans lire un titre. Chaque
condition de la règle a son cas, chaque point d'arrêt le sien, et le rejeu
doit laisser le moyeu intact à l'octet près.

Scénario de base :
  1  exact_author, MAL 1 → Kitsu 100 (manga)           → rattachable
  2  llm_review, MAL 2 → Kitsu 200 ET 201              → point A, exclue
  3  exact, MAL 3 → aucune correspondance              → sans chemin
  4  exact_author, AniList 4 → Kitsu 400, sans meta    → hors type
  5  needs_review, MAL 5 → Kitsu 500                   → pas candidate
  6  kitsu_id déjà renseigné (600)                     → pas candidate
  7  exact_author, MAL 7 ET AniList 7 → Kitsu 700      → rattachable (deux voies)
"""

from __future__ import annotations

import psycopg
import pytest

from identity.propagation_kitsu import (
    ErreurPropagation,
    executer,
    verifier_prerequis,
)

CANARIS_TEST = {1: 100}


def _seed(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as cx:
        series = {
            # series_id: (titre, méthode, statut, mal, anilist, qid, kitsu_id)
            1: ("Une", "exact_author", "auto", "1", None, "Q1", None),
            2: ("Deux", "llm_review", "auto", "2", None, "Q2", None),
            3: ("Trois", "exact", "auto", "3", None, "Q3", None),
            4: ("Quatre", "exact_author", "auto", None, "4", "Q4", None),
            5: ("Cinq", "exact", "needs_review", "5", None, None, None),
            6: ("Six", "exact_kitsu", "auto", "6", None, None, "600"),
            7: ("Sept", "exact_author", "auto", "7", "7", "Q7", None),
        }
        for sid, (titre, methode, statut, mal, ani, qid, kid) in series.items():
            cx.execute(
                "INSERT INTO manga.ms_series_enriched (series_id, series_title) "
                "VALUES (%s, %s)",
                (sid, titre),
            )
            cx.execute(
                "INSERT INTO manga.work_identity "
                "(series_id, mal_id, anilist_id, wikidata_qid, kitsu_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (sid, mal, ani, qid, kid),
            )
            cx.execute(
                "INSERT INTO manga.match_decision "
                "(series_id, wikidata_qid, method, status) VALUES (%s, %s, %s, %s)",
                (sid, qid, methode, statut),
            )
        for kid, site, ext in (
            (100, "myanimelist/manga", "1"),
            (200, "myanimelist/manga", "2"),
            (201, "myanimelist/manga", "2"),
            (400, "anilist/manga", "4"),
            (500, "myanimelist/manga", "5"),
            (700, "myanimelist/manga", "7"),
            (700, "anilist/manga", "7"),
        ):
            cx.execute(
                "INSERT INTO manga.kitsu_mappings (kitsu_id, external_site, "
                "external_id) VALUES (%s, %s, %s)",
                (kid, site, ext),
            )
        for kid, sous_type, titre in (
            (100, "manga", "Une"),
            (200, "manga", "Deux"),
            (201, "manhua", "Deux bis"),
            (500, "manga", "Cinq"),
            (600, "manga", "Six"),
            (700, "manhwa", "Autre titre"),
        ):
            cx.execute(
                "INSERT INTO manga.kitsu_meta (kitsu_id, subtype) VALUES (%s, %s)",
                (kid, sous_type),
            )
            cx.execute(
                "INSERT INTO manga.kitsu_formes "
                "(kitsu_id, forme, forme_norm, forme_type, subtype) "
                "VALUES (%s, %s, lower(%s), 'canonical', %s)",
                (kid, titre, titre, sous_type),
            )
        # Un synopsis au corpus pour l'entrée 100 : la série 1 devient atteignable.
        cx.execute(
            "INSERT INTO bench.corpus_docs (doc_key, source, kitsu_id, doc_text) "
            "VALUES ('k100', 'kitsu_synopsis', 100, 'Synopsis')"
        )
        cx.execute(
            "INSERT INTO bench.corpus_chunks (doc_key, chunk_index, chunk_text) "
            "VALUES ('k100', 0, 'Synopsis')"
        )


@pytest.fixture
def base_semee(base) -> str:
    _seed(base)
    return base


def identites(dsn: str) -> dict[int, str | None]:
    with psycopg.connect(dsn) as cx:
        return dict(
            cx.execute(
                "SELECT series_id, kitsu_id FROM manga.work_identity ORDER BY 1"
            ).fetchall()
        )


def test_la_regle_rattache_les_seules_series_qui_la_remplissent(base_semee, tmp_path):
    r, _ = executer(
        base_semee, commit=True, chemin=tmp_path / "r.md", canaris=CANARIS_TEST
    )
    assert r["arret"] is None
    assert r["cas"] == {
        "rattachable": 2,
        "sans_chemin": 1,
        "conflit_plusieurs_entrees": 1,
        "hors_type": 1,
        "conflit_deja_rattachee": 0,
        "conflit_entree_partagee": 0,
    }
    assert identites(base_semee) == {
        1: "100",
        2: None,  # point A : on ne choisit pas entre 200 et 201
        3: None,
        4: None,
        5: None,  # needs_review : pas une décision de la cascade
        6: "600",
        7: "700",  # le titre Kitsu diffère : l'égalité de titre ne décide pas
    }
    assert r["titres"].confirmes == 1  # série 1 ; la série 7 n'a pas de titre égal


def test_le_journal_nomme_l_etage_et_garde_la_provenance(base_semee, tmp_path):
    executer(base_semee, commit=True, chemin=tmp_path / "r.md", canaris=CANARIS_TEST)
    with psycopg.connect(base_semee) as cx:
        courante = cx.execute(
            "SELECT method, status, wikidata_qid, decision_id FROM "
            "manga.v_match_current WHERE series_id = 7"
        ).fetchone()
        details, source = cx.execute(
            "SELECT d.details, s.method FROM manga.match_decision d "
            "JOIN manga.match_decision s "
            "  ON s.decision_id = (d.details->>'decision_source')::bigint "
            "WHERE d.decision_id = %s",
            (courante[3],),
        ).fetchone()
        n_decisions = cx.execute(
            "SELECT count(*) FROM manga.match_decision WHERE series_id = 7"
        ).fetchone()[0]
    assert courante[:3] == ("kitsu_propagation", "auto", "Q7")
    assert source == "exact_author"
    assert details["kitsu_id"] == 700
    assert details["via"] == ["anilist", "mal"]
    assert details["mal_id"] == "7" and details["anilist_id"] == "7"
    assert n_decisions == 2, "append-only : la décision source reste au journal"


def test_le_rejeu_n_ecrit_rien(base_semee, tmp_path):
    premier, _ = executer(
        base_semee, commit=True, chemin=tmp_path / "1.md", canaris=CANARIS_TEST
    )
    second, _ = executer(
        base_semee, commit=True, chemin=tmp_path / "2.md", canaris=CANARIS_TEST
    )
    assert premier["ecrites"] == 2
    assert second["ecrites"] == 0
    assert second["empreinte_avant"] == second["empreinte_apres"]
    assert second["empreinte_avant"] == premier["empreinte_apres"]


def test_a_blanc_rien_n_est_ecrit(base_semee, tmp_path):
    avant = identites(base_semee)
    r, chemin = executer(
        base_semee, commit=False, chemin=tmp_path / "r.md", canaris=CANARIS_TEST
    )
    assert r["ecrites"] == 2, "à blanc joue l'écriture…"
    assert identites(base_semee) == avant, "…puis l'annule"
    assert "à blanc" in chemin.read_text(encoding="utf-8")


def test_l_atteignabilite_est_mesuree_avant_et_apres(base_semee, tmp_path):
    r, _ = executer(
        base_semee, commit=True, chemin=tmp_path / "r.md", canaris=CANARIS_TEST
    )
    assert (r["atteignables_avant"], r["atteignables_apres"]) == (0, 1)


def test_point_b_entree_deja_rattachee_arrete_tout(base_semee, tmp_path):
    """La série 3 déduit l'entrée 600, déjà celle de la série 6."""
    with psycopg.connect(base_semee, autocommit=True) as cx:
        cx.execute(
            "INSERT INTO manga.kitsu_mappings (kitsu_id, external_site, "
            "external_id) VALUES (600, 'myanimelist/manga', '3')"
        )
    avant = identites(base_semee)
    r, chemin = executer(
        base_semee, commit=True, chemin=tmp_path / "r.md", canaris=CANARIS_TEST
    )
    assert r["arret"].startswith("point B")
    assert r["cas"]["conflit_deja_rattachee"] == 1
    assert identites(base_semee) == avant
    assert "ARRÊT" in chemin.read_text(encoding="utf-8")


def test_point_b_entree_deduite_par_deux_series_arrete_tout(base_semee, tmp_path):
    """Les séries 3 et 1 déduisent toutes deux l'entrée 100."""
    with psycopg.connect(base_semee, autocommit=True) as cx:
        cx.execute(
            "INSERT INTO manga.kitsu_mappings (kitsu_id, external_site, "
            "external_id) VALUES (100, 'myanimelist/manga', '3')"
        )
    avant = identites(base_semee)
    r, _ = executer(
        base_semee, commit=True, chemin=tmp_path / "r.md", canaris=CANARIS_TEST
    )
    assert r["arret"].startswith("point B")
    assert r["cas"]["conflit_entree_partagee"] == 2
    assert identites(base_semee) == avant


def test_point_c_un_canari_non_rattache_arrete_avant_d_ecrire(base_semee, tmp_path):
    avant = identites(base_semee)
    r, _ = executer(base_semee, commit=True, chemin=tmp_path / "r.md", canaris={3: 300})
    assert r["arret"].startswith("point C")
    assert r["ecrites"] == 0
    assert identites(base_semee) == avant


def test_un_canari_deja_rattache_passe(base_semee, tmp_path):
    """Au rejeu, le canari a déjà son entrée : ce n'est pas un échec."""
    r, _ = executer(base_semee, commit=True, chemin=tmp_path / "r.md", canaris={6: 600})
    assert r["arret"] is None


def test_sans_la_migration_018_l_etage_refuse(base, tmp_path):
    with psycopg.connect(base, autocommit=True) as cx:
        cx.execute(
            "ALTER TABLE manga.match_decision "
            "DROP CONSTRAINT match_decision_method_check"
        )
        cx.execute(
            "ALTER TABLE manga.match_decision ADD CONSTRAINT "
            "match_decision_method_check CHECK (method IN ('exact'))"
        )
        with cx.cursor() as cur, pytest.raises(ErreurPropagation, match="018"):
            verifier_prerequis(cur)
