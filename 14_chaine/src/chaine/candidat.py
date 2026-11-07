"""Le fichier de candidat : lecture, schéma fermé, contrôle git.

Un candidat dit ce que la chaîne doit juger : le corpus, l'encodage (nouveau, avec son
modèle, ou existant, par son numéro), le **numéro** de la mesure du module 10, la règle
(le fichier unique du module 10), une référence de rejeu facultative et l'état attendu
de la préproduction au départ. Schéma fermé : une clé inconnue, une clé manquante ou un
type faux → `CANDIDAT_INVALIDE` (21). Le fichier doit être suivi par git et identique
à `HEAD`, sinon `PREREQUIS` (20).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chaine.codes import Arret, Code
from chaine.environnement import RACINE_DEPOT

REGLE = "10_mesures_recherche/config/promotion_corpus_v2.toml"
CORPUS = ("v2",)
MODELES = {"BAAI/bge-m3": "bge-m3", "google/embeddinggemma-300m": "embeddinggemma"}
RUN = re.compile(r"^[0-9a-f]{32}$")
REVISION = re.compile(r"^[0-9a-f]{40}$")

# table → clé → (type, obligatoire)
SCHEMA: dict[str, dict[str, tuple[type, bool]]] = {
    "": {"nom": (str, True), "motif": (str, True)},
    "corpus": {"version": (str, True), "nouvelle_version": (bool, True)},
    "encodage": {
        "nouveau": (bool, True),
        "instance": (str, True),
        "numero": (int, False),
        "modele": (str, False),
        "revision": (str, False),
        "precision": (str, False),
    },
    "mesure": {"numero": (int, True), "reference_run": (str, False)},
    "regle": {"fichier": (str, True)},
    "preproduction": {"encodage_en_service": (int, True)},
}


@dataclass(frozen=True)
class Candidat:
    chemin: Path
    nom: str
    motif: str
    corpus: str
    nouvelle_version: bool
    nouvel_encodage: bool
    instance: str
    encodage: int | None  # existant ; None pour un nouvel encodage (lu dans le bilan)
    modele: str | None
    revision: str | None
    precision: str | None
    mesure: int
    reference_run: str | None
    regle: str
    encodage_en_service: int


def invalide(chemin: Path, motif: str) -> Arret:
    return Arret(Code.CANDIDAT_INVALIDE, f"candidat {chemin.name} : {motif}")


def du_bon_type(valeur: object, attendu: type) -> bool:
    # bool est un int en Python : un booléen n'est pas un nombre, ni l'inverse.
    if attendu is int:
        return isinstance(valeur, int) and not isinstance(valeur, bool)
    return isinstance(valeur, attendu)


def verifier_schema(chemin: Path, donnees: dict[str, Any]) -> None:
    tables = {k for k, v in donnees.items() if isinstance(v, dict)}
    racine = {k: v for k, v in donnees.items() if k not in tables}
    inconnues = tables - set(SCHEMA)
    if inconnues:
        raise invalide(chemin, f"table(s) inconnue(s) {sorted(inconnues)}")
    for table, cles in SCHEMA.items():
        if table and table not in tables:
            raise invalide(chemin, f"table [{table}] manquante")
        contenu: dict[str, Any] = racine if not table else donnees[table]
        nom = f"[{table}] " if table else ""
        for cle in contenu:
            if cle not in cles:
                raise invalide(chemin, f"clé inconnue {nom}{cle}")
        for cle, (attendu, obligatoire) in cles.items():
            if cle not in contenu:
                if obligatoire:
                    raise invalide(chemin, f"clé manquante {nom}{cle}")
                continue
            if not du_bon_type(contenu[cle], attendu):
                raise invalide(chemin, f"{nom}{cle} : {attendu.__name__} attendu")


def lire(chemin: Path) -> Candidat:
    """Le candidat, ou un refus (21) — sans contrôle git (voir `exiger_suivi`)."""
    try:
        donnees = tomllib.loads(chemin.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise Arret(Code.PREREQUIS, f"candidat introuvable : {chemin.name}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise invalide(chemin, f"TOML illisible ({exc})") from exc
    verifier_schema(chemin, donnees)
    e: dict[str, Any] = donnees["encodage"]
    nouveau: bool = e["nouveau"]
    propres_au_nouveau = {"modele", "revision", "precision"}
    if nouveau:
        manquent = sorted(propres_au_nouveau - set(e))
        if manquent or "numero" in e:
            raise invalide(
                chemin,
                "un nouvel encodage déclare modele, revision et precision,"
                " pas de numero",
            )
        if e["modele"] not in MODELES or MODELES[e["modele"]] != e["instance"]:
            raise invalide(
                chemin, f"modèle {e['modele']!r} et instance {e['instance']!r}"
            )
        if not REVISION.match(e["revision"]):
            raise invalide(chemin, "revision : 40 caractères hexadécimaux attendus")
    elif "numero" not in e or propres_au_nouveau & set(e):
        raise invalide(
            chemin, "un encodage existant déclare son numero, et seulement lui"
        )
    elif e["instance"] not in MODELES.values():
        raise invalide(chemin, f"instance {e['instance']!r} inconnue du module 09")
    m: dict[str, Any] = donnees["mesure"]
    if "reference_run" in m and not RUN.match(m["reference_run"]):
        raise invalide(chemin, "reference_run : un identifiant de run MLflow (32 hex)")
    if donnees["corpus"]["version"] not in CORPUS:
        raise invalide(
            chemin, f"corpus {donnees['corpus']['version']!r} : {CORPUS} seul"
        )
    if donnees["regle"]["fichier"] != REGLE:
        raise invalide(chemin, f"la règle est le fichier unique du module 10 : {REGLE}")
    if donnees["nom"] != chemin.stem:
        raise invalide(chemin, f"nom {donnees['nom']!r} ≠ nom du fichier")
    return Candidat(
        chemin=chemin,
        nom=donnees["nom"],
        motif=donnees["motif"],
        corpus=donnees["corpus"]["version"],
        nouvelle_version=donnees["corpus"]["nouvelle_version"],
        nouvel_encodage=nouveau,
        instance=e["instance"],
        encodage=None if nouveau else e["numero"],
        modele=e.get("modele"),
        revision=e.get("revision"),
        precision=e.get("precision"),
        mesure=m["numero"],
        reference_run=m.get("reference_run"),
        regle=donnees["regle"]["fichier"],
        encodage_en_service=donnees["preproduction"]["encodage_en_service"],
    )


def resoudre(argument: str) -> Path:
    """Un chemin de candidat, relatif à la racine du dépôt s'il n'est pas absolu."""
    chemin = Path(argument)
    return chemin if chemin.is_absolute() else RACINE_DEPOT / chemin
