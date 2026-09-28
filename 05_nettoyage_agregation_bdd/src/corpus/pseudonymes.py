"""Pseudonymes d'auteurs : empreinte, repérage, masquage, listes versionnées.

Décision D5 (2026-09-28). Le test mécanique ne distingue pas un chroniqueur d'un
personnage qui porte le même nom. Deux listes, versionnées dans
`database/donnees/`, disent ce que le mécanisme ne sait pas dire :

  - `corpus_references_masquees.csv` — les vraies références à un membre
    (remerciement, signature d'un tiers, collègue cité…). Le pseudonyme y est
    remplacé par `JETON` ; la critique reste dans le corpus. Masquer n'est pas
    filtrer ;
  - `corpus_homonymes_admis.csv` — les occurrences qui ne désignent pas un
    membre (personnage, mangaka, titre d'œuvre, texte Kitsu). Le test de
    non-fuite les tolère, et elles seules.

Les listes ne portent jamais le pseudonyme en clair, seulement son empreinte :
le dépôt est public, et une ligne « telle critique cite tel membre » en clair
republierait ce que l'anonymisation retire. L'empreinte d'une liste courte de
pseudonymes publics n'est pas un secret — elle évite la publication, elle ne
protège pas.

Une entrée qui ne correspond plus à rien est une ERREUR, pas un avertissement :
une liste qui dérive en silence finirait par tolérer ce qu'elle ne décrit plus.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

JETON = "[membre]"

NATURES = (
    "remerciement",
    "signature",
    "collegue",
    "citation",
    "interpellation",
    "auto_designation",
)
MOTIFS_HOMONYMIE = (
    "personnage",
    "mangaka",
    "lieu_fictif",
    "titre_oeuvre",
    "provenance_kitsu",
)

FICHIER_MASQUAGE = "corpus_references_masquees.csv"
FICHIER_HOMONYMES = "corpus_homonymes_admis.csv"


class ListeInvalide(Exception):
    """Une liste versionnée est mal formée ou ne correspond plus au corpus."""


def empreinte(pseudo: str) -> str:
    """16 premiers caractères hexadécimaux du SHA-256 du pseudonyme (UTF-8)."""
    return hashlib.sha256(pseudo.encode("utf-8")).hexdigest()[:16]


def motif(pseudo: str) -> re.Pattern[str]:
    """Le pseudonyme comme mot entier, casse ignorée.

    Équivalent Python de `\\m<pseudo>\\M` côté PostgreSQL : les 54 pseudonymes
    commencent et finissent par un caractère de mot (vérifié le 2026-09-28),
    donc « non précédé / non suivi d'un caractère de mot » dit la même chose.
    """
    return re.compile(r"(?<!\w)" + re.escape(pseudo) + r"(?!\w)", re.IGNORECASE)


def masquer(texte: str, pseudo: str) -> tuple[str, int]:
    """Remplace chaque occurrence du pseudonyme par `JETON`."""
    return motif(pseudo).subn(JETON, texte)


@dataclass(frozen=True)
class Entree:
    doc_key: str
    empreinte: str
    valeur: str


def lire_liste(chemin: Path, colonne: str, valeurs: tuple[str, ...]) -> list[Entree]:
    """Lit une liste versionnée en vérifiant en-tête, valeurs et unicité."""
    if not chemin.is_file():
        raise ListeInvalide(f"Liste introuvable : {chemin}")
    with chemin.open(encoding="utf-8", newline="") as f:
        lecteur = csv.DictReader(f)
        attendu = ["doc_key", "empreinte_pseudo", colonne]
        if lecteur.fieldnames != attendu:
            raise ListeInvalide(
                f"{chemin.name} : en-tête {lecteur.fieldnames}, attendu {attendu}"
            )
        entrees = [
            Entree(ligne["doc_key"], ligne["empreinte_pseudo"], ligne[colonne])
            for ligne in lecteur
        ]
    vues: set[tuple[str, str]] = set()
    for e in entrees:
        if e.valeur not in valeurs:
            raise ListeInvalide(f"{chemin.name} : valeur inconnue {e.valeur!r}")
        if not re.fullmatch(r"[0-9a-f]{16}", e.empreinte):
            raise ListeInvalide(f"{chemin.name} : empreinte mal formée {e.empreinte!r}")
        if (e.doc_key, e.empreinte) in vues:
            raise ListeInvalide(f"{chemin.name} : doublon {e.doc_key}")
        vues.add((e.doc_key, e.empreinte))
    return entrees


def lire_listes(donnees: Path) -> tuple[list[Entree], list[Entree]]:
    """Les deux listes ; un couple présent dans les deux est une contradiction."""
    masquage = lire_liste(donnees / FICHIER_MASQUAGE, "nature", NATURES)
    homonymes = lire_liste(donnees / FICHIER_HOMONYMES, "motif", MOTIFS_HOMONYMIE)
    communs = {(e.doc_key, e.empreinte) for e in masquage} & {
        (e.doc_key, e.empreinte) for e in homonymes
    }
    if communs:
        raise ListeInvalide(f"couples à la fois masqués et admis : {sorted(communs)}")
    return masquage, homonymes


def appliquer_masquage(
    docs: dict[str, dict], masquage: list[Entree], pseudos: dict[str, str]
) -> int:
    """Masque en place `doc_text` pour chaque entrée ; rend le nombre de
    remplacements. Échoue si une entrée ne désigne plus rien."""
    total = 0
    for e in masquage:
        pseudo = pseudos.get(e.empreinte)
        if pseudo is None:
            raise ListeInvalide(f"empreinte sans pseudonyme : {e.empreinte}")
        doc = docs.get(e.doc_key)
        if doc is None:
            raise ListeInvalide(f"document absent du corpus cible : {e.doc_key}")
        texte, n = masquer(doc["doc_text"], pseudo)
        if n == 0:
            raise ListeInvalide(
                f"{e.doc_key} : le pseudonyme {e.empreinte} n'y figure plus"
            )
        doc["doc_text"] = texte
        total += n
    return total


def verifier_homonymes(
    docs: dict[str, dict], homonymes: list[Entree], pseudos: dict[str, str]
) -> None:
    """Chaque homonyme admis doit toujours figurer dans son document."""
    for e in homonymes:
        pseudo = pseudos.get(e.empreinte)
        if pseudo is None:
            raise ListeInvalide(f"empreinte sans pseudonyme : {e.empreinte}")
        doc = docs.get(e.doc_key)
        if doc is None:
            raise ListeInvalide(f"document absent du corpus cible : {e.doc_key}")
        m = motif(pseudo)
        champs = (doc["doc_text"], doc["title"] or "", doc.get("metadata_json") or "")
        if not any(m.search(c) for c in champs):
            raise ListeInvalide(
                f"{e.doc_key} : l'homonyme {e.empreinte} n'y figure plus"
            )
