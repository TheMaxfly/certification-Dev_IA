"""Le pont Kitsu → Wikidata (étage 0), de bout en bout, sur base JETABLE.

Un seul jeu de fixtures couvre les huit cas des décisions figées :
  auto concordant · auto à chemin unique · divergence de QID · collision
  d'unicité (deux séries, un QID) · exclusion ambiguë · exclusion needs_review
  · hors pont sans QID · hors pont sans mapping — plus une série hors
  ms_kitsu_map qui ne reçoit que le moyeu.

Les décisions sont vérifiées dans work_identity ET match_decision, jamais l'une
sans l'autre : un remplissage d'identité sans décision journalisée, ou
l'inverse, serait un bug silencieux.
"""

from __future__ import annotations

import psycopg
import pytest
from conftest import lire

# series_id, kitsu_id, mal_id→qid, anilist_id→qid, attendu
# Les qid sont Q1..Q7 ; Q5 est partagé (collision).
SERIES = [
    # auto concordant : mal et anilist mènent au même QID
    (1, 100, ("1000", "Q1"), ("2000", "Q1"), "auto"),
    # auto à chemin unique : seul mal, pas d'anilist
    (2, 200, ("1001", "Q2"), None, "auto"),
    # divergence : mal → Q3, anilist → Q4
    (3, 300, ("1002", "Q3"), ("2002", "Q4"), "needs_review"),
    # collision : deux séries MS matchées au MÊME kitsu_id (450) → même mal →
    # même Q5. wd_pivot étant 1:1 sur qid, c'est la seule forme réelle de
    # collision : un identifiant partagé, pas deux ids vers un même QID.
    (4, 450, ("1003", "Q5"), None, "needs_review"),
    (5, 450, ("1003", "Q5"), None, "needs_review"),
    # exclue car ambiguë (aurait été auto)
    (6, 600, ("1005", "Q6"), None, "exclu"),
    # exclue car needs_review historique
    (7, 700, ("1006", "Q7"), None, "exclu"),
    # hors pont : mapping externe présent mais absent du pivot
    (8, 800, ("1007", None), None, "hors_pont"),
    # hors pont : aucun mapping Kitsu du tout
    (9, 900, None, None, "hors_pont"),
]
# Série 10 : hors ms_kitsu_map — ne reçoit que le moyeu.


def _seed(dsn: str) -> None:
    qids = {"Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"}
    with psycopg.connect(dsn, autocommit=True) as cx:
        for series_id, _kitsu, _mal, _ani, _att in SERIES:
            cx.execute(
                "INSERT INTO manga.ms_series_enriched (series_id, needs_review) "
                "VALUES (%s, %s)",
                (series_id, series_id == 7),
            )
            cx.execute(
                "INSERT INTO manga.ms_kitsu_map (series_id, kitsu_id) VALUES (%s, %s)",
                (series_id, _kitsu),
            )
        # série 10 : présente en catalogue, absente de ms_kitsu_map
        cx.execute("INSERT INTO manga.ms_series_enriched (series_id) VALUES (10)")
        # série 6 ambiguë
        cx.execute("INSERT INTO manga.ms_kitsu_ambiguous (series_id) VALUES (6)")

        # Dédoublonnage : deux séries au même kitsu_id (4 et 5) partagent leurs
        # mappings — un seul jeu par kitsu_id existe côté source.
        mappings: set[tuple[int, str, str]] = set()
        for _sid, kitsu, mal, ani, _att in SERIES:
            if mal is not None:
                mappings.add((kitsu, "myanimelist/manga", mal[0]))
            if ani is not None:
                mappings.add((kitsu, "anilist/manga", ani[0]))
        for kitsu, site, ext in sorted(mappings):
            cx.execute(
                "INSERT INTO manga.kitsu_mappings "
                "(kitsu_id, external_site, external_id) VALUES (%s,%s,%s)",
                (kitsu, site, ext),
            )
        # un mapping mangaupdates parasite : doit être ignoré par le filtre
        cx.execute(
            "INSERT INTO manga.kitsu_mappings "
            "(kitsu_id, external_site, external_id) VALUES (100,'mangaupdates','X')"
        )

        # pivot : chaque (mal_id, qid) et (anilist_id, qid). Regroupé par qid.
        pivot: dict[str, dict[str, str]] = {}
        for _sid, _kitsu, mal, ani, _att in SERIES:
            if mal is not None and mal[1] is not None:
                pivot.setdefault(mal[1], {})["mal_id"] = mal[0]
            if ani is not None and ani[1] is not None:
                pivot.setdefault(ani[1], {})["anilist_id"] = ani[0]
        for qid, ids in pivot.items():
            cx.execute(
                "INSERT INTO manga.wd_pivot (qid, mal_id, anilist_id) "
                "VALUES (%s,%s,%s)",
                (qid, ids.get("mal_id"), ids.get("anilist_id")),
            )
        # un label par qid (pour l'échantillon CSV) ; forme_id est GENERATED
        for qid in sorted(qids):
            cx.execute(
                "INSERT INTO manga.wd_formes "
                "(qid, forme, forme_norm, forme_type) "
                "VALUES (%s,%s,%s,'label')",
                (qid, f"Label {qid}", f"label {qid}".lower()),
            )


@pytest.fixture
def execute(base, tmp_path):
    """Sème, exécute le pont une fois, renvoie (dsn, dossier de rapport)."""
    _seed(base)
    from identity import pont_kitsu

    dossier = tmp_path / "rapport"
    pont_kitsu.construire(dry_run=False, rapport_dir=str(dossier))
    return base, dossier


def _un(dsn, sql, params=None):
    return lire(dsn, sql, params)[0][0]


# --------------------------------------------------------------------------- #
# Semis du moyeu
# --------------------------------------------------------------------------- #
def test_le_moyeu_couvre_toutes_les_series(execute):
    dsn, _ = execute
    assert _un(dsn, "SELECT count(*) FROM manga.work_identity") == 10
    assert (
        _un(
            dsn,
            "SELECT count(*) FROM manga.ms_series_enriched WHERE work_uid IS NOT NULL",
        )
        == 10
    )


def test_une_serie_hors_map_recoit_le_moyeu_sans_identite(execute):
    dsn, _ = execute
    ligne = lire(
        dsn,
        "SELECT work_uid IS NOT NULL, wikidata_qid, kitsu_id "
        "FROM manga.work_identity WHERE series_id=10",
    )[0]
    assert ligne == (True, None, None)


# --------------------------------------------------------------------------- #
# Décisions auto
# --------------------------------------------------------------------------- #
def test_auto_concordant_remplit_identite_et_journal(execute):
    dsn, _ = execute
    ident = lire(
        dsn,
        "SELECT kitsu_id, mal_id, anilist_id, wikidata_qid "
        "FROM manga.work_identity WHERE series_id=1",
    )[0]
    assert ident == ("100", "1000", "2000", "Q1")
    dec = lire(
        dsn,
        "SELECT method, score, status, wikidata_qid FROM manga.match_decision "
        "WHERE series_id=1",
    )
    assert dec == [("kitsu_bridge", pytest.approx(1.0), "auto", "Q1")]


def test_auto_chemin_unique_sans_anilist(execute):
    dsn, _ = execute
    ident = lire(
        dsn,
        "SELECT mal_id, anilist_id, wikidata_qid FROM manga.work_identity "
        "WHERE series_id=2",
    )[0]
    assert ident == ("1001", None, "Q2")
    assert (
        _un(dsn, "SELECT status FROM manga.match_decision WHERE series_id=2") == "auto"
    )


# --------------------------------------------------------------------------- #
# needs_review : divergence et collision
# --------------------------------------------------------------------------- #
def test_divergence_ne_remplit_pas_l_identite(execute):
    dsn, _ = execute
    assert (
        _un(dsn, "SELECT status FROM manga.match_decision WHERE series_id=3")
        == "needs_review"
    )
    assert (
        _un(dsn, "SELECT wikidata_qid FROM manga.work_identity WHERE series_id=3")
        is None
    )


def test_collision_envoie_tout_le_groupe_en_review(execute):
    dsn, _ = execute
    statuts = lire(
        dsn,
        "SELECT series_id, status FROM manga.match_decision "
        "WHERE series_id IN (4,5) ORDER BY series_id",
    )
    assert statuts == [(4, "needs_review"), (5, "needs_review")]
    # ni l'un ni l'autre n'a saisi le QID partagé
    assert (
        _un(
            dsn,
            "SELECT count(*) FROM manga.work_identity "
            "WHERE series_id IN (4,5) AND wikidata_qid IS NOT NULL",
        )
        == 0
    )


# --------------------------------------------------------------------------- #
# Exclusions et hors pont : aucune décision
# --------------------------------------------------------------------------- #
def test_serie_ambigue_exclue_du_pont(execute):
    dsn, _ = execute
    assert _un(dsn, "SELECT count(*) FROM manga.match_decision WHERE series_id=6") == 0
    assert (
        _un(dsn, "SELECT wikidata_qid FROM manga.work_identity WHERE series_id=6")
        is None
    )


def test_serie_needs_review_historique_exclue(execute):
    dsn, _ = execute
    assert _un(dsn, "SELECT count(*) FROM manga.match_decision WHERE series_id=7") == 0


def test_hors_pont_sans_qid_n_a_pas_de_decision(execute):
    dsn, _ = execute
    assert (
        _un(
            dsn,
            "SELECT count(*) FROM manga.match_decision WHERE series_id IN (8,9)",
        )
        == 0
    )


# --------------------------------------------------------------------------- #
# Entonnoir et livrables
# --------------------------------------------------------------------------- #
def test_comptes_globaux(execute):
    dsn, _ = execute
    assert _un(dsn, "SELECT count(*) FROM manga.match_decision") == 5
    assert (
        _un(dsn, "SELECT count(*) FROM manga.match_decision WHERE status='auto'") == 2
    )
    assert (
        _un(
            dsn,
            "SELECT count(*) FROM manga.match_decision WHERE status='needs_review'",
        )
        == 3
    )
    assert (
        _un(
            dsn,
            "SELECT count(*) FROM manga.work_identity WHERE wikidata_qid IS NOT NULL",
        )
        == 2
    )


def test_echantillon_et_exclus_ecrits(execute):
    _, dossier = execute
    echantillon = (dossier / "echantillon_auto.csv").read_text(encoding="utf-8")
    assert "Label Q1" in echantillon  # label Wikidata joint
    lignes = echantillon.strip().splitlines()
    assert len(lignes) == 1 + 2  # en-tête + 2 auto
    exclus = (dossier / "exclus.csv").read_text(encoding="utf-8")
    assert "6," in exclus and "7," in exclus


# --------------------------------------------------------------------------- #
# Idempotence
# --------------------------------------------------------------------------- #
def test_rejeu_ne_change_aucun_compte(execute, tmp_path):
    dsn, _ = execute
    from identity import pont_kitsu

    avant = lire(
        dsn,
        "SELECT (SELECT count(*) FROM manga.match_decision), "
        "(SELECT count(*) FROM manga.work_identity), "
        "(SELECT count(*) FROM manga.work_identity WHERE wikidata_qid IS NOT NULL)",
    )[0]
    pont_kitsu.construire(dry_run=False, rapport_dir=str(tmp_path / "rejeu"))
    apres = lire(
        dsn,
        "SELECT (SELECT count(*) FROM manga.match_decision), "
        "(SELECT count(*) FROM manga.work_identity), "
        "(SELECT count(*) FROM manga.work_identity WHERE wikidata_qid IS NOT NULL)",
    )[0]
    assert avant == apres == (5, 10, 2)


def test_dry_run_n_ecrit_rien(base, tmp_path):
    _seed(base)
    from identity import pont_kitsu

    pont_kitsu.construire(dry_run=True, rapport_dir=str(tmp_path / "dry"))
    assert _un(base, "SELECT count(*) FROM manga.work_identity") == 0
    assert _un(base, "SELECT count(*) FROM manga.match_decision") == 0
