"""Le poste en double : git, docker, Compose, psql, uv, le bouclage.

Aucune commande réelle n'est lancée (sauf l'essai de la relecture par le hook, dans un
dépôt jetable) ; aucun journal n'est écrit hors de `tmp_path`. L'état de départ est une
production saine à l'étiquette `e4-livraison`.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE / "tests"))

from livraison import deployer, sante  # noqa: E402
from livraison.outils import Commande, Outils, Sortie  # noqa: E402

E4 = "e4-livraison"
E3 = "chaine-livraison"
COMMIT_E4 = "a" * 40
COMMIT_E3 = "b" * 40


def compose_11(version: str) -> str:
    return f"name: api-modele\nservices:\n  api:\n    image: api-modele:{version}\n"


def compose_12(app: str, catalogue: str | None, comptes: bool) -> str:
    t = "services:\n"
    if catalogue:
        t += f"  catalogue:\n    image: api-catalogue:{catalogue}\n"
    t += f"  application:\n    image: application-conseiller:{app}\n"
    if comptes:
        t += "    environment:\n      APPLICATION_BASE_DSN: ${X:-}\n"
    return t


def pyproject(version: str) -> str:
    return f'[project]\nname = "x"\nversion = "{version}"\n'


FICHIERS = {
    E4: {
        "11_api_modele/docker-compose.yml": compose_11("1.5.0"),
        "12_application/compose.yaml": compose_12("0.9.1", "0.7.0", True),
        "13_monitorage/compose.yaml": "services: {}\n",
        "11_api_modele/pyproject.toml": pyproject("1.5.0"),
        "02_api_manga/pyproject.toml": pyproject("0.7.0"),
        "12_application/pyproject.toml": pyproject("0.9.1"),
    },
    E3: {
        "11_api_modele/docker-compose.yml": compose_11("1.4.0"),
        "12_application/compose.yaml": compose_12("0.1.0", None, False),
        "13_monitorage/compose.yaml": "services: {}\n",
        "11_api_modele/pyproject.toml": pyproject("1.4.0"),
        "02_api_manga/pyproject.toml": pyproject("0.4.0"),
        "12_application/pyproject.toml": pyproject("0.1.0"),
    },
}
SERVIES = {
    E4: {
        "api": "api-modele:1.5.0",
        "catalogue": "api-catalogue:0.7.0",
        "application": "application-conseiller:0.9.1",
        "embedding": "tei",
    },
    E3: {"api": "api-modele:1.4.0", "application": "application-conseiller:0.1.0"},
}
PROJETS = {"production": "api-modele", "staging": "senpai-staging"}


@dataclass
class Poste:
    """Un poste simulé ; chaque commande reçue est gardée dans `appels`."""

    appels: list[Commande] = field(default_factory=list[Commande])
    statut_git: str = ""
    branche: str = "main"
    head: str | None = E4
    annotees: dict[str, str] = field(
        default_factory=lambda: {E4: COMMIT_E4, E3: COMMIT_E3}
    )
    legeres: set[str] = field(default_factory=set[str])
    fichiers: dict[str, dict[str, str]] = field(
        default_factory=lambda: {k: dict(v) for k, v in FICHIERS.items()}
    )
    images: dict[str, str] = field(
        default_factory=lambda: {
            "api-modele:1.5.0": "sha256:" + "1" * 64,
            "api-catalogue:0.7.0": "sha256:" + "2" * 64,
            "application-conseiller:0.9.1": "sha256:" + "3" * 64,
            "api-modele:1.4.0": "sha256:" + "4" * 64,
            "application-conseiller:0.1.0": "sha256:" + "5" * 64,
        }
    )
    construction: dict[str, str] = field(default_factory=dict[str, str])
    build_code: int = 0
    en_marche: dict[str, dict[str, str]] = field(
        default_factory=lambda: {"api-modele": dict(SERVIES[E4])}
    )
    apres_up: dict[str, str] | None = None
    compose_code: int = 0
    migrations: tuple[int, int] = (30, 0)
    encodage: str = "3"
    api: dict[str, object] = field(
        default_factory=lambda: {
            "sante": 200,
            "statut": "ok",
            "info": 200,
            "version_api": "1.5.0",
            "encodage_id": 3,
            "corpus_id": "v2",
            "nb_fragments": 77006,
        }
    )
    metriques: dict[str, object] = field(
        default_factory=lambda: {"statut": 200, "appels": 4, "cout": 0.0123}
    )
    refus_cles: list[str] = field(default_factory=list[str])
    refus_mention: bool = False

    def noms(self) -> list[str]:
        return [c.nom for c in self.appels]

    def argvs(self) -> list[tuple[str, ...]]:
        return [c.argv for c in self.appels]

    def __call__(self, c: Commande) -> Sortie:  # noqa: C901 — un aiguillage
        self.appels.append(c)
        a = c.argv
        if a[0] == "git":
            return self.git(a[1:])
        if a[:3] == ("docker", "image", "inspect"):
            ref = a[-1]
            return Sortie(0, self.images[ref]) if ref in self.images else Sortie(1)
        if a[:2] == ("docker", "build"):
            cand = a[a.index("-t") + 1]
            if self.build_code:
                return Sortie(self.build_code, stderr="échec")
            ref = cand.removesuffix("-candidat")
            self.images[cand] = self.construction.get(
                ref, self.images.get(ref, "sha256:neuve")
            )
            return Sortie(0)
        if a[:2] == ("docker", "tag"):
            self.images[a[3]] = self.images[a[2]]
            return Sortie(0)
        if a[:3] == ("docker", "image", "rm"):
            self.images.pop(a[3], None)
            return Sortie(0)
        if a[:2] == ("docker", "ps"):
            projet = a[3].rsplit("=", 1)[1]
            servies = self.en_marche.get(projet, {})
            if "{{.Names}}" in a:
                return Sortie(0, "".join(f"{projet}-{s}-1\n" for s in servies))
            return Sortie(0, "".join(f"{s}\t{i}\n" for s, i in servies.items()))
        if a[:2] == ("docker", "compose"):
            projet = a[a.index("-p") + 1]
            if self.compose_code:
                return Sortie(self.compose_code, stderr="refus")
            if "up" in a:
                self.en_marche[projet] = dict(self.apres_up or SERVIES[E4])
            if "down" in a:
                self.en_marche.pop(projet, None)
            return Sortie(0)
        if a[:2] == ("docker", "exec"):
            if a[-1] == sante.PROGRAMME_API:
                return Sortie(0, json.dumps(self.api))
            if a[-1] == deployer.PROGRAMME_METRIQUES:
                return Sortie(0, json.dumps(self.metriques))
            return Sortie(1)
        if a[0] == "uv" and "migrate.py" in a:
            if a[-1] != "status":
                raise AssertionError(f"migrate.py {a[-1]} : livrer ne migre jamais")
            m = self.migrations
            return Sortie(0, f"…\n{m[0]} appliquée(s), {m[1]} en attente\n")
        if a[0] == "psql":
            return Sortie(0, self.encodage + "\n")
        if c.nom == "hook.cles":
            return Sortie(0, "".join(f"{r}\n" for r in self.refus_cles))
        if c.nom == "hook.mentions":
            return Sortie(1 if self.refus_mention else 0)
        raise AssertionError(f"commande inattendue : {a}")

    def git(self, a: tuple[str, ...]) -> Sortie:
        if a[0] == "status":
            return Sortie(0, self.statut_git)
        if a[0] == "branch":
            return Sortie(0, self.branche + "\n")
        if a[0] == "describe":
            return Sortie(0, self.head + "\n") if self.head else Sortie(128)
        if a[0] == "cat-file":
            nom = a[2].removeprefix("refs/tags/")
            if nom in self.annotees:
                return Sortie(0, "tag\n")
            return Sortie(0, "commit\n") if nom in self.legeres else Sortie(1)
        if a[0] == "rev-parse":
            nom = a[1].removeprefix("refs/tags/").removesuffix("^{commit}")
            return Sortie(0, self.annotees[nom] + "\n")
        if a[0] == "show":
            ref, _, chemin = a[1].partition(":")
            ref = (self.head or E4) if ref == "HEAD" else ref
            texte = self.fichiers.get(ref or "", {}).get(chemin)
            return Sortie(0, texte) if texte is not None else Sortie(128)
        if a[0] == "worktree":
            return Sortie(0)
        raise AssertionError(f"git inattendu : {a}")


@dataclass
class Bouclage:
    """Le bouclage : URL → (statut, corps) ; ce qui n'est pas réglé ne répond pas."""

    reponses: dict[str, tuple[int, str]] = field(
        default_factory=dict[str, tuple[int, str]]
    )
    demandes: list[str] = field(default_factory=list[str])

    def __call__(self, url: str) -> tuple[int, str]:
        self.demandes.append(url)
        for debut, reponse in self.reponses.items():
            if url.startswith(debut):
                return reponse
        return 0, "ConnectError"


def sante_application(**surcharges: object) -> str:
    corps: dict[str, object] = {
        "application": "ok",
        "api": "ok",
        "base": "ok",
        "catalogue": "ok",
        "cles_api": "deux",
        "profil_actif": "defaut",
        "version": "0.9.1",
    }
    return json.dumps(corps | surcharges)


def bouclage_sain(port_app: int = 8020, port_cat: int = 8000) -> Bouclage:
    return Bouclage(
        {
            f"http://127.0.0.1:{port_app}/sante": (200, sante_application()),
            f"http://127.0.0.1:{port_cat}/health": (
                200,
                json.dumps({"status": "ok", "db": "ok", "normalisation": "fonction"}),
            ),
            "http://127.0.0.1:9090/api/v1/targets": (
                200,
                json.dumps(
                    {
                        "data": {
                            "activeTargets": [
                                {"labels": {"job": "api"}, "health": "up"},
                                {"labels": {"job": "application"}, "health": "up"},
                            ]
                        }
                    }
                ),
            ),
        }
    )


@pytest.fixture
def racine(tmp_path: Path) -> Path:
    """Un dépôt de travail : les fichiers d'environnement des modules (vides)."""
    r = tmp_path / "depot"
    for f in ("11_api_modele/.env", "12_application/.env", "02_api_manga/.env"):
        (r / f).parent.mkdir(parents=True, exist_ok=True)
        (r / f).write_text("", encoding="utf-8")
    return r


@pytest.fixture
def poste() -> Poste:
    return Poste()


@pytest.fixture
def bouclage() -> Bouclage:
    return bouclage_sain()


@pytest.fixture
def ecran() -> list[str]:
    return []


@pytest.fixture
def o(
    racine: Path, poste: Poste, bouclage: Bouclage, ecran: list[str], tmp_path: Path
) -> Outils:
    temps = iter(float(i) for i in range(10_000))
    return Outils(
        lanceur=poste,
        http=bouclage,
        afficher=ecran.append,
        horloge=lambda: next(temps),
        dormir=lambda _s: None,
        maintenant=lambda: datetime(2026, 11, 11, 10, 0, 0, tzinfo=UTC),
        racine=racine,
        environ={},
        utilisateur=lambda: "poste",
        journal=tmp_path / "journal",
        constructions=tmp_path / "constructions",
    )
