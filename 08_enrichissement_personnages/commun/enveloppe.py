"""Enveloppe de run : etat, reprise, et compteurs qui se nomment.

Regle de nommage des compteurs, non negociable. Tout compteur porte dans son
nom ce qu'il compte.

Motif : l'etat de la collecte Kitsu nomme `items` un compteur de **liens**
oeuvre x personnage. Repris comme un nombre de personnages, il a produit une
surestimation de 14 % — 39 161 au lieu de 34 293 —, propagee dans plusieurs
documents de pilotage avant d'etre corrigee. La definition etait dans le nom
qu'on n'a pas donne.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

#: Noms qui ne disent pas ce qu'ils comptent. Refuses a l'ecriture.
NOMS_GENERIQUES = frozenset(
    {
        "items",
        "item",
        "count",
        "counts",
        "total",
        "totaux",
        "n",
        "nb",
        "num",
        "nombre",
        "entries",
        "records",
        "data",
        "resultats",
        "valeurs",
        "elements",
    }
)


class NomDeCompteurGenerique(ValueError):
    """Un compteur doit dire ce qu'il compte."""


def verifier_nom(nom: str) -> str:
    """Rejette un nom generique. Rend le nom si valide."""
    if nom.strip().lower() in NOMS_GENERIQUES:
        raise NomDeCompteurGenerique(
            f"compteur {nom!r} : un nom generique ne dit pas ce qu'il compte. "
            f"Employer par exemple `articles_traites`, `liens_collectes`, "
            f"`personnages_distincts`, `series_du_perimetre`."
        )
    return nom


@dataclass
class Enveloppe:
    """Etat d'un run, suffisant pour reprendre sans reteledecharger."""

    source: str
    partition: str
    date_run: str
    series_collectees: set[int] = field(default_factory=set)
    echecs: dict[int, str] = field(default_factory=dict)
    compteurs: dict[str, int] = field(default_factory=dict)

    def incrementer(self, nom: str, de: int = 1) -> int:
        """Incremente un compteur, apres validation de son nom."""
        verifier_nom(nom)
        self.compteurs[nom] = self.compteurs.get(nom, 0) + de
        return self.compteurs[nom]

    def marquer_collectee(self, series_id: int) -> None:
        self.series_collectees.add(series_id)
        self.echecs.pop(series_id, None)

    def marquer_echec(self, series_id: int, motif: str) -> None:
        """Un echec se documente ; il ne disparait pas."""
        if not motif.strip():
            raise ValueError("un echec sans motif est un trou silencieux")
        self.echecs[series_id] = motif

    def reste_a_faire(self, perimetre: list[int]) -> list[int]:
        """Series du perimetre ni collectees ni en echec — l'ordre est preserve."""
        faits = self.series_collectees | set(self.echecs)
        return [s for s in perimetre if s not in faits]

    def verifier_egalite_ensembles(self, perimetre: list[int]) -> None:
        """Perimetre = collectees union echecs documentes. Aucun trou silencieux."""
        attendu = set(perimetre)
        obtenu = self.series_collectees | set(self.echecs)
        manquantes = attendu - obtenu
        intruses = obtenu - attendu
        if manquantes or intruses:
            raise AssertionError(
                f"egalite d'ensembles rompue — "
                f"{len(manquantes)} series du perimetre sans issue documentee, "
                f"{len(intruses)} series hors perimetre dans l'etat"
            )

    def enregistrer(self, chemin: Path) -> Path:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        charge = {
            "source": self.source,
            "partition": self.partition,
            "date_run": self.date_run,
            "series_collectees": sorted(self.series_collectees),
            "echecs": {str(k): v for k, v in sorted(self.echecs.items())},
            "compteurs": dict(sorted(self.compteurs.items())),
        }
        chemin.write_text(
            json.dumps(charge, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return chemin

    @classmethod
    def charger(cls, chemin: Path) -> Enveloppe | None:
        """Rend l'enveloppe d'un run interrompu, ou None s'il n'y en a pas."""
        if not chemin.exists():
            return None
        brut = json.loads(chemin.read_text(encoding="utf-8"))
        for nom in brut.get("compteurs", {}):
            verifier_nom(nom)
        return cls(
            source=brut["source"],
            partition=brut["partition"],
            date_run=brut["date_run"],
            series_collectees=set(brut.get("series_collectees", [])),
            echecs={int(k): v for k, v in brut.get("echecs", {}).items()},
            compteurs=dict(brut.get("compteurs", {})),
        )
