"""`livrer construire` : les images de la pile, construites à l'étiquette.

UNE ÉTIQUETTE = UNE CONSTRUCTION (leçon des lots 4 et 5). Chaque image est d'abord
construite sous une étiquette d'image provisoire (`<version>-candidat`) ; son
empreinte est comparée à celle de l'étiquette d'image définitive, si elle existe déjà.
Égales : rien ne change. Différentes : refus, **avant toute réécriture** — sauf
`--reconstruire`, journalisé avec l'ancienne empreinte. Absente : l'image est étiquetée.

LES VERSIONS viennent des `pyproject.toml` de l'étiquette ; le Compose de l'étiquette
doit désigner la même (sinon refus). Une image que le Compose de l'étiquette ne nomme
pas n'en fait pas partie (le catalogue n'est pas dans la pile de `chaine-livraison`).

`livrer` ne supprime jamais une image : il ne retire que ses étiquettes provisoires
`…-candidat`.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from livraison import depot
from livraison.codes import Arret, Code
from livraison.outils import Commande, Outils, commande_affichee, objet

SUFFIXE_CANDIDAT = "-candidat"


@dataclass(frozen=True)
class Recette:
    """Comment une image se construit, depuis la racine d'un arbre du dépôt."""

    module: str
    nom: str
    service: str
    compose: str
    dockerfile: str
    contexte: str
    cible_build: str | None = None


RECETTES = (
    Recette(
        "11_api_modele",
        "api-modele",
        "api",
        "11_api_modele/docker-compose.yml",
        "11_api_modele/Dockerfile",
        ".",
    ),
    Recette(
        "02_api_manga",
        "api-catalogue",
        "catalogue",
        "12_application/compose.yaml",
        "02_api_manga/Dockerfile",
        "02_api_manga",
    ),
    Recette(
        "12_application",
        "application-conseiller",
        "application",
        "12_application/compose.yaml",
        "12_application/Dockerfile",
        ".",
        "application",
    ),
)


@dataclass(frozen=True)
class Image:
    recette: Recette
    version: str

    @property
    def ref(self) -> str:
        return f"{self.recette.nom}:{self.version}"

    @property
    def candidat(self) -> str:
        return f"{self.ref}{SUFFIXE_CANDIDAT}"


@dataclass(frozen=True)
class Construite:
    image: Image
    empreinte: str
    statut: (
        str  # « construite », « inchangée », « réécrite (ancienne …) », « présente »
    )


def version_du_projet(texte: str) -> str:
    version = objet(tomllib.loads(texte).get("project")).get("version")
    if not isinstance(version, str):
        raise Arret(Code.PREREQUIS, "pyproject sans version de projet")
    return version


def images_de(o: Outils, reference: str) -> list[Image]:
    """Les images de la pile à cette référence, à la version de leur `pyproject`."""
    images: list[Image] = []
    for r in RECETTES:
        compose = depot.fichier_a(o, reference, r.compose)
        if compose is None:
            raise Arret(Code.PREREQUIS, f"{r.compose} absent à {reference}")
        m = re.search(rf"^\s*image:\s*{re.escape(r.nom)}:(\S+)\s*$", compose, re.M)
        if m is None:
            continue
        pyproject = depot.fichier_a(o, reference, f"{r.module}/pyproject.toml")
        if pyproject is None:
            raise Arret(
                Code.PREREQUIS, f"{r.module}/pyproject.toml absent à {reference}"
            )
        version = version_du_projet(pyproject)
        if m.group(1) != version:
            raise Arret(
                Code.PREREQUIS,
                f"{r.nom} : le Compose désigne {m.group(1)}, le pyproject {version}"
                f" (à {reference}) — une version, une image",
            )
        images.append(Image(r, version))
    return images


def empreinte(o: Outils, ref: str) -> str | None:
    s = o.lanceur(
        Commande(
            f"docker.empreinte.{ref}",
            ("docker", "image", "inspect", "--format", "{{.Id}}", ref),
            dossier=o.racine,
        )
    )
    lu = s.stdout.strip()
    return lu if s.code == 0 and lu else None


def commande_construction(image: Image, arbre: Path) -> Commande:
    r = image.recette
    argv = ["docker", "build", "-f", str(arbre / r.dockerfile)]
    if r.cible_build:
        argv += ["--target", r.cible_build]
    argv += ["-t", image.candidat, str(arbre / r.contexte)]
    return Commande(f"docker.build.{r.nom}", tuple(argv), dossier=arbre)


def etiqueter(image: Image, racine: Path) -> Commande:
    return Commande(
        f"docker.tag.{image.recette.nom}",
        ("docker", "tag", image.candidat, image.ref),
        dossier=racine,
    )


def retirer_candidat(image: Image, racine: Path) -> Commande:
    """Retire l'étiquette provisoire, et elle seule (jamais une image livrée)."""
    if not image.candidat.endswith(SUFFIXE_CANDIDAT):  # pragma: no cover — garde
        raise Arret(Code.INTERNE, "seules les étiquettes provisoires se retirent")
    return Commande(
        f"docker.rmi.{image.recette.nom}",
        ("docker", "image", "rm", image.candidat),
        dossier=racine,
    )


def construire(
    o: Outils,
    images: list[Image],
    *,
    arbre: Path | None = None,
    reconstruire: bool = False,
    a_blanc: bool = False,
) -> list[Construite]:
    """Construit les images depuis `arbre` (l'arbre à l'étiquette), compare, étiquette.
    Refus avant toute réécriture si une étiquette d'image existe avec une autre
    empreinte, sauf `reconstruire`."""
    arbre = arbre or o.racine
    if a_blanc:
        for image in images:
            commande = commande_construction(image, arbre)
            o.afficher(f"  {commande_affichee(commande, o.racine)}")
            o.afficher(
                f"  docker tag {image.candidat} {image.ref}  (si absente ou égale)"
            )
        return []
    avant = {image.ref: empreinte(o, image.ref) for image in images}
    neuves: dict[str, str] = {}
    for image in images:
        s = o.lanceur(commande_construction(image, arbre))
        lu = empreinte(o, image.candidat) if s.code == 0 else None
        if lu is None:
            raise Arret(
                Code.CONSTRUCTION, f"construction de {image.ref} en échec", s.stderr
            )
        neuves[image.ref] = lu
    conflits = [
        i for i in images if avant[i.ref] is not None and avant[i.ref] != neuves[i.ref]
    ]
    if conflits and not reconstruire:
        for image in images:
            o.lanceur(retirer_candidat(image, o.racine))
        raise Arret(
            Code.PREREQUIS,
            "une étiquette = une construction : "
            + " ; ".join(
                f"{i.ref} existe avec {str(avant[i.ref])[:19]}…, la construction à"
                f" l'étiquette donne {neuves[i.ref][:19]}…"
                for i in conflits
            )
            + " — `--reconstruire` pour la réécrire (journalisé)",
        )
    faites: list[Construite] = []
    for image in images:
        ancienne, neuve = avant[image.ref], neuves[image.ref]
        if ancienne == neuve:
            statut = "inchangée"
        else:
            o.lanceur(etiqueter(image, o.racine))
            statut = (
                "construite"
                if ancienne is None
                else f"réécrite (ancienne empreinte {ancienne})"
            )
        o.lanceur(retirer_candidat(image, o.racine))
        faites.append(Construite(image, neuve, statut))
    return faites


def presentes(o: Outils, images: list[Image]) -> list[Construite]:
    """Les images présentes sur le poste, et leur empreinte ; sinon refus."""
    absentes = [i.ref for i in images if empreinte(o, i.ref) is None]
    if absentes:
        raise Arret(
            Code.PREREQUIS,
            f"images absentes du poste : {', '.join(absentes)} — lancez"
            " `livrer construire` à l'étiquette",
        )
    return [Construite(i, str(empreinte(o, i.ref)), "présente") for i in images]


def noter_construction(o: Outils, etiquette: str, faites: list[Construite]) -> Path:
    """La construction d'une étiquette, gardée hors dépôt pour le journal qui suit."""
    dossier = o.constructions
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / f"{etiquette}.json"
    chemin.write_text(
        json.dumps(
            {
                c.image.ref: {"empreinte": c.empreinte, "statut": c.statut}
                for c in faites
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return chemin


def avec_construction(
    o: Outils, etiquette: str, presentes_: list[Construite]
) -> list[Construite]:
    """Les images présentes, avec le statut de leur construction à cette étiquette
    quand l'empreinte est la même (« réécrite (ancienne …) » reste visible)."""
    chemin = o.constructions / f"{etiquette}.json"
    try:
        notes: dict[str, dict[str, str]] = json.loads(
            chemin.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError):
        return presentes_
    return [
        Construite(c.image, c.empreinte, notes[c.image.ref]["statut"])
        if notes.get(c.image.ref, {}).get("empreinte") == c.empreinte
        else c
        for c in presentes_
    ]
