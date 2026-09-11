"""Controles 1 et 5 du paragraphe 10 : ce que le depot porte, et l'echec visible."""

import subprocess
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]


def _est_ignore(chemin: Path) -> bool:
    """True si git ignore ce chemin. `check-ignore` rend 0 si ignore, 1 sinon."""
    issue = subprocess.run(
        ["git", "check-ignore", "-q", str(chemin)],
        cwd=MODULE,
        capture_output=True,
    )
    return issue.returncode == 0


def test_les_manifestes_ne_sont_jamais_ignores() -> None:
    """Sentinelle explicite : le manifeste est le livrable qui prouve la collecte.

    Le raw ne part pas au depot. Si le `.gitignore` masquait aussi le manifeste,
    il ne resterait rien pour verifier la collecte — le defaut ouvert du module
    Kitsu, reproduit sur deux sources neuves.
    """
    for source in ("wikipedia_fr", "anilist"):
        manifeste = MODULE / "data" / "raw" / source / "2026-09" / "MANIFEST.md"
        assert not _est_ignore(manifeste), (
            f"le .gitignore masque {manifeste.relative_to(MODULE)} — "
            f"le manifeste doit rester versionne"
        )


def test_le_raw_est_bien_ignore() -> None:
    """La contrepartie : la donnee collectee, elle, ne part pas au depot."""
    for source in ("wikipedia_fr", "anilist"):
        brut = MODULE / "data" / "raw" / source / "2026-09" / f"{source}.ndjson"
        assert _est_ignore(brut), f"{brut.relative_to(MODULE)} devrait etre ignore"


def test_l_etat_de_fusion_est_ignore() -> None:
    etat = MODULE / "data" / "raw" / "anilist" / "2026-09" / ".manifeste_etat.json"
    assert _est_ignore(etat)


def test_un_echec_sort_en_code_non_nul_et_l_affiche() -> None:
    """Controle 5 : un echec non signale produit un succes apparent."""
    for script in ("collecte_wikipedia_fr.py", "collecte_anilist.py"):
        issue = subprocess.run(
            [sys.executable, script, "collecte", "--partition", "2026-09"],
            cwd=MODULE,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert issue.returncode != 0, f"{script} a masque un echec"
        assert issue.stderr.strip(), f"{script} a echoue en silence"
