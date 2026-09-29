"""Confirmation au catalogue, de bout en bout, sur base JETABLE migrée.

Le cœur de ces tests est une règle, pas une fonction : l'outil CONFIRME par
égalité stricte, il ne CHERCHE jamais. « Bersek » ne doit pas retrouver
« Berserk » — c'est précisément ce que la famille F4 mesurera, et un outil de
vérification qui le trouverait aurait biaisé le jeu en faveur du bras lexical.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evaluation import atteignabilite, catalogue, confirmer, regles  # noqa: E402
from evaluation.jeu import COLONNES_ATTENDUS, COLONNES_QUESTIONS  # noqa: E402
from identity.wikidata_dump import normaliser  # noqa: E402

SERIES = [
    (1, "Berserk", "Kentaro MIURA", "Kentaro MIURA"),
    (2, "Monster", "Naoki URASAWA", "Naoki URASAWA"),
    (3, "20th Century Boys", "Naoki URASAWA", "Naoki URASAWA"),
    (4, "Pluto", "Naoki URASAWA", "Takashi NAGASAKI"),
    (5, "Vagabond", "Takehiko INOUE", "Takehiko INOUE"),
    (6, "Vagabond", "Takehiko INOUE", "Takehiko INOUE"),
]


@pytest.fixture
def base_catalogue(base):
    with psycopg.connect(base) as cx:
        for s in SERIES:
            cx.execute(
                "INSERT INTO manga.ms_series_enriched (series_id, series_title,"
                " series_dessinateur, series_scenariste) VALUES (%s, %s, %s, %s)",
                s,
            )
        cx.execute(
            "INSERT INTO manga.ms_formes (series_id, forme, forme_norm, forme_type,"
            " source) VALUES (1, 'Beruseruku', %s, 'alias', 'ms')",
            (normaliser("Beruseruku"),),
        )
        for kid, forme in ((900, "Tetsuwan Atom"), (902, "Kaibutsu")):
            cx.execute(
                "INSERT INTO manga.kitsu_formes (kitsu_id, forme, forme_norm,"
                " forme_type, subtype) VALUES (%s, %s, %s, 'canonical', 'manga')",
                (kid, forme, normaliser(forme)),
            )
        cx.execute(
            "INSERT INTO manga.work_identity (series_id, kitsu_id) VALUES (2, '902')"
        )
        cx.execute(
            "INSERT INTO manga.kitsu_staff (kitsu_id, personne, personne_norm)"
            " VALUES (900, 'Osamu TEZUKA', %s)",
            (normaliser("Osamu TEZUKA"),),
        )
    return base


def jeu(tmp_path: Path, questions: list[list[str]], attendus: list[list[str]]) -> Path:
    for nom, colonnes, lignes in (
        ("questions.csv", COLONNES_QUESTIONS, questions),
        ("attendus.csv", COLONNES_ATTENDUS, attendus),
    ):
        with (tmp_path / nom).open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(colonnes)
            w.writerows(lignes)
    return tmp_path


def question(qid, famille="F1", mode="reconnaissance", issue="au_catalogue", texte="t"):
    return [qid, texte, mode, famille, issue, "nouvelle", "", "note"]


def attendu(qid, reponse, grade="2"):
    return [qid, reponse, grade, "", "", ""]


def bilans(url, dossier):
    sortie, b = confirmer.executer(url, dossier)
    return sortie, {x.question.question_id: x for x in b}


# --------------------------------------------------------------------------- #
#  Au catalogue
# --------------------------------------------------------------------------- #


def test_titre_exact_normalise(base_catalogue, tmp_path):
    d = jeu(
        tmp_path,
        [question("Q001"), question("Q002", famille="F5")],
        [attendu("Q001", "titre:  le BERSERK "), attendu("Q002", "titre: Beruseruku")],
    )
    sortie, b = bilans(base_catalogue, d)
    assert b["Q001"].statut == b["Q002"].statut == "confirmee"
    assert [(a.series_id, a.titre_catalogue, a.confirmation) for a in sortie] == [
        ("1", "Berserk", "titre:title"),
        ("1", "Berserk", "titre:alias"),
    ]


def test_une_faute_n_est_pas_repechee(base_catalogue, tmp_path):
    """La règle : on confirme, on ne cherche pas. Une réponse introuvable met la
    question de côté."""
    d = jeu(
        tmp_path, [question("Q001", famille="F4")], [attendu("Q001", "titre: Bersek")]
    )
    sortie, b = bilans(base_catalogue, d)
    assert b["Q001"].statut == "de_cote"
    assert sortie[0].series_id == "" and sortie[0].confirmation.startswith(
        "introuvable"
    )


def test_titre_ambigu_puis_leve_par_id(base_catalogue, tmp_path):
    d = jeu(tmp_path, [question("Q001")], [attendu("Q001", "titre: Vagabond")])
    sortie, b = bilans(base_catalogue, d)
    assert b["Q001"].statut == "a_revoir"
    assert sortie[0].confirmation == "ambigue: 5 | 6"
    d = jeu(tmp_path, [question("Q001")], [attendu("Q001", "id: 6")])
    _, b = bilans(base_catalogue, d)
    assert b["Q001"].statut == "confirmee" and b["Q001"].series == {6: "2"}


def test_auteur_se_deplie_en_proposition(base_catalogue, tmp_path):
    d = jeu(
        tmp_path,
        [question("Q001", famille="F1", mode="proposition")],
        [attendu("Q001", "auteur: Naoki Urasawa", "1")],
    )
    sortie, b = bilans(base_catalogue, d)
    assert b["Q001"].statut == "confirmee"
    assert [a.series_id for a in sortie] == ["2", "3", "4"]
    assert {a.confirmation for a in sortie} == {"auteur:dessinateur"}


def test_auteur_a_plusieurs_series_ambigu_en_reconnaissance(base_catalogue, tmp_path):
    d = jeu(tmp_path, [question("Q001")], [attendu("Q001", "auteur: Naoki Urasawa")])
    _, b = bilans(base_catalogue, d)
    assert b["Q001"].statut == "a_revoir"


def test_scenariste_seul(base_catalogue, tmp_path):
    d = jeu(tmp_path, [question("Q001")], [attendu("Q001", "auteur: Takashi Nagasaki")])
    sortie, b = bilans(base_catalogue, d)
    assert b["Q001"].statut == "confirmee"
    assert sortie[0].confirmation == "auteur:scenariste"


def test_sans_reponse_ecrite_a_revoir(base_catalogue, tmp_path):
    d = jeu(tmp_path, [question("Q001")], [])
    _, b = bilans(base_catalogue, d)
    assert b["Q001"].statut == "a_revoir"


# --------------------------------------------------------------------------- #
#  Issues sans clé
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("reponse", "statut", "confirmation"),
    [
        ("titre: Tetsuwan Atom", "confirmee", "hors_catalogue_confirmee"),
        ("titre: Berserk", "a_revoir", "présente au catalogue"),
        ("titre: Kaibutsu", "a_revoir", "rattachée au catalogue"),
        ("titre: Zzyzx", "a_revoir", "inconnue de Kitsu"),
        ("auteur: Osamu Tezuka", "confirmee", "hors_catalogue_confirmee"),
    ],
)
def test_reconnue_hors_catalogue(
    base_catalogue, tmp_path, reponse, statut, confirmation
):
    d = jeu(
        tmp_path,
        [question("Q001", famille="F2", issue="reconnue_hors_catalogue")],
        [attendu("Q001", reponse, "")],
    )
    sortie, b = bilans(base_catalogue, d)
    assert b["Q001"].statut == statut
    assert confirmation in sortie[0].confirmation
    assert sortie[0].series_id == ""


@pytest.mark.parametrize(
    ("reponse", "statut"),
    [
        ("titre: Zzyzx le Magnifique", "confirmee"),
        ("auteur: Personne Inventée", "confirmee"),
        ("titre: Tetsuwan Atom", "a_revoir"),
        ("auteur: Osamu Tezuka", "a_revoir"),
        ("titre: Monster", "a_revoir"),
    ],
)
def test_inconnue(base_catalogue, tmp_path, reponse, statut):
    d = jeu(
        tmp_path,
        [question("Q001", famille="F7", mode="refus", issue="inconnue")],
        [attendu("Q001", reponse, "")],
    )
    _, b = bilans(base_catalogue, d)
    assert b["Q001"].statut == statut


# --------------------------------------------------------------------------- #
#  Écriture, recouvrement, périmètre de lecture
# --------------------------------------------------------------------------- #


def test_ecriture_idempotente(base_catalogue, tmp_path):
    d = jeu(
        tmp_path,
        [question("Q001", mode="proposition"), question("Q002")],
        [
            attendu("Q001", "auteur: Naoki Urasawa", "1"),
            attendu("Q002", "titre: Vagabond"),
        ],
    )
    sortie, _ = confirmer.executer(base_catalogue, d)
    confirmer.ecrire_attendus(d / "attendus.csv", sortie)
    premier = (d / "attendus.csv").read_bytes()
    sortie, _ = confirmer.executer(base_catalogue, d)
    confirmer.ecrire_attendus(d / "attendus.csv", sortie)
    assert (d / "attendus.csv").read_bytes() == premier
    assert premier.count(b"auteur: Naoki Urasawa") == 3


def test_recouvrement_signale(base_catalogue, tmp_path):
    d = jeu(
        tmp_path,
        [
            question(
                "Q001", famille="F3", mode="proposition", texte="un manga comme Monster"
            ),
            question(
                "Q002", famille="F3", mode="proposition", texte="un thriller glaçant"
            ),
        ],
        [attendu("Q001", "id: 2"), attendu("Q002", "id: 2")],
    )
    _, b = bilans(base_catalogue, d)
    assert b["Q001"].recouvrement == 1.0
    assert b["Q002"].recouvrement == 0.0


def test_l_outil_ne_lit_jamais_le_corpus():
    """Le jeu mesurera `bench` : l'outil qui le vérifie ne doit pas le lire."""
    for module in (catalogue, confirmer, regles):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert not re.search(r"\bbench\.\w", source), module.__name__


def test_session_en_lecture_seule(base_catalogue, tmp_path, monkeypatch):
    """Si l'outil tentait d'écrire en base, la session le refuserait."""
    d = jeu(tmp_path, [question("Q001")], [attendu("Q001", "titre: Berserk")])
    ecrit = []
    original = catalogue.Catalogue.charger

    def espion(cx):
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            cx.execute("CREATE TEMP TABLE t (x int)")
        cx.rollback()
        ecrit.append(True)
        return original(cx)

    monkeypatch.setattr(catalogue.Catalogue, "charger", staticmethod(espion))
    confirmer.executer(base_catalogue, d)
    assert ecrit == [True]


# --------------------------------------------------------------------------- #
#  Atteignabilité
# --------------------------------------------------------------------------- #


def test_atteignabilite(base_catalogue):
    with psycopg.connect(base_catalogue) as cx:
        cx.execute(
            "INSERT INTO manga.work_identity (series_id, kitsu_id) VALUES (1, '900'),"
            " (4, '905')"
        )
        for doc_key, source, series_id, kitsu_id in (
            ("ms_review:1", "ms_review", 3, None),
            ("kitsu:900", "kitsu_synopsis", None, 900),
            ("kitsu:902", "kitsu_synopsis", None, 902),
        ):
            cx.execute(
                "INSERT INTO bench.corpus_docs (doc_key, source, series_id, kitsu_id,"
                " doc_text) VALUES (%s, %s, %s, %s, 'texte')",
                (doc_key, source, series_id, kitsu_id),
            )
        for doc_key in ("ms_review:1", "kitsu:900"):  # kitsu:902 sans fragment
            cx.execute(
                "INSERT INTO bench.corpus_chunks (doc_key, chunk_index, chunk_text)"
                " VALUES (%s, 0, 'texte')",
                (doc_key,),
            )
        assert atteignabilite.atteignables(cx) == {1, 3}
        decomposition, defaut = atteignabilite.mesurer(cx)
    assert decomposition["catalogue"] == 6
    assert decomposition["atteignables"] == 2
    assert decomposition["au_niveau_document"] == 3  # la série 2, via kitsu:902
    assert defaut["liees_par_la_cascade"] == 3
    assert defaut["document_sans_fragment"] == 1  # 902
    assert defaut["sans_document_au_corpus"] == 1  # 905
    assert defaut["sans_synopsis_atteignable"] == 2  # 902 et 905


@pytest.mark.parametrize(
    "module", ["evaluation.confirmer", "evaluation.atteignabilite", "corpus.mesurer"]
)
def test_lecture_seule_des_la_premiere_transaction(
    base_catalogue, tmp_path, monkeypatch, module
):
    """Régression du 2026-09-29 : `SET SESSION CHARACTERISTICS` après connexion
    laissait la transaction COURANTE en écriture. La première requête de chaque
    outil de lecture doit déjà voir `transaction_read_only = on`."""
    import importlib

    cible = importlib.import_module(module)
    vu = []
    connecter = psycopg.connect

    def espion(*args, **kwargs):
        cx = connecter(*args, **kwargs)
        vu.append(cx.execute("SHOW transaction_read_only").fetchone()[0])
        return cx

    monkeypatch.setattr(cible.psycopg, "connect", espion)
    if module == "evaluation.confirmer":
        d = jeu(tmp_path, [question("Q001")], [attendu("Q001", "titre: Berserk")])
        cible.executer(base_catalogue, d)
    elif module == "evaluation.atteignabilite":
        cible.principal()
    else:
        cible.mesurer(base_catalogue)
    assert vu == ["on"]


# --------------------------------------------------------------------------- #
#  regle: — exécution sur base jetable (contrôles statiques : test_evaluation_regles)
# --------------------------------------------------------------------------- #


def poser_regle(dossier: Path, question_id: str, sql: str) -> None:
    (dossier / "regles").mkdir(exist_ok=True)
    (dossier / "regles" / f"{question_id}.sql").write_text(sql, encoding="utf-8")


REGLE_NOTES = """
SELECT series_id FROM manga.ms_series_enriched
WHERE series_members_rating >= 8
ORDER BY series_members_rating DESC, series_id
LIMIT 2
"""


def noter(dsn: str) -> None:
    with psycopg.connect(dsn) as cx:
        for series_id, note in ((1, 9.5), (2, 9.0), (3, 8.5), (4, 7.0)):
            cx.execute(
                "UPDATE manga.ms_series_enriched SET series_members_rating = %s"
                " WHERE series_id = %s",
                (note, series_id),
            )


def test_regle_executee_plafonnee_dans_son_ordre(base_catalogue, tmp_path):
    noter(base_catalogue)
    d = jeu(
        tmp_path,
        [question("Q001", famille="F10", mode="proposition")],
        [attendu("Q001", "regle: Q001", "1")],
    )
    poser_regle(d, "Q001", REGLE_NOTES)
    sortie, bilans = confirmer.executer(base_catalogue, d)
    assert bilans[0].statut == "confirmee"
    assert [(a.series_id, a.grade, a.confirmation) for a in sortie] == [
        ("1", "1", "regle"),
        ("2", "1", "regle"),
    ]


def test_regle_en_erreur_n_empeche_pas_la_suite(base_catalogue, tmp_path):
    noter(base_catalogue)
    d = jeu(
        tmp_path,
        [
            question("Q001", famille="F10", mode="proposition"),
            question("Q002", famille="F9", mode="proposition"),
            question("Q003"),
        ],
        [
            attendu("Q001", "regle: Q001", "1"),
            attendu("Q002", "regle: Q002", "1"),
            attendu("Q003", "titre: Berserk"),
        ],
    )
    poser_regle(
        d,
        "Q001",
        REGLE_NOTES.replace("series_members_rating >= 8", "colonne_inconnue > 1"),
    )
    poser_regle(
        d, "Q002", REGLE_NOTES.replace("SELECT series_id", "SELECT series_title")
    )
    _, bilans = confirmer.executer(base_catalogue, d)
    statuts = {b.question.question_id: (b.statut, " ".join(b.motifs)) for b in bilans}
    assert statuts["Q001"][0] == "a_revoir" and "SQL invalide" in statuts["Q001"][1]
    assert statuts["Q002"][0] == "a_revoir" and "series_id" in statuts["Q002"][1]
    assert statuts["Q003"][0] == "confirmee"


def test_regle_absente(base_catalogue, tmp_path):
    d = jeu(
        tmp_path,
        [question("Q001", famille="F10", mode="proposition")],
        [attendu("Q001", "regle: Q001", "1")],
    )
    _, bilans = confirmer.executer(base_catalogue, d)
    assert bilans[0].statut == "a_revoir"
    assert "introuvable" in " ".join(bilans[0].motifs)


# --------------------------------------------------------------------------- #
#  Clé combinée titre + auteur, et diagnostic du format source
# --------------------------------------------------------------------------- #


def test_titre_ambigu_leve_par_l_auteur(base_catalogue, tmp_path):
    with psycopg.connect(base_catalogue) as cx:
        cx.execute(
            "INSERT INTO manga.ms_series_enriched (series_id, series_title,"
            " series_dessinateur, series_scenariste)"
            " VALUES (7, 'Monster', 'Autre AUTEUR', 'Autre AUTEUR')"
        )
    d = jeu(
        tmp_path, [question("Q001", famille="F8")], [attendu("Q001", "titre: Monster")]
    )
    _, b = bilans(base_catalogue, d)
    assert b["Q001"].statut == "a_revoir"
    d = jeu(
        tmp_path,
        [question("Q001", famille="F8")],
        [attendu("Q001", "titre: Monster ; auteur: Naoki Urasawa")],
    )
    sortie, b = bilans(base_catalogue, d)
    assert b["Q001"].statut == "confirmee" and b["Q001"].series == {2: "2"}
    assert sortie[0].confirmation == "titre+auteur:title+auteur"


def test_diagnostic_source_liste_tout(base_catalogue, tmp_path):
    from evaluation.jeu import COLONNES_SOURCE

    lignes = [
        [
            "Q001",
            "t",
            "reconnaissance",
            "F1",
            "au_catalogue",
            "Berserk",
            "",
            "nouvelle",
            "n",
            "",
        ],
        [
            "Q002",
            "t",
            "proposition",
            "F5",
            "reconnue_hors_catalogue",
            "Berserk",
            "",
            "nouvelle",
            "n",
            "",
        ],
        [
            "Q003",
            "t",
            "reconnaissance",
            "F1",
            "au_catalogue",
            "Bersek",
            "",
            "nouvelle",
            "n",
            "",
        ],
    ]
    with (tmp_path / "jeu_evaluation_recherche.csv").open(
        "w", encoding="utf-8", newline=""
    ) as f:
        w = csv.writer(f)
        w.writerow(COLONNES_SOURCE)
        w.writerows(lignes)
    _, b = confirmer.executer(base_catalogue, tmp_path)
    statuts = {x.question.question_id: x.statut for x in b}
    assert statuts == {"Q001": "confirmee", "Q002": "invalide", "Q003": "de_cote"}
    motifs = " ".join(b[1].motifs)
    assert "reconnaissance" in motifs and "présente au catalogue" in motifs


def test_recherche_par_auteur_stricte(base_catalogue):
    from evaluation import auteur

    with psycopg.connect(base_catalogue) as cx:
        c = catalogue.Catalogue.charger(cx)
    assert [(i, t) for i, t, _ in auteur.lister(c, "naoki urasawa")] == [
        (3, "20th Century Boys"),
        (2, "Monster"),
        (4, "Pluto"),
    ]
    assert auteur.lister(c, "Naoki Urasaw") == []  # pas de recherche floue
    assert auteur.lister(c, "Takashi Nagasaki")[0][2] == "scenariste"


# --------------------------------------------------------------------------- #
#  Hors catalogue renforcé : tous les titres Kitsu, identifiants, auteurs
# --------------------------------------------------------------------------- #


def oeuvre_kitsu(dsn, kitsu_id, formes, staff=(), mal=None):
    with psycopg.connect(dsn) as cx:
        for forme in formes:
            cx.execute(
                "INSERT INTO manga.kitsu_formes (kitsu_id, forme, forme_norm,"
                " forme_type, subtype) VALUES (%s, %s, %s, 'title', 'manga')",
                (kitsu_id, forme, normaliser(forme)),
            )
        for personne in staff:
            cx.execute(
                "INSERT INTO manga.kitsu_staff (kitsu_id, personne, personne_norm)"
                " VALUES (%s, %s, %s)",
                (kitsu_id, personne, normaliser(personne)),
            )
        if mal:
            cx.execute(
                "INSERT INTO manga.kitsu_mappings (kitsu_id, external_site,"
                " external_id) VALUES (%s, 'myanimelist/manga', %s)",
                (kitsu_id, mal),
            )


def hors_catalogue(dsn, tmp_path, reponse):
    d = jeu(
        tmp_path,
        [question("Q001", famille="F5", issue="reconnue_hors_catalogue")],
        [attendu("Q001", reponse, "")],
    )
    return bilans(dsn, d)[1]["Q001"]


def test_un_autre_titre_kitsu_au_catalogue_bloque(base_catalogue, tmp_path):
    oeuvre_kitsu(base_catalogue, 905, ["Astro Boy", "Berserk"], ["Quelqu'un"])
    b = hors_catalogue(base_catalogue, tmp_path, "titre: Astro Boy")
    assert b.statut == "a_revoir"
    assert "titre Kitsu « Berserk »" in " ".join(b.motifs)


def test_un_identifiant_qui_mene_au_catalogue_bloque(base_catalogue, tmp_path):
    oeuvre_kitsu(base_catalogue, 906, ["Pluton"], ["Quelqu'un"], mal="777")
    with psycopg.connect(base_catalogue) as cx:
        cx.execute(
            "INSERT INTO manga.work_identity (series_id, mal_id) VALUES (4, '777')"
        )
    b = hors_catalogue(base_catalogue, tmp_path, "titre: Pluton")
    assert b.statut == "a_revoir"
    assert "par identifiant myanimelist/manga : série 4 « Pluto »" in " ".join(b.motifs)


def test_meme_auteur_signale_sans_bloquer(base_catalogue, tmp_path):
    oeuvre_kitsu(base_catalogue, 907, ["Oeuvre Inedite"], ["Kentaro MIURA"])
    b = hors_catalogue(base_catalogue, tmp_path, "titre: Oeuvre Inedite")
    assert b.statut == "confirmee"
    assert "même auteur au catalogue" in " ".join(b.signalements)
    assert "Berserk" in " ".join(b.signalements)


def test_auteur_inconnu_signale(base_catalogue, tmp_path):
    oeuvre_kitsu(base_catalogue, 908, ["Sans Auteur"])
    b = hors_catalogue(base_catalogue, tmp_path, "titre: Sans Auteur")
    assert b.statut == "confirmee"
    assert "aucun auteur au staff Kitsu" in " ".join(b.signalements)


def test_liste_des_hors_catalogue(base_catalogue):
    from evaluation import hors_catalogue as hc

    oeuvre_kitsu(base_catalogue, 905, ["Astro Boy", "Berserk"])
    oeuvre_kitsu(base_catalogue, 908, ["Sans Auteur"])
    with psycopg.connect(base_catalogue) as cx:
        for kid, rang in ((905, 1), (908, 2)):
            cx.execute(
                "INSERT INTO bench.corpus_docs (doc_key, source, kitsu_id, doc_text,"
                " metadata_json, title) VALUES (%s, 'kitsu_synopsis', %s, 'texte',"
                " %s, 't')",
                (
                    f"kitsu:{kid}",
                    kid,
                    json.dumps({"popularity_rank": rang, "subtype": "manga"}),
                ),
            )
        retenues, ecartees = hc.lister(cx, 5)
    assert [r[1] for r in retenues] == [908]
    assert ecartees["titre"] == 1
