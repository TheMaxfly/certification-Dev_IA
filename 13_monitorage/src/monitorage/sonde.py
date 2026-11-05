"""La sonde d'équivalence : un échantillon fixe réencodé, confronté à ses vecteurs.

Le calcul est celui du module 09 (`equivalence.comparer`), lu dans l'échantillon exporté
et auprès du service : aucun accès à la base. Le seuil est lu dans sa configuration,
jamais recopié. Chaque passage s'ajoute à un journal hors dépôt : date, cosinus,
identifiants des fragments sous le seuil. Aucun texte.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from monitorage import journal, service
from monitorage.configuration import Configuration
from monitorage.metriques import EtatSonde, Metriques

NOM_JOURNAL = "journal_sonde.jsonl"


class Sonde:
    def __init__(
        self,
        configuration: Configuration,
        metriques: Metriques,
        config_equivalence: Path | None = None,
        horloge: Callable[[], float] = time.time,
    ) -> None:
        self._configuration = configuration
        self._metriques = metriques
        self._config_equivalence = config_equivalence
        self._horloge = horloge

    def _charger_config(self, equivalence: Any) -> dict[str, Any]:
        if self._config_equivalence is not None:
            return equivalence.charger_config(self._config_equivalence)
        return equivalence.charger_config()

    def _echec(self, cause: str, detail: str) -> None:
        self._metriques.erreurs.labels(cause=cause).inc()
        journal.evenement("sonde_echec", cause=cause, detail=detail)

    def passage(self, origine: str = "sonde") -> dict[str, Any] | None:
        """Un passage ; `None` s'il échoue : les jauges gardent leur dernière valeur."""
        debut = time.monotonic()
        equivalence = service.module("equivalence")
        erreur_service = service.module("client").ErreurService
        c = self._configuration
        config = self._charger_config(equivalence)
        chemin = c.echantillon or equivalence.chemin_export(config, c.encodage)
        try:
            export = json.loads(chemin.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._echec("echantillon_absent", "fichier de l'échantillon absent")
            return None
        except (OSError, ValueError) as exc:
            self._echec("autre", type(exc).__name__)
            return None
        client = service.module("client").ClientService(
            service.module("configuration").charger(service.INSTANCE),
            hote=c.hote_service,
        )
        try:
            resultat = equivalence.comparer(client, export, config)
        except erreur_service:
            self._echec("service_injoignable", "le service ne répond pas")
            return None
        except Exception as exc:  # une sonde ne tombe pas : elle le dit, et continue
            self._echec("autre", type(exc).__name__)
            return None
        return self._consigner(resultat, config, origine, debut)

    def _consigner(
        self,
        resultat: dict[str, Any],
        config: dict[str, Any],
        origine: str,
        debut: float,
    ) -> dict[str, Any]:
        seuil = float(config["seuils"]["equivalent"])
        tranches = sum(1 for v in resultat["par_tranche"].values() if v < seuil)
        duree = time.monotonic() - debut
        maintenant = self._horloge()
        encodage = int(resultat["encodage_id"])
        ligne: dict[str, Any] = {
            "date": datetime.fromtimestamp(maintenant, UTC).isoformat(
                timespec="seconds"
            ),
            "origine": origine,
            "encodage": encodage,
            "cos_min": resultat["cos_min"],
            "cos_mediane": resultat["cos_mediane"],
            "tranches_sous_seuil": tranches,
            "fragments_sous_seuil": [
                {"chunk_id": s["chunk_id"], "cos": s["cos"]}
                for s in resultat["sous_equivalent"]
            ],
            "duree_s": round(duree, 3),
        }
        self._journaliser(ligne, config)
        self._metriques.publier_sonde(
            EtatSonde(
                encodage=encodage,
                cosinus_min=float(resultat["cos_min"]),
                cosinus_mediane=float(resultat["cos_mediane"]),
                tranches_sous_seuil=tranches,
                horodatage=maintenant,
                duree_s=duree,
            )
        )
        journal.evenement("sonde_passage", **{k: ligne[k] for k in RESUME})
        return ligne

    def _journaliser(self, ligne: dict[str, Any], config: dict[str, Any]) -> None:
        equivalence = service.module("equivalence")
        chemin = self._configuration.journal or (
            equivalence.chemin_export(config, 0).parent / NOM_JOURNAL
        )
        try:
            with chemin.open("a", encoding="utf-8") as sortie:
                sortie.write(json.dumps(ligne, ensure_ascii=False) + "\n")
        except OSError as exc:
            self._echec("autre", type(exc).__name__)


RESUME = ("origine", "encodage", "cos_min", "tranches_sous_seuil", "duree_s")
