"""La documentation interactive (Swagger UI) : sur réglage, désactivée par défaut.

Servie à `/v1/docs` quand `[documentation] interactive` (ou
`API_MODELE_DOCUMENTATION`) est vrai ; sinon la route n'existe pas (404). La page lit
le contrat publié (`/v1/openapi.json`, sans clé, déjà public dans le dépôt). Les
routes protégées s'y essaient avec une clé, saisie dans « Authorize » (en-tête
`X-API-Key`) : sans clé, elles rendent 401, comme ailleurs.

Swagger UI est vendu dans `statique/`, avec ses empreintes et sa licence : la page ne
charge rien hors de l'API et fonctionne sans réseau. Version 5.33.1, publiée le
2026-10-01. Une politique de sécurité du contenu (CSP) ne laisse charger que ces deux
fichiers, servis par l'API, et le script d'amorce de la page, et n'autorise de requêtes
que vers l'API elle-même.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
from pathlib import Path

VERSION_SWAGGER_UI = "5.33.1"
STATIQUE = Path(__file__).parent / "statique"
SCRIPT = "swagger-ui-bundle.js"
STYLE = "swagger-ui.css"
TYPES = {SCRIPT: "text/javascript", STYLE: "text/css"}


def ressource(nom: str) -> tuple[bytes, str] | None:
    """(contenu, type) de l'un des deux fichiers vendus ; `None` pour tout autre nom."""
    type_ = TYPES.get(nom)
    if type_ is None:
        return None
    return (STATIQUE / nom).read_bytes(), type_


def amorce(contrat: str) -> str:
    """Le script qui monte Swagger UI sur le contrat ; sans validateur distant."""
    reglages = {
        "url": contrat,
        "dom_id": "#swagger-ui",
        "validatorUrl": None,
        "persistAuthorization": False,
        "tryItOutEnabled": False,
    }
    return f"SwaggerUIBundle({json.dumps(reglages)});"


def politique(script_amorce: str) -> str:
    """La CSP de la page : les deux fichiers de l'API, l'amorce (par son empreinte), et
    des requêtes vers l'API seulement."""
    empreinte = base64.b64encode(
        hashlib.sha256(script_amorce.encode("utf-8")).digest()
    ).decode("ascii")
    return "; ".join(
        (
            "default-src 'none'",
            f"script-src 'self' 'sha256-{empreinte}'",
            # Swagger UI pose des attributs `style` : 'unsafe-inline' pour les styles.
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "connect-src 'self'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
        )
    )


def page(titre: str, contrat: str, base: str) -> tuple[str, str]:
    """(HTML de la page, sa CSP) ; `base` : l'adresse de la page et de ses fichiers."""
    script_amorce = amorce(contrat)
    corps = f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(titre)} — documentation</title>
<link rel="stylesheet" href="{base}/{STYLE}">
</head>
<body>
<div id="swagger-ui"></div>
<script src="{base}/{SCRIPT}"></script>
<script>{script_amorce}</script>
</body>
</html>
"""
    return corps, politique(script_amorce)
