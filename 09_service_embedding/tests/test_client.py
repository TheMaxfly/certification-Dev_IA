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

from service_embedding.client import (
    ClientService,
    ErreurService,
    ServiceNonConforme,
    confronter,
    cosinus,
    norme,
)
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
    # Interrogation courte : `shutdown()` attend la fin d'un cycle (0,5 s par défaut).
    fil = threading.Thread(
        target=serveur.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
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


# --------------------------------------------------------------------------- #
#  La confrontation au registre — une règle pure, appelée par l'équivalence (09,
#  rôle « document ») et par la mesure (10, rôle « requete »)
# --------------------------------------------------------------------------- #


class ClientFige:
    """Un client dont `/info` est figé : aucun service n'est appelé."""

    def __init__(self, instance, info):
        self.instance, self._info = instance, info

    def info(self):
        return self._info


@pytest.fixture
def registre():
    """L'instance, ce qu'elle sert, et la ligne d'encodage qui lui correspond."""
    i = charger("embeddinggemma")
    assert i.prefixe_document != i.prefixe_requete, "les deux rôles se distinguent"
    info = {
        "model_id": i.model_id,
        "model_sha": i.revision,
        "model_dtype": i.dtype,
        "version": "1.9.4",
    }
    encodage = {
        "modele": i.model_id,
        "revision": i.revision,
        "precision_calcul": i.dtype,
        "outil_version": "1.9.4",
        "prefixe_document": i.prefixe_document,
        "prefixe_requete": i.prefixe_requete,
    }
    return i, info, encodage


@pytest.mark.parametrize("role", ["document", "requete"])
def test_un_service_conforme_est_admis(registre, role):
    instance, info, encodage = registre
    assert confronter(ClientFige(instance, info), encodage, role) == info
    assert confronter(ClientFige(instance, info), None, role) == info


@pytest.mark.parametrize(
    "champ, valeur, nomme",
    [
        ("model_id", "autre/modele", "modele"),
        ("model_sha", "0" * 40, "revision"),
        ("model_dtype", "float16", "precision_calcul"),
        ("version", "1.9.5", "outil_version"),
    ],
)
def test_un_ecart_avec_l_encodage_arrete(registre, champ, valeur, nomme):
    instance, info, encodage = registre
    with pytest.raises(ServiceNonConforme, match=nomme):
        confronter(ClientFige(instance, info | {champ: valeur}), encodage, "document")


@pytest.mark.parametrize(
    "role, autre", [("document", "requete"), ("requete", "document")]
)
def test_le_prefixe_du_role_est_confronte_a_l_encodage(registre, role, autre):
    instance, info, encodage = registre
    encodage = encodage | {f"prefixe_{role}": "un autre préfixe : "}
    with pytest.raises(ServiceNonConforme, match=f"prefixe_{role}"):
        confronter(ClientFige(instance, info), encodage, role)
    # Le préfixe de l'autre rôle n'entre pas dans la confrontation.
    confronter(ClientFige(instance, info), encodage, autre)


def test_sans_encodage_la_configuration_fait_foi_sans_version(registre):
    instance, info, _ = registre
    confronter(ClientFige(instance, info | {"version": "9.9.9"}), None, "requete")
    with pytest.raises(ServiceNonConforme, match="revision"):
        confronter(
            ClientFige(instance, info | {"model_sha": "0" * 40}), None, "requete"
        )
