"""Boucle de run partagee par les deux collectes.

Tout ce qui n'est pas propre a une source vit ici : derivation du perimetre,
cadence, reprise, enveloppe NDJSON, manifeste, rapport. Une source n'apporte
que trois choses — son nom, sa cadence et sa fonction de recuperation.

Si cette boucle finissait dupliquee dans les deux collecteurs, le choix du
module unique aurait echoue et le rapport devrait le dire.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import manifeste, rapport
from .enveloppe import Enveloppe
from .limiteur import Limiteur
from .perimetre import DERIVATIONS, Serie, deriver


@dataclass(frozen=True)
class Collecteur:
    """Ce qu'une source apporte en propre."""

    nom: str
    intervalle_minimal_s: float
    motif_cadence: str
    user_agent: str
    #: Recupere le contenu brut d'une serie. Leve pour signaler un echec.
    recuperer: Callable[[Serie, Limiteur], dict]
    #: Appele une fois avant la boucle, avec tout le perimetre. Sert aux
    #: preparations par lots — resoudre 1 368 titres en 28 requetes plutot
    #: qu'en 1 368. Optionnel.
    preparer: Callable[[list[Serie], Limiteur], None] | None = None
    #: Appele une fois apres la boucle, avec le NDJSON produit. Rend les lignes
    #: de resultat propres a la source, fusionnees au rapport de collecte.
    #: C'est par la que passent les mesures qu'une seule source sait produire.
    mesurer: Callable[[Path], dict[str, str]] | None = None


def dossier_partition(racine: Path, source: str, partition: str) -> Path:
    return racine / "data" / "raw" / source / partition


def _enveloppe_ndjson(serie: Serie, source: str, quand: str, charge: dict) -> dict:
    """Chaque enregistrement porte l'identifiant catalogue. Zero exception.

    Sans cela, on referait au chargement le travail que le perimetre avait
    deja fait.
    """
    return {
        "series_id": serie.series_id,
        "cle_source": serie.cle_source,
        "rang_popularite": serie.rang_popularite,
        "source": source,
        "collecte_le": quand,
        "charge": charge,
    }


def reconnaissance(
    collecteur: Collecteur,
    *,
    dsn: str,
    racine: Path,
    non_etabli: list[str],
    resultats: dict[str, str] | None = None,
    ecarts: list[str] | None = None,
    environnement: dict[str, str] | None = None,
) -> Path:
    """Derive le perimetre, ecrit le rapport de reconnaissance. Ne collecte rien."""
    series = deriver(dsn, collecteur.nom)
    derivation = DERIVATIONS[collecteur.nom]
    env = {
        "cadence appliquee": f"{collecteur.intervalle_minimal_s} s entre requetes",
        "motif de la cadence": collecteur.motif_cadence,
        "user-agent": collecteur.user_agent,
        **(environnement or {}),
    }
    return rapport.ecrire(
        racine / "rapports",
        source=collecteur.nom,
        phase="reconnaissance",
        environnement=env,
        perimetre={
            "colonne source": derivation.colonne_source,
            "definition": derivation.definition,
            "series du perimetre": f"{len(series)}",
            "rang de popularite min / max": (
                f"{series[0].rang_popularite} / {series[-1].rang_popularite}"
                if series
                else "—"
            ),
        },
        resultats=resultats or {"mesures": "aucune — phase de derivation seule"},
        ecarts=ecarts or [],
        non_etabli=non_etabli,
    )


def collecte(
    collecteur: Collecteur,
    *,
    dsn: str,
    racine: Path,
    partition: str,
    quand: str,
    limite_series: int | None = None,
) -> Path:
    """Collecte le perimetre, en reprenant un run interrompu s'il en existe un.

    Verrouille par `exiger_reconnaissance_validee` : la phase ne demarre pas
    sans validation ecrite de la reconnaissance.
    """
    dossier_rapports = racine / "rapports"
    rapport.exiger_reconnaissance_validee(dossier_rapports, collecteur.nom)

    series = deriver(dsn, collecteur.nom)
    if limite_series is not None:
        series = series[:limite_series]
    par_id = {s.series_id: s for s in series}
    ordre = [s.series_id for s in series]

    dossier = dossier_partition(racine, collecteur.nom, partition)
    dossier.mkdir(parents=True, exist_ok=True)
    chemin_enveloppe = dossier / f".enveloppe_{collecteur.nom}.json"
    etat_run = Enveloppe.charger(chemin_enveloppe) or Enveloppe(
        source=collecteur.nom, partition=partition, date_run=quand
    )
    etat_run.incrementer(
        "series_du_perimetre",
        len(series) - etat_run.compteurs.get("series_du_perimetre", 0),
    )

    limiteur = Limiteur(collecteur.intervalle_minimal_s)
    if collecteur.preparer is not None:
        collecteur.preparer(series, limiteur)
    fichier = dossier / f"{collecteur.nom}_personnages.ndjson"

    with fichier.open("a", encoding="utf-8") as flux:
        for series_id in etat_run.reste_a_faire(ordre):
            serie = par_id[series_id]
            try:
                charge = collecteur.recuperer(serie, limiteur)
            except Exception as erreur:  # documente, jamais avale
                etat_run.marquer_echec(series_id, f"{type(erreur).__name__}: {erreur}")
                etat_run.incrementer("echecs_documentes")
            else:
                ligne = _enveloppe_ndjson(serie, collecteur.nom, quand, charge)
                flux.write(json.dumps(ligne, ensure_ascii=False) + "\n")
                etat_run.marquer_collectee(series_id)
                etat_run.incrementer("series_collectees")
            finally:
                etat_run.enregistrer(chemin_enveloppe)

    etat_run.verifier_egalite_ensembles(ordre)

    etat_manifeste = manifeste.charger_etat(
        dossier, source=collecteur.nom, partition=partition
    )
    entrees = (
        [
            manifeste.empreinte(
                fichier,
                source=collecteur.nom,
                date_run=quand,
                perimetre=f"{len(series)} series du catalogue",
            )
        ]
        if fichier.exists()
        else []
    )
    etat_manifeste = manifeste.fusionner(etat_manifeste, entrees, date_run=quand)
    manifeste.enregistrer(dossier, etat_manifeste)

    return rapport.ecrire(
        dossier_rapports,
        source=collecteur.nom,
        phase="collecte",
        environnement={
            "cadence appliquee": f"{collecteur.intervalle_minimal_s} s",
            "requetes autorisees": f"{limiteur.requetes_autorisees}",
            "attente cumulee": f"{limiteur.attente_cumulee_s:.1f} s",
        },
        perimetre={
            "partition": partition,
            "series du perimetre": f"{len(series)}",
        },
        resultats={
            **{nom: f"{v}" for nom, v in sorted(etat_run.compteurs.items())},
            **(
                collecteur.mesurer(fichier)
                if collecteur.mesurer is not None and fichier.exists()
                else {}
            ),
        },
        ecarts=[f"serie {sid} : {motif}" for sid, motif in etat_run.echecs.items()],
        non_etabli=[
            "Taux d'extraction reel par rapport a la projection de la phase A — "
            "a etablir en relisant le raw collecte.",
        ],
        quand=quand,
    )
