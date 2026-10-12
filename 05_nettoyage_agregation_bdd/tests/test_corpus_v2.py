"""La construction du corpus v2 à côté du v1 (E3, étape 1, bloc D), sur base
JETABLE : les trois différences et seulement elles, les pseudonymes contrôlés avant
d'écrire, le v1 intact, le rejeu sans écriture, la clôture, le rechargement à
l'identique."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from conftest import MIGRATIONS, lire

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_corpus_construire import (  # noqa: E402, F401
    CORPS_LONG,
    charger,
    ecrire_listes,
    ecrire_raw,
    jeu,
    peupler,
    run_kitsu,
)

from corpus import construire, construire_v2, pseudonymes  # noqa: E402
from evaluation import atteignabilite  # noqa: E402

#: Les résumés de série du raw de juillet (et du catalogue) : un retenu en
#: français, un en anglais, et un cas par motif d'exclusion.
RESUMES = {
    1: "Dans un monde lointain, une jeune fille part à la recherche de son frère"
    " disparu. Elle traverse des montagnes et des déserts.",
    2: "Présentation de la série. " + CORPS_LONG,  # contient la critique 106 entière
    3: "Trop court.",
    4: "Un extrait de chronique bien tourné, qui donne envie de lire la suite."
    " LA CHRONIQUE COMPLETE...",
    5: "Un extrait de chronique... LIRE LA CHRONIQUE COMPLETE... Puis une vraie"
    " présentation de la série ici.",
    6: "Une histoire où Plume rencontre un dragon dans une forêt enchantée.",
    7: "Pas de résumé",
    9: "A young pirate sets sail across the ocean to find a legendary treasure"
    " with his crew.",
}
#: Au catalogue seulement : une série que la collecte de juillet n'a pas revue.
HORS_JUILLET = {
    8: "Une série que la collecte de juillet n'a pas revue, au résumé long."
}


def ecrire_volumes(dossier: Path, resumes=RESUMES) -> Path:
    dossier.mkdir(parents=True, exist_ok=True)
    raw = dossier / "manga_sanctuary_volumes.jsonl"
    lignes = []
    for sid, texte in resumes.items():
        for tome in (1, 2):  # deux volumes, le même résumé de série
            lignes.append(
                json.dumps(
                    {
                        "series_id": str(sid),
                        "volume_number": str(tome),
                        "series_synopsis": texte,
                    }
                )
                + "\n"
            )
    lignes.append(json.dumps({"series_id": "10", "series_synopsis": None}) + "\n")
    raw.write_text("".join(lignes), encoding="utf-8")
    sha = hashlib.sha256(raw.read_bytes()).hexdigest()
    (dossier / "MANIFEST.md").write_text(
        "| Fichier | Lignes | Octets | SHA-256 |\n|---|---:|---:|---|\n"
        f"| `{raw.name}` | {len(lignes)} | {raw.stat().st_size} | `{sha}` |\n",
        encoding="utf-8",
    )
    return raw


def catalogue(url: str, resumes=RESUMES) -> None:
    with psycopg.connect(url) as cx:
        for sid, texte in {**resumes, **HORS_JUILLET, 10: None}.items():
            cx.execute(
                "INSERT INTO manga.ms_series_enriched (series_id, series_title,"
                " series_synopsis) VALUES (%s, %s, %s) ON CONFLICT (series_id)"
                " DO UPDATE SET series_synopsis = EXCLUDED.series_synopsis",
                (sid, f"Serie {sid}", texte),
            )


@pytest.fixture
def jeu_v2(jeu, tmp_path):  # noqa: F811
    charger(jeu)  # le v1, d'abord
    catalogue(jeu["url"])
    config = copy.deepcopy(construire_v2.charger_config())
    config["resumes"]["raw"] = str(ecrire_volumes(tmp_path / "volumes"))
    return jeu | {"config": config}


def batir(j, **options):
    return construire_v2.executer(
        j["url"],
        raw=j["raw"],
        donnees=j["donnees"],
        run_kitsu=j["kitsu"],
        config=j["config"],
        **options,
    )


def empreinte(url: str, corpus_id: str) -> tuple:
    with psycopg.connect(url) as cx:
        return cx.execute(construire.SQL_EMPREINTE, {"corpus": corpus_id}).fetchone()


# --------------------------------------------------------------------------- #
#  Les trois différences, et le reste à l'identique
# --------------------------------------------------------------------------- #


def test_le_v2_se_construit_a_cote_du_v1(jeu_v2):
    v1 = empreinte(jeu_v2["url"], "v1")
    r = batir(jeu_v2)
    assert r["issue"].endswith("VALIDÉ"), r["issue"]
    assert r["documents"] == {"ms_review": 3, "kitsu_synopsis": 3, "ms_synopsis": 2}
    assert r["resumes"]["exclus"] == {
        "1_absent_du_raw_de_juillet": 1,
        "2_moins_de_50": 1,
        "3_faux_resume": 3,
        "4_pseudonyme": 1,
    }
    assert r["resumes"]["langues"] == {"fr": 1, "en": 1}
    assert empreinte(jeu_v2["url"], "v1") == v1, "le v1 n'est ni lu ni touché"
    assert lire(
        jeu_v2["url"],
        "SELECT corpus_id, clos_le IS NOT NULL, nb_documents FROM bench.corpus"
        " WHERE corpus_id = 'v2'",
    ) == [("v2", True, 8)]


def test_le_texte_des_critiques_n_est_plus_masque(jeu_v2):
    batir(jeu_v2)
    textes = dict(
        lire(
            jeu_v2["url"],
            "SELECT corpus_id, doc_text FROM bench.corpus_docs"
            " WHERE doc_key = 'ms_review:101'",
        )
    )
    assert pseudonymes.JETON in textes["v1"] and pseudonymes.JETON not in textes["v2"]
    differents = lire(
        jeu_v2["url"],
        "SELECT a.doc_key FROM bench.corpus_docs a JOIN bench.corpus_docs b"
        " ON b.doc_key = a.doc_key AND b.corpus_id = 'v2'"
        " WHERE a.corpus_id = 'v1' AND a.doc_text <> b.doc_text",
    )
    assert differents == [("ms_review:101",)], "seul le texte masqué en v1 diffère"


def test_le_resume_porte_son_titre_et_sa_langue(jeu_v2):
    batir(jeu_v2)
    ((texte, meta),) = lire(
        jeu_v2["url"],
        "SELECT doc_text, metadata_json FROM bench.corpus_docs"
        " WHERE corpus_id = 'v2' AND doc_key = 'ms_synopsis:9'",
    )
    assert texte == "Résumé Manga Serie 9\n" + RESUMES[9]
    assert meta["langue"] == "en" and meta["series_id"] == 9


def test_le_decoupage_par_phrases_pour_tous_les_types(jeu_v2):
    batir(jeu_v2)
    strategies = lire(
        jeu_v2["url"],
        "SELECT s.name FROM bench.corpus c JOIN bench.chunking_strategies s"
        " USING (chunking_id) WHERE c.corpus_id = 'v2'",
    )
    assert strategies == [("phrases_1200_recouvrement_200",)]
    # La critique longue : coupée aux phrases, et non à 1 200 caractères pile.
    longs = lire(
        jeu_v2["url"],
        "SELECT corpus_id, count(*) FROM bench.corpus_chunks"
        " WHERE doc_key = 'ms_review:106' GROUP BY 1 ORDER BY 1",
    )
    assert dict(longs)["v2"] >= 1


def test_le_rattachement_atteint_les_series_des_resumes(jeu_v2):
    batir(jeu_v2)
    with psycopg.connect(jeu_v2["url"]) as cx:
        assert {1, 9} <= atteignabilite.atteignables(cx, "v2")
        assert 9 not in atteignabilite.atteignables(cx, "v1")
        sources = {r[1] for r in atteignabilite.rattachement(cx, "v2")}
    assert sources == {"ms_review", "kitsu_synopsis", "ms_synopsis"}


# --------------------------------------------------------------------------- #
#  Rejeu, essai, clôture
# --------------------------------------------------------------------------- #


def fragments_v2(url: str):
    return lire(
        url,
        "SELECT chunk_id, xmin::text FROM bench.corpus_chunks WHERE corpus_id = 'v2'"
        " ORDER BY 1",
    )


def test_le_rejeu_n_ecrit_rien(jeu_v2):
    premier = batir(jeu_v2)
    avant = fragments_v2(jeu_v2["url"])
    rejeu = batir(jeu_v2)
    assert all(not v for v in rejeu["ecritures"].values()), rejeu["ecritures"]
    assert not rejeu["clos_maintenant"]
    assert rejeu["empreinte_apres"] == premier["empreinte_apres"]
    assert fragments_v2(jeu_v2["url"]) == avant, "aucune ligne réécrite (xmin)"


def test_le_dry_run_n_ecrit_rien(jeu_v2):
    r = batir(jeu_v2, dry_run=True)
    assert "ANNULÉ" in r["issue"]
    assert lire(
        jeu_v2["url"], "SELECT count(*) FROM bench.corpus WHERE corpus_id = 'v2'"
    ) == [(0,)]


def test_un_v2_clos_ne_se_reconstruit_pas_autrement(jeu_v2, tmp_path):
    batir(jeu_v2)
    autres = RESUMES | {1: RESUMES[1] + " Une phrase de plus, ajoutée ensuite."}
    catalogue(jeu_v2["url"], autres)
    jeu_v2["config"]["resumes"]["raw"] = str(ecrire_volumes(tmp_path / "v", autres))
    with pytest.raises(psycopg.errors.RaiseException, match="le corpus v2 est clos"):
        batir(jeu_v2)


# --------------------------------------------------------------------------- #
#  Les gardes : pseudonymes, raw contre catalogue
# --------------------------------------------------------------------------- #


def test_un_pseudonyme_inconnu_dans_une_critique_arrete_tout(jeu_v2):
    with psycopg.connect(jeu_v2["url"]) as cx:
        cx.execute(
            "UPDATE manga.ms_reviews_all SET review_body = review_body || ' Tori'"
            " WHERE review_url LIKE '%%id=102'"
        )
    with pytest.raises(construire.ErreurChargement, match="pseudonymes"):
        batir(jeu_v2)
    assert lire(
        jeu_v2["url"], "SELECT count(*) FROM bench.corpus WHERE corpus_id = 'v2'"
    ) == [(0,)]


def test_un_resume_du_raw_qui_differe_du_catalogue_arrete_tout(jeu_v2):
    catalogue(jeu_v2["url"], RESUMES | {1: RESUMES[1] + " (modifié)"})
    with pytest.raises(construire.ErreurChargement, match="résumé du raw"):
        batir(jeu_v2)


# --------------------------------------------------------------------------- #
#  Le rechargement sur une autre base : même contenu, quel que soit l'ordre
# --------------------------------------------------------------------------- #


def autre_base(conteneur: str) -> str:
    nom = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(conteneur, autocommit=True) as cx:
        cx.execute(f'CREATE DATABASE "{nom}"')
    dsn = conteneur.rsplit("/", 1)[0] + f"/{nom}"
    sortie = subprocess.run(
        ["uv", "run", "python", "migrate.py", "up"],
        cwd=MIGRATIONS,
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        | {"DATABASE_URL": dsn},
    )
    assert sortie.returncode == 0, sortie.stderr
    return dsn


def test_rechargement_a_l_identique_quel_que_soit_l_ordre(jeu_v2, conteneur, tmp_path):
    batir(jeu_v2)
    reference = empreinte(jeu_v2["url"], "v2")

    url = autre_base(conteneur)
    try:
        peupler(url)
        with psycopg.connect(url) as cx:
            # Une autre numérotation : les chunk_id ne font pas partie du contenu.
            cx.execute("SELECT setval('bench.corpus_chunks_chunk_id_seq', 5000)")
        (tmp_path / "b").mkdir()
        autre = jeu_v2 | {
            "url": url,
            "raw": ecrire_raw(tmp_path / "b"),
            "donnees": ecrire_listes(tmp_path / "b"),
            "kitsu": run_kitsu(tmp_path / "b"),
        }
        catalogue(url)
        charger(autre)
        batir(autre)
        assert empreinte(url, "v2") == reference
        # Un autre ordre physique : la table réécrite dans l'ordre des empreintes.
        with psycopg.connect(url, autocommit=True) as cx:
            cx.execute("CREATE INDEX essai_ordre ON bench.corpus_chunks (chunk_hash)")
            cx.execute("CLUSTER bench.corpus_chunks USING essai_ordre")
        assert empreinte(url, "v2") == reference
    finally:
        with psycopg.connect(conteneur, autocommit=True) as cx:
            cx.execute(
                f'DROP DATABASE IF EXISTS "{url.rsplit("/", 1)[1]}" WITH (FORCE)'
            )
