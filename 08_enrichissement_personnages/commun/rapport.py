"""Rapports de phase, et verrou entre reconnaissance et collecte.

Les deux specs de collecte imposent un arret entre les deux phases. Le verrou
est ici **structurel** : `collecte` refuse de demarrer tant qu'un rapport de
reconnaissance ne porte pas une validation ecrite a la main. L'outil ecrit le
rapport avec le statut en attente ; il ne se valide jamais lui-meme.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

STATUT_ATTENTE = "EN ATTENTE DE VALIDATION"
STATUT_VALIDE = "VALIDE"
_MOTIF_STATUT = re.compile(r"^Statut\s*:\s*(.+)$", re.MULTILINE)


class ReconnaissanceNonValidee(RuntimeError):
    """La phase de collecte est verrouillee tant que la reconnaissance ne l'est pas."""


def horodatage() -> str:
    """Horodatage UTC complet — deux runs le meme jour ne s'ecrasent pas."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def chemin_rapport(dossier: Path, source: str, phase: str, quand: str) -> Path:
    return dossier / f"{source}_{phase}_{quand}.md"


def ecrire(
    dossier: Path,
    *,
    source: str,
    phase: str,
    environnement: dict[str, str],
    perimetre: dict[str, str],
    resultats: dict[str, str],
    ecarts: list[str],
    non_etabli: list[str],
    quand: str | None = None,
) -> Path:
    """Ecrit un rapport a la structure imposee par le paragraphe 8.

    `non_etabli` ne peut pas etre vide : une section « ce que je n'ai pas pu
    etablir » vide par principe est un rapport qui ne dit pas ce qu'il ignore.
    """
    if not non_etabli:
        raise ValueError(
            "section « ce que je n'ai pas pu etablir » vide — obligatoire, "
            "jamais vide par principe"
        )
    quand = quand or horodatage()
    lignes = [
        f"# {source} — {phase} — {quand}",
        "",
        f"Statut : {STATUT_ATTENTE}",
        "",
        "> Ce statut est ecrit par l'outil. Le passer a "
        f"`{STATUT_VALIDE}` est un acte humain : c'est lui qui deverrouille la",
        "> phase suivante.",
        "",
        "## 1. Environnement declare",
        "",
        "| | |",
        "|---|---|",
    ]
    lignes += [f"| {c} | {v} |" for c, v in environnement.items()]
    lignes += ["", "## 2. Perimetre", "", "| | |", "|---|---|"]
    lignes += [f"| {c} | {v} |" for c, v in perimetre.items()]
    lignes += ["", "## 3. Resultats", "", "| | |", "|---|---|"]
    lignes += [f"| {c} | {v} |" for c, v in resultats.items()]
    lignes += ["", "## 4. Ecarts aux attendus", ""]
    lignes += [f"- {e}" for e in ecarts] or ["- Aucun ecart constate."]
    lignes += ["", "## 5. Ce que je n'ai pas pu etablir", ""]
    lignes += [f"- {e}" for e in non_etabli]
    lignes.append("")

    dossier.mkdir(parents=True, exist_ok=True)
    chemin = chemin_rapport(dossier, source, phase, quand)
    chemin.write_text("\n".join(lignes), encoding="utf-8")
    return chemin


def statut(chemin: Path) -> str | None:
    """Lit le statut porte par un rapport."""
    trouve = _MOTIF_STATUT.search(chemin.read_text(encoding="utf-8"))
    return trouve.group(1).strip() if trouve else None


def exiger_valide(dossier: Path, motif: str, quoi: str, remede: str) -> Path:
    """Rend le document valide le plus recent repondant au motif, ou leve.

    C'est le verrou generique du projet : sans lui, un arret impose par une
    spec ne serait qu'une convention que rien n'empeche de franchir.
    """
    candidats = sorted(dossier.glob(motif))
    if not candidats:
        raise ReconnaissanceNonValidee(f"aucun {quoi} dans {dossier}. {remede}")
    valides = [c for c in candidats if statut(c) == STATUT_VALIDE]
    if not valides:
        dernier = candidats[-1]
        raise ReconnaissanceNonValidee(
            f"{quoi} non valide : {dernier.name} porte « {statut(dernier)} ». "
            f"L'etape suivante reste verrouillee tant que le statut n'est pas "
            f"passe a « {STATUT_VALIDE} » a la main."
        )
    return valides[-1]


def exiger_reconnaissance_validee(dossier: Path, source: str) -> Path:
    """Verrou du paragraphe 6 : pas de collecte sans reconnaissance validee."""
    return exiger_valide(
        dossier,
        f"{source}_mesures_*.md",
        "rapport de mesures de la phase A",
        "Lancer d'abord `reconnaissance --etape inventaire` puis `--etape mesures`.",
    )


def exiger_definition_validee(dossier: Path, source: str) -> Path:
    """Verrou du paragraphe 4.3 : definir avant de compter.

    « Ecrire et soumettre la definition de section personnages exploitable
    avant d'en compter une seule. » Le verrou rend cet arret structurel.
    """
    return exiger_valide(
        dossier,
        f"{source}_definition_*.md",
        "definition de « section personnages exploitable »",
        "Lancer d'abord `reconnaissance --etape inventaire`, puis rediger "
        "et valider la definition.",
    )
