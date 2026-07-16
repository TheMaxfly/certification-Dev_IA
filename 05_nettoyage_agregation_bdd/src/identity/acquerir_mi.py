"""Ré-acquisition du parquet Manga Insight depuis son Space Hugging Face.

    uv run python -m identity.acquerir_mi
    uv run python -m identity.acquerir_mi --date 2026-08

Le fichier source est RAFRAÎCHI par son auteur : le télécharger sans le dater ni
l'empreinter, c'est perdre la capacité de dire quelles données ont produit quel
résultat. D'où le pattern appliqué ici, le même que pour les autres sources du
projet : un dossier daté, immuable, accompagné d'un MANIFEST qui note d'où vient
le fichier, quand, et sous quelle empreinte.

Le script REFUSE d'écraser une acquisition existante. Un raw daté qui change
sous les pieds ne vaut rien : pour re-télécharger, on change de date.
"""

from __future__ import annotations

import hashlib
import sys
import time
from datetime import date
from pathlib import Path

import requests
import typer

RACINE = Path(__file__).resolve().parents[3]
DESTINATION = RACINE / "05_nettoyage_agregation_bdd/data/raw/mi"

DEPOT = "MangaInsight/manga-insight-dashboard"
FICHIER = ".manga_lab_data/data.parquet"
URL = f"https://huggingface.co/spaces/{DEPOT}/resolve/main/{FICHIER}"
API = f"https://huggingface.co/api/spaces/{DEPOT}"

# Un User-Agent qui dit qui appelle et où écrire en cas de problème : la même
# politesse que pour les autres sources du projet.
AGENT = "certification-Dev_IA/1.0 (+https://github.com/TheMaxfly/certification-Dev_IA)"

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurAcquisition(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def empreinte(chemin: Path) -> str:
    """SHA-256 en flux : l'empreinte ne doit pas dépendre de la RAM libre."""
    digest = hashlib.sha256()
    with chemin.open("rb") as fh:
        for bloc in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(bloc)
    return digest.hexdigest()


def metadonnees_du_space() -> dict:
    """Licence et commit du Space, lus à la source plutôt que supposés."""
    reponse = requests.get(API, headers={"User-Agent": AGENT}, timeout=30)
    reponse.raise_for_status()
    donnees = reponse.json()
    return {
        "licence": (donnees.get("cardData") or {}).get("license"),
        "commit": donnees.get("sha"),
        "modifie": donnees.get("lastModified"),
        "auteur": donnees.get("author"),
    }


def telecharger(cible: Path) -> int:
    """Téléchargement en flux vers un fichier temporaire, puis renommage.

    Le renommage final est atomique : une coupure réseau laisse un `.partiel`
    visible, jamais un parquet tronqué qui aurait l'air complet.
    """
    partiel = cible.with_suffix(".partiel")
    with requests.get(
        URL, headers={"User-Agent": AGENT}, stream=True, timeout=120
    ) as reponse:
        reponse.raise_for_status()
        with partiel.open("wb") as fh:
            for bloc in reponse.iter_content(chunk_size=1024 * 1024):
                fh.write(bloc)
    partiel.rename(cible)
    return cible.stat().st_size


def ecrire_manifest(
    dossier: Path, cible: Path, taille: int, sha: str, meta: dict
) -> Path:
    """Le MANIFEST est la mémoire de l'acquisition : sans lui, le parquet n'est
    qu'un fichier binaire dont personne ne sait d'où il sort."""
    manifest = dossier / "MANIFEST.md"
    licence = meta.get("licence") or "non déclarée"
    manifest.write_text(
        f"""# Manga Insight — acquisition du {date.today().isoformat()}

Fichier **immuable**. Pour rafraîchir la source, créer un dossier daté voisin ;
ne jamais réécrire celui-ci.

| | |
|---|---|
| Fichier | `{cible.name}` |
| URL source | <{URL}> |
| Dépôt | Hugging Face Space `{DEPOT}` |
| Chemin dans le dépôt | `{FICHIER}` |
| Commit du Space | `{meta.get("commit")}` |
| Space modifié le | {meta.get("modifie")} |
| Téléchargé le | {date.today().isoformat()} |
| Taille | {taille} octets ({taille / 1e6:.2f} Mo) |
| SHA-256 | `{sha}` |
| Licence déclarée | **{licence}** |

## Attribution

Manga Insight — Observatoire data du marché manga français.
Projet R&D porté par Juliet Faure — <https://mangainsight.fr>, <https://bethesource.fr>.

## Licence — à lire avant réutilisation

Le Space déclare **`license: {licence}`** dans les métadonnées de son README,
et c'est la seule licence que la source affiche. La commande d'acquisition
attendait « CC BY 4.0 » : cette mention ne vient pas de la source et n'a donc
pas été reprise ici.

La licence d'un Space couvre son **code**. Elle ne dit pas explicitement sous
quel régime sont publiées les **données** du parquet, qui sont un travail
éditorial distinct. Dans le doute, l'attribution ci-dessus est portée dans tous
les cas — elle est due au titre de MIT comme de CC BY.

## Vérifier l'intégrité

```bash
sha256sum {cible.name}
# doit rendre : {sha}
```
""",
        encoding="utf-8",
    )
    return manifest


@app.command()
def acquerir(
    date_acquisition: str = typer.Option(  # noqa: B008
        None, "--date", help="Dossier daté (défaut : le mois courant, AAAA-MM)."
    ),
) -> None:
    """Télécharge le parquet Manga Insight vers un raw daté et immuable."""
    horodatage = date_acquisition or date.today().strftime("%Y-%m")
    dossier = DESTINATION / horodatage
    cible = dossier / "data.parquet"
    if cible.exists():
        raise ErreurAcquisition(
            f"{cible} existe déjà. Un raw daté est immuable : pour re-télécharger, "
            "utiliser une autre date (--date AAAA-MM)."
        )
    dossier.mkdir(parents=True, exist_ok=True)

    debut = time.monotonic()
    typer.echo(f"→ {URL}")
    meta = metadonnees_du_space()
    taille = telecharger(cible)
    sha = empreinte(cible)
    manifest = ecrire_manifest(dossier, cible, taille, sha, meta)

    typer.echo(f"  ✓ {cible.relative_to(RACINE)} — {taille / 1e6:.2f} Mo")
    typer.echo(f"  ✓ SHA-256 : {sha}")
    typer.echo(f"  ✓ licence déclarée par la source : {meta.get('licence')}")
    typer.echo(f"  ✓ {manifest.relative_to(RACINE)}")
    typer.echo(f"Terminé en {time.monotonic() - debut:.1f} s.")


def main() -> int:
    try:
        app()
    except ErreurAcquisition as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except requests.RequestException as erreur:
        typer.echo(f"ERREUR réseau : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
