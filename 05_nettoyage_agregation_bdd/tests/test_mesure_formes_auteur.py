"""Mesure « formes d'auteur » (dette 22.3), de bout en bout sur base JETABLE.

Ce que ces tests protègent : un pourcentage cité dans la documentation du
projet. Le précédent — « 99,6 % » — venait d'une requête jetable à
normalisation SQL approchée, non conservée. Le remplacer par un autre chiffre
non testé n'aurait rien réglé.

Deux risques sont couverts :

  1. **la classe de caractères « non latin » arrive corrompue.** Elle est écrite
     en caractères littéraux dans le SQL ; un fichier réencodé de travers
     donnerait un chiffre plausible et faux. Les témoins la vérifient.
  2. **le prédicat de concordance dérive** de celui de `etage1_exact.sql`. Le
     scénario ci-dessous pose les trois cas qui distinguent les définitions —
     latine, non latine, D0 seul — et fige leur comptage.
"""

from __future__ import annotations

import psycopg
import pytest

from identity.mesure_formes_auteur import (
    REQUETE,
    TEMOINS_LATINS,
    TEMOINS_NON_LATINS,
    classe_non_latine,
    mesurer,
    pourcentage,
)

SQL = REQUETE.read_text(encoding="utf-8")


def _seed(dsn: str) -> None:
    """Trois séries décidées par l'auteur, une par cas à distinguer.

    - 1 : concorde par une forme LATINE (alias romanisé) ;
    - 2 : concorde par une forme NON LATINE (le catalogue MS porte du japonais,
          ce qui est rarissime en vrai mais doit rester mesurable) ;
    - 3 : concorde par le nom RETENU en D0, donc sans la table des formes.
    """
    with psycopg.connect(dsn, autocommit=True) as cx:
        for sid, scenariste in (
            (1, "Tsubasa Fukuchi"),
            (2, "十夜"),
            (3, "Naoki Urasawa"),
        ):
            cx.execute(
                "INSERT INTO manga.ms_series_enriched "
                "(series_id, series_scenariste) VALUES (%s, %s)",
                (sid, scenariste),
            )
            cx.execute(
                "INSERT INTO manga.work_identity (series_id) VALUES (%s)", (sid,)
            )

        for sid, qid in ((1, "Q1"), (2, "Q2"), (3, "Q3")):
            cx.execute("INSERT INTO manga.wd_pivot (qid) VALUES (%s)", (qid,))
            cx.execute(
                "INSERT INTO manga.match_decision "
                "(series_id, wikidata_qid, method, status) "
                "VALUES (%s, %s, 'exact_author', 'auto')",
                (sid, qid),
            )

        # Q1 : label japonais + alias romanisé. C'est l'alias qui porte le lien.
        cx.execute(
            "INSERT INTO manga.wd_auteurs (qid, auteur_qid, auteur, auteur_norm) "
            "VALUES ('Q1', 'QA1', %s, %s)",
            ("福地翼", "福地翼"),
        )
        for forme, norme, typ, langue in (
            ("福地翼", "福地翼", "label", "ja"),
            ("Tsubasa Fukuchi", "tsubasa fukuchi", "alias", "en"),
        ):
            cx.execute(
                "INSERT INTO manga.wd_auteurs_formes "
                "(auteur_qid, forme, forme_norm, forme_type, langue) "
                "VALUES ('QA1', %s, %s, %s, %s)",
                (forme, norme, typ, langue),
            )

        # Q2 : seule une forme japonaise existe, et le MS porte du japonais.
        cx.execute(
            "INSERT INTO manga.wd_auteurs (qid, auteur_qid, auteur, auteur_norm) "
            "VALUES ('Q2', 'QA2', %s, %s)",
            ("十夜", "十夜"),
        )
        cx.execute(
            "INSERT INTO manga.wd_auteurs_formes "
            "(auteur_qid, forme, forme_norm, forme_type, langue) "
            "VALUES ('QA2', %s, %s, 'label', 'ja')",
            ("十夜", "十夜"),
        )

        # Q3 : le nom RETENU en D0 suffit — la forme le répète.
        cx.execute(
            "INSERT INTO manga.wd_auteurs (qid, auteur_qid, auteur, auteur_norm) "
            "VALUES ('Q3', 'QA3', %s, %s)",
            ("Naoki Urasawa", "naoki urasawa"),
        )
        cx.execute(
            "INSERT INTO manga.wd_auteurs_formes "
            "(auteur_qid, forme, forme_norm, forme_type, langue) "
            "VALUES ('QA3', %s, %s, 'label', 'en')",
            ("Naoki Urasawa", "naoki urasawa"),
        )


@pytest.fixture
def mesure(base):
    _seed(base)
    with psycopg.connect(base) as cx, cx.cursor() as cur:
        return mesurer(cur)


class TestClasseDeCaracteres:
    """La classe vient du SQL, jamais redéclarée en Python."""

    def test_elle_est_extraite_du_sql_et_non_vide(self):
        assert classe_non_latine(SQL)

    def test_les_temoins_sont_classes_par_le_serveur(self, base):
        """Le vrai test : c'est PostgreSQL qui applique la classe, pas Python."""
        classe = classe_non_latine(SQL)
        with psycopg.connect(base) as cx:
            verdicts = dict(
                cx.execute(
                    "SELECT f, f !~ ('[' || %s || ']') FROM unnest(%s::text[]) AS t(f)",
                    (classe, list(TEMOINS_LATINS + TEMOINS_NON_LATINS)),
                ).fetchall()
            )
        assert all(verdicts[f] for f in TEMOINS_LATINS)
        assert not any(verdicts[f] for f in TEMOINS_NON_LATINS)

    def test_une_romanisation_etiquetee_ja_compte_comme_latine(self, base):
        """Le critère retenu est la GRAPHIE, pas le tag de langue Wikidata."""
        classe = classe_non_latine(SQL)
        with psycopg.connect(base) as cx:
            (latine,) = cx.execute(
                "SELECT %s !~ ('[' || %s || ']')", ("fukuchi tsubasa", classe)
            ).fetchone()
        assert latine


class TestMesure:
    def test_le_denominateur_est_le_journal_des_decisions(self, mesure):
        assert mesure["series_decidees"] == 3
        assert mesure["series_concordantes"] == 3

    def test_la_forme_latine_est_comptee_meme_si_le_label_est_japonais(self, mesure):
        """Série 1 : label `ja`, alias romanisé. L'alias suffit."""
        assert mesure["series_avec_latine"] == 2  # séries 1 et 3

    def test_une_concordance_purement_japonaise_est_comptee_comme_telle(self, mesure):
        assert mesure["series_sans_latine"] == 1  # série 2
        assert mesure["couples_non_latins"] == 1

    def test_le_contrefactuel_d0_ne_retient_que_le_nom_retenu(self, mesure):
        """Séries 2 et 3 : `auteur_norm` égale le nom MS. Série 1 : non."""
        assert mesure["series_d0_seul"] == 2

    def test_le_pourcentage_est_celui_des_series_concordantes(self, mesure):
        assert pourcentage(
            mesure["series_avec_latine"], mesure["series_concordantes"]
        ) == pytest.approx(66.67, abs=0.01)


def test_pourcentage_ne_divise_pas_par_zero():
    assert pourcentage(0, 0) == 0.0
