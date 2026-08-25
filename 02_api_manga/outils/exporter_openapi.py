"""Écrit le contrat OpenAPI de l'API dans `openapi.json`, à la racine du module.

Pourquoi versionner une sortie que l'application sait produire
--------------------------------------------------------------
`/openapi.json` est une sortie **volatile** : elle n'existe que si l'API tourne,
et elle change avec le code sans laisser de trace. Un consommateur qui intègre
l'API ne peut ni la relire à une date donnée, ni voir ce qui a bougé entre deux
versions.

Écrit dans le dépôt, le même contrat devient **opposable et diffable** : une
modification de route apparaît dans la revue de code au même titre que le code
qui la produit. C'est la différence entre « le contrat est quelque part » et
« le contrat est ici, daté, et son évolution est visible ».

Le fichier n'est pas maintenu à la main : `tests/test_openapi.py` refuse tout
écart entre lui et le schéma généré. S'il dérive, la suite tombe.

    uv run python outils/exporter_openapi.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

# `app.main` construit l'application A L'IMPORT, et `Settings.from_env()` refuse
# de démarrer sans `API_KEYS` : c'est la défaillance fermée, et elle s'applique
# ici aussi. D'où ce trousseau jetable, posé AVANT l'import — il ne vaut que
# dans ce processus, et le schéma décrit OÙ l'autorisation s'applique, jamais
# quelle clé l'ouvre. Aucune valeur d'ici n'atteint le fichier produit.
CLE_JETABLE = "x" * 40
os.environ.setdefault("API_KEYS", f"app_backend:{CLE_JETABLE}")

from app.main import create_app  # noqa: E402
from app.settings import ApiKey, Settings  # noqa: E402

DESTINATION = RACINE / "openapi.json"

REGLAGES_HORS_ENVIRONNEMENT = Settings(
    api_keys=(ApiKey(label="app_backend", secret=CLE_JETABLE),)
)


def contrat() -> dict:
    """Le schéma tel que l'application le sert sur `/openapi.json`."""
    return create_app(REGLAGES_HORS_ENVIRONNEMENT).openapi()


def serialiser(schema: dict) -> str:
    """Forme stable : clés triées, indentation fixe, UTF-8 littéral.

    Un export dont l'ordre des clés varie produirait un diff à chaque
    régénération, et le fichier cesserait d'être lisible en revue — ce qui lui
    ôterait sa seule raison d'être.
    """
    return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main() -> int:
    DESTINATION.write_text(serialiser(contrat()), encoding="utf-8")
    print(f"{DESTINATION.relative_to(RACINE)} écrit ({DESTINATION.stat().st_size} o)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
