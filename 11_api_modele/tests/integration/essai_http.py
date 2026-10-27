"""Essai d'intégration du Compose : l'API conteneurisée répond, de bout en bout.

Joué par le service `essai` de `compose.integration.yml`, une fois l'API saine (donc
démarrée : base migrée, rôle `modele_api`, jeu d'essai promu, service cohérent,
classement chargé). Il interroge l'API par le réseau du Compose, comme le ferait
l'application :

1. `/v1/sante` : 200, tous les composants `ok` ;
2. `/v1/recherche` avec la clé : 200, le classement attendu du jeu d'essai, le
   contexte de l'encodage en service, l'identifiant et `Cache-Control: no-store` ;
3. `/v1/info` avec la clé : le modèle et l'encodage servis ; le modèle de langage, le
   profil actif (celui de l'essai) et la disponibilité de chaque profil ;
4. `/v1/recherche` sans clé : 401 `cle_invalide` ;
5. `/v1/reponse` vers la doublure du fournisseur (aucun fournisseur réel) : une
   réponse qui cite la série du jeu d'essai, son contexte, un appel ; puis, avec un
   historique, la reformulation et deux appels.

Bibliothèque standard seule ; code 1 au premier écart.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from email.message import Message

BASE = os.environ.get("API_MODELE_URL", "http://api:8010")
CLE = os.environ["API_MODELE_CLE"]


def appeler(
    methode: str, chemin: str, corps: object = None, cle: str | None = None
) -> tuple[int, Message, dict]:
    """Statut, en-têtes (lus sans égard à la casse) et corps JSON."""
    donnees = None if corps is None else json.dumps(corps).encode()
    requete = urllib.request.Request(BASE + chemin, data=donnees, method=methode)
    requete.add_header("Content-Type", "application/json")
    if cle is not None:
        requete.add_header("X-API-Key", cle)
    try:
        with urllib.request.urlopen(requete, timeout=10) as r:  # noqa: S310
            return r.status, r.headers, json.loads(r.read())
    except urllib.error.HTTPError as erreur:
        return erreur.code, erreur.headers, json.loads(erreur.read())


def verifier(condition: bool, message: str) -> None:
    if not condition:
        print(f"ÉCHEC — {message}", file=sys.stderr)
        sys.exit(1)
    print(f"ok — {message}")


def principal() -> None:
    statut, _, sante = appeler("GET", "/v1/sante")
    verifier(
        statut == 200 and set(sante.values()) == {"ok"},
        f"/v1/sante : {statut}, {sante}",
    )

    statut, entetes, resultat = appeler(
        "POST", "/v1/recherche", {"question": "une aventure de pirates"}, CLE
    )
    verifier(statut == 200, f"/v1/recherche avec la clé : {statut}")
    entites = [s["entite"] for s in resultat["series"]]
    verifier(
        entites == ["serie:1", "serie:2"], f"classement du jeu d'essai : {entites}"
    )
    premier = resultat["series"][0]["passages"][0]
    verifier(
        premier["fragment_id"] == 702, f"meilleur passage : {premier['fragment_id']}"
    )
    contexte = resultat["contexte"]
    verifier(
        contexte["corpus_id"] == "v7"
        and contexte["identifiant_requete"] == entetes.get("X-Identifiant-Requete"),
        "contexte : corpus en service et identifiant de requête",
    )
    verifier(entetes.get("Cache-Control") == "no-store", "Cache-Control: no-store")

    statut, _, info = appeler("GET", "/v1/info", cle=CLE)
    verifier(
        statut == 200 and info["encodage_id"] == contexte["encodage_id"],
        f"/v1/info : {statut}, encodage {info.get('encodage_id')}",
    )

    langage = info["modele_langage"]
    disponibles = {p["nom"]: p["disponible"] for p in langage["profils"]}
    verifier(
        langage["profil_actif"] == "essai-local"
        and disponibles.get("essai-local") is True
        and disponibles.get("mistral-large-2512") is True
        and disponibles.get("gpt-6-luna") is False,
        f"/v1/info : profil actif {langage['profil_actif']}, profils {disponibles}",
    )

    statut, _, erreur = appeler("POST", "/v1/recherche", {"question": "x"})
    verifier(
        statut == 401 and erreur["code"] == "cle_invalide",
        f"/v1/recherche sans clé : {statut}",
    )

    statut, entetes, reponse = appeler(
        "POST", "/v1/reponse", {"question": "une aventure de pirates"}, CLE
    )
    verifier(statut == 200, f"/v1/reponse : {statut}")
    citees = [(s["serie_id"], s["fragment_ids"]) for s in reponse["series_citees"]]
    verifier(
        reponse["refus"] is False and citees == [(1, [702])],
        f"/v1/reponse : séries citées {citees}",
    )
    contexte = reponse["contexte"]
    verifier(
        contexte["profil"] == "essai-local"
        and [a["role"] for a in contexte["appels"]] == ["reponse"]
        and contexte["cout_estime_usd"] > 0
        and contexte["version_api"] == "1.1.0",
        "/v1/reponse : contexte (profil, un appel, coût, version 1.1.0)",
    )
    verifier(entetes.get("Cache-Control") == "no-store", "Cache-Control: no-store")

    historique = [
        {"role": "libraire", "contenu": "Vous auriez un manga de pirates ?"},
        {"role": "assistant", "contenu": "Oui, une série d'aventure en mer."},
    ]
    statut, _, reponse = appeler(
        "POST",
        "/v1/reponse",
        {"question": "Et il est long ?", "historique": historique},
        CLE,
    )
    contexte = reponse.get("contexte", {})
    verifier(
        statut == 200
        and contexte.get("question_reformulee") == "une aventure de pirates"
        and [a["role"] for a in contexte.get("appels", [])]
        == ["reformulation", "reponse"],
        f"/v1/reponse avec historique : {statut}, reformulation et deux appels",
    )
    print("essai d'intégration : l'API conteneurisée répond, recherche et réponse")


if __name__ == "__main__":
    principal()
