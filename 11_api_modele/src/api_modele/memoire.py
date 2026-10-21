"""La mémoire de l'API : relevés, et le seuil de `verify-poste`.

    uv run python -m api_modele.memoire   # code 1 si la mémoire ne suffit pas

Au démarrage, le chargement des vecteurs culmine à environ 1,5 Go (1,8 Go dans le
test d'égalité avec le banc), puis l'API rend au système ce qu'elle a libéré
(`malloc_trim`) : environ 0,5 Go résidents. Le seuil de l'étape d'équivalence de
`verify-poste` est celui du service d'embedding (module 09, inchangé) plus ce besoin
(`config/reglages.toml`, [memoire]).
"""

from __future__ import annotations

import ctypes
import gc
import json
import resource
import sys
from pathlib import Path

from service_embedding import mesures  # pyright: ignore[reportMissingTypeStubs]

from api_modele.configuration import besoin_memoire


def rss_octets() -> int:
    """Mémoire résidente du processus."""
    for ligne in Path("/proc/self/status").read_text().splitlines():
        if ligne.startswith("VmRSS:"):
            return int(ligne.split()[1]) * 1024
    return 0


def pic_octets() -> int:
    """Pic de mémoire résidente du processus depuis son lancement."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def rendre_au_systeme() -> None:
    """Ramasser, puis rendre au système ce que l'allocateur garde (glibc)."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:  # pas de glibc : rien à rendre de cette façon
        return


def seuil_octets() -> int:
    seuil_service: int = mesures.SEUIL_RAM_DISPONIBLE
    return seuil_service + besoin_memoire()


def principal() -> int:
    disponible: int = mesures.meminfo()["MemAvailable"]
    seuil = seuil_octets()
    print(
        json.dumps(
            {
                "ram_disponible": disponible,
                "seuil_service_et_api": seuil,
                "besoin_api": besoin_memoire(),
                "tenu": disponible >= seuil,
            }
        )
    )
    return 0 if disponible >= seuil else 1


if __name__ == "__main__":
    sys.exit(principal())
