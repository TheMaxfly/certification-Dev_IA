"""API Manga package.

`API_VERSION` est la version du contrat HTTP publié. Elle est la constante de
référence : `Settings.app_version` en hérite, les métadonnées OpenAPI aussi, et
`tests/test_version.py` vérifie qu'elle n'a pas divergé de `pyproject.toml`.
"""

API_VERSION = "0.4.0"
