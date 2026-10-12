"""Le test d'équivalence du service — E3, étape 1, bloc F.

    uv run python -m service_embedding.equivalence exporter --encodage 2
    uv run python -m service_embedding.equivalence comparer --encodage 2

Un échantillon FIXE de fragments d'un encodage — dix tranches de longueur, vingt
fragments par tranche, graine de la configuration — est réencodé par le service,
puis comparé aux vecteurs inscrits pour cet encodage. Seuils lus dans
`config/equivalence.toml` : cosinus minimal ≥ 0,999, équivalent ; de 0,99 à
0,999, le test passe et rapporte les fragments concernés ; sous 0,99, échec.

DEUX TEMPS, pour que le test ne touche jamais une base réelle :
  - `exporter` lit la base EN LECTURE SEULE et écrit l'échantillon (textes,
    vecteurs, ligne du registre) hors dépôt, sous `mesures/equivalence/` ;
  - `comparer`, et le test `tests/test_equivalence.py`, ne lisent que ce fichier
    et le service. Avant de comparer, ce que sert l'instance (`/info`) est
    confronté à la ligne de l'encodage : un écart arrête.

Les fragments où figure un pseudonyme sont écartés de l'échantillon : aucun
pseudonyme en clair dans un fichier.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from service_embedding.client import ClientService, cosinus
from service_embedding.configuration import RACINE as RACINE_MODULE
from service_embedding.configuration import charger
from service_embedding.encodage import TABLES

CONFIG = RACINE_MODULE / "config/equivalence.toml"
INSTANCE_DU_MODELE = {
    "google/embeddinggemma-300m": "embeddinggemma",
    "BAAI/bge-m3": "bge-m3",
}


class ServiceNonConforme(RuntimeError):
    """L'instance ne sert pas l'encodage de l'échantillon."""


def charger_config(chemin: Path = CONFIG) -> dict:
    brut = chemin.read_bytes()
    c = tomllib.loads(brut.decode("utf-8"))
    c["empreinte"] = hashlib.sha256(brut).hexdigest()
    return c


def verdict(cos_min: float, seuils: dict) -> str:
    if cos_min >= seuils["equivalent"]:
        return "equivalent"
    if cos_min >= seuils["echec"]:
        return "a_surveiller"
    return "echec"


def chemin_export(config: dict, encodage_id: int) -> Path:
    return RACINE_MODULE / config["export"]["dossier"] / f"encodage_{encodage_id}.json"


# --------------------------------------------------------------------------- #
#  Exporter — lecture seule
# --------------------------------------------------------------------------- #


def echantillon(cx, encodage_id: int, config: dict) -> tuple[dict, list[dict]]:
    """La ligne de l'encodage, et l'échantillon : (chunk_id, texte, vecteur)."""
    import re

    r = cx.execute(
        "SELECT encodage_id, corpus_id, modele, revision, precision_calcul,"
        " outil_version, prefixe_document, image_digest,"
        " termine_le IS NOT NULL AS termine"
        " FROM bench.encodages WHERE encodage_id = %s",
        (encodage_id,),
    )
    ligne = r.fetchone()
    if ligne is None:
        raise RuntimeError(f"encodage {encodage_id} inconnu du registre")
    e = dict(zip([c.name for c in r.description], ligne, strict=True))
    table = TABLES[e["modele"]]
    pseudos = [
        re.compile(r"(?<!\w)" + re.escape(p) + r"(?!\w)", re.IGNORECASE)
        for (p,) in cx.execute(
            "SELECT DISTINCT review_author FROM manga.ms_reviews_all"
            " WHERE review_author IS NOT NULL"
        )
    ]
    candidats = [
        (c, t)
        for c, t in cx.execute(
            "SELECT k.chunk_id, k.chunk_text FROM bench.corpus_chunks k"
            " WHERE k.corpus_id = %s"
            f" AND EXISTS (SELECT 1 FROM {table} v"  # nosec B608 — table du registre
            "             WHERE v.encodage_id = %s AND v.chunk_id = k.chunk_id)"
            " ORDER BY length(k.chunk_text), k.chunk_id",
            (e["corpus_id"], encodage_id),
        )
        if not any(p.search(t) for p in pseudos)
    ]
    s = config["echantillon"]
    n = len(candidats)
    if n < s["tranches"] * s["par_tranche"]:
        raise RuntimeError(f"{n} fragments : trop peu pour l'échantillon")
    tirage = random.Random(s["graine"])  # nosec B311 — échantillon fixe, pas un secret
    choisis = []
    for i in range(s["tranches"]):
        tranche = candidats[i * n // s["tranches"] : (i + 1) * n // s["tranches"]]
        choisis += [
            (i, c, t) for c, t in sorted(tirage.sample(tranche, s["par_tranche"]))
        ]
    vecteurs = dict(
        cx.execute(
            f"SELECT chunk_id, embedding::text FROM {table}"  # nosec B608
            " WHERE encodage_id = %s AND chunk_id = ANY(%s)",
            (encodage_id, [c for _, c, _ in choisis]),
        ).fetchall()
    )
    return e, [
        {
            "tranche": i,
            "chunk_id": c,
            "longueur": len(t),
            "texte": t,
            "vecteur": json.loads(vecteurs[c]),
        }
        for i, c, t in choisis
    ]


def exporter(dsn: str, encodage_id: int, config: dict) -> Path:
    import psycopg

    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as cx:
        e, ech = echantillon(cx, encodage_id, config)
    chemin = chemin_export(config, encodage_id)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(
        json.dumps(
            {
                "encodage": e,
                "config_empreinte": config["empreinte"],
                "exporte_le": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "echantillon": ech,
            },
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )
    return chemin


# --------------------------------------------------------------------------- #
#  Comparer — le service, contre le fichier
# --------------------------------------------------------------------------- #


def confronter(client: ClientService, encodage: dict) -> None:
    info = client.info()
    servi = (info["model_id"], info["model_sha"], info["model_dtype"], info["version"])
    attendu = (
        encodage["modele"],
        encodage["revision"],
        encodage["precision_calcul"],
        encodage["outil_version"],
    )
    if servi != attendu:
        raise ServiceNonConforme(f"servi {servi} ≠ encodage {attendu}")


def comparer(client: ClientService, export: dict, config: dict) -> dict:
    confronter(client, export["encodage"])
    ech = export["echantillon"]
    lot = client.instance.taille_lot
    rendus = []
    for i in range(0, len(ech), lot):
        rendus += client.encoder(
            [f["texte"] for f in ech[i : i + lot]], role="document"
        )
    cos = [cosinus(a, f["vecteur"]) for a, f in zip(rendus, ech, strict=True)]
    seuils = config["seuils"]
    ordonnes = sorted(cos)
    return {
        "encodage_id": export["encodage"]["encodage_id"],
        "corpus_id": export["encodage"]["corpus_id"],
        "n": len(cos),
        "cos_min": ordonnes[0],
        "cos_mediane": ordonnes[len(ordonnes) // 2],
        "par_tranche": {
            str(t): min(c for c, f in zip(cos, ech, strict=True) if f["tranche"] == t)
            for t in sorted({f["tranche"] for f in ech})
        },
        "sous_equivalent": [
            {"chunk_id": f["chunk_id"], "longueur": f["longueur"], "cos": c}
            for c, f in zip(cos, ech, strict=True)
            if c < seuils["equivalent"]
        ],
        "verdict": verdict(ordonnes[0], seuils),
        "seuils": seuils,
    }


def client_de(encodage: dict) -> ClientService:
    return ClientService(charger(INSTANCE_DU_MODELE[encodage["modele"]]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sous = parser.add_subparsers(dest="commande", required=True)
    for nom in ("exporter", "comparer"):
        p = sous.add_parser(nom)
        p.add_argument("--encodage", type=int, required=True)
    args = parser.parse_args(argv)
    config = charger_config()
    if args.commande == "exporter":
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise SystemExit("DATABASE_URL absente")
        print(exporter(dsn, args.encodage, config))
        return 0
    export = json.loads(
        chemin_export(config, args.encodage).read_text(encoding="utf-8")
    )
    r = comparer(client_de(export["encodage"]), export, config)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 1 if r["verdict"] == "echec" else 0


if __name__ == "__main__":
    sys.exit(main())
