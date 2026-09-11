"""Manifeste de collecte : fusion, jamais reecriture.

Le raw ne part pas au depot. Le manifeste est donc le seul artefact qui prouve
la collecte, et il doit survivre a un run partiel.

Deux artefacts, une seule verite :

- `.manifeste_etat.json` — l'etat de fusion, hors depot, structure et donc
  fusionnable de facon fiable ;
- `MANIFEST.md` — son rendu deterministe, versionne, lisible par un examinateur.

Le format Markdown est celui des quatre sources conformes du projet (01, 04, et
les deux sources du 05). La seule source en JSON, Kitsu, est aussi la seule dont
le manifeste n'est pas versionne : le defaut de format et le defaut de
versionnement sont le meme ecart au patron.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

NOM_ETAT = ".manifeste_etat.json"
NOM_MANIFESTE = "MANIFEST.md"


@dataclass(frozen=True)
class Entree:
    """Un fichier collecte, decrit par ce qui permet de le verifier."""

    chemin: str
    octets: int
    sha256: str
    lignes: int
    date_run: str
    source: str
    perimetre_couvert: str


@dataclass
class Etat:
    """Etat cumule du manifeste d'une partition."""

    source: str
    partition: str
    entrees: dict[str, Entree] = field(default_factory=dict)
    runs: list[str] = field(default_factory=list)


class FusionImpossible(RuntimeError):
    """La fusion porterait sur une autre source ou une autre partition."""


def empreinte(fichier: Path, *, source: str, date_run: str, perimetre: str) -> Entree:
    """Calcule taille, SHA-256 et nombre de lignes d'un fichier collecte."""
    sha = hashlib.sha256()
    lignes = 0
    octets = 0
    with fichier.open("rb") as flux:
        for bloc in iter(lambda: flux.read(1024 * 1024), b""):
            sha.update(bloc)
            octets += len(bloc)
            lignes += bloc.count(b"\n")
    return Entree(
        chemin=fichier.name,
        octets=octets,
        sha256=sha.hexdigest(),
        lignes=lignes,
        date_run=date_run,
        source=source,
        perimetre_couvert=perimetre,
    )


def charger_etat(dossier: Path, *, source: str, partition: str) -> Etat:
    """Lit l'etat existant, ou rend un etat vide. Ne cree rien."""
    chemin = dossier / NOM_ETAT
    if not chemin.exists():
        return Etat(source=source, partition=partition)
    brut = json.loads(chemin.read_text(encoding="utf-8"))
    return Etat(
        source=brut["source"],
        partition=brut["partition"],
        entrees={c: Entree(**e) for c, e in brut.get("entrees", {}).items()},
        runs=list(brut.get("runs", [])),
    )


def fusionner(etat: Etat, nouvelles: list[Entree], *, date_run: str) -> Etat:
    """Rend l'union de l'etat et des nouvelles entrees.

    Une entree existante que le run n'a pas retouchee est **conservee**. C'est
    toute la regle : au bloc 1, un manifeste de run cible reecrit au lieu d'etre
    fusionne a fait perdre les empreintes de trois fichiers.
    """
    fusion = Etat(
        source=etat.source,
        partition=etat.partition,
        entrees=dict(etat.entrees),
        runs=list(etat.runs),
    )
    for entree in nouvelles:
        if entree.source != etat.source:
            raise FusionImpossible(
                f"entree de source {entree.source!r} dans un manifeste {etat.source!r}"
            )
        fusion.entrees[entree.chemin] = entree
    if date_run not in fusion.runs:
        fusion.runs.append(date_run)
    return fusion


def rendre_markdown(etat: Etat) -> str:
    """Rendu deterministe : entrees triees par chemin, runs par ordre d'arrivee."""
    lignes = [
        f"# {etat.source} — partition {etat.partition}",
        "",
        "Fichiers **immuables**. Pour rafraichir la source, creer une partition",
        "datee voisine ; ne jamais reecrire celle-ci.",
        "",
        "Ce manifeste est **versionne** (exception ciblee du `.gitignore` du",
        "module) alors que les fichiers qu'il decrit ne le sont pas : c'est le",
        "seul moyen de confronter une copie locale du raw a une reference.",
        "",
        "## Fichiers",
        "",
        "| Fichier | Lignes | Octets | SHA-256 |",
        "|---|---:|---:|---|",
    ]
    for chemin in sorted(etat.entrees):
        e = etat.entrees[chemin]
        lignes.append(f"| `{e.chemin}` | {e.lignes:n} | {e.octets:n} | `{e.sha256}` |")
    lignes += ["", "## Runs", "", "| Run | Perimetre couvert |", "|---|---|"]
    for run in etat.runs:
        couverts = sorted(
            {e.perimetre_couvert for e in etat.entrees.values() if e.date_run == run}
        )
        lignes.append(f"| `{run}` | {', '.join(couverts) or '—'} |")
    lignes.append("")
    return "\n".join(lignes)


def enregistrer(dossier: Path, etat: Etat) -> tuple[Path, Path]:
    """Ecrit l'etat JSON puis son rendu Markdown. Rend les deux chemins."""
    dossier.mkdir(parents=True, exist_ok=True)
    chemin_etat = dossier / NOM_ETAT
    charge = {
        "source": etat.source,
        "partition": etat.partition,
        "entrees": {c: asdict(e) for c, e in sorted(etat.entrees.items())},
        "runs": etat.runs,
    }
    chemin_etat.write_text(
        json.dumps(charge, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    chemin_md = dossier / NOM_MANIFESTE
    chemin_md.write_text(rendre_markdown(etat), encoding="utf-8")
    return chemin_etat, chemin_md
