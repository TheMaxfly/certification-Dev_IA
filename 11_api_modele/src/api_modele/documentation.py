"""La documentation interactive (Swagger UI) : sur réglage, désactivée par défaut.

Servie à `/v1/docs` quand `[documentation] interactive` (ou
`API_MODELE_DOCUMENTATION`) est vrai ; sinon la route n'existe pas (404). La page lit
le contrat publié (`/v1/openapi.json`, sans clé, déjà public dans le dépôt). Les
routes protégées s'y essaient avec une clé, saisie dans « Authorize » (en-tête
`X-API-Key`) : sans clé, elles rendent 401, comme ailleurs.

Swagger UI vient d'un CDN, à version exacte et avec empreinte d'intégrité (SRI) : le
navigateur refuse un fichier qui ne serait plus celui-là. Version 5.33.1, publiée le
2026-10-01 ; les deux fichiers sont ceux du paquet npm, dont l'intégrité au registre a
été vérifiée le 2026-10-20. Une politique de sécurité du contenu (CSP) ne laisse
charger que ces deux fichiers et le script d'amorce de la page, et n'autorise de
requêtes que vers l'API elle-même.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json

VERSION_SWAGGER_UI = "5.33.1"
CDN = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{VERSION_SWAGGER_UI}"
SCRIPT = (
    f"{CDN}/swagger-ui-bundle.js",
    "sha384-ZPehFMQommnnuaZ4rpxgkgTT2DKFVp4hZC/7pLit+9Lek9T1YGSo23eHFbvNkXkw",
)
STYLE = (
    f"{CDN}/swagger-ui.css",
    "sha384-Ov4/wv3j2bmct8cDc5X4ngJZohVPzEmc6uDPH8WeljUxO5vtoykvMEfbu9Vh6RaW",
)


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
    """La CSP de la page : les deux fichiers du CDN, l'amorce (par son empreinte), et
    des requêtes vers l'API seulement."""
    empreinte = base64.b64encode(
        hashlib.sha256(script_amorce.encode("utf-8")).digest()
    ).decode("ascii")
    return "; ".join(
        (
            "default-src 'none'",
            f"script-src {SCRIPT[0]} 'sha256-{empreinte}'",
            # Swagger UI pose des attributs `style` : 'unsafe-inline' pour les styles.
            f"style-src {STYLE[0]} 'unsafe-inline'",
            "img-src 'self' data:",
            "connect-src 'self'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
        )
    )


def page(titre: str, contrat: str) -> tuple[str, str]:
    """(HTML de la page, sa CSP)."""
    script_amorce = amorce(contrat)
    corps = f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(titre)} — documentation</title>
<link rel="stylesheet" href="{STYLE[0]}" integrity="{STYLE[1]}" crossorigin="anonymous">
</head>
<body>
<div id="swagger-ui"></div>
<script src="{SCRIPT[0]}" integrity="{SCRIPT[1]}" crossorigin="anonymous"></script>
<script>{script_amorce}</script>
</body>
</html>
"""
    return corps, politique(script_amorce)
