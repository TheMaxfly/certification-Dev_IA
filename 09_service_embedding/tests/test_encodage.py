"""L'encodeur, sur base jetable : écriture, reprise, rejeu sans écriture, refus.

Le service est remplacé par une doublure HTTP déterministe (le vecteur dépend du
texte reçu, préfixe compris) qui sait tomber en panne au N-ième appel. Le
dernier test, lui, passe par l'instance RÉELLE quand elle tourne en local.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg
import pytest

from service_embedding.client import ClientService, ErreurService
from service_embedding.configuration import charger
from service_embedding.encodage import (
    EncodageRefuse,
    encoder_corpus,
    image_du_conteneur,
    service_de,
)

DIGEST = "sha256:" + "c" * 64


def vecteur_de(texte: str, dimension: int) -> list[float]:
    graine = hashlib.sha256(texte.encode()).digest()
    brut = [graine[i % 32] - 127.5 + i % 7 for i in range(dimension)]
    n = math.sqrt(sum(x * x for x in brut))
    return [x / n for x in brut]


class Doublure(BaseHTTPRequestHandler):
    instance = None
    revision = None
    recues: list[list[str]] = []
    panne_a = None  # numéro d'appel /embed qui échoue

    def log_message(self, *args):
        pass

    def _repondre(self, statut, corps):
        donnees = json.dumps(corps).encode()
        self.send_response(statut)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)

    def do_GET(self):  # noqa: N802
        i = Doublure.instance
        self._repondre(
            200,
            {
                "model_id": i.model_id,
                "model_sha": Doublure.revision or i.revision,
                "model_dtype": i.dtype,
                "version": "1.9.4",
            },
        )

    def do_POST(self):  # noqa: N802
        corps = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Doublure.recues.append(corps["inputs"])
        if Doublure.panne_a == len(Doublure.recues):
            self._repondre(500, {"error": "panne simulée"})
            return
        d = Doublure.instance.dimension
        self._repondre(200, [vecteur_de(t, d) for t in corps["inputs"]])


@pytest.fixture
def doublure():
    serveur = HTTPServer(("127.0.0.1", 0), Doublure)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    Doublure.recues, Doublure.panne_a, Doublure.revision = [], None, None

    def preparer(nom: str):
        instance = replace(charger(nom), port=serveur.server_address[1], taille_lot=2)
        Doublure.instance = instance
        client = ClientService(instance)
        image = f"ghcr.io/huggingface/text-embeddings-inference:86-1.9.4@{DIGEST}"
        return instance, client, service_de(client, image)

    yield preparer
    serveur.shutdown()


def etat(dsn: str, table: str) -> dict:
    """Ce qui a été écrit, et par quelle transaction (xmin)."""
    with psycopg.connect(dsn) as cx:
        return {
            "encodages": cx.execute(
                "SELECT encodage_id, xmin::text, nb_fragments, termine_le IS NOT NULL"
                " FROM bench.encodages ORDER BY 1"
            ).fetchall(),
            "vecteurs": cx.execute(
                f"SELECT chunk_id, xmin::text FROM {table} ORDER BY 1"  # nosec B608
            ).fetchall(),
            "fragments": cx.execute(
                "SELECT chunk_id, xmin::text FROM bench.corpus_chunks ORDER BY 1"
            ).fetchall(),
        }


def test_encodage_complet_puis_rejeu_sans_ecriture(base, doublure):
    instance, client, service = doublure("bge-m3")
    avant = etat(base, "bench.vecteurs_bge_m3")

    bilan = encoder_corpus(instance, client, service, base)
    assert bilan["vecteurs_ecrits"] == 3
    assert bilan["encodage_cree"] and bilan["termine"]
    apres = etat(base, "bench.vecteurs_bge_m3")
    assert [(e[2], e[3]) for e in apres["encodages"]] == [(3, True)]
    assert [c for c, _ in apres["vecteurs"]] == [101, 102, 103]
    assert apres["fragments"] == avant["fragments"], (
        "les fragments sont lus, pas écrits"
    )

    rejeu = encoder_corpus(instance, client, service, base)
    assert rejeu["vecteurs_ecrits"] == 0
    assert not rejeu["encodage_cree"] and not rejeu["encodage_mis_a_jour"]
    assert etat(base, "bench.vecteurs_bge_m3") == apres, "un rejeu complet n'écrit rien"


def test_vecteurs_ecrits_sont_ceux_du_service(base, doublure):
    instance, client, service = doublure("bge-m3")
    encoder_corpus(instance, client, service, base)
    with psycopg.connect(base) as cx:
        texte, norme_db = cx.execute(
            "SELECT embedding::text, public.vector_norm(embedding)"
            " FROM bench.vecteurs_bge_m3 WHERE chunk_id = 101"
        ).fetchone()
    attendu = vecteur_de("Une critique enthousiaste du premier tome.", 1024)
    lu = json.loads(texte)
    assert max(abs(a - b) for a, b in zip(lu, attendu, strict=True)) < 1e-6
    assert norme_db == pytest.approx(1.0, abs=1e-6)


def test_reprise_apres_interruption(base, doublure):
    instance, client, service = doublure("bge-m3")
    Doublure.panne_a = 2  # le premier lot (2 fragments) passe, le second tombe
    with pytest.raises(ErreurService, match="500"):
        encoder_corpus(instance, client, service, base, validation=1)
    partiel = etat(base, "bench.vecteurs_bge_m3")
    assert [c for c, _ in partiel["vecteurs"]] == [101, 102]
    assert [e[3] for e in partiel["encodages"]] == [False], "pas encore terminé"

    Doublure.panne_a = None
    bilan = encoder_corpus(instance, client, service, base)
    assert bilan["vecteurs_ecrits"] == 1 and bilan["termine"]
    assert not bilan["encodage_cree"], "la reprise retrouve sa ligne"
    final = etat(base, "bench.vecteurs_bge_m3")
    assert final["vecteurs"][:2] == partiel["vecteurs"], "rien n'est réécrit"
    assert [(e[0], e[2], e[3]) for e in final["encodages"]] == [
        (partiel["encodages"][0][0], 3, True)
    ]


def test_prefixe_document_applique_pour_gemma(base, doublure):
    instance, client, service = doublure("embeddinggemma")
    encoder_corpus(instance, client, service, base)
    envoyes = [t for lot in Doublure.recues for t in lot]
    assert envoyes and all(t.startswith("title: none | text: ") for t in envoyes)
    with psycopg.connect(base) as cx:
        prefixes = cx.execute(
            "SELECT prefixe_document, prefixe_requete, dimension FROM bench.encodages"
        ).fetchone()
    assert prefixes == ("title: none | text: ", "task: search result | query: ", 768)


def test_bge_m3_sans_prefixe(base, doublure):
    instance, client, service = doublure("bge-m3")
    encoder_corpus(instance, client, service, base)
    assert Doublure.recues[0][0] == "Une critique enthousiaste du premier tome."


def test_revision_servie_differente_refusee_sans_ecrire(base, doublure):
    Doublure.revision = "0" * 40
    instance, client, service = doublure("bge-m3")
    with pytest.raises(EncodageRefuse, match="sert"):
        encoder_corpus(instance, client, service, base)
    assert etat(base, "bench.vecteurs_bge_m3")["encodages"] == []


def test_image_non_epinglee_refusee(doublure):
    instance, client, _ = doublure("bge-m3")
    with pytest.raises(EncodageRefuse, match="digest"):
        service_de(client, "ghcr.io/huggingface/text-embeddings-inference:86-1.9.4")


def test_table_d_un_autre_encodage_refusee(base, doublure):
    instance, client, service = doublure("bge-m3")
    encoder_corpus(instance, client, service, base)
    autre = replace(service, digest="sha256:" + "d" * 64)
    with pytest.raises(EncodageRefuse, match="autre encodage"):
        encoder_corpus(instance, client, autre, base)


def test_corpus_change_apres_terminaison_refuse(base, doublure):
    instance, client, service = doublure("bge-m3")
    encoder_corpus(instance, client, service, base)
    with psycopg.connect(base) as cx:
        cx.execute(
            "INSERT INTO bench.corpus_chunks (chunk_id, doc_key, chunk_index,"
            " chunk_text) VALUES (104, 'ms:1', 2, 'Un fragment arrivé après.')"
        )
    with pytest.raises(EncodageRefuse, match="corpus a changé"):
        encoder_corpus(instance, client, service, base)


def test_lot_reduit_a_la_reprise_est_note(base, doublure):
    instance, client, service = doublure("bge-m3")
    Doublure.panne_a = 2
    with pytest.raises(ErreurService):
        encoder_corpus(instance, client, service, base, validation=1)
    Doublure.panne_a = None
    bilan = encoder_corpus(instance, client, service, base, taille_lot=1)
    assert bilan["encodage_mis_a_jour"]
    with psycopg.connect(base) as cx:
        assert cx.execute("SELECT taille_lot FROM bench.encodages").fetchone() == (1,)


def test_service_reel_bge_m3(base):
    """Instance BGE-M3 réelle, si elle tourne : trois fragments, bout en bout."""
    instance = charger("bge-m3")
    client = ClientService(instance)
    if not client.sante():
        pytest.skip("instance bge-m3 non lancée (docker compose --profile bge-m3)")
    service = service_de(client, image_du_conteneur("tei-bge-m3"))
    bilan = encoder_corpus(instance, client, service, base)
    assert bilan["vecteurs_ecrits"] == 3 and bilan["termine"]
    direct = client.encoder(["Une critique enthousiaste du premier tome."], "document")
    with psycopg.connect(base) as cx:
        texte, n, dim = cx.execute(
            "SELECT embedding::text, public.vector_norm(embedding),"
            " public.vector_dims(embedding)"
            " FROM bench.vecteurs_bge_m3 WHERE chunk_id = 101"
        ).fetchone()
    assert dim == 1024 and abs(n - 1) <= 1e-3
    ecart = max(abs(a - b) for a, b in zip(json.loads(texte), direct[0], strict=True))
    # Le fragment a été encodé dans un lot de 3, le témoin seul : cf. contrôles A.
    assert ecart < 1e-3
