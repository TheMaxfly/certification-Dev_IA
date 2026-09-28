"""Reconstruction du corpus, de bout en bout, sur base JETABLE migrée.

Un jeu minuscule, construit pour que chaque clause de la règle ait quelque
chose à écarter, et chaque contrôle du §7 quelque chose à voir :

  101  critique qui remercie un membre       → retenue, pseudonyme MASQUÉ
  102  texte publié deux fois (avec 103)     → UN document (S6)
  104  corps blanc                           → écartée (S3)
  105  hors snapshot                         → écartée (S2)
  106  corps long, coupure à 1 200 au milieu d'un mot qui commence comme un
       pseudonyme                           → retenue, COUPURE rapportée
  107  auteur « Tori », hors snapshot        → fournit un pseudonyme homonyme

  La part Kitsu vient d'un run miniature, manifeste compris (règle K1) :
  kitsu:7   manga, « Tori » est un oiseau, auteur, « (Source: MU) »
                                             → retenu, homonyme ADMIS
  kitsu:8   manga, synopsis de 6 caractères  → retenu, sans fragment (plancher 50)
  kitsu:9   roman non rattaché               → écarté (type)
  kitsu:10  manga sans synopsis              → écarté (K1)
  kitsu:11  roman rattaché par la cascade    → RÉADMIS
  kitsu:12  doujin                           → écarté, toujours
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

import psycopg
import pytest
from conftest import ecrire_run_kitsu, lire

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from corpus import construire, decoupage  # noqa: E402
from corpus.pseudonymes import JETON, empreinte  # noqa: E402

URL = "https://www.manga-sanctuary.com/fiche_serie_critique.php?id="
#: 29 caractères de préfixe (« Critique Manga Serie Deux #3\n ») + 1 167 : le mot
#: commence au caractère 1 196, la coupure à 1 200 en garde quatre lettres.
CORPS_LONG = "x" * 1166 + " Toriko" + " suite" * 20

CRITIQUES = [
    (101, 1, 1, "Plume", "Belle critique. Merci a Zorglub pour cette critique"),
    (102, 1, 2, "Plume", "Texte identique publie deux fois"),
    (103, 1, 2, "Plume", "Texte identique publie deux fois"),
    (104, 2, 1, "Zorglub", "   "),
    (105, 2, 1, "Plume", "Hors snapshot"),
    (106, 2, 3, "Plume", CORPS_LONG),
    (107, 1, 3, "Tori", "Une critique hors snapshot"),
]
SNAPSHOT = [101, 102, 103, 104, 106]
KITSU = [
    (
        7,
        "manga",
        "Birds",
        {"en": "Birds"},
        "The bird Tori flies over the sea every morning, a long synopsis. (Source: MU)",
        ["Fantasy"],
        [],
    ),
    (8, "manga", "Mini", {}, "Court.", [], []),
    (9, "novel", "Roman", {}, "Un roman au synopsis assez long pour compter.", [], []),
    (10, "manga", "Muet", {}, "", [], []),
    (
        11,
        "novel",
        "Rattache",
        {},
        "Un roman que la cascade rattache au catalogue.",
        [],
        [],
    ),
    (
        12,
        "doujin",
        "Doujin",
        {},
        "Un doujin au synopsis assez long pour compter.",
        [],
        [],
    ),
]
STAFF = {7: [("Naoki Urasawa", "Story & Art")]}
MASQUAGE = [("ms_review:101", empreinte("Zorglub"), "remerciement")]
HOMONYMES = [("kitsu:7", empreinte("Tori"), "provenance_kitsu")]


def peupler(dsn: str, critiques=CRITIQUES) -> None:
    with psycopg.connect(dsn) as cx:
        cx.execute(
            "INSERT INTO manga.ms_series_enriched (series_id, series_title)"
            " VALUES (1, 'Serie Un'), (2, 'Serie Deux')"
        )
        for site_id, serie, tome, auteur, corps in critiques:
            cx.execute(
                "INSERT INTO manga.ms_reviews_all"
                " (series_id, volume_number, review_url, review_author, review_body)"
                " VALUES (%s, %s, %s, %s, %s)",
                (serie, tome, f"{URL}{site_id}", auteur, corps),
            )
        cx.execute(
            "INSERT INTO manga.work_identity (series_id, kitsu_id) VALUES (1, '11')"
        )


def ecrire_raw(dossier: Path, site_ids=SNAPSHOT) -> Path:
    raw = dossier / "manga_sanctuary_reviews.jsonl"
    raw.write_text(
        "".join(json.dumps({"review_url": f"{URL}{i}"}) + "\n" for i in site_ids),
        encoding="utf-8",
    )
    sha = hashlib.sha256(raw.read_bytes()).hexdigest()
    (dossier / "MANIFEST.md").write_text(
        "| Fichier | Lignes | Octets | SHA-256 |\n|---|---:|---:|---|\n"
        f"| `{raw.name}` | {len(site_ids)} | {raw.stat().st_size} | `{sha}` |\n",
        encoding="utf-8",
    )
    return raw


def ecrire_listes(dossier: Path, masquage=MASQUAGE, homonymes=HOMONYMES) -> Path:
    for nom, colonne, lignes in (
        ("corpus_references_masquees.csv", "nature", masquage),
        ("corpus_homonymes_admis.csv", "motif", homonymes),
    ):
        with (dossier / nom).open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["doc_key", "empreinte_pseudo", colonne])
            w.writerows(lignes)
    return dossier


def run_kitsu(tmp_path: Path) -> Path:
    return ecrire_run_kitsu(tmp_path / "kitsu", KITSU, STAFF)


@pytest.fixture
def jeu(base, tmp_path):
    peupler(base)
    return {
        "url": base,
        "raw": ecrire_raw(tmp_path),
        "donnees": ecrire_listes(tmp_path),
        "kitsu": run_kitsu(tmp_path),
    }


def charger(jeu, **options):
    return construire.executer(
        jeu["url"],
        raw=jeu["raw"],
        donnees=jeu["donnees"],
        run_kitsu=jeu["kitsu"],
        **options,
    )


# --------------------------------------------------------------------------- #
#  La règle
# --------------------------------------------------------------------------- #


def test_chargement_applique_la_regle(jeu):
    r = charger(jeu)
    assert r["issue"].endswith("VALIDÉ"), r["issue"]
    assert r["clauses"] == {
        "S1 source": 7,
        "S2 snapshot": 5,
        "S3 corps": 4,
        "S5 série": 4,
        "S6 doublons": 3,
    }
    assert lire(jeu["url"], "SELECT doc_key FROM bench.corpus_docs ORDER BY 1") == [
        ("kitsu:11",),
        ("kitsu:7",),
        ("kitsu:8",),
        ("ms_review:101",),
        ("ms_review:102",),
        ("ms_review:106",),
    ]


def test_doublons_fusionnes_sous_le_plus_petit_site_id(jeu):
    charger(jeu)
    [(meta,)] = lire(
        jeu["url"],
        "SELECT metadata_json FROM bench.corpus_docs WHERE doc_key = 'ms_review:102'",
    )
    assert meta["site_ids"] == [102, 103]
    assert meta["volumes"] == [2]


def test_forme_du_document_et_aucun_auteur(jeu):
    charger(jeu)
    [(texte, titre, serie, meta)] = lire(
        jeu["url"],
        "SELECT doc_text, title, series_id, metadata_json FROM bench.corpus_docs"
        " WHERE doc_key = 'ms_review:102'",
    )
    assert texte == "Critique Manga Serie Un #2\nTexte identique publie deux fois"
    assert (titre, serie) == ("Serie Un", 1)
    assert not {"review_author", "review_url", "review_title"} & set(meta)


def test_reference_a_un_membre_masquee(jeu):
    r = charger(jeu)
    [(texte,)] = lire(
        jeu["url"],
        "SELECT doc_text FROM bench.corpus_docs WHERE doc_key = 'ms_review:101'",
    )
    assert JETON in texte and "Zorglub" not in texte
    assert r["cible"]["masquage_remplacements"] == 1
    assert r["controles"]["7.4 fuites"] == []


def test_coupure_de_fragment_rapportee_sans_bloquer(jeu):
    r = charger(jeu)
    [(texte,)] = lire(
        jeu["url"],
        "SELECT doc_text FROM bench.corpus_docs WHERE doc_key = 'ms_review:106'",
    )
    assert decoupage.decouper(texte)[0][2].endswith(" Tori")  # la prémisse du test
    assert r["controles"]["7.4 coupures de fragment"] == [
        ("ms_review:106", empreinte("Tori"))
    ]
    assert r["issue"].endswith("VALIDÉ")


def test_plancher_de_decembre_compte(jeu):
    r = charger(jeu)
    assert r["decoupe"]["sans_fragment"] == {"kitsu_synopsis": 1}
    assert lire(
        jeu["url"], "SELECT count(*) FROM bench.corpus_chunks WHERE doc_key = 'kitsu:8'"
    ) == [(0,)]


# --------------------------------------------------------------------------- #
#  §7.2 et §7.3
# --------------------------------------------------------------------------- #


def fragments(url):
    return lire(
        url, "SELECT chunk_id, doc_key, chunk_index FROM bench.corpus_chunks ORDER BY 1"
    )


def test_rejeu_n_ecrit_rien(jeu):
    premier = charger(jeu)
    avant = fragments(jeu["url"])
    second = charger(jeu)
    assert all(not v for v in second["ecritures"].values()), second["ecritures"]
    assert second["empreinte_avant"] == second["empreinte_apres"]
    assert second["empreinte_apres"] == premier["empreinte_apres"]
    assert fragments(jeu["url"]) == avant, "un rejeu ne renumérote aucun fragment"


def test_rechargement_complet_identique_et_annule(jeu):
    charger(jeu)
    avant = fragments(jeu["url"])
    r = charger(jeu, a_blanc=True)
    assert r["ecritures"]["vidage"]["ms_review"] == 3
    assert r["empreinte_avant"] == r["empreinte_apres"]
    assert fragments(jeu["url"]) == avant, "le mode à blanc doit être annulé"


def test_dry_run_n_ecrit_rien(jeu):
    r = charger(jeu, dry_run=True)
    assert "ANNULÉ" in r["issue"]
    assert lire(jeu["url"], "SELECT count(*) FROM bench.corpus_docs") == [(0,)]


def test_documents_hors_regle_retires_avec_leurs_qrels(jeu):
    with psycopg.connect(jeu["url"]) as cx:
        cx.execute(
            "INSERT INTO bench.corpus_docs (doc_key, source, doc_text)"
            " VALUES ('ms_hybrid:1', 'ms_hybrid', 'ancien')"
        )
        cx.execute("INSERT INTO bench.queries (query_id, query_text) VALUES (1, 'q')")
        cx.execute(
            "INSERT INTO bench.qrels (query_id, doc_key) VALUES (1, 'ms_hybrid:1')"
        )
    r = charger(jeu)
    assert r["ecritures"]["docs retirés"] == {"ms_hybrid": 1}
    assert lire(jeu["url"], "SELECT count(*) FROM bench.qrels") == [(0,)]
    assert lire(jeu["url"], "SELECT count(*) FROM bench.queries") == [(1,)]


# --------------------------------------------------------------------------- #
#  Arrêts
# --------------------------------------------------------------------------- #


def test_fuite_annule_tout(base, tmp_path):
    peupler(base, CRITIQUES + [(108, 1, 4, "Plume", "Comme le dit Zorglub, super.")])
    jeu = {
        "url": base,
        "raw": ecrire_raw(tmp_path, SNAPSHOT + [108]),
        "donnees": ecrire_listes(tmp_path),
        "kitsu": run_kitsu(tmp_path),
    }
    r = charger(jeu)
    assert r["echecs"] and "ANNULÉ" in r["issue"]
    assert r["controles"]["7.4 fuites"] == [
        ("ms_review:108", empreinte("Zorglub"), "document")
    ]
    assert lire(base, "SELECT count(*) FROM bench.corpus_docs") == [(0,)]


def test_raw_altere_refuse(jeu):
    with jeu["raw"].open("a", encoding="utf-8") as f:
        f.write(json.dumps({"review_url": f"{URL}999"}) + "\n")
    with pytest.raises(construire.ErreurChargement, match="G5"):
        charger(jeu)


def test_critique_sans_serie_arrete(base, tmp_path):
    peupler(base)
    with psycopg.connect(base) as cx:
        cx.execute(
            "INSERT INTO manga.ms_reviews_all (series_id, review_url, review_body)"
            " VALUES (NULL, %s, 'corps')",
            (f"{URL}109",),
        )
    jeu = {
        "url": base,
        "raw": ecrire_raw(tmp_path, SNAPSHOT + [109]),
        "donnees": ecrire_listes(tmp_path),
        "kitsu": run_kitsu(tmp_path),
    }
    with pytest.raises(construire.ErreurChargement, match="G3"):
        charger(jeu)


def test_liste_perimee_arrete(base, tmp_path):
    peupler(base)
    jeu = {
        "url": base,
        "raw": ecrire_raw(tmp_path),
        "donnees": ecrire_listes(
            tmp_path, masquage=[("ms_review:999", empreinte("Zorglub"), "signature")]
        ),
        "kitsu": run_kitsu(tmp_path),
    }
    with pytest.raises(construire.ErreurChargement, match="D5"):
        charger(jeu)
    assert lire(base, "SELECT count(*) FROM bench.corpus_docs") == [(0,)]


# --------------------------------------------------------------------------- #
#  La part Kitsu (règle du 2026-09-29)
# --------------------------------------------------------------------------- #


def test_part_kitsu_selon_k1(jeu):
    r = charger(jeu)
    b = r["cible"]["kitsu"]
    assert (b.oeuvres, b.retenues) == (6, 3)
    assert dict(b.exclues_sous_type) == {"novel": 1, "doujin": 1}
    assert dict(b.sur_rattachement_admises) == {"novel": 1}
    assert b.exclues_sans_synopsis == 1


def test_document_kitsu_au_gabarit_de_decembre(jeu):
    charger(jeu)
    [(texte, meta, boost, titre)] = lire(
        jeu["url"],
        "SELECT doc_text, metadata_json, boost_score, title FROM bench.corpus_docs"
        " WHERE doc_key = 'kitsu:7'",
    )
    assert texte == (
        "Titres: Birds | Birds\n"
        "Auteurs: Naoki Urasawa (Scénario & Dessin)\n"
        'Tags: ["Fantasy"]\n'
        "Synopsis: The bird Tori flies over the sea every morning, a long synopsis."
    )
    assert meta["source_citee"] == ["MU"]
    assert boost is None and "trending_pos" not in meta
    assert (titre, meta["subtype"]) == ("Birds", "manga")


def test_run_kitsu_altere_refuse(jeu):
    with (jeu["kitsu"] / "manga.ndjson").open("a", encoding="utf-8") as f:
        f.write("{}\n")
    with pytest.raises(construire.ErreurChargement, match="Kitsu"):
        charger(jeu)
