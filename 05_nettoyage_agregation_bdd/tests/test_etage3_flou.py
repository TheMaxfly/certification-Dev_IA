"""Étage 3 (flou pg_trgm), de bout en bout, sur base JETABLE.

Le test central de cet étage est un test d'ABSTENTION : quoi qu'on lui donne,
il ne doit jamais produire d'AUTO. Les autres vérifient la déduplication par
œuvre-cible, le TOP 3, le contenu de `details`, et le sort des orphelines.

`apimanga` n'est jamais atteignable.
"""

from __future__ import annotations

import json

import psycopg
import pytest
from conftest import lire

from identity import etage3_flou

# --------------------------------------------------------------------------- #
#  La calibration, en pur Python
# --------------------------------------------------------------------------- #


class CurseurFactice:
    """Rend une liste de (méthode, similarité) figée."""

    def __init__(self, lignes):
        self._lignes = lignes

    def execute(self, *_args, **_kwargs):
        return self

    def fetchall(self):
        return self._lignes


def test_la_calibration_ecarte_les_methodes_circulaires():
    """Le cœur méthodologique : les identités appariées SUR forme_norm exacte
    valent 1.0 par construction. Les compter dans le verdict fabriquerait un
    « 100 % » circulaire — c'est-à-dire un chiffre faux."""
    lignes = [("exact", 1.0)] * 300 + [("kitsu_bridge", 0.99)] * 100
    calib = etage3_flou.calibrer_seuil(CurseurFactice(lignes))

    assert calib["circulaires"] == 300
    assert calib["temoin"]["n"] == 100, "le verdict ne porte QUE sur le témoin"
    assert calib["verdict_ok"] is True


def test_un_temoin_majoritairement_sous_le_seuil_declenche_un_stop():
    """MUTATION : sans ce garde-fou, un seuil inadapté passerait en silence et
    l'étage ne proposerait presque rien, sans qu'on sache pourquoi."""
    lignes = [("exact", 1.0)] * 400 + [("kitsu_bridge", 0.5)] * 100
    calib = etage3_flou.calibrer_seuil(CurseurFactice(lignes))

    assert calib["verdict_ok"] is False, (
        "un témoin à 0 % au-dessus du seuil doit invalider le seuil, "
        "même si les circulaires affichent 100 %"
    )


def test_la_calibration_refuse_un_echantillon_sans_temoin():
    """Sans population indépendante, le seuil n'est pas contrôlable."""
    with pytest.raises(etage3_flou.ErreurEtage3, match="non circulaire"):
        etage3_flou.calibrer_seuil(CurseurFactice([("exact", 1.0)] * 50))


def test_la_calibration_refuse_un_echantillon_vide():
    with pytest.raises(etage3_flou.ErreurEtage3, match="Aucune identité"):
        etage3_flou.calibrer_seuil(CurseurFactice([]))


# --------------------------------------------------------------------------- #
#  Le harnais de scénario
# --------------------------------------------------------------------------- #


# Fixtures MESURÉES en base : à 0.85, le trigramme est une barre haute. Seuls
# des titres longs à variation minime la franchissent — un « Berserkk » vs
# « Berserk » plafonne à 0.700. Les similarités ci-dessous sont mesurées, pas
# supposées, et c'est ce qui empêche ces tests de passer pour la mauvaise
# raison (une assertion sur zéro décision est trivialement vraie).
BASE = "les chroniques de la lune noire"
V_933 = "les chroniques de la lune noires"
V_906 = "les chroniques de la lune noire ii"
V_903A = "les chroniques de la lune noirre"
V_903B = "les chroniques de la lune noiree"
V_903C = "les chroniques de la lune nooire"
V_900 = "les chroniques de la lune noir"
V_871 = "les chroniques de la luna noire"


def semer_serie(cur, series_id: int, titre: str, annee=None, auteur=None):
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


def semer_cible_kitsu(cur, kitsu_id: int, formes: list[str], annee=2000, auteur=None):
    cur.execute(
        "INSERT INTO manga.kitsu_meta (kitsu_id, annee, subtype) "
        "VALUES (%s, %s, 'manga')",
        (kitsu_id, annee),
    )
    for forme in formes:
        cur.execute(
            "INSERT INTO manga.kitsu_formes "
            "(kitsu_id, forme, forme_norm, forme_type, subtype) "
            "VALUES (%s, %s, %s, 'title', 'manga') "
            "ON CONFLICT DO NOTHING",
            (kitsu_id, forme, forme.lower()),
        )
    if auteur:
        cur.execute(
            "INSERT INTO manga.kitsu_staff (kitsu_id, personne, personne_norm, role) "
            "VALUES (%s, %s, %s, 'Story')",
            (kitsu_id, auteur, auteur.lower()),
        )


def semer_pont(cur, series_id: int, kitsu_id: int):
    """Une identité du pont : la population TÉMOIN de la calibration."""
    cur.execute(
        "INSERT INTO manga.ms_series_enriched (series_id, series_title, series_year) "
        "VALUES (%s, %s, 2000)",
        (series_id, f"pont{series_id}"),
    )
    # La forme MS est indispensable : la calibration mesure la similarité
    # ms_formes × kitsu_formes. Sans elle, le témoin serait à 0 % et le
    # garde-fou de seuil arrêterait le run — ce qu'il fait, correctement.
    cur.execute(
        "INSERT INTO manga.ms_formes (series_id, forme, forme_norm, forme_type) "
        "VALUES (%s, %s, %s, 'title')",
        (series_id, f"pont{series_id}", f"pont{series_id}"),
    )
    cur.execute(
        "INSERT INTO manga.work_identity (series_id, kitsu_id) VALUES (%s, %s)",
        (series_id, str(kitsu_id)),
    )
    cur.execute(
        "INSERT INTO manga.match_decision (series_id, method, status) "
        "VALUES (%s, 'kitsu_bridge', 'auto')",
        (series_id,),
    )
    cur.execute(
        "INSERT INTO manga.kitsu_meta (kitsu_id, annee, subtype) "
        "VALUES (%s, 2000, 'manga')",
        (kitsu_id,),
    )
    cur.execute(
        "INSERT INTO manga.kitsu_formes "
        "(kitsu_id, forme, forme_norm, forme_type, subtype) "
        "VALUES (%s, %s, %s, 'title', 'manga')",
        (kitsu_id, f"pont{series_id}", f"pont{series_id}"),
    )


def jouer(base: str, perimetre: int) -> str:
    import os

    os.environ["DATABASE_URL"] = base
    from typer.testing import CliRunner

    resultat = CliRunner().invoke(
        etage3_flou.app,
        [
            "--rapport-dir",
            "/tmp/etage3-test",
            "--perimetre-attendu",
            str(perimetre),
        ],
    )
    assert resultat.exit_code == 0, resultat.output
    return resultat.output


def executer(base: str, scenario, perimetre: int) -> str:
    with psycopg.connect(base) as connexion:
        with connexion.cursor() as cur:
            # 20 identités du pont : le témoin, à similarité 1.0.
            for i in range(1, 21):
                semer_pont(cur, 90000 + i, 90000 + i)
            scenario(cur)
            connexion.commit()
    return jouer(base, perimetre)


def decision(base: str, series_id: int):
    lignes = lire(
        base,
        "SELECT method, status, score, details FROM manga.match_decision "
        "WHERE series_id = %s",
        (series_id,),
    )
    return lignes[0] if lignes else None


# --------------------------------------------------------------------------- #
#  L'abstention — le test central
# --------------------------------------------------------------------------- #


def test_l_etage_ne_produit_jamais_d_auto(base):
    """LA règle de cet étage. Même avec un candidat unique à similarité
    quasi parfaite ET un auteur concordant — la configuration qui déclencherait
    un AUTO à l'étage 2 — le flou reste en needs_review."""

    def scenario(cur):
        semer_serie(cur, 1, BASE, 1989, "Kentaro Miura")
        semer_cible_kitsu(cur, 500, [V_933], 1989, auteur="Kentaro Miura")

    executer(base, scenario, perimetre=1)

    methode, statut, score, details = decision(base, 1)
    assert methode == "trgm"
    assert statut == "needs_review", "le flou PROPOSE, il ne décide jamais"
    assert score is not None and score >= 0.85
    assert details["case"] == "trgm_candidats"

    assert lire(
        base,
        "SELECT count(*) FROM manga.match_decision "
        "WHERE method='trgm' AND status='auto'",
    ) == [(0,)]


def test_l_etage_n_ecrit_rien_dans_work_identity(base):
    """Corollaire : ne rien décider, c'est ne rien remplir. Une écriture ici
    serait une décision déguisée."""

    def scenario(cur):
        semer_serie(cur, 2, BASE, 1998, "Takehiko Inoue")
        semer_cible_kitsu(cur, 600, [V_933], 1998, auteur="Takehiko Inoue")

    executer(base, scenario, perimetre=1)

    ligne = lire(
        base,
        "SELECT wikidata_qid, kitsu_id, mal_id FROM manga.work_identity "
        "WHERE series_id = 2",
    )[0]
    assert ligne == (None, None, None)


# --------------------------------------------------------------------------- #
#  Candidats : déduplication, TOP 3, contenu du dossier
# --------------------------------------------------------------------------- #


def test_une_oeuvre_cible_ne_compte_qu_une_fois(base):
    """MUTATION : sans la déduplication par œuvre-cible, une même œuvre
    occuperait les trois places du TOP 3 avec trois de ses propres titres et
    masquerait les vrais concurrents."""

    def scenario(cur):
        semer_serie(cur, 3, BASE, 1994)
        # UNE œuvre, QUATRE formes toutes au-dessus du seuil.
        semer_cible_kitsu(cur, 700, [V_933, V_903A, V_903B, V_900], 1994)

    executer(base, scenario, perimetre=1)

    _, _, _, details = decision(base, 3)
    cibles = [c["cible"] for c in details["top"]]
    assert cibles == ["kitsu:700"], "une œuvre = un candidat"
    assert details["n"] == 1


def test_le_dossier_est_limite_a_trois_candidats(base):
    def scenario(cur):
        semer_serie(cur, 4, BASE, 2000)
        # CINQ œuvres distinctes, toutes au-dessus du seuil.
        for kitsu_id, titre in enumerate(
            [V_933, V_906, V_903A, V_903B, V_900], start=800
        ):
            semer_cible_kitsu(cur, kitsu_id, [titre], 2000)

    executer(base, scenario, perimetre=1)

    _, _, _, details = decision(base, 4)
    assert len(details["top"]) == 3, "TOP 3 maximum dans le dossier"
    assert details["n"] == 5, "mais le compte TOTAL des candidats est conservé"


def test_le_dossier_porte_les_candidats_en_base(base):
    """La leçon de la dépendance à l'artefact : les candidats vivent dans
    `details`, pas seulement dans un CSV."""

    def scenario(cur):
        semer_serie(cur, 5, BASE, 2003, "Naoki Urasawa")
        semer_cible_kitsu(cur, 900, [V_933], 2003, auteur="Naoki Urasawa")

    executer(base, scenario, perimetre=1)

    details = lire(
        base,
        "SELECT details FROM manga.match_decision WHERE series_id = 5",
    )[0][0]
    candidat = details["top"][0]
    for clef in ("cible", "sim", "forme_ms", "forme_cible", "auteur", "ecart_annee"):
        assert clef in candidat, f"le dossier doit porter {clef}"
    assert candidat["auteur"] == "concordant"
    assert candidat["ecart_annee"] == 0


def test_les_candidats_sont_interrogeables_en_sql(base):
    """Le dossier doit être exploitable par requête, pas seulement lisible."""

    def scenario(cur):
        semer_serie(cur, 6, BASE, 1982)
        semer_cible_kitsu(cur, 1000, [V_933], 1982)

    executer(base, scenario, perimetre=1)

    trouve = lire(
        base,
        "SELECT series_id FROM manga.match_decision "
        "WHERE details->>'case' = 'trgm_candidats' "
        "  AND details->'top'->0->>'cible' = 'kitsu:1000'",
    )
    assert trouve == [(6,)]


# --------------------------------------------------------------------------- #
#  Les orphelines
# --------------------------------------------------------------------------- #


def test_une_serie_sans_candidat_ne_recoit_aucune_decision(base):
    """Décision figée n°7 : les orphelines ne sont PAS un needs_review de plus.
    Les empiler dans la file du juge lui demanderait d'arbitrer du vide."""

    def scenario(cur):
        semer_serie(cur, 7, "Absolument Introuvable Ici", 2010)
        semer_cible_kitsu(cur, 1100, ["Rien A Voir"], 2010)

    sortie = executer(base, scenario, perimetre=1)

    assert decision(base, 7) is None, "aucune décision pour une orpheline"
    assert lire(
        base,
        "SELECT count(*) FROM manga.ms_series_enriched s WHERE NOT EXISTS "
        "(SELECT 1 FROM manga.v_match_current v WHERE v.series_id=s.series_id)",
    ) == [(1,)], "elle reste sans-décision-courante"
    assert "orphelines de cascade" in sortie


# --------------------------------------------------------------------------- #
#  Les disciplines transverses
# --------------------------------------------------------------------------- #


def test_un_perimetre_inattendu_arrete_le_run(base):
    """Étape 0 : un écart inexpliqué arrête, il ne s'accommode pas."""
    import os

    os.environ["DATABASE_URL"] = base
    from typer.testing import CliRunner

    with psycopg.connect(base) as connexion:
        with connexion.cursor() as cur:
            for i in range(1, 21):
                semer_pont(cur, 90000 + i, 90000 + i)
            semer_serie(cur, 8, "Une Serie", 2000)
        connexion.commit()

    resultat = CliRunner().invoke(
        etage3_flou.app,
        ["--rapport-dir", "/tmp/etage3-test", "--perimetre-attendu", "999"],
    )
    assert resultat.exit_code == 1
    assert "écart inexpliqué" in str(resultat.exception)


def test_une_serie_deja_decidee_n_est_jamais_rejouee(base):
    def scenario(cur):
        semer_serie(cur, 9, BASE, 1990)
        semer_cible_kitsu(cur, 1200, [V_933], 1990)
        cur.execute(
            "INSERT INTO manga.match_decision (series_id, method, status) "
            "VALUES (9, 'manual', 'validated')"
        )

    executer(base, scenario, perimetre=0)

    lignes = lire(base, "SELECT method FROM manga.match_decision WHERE series_id = 9")
    assert lignes == [("manual",)]


def test_le_rejeu_n_ecrit_rien(base):
    """MUTATION : sans le filtre d'idempotence, chaque run doublerait le
    journal."""

    def scenario(cur):
        semer_serie(cur, 10, BASE, 1995)
        semer_cible_kitsu(cur, 1300, [V_933], 1995)

    executer(base, scenario, perimetre=1)
    avant = lire(base, "SELECT count(*) FROM manga.match_decision")[0][0]

    jouer(base, perimetre=0)

    assert lire(base, "SELECT count(*) FROM manga.match_decision")[0][0] == avant


def test_details_est_renseigne_sur_toutes_les_decisions_de_l_etage(base):
    def scenario(cur):
        for series_id, kitsu_id, variante in (
            (11, 1400, V_933),
            (12, 1401, V_906),
        ):
            semer_serie(cur, series_id, BASE, 2000)
            semer_cible_kitsu(cur, kitsu_id, [variante], 2000)

    executer(base, scenario, perimetre=2)

    assert lire(
        base,
        "SELECT count(*) FROM manga.match_decision "
        "WHERE method='trgm' AND details IS NULL",
    ) == [(0,)]


def test_verifier_prerequis_refuse_un_check_sans_trgm(base):
    """Décision figée n°6 : sans 'trgm' au CHECK, STOP — pas de migration."""
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
            with pytest.raises(etage3_flou.ErreurEtage3, match="trgm"):
                etage3_flou.verifier_prerequis(cur)


def test_verifier_prerequis_refuse_l_absence_d_index_trigramme(base):
    """Sans les index GIN, le rapprochement serait un produit cartésien de
    ~1,9 milliard de paires : mieux vaut s'arrêter que ramer."""
    with psycopg.connect(base) as connexion:
        connexion.execute("DROP INDEX manga.kitsu_formes_forme_norm_trgm_idx")
        connexion.commit()
        with connexion.cursor() as cur:
            with pytest.raises(etage3_flou.ErreurEtage3, match="GIN"):
                etage3_flou.verifier_prerequis(cur)


def test_le_seuil_ne_survit_pas_a_la_transaction(base):
    """Portée transaction et non session : un seuil qui fuirait fausserait
    silencieusement toute requête trigramme ultérieure."""

    def scenario(cur):
        semer_serie(cur, 13, BASE, 2000)
        semer_cible_kitsu(cur, 1500, [V_933], 2000)

    executer(base, scenario, perimetre=1)

    with psycopg.connect(base) as connexion:
        connexion.execute("SELECT similarity('a', 'a')")  # charge le module
        seuil = connexion.execute("SHOW pg_trgm.similarity_threshold").fetchone()[0]
    assert float(seuil) == pytest.approx(0.3), "le seuil par défaut doit être rendu"


def test_le_livrable_consolide_porte_les_trois_origines(base):
    """Le consolidé est l'ENTRÉE de l'étage R : il doit réunir les étages."""
    from pathlib import Path

    def scenario(cur):
        # Un dossier d'un étage antérieur, laissé en needs_review.
        semer_serie(cur, 14, "Ancien Dossier", 2001)
        cur.execute(
            "INSERT INTO manga.match_decision (series_id, method, status, details) "
            "VALUES (14, 'exact_kitsu', 'needs_review', %s)",
            (json.dumps({"case": "review_k_ambiguite", "n_cand": 2}),),
        )
        semer_serie(cur, 15, BASE, 2002)
        semer_cible_kitsu(cur, 1600, [V_933], 2002)

    executer(base, scenario, perimetre=1)

    contenu = Path("/tmp/etage3-test/needs_review_consolide_final.csv").read_text(
        encoding="utf-8"
    )
    assert "etage2" in contenu and "etage3" in contenu
    assert "review_k_ambiguite" in contenu
    assert "trgm_candidats" in contenu
