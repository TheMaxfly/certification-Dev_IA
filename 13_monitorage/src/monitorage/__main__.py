"""`python -m monitorage` : le serveur, la sonde périodique et l'exporteur.

`python -m monitorage mesurer` : un passage à la main, journalisé, rendu sur la sortie
standard ; sans serveur, pour mesurer l'écart d'équivalence.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from types import FrameType

from monitorage import journal
from monitorage.configuration import Configuration, ConfigurationInvalide, charger
from monitorage.exporteur import LecteurMesure
from monitorage.metriques import Metriques
from monitorage.serveur import creer_serveur
from monitorage.sonde import Sonde

TIC_S = 1.0


def executer(
    configuration: Configuration,
    sonde: Sonde,
    lecteur: LecteurMesure | None,
    arret: threading.Event,
) -> None:
    """La sonde à sa période, la mesure à la sienne, jusqu'à l'arrêt."""
    prochaine_sonde = time.monotonic() + configuration.delai_initial_s
    prochaine_base = time.monotonic()
    while not arret.is_set():
        if time.monotonic() >= prochaine_sonde:
            sonde.passage()
            prochaine_sonde = time.monotonic() + configuration.periode_s
        if lecteur is not None and time.monotonic() >= prochaine_base:
            lecteur.lire()
            prochaine_base = time.monotonic() + configuration.periode_base_s
        arret.wait(TIC_S)


def mesurer(sonde: Sonde) -> int:
    ligne = sonde.passage(origine="mesure")
    if ligne is None:
        return 1
    print(json.dumps(ligne, ensure_ascii=False), flush=True)
    return 0


def principal(arguments: list[str]) -> int:
    try:
        configuration = charger(os.environ)
    except ConfigurationInvalide as exc:
        print(f"configuration refusée : {exc}", file=sys.stderr)
        return 2
    metriques = Metriques(lecture_base=configuration.lecture_base)
    sonde = Sonde(configuration, metriques)
    if arguments == ["mesurer"]:
        return mesurer(sonde)
    if arguments:
        print(__doc__, file=sys.stderr)
        return 2
    lecteur = (
        LecteurMesure(configuration.dsn, metriques)
        if configuration.lecture_base and configuration.dsn
        else None
    )
    serveur = creer_serveur(configuration.hote, configuration.port, metriques)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    arret = threading.Event()

    def arreter(_signal: int, _cadre: FrameType | None) -> None:
        arret.set()

    signal.signal(signal.SIGTERM, arreter)
    signal.signal(signal.SIGINT, arreter)
    journal.evenement(
        "demarrage",
        encodage=configuration.encodage,
        periode_s=configuration.periode_s,
        lecture_base=configuration.lecture_base,
    )
    try:
        executer(configuration, sonde, lecteur, arret)
    finally:
        serveur.shutdown()
        serveur.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(principal(sys.argv[1:]))
