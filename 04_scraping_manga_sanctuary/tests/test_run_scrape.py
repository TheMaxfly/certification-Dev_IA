from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_scrape.py"
SPEC = importlib.util.spec_from_file_location("run_scrape", SCRIPT_PATH)
run_scrape = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(run_scrape)


@pytest.fixture
def raw_dir(tmp_path, monkeypatch) -> Path:
    """Isole les snapshots mensuels du vrai data/raw/ du module."""
    directory = tmp_path / "raw"
    directory.mkdir()
    monkeypatch.setattr(run_scrape, "RAW_DIR", directory)
    return directory


def ecrire_snapshot(raw_dir: Path, month: str, filename: str, lignes: int) -> None:
    dossier = raw_dir / month
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / filename).write_text("{}\n" * lignes, encoding="utf-8")


def test_validate_and_deduplicate_keeps_latest_url(tmp_path):
    source = tmp_path / "source.jsonl"
    destination = tmp_path / "destination.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps({"volume_url": "https://example/a", "version": 1}),
                json.dumps({"volume_url": "https://example/b", "version": 1}),
                json.dumps({"volume_url": "https://example/a", "version": 2}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    count, duplicates = run_scrape.validate_and_deduplicate_jsonl(
        source, destination, "volume_url"
    )

    rows = [
        json.loads(line)
        for line in destination.read_text(encoding="utf-8").splitlines()
    ]
    assert (count, duplicates) == (2, 1)
    assert rows[0]["version"] == 2


def test_validation_refuse_une_ligne_sans_cle(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps({"volume_title": "sans url"}) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="volume_url"):
        run_scrape.validate_and_deduplicate_jsonl(
            source, tmp_path / "out.jsonl", "volume_url"
        )


def test_plancher_calcule_face_au_dernier_snapshot(raw_dir):
    ecrire_snapshot(raw_dir, "2025-12", "manga_sanctuary_volumes.jsonl", 89_188)
    ecrire_snapshot(raw_dir, "2025-12", "manga_sanctuary_reviews.jsonl", 6_749)

    required = run_scrape.required_counts("2026-07", {"volumes": None, "reviews": None})

    # 95 % du dernier snapshot : au-delà de 5 % de perte, c'est une régression.
    assert required == {"volumes": 84_728, "reviews": 6_411}


def test_garde_fou_absolu_quand_aucun_snapshot(raw_dir):
    required = run_scrape.required_counts("2026-07", {"volumes": None, "reviews": None})

    assert required == {"volumes": 80_000, "reviews": 6_000}


def test_snapshot_du_mois_cible_ignore(raw_dir):
    """Le mois en cours est la cible : s'en servir de plancher ferait dépendre
    l'exigence d'un run précédent, éventuellement partiel."""
    ecrire_snapshot(raw_dir, "2026-07", "manga_sanctuary_volumes.jsonl", 200_000)

    required = run_scrape.required_counts("2026-07", {"volumes": None, "reviews": None})

    assert required["volumes"] == 80_000


def test_seuil_explicite_prioritaire(raw_dir):
    ecrire_snapshot(raw_dir, "2025-12", "manga_sanctuary_volumes.jsonl", 89_188)

    required = run_scrape.required_counts("2026-07", {"volumes": 10, "reviews": None})

    assert required["volumes"] == 10


def test_la_reprise_reutilise_le_job_dir_du_run(tmp_path):
    """Le JOBDIR est la seule mémoire de ce qui a déjà été demandé : en donner
    un neuf à la reprise ferait repartir des index et re-crawler l'acquis
    (constaté sur le site : 17 URLs redemandées sur 66)."""
    autre_run = tmp_path / "autre"

    assert run_scrape.resolve_job_dir(tmp_path) == tmp_path / "job"
    assert run_scrape.resolve_job_dir(autre_run) == autre_run / "job"


def test_chaque_run_a_son_propre_job_dir(tmp_path):
    """Cloisonner par run : un JOBDIR partagé ferait ignorer les URLs déjà vues
    au run suivant, donc un faux rafraîchissement incomplet."""
    assert run_scrape.resolve_job_dir(tmp_path / "run-a") != run_scrape.resolve_job_dir(
        tmp_path / "run-b"
    )


def test_feeds_routent_chaque_item_vers_son_fichier(tmp_path):
    staging = run_scrape.staging_paths(tmp_path)

    feeds = run_scrape.build_feeds(staging, resumed=False)

    assert feeds[str(tmp_path / "volumes.jsonl")]["item_classes"] == [
        "manga_sanctuary.items.VolumeItem"
    ]
    assert feeds[str(tmp_path / "reviews.jsonl")]["item_classes"] == [
        "manga_sanctuary.items.ReviewItem"
    ]


def test_feeds_ajoutent_en_reprise_et_ecrasent_au_premier_run(tmp_path):
    """Une reprise qui écraserait le staging perdrait tout ce qui précède
    l'interruption — le JOBDIR, lui, ne rejouerait pas ces URLs."""
    staging = run_scrape.staging_paths(tmp_path)

    assert all(f["overwrite"] for f in run_scrape.build_feeds(staging, False).values())
    assert not any(
        f["overwrite"] for f in run_scrape.build_feeds(staging, True).values()
    )


def test_promotion_rejette_les_deux_flux_si_un_seul_est_sous_le_plancher(
    tmp_path, raw_dir
):
    """Promotion tout ou rien : un couple volumes/reviews désynchronisé en base
    serait plus coûteux à démêler qu'un crawl à refaire."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    staging = run_scrape.staging_paths(run_dir)
    staging["volumes"].write_text(
        json.dumps({"volume_url": "https://example/v1"}) + "\n", encoding="utf-8"
    )
    staging["reviews"].write_text(
        json.dumps({"review_url": "https://example/r1"}) + "\n", encoding="utf-8"
    )
    target_dir = raw_dir / "2026-07"

    results, error = run_scrape.promote(
        run_dir, target_dir, {"volumes": 1, "reviews": 99}
    )

    assert error is not None and "reviews" in error
    assert results == {"volumes": (1, 0)}
    assert not (target_dir / "manga_sanctuary_volumes.jsonl").exists()
    assert list(target_dir.iterdir()) == [], "aucun fichier temporaire ne doit rester"


def test_promotion_remplace_les_deux_snapshots(tmp_path, raw_dir):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    staging = run_scrape.staging_paths(run_dir)
    staging["volumes"].write_text(
        json.dumps({"volume_url": "https://example/v1"}) + "\n", encoding="utf-8"
    )
    staging["reviews"].write_text(
        json.dumps({"review_url": "https://example/r1"}) + "\n", encoding="utf-8"
    )
    target_dir = raw_dir / "2026-07"
    target_dir.mkdir(parents=True)
    ancien = target_dir / "manga_sanctuary_volumes.jsonl"
    ancien.write_text(json.dumps({"volume_url": "https://example/vieux"}) + "\n")

    results, error = run_scrape.promote(
        run_dir, target_dir, {"volumes": 1, "reviews": 1}
    )

    assert error is None
    assert results == {"volumes": (1, 0), "reviews": (1, 0)}
    assert json.loads(ancien.read_text())["volume_url"] == "https://example/v1"
    assert sorted(p.name for p in target_dir.iterdir()) == [
        "manga_sanctuary_reviews.jsonl",
        "manga_sanctuary_volumes.jsonl",
    ]


def test_mois_invalide_rejete():
    parser = run_scrape.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--month", "juillet"])

    assert parser.parse_args(["--month", "2026-07"]).month == "2026-07"


# --------------------------------------------------------------------------- #
#  Décision de promotion : la seule voie qu'un crawl réel ne peut pas emprunter
#  en moins de 11 h. On simule donc Scrapy, en relisant les réglages que le
#  lanceur lui passe — ce qui vérifie du même coup qu'ils sont cohérents.
# --------------------------------------------------------------------------- #


def faux_crawl(reason: str, items: int = 1):
    """Remplace le sous-processus Scrapy : écrit les exports que le lanceur a
    demandés via -s FEEDS, puis la raison de fermeture via -s RUN_STATUS_PATH."""

    def _run(command, cwd=None, check=False):
        # Chaque valeur est appairée au drapeau qui la précède : les deux
        # séquences sont décalées d'un cran, donc de longueurs différentes.
        settings = dict(
            argument.split("=", 1)
            for argument, drapeau in zip(command[1:], command, strict=False)
            if drapeau == "-s"
        )
        cles = {
            "manga_sanctuary.items.VolumeItem": "volume_url",
            "manga_sanctuary.items.ReviewItem": "review_url",
        }
        # Scrapy crée la file persistée : sans elle, aucune reprise n'est possible.
        Path(settings["JOBDIR"]).mkdir(parents=True, exist_ok=True)
        for chemin, feed in json.loads(settings["FEEDS"]).items():
            cle = cles[feed["item_classes"][0]]
            with open(chemin, "w" if feed["overwrite"] else "a", encoding="utf-8") as f:
                for numero in range(items):
                    f.write(json.dumps({cle: f"https://example/{cle}/{numero}"}) + "\n")
        Path(settings["RUN_STATUS_PATH"]).write_text(
            json.dumps({"reason": reason}), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0)

    return _run


def lancer(tmp_path, monkeypatch, reason, items=1):
    monkeypatch.setattr(run_scrape.subprocess, "run", faux_crawl(reason, items))
    args = run_scrape.build_parser().parse_args(
        [
            "--run-dir",
            str(tmp_path / "run"),
            "--month",
            "2026-07",
            "--min-volumes",
            "1",
            "--min-reviews",
            "1",
        ]
    )
    return run_scrape.run(args)


def test_crawl_termine_promeut_les_deux_snapshots(tmp_path, raw_dir, monkeypatch):
    code = lancer(tmp_path, monkeypatch, "finished")

    assert code == 0
    assert sorted(p.name for p in (raw_dir / "2026-07").iterdir()) == [
        "manga_sanctuary_reviews.jsonl",
        "manga_sanctuary_volumes.jsonl",
    ]
    etat = json.loads((tmp_path / "run" / "run.json").read_text())
    assert etat["state"] == "promoted"


def test_blocage_ne_promeut_rien_et_conserve_le_partiel(tmp_path, raw_dir, monkeypatch):
    """Un 403 en cours de route ne doit jamais écraser le snapshot précédent."""
    precedent = raw_dir / "2026-07"
    precedent.mkdir(parents=True)
    intact = precedent / "manga_sanctuary_volumes.jsonl"
    intact.write_text(json.dumps({"volume_url": "https://example/intact"}) + "\n")

    code = lancer(
        tmp_path, monkeypatch, "manga_sanctuary_access_blocked_http_403", items=5
    )

    assert code == 1
    assert json.loads(intact.read_text())["volume_url"] == "https://example/intact"
    etat = json.loads((tmp_path / "run" / "run.json").read_text())
    assert etat["state"] == "failed"
    assert etat["item_counts"]["volumes"] == 5, "le partiel reste pour la reprise"


def test_arret_propre_est_distingue_d_une_panne(tmp_path, raw_dir, monkeypatch):
    code = lancer(tmp_path, monkeypatch, "shutdown")

    assert code == 1
    assert not (raw_dir / "2026-07").exists()
    etat = json.loads((tmp_path / "run" / "run.json").read_text())
    assert etat["state"] == "interrupted"


def test_reprise_sans_job_dir_refusee(tmp_path, raw_dir, monkeypatch):
    """Sans file persistée, une reprise re-crawlerait l'acquis : mieux vaut
    refuser que de le faire silencieusement."""
    lancer(tmp_path, monkeypatch, "shutdown")
    assert (tmp_path / "run" / "job").exists(), "le crawl doit laisser une file"
    shutil.rmtree(tmp_path / "run" / "job")
    args = run_scrape.build_parser().parse_args(["--resume", str(tmp_path / "run")])

    assert run_scrape.run(args) == 2


def test_reprise_ajoute_au_staging_et_dedoublonne(tmp_path, raw_dir, monkeypatch):
    """La reprise réécrit dans le même staging : les lignes d'avant doivent y
    rester, et la promotion ne garder qu'une occurrence par URL."""
    lancer(tmp_path, monkeypatch, "shutdown", items=2)
    staging = tmp_path / "run" / "volumes.jsonl"
    assert len(staging.read_text().splitlines()) == 2

    monkeypatch.setattr(run_scrape.subprocess, "run", faux_crawl("finished", items=2))
    args = run_scrape.build_parser().parse_args(
        ["--resume", str(tmp_path / "run"), "--min-volumes", "1", "--min-reviews", "1"]
    )
    code = run_scrape.run(args)

    assert code == 0
    promu = raw_dir / "2026-07" / "manga_sanctuary_volumes.jsonl"
    assert len(promu.read_text().splitlines()) == 2, "2 URLs uniques, pas 4 lignes"
    etat = json.loads((tmp_path / "run" / "run.json").read_text())
    assert etat["duplicates_removed"]["volumes"] == 2
    assert etat["resume_job"].endswith("/job"), "la reprise réutilise la file du run"
