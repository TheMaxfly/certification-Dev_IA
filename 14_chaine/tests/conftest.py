"""Le harnais de `verify 14` : des étapes factices, aucun service, aucune base.

Le lanceur factice répond, par nom de commande, une sortie écrite d'avance (ou calculée
d'après la commande), et retient tout ce qui a été lancé : l'ordre, les arguments,
l'environnement transmis. Le bouclage HTTP est factice lui aussi.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from chaine import environnement
from chaine.environnement import Commande, Sortie
from chaine.executer import Outils

MODULE = Path(__file__).resolve().parents[1]
RACINE = MODULE.parent
REELLES = Path(__file__).resolve().parent / "sorties_reelles"
CANDIDATS = MODULE / "candidats"
EMPREINTE_JEU = "31712b0d0b0cbd318ef228da105673af0cba2db431a72462f7635b2f6ec78b30"
INFO_BGE_M3 = {
    "model_id": "BAAI/bge-m3",
    "model_sha": "5617a9f61b028005a4858fdac845db406aefb181",
    "model_dtype": "float32",
    "version": "1.9.4",
}
INFO_GEMMA = {
    "model_id": "google/embeddinggemma-300m",
    "model_sha": "57c266a740f537b4dc058e1b0cda161fd15afa75",
    "model_dtype": "float32",
    "version": "1.9.4",
}
REGISTRE = (
    "1\tBAAI/bge-m3\t5617a9f61b028005a4858fdac845db406aefb181\tfloat32\t1.9.4\n"
    "2\tgoogle/embeddinggemma-300m\t57c266a740f537b4dc058e1b0cda161fd15afa75\tfloat32\t1.9.4\n"
    "3\tgoogle/embeddinggemma-300m\t57c266a740f537b4dc058e1b0cda161fd15afa75\tfloat32\t1.9.4\n"
)
IMAGE = "sha256:" + "ab" * 32

Reponse = Sortie | Callable[[Commande], Sortie]


@dataclass
class Factice:
    """Le lanceur factice."""

    reponses: dict[str, Reponse]
    lancees: list[Commande] = field(default_factory=list[Commande])

    def __call__(self, commande: Commande) -> Sortie:
        self.lancees.append(commande)
        if commande.nom not in self.reponses:
            raise AssertionError(f"commande inattendue : {commande.nom}")
        r = self.reponses[commande.nom]
        return r(commande) if callable(r) else r

    def noms(self) -> list[str]:
        return [c.nom for c in self.lancees]

    def une(self, nom: str) -> Commande:
        trouvees = [c for c in self.lancees if c.nom == nom]
        assert trouvees, f"{nom} n'a pas été lancée"
        return trouvees[-1]


def apres(commande: Commande, option: str) -> str:
    argv = list(commande.argv)
    return argv[argv.index(option) + 1]


def empreintes_de(corpus: str) -> Sortie:
    lignes = [
        "\t".join(c[1:])
        for c in environnement.reference()
        if c[0] == corpus or (corpus == "v1" and c[:2] == ["-", "eval_mesures"])
    ]
    return Sortie(0, "\n".join(lignes) + "\n")


def bilan_encodeur(
    encodage_id: int = 4, cree: bool = True
) -> Callable[[Commande], Sortie]:
    def ecrire(c: Commande) -> Sortie:
        b = json.loads((REELLES / "encodage_bge-m3.json").read_text("utf-8"))
        b |= {"encodage_id": encodage_id, "corpus_id": "v2", "encodage_cree": cree}
        b |= {"vecteurs_ecrits": 77006, "fragments_corpus": 77006}
        Path(apres(c, "--sortie")).write_text(json.dumps(b), "utf-8")
        return Sortie(0, json.dumps(b))

    return ecrire


def exporte(c: Commande) -> Sortie:
    dossier = Path(c.env[environnement.VARIABLE_DOSSIER])
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / f"encodage_{apres(c, '--encodage')}.json"
    chemin.write_text("{}", "utf-8")
    return Sortie(0, f"{chemin}\n")


def comparer_rend(verdict: str = "equivalent", cos_min: float = 0.99999) -> Sortie:
    r = json.loads((REELLES / "comparer_encodage_2.json").read_text("utf-8"))
    r |= {"verdict": verdict, "cos_min": cos_min}
    return Sortie(1 if verdict == "echec" else 0, json.dumps(r, indent=1))


def mesure_rend(
    encodage_id: int = 4, run: str = "f" * 32
) -> Callable[[Commande], Sortie]:
    def rendre(c: Commande) -> Sortie:
        n = int(apres(c, "--encodage"))
        ligne = {
            "mesure": int(c.argv[c.argv.index("mesures_recherche.executer") + 1]),
            "global": {"hit_rate@10/toutes": 0.3729},
            "corpus_id": "v2",
            "encodage_id": n if n == encodage_id else encodage_id,
            "run_id": run,
            "rejeu": None,
        }
        return Sortie(0, "avertissement de MLflow\n" + json.dumps(ligne))

    return rendre


def rejeu_rend(identiques: bool = True) -> Sortie:
    ligne = {"rejeu": {"reference": "x", "identiques": identiques}, "run_id": None}
    return Sortie(0 if identiques else 1, json.dumps(ligne))


def verdict_rend(
    verdict: str = "refus proposé", global_: int = 22, reconnaissance: int = 16
) -> Callable[[Commande], Sortie]:
    def rendre(c: Commande) -> Sortie:
        complet = json.loads((REELLES / "promotion_mesure_8.json").read_text("utf-8"))
        complet["verdict"] = verdict
        complet["nouveau"] |= {"global": global_, "reconnaissance": reconnaissance}
        Path(apres(c, "--sortie")).write_text(json.dumps(complet), "utf-8")
        court = {k: complet[k] for k in ("verdict", "nouveau", "hypothese")}
        return Sortie(0, json.dumps(court, ensure_ascii=False))

    return rendre


def reponses_du_passage(
    *, en_service: str = "3", verdict: str = "refus proposé", encodage_id: int = 4
) -> dict[str, Reponse]:
    """Toutes les commandes d'un passage complet, chacune tenue."""
    return {
        "git.status": Sortie(0, ""),
        "git.rev-parse": Sortie(0, "c0ffee" + "0" * 34 + "\n"),
        "git.contient": Sortie(0, "  origin/feature/chaine-livraison\n"),
        "git.suivi": Sortie(0, ""),
        "git.inchange": Sortie(0, ""),
        "git.regle": Sortie(0, "b6c081c" + "0" * 33 + " 2026-10-10T02:50:54+02:00\n"),
        "docker.pile": Sortie(0, ""),
        "psql.encodages": Sortie(0, REGISTRE),
        "psql.en_service": Sortie(0, f"{en_service}\n"),
        "psql.jeu": Sortie(0, f"{EMPREINTE_JEU}\n"),
        "psql.empreintes.v2": empreintes_de("v2"),
        "05.construire_v2.dry_run": Sortie(
            0, (REELLES / "construire_v2_dry_run.txt").read_text("utf-8")
        ),
        "05.construire_v2": Sortie(0, "contrôles verts — VALIDÉ\n{}"),
        "09.encodage": bilan_encodeur(encodage_id),
        "09.equivalence.exporter": exporte,
        "09.equivalence.comparer": comparer_rend(),
        "10.executer": mesure_rend(encodage_id),
        "10.executer.rejeu": rejeu_rend(),
        "10.promotion": verdict_rend(verdict),
        "11.image": Sortie(0, "construite\n"),
        "docker.image": Sortie(0, f"{IMAGE}\n"),
        "10.mlflow.log_artifact": Sortie(0, ""),
        "psql.pseudonymes": Sortie(0, "Zorglub\nMlle Factice\n"),
    }


@dataclass
class Bouclage:
    """Le bouclage HTTP factice : une réponse par URL (préfixe) ; les en-têtes reçus."""

    reponses: dict[str, tuple[int, str]]
    recues: list[tuple[str, dict[str, str]]] = field(
        default_factory=list[tuple[str, dict[str, str]]]
    )

    def __call__(self, url: str, entetes: Any) -> tuple[int, str]:
        self.recues.append((url, dict(entetes)))
        for prefixe, r in self.reponses.items():
            if url.startswith(prefixe):
                return r
        return 0, "ConnectionRefusedError"


def bouclage(info: dict[str, str] = INFO_BGE_M3) -> Bouclage:
    return Bouclage(
        {
            "http://127.0.0.1:8081/info": (200, json.dumps(INFO_BGE_M3)),
            "http://127.0.0.1:8082/info": (200, json.dumps(INFO_GEMMA)),
        }
        if info is INFO_BGE_M3
        else {"http://127.0.0.1:8082/info": (200, json.dumps(info))}
    )


@dataclass
class Monde:
    lanceur: Factice
    http: Bouclage
    outils: Outils
    ecran: list[str]
    environ: dict[str, str]
    preprod: Path


@pytest.fixture
def monde(tmp_path: Path) -> Monde:
    ecran: list[str] = []
    lanceur = Factice(reponses_du_passage())
    http = bouclage()
    tic = iter(range(1, 10_000))
    outils = Outils(
        lanceur=lanceur,
        http=http,
        afficher=ecran.append,
        horloge=lambda: float(next(tic)),
        dormir=lambda _s: None,
        maintenant=lambda: datetime(2026, 11, 7, 3, 0, 0, tzinfo=UTC),
        livraisons=tmp_path / "livraisons",
        travail=tmp_path / "travail",
    )
    preprod = tmp_path / "equivalence_preprod"
    environ = {environnement.VARIABLE_DOSSIER: str(preprod)}
    return Monde(lanceur, http, outils, ecran, environ, preprod)
