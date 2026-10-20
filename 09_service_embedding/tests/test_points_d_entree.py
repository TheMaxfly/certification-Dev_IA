"""Les points d'entrée (`main`) des quatre commandes, sur base jetable et service
simulé.

Ce qui se lit de l'extérieur : le code de sortie, le JSON écrit, les lignes en
base, et les refus — DATABASE_URL absente, corpus non nommé. Rien n'interroge
Docker ni la carte graphique : l'image du conteneur, la surveillance et la
mémoire de l'instance sont remplacées par des valeurs fixes.
"""

from __future__ import annotations

import json
import threading
import time
import types
from dataclasses import replace
from http.server import HTTPServer

import psycopg
import pytest
from test_corpus_cohabitation import corpus_v2
from test_encodage import (
    DIGEST,
    Doublure,
    doublure,  # noqa: F401 — la fixture du service simulé
    vecteur_de,
)
from test_equivalence import (
    encodee,  # noqa: F401 — la fixture d'un encodage simulé
    tourner,
)

from service_embedding import (
    controles,
    encodage,
    equivalence,
    mesures,
    verification,
)
from service_embedding.configuration import charger
from service_embedding.encodage import EncodageRefuse

IMAGE = f"ghcr.io/huggingface/text-embeddings-inference:86-1.9.4@{DIGEST}"
MEMOIRE = {"ram": 1, "ram_pic": 2, "ram_anonyme": 3, "ram_cache_fichiers": 4}


# --------------------------------------------------------------------------- #
#  encodage.main
# --------------------------------------------------------------------------- #


class SurveillanceFactice:
    """La surveillance, sans cgroup ni nvidia-smi : un swap continu ou non."""

    def __init__(self, conteneur: str, swap: bool) -> None:
        self.conteneur = conteneur
        self.swap_continu = swap
        self.secondes_continues = 10

    def demarrer(self) -> SurveillanceFactice:
        return self

    def arreter(self) -> dict:
        return {"swap_continu": self.swap_continu, "echantillons": 0}


@pytest.fixture
def encodeur(base, doublure, monkeypatch):  # noqa: F811
    """`encodage.main` branché sur la base jetable et le service simulé ; rend
    l'instance et les noms de conteneur interrogés."""

    def brancher(nom: str = "bge-m3", swap: bool = False):
        instance, _, _ = doublure(nom)
        conteneurs: list[str] = []

        def image(conteneur: str) -> str:
            conteneurs.append(conteneur)
            return IMAGE

        def surveillance(conteneur: str) -> SurveillanceFactice:
            conteneurs.append(conteneur)
            return SurveillanceFactice(conteneur, swap)

        monkeypatch.setenv("DATABASE_URL", base)
        monkeypatch.setattr(encodage, "charger", lambda _: instance)
        monkeypatch.setattr(encodage, "image_du_conteneur", image)
        monkeypatch.setattr(mesures, "Surveillance", surveillance)
        monkeypatch.setattr(mesures, "memoire_conteneur", lambda _: MEMOIRE)
        return instance, conteneurs

    return brancher


def vecteurs_en_base(dsn: str, table: str) -> list[int]:
    with psycopg.connect(dsn) as cx:
        return [c for (c,) in cx.execute(f"SELECT chunk_id FROM {table} ORDER BY 1")]  # nosec B608


def test_encodage_sans_database_url_refuse(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="DATABASE_URL absente"):
        encodage.main(["bge-m3", "--sortie", str(tmp_path / "bilan.json")])
    assert not (tmp_path / "bilan.json").exists()


def test_encodage_ecrit_les_vecteurs_et_le_bilan(base, encodeur, tmp_path, capsys):
    _, conteneurs = encodeur()
    sortie = tmp_path / "e2" / "bilan.json"

    code = encodage.main(["bge-m3", "--sortie", str(sortie), "--lot", "1"])

    assert code == 0
    bilan = json.loads(sortie.read_text(encoding="utf-8"))
    assert (bilan["corpus_id"], bilan["vecteurs_ecrits"], bilan["termine"]) == (
        "v1",
        3,
        True,
    )
    assert bilan["taille_lot"] == 1 and [len(lot) for lot in Doublure.recues] == [
        1,
        1,
        1,
    ], "le lot de la ligne de commande est celui de l'encodage"
    assert bilan["service"]["digest"] == DIGEST
    assert bilan["service"]["modele"] == "BAAI/bge-m3"
    assert bilan["surveillance"] == {"swap_continu": False, "echantillons": 0}
    assert bilan["memoire_fin"] == MEMOIRE
    assert conteneurs == ["tei-bge-m3", "tei-bge-m3"], "image puis surveillance"
    assert json.loads(capsys.readouterr().out) == bilan, "le bilan est aussi affiché"
    assert vecteurs_en_base(base, "bench.vecteurs_bge_m3") == [101, 102, 103]


def test_encodage_arrete_par_le_swap_sort_en_code_3(base, encodeur, tmp_path):
    """Swap continu dès le premier lot : arrêt propre, le registre est inscrit,
    aucun vecteur n'est écrit, et le bilan dit pourquoi."""
    encodeur(swap=True)
    sortie = tmp_path / "bilan.json"

    assert encodage.main(["bge-m3", "--sortie", str(sortie)]) == 3

    bilan = json.loads(sortie.read_text(encoding="utf-8"))
    assert "swap écrit 10 s d'affilée après 0 vecteurs" in bilan["arret"]
    assert bilan["surveillance"]["swap_continu"] is True
    assert vecteurs_en_base(base, "bench.vecteurs_bge_m3") == []
    with psycopg.connect(base) as cx:
        assert cx.execute("SELECT count(*) FROM bench.encodages").fetchone() == (1,)


def test_encodage_nomme_le_corpus_ou_refuse(base, encodeur, tmp_path):
    """Deux corpus : sans --corpus, refus avant toute écriture, et pas de bilan ;
    avec, seul le corpus nommé est encodé."""
    encodeur("embeddinggemma")
    corpus_v2(base)
    sortie = tmp_path / "bilan.json"

    with pytest.raises(EncodageRefuse, match="nommer le corpus"):
        encodage.main(["embeddinggemma", "--sortie", str(sortie)])
    assert not sortie.exists()

    code = encodage.main(["embeddinggemma", "--corpus", "v2", "--sortie", str(sortie)])
    assert code == 0
    assert json.loads(sortie.read_text(encoding="utf-8"))["corpus_id"] == "v2"
    assert vecteurs_en_base(base, "bench.vecteurs_embeddinggemma") == [201]


# --------------------------------------------------------------------------- #
#  verification.main
# --------------------------------------------------------------------------- #


def test_verification_sans_database_url_refuse(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="DATABASE_URL absente"):
        verification.main(["--sortie", str(tmp_path / "v.json")])


def test_verification_conforme_sort_en_zero(
    base,
    doublure,  # noqa: F811
    monkeypatch,
    tmp_path,
    capsys,
):
    """Les deux modèles encodés sur le v1, le jeu inscrit : tout est conforme
    aux valeurs posées (ramenées à la taille de la base jetable)."""
    for nom in ("bge-m3", "embeddinggemma"):
        instance, client, service = doublure(nom)
        encodage.encoder_corpus(instance, client, service, base)
    with psycopg.connect(base) as cx:
        cx.execute(
            "INSERT INTO bench.eval_jeux (version, empreinte, gele_le, declaration)"
            " VALUES (%s, %s, now(), 'essai')",
            (verification.JEU, verification.EMPREINTE_JEU),
        )
    monkeypatch.setattr(verification, "FRAGMENTS", 3)
    monkeypatch.setattr(verification, "DOCUMENTS", 2)
    monkeypatch.setattr(verification, "ENCODAGES", 2)
    monkeypatch.setenv("DATABASE_URL", base)
    sortie = tmp_path / "verif" / "verification.json"

    assert verification.main(["--sortie", str(sortie)]) == 0

    resultats = json.loads(sortie.read_text(encoding="utf-8"))
    assert len(resultats) == 15 and all(r["conforme"] for r in resultats)
    lignes = capsys.readouterr().out.splitlines()
    assert len(lignes) == 15 and all(ligne.startswith("OK ") for ligne in lignes)


def test_verification_non_conforme_sort_en_un(base, monkeypatch, tmp_path, capsys):
    """Base sans encodage, valeurs posées réelles : les écarts sont écrits et
    affichés, et le code de sortie le dit."""
    with psycopg.connect(base) as cx:
        cx.execute(
            "INSERT INTO bench.eval_jeux (version, empreinte, gele_le, declaration)"
            " VALUES (%s, %s, now(), 'essai')",
            (verification.JEU, verification.EMPREINTE_JEU),
        )
    monkeypatch.setenv("DATABASE_URL", base)
    sortie = tmp_path / "verification.json"

    assert verification.main(["--sortie", str(sortie)]) == 1

    constats = {
        r["controle"]: r for r in json.loads(sortie.read_text(encoding="utf-8"))
    }
    lignes = constats["bench.vecteurs_bge_m3 : lignes"]
    assert (lignes["attendu"], lignes["constate"], lignes["conforme"]) == (
        verification.FRAGMENTS,
        0,
        False,
    )
    assert constats["jeu v2 : empreinte des fichiers"]["conforme"]
    sortie_ecran = capsys.readouterr().out
    assert "ÉCART bench.vecteurs_bge_m3 : lignes : attendu 66290, constaté 0" in (
        sortie_ecran
    )


# --------------------------------------------------------------------------- #
#  equivalence.main
# --------------------------------------------------------------------------- #


def test_equivalence_exporter_sans_database_url_refuse(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="DATABASE_URL absente"):
        equivalence.main(["exporter", "--encodage", "1"])


def test_equivalence_exporter_puis_comparer(
    encodee,  # noqa: F811
    monkeypatch,
    tmp_path,
    capsys,
):
    """Les deux temps par la ligne de commande : l'export lit la base et écrit
    l'échantillon hors dépôt ; la comparaison ne lit que ce fichier et le
    service. Un vecteur inscrit qui s'écarte fait sortir en erreur."""
    base, client, eid, config = encodee
    config = config | {"export": {"dossier": str(tmp_path / "equivalence")}}
    monkeypatch.setattr(equivalence, "charger_config", lambda: config)
    monkeypatch.setattr(equivalence, "client_de", lambda _: client)
    monkeypatch.setenv("DATABASE_URL", base)

    assert equivalence.main(["exporter", "--encodage", str(eid)]) == 0
    chemin = tmp_path / "equivalence" / f"encodage_{eid}.json"
    assert capsys.readouterr().out.strip() == str(chemin)
    export = json.loads(chemin.read_text(encoding="utf-8"))
    assert export["encodage"]["encodage_id"] == eid
    assert export["config_empreinte"] == config["empreinte"]
    assert len(export["echantillon"]) == 6

    monkeypatch.delenv("DATABASE_URL")  # comparer ne lit jamais la base
    assert equivalence.main(["comparer", "--encodage", str(eid)]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "equivalent"

    ech = export["echantillon"]
    ech[0]["vecteur"] = tourner(ech[0]["vecteur"], ech[1]["vecteur"], 0.9)
    chemin.write_text(json.dumps(export), encoding="utf-8")
    assert equivalence.main(["comparer", "--encodage", str(eid)]) == 1
    rendu = json.loads(capsys.readouterr().out)
    assert rendu["verdict"] == "echec"
    assert [f["chunk_id"] for f in rendu["sous_equivalent"]] == [ech[0]["chunk_id"]]


# --------------------------------------------------------------------------- #
#  controles.main
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "etat, code, ecarts",
    [
        ({"ram_disponible": 8 * 10**9, "vram_libre": 6 * 10**9}, 0, 0),
        ({"ram_disponible": 8 * 10**9, "vram_libre": 10**9}, 1, 1),
    ],
)
def test_controles_etat_sort_en_erreur_sous_un_seuil(
    monkeypatch, capsys, etat, code, ecarts
):
    monkeypatch.setattr(mesures, "etat_hote", lambda: dict(etat))
    assert controles.main(["etat"]) == code
    rendu = json.loads(capsys.readouterr().out)
    assert rendu["ram_disponible"] == etat["ram_disponible"]
    assert len(rendu["seuils_non_tenus"]) == ecarts


class DoublureControles(Doublure):
    """La doublure du service, complétée de ce que lisent les contrôles : `/info`
    avec la longueur maximale, `/metrics` au format Prometheus, `/tokenize` (un
    jeton par mot), et le refus d'un texte au-delà de la longueur maximale."""

    requetes = 0

    def do_GET(self):  # noqa: N802
        i = Doublure.instance
        if self.path != "/metrics":
            self._repondre(
                200,
                {
                    "model_id": i.model_id,
                    "model_sha": i.revision,
                    "model_dtype": i.dtype,
                    "version": "1.9.4",
                    "max_input_length": i.longueur_maximale,
                },
            )
            return
        texte = (
            "# TYPE te_request_count counter\n"
            f'te_request_count{{method="batch"}} {DoublureControles.requetes}\n'
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(texte)))
        self.end_headers()
        self.wfile.write(texte)

    def do_POST(self):  # noqa: N802
        corps = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        textes = corps["inputs"]
        if self.path == "/tokenize":
            self._repondre(200, [t.split() for t in textes])
            return
        DoublureControles.requetes += 1
        i = Doublure.instance
        if any(len(t.split()) > i.longueur_maximale for t in textes):
            self._repondre(413, {"error": "entrée trop longue"})
            return
        self._repondre(200, [vecteur_de(t, i.dimension) for t in textes])


@pytest.fixture
def service_controles(monkeypatch):
    """Une instance bge-m3 simulée, métriques sur le même port ; ni Docker, ni
    attente de cinq secondes."""
    serveur = HTTPServer(("127.0.0.1", 0), DoublureControles)
    threading.Thread(
        target=serveur.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    port = serveur.server_address[1]
    instance = replace(charger("bge-m3"), port=port, port_metriques=port)
    Doublure.instance = instance
    DoublureControles.requetes = 0
    monkeypatch.setattr(controles, "charger", lambda _: instance)
    monkeypatch.setattr(mesures, "memoire_conteneur", lambda _: MEMOIRE)
    horloge = types.SimpleNamespace(
        strftime=time.strftime,
        gmtime=time.gmtime,
        monotonic=time.monotonic,
        sleep=lambda _: None,
    )
    monkeypatch.setattr(controles, "time", horloge)
    yield instance
    serveur.shutdown()


def test_controles_instance_ecrit_le_bilan_et_balaie_le_corpus(
    base, service_controles, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("DATABASE_URL", base)
    sortie = tmp_path / "a" / "controles.json"

    assert controles.main(["instance", "bge-m3", "--sortie", str(sortie)]) == 0

    r = json.loads(sortie.read_text(encoding="utf-8"))
    assert r["instance"] == "bge-m3" and r["sante"] is True
    assert all(r["conformite"].values()), r["conformite"]
    assert r["vecteurs"]["dimensions"] == [1024] and r["vecteurs"]["normes_conformes"]
    assert r["determinisme"]["identiques"]
    assert r["determinisme"]["ecart_absolu_max"] == 0.0
    assert r["au_dela_longueur_maximale"]["refuse"]
    assert "413" in r["au_dela_longueur_maximale"]["message"]
    m = r["metriques"]
    assert m["compteurs"] == ["te_request_count"]
    assert (m["requetes_avant"], m["requetes_apres"]) == (0.0, 5.0), (
        "cinq requêtes d'encodage entre les deux relevés"
    )
    t = r["troncature"]
    assert (t["fragments"], t["max"], t["au_dela_512"]) == (3, 9, 0)
    assert t["plus_longs"] == [[103, 9], [102, 8], [101, 6]]
    assert t["plus_longs_encodes_sans_troncature"]
    assert r["memoire_repos_avant"] == r["memoire_repos_apres"] == MEMOIRE
    affiche = json.loads(capsys.readouterr().out)
    assert set(affiche) == {"conformite", "vecteurs", "determinisme"}


def test_controles_instance_sans_corpus_se_passe_de_la_base(
    service_controles, monkeypatch, tmp_path
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    sortie = tmp_path / "controles.json"
    assert (
        controles.main(["instance", "bge-m3", "--sortie", str(sortie), "--sans-corpus"])
        == 0
    )
    assert "troncature" not in json.loads(sortie.read_text(encoding="utf-8"))


def test_le_balayage_exige_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="DATABASE_URL absente"):
        controles.fragments()


def test_le_balayage_ne_devine_pas_le_corpus(base, monkeypatch):
    """Deux corpus et aucun en service : refus ; nommé, seul ce corpus est lu."""
    monkeypatch.setenv("DATABASE_URL", base)
    corpus_v2(base)
    with pytest.raises(SystemExit, match="plusieurs corpus"):
        controles.fragments()
    assert controles.fragments("v2") == [(201, "Une critique, en un seul morceau.")]
