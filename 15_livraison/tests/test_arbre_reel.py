"""L'arbre propre, journaux exceptés, sur un dépôt jetable RÉEL (correctif 0.1.1).

Un outil qui écrit dans l'arbre se teste sur un dépôt réel, deux gestes à la suite :
git est réel (dépôt et HOME jetables, `tmp_path`) ; docker, Compose, psql, uv et la
relecture par le hook restent doublés par le poste simulé. Le premier déploiement écrit
son journal et met `index.md` à jour dans l'arbre ; le second est accepté ; un fichier
modifié hors des journaux est refusé, nommé.
"""

from __future__ import annotations

import subprocess  # nosec B404 — essai, commandes fixes
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import (
    CIBLES,
    E4,
    FICHIERS,
    JOBS,
    Bouclage,
    Poste,
    cible_prometheus,
    cibles,
)

from livraison import deployer
from livraison.codes import Arret, Code
from livraison.journal import EN_TETE_INDEX
from livraison.outils import Commande, Outils, Sortie, lancer_reel

#: git sans configuration du poste : identité factice, aucun crochet, aucune signature.
GIT = (
    "git",
    "-c",
    "user.name=essai",
    "-c",
    "user.email=essai@example.invalid",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "tag.gpgsign=false",
)
ENV_MODULES = ("11_api_modele/.env", "12_application/.env", "02_api_manga/.env")


def git_reel(depot: Path, maison: Path, *arguments: str) -> str:
    s = subprocess.run(  # noqa: S603 — commande fixe, dépôt jetable
        [*GIT, "-C", str(depot), *arguments],
        check=True,
        capture_output=True,
        text=True,
        env={"HOME": str(maison), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    return s.stdout


@pytest.fixture
def depot_reel(tmp_path: Path) -> tuple[Path, Path]:
    """Un dépôt à l'étiquette annotée `e4-livraison`, sur `main`, propre ; les `.env`
    des modules présents et ignorés, comme au vrai dépôt."""
    r = tmp_path / "reel"
    maison = tmp_path / "maison_git"
    maison.mkdir()
    r.mkdir()
    git_reel(r, maison, "init", "-q", "-b", "main")
    for chemin, texte in FICHIERS[E4].items():
        (r / chemin).parent.mkdir(parents=True, exist_ok=True)
        (r / chemin).write_text(texte, encoding="utf-8")
    (r / ".gitignore").write_text(".env\n", encoding="utf-8")
    for f in ENV_MODULES:
        (r / f).write_text("", encoding="utf-8")
    journal = r / "15_livraison" / "journal"
    journal.mkdir(parents=True)
    (journal / "index.md").write_text(EN_TETE_INDEX, encoding="utf-8")
    git_reel(r, maison, "add", "-A")
    git_reel(r, maison, "commit", "-q", "-m", "départ")
    git_reel(r, maison, "tag", "-a", E4, "-m", "étiquette de l'essai")
    assert git_reel(r, maison, "status", "--porcelain") == ""
    return r, maison


def horloge_murale() -> Iterator[datetime]:
    t = datetime(2026, 11, 11, 10, 0, 0, tzinfo=UTC)
    while True:
        yield t
        t += timedelta(minutes=5)


@pytest.fixture
def o_reel(
    o: Outils, poste: Poste, bouclage: Bouclage, depot_reel: tuple[Path, Path]
) -> Outils:
    r, maison = depot_reel

    def lanceur(c: Commande) -> Sortie:
        if c.argv[0] == "git":
            return lancer_reel(
                Commande(c.nom, c.argv, r, {**c.env, "HOME": str(maison)}, c.entree)
            )
        return poste(c)

    murale = horloge_murale()
    lue = [datetime(2026, 11, 11, 10, 0, 0, tzinfo=UTC)]

    def maintenant() -> datetime:
        lue[0] = next(murale)
        return lue[0]

    def http(url: str) -> tuple[int, str]:
        """Prometheus suit l'horloge murale : son dernier relevé commence 7 s après la
        dernière heure lue par `livrer` (l'instant où la pile a répondu)."""
        if url == CIBLES:
            releve = (lue[0] + timedelta(seconds=7)).strftime(
                "%Y-%m-%dT%H:%M:%S.450771275Z"
            )
            return cibles(*[cible_prometheus(j, releve=releve) for j in JOBS])
        return bouclage(url)

    return replace(
        o,
        lanceur=lanceur,
        http=http,
        racine=r,
        journal=r / "15_livraison" / "journal",
        maintenant=maintenant,
    )


def statut(o: Outils, depot_reel: tuple[Path, Path]) -> list[str]:
    r, maison = depot_reel
    return git_reel(r, maison, "status", "--porcelain").splitlines()


def test_deux_deploiements_a_la_suite_le_journal_du_premier_dans_l_arbre(
    o_reel: Outils, depot_reel: tuple[Path, Path], ecran: list[str]
):
    assert deployer.deployer(o_reel, "production") == Code.OK
    apres_premier = statut(o_reel, depot_reel)
    assert " M 15_livraison/journal/index.md" in apres_premier
    assert "?? 15_livraison/journal/20261111T100000Z_production_e4-livraison.md" in (
        apres_premier
    )
    ecran.clear()
    assert deployer.deployer(o_reel, "production", a_blanc=True) == Code.OK
    assert "  préalable tenu : étiquette" in ecran
    assert deployer.deployer(o_reel, "production") == Code.OK
    journaux = sorted(p.name for p in o_reel.journal.glob("2*.md"))
    # Le premier journal à 10:00 ; le second plus tard (l'horloge murale avance à chaque
    # heure que `livrer` lit : début, fin des commandes Compose, instant du lever).
    assert len(journaux) == 2
    assert journaux[0] == "20261111T100000Z_production_e4-livraison.md"
    assert journaux[1] > journaux[0] and journaux[1].endswith(
        "_production_e4-livraison.md"
    )


@pytest.mark.parametrize(
    "nom",
    ["12_application/compose.yaml", "notes.txt"],
    ids=["suivi_modifie", "non_suivi"],
)
def test_un_fichier_hors_journal_est_refuse_et_nomme(
    o_reel: Outils, depot_reel: tuple[Path, Path], ecran: list[str], nom: str
):
    r, _ = depot_reel
    assert deployer.deployer(o_reel, "production") == Code.OK
    (r / nom).write_text("modifié hors des journaux\n", encoding="utf-8")
    with pytest.raises(Arret, match=f"premier fichier en cause : {nom}") as e:
        deployer.deployer(o_reel, "production")
    assert e.value.code == Code.PREREQUIS
    ecran.clear()
    deployer.deployer(o_reel, "production", a_blanc=True)
    assert any(
        ligne.startswith("  refuserait : étiquette") and nom in ligne for ligne in ecran
    )
