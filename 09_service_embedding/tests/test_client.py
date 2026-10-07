"""Le client : préfixes, paramètres envoyés, contrôle des réponses.

Le service est remplacé ici par une doublure HTTP minimale qui enregistre ce
qu'elle reçoit : on vérifie ce que le CLIENT envoie et refuse. Le service réel
est exercé par `service_embedding.controles` (rapport du bloc A) et par le test
d'intégration de l'encodage.
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from service_embedding.client import ClientService, ErreurService, cosinus, norme
from service_embedding.configuration import charger


class Doublure(BaseHTTPRequestHandler):
    recues: list[dict] = []
    dimension = 768
    statut = 200

    def log_message(self, *args):  # silence
        pass

    def do_POST(self):  # noqa: N802 — nom imposé par http.server
        corps = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Doublure.recues.append({"chemin": self.path, "corps": corps})
        if Doublure.statut != 200:
            reponse = {"error": "Input validation error", "error_type": "Validation"}
        elif self.path == "/embed":
            reponse = [
                [1.0] + [0.0] * (Doublure.dimension - 1) for _ in corps["inputs"]
            ]
        else:
            reponse = [[{"id": 0}] * len(t.split()) for t in corps["inputs"]]
        donnees = json.dumps(reponse).encode()
        self.send_response(Doublure.statut)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)


@pytest.fixture
def client_gemma():
    serveur = HTTPServer(("127.0.0.1", 0), Doublure)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    Doublure.recues, Doublure.dimension, Doublure.statut = [], 768, 200
    instance = replace(charger("embeddinggemma"), port=serveur.server_address[1])
    try:
        yield ClientService(instance)
    finally:
        serveur.shutdown()


def test_fragment_recoit_le_prefixe_document(client_gemma):
    client_gemma.encoder(["Un texte."], role="document")
    assert Doublure.recues[-1]["corps"]["inputs"] == ["title: none | text: Un texte."]


def test_question_recoit_le_prefixe_requete(client_gemma):
    client_gemma.encoder(["Un manga de pirates"], role="requete")
    assert Doublure.recues[-1]["corps"]["inputs"] == [
        "task: search result | query: Un manga de pirates"
    ]


def test_normalisation_demandee_et_troncature_refusee(client_gemma):
    client_gemma.encoder(["a", "b"], role="document")
    corps = Doublure.recues[-1]["corps"]
    assert corps["normalize"] is True
    assert corps["truncate"] is False


def test_tokenisation_prefixee_avec_jetons_speciaux(client_gemma):
    assert client_gemma.compter_jetons(["un deux"], role="document") == [6]
    corps = Doublure.recues[-1]["corps"]
    assert corps["add_special_tokens"] is True
    assert corps["inputs"] == ["title: none | text: un deux"]


def test_dimension_inattendue_refusee(client_gemma):
    Doublure.dimension = 1024
    with pytest.raises(ErreurService, match="dimension 1024"):
        client_gemma.encoder(["a"], role="document")


def test_refus_du_service_remonte_avec_son_message(client_gemma):
    Doublure.statut = 413
    with pytest.raises(ErreurService, match="HTTP 413"):
        client_gemma.encoder(["trop long"], role="document")


def test_lot_au_dela_du_plafond_jamais_envoye(client_gemma):
    with pytest.raises(ValueError, match="plafond"):
        client_gemma.encoder(
            ["x"] * (client_gemma.instance.plafond_lot + 1), "document"
        )
    assert Doublure.recues == []


def test_role_inconnu_refuse(client_gemma):
    with pytest.raises(ValueError, match="rôle"):
        client_gemma.encoder(["x"], role="passage")  # type: ignore[arg-type]


def test_norme_et_cosinus():
    assert norme([3.0, 4.0]) == 5.0
    assert cosinus([1.0, 0.0], [2.0, 0.0]) == pytest.approx(1.0)
    assert cosinus([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
