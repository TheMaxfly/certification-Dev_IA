"""Étage 2 de la cascade, de bout en bout, sur base JETABLE.

Chaque test fabrique une situation MINIMALE qui force UNE case de la matrice,
puis vérifie la décision, le score, la méthode et le contenu de `details`.
`apimanga` n'est jamais atteignable.

Le pont (étage 0) est simulé par quelques identités à kitsu_id : c'est lui qui
sert d'étalon à la calibration de la fenêtre d'année.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from conftest import lire

from identity import etage2_kitsu

# --------------------------------------------------------------------------- #
#  La calibration, en pur Python
# --------------------------------------------------------------------------- #


def test_la_fenetre_est_empirique_sans_plancher():
    """Dette 22.4 : l'étage 1 élargissait à [-1, +8] par un plancher hérité
    d'une prémisse réfutée. Ici, [p5, p95] et rien d'autre."""
    # 100 écarts : 90 à 0, 5 à -3, 5 à +7 -> p5 et p95 sortent des extrêmes.
    ecarts = [0] * 90 + [-3] * 5 + [7] * 5
    calib = etage2_kitsu.calibrer(ecarts)

    basse, haute = calib["fenetre"]
    assert basse >= -3 and haute <= 7
    assert calib["med"] == 0
    # Le plancher de l'étage 1 aurait imposé -1 en borne basse et +8 en haute.
    assert (basse, haute) != (-1, 8), "aucun plancher ne doit être appliqué"


def test_une_distribution_serree_donne_une_fenetre_serree():
    """La conséquence voulue : des données propres resserrent la fenêtre au
    lieu de la laisser ouverte à huit ans."""
    calib = etage2_kitsu.calibrer([0] * 95 + [1] * 5)
    assert calib["fenetre"] == (0, 1)


def test_calibrer_refuse_de_deviner_sans_donnee():
    with pytest.raises(etage2_kitsu.ErreurEtage2, match="ne peut"):
        etage2_kitsu.calibrer([])


# --------------------------------------------------------------------------- #
#  Le harnais de scénario
# --------------------------------------------------------------------------- #


def semer(cur, series_id: int, titre: str, annee: int | None, auteur: str | None):
    """Une série MS, son moyeu d'identité et sa forme de titre."""
    cur.execute(
        "INSERT INTO manga.ms_series_enriched "
        "(series_id, series_title, series_year, series_scenariste) "
        "VALUES (%s, %s, %s, %s)",
        (series_id, titre, annee, auteur),
    )
    cur.execute("INSERT INTO manga.work_identity (series_id) VALUES (%s)", (series_id,))
    cur.execute(
        "INSERT INTO manga.ms_formes (series_id, forme, forme_norm, forme_type) "
        "VALUES (%s, %s, %s, 'title')",
        (series_id, titre, titre.lower()),
    )


def semer_kitsu(
    cur,
    kitsu_id: int,
    titre: str,
    annee: int | None,
    auteur: str | None = None,
    mal_id: str | None = None,
):
    cur.execute(
        "INSERT INTO manga.kitsu_meta (kitsu_id, annee, subtype) "
        "VALUES (%s, %s, 'manga')",
        (kitsu_id, annee),
    )
    cur.execute(
        "INSERT INTO manga.kitsu_formes "
        "(kitsu_id, forme, forme_norm, forme_type, subtype) "
        "VALUES (%s, %s, %s, 'canonical', 'manga')",
        (kitsu_id, titre, titre.lower()),
    )
    if auteur:
        cur.execute(
            "INSERT INTO manga.kitsu_staff "
            "(kitsu_id, personne, personne_norm, role) "
            "VALUES (%s, %s, %s, 'Story')",
            (kitsu_id, auteur, auteur.lower()),
        )
    if mal_id:
        cur.execute(
            "INSERT INTO manga.kitsu_mappings "
            "(kitsu_id, external_site, external_id) "
            "VALUES (%s, 'myanimelist/manga', %s)",
            (kitsu_id, mal_id),
        )


def semer_pont(cur, series_id: int, kitsu_id: int, annee_ms: int, annee_k: int):
    """Une identité du pont : l'étalon indépendant de la calibration."""
    cur.execute(
        "INSERT INTO manga.ms_series_enriched (series_id, series_title, series_year) "
        "VALUES (%s, %s, %s)",
        (series_id, f"pont-{series_id}", annee_ms),
    )
    cur.execute(
        "INSERT INTO manga.work_identity (series_id, kitsu_id, wikidata_qid) "
        "VALUES (%s, %s, %s)",
        (series_id, str(kitsu_id), f"Q{series_id}"),
    )
    cur.execute(
        "INSERT INTO manga.match_decision (series_id, method, status) "
        "VALUES (%s, 'kitsu_bridge', 'auto')",
        (series_id,),
    )
    cur.execute(
        "INSERT INTO manga.kitsu_meta (kitsu_id, annee, subtype) "
        "VALUES (%s, %s, 'manga')",
        (kitsu_id, annee_k),
    )


def jouer(base: str) -> str:
    """Joue l'étage 2, sans rien semer. Séparé de l'ensemencement pour que le
    test de rejeu puisse relancer l'étage sans recréer ses fixtures."""
    import os

    os.environ["DATABASE_URL"] = base
    from typer.testing import CliRunner

    resultat = CliRunner().invoke(
        etage2_kitsu.app, ["--rapport-dir", "/tmp/etage2-test"]
    )
    assert resultat.exit_code == 0, resultat.output
    return resultat.output


def executer(base: str, scenario) -> str:
    """Sème le pont d'étalonnage et le scénario, puis joue l'étage 2."""
    with psycopg.connect(base) as connexion:
        with connexion.cursor() as cur:
            # Un pont minimal mais suffisant pour calibrer (écart 0 partout).
            for i in range(1, 21):
                semer_pont(cur, 90000 + i, 90000 + i, 2000, 2000)
            scenario(cur)
            connexion.commit()
    return jouer(base)


def decision(base: str, series_id: int):
    lignes = lire(
        base,
        "SELECT method, status, score, details->>'case' FROM manga.match_decision "
        "WHERE series_id = %s",
        (series_id,),
    )
    return lignes[0] if lignes else None


# --------------------------------------------------------------------------- #
#  La matrice, case par case
# --------------------------------------------------------------------------- #


def test_kitsu_id_historique_confirme_passe_en_auto(base):
    """Le signal le plus fort : Manga Sanctuary ET une forme disent le même
    kitsu_id. Deux sources indépendantes."""

    def scenario(cur):
        semer(cur, 1, "Monster", 1994, None)
        semer_kitsu(cur, 500, "Monster", 1994)
        cur.execute(
            "INSERT INTO manga.ms_kitsu_map (series_id, kitsu_id) VALUES (1, 500)"
        )

    executer(base, scenario)

    methode, statut, score, case = decision(base, 1)
    assert (methode, statut) == ("exact_kitsu", "auto")
    assert score == pytest.approx(0.96)
    assert case == "auto_k_historique_confirme"


def test_kitsu_id_historique_contredit_part_en_review(base):
    """MUTATION : sans ce cas, une contradiction entre l'historique MS et les
    formes serait tranchée en silence par le candidat unique."""

    def scenario(cur):
        semer(cur, 2, "Pluto", 2003, None)
        # Les formes désignent 600 ; l'historique MS dit 601.
        semer_kitsu(cur, 600, "Pluto", 2003)
        cur.execute(
            "INSERT INTO manga.ms_kitsu_map (series_id, kitsu_id) VALUES (2, 601)"
        )

    executer(base, scenario)

    methode, statut, score, case = decision(base, 2)
    assert statut == "needs_review"
    assert case == "review_k_historique_contredit"
    assert score is None


def test_candidat_unique_avec_auteur_concordant(base):
    def scenario(cur):
        semer(cur, 3, "Berserk", 1989, "Kentaro Miura")
        semer_kitsu(cur, 700, "Berserk", 1989, auteur="Kentaro Miura")

    executer(base, scenario)

    methode, statut, score, case = decision(base, 3)
    assert (methode, statut) == ("exact_kitsu_author", "auto")
    assert score == pytest.approx(0.95)
    assert case == "auto_k_unique_auteur"


def test_auteur_discordant_interdit_l_auto(base):
    """MUTATION : sans ce cas, un homonyme d'un autre auteur passerait en auto."""

    def scenario(cur):
        semer(cur, 4, "Eden", 1997, "Hiroki Endo")
        semer_kitsu(cur, 800, "Eden", 1997, auteur="Quelqu'un d'Autre")

    executer(base, scenario)

    _, statut, _, case = decision(base, 4)
    assert statut == "needs_review"
    assert case == "review_k_auteur_discordant"


def test_annee_discordante_interdit_l_auto_meme_seule(base):
    """La règle dure de la cascade : une année hors fenêtre n'est jamais auto,
    quel que soit le reste."""

    def scenario(cur):
        semer(cur, 5, "Akira", 1982, None)
        semer_kitsu(cur, 900, "Akira", 2015)  # écart +(-33), hors fenêtre

    executer(base, scenario)

    _, statut, _, case = decision(base, 5)
    assert statut == "needs_review"
    assert case == "review_k_annee_discordante"


def test_annee_confirme_quand_l_auteur_est_incomparable(base):
    """L'année CONFIRME : elle suffit sur un candidat unique sans auteur
    comparable — mais avec un score volontairement inférieur."""

    def scenario(cur):
        semer(cur, 6, "Vagabond", 1998, None)
        semer_kitsu(cur, 1000, "Vagabond", 1998)

    executer(base, scenario)

    methode, statut, score, case = decision(base, 6)
    assert (methode, statut) == ("exact_kitsu", "auto")
    assert score == pytest.approx(0.90)
    assert case == "auto_k_unique_annee"


def test_aucun_signal_part_en_review(base):
    """Ni auteur comparable, ni année des deux côtés : rien ne confirme."""

    def scenario(cur):
        semer(cur, 7, "Inconnu", None, None)
        semer_kitsu(cur, 1100, "Inconnu", None)

    executer(base, scenario)

    _, statut, _, case = decision(base, 7)
    assert statut == "needs_review"
    assert case == "review_k_sans_signal"


def test_multi_candidats_departages_par_l_auteur(base):
    def scenario(cur):
        semer(cur, 8, "Phoenix", 1967, "Osamu Tezuka")
        semer_kitsu(cur, 1200, "Phoenix", 1967, auteur="Osamu Tezuka")
        semer_kitsu(cur, 1201, "Phoenix", 1967, auteur="Autre Personne")

    executer(base, scenario)

    methode, statut, score, case = decision(base, 8)
    assert (methode, statut) == ("exact_kitsu_author", "auto")
    assert score == pytest.approx(0.93)
    assert case == "auto_k_multi_auteur"


def test_ambiguite_persistante_part_en_review(base):
    """Deux candidats, rien pour départager : jamais d'auto par ordre
    d'arrivée."""

    def scenario(cur):
        semer(cur, 9, "Gemini", 2000, None)
        semer_kitsu(cur, 1300, "Gemini", 2000)
        semer_kitsu(cur, 1301, "Gemini", 2000)

    executer(base, scenario)

    _, statut, _, case = decision(base, 9)
    assert statut == "needs_review"
    assert case in ("review_k_ambiguite", "review_k_multi_annee_seule")


def test_collision_deux_series_vers_le_meme_kitsu_id(base):
    """MUTATION : sans la détection AVANT insert, l'index UNIQUE partiel
    ferait échouer la transaction — ou pire, le premier arrivé gagnerait."""

    def scenario(cur):
        semer(cur, 10, "Clone", 2001, "Auteur Commun")
        semer(cur, 11, "Clone", 2001, "Auteur Commun")
        semer_kitsu(cur, 1400, "Clone", 2001, auteur="Auteur Commun")

    executer(base, scenario)

    for series_id in (10, 11):
        _, statut, _, case = decision(base, series_id)
        assert statut == "needs_review", "TOUT le groupe part en review"
        assert case == "review_k_collision_kitsu"


# --------------------------------------------------------------------------- #
#  Remplissage de l'identité
# --------------------------------------------------------------------------- #


def test_l_identite_partielle_est_legitime(base):
    """Sans mapping vers MAL, l'identité s'arrête au kitsu_id — c'est l'étage
    0bis réalisé, pas un échec. Les colonnes sont indépendantes."""

    def scenario(cur):
        semer(cur, 12, "Longue Traine", 2012, "Auteur Traine")
        semer_kitsu(cur, 1500, "Longue Traine", 2012, auteur="Auteur Traine")

    executer(base, scenario)

    ligne = lire(
        base,
        "SELECT kitsu_id, wikidata_qid, mal_id FROM manga.work_identity "
        "WHERE series_id = 12",
    )[0]
    assert ligne[0] == "1500"
    assert ligne[1] is None, "aucun QID atteignable : la colonne reste vide"
    assert ligne[2] is None


def test_l_identite_complete_remonte_le_qid(base):
    """Quand le mapping existe et mène au pivot, le QID est rempli."""

    def scenario(cur):
        cur.execute("INSERT INTO manga.wd_pivot (qid, mal_id) VALUES ('Q42', '4242')")
        semer(cur, 13, "Tete De Catalogue", 2005, "Auteur Tete")
        semer_kitsu(
            cur, 1600, "Tete De Catalogue", 2005, auteur="Auteur Tete", mal_id="4242"
        )

    executer(base, scenario)

    ligne = lire(
        base,
        "SELECT kitsu_id, wikidata_qid, mal_id FROM manga.work_identity "
        "WHERE series_id = 13",
    )[0]
    assert ligne == ("1600", "Q42", "4242")


# --------------------------------------------------------------------------- #
#  Les disciplines transverses
# --------------------------------------------------------------------------- #


def test_une_serie_deja_decidee_n_est_jamais_rejouee(base):
    """Idempotence par v_match_current : le fondement du run mensuel."""

    def scenario(cur):
        semer(cur, 14, "Deja Vu", 1990, "Auteur X")
        semer_kitsu(cur, 1700, "Deja Vu", 1990, auteur="Auteur X")
        cur.execute(
            "INSERT INTO manga.match_decision (series_id, method, status) "
            "VALUES (14, 'manual', 'validated')"
        )

    executer(base, scenario)

    lignes = lire(base, "SELECT method FROM manga.match_decision WHERE series_id = 14")
    assert lignes == [("manual",)], "aucune décision d'étage 2 ne doit s'ajouter"


def test_le_rejeu_n_ecrit_rien(base):
    """MUTATION : sans le filtre d'idempotence, chaque run doublerait le
    journal."""

    def scenario(cur):
        semer(cur, 15, "Rejeu", 1995, "Auteur Y")
        semer_kitsu(cur, 1800, "Rejeu", 1995, auteur="Auteur Y")

    executer(base, scenario)
    avant = lire(base, "SELECT count(*) FROM manga.match_decision")[0][0]

    jouer(base)  # même base, mêmes données : l'étage doit ne rien écrire

    assert lire(base, "SELECT count(*) FROM manga.match_decision")[0][0] == avant


def test_chaque_decision_porte_sa_case(base):
    """L'exigence qui rend un dossier ré-instruisable depuis la SEULE base."""

    def scenario(cur):
        semer(cur, 16, "Avec Case", 1999, "Auteur Z")
        semer_kitsu(cur, 1900, "Avec Case", 1999, auteur="Auteur Z")
        semer(cur, 17, "Sans Signal", None, None)
        semer_kitsu(cur, 1901, "Sans Signal", None)

    executer(base, scenario)

    lignes = lire(
        base,
        "SELECT count(*) FROM manga.match_decision "
        "WHERE method LIKE 'exact_kitsu%' AND details->>'case' IS NULL",
    )
    assert lignes == [(0,)], "toute décision d'étage 2 porte sa case"


def test_l_annexe_r_n_ecrit_aucune_decision(base):
    """Le cœur de l'ANNEXE R : elle éclaire, elle ne décide pas. La série en
    needs_review garde SA décision, inchangée."""

    def scenario(cur):
        # Un dossier laissé en needs_review par un étage antérieur.
        semer(cur, 18, "Dossier Douteux", 2008, None)
        cur.execute(
            "INSERT INTO manga.match_decision (series_id, method, status) "
            "VALUES (18, 'exact', 'needs_review')"
        )
        semer_kitsu(cur, 2000, "Dossier Douteux", 2008)

    executer(base, scenario)

    lignes = lire(
        base,
        "SELECT method, status FROM manga.match_decision WHERE series_id = 18",
    )
    assert lignes == [("exact", "needs_review")], "l'annexe n'écrit pas au journal"


def test_l_annexe_r_produit_son_livrable(base):
    """Et elle produit bien l'enrichissement attendu par l'étage R."""

    def scenario(cur):
        semer(cur, 19, "Eclaire Moi", 2010, None)
        cur.execute(
            "INSERT INTO manga.match_decision (series_id, method, status) "
            "VALUES (19, 'exact', 'needs_review')"
        )

    executer(base, scenario)

    fichier = Path("/tmp/etage2-test/enrichissement_r.csv")
    assert fichier.is_file()
    contenu = fichier.read_text(encoding="utf-8")
    assert "19" in contenu
    assert "verdict_kitsu" in contenu


def test_verifier_prerequis_refuse_un_check_non_elargi(base):
    """MUTATION : sans la 009, l'étage doit s'arrêter au lieu de contourner."""
    with psycopg.connect(base) as connexion:
        connexion.execute(
            "ALTER TABLE manga.match_decision "
            "DROP CONSTRAINT match_decision_method_check"
        )
        connexion.execute(
            "ALTER TABLE manga.match_decision ADD CONSTRAINT "
            "match_decision_method_check CHECK (method IN ('exact', 'manual'))"
        )
        connexion.commit()
        with connexion.cursor() as cur:
            with pytest.raises(etage2_kitsu.ErreurEtage2, match="exact_kitsu"):
                etage2_kitsu.verifier_prerequis(cur)
