"""Re-parse local des sitelinks jawiki (D0-2), sans réseau.

Le point à garantir n'est pas seulement que wiki_ja se remplit : c'est que ce
module ne télécharge RIEN et n'écrit RIEN dans le répertoire d'entités. Un
dossier vide doit donc échouer bruyamment plutôt que d'aller chercher la donnée.
"""

from __future__ import annotations

import json

import psycopg
import pytest
from conftest import lire


def _ecrire_entites(dossier, entites: dict) -> None:
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "entities_000000.json").write_text(
        json.dumps({"entities": entites}, ensure_ascii=False), encoding="utf-8"
    )


def _sitelinks(**wikis) -> dict:
    return {"sitelinks": {nom: {"title": titre} for nom, titre in wikis.items()}}


# --------------------------------------------------------------------------- #
# Extraction locale
# --------------------------------------------------------------------------- #
def test_extraction_ne_retient_que_jawiki(tmp_path):
    from identity.reparse_jawiki import extraire_jawiki

    dossier = tmp_path / "entities"
    _ecrire_entites(
        dossier,
        {
            "Q1": _sitelinks(jawiki="ドラゴンボール", enwiki="Dragon Ball"),
            "Q2": _sitelinks(enwiki="Naruto"),  # pas de jawiki
            "Q3": {"missing": ""},  # entité supprimée
        },
    )
    assert extraire_jawiki(dossier) == {"Q1": "ドラゴンボール"}


def test_dossier_sans_lot_echoue_plutot_que_de_telecharger(tmp_path):
    from identity.reparse_jawiki import ErreurReparse, extraire_jawiki

    vide = tmp_path / "entities"
    vide.mkdir()
    with pytest.raises(ErreurReparse):
        extraire_jawiki(vide)


# --------------------------------------------------------------------------- #
# Chargement en base
# --------------------------------------------------------------------------- #
@pytest.fixture
def reparse(base, tmp_path):
    with psycopg.connect(base, autocommit=True) as cx:
        cx.execute(
            "INSERT INTO manga.wd_pivot (qid, wiki_fr, wiki_en) "
            "VALUES ('Q1', 'Dragon Ball (fr)', 'Dragon Ball')"
        )
        # Q2 n'a ni fr ni en : c'est le gisement « ja seulement ».
        cx.execute("INSERT INTO manga.wd_pivot (qid) VALUES ('Q2')")

    dossier = tmp_path / "entities"
    _ecrire_entites(
        dossier,
        {
            "Q1": _sitelinks(jawiki="ドラゴンボール", enwiki="Dragon Ball"),
            "Q2": _sitelinks(jawiki="ナルト"),
            # présent sur disque mais hors référentiel : ignoré sans erreur
            "Q999": _sitelinks(jawiki="幽霊"),
        },
    )
    from identity import reparse_jawiki

    reparse_jawiki.charger(entites_dir=dossier)
    return base, dossier


def test_wiki_ja_est_rempli(reparse):
    dsn, _ = reparse
    assert lire(dsn, "SELECT qid, wiki_ja FROM manga.wd_pivot ORDER BY qid") == [
        ("Q1", "ドラゴンボール"),
        ("Q2", "ナルト"),
    ]


def test_les_autres_sitelinks_ne_sont_pas_touches(reparse):
    dsn, _ = reparse
    assert lire(dsn, "SELECT wiki_fr, wiki_en FROM manga.wd_pivot WHERE qid='Q1'") == [
        ("Dragon Ball (fr)", "Dragon Ball")
    ]


def test_qid_hors_referentiel_ignore(reparse):
    dsn, _ = reparse
    assert lire(dsn, "SELECT count(*) FROM manga.wd_pivot WHERE qid='Q999'") == [(0,)]


def test_ja_seulement_identifiable(reparse):
    dsn, _ = reparse
    assert lire(
        dsn,
        "SELECT qid FROM manga.wd_pivot WHERE wiki_ja IS NOT NULL "
        "AND wiki_fr IS NULL AND wiki_en IS NULL",
    ) == [("Q2",)]


def test_rejeu_ne_change_rien(reparse):
    dsn, dossier = reparse
    from identity import reparse_jawiki

    avant = lire(dsn, "SELECT qid, wiki_ja FROM manga.wd_pivot ORDER BY qid")
    reparse_jawiki.charger(entites_dir=dossier)
    assert lire(dsn, "SELECT qid, wiki_ja FROM manga.wd_pivot ORDER BY qid") == avant
