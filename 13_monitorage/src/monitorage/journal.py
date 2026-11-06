"""Une ligne JSON par événement, sur la sortie d'erreur.

Jamais un texte de fragment, jamais un DSN, jamais le corps d'une alerte.
"""

from __future__ import annotations

import json
import sys


def evenement(nom: str, **champs: object) -> None:
    ligne = {"evenement": nom, **champs}
    print(json.dumps(ligne, ensure_ascii=False), file=sys.stderr, flush=True)
