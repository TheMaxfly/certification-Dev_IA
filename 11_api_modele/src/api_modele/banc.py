"""Le classement du banc de mesure (module 10), chargé par son chemin.

Ce que l'API sert est ce que le banc a mesuré : le rattachement des fragments, le
score d'une série (son meilleur fragment), le départage et la limitation au
catalogue sont ceux du module 10, réutilisés et non réécrits.

Le paquet `mesures_recherche` n'est pas installé : il est chargé depuis
`10_mesures_recherche/src/`, comme le module 10 charge `atteignabilite.py` du
module 05. L'API n'installe ainsi que ce qu'elle exécute (ni MLflow, ni
scikit-learn) ; les versions dont le calcul dépend (numpy, pgvector, psycopg,
typer) sont épinglées à celles du verrou du module 10, et `tests/test_verrous.py`
compare les deux verrous.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

RACINE_DEPOT = Path(__file__).resolve().parents[3]
PAQUET = RACINE_DEPOT / "10_mesures_recherche" / "src" / "mesures_recherche"
NOM = "mesures_recherche"


def charger_paquet() -> ModuleType:
    """Le paquet du module 10, depuis son dossier ; une seule fois par processus."""
    deja = sys.modules.get(NOM)
    if deja is not None:
        origine = Path(deja.__file__ or "").resolve().parent
        if origine != PAQUET.resolve():
            raise ImportError(f"{NOM} déjà chargé depuis {origine}, pas de {PAQUET}")
        return deja
    spec = importlib.util.spec_from_file_location(
        NOM, PAQUET / "__init__.py", submodule_search_locations=[str(PAQUET)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"classement du banc introuvable : {PAQUET}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[NOM] = module
    spec.loader.exec_module(module)
    return module


def module(nom: str) -> ModuleType:
    """Un module du paquet (`entites`, `recherche`…), le paquet chargé d'abord."""
    charger_paquet()
    return importlib.import_module(f"{NOM}.{nom}")
