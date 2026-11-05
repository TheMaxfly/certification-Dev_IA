"""Essai d'intégration du Compose : l'application conteneurisée répond, de bout en bout.

Joué par le service `essai` de `compose.integration.yml`, une fois l'application saine.
Il parle à l'application par le réseau du Compose, comme un navigateur :

1. `/sante` : 200, l'API jointe ;
2. `/` : la page, le profil actif, la mention, les en-têtes de sécurité ;
3. `POST /question` : la réponse et une carte de série, depuis la doublure ;
4. le même, la doublure en refus de débit : l'encart, `Retry-After`, la question ;
5. `/a-propos`, `/metrics` ; la doublure n'a vu aucun écart au contrat.

Bibliothèque standard seule ; code 1 au premier écart.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message

BASE = os.environ["APPLICATION_URL"]
DOUBLURE = os.environ["DOUBLURE_URL"]


def appeler(
    url: str, donnees: bytes | None = None, type_: str | None = None
) -> tuple[int, Message, str]:
    entetes = {"Content-Type": type_} if type_ else {}
    requete = urllib.request.Request(url, data=donnees, headers=entetes)
    try:
        with urllib.request.urlopen(requete, timeout=10) as r:  # noqa: S310
            return r.status, r.headers, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8")


def poser(question: str) -> tuple[int, Message, str]:
    corps = urllib.parse.urlencode(
        {
            "question": question,
            "profil": "mistral-large-2512",
            "temperature": "0.0",
            "reformuler": "1",
            "historique": "",
        }
    ).encode()
    return appeler(f"{BASE}/question", corps, "application/x-www-form-urlencoded")


def scenario(**choix: str) -> None:
    corps = json.dumps(choix).encode()
    statut, _, _ = appeler(f"{DOUBLURE}/_essai/scenario", corps, "application/json")
    verifier(statut == 200, f"scénario {choix} : 200")


def verifier(condition: bool, libelle: str) -> None:
    if not condition:
        print(f"ÉCHEC — {libelle}", file=sys.stderr)
        sys.exit(1)
    print(f"ok — {libelle}")


def principal() -> None:
    statut, _, corps = appeler(f"{BASE}/sante")
    verifier(statut == 200 and json.loads(corps)["api"] == "ok", "/sante : API jointe")

    statut, entetes, corps = appeler(f"{BASE}/")
    verifier(statut == 200, "/ : 200")
    verifier("profil actif : mistral-large-2512" in corps, "/ : le profil actif")
    verifier("Vous parlez à une IA." in corps, "/ : la mention")
    verifier("mistral-large-4" not in corps, "/ : Large 4 n'est pas proposé")
    verifier(
        "default-src 'none'" in entetes.get("Content-Security-Policy", "")
        and entetes.get("Cache-Control") == "no-store",
        "/ : les en-têtes de sécurité",
    )
    verifier("cle-jetable-essai" not in corps, "/ : la clé n'y paraît pas")

    statut, _, corps = poser("Un manga factice ?")
    verifier(statut == 200, "/question : 200")
    verifier('id="serie-101"' in corps and "factice A" in corps, "/question : la carte")
    verifier('href="#serie-101"' in corps, "/question : l'ancre vers la carte")
    verifier("Passage factice n° 12" not in corps, "/question : le passage non cité")

    scenario(reponse="fournisseur_refus_429")
    statut, entetes, corps = poser("Une autre question factice ?")
    verifier(statut == 503 and entetes.get("Retry-After") == "7", "503 : Retry-After")
    verifier('role="alert"' in corps and "dans 7 secondes" in corps, "503 : l'encart")
    verifier("Une autre question factice ?" in corps, "503 : la question gardée")
    scenario(reponse="nominale")

    statut, _, corps = appeler(f"{BASE}/a-propos")
    verifier(statut == 200 and "Union européenne" in corps, "/a-propos : l'hébergement")

    statut, _, corps = appeler(f"{BASE}/metrics")
    verifier(
        statut == 200 and 'application_requetes_total{route="/question"' in corps,
        "/metrics : les requêtes comptées",
    )

    statut, _, corps = appeler(f"{DOUBLURE}/_essai/ecarts")
    verifier(statut == 200 and json.loads(corps) == [], "la doublure : aucun écart")
    print("essai d'intégration : l'application conteneurisée répond, de bout en bout")


if __name__ == "__main__":
    principal()
