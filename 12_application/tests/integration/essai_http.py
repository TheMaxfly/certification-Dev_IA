"""Essai d'intégration du Compose : l'application conteneurisée répond, de bout en bout.

Joué par le service `essai` de `compose.integration.yml`, une fois l'application saine.
Il parle à l'application par le réseau du Compose, comme un navigateur :

1. `/sante` : 200, l'API jointe ;
2. `/` : la page, le profil actif, la mention, les en-têtes de sécurité ;
3. `POST /question` : la réponse et une carte de série, depuis la doublure — avec
   le cookie de session et le jeton CSRF de la page, comme un navigateur ; sans
   jeton, 403 ;
4. le même, la doublure en refus de débit : l'encart, `Retry-After`, la question ;
5. `/a-propos`, `/metrics` ;
6. le catalogue (E4, lot 2a) : `/catalogue?q=…`, des résultats ; `/series/101`, la
   fiche ; la doublure en panne : l'encart nommé, 503 ;
7. la doublure n'a vu aucun écart à ses contrats.

Bibliothèque standard seule ; code 1 au premier écart.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message

BASE = os.environ["APPLICATION_URL"]
DOUBLURE = os.environ["DOUBLURE_URL"]
#: Le cookie de session, gardé d'une requête à l'autre (`nom=valeur`), comme un
#: navigateur.
COOKIE: dict[str, str] = {}


def appeler(
    url: str, donnees: bytes | None = None, type_: str | None = None
) -> tuple[int, Message, str]:
    entetes = {"Content-Type": type_} if type_ else {}
    if COOKIE and url.startswith(BASE):
        entetes["Cookie"] = "; ".join(f"{n}={v}" for n, v in COOKIE.items())
    requete = urllib.request.Request(url, data=donnees, headers=entetes)
    try:
        with urllib.request.urlopen(requete, timeout=10) as r:  # noqa: S310
            reponse = r.status, r.headers, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        reponse = e.code, e.headers, e.read().decode("utf-8")
    for entete in reponse[1].get_all("Set-Cookie") or []:
        nom, _, valeur = entete.split(";", 1)[0].partition("=")
        COOKIE[nom] = valeur
    return reponse


def jeton() -> str:
    """Le jeton CSRF de la page du conseiller (et le cookie qui le porte)."""
    _, _, corps = appeler(f"{BASE}/")
    trouve = re.search(r'name="jeton" value="([^"]+)"', corps)
    verifier(trouve is not None, "/ : le jeton du formulaire")
    assert trouve is not None
    return trouve.group(1)


def poser(question: str, avec_jeton: bool = True) -> tuple[int, Message, str]:
    champs = {
        "question": question,
        "profil": "mistral-large-2512",
        "temperature": "0.0",
        "reformuler": "1",
        "historique": "",
    }
    if avec_jeton:
        champs["jeton"] = jeton()
    corps = urllib.parse.urlencode(champs).encode()
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

    statut, _, corps = poser("Une question factice sans jeton ?", avec_jeton=False)
    verifier(
        statut == 403 and "Ce formulaire a expiré" in corps,
        "/question : sans jeton, 403",
    )

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

    statut, _, corps = appeler(f"{BASE}/catalogue?q=factice")
    verifier(
        statut == 200 and 'href="/series/101"' in corps,
        "/catalogue : des résultats, depuis le catalogue",
    )
    statut, _, corps = appeler(f"{BASE}/series/101")
    verifier(
        statut == 200 and "Les libraires du réseau" in corps,
        "/series/101 : la fiche",
    )
    scenario(catalogue="indisponible")
    statut, _, corps = appeler(f"{BASE}/catalogue?q=factice")
    verifier(
        statut == 503 and "Le catalogue ne répond pas" in corps,
        "catalogue en panne : l'encart, 503",
    )
    scenario(catalogue="nominal")
    statut, _, corps = appeler(f"{BASE}/sante")
    verifier(
        statut == 200 and json.loads(corps)["catalogue"] == "ok",
        "/sante : le catalogue joint",
    )

    statut, _, corps = appeler(f"{DOUBLURE}/_essai/ecarts")
    verifier(statut == 200 and json.loads(corps) == [], "la doublure : aucun écart")
    print("essai d'intégration : l'application conteneurisée répond, de bout en bout")


if __name__ == "__main__":
    principal()
