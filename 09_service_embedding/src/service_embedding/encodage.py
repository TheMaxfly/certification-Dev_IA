"""Encodage du corpus par une instance du service — spec E2, bloc C.

    uv run python -m service_embedding.encodage bge-m3 --sortie bilan.json
    uv run python -m service_embedding.encodage embeddinggemma --corpus v2 --sortie …

UN CORPUS, NOMMÉ (migration 021). Plusieurs versions du corpus coexistent dans
`bench` : l'encodage porte sur un corpus, nommé par `--corpus` ; sans nom, le seul
corpus de la base, et s'il y en a plusieurs, refus. L'encodage est inscrit au
registre avec son corpus ; deux encodages d'un même modèle coexistent dans la
table, sur deux corpus. Sur un même corpus, un second encodage reste refusé.

UNE SEULE ÉCRITURE DE VECTEURS (CONTRIBUTING.md, « Écrire dans le corpus ou les
vecteurs ») : `ecrire_vecteurs`, corpus en argument nommé, sans défaut.

Lit les fragments (`bench.corpus_chunks`) sur une connexion en LECTURE SEULE,
les envoie au service par lots de `CLIENT_TAILLE_LOT` (préfixe « document »
appliqué par le client), et écrit les vecteurs dans la table du modèle
(migration 020) sur une seconde connexion.

Reprise : seuls les fragments sans vecteur sont encodés, et l'écriture est
validée tous les `--validation` lots ; un encodage interrompu reprend où il
s'est arrêté, sous la même ligne de `bench.encodages` (mêmes paramètres = même
encodage). Un rejeu complet ne trouve rien à encoder et n'écrit RIEN — ni
vecteur, ni mise à jour de `bench.encodages`.

Refus, avant toute écriture : instance qui ne sert pas le modèle, la révision ou
la précision de la configuration ; image non épinglée par digest ; table déjà
remplie par un autre encodage ; encodage terminé dont le corpus a changé depuis.

Surveillance (§6) : mémoire vidéo et vive de l'instance, et swap écrit. Si du
swap s'écrit de façon continue, l'encodage s'arrête proprement (ce qui est écrit
reste écrit) : on réduit le lot et on reprend.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 — docker inspect, commande fixe, sans shell
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg

from service_embedding import mesures
from service_embedding.client import ClientService, norme
from service_embedding.configuration import INSTANCES, Instance, charger

TABLES = {
    "BAAI/bge-m3": "bench.vecteurs_bge_m3",
    "google/embeddinggemma-300m": "bench.vecteurs_embeddinggemma",
}
OUTIL = "text-embeddings-inference"
TOLERANCE_NORME = 1e-3


class EncodageRefuse(Exception):
    """L'encodage ne peut pas commencer ; rien n'a été écrit."""


class ArretSwap(Exception):
    """Du swap s'écrit en continu : arrêt propre, lot à réduire."""


@dataclass(frozen=True)
class Service:
    """Ce que l'instance dit servir, et l'image qui la porte."""

    modele: str
    revision: str
    precision: str
    version: str
    image: str
    digest: str


def service_de(client: ClientService, image_ref: str) -> Service:
    info = client.info()
    if "@" not in image_ref:
        raise EncodageRefuse(f"image non épinglée par digest : {image_ref}")
    image, digest = image_ref.split("@", 1)
    return Service(
        modele=info["model_id"],
        revision=info["model_sha"],
        precision=info["model_dtype"],
        version=info["version"],
        image=image,
        digest=digest,
    )


def image_du_conteneur(nom: str) -> str:
    """Référence de l'image telle que le conteneur l'a lancée (tag@digest)."""
    return subprocess.run(  # nosec B603 B607 — commande fixe, sans shell
        ["docker", "inspect", "-f", "{{.Config.Image}}", nom],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def verifier_service(instance: Instance, service: Service) -> None:
    attendu = (instance.model_id, instance.revision, instance.dtype)
    servi = (service.modele, service.revision, service.precision)
    if servi != attendu:
        raise EncodageRefuse(f"l'instance sert {servi}, la configuration dit {attendu}")


def vecteur_texte(vecteur: list[float]) -> str:
    return "[" + ",".join(repr(x) for x in vecteur) + "]"


def ecrire_vecteurs(
    cx: psycopg.Connection, *, corpus_id: str, encodage_id: int, modele: str, lignes
) -> int:
    """LA seule écriture de vecteurs du module 09 ; rend le nombre écrit.

    `lignes` : des (chunk_id, vecteur). Le corpus est NOMMÉ, jamais laissé au
    défaut de la base : les clés composites de 021 refusent alors tout vecteur
    dont le fragment ou l'encodage n'est pas de ce corpus.
    """
    copies = {
        "BAAI/bge-m3": "COPY bench.vecteurs_bge_m3"
        " (encodage_id, chunk_id, corpus_id, embedding) FROM STDIN",
        "google/embeddinggemma-300m": "COPY bench.vecteurs_embeddinggemma"
        " (encodage_id, chunk_id, corpus_id, embedding) FROM STDIN",
    }
    n = 0
    with cx.cursor().copy(copies[modele]) as copie:
        for chunk_id, vecteur in lignes:
            copie.write_row((encodage_id, chunk_id, corpus_id, vecteur_texte(vecteur)))
            n += 1
    return n


def corpus_a_encoder(cx: psycopg.Connection, corpus_id: str | None) -> str:
    """Le corpus nommé ; sans nom, le seul corpus de la base — plusieurs : refus."""
    if corpus_id is not None:
        connu = cx.execute(
            "SELECT 1 FROM bench.corpus WHERE corpus_id = %s", (corpus_id,)
        ).fetchone()
        if connu is None:
            raise EncodageRefuse(f"corpus {corpus_id} inconnu de bench.corpus")
        return corpus_id
    tous = [c for (c,) in cx.execute("SELECT corpus_id FROM bench.corpus ORDER BY 1")]
    if len(tous) != 1:
        raise EncodageRefuse(
            f"{len(tous)} corpus dans bench : nommer le corpus à encoder (--corpus)"
        )
    return tous[0]


def ligne_encodage(
    cx, instance: Instance, service: Service, corpus_id: str
) -> tuple | None:
    """La ligne du registre retrouvée PAR ce que sert l'instance, sur ce corpus :
    le service est confronté au registre par construction."""
    return cx.execute(
        "SELECT encodage_id, termine_le, nb_fragments, taille_lot"
        " FROM bench.encodages"
        " WHERE corpus_id = %s AND modele = %s AND revision = %s"
        "   AND precision_calcul = %s"
        "   AND prefixe_document = %s AND prefixe_requete = %s AND outil = %s"
        "   AND outil_version = %s AND image_digest IS NOT DISTINCT FROM %s",
        (
            corpus_id,
            service.modele,
            service.revision,
            service.precision,
            instance.prefixe_document,
            instance.prefixe_requete,
            OUTIL,
            service.version,
            service.digest,
        ),
    ).fetchone()


def encoder_corpus(
    instance: Instance,
    client: ClientService,
    service: Service,
    dsn: str,
    *,
    corpus_id: str | None = None,
    taille_lot: int | None = None,
    validation: int = 32,
    surveillance: mesures.Surveillance | None = None,
) -> dict:
    """Encode les fragments du corpus sans vecteur de cet encodage ; rend le bilan
    (écritures comprises). `corpus_id` : voir `corpus_a_encoder`."""
    verifier_service(instance, service)
    table = TABLES[instance.model_id]
    lot = taille_lot or instance.taille_lot
    if not 0 < lot <= instance.plafond_lot:
        raise EncodageRefuse(f"lot {lot} hors de ]0, {instance.plafond_lot}]")

    lecture = psycopg.connect(dsn, options="-c default_transaction_read_only=on")
    ecriture = psycopg.connect(dsn)
    bilan: dict = {
        "instance": instance.nom,
        "table": table,
        "taille_lot": lot,
        "vecteurs_ecrits": 0,
        "encodage_cree": False,
        "encodage_mis_a_jour": False,
    }
    try:
        corpus_id = corpus_a_encoder(lecture, corpus_id)
        bilan["corpus_id"] = corpus_id
        corpus = lecture.execute(
            "SELECT count(*) FROM bench.corpus_chunks WHERE corpus_id = %s",
            (corpus_id,),
        ).fetchone()
        bilan["fragments_corpus"] = corpus[0]
        autres = lecture.execute(
            f"SELECT count(*) FROM {table} v JOIN bench.encodages e"  # nosec B608
            " USING (encodage_id) WHERE e.corpus_id = %s AND NOT (e.revision = %s AND"
            " e.precision_calcul = %s AND e.outil_version = %s AND"
            " e.image_digest IS NOT DISTINCT FROM %s AND e.prefixe_document = %s)",
            (
                corpus_id,
                service.revision,
                service.precision,
                service.version,
                service.digest,
                instance.prefixe_document,
            ),
        ).fetchone()[0]
        if autres:
            raise EncodageRefuse(
                f"{table} porte, pour le corpus {corpus_id}, {autres} vecteurs d'un"
                " autre encodage : un seul encodage par modèle et par corpus"
            )
        deja = ligne_encodage(lecture, instance, service, corpus_id)
        a_faire = lecture.execute(
            "SELECT c.chunk_id, c.chunk_text FROM bench.corpus_chunks c"
            " WHERE c.corpus_id = %s"
            f" AND NOT EXISTS (SELECT 1 FROM {table} v"  # nosec B608
            "                  WHERE v.encodage_id = %s AND v.chunk_id = c.chunk_id)"
            " ORDER BY c.chunk_id",
            (corpus_id, deja[0] if deja else None),
        ).fetchall()
        lecture.rollback()
        bilan["fragments_a_encoder"] = len(a_faire)

        existante = ligne_encodage(ecriture, instance, service, corpus_id)
        if existante and existante[1] is not None and a_faire:
            raise EncodageRefuse(
                f"encodage {existante[0]} terminé ({existante[2]} fragments), mais "
                f"{len(a_faire)} fragments sans vecteur : le corpus a changé"
            )
        if not a_faire:
            ecriture.rollback()
            bilan |= {"encodage_id": existante[0] if existante else None}
            return bilan | _cloture(ecriture, table, existante, bilan)

        if existante is None:
            encodage_id = ecriture.execute(
                "INSERT INTO bench.encodages (corpus_id, modele, revision,"
                " dimension, precision_calcul, prefixe_document, prefixe_requete,"
                " outil, outil_version, image, image_digest, taille_lot)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " RETURNING encodage_id",
                (
                    corpus_id,
                    service.modele,
                    service.revision,
                    instance.dimension,
                    service.precision,
                    instance.prefixe_document,
                    instance.prefixe_requete,
                    OUTIL,
                    service.version,
                    service.image,
                    service.digest,
                    lot,
                ),
            ).fetchone()[0]
            bilan["encodage_cree"] = True
        else:
            encodage_id = existante[0]
            if existante[3] != lot:
                # Le lot a changé à la reprise (réduction pour le swap) : la ligne
                # garde le dernier lot réellement utilisé.
                ecriture.execute(
                    "UPDATE bench.encodages SET taille_lot = %s WHERE encodage_id = %s",
                    (lot, encodage_id),
                )
                bilan["encodage_mis_a_jour"] = True
        ecriture.commit()
        bilan["encodage_id"] = encodage_id

        debut = time.monotonic()
        lots = [a_faire[i : i + lot] for i in range(0, len(a_faire), lot)]
        for numero, paquet in enumerate(lots, start=1):
            if surveillance is not None and surveillance.swap_continu:
                ecriture.commit()
                raise ArretSwap(
                    f"swap écrit {surveillance.secondes_continues} s d'affilée "
                    f"après {bilan['vecteurs_ecrits']} vecteurs : réduire le lot"
                )
            vecteurs = client.encoder([t for _, t in paquet], role="document")
            for v in vecteurs:
                if abs(norme(v) - 1.0) > TOLERANCE_NORME:
                    raise EncodageRefuse(f"vecteur de norme {norme(v)} rendu")
            bilan["vecteurs_ecrits"] += ecrire_vecteurs(
                ecriture,
                corpus_id=corpus_id,
                encodage_id=encodage_id,
                modele=instance.model_id,
                lignes=zip((c for c, _ in paquet), vecteurs, strict=True),
            )
            if numero % validation == 0:
                ecriture.commit()
        ecriture.commit()
        bilan["duree_encodage_s"] = round(time.monotonic() - debut, 1)
        bilan["debit_fragments_s"] = round(
            bilan["vecteurs_ecrits"] / max(bilan["duree_encodage_s"], 1e-9), 1
        )
        existante = ligne_encodage(ecriture, instance, service, corpus_id)
        return bilan | _cloture(ecriture, table, existante, bilan)
    finally:
        lecture.close()
        ecriture.close()


def _cloture(cx, table: str, ligne: tuple | None, bilan: dict) -> dict:
    """Pose `termine_le` / `nb_fragments` si l'encodage est complet — et seulement
    s'ils changent : un rejeu complet n'écrit rien."""
    if ligne is None:
        return {"termine": False}
    encodage_id, termine_le, nb_fragments, _ = ligne
    compte = cx.execute(
        f"SELECT count(*) FROM {table} WHERE encodage_id = %s",  # nosec B608
        (encodage_id,),
    ).fetchone()[0]
    complet = compte == bilan["fragments_corpus"]
    if complet and (termine_le is None or nb_fragments != compte):
        cx.execute(
            "UPDATE bench.encodages SET termine_le = now(), nb_fragments = %s"
            " WHERE encodage_id = %s",
            (compte, encodage_id),
        )
        cx.commit()
        bilan["encodage_mis_a_jour"] = True
    else:
        cx.rollback()
    return {"termine": complet, "vecteurs_de_l_encodage": compte}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("instance", choices=INSTANCES)
    parser.add_argument("--corpus", default=None, help="corpus à encoder")
    parser.add_argument("--sortie", type=Path, required=True)
    parser.add_argument("--lot", type=int, default=None)
    parser.add_argument("--validation", type=int, default=32)
    args = parser.parse_args(argv)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL absente")
    instance = charger(args.instance)
    client = ClientService(instance)
    conteneur = f"tei-{args.instance}"
    service = service_de(client, image_du_conteneur(conteneur))
    surveillance = mesures.Surveillance(conteneur).demarrer()
    code = 0
    try:
        bilan = encoder_corpus(
            instance,
            client,
            service,
            dsn,
            corpus_id=args.corpus,
            taille_lot=args.lot,
            validation=args.validation,
            surveillance=surveillance,
        )
    except ArretSwap as exc:
        bilan, code = {"arret": str(exc)}, 3
    bilan["surveillance"] = surveillance.arreter()
    bilan["memoire_fin"] = mesures.memoire_conteneur(conteneur)
    bilan["service"] = service.__dict__
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(json.dumps(bilan, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(bilan, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
