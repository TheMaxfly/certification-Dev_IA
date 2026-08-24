"""Environnement commun aux tests unitaires.

`app.main` construit l'application à l'import (`app = create_app()`), et
`Settings.from_env()` REFUSE désormais de démarrer sans `API_KEYS`. C'est le
comportement voulu — la défaillance fermée — et il s'applique aussi ici : les
tests posent donc un trousseau jetable avant tout import du paquet.

Cette clé n'ouvre rien : elle ne vaut que dans le processus pytest, contre une
application dont le pool PostgreSQL est simulé.
"""

from __future__ import annotations

import os

LIBELLE_TEST = "app_backend"
CLE_TEST = "cle-de-test-jetable-0123456789abcdef"

os.environ.setdefault("API_KEYS", f"{LIBELLE_TEST}:{CLE_TEST}")
