"""Le test d'équivalence du service (E3, étape 1, bloc F).

Les seuils et l'échantillon, sur base jetable et service simulé ; puis le test
contre le service réel, sur les échantillons exportés hors dépôt — sauté quand il
n'y a ni échantillon ni instance (GitHub, `make verify` sans service).
"""

from __future__ import annotations

import copy
import json
import math
from datetime import UTC, datetime

import psycopg
import pytest
from test_encodage import doublure  # noqa: F401 — la fixture du service simulé

from service_embedding import equivalence
from service_embedding.encodage import encoder_corpus

CONFIG = equivalence.charger_config()


def test_les_seuils_de_la_configuration():
    assert CONFIG["seuils"] == {"equivalent": 0.999, "echec": 0.99}
    s = CONFIG["echantillon"]
    assert (s["tranches"], s["par_tranche"]) == (10, 20) and s["graine"] == 20261010


@pytest.mark.parametrize(
    "cos_min, attendu",
    [
        (1.0, "equivalent"),
        (0.999, "equivalent"),
        (0.99874, "a_surveiller"),
        (0.99, "a_surveiller"),
        (0.9899, "echec"),
    ],
)
def test_verdict_selon_les_seuils(cos_min, attendu):
    assert equivalence.verdict(cos_min, CONFIG["seuils"]) == attendu


@pytest.fixture
def encodee(base, doublure):  # noqa: F811
    """Douze fragments de longueurs variées, l'un portant un pseudonyme ; encodés
    par le service simulé."""
    with psycopg.connect(base) as cx:
        cx.execute(
            "INSERT INTO manga.ms_reviews_all (review_url, review_author) VALUES"
            " ('https://x/fiche_serie_critique.php?id=1', 'Zorglub')"
        )
        for i in range(12):
            texte = (
                "Un fragment " + "assez long " * i + ("de Zorglub" if i == 5 else "")
            )
            cx.execute(
                "INSERT INTO bench.corpus_chunks (chunk_id, doc_key, chunk_index,"
                " chunk_text) VALUES (%s, 'ms:1', %s, %s)",
                (200 + i, 10 + i, texte),
            )
    instance, client, service = doublure("bge-m3")
    encoder_corpus(instance, client, service, base)
    with psycopg.connect(base) as cx:
        (eid,) = cx.execute("SELECT encodage_id FROM bench.encodages").fetchone()
    config = copy.deepcopy(CONFIG)
    config["echantillon"] |= {"tranches": 3, "par_tranche": 2, "graine": 7}
    return base, client, eid, config


def test_l_echantillon_est_fixe_stratifie_et_sans_pseudonyme(encodee):
    base, _, eid, config = encodee
    with psycopg.connect(base, options="-c default_transaction_read_only=on") as cx:
        e, a = equivalence.echantillon(cx, eid, config)
        _, b = equivalence.echantillon(cx, eid, config)
    assert a == b, "le même échantillon à chaque fois"
    assert e["encodage_id"] == eid and e["termine"]
    assert [f["tranche"] for f in a] == [0, 0, 1, 1, 2, 2]
    assert 205 not in {f["chunk_id"] for f in a}, "le fragment au pseudonyme est écarté"
    longueurs = [f["longueur"] for f in a]
    assert max(longueurs[:2]) <= min(longueurs[4:]), "tranches par longueur croissante"


def test_un_service_identique_est_equivalent(encodee):
    base, client, eid, config = encodee
    with psycopg.connect(base) as cx:
        e, ech = equivalence.echantillon(cx, eid, config)
    r = equivalence.comparer(client, {"encodage": e, "echantillon": ech}, config)
    assert r["verdict"] == "equivalent" and r["n"] == 6 and r["cos_min"] > 0.999999


def tourner(v: list[float], u: list[float], cos: float) -> list[float]:
    """Un vecteur unitaire à l'angle voulu de `v` (dans le plan de v et u)."""
    produit = sum(a * b for a, b in zip(v, u, strict=True))
    w = [b - produit * a for a, b in zip(v, u, strict=True)]
    norme = math.sqrt(sum(x * x for x in w))
    sin = math.sqrt(1 - cos * cos)
    return [cos * a + sin * b / norme for a, b in zip(v, w, strict=True)]


@pytest.mark.parametrize("cos, attendu", [(0.995, "a_surveiller"), (0.9, "echec")])
def test_un_vecteur_qui_s_ecarte_est_rapporte_ou_echoue(encodee, cos, attendu):
    base, client, eid, config = encodee
    with psycopg.connect(base) as cx:
        e, ech = equivalence.echantillon(cx, eid, config)
    ech[0]["vecteur"] = tourner(ech[0]["vecteur"], ech[1]["vecteur"], cos)
    r = equivalence.comparer(client, {"encodage": e, "echantillon": ech}, config)
    assert r["cos_min"] == pytest.approx(cos, abs=1e-6)
    assert r["verdict"] == attendu
    assert [x["chunk_id"] for x in r["sous_equivalent"]] == [ech[0]["chunk_id"]]


def test_un_service_qui_ne_sert_pas_l_encodage_est_refuse(encodee):
    base, client, eid, config = encodee
    with psycopg.connect(base) as cx:
        e, ech = equivalence.echantillon(cx, eid, config)
    e = e | {"revision": "0" * 40}
    with pytest.raises(equivalence.ServiceNonConforme):
        equivalence.comparer(client, {"encodage": e, "echantillon": ech}, config)


# --------------------------------------------------------------------------- #
#  Le service réel, sur les échantillons exportés (make verify-poste)
# --------------------------------------------------------------------------- #


def test_equivalence_du_service_reel():
    exports = sorted(
        (equivalence.RACINE_MODULE / CONFIG["export"]["dossier"]).glob(
            "encodage_*.json"
        )
    )
    if not exports:
        pytest.skip(
            "aucun échantillon exporté (service_embedding.equivalence exporter)"
        )
    resultats = []
    for chemin in exports:
        export = json.loads(chemin.read_text(encoding="utf-8"))
        client = equivalence.client_de(export["encodage"])
        if not client.sante():
            pytest.skip(f"instance {client.instance.nom} non lancée")
        r = equivalence.comparer(client, export, CONFIG)
        r["teste_le"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        (chemin.parent / f"resultat_{chemin.stem}.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        resultats.append(r)
    echecs = [r for r in resultats if r["verdict"] == "echec"]
    assert not echecs, [(r["encodage_id"], r["cos_min"]) for r in echecs]
