"""Le paquet du module 09, chargé par son chemin : aucune copie de son code ici.

`service_embedding` n'est pas une dépendance : ce module réutilise le calcul et la
confrontation de `make verify-poste`, à la lettre. Son dossier `config/` reste à côté de
ses sources, sur le poste comme dans l'image.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

RACINE_DEPOT = Path(__file__).resolve().parents[3]
PAQUET = RACINE_DEPOT / "09_service_embedding" / "src" / "service_embedding"
NOM = "service_embedding"
INSTANCE = "embeddinggemma"


def charger_paquet() -> ModuleType:
    """Le paquet du module 09, depuis son dossier ; une seule fois par processus."""
    deja = sys.modules.get(NOM)
    if deja is not None:
        return deja
    spec = importlib.util.spec_from_file_location(
        NOM, PAQUET / "__init__.py", submodule_search_locations=[str(PAQUET)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"paquet du module 09 introuvable : {PAQUET}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[NOM] = module
    spec.loader.exec_module(module)
    return module


def module(nom: str) -> ModuleType:
    """Un module du paquet (`equivalence`, `client`…), le paquet chargé d'abord."""
    charger_paquet()
    return importlib.import_module(f"{NOM}.{nom}")
