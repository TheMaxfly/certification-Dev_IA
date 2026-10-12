"""Contrôles du bloc A sur une instance en marche — spec E2 §2.5.

    uv run python -m service_embedding.controles etat
    uv run python -m service_embedding.controles instance bge-m3 --sortie f.json

`etat` relève ce que la spec demande avant chaque lancement (mémoire vive
disponible, swap occupé, mémoire vidéo libre) et sort en erreur sous un seuil.

`instance` interroge une instance lancée et rend, en JSON :
- ce que l'instance dit servir (`/info`), confronté à la configuration :
  modèle, révision, précision, longueur maximale ;
- dimension et norme des vecteurs, fragments et question (préfixes compris) ;
- le même texte encodé deux fois : écart maximal entre les deux vecteurs ;
- l'adresse des métriques et son compteur de requêtes, avant et après ;
- la troncature : longueur, selon le tokenizer DU SERVICE, de chaque fragment
  du corpus préfixé, et encodage sans troncature des plus longs ;
- la mémoire de l'instance au repos.

Le corpus est lu en lecture seule (`default_transaction_read_only`) ; rien
n'est écrit en base.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from service_embedding import mesures
from service_embedding.client import ClientService, ErreurService, norme
from service_embedding.configuration import INSTANCES, charger

TOLERANCE_NORME = 1e-3
LOT_TOKENISATION = 64
TEXTES = [
    "Un lycéen découvre un carnet qui tue quiconque y voit son nom inscrit.",
    "Shonen nekketsu : un équipage de pirates en quête d'un trésor légendaire.",
    "Chronique douce d'une famille de paysans dans le Japon rural d'après-guerre.",
]
QUESTION = "Un manga de pirates avec beaucoup d'humour et d'aventure"
COMPTEUR_REQUETES = "te_request_count"


def connexion_lecture():
    import psycopg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL absente : le balayage du corpus en a besoin")
    return psycopg.connect(dsn, options="-c default_transaction_read_only=on")


#: Le corpus balayé (migration 021) — même règle que
#: `evaluation.atteignabilite.corpus_lu` (module 05) : nommé, sinon celui de
#: l'encodage en service, sinon le seul corpus de la base ; jamais deviné.
SQL_CORPUS_LU = """
SELECT coalesce(
  (SELECT corpus_id FROM bench.v_encodage_en_service),
  (SELECT min(corpus_id) FROM bench.corpus HAVING count(*) = 1))
"""


def fragments(corpus_id: str | None = None) -> list[tuple[int, str]]:
    with connexion_lecture() as cx:
        if corpus_id is None:
            (corpus_id,) = cx.execute(SQL_CORPUS_LU).fetchone()
            if corpus_id is None:
                raise SystemExit("plusieurs corpus et aucun en service : le nommer")
        return cx.execute(
            "SELECT chunk_id, chunk_text FROM bench.corpus_chunks"
            " WHERE corpus_id = %s ORDER BY chunk_id",
            (corpus_id,),
        ).fetchall()


def compteur(texte_metriques: str, nom: str) -> float | None:
    """Somme des séries d'un compteur Prometheus (toutes étiquettes)."""
    total, vu = 0.0, False
    for ligne in texte_metriques.splitlines():
        if re.match(rf"^{re.escape(nom)}(\{{.*\}})?\s", ligne):
            total += float(ligne.rsplit(maxsplit=1)[1])
            vu = True
    return total if vu else None


def compteurs_declares(texte_metriques: str) -> list[str]:
    return sorted(
        ligne.split()[2]
        for ligne in texte_metriques.splitlines()
        if ligne.startswith("# TYPE ") and ligne.split()[3] == "counter"
    )


def controler_instance(nom: str, balayer_corpus: bool = True) -> dict:
    instance = charger(nom)
    client = ClientService(instance)
    conteneur = f"tei-{nom}"
    r: dict = {
        "instance": nom,
        "horodatage": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    r["sante"] = client.sante()
    r["memoire_repos_avant"] = mesures.memoire_conteneur(conteneur)
    info = client.info()
    r["info"] = info
    r["conformite"] = {
        "model_id": info.get("model_id") == instance.model_id,
        "revision": info.get("model_sha") == instance.revision,
        "dtype": info.get("model_dtype") == instance.dtype,
        "longueur_maximale": info.get("max_input_length") == instance.longueur_maximale,
        "longueur_couvre_corpus": (info.get("max_input_length") or 0)
        >= instance.longueur_requise,
    }

    # Compteur de requêtes, avant toute requête d'encodage de ce contrôle.
    avant = client.metriques()
    r["metriques"] = {
        "url": client.url_metriques,
        "compteurs": compteurs_declares(avant),
        # Absent avant la première requête : le service ne déclare un compteur
        # qu'à sa première incrémentation. Absent compte pour zéro ici.
        "requetes_avant": compteur(avant, COMPTEUR_REQUETES) or 0.0,
    }

    documents = client.encoder(TEXTES, role="document")
    requete = client.encoder([QUESTION], role="requete")
    vecteurs = documents + requete
    ecarts = [abs(norme(v) - 1.0) for v in vecteurs]
    r["vecteurs"] = {
        "dimensions": sorted({len(v) for v in vecteurs}),
        "dimension_attendue": instance.dimension,
        "ecart_norme_max": max(ecarts),
        "normes_conformes": all(e <= TOLERANCE_NORME for e in ecarts),
    }

    premier = client.encoder([TEXTES[0]], role="document")[0]
    second = client.encoder([TEXTES[0]], role="document")[0]
    r["determinisme"] = {
        "ecart_absolu_max": max(
            abs(a - b) for a, b in zip(premier, second, strict=True)
        ),
        "identiques": premier == second,
        # Même texte, seul puis au sein d'un lot : renseigne sur la reprise.
        "ecart_seul_vs_lot": max(
            abs(a - b) for a, b in zip(premier, documents[0], strict=True)
        ),
    }

    # Au-delà de la longueur maximale : la requête doit échouer, pas tronquer.
    trop_long = "manga " * (instance.longueur_maximale + 8)
    try:
        client.encoder([trop_long], role="document")
        r["au_dela_longueur_maximale"] = {"refuse": False, "message": None}
    except ErreurService as exc:
        r["au_dela_longueur_maximale"] = {"refuse": True, "message": str(exc)[:300]}

    apres = client.metriques()
    r["metriques"]["requetes_apres"] = compteur(apres, COMPTEUR_REQUETES)

    if balayer_corpus:
        r["troncature"] = balayer(client)

    time.sleep(5)
    r["memoire_repos_apres"] = mesures.memoire_conteneur(conteneur)
    return r


def balayer(client: ClientService) -> dict:
    """Longueur de chaque fragment préfixé selon le service ; encode les plus longs."""
    corpus = fragments()
    longueurs: list[tuple[int, int]] = []
    debut = time.monotonic()
    for i in range(0, len(corpus), LOT_TOKENISATION):
        lot = corpus[i : i + LOT_TOKENISATION]
        comptes = client.compter_jetons([t for _, t in lot], role="document")
        longueurs.extend(zip((c for c, _ in lot), comptes, strict=True))
    duree = time.monotonic() - debut
    longueurs.sort(key=lambda x: (-x[1], x[0]))
    plus_longs = longueurs[:5]
    textes = dict(corpus)
    vecteurs = client.encoder([textes[c] for c, _ in plus_longs], role="document")
    return {
        "fragments": len(longueurs),
        "max": plus_longs[0][1],
        "plus_longs": plus_longs,
        "au_dela_512": sum(1 for _, n in longueurs if n > 512),
        "au_dela_maximale": sum(
            1 for _, n in longueurs if n > client.instance.longueur_maximale
        ),
        "longueur_requise": client.instance.longueur_requise,
        "plus_longs_encodes_sans_troncature": len(vecteurs) == len(plus_longs),
        "duree_s": round(duree, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sous = parser.add_subparsers(dest="commande", required=True)
    sous.add_parser("etat", help="mémoire avant lancement, et seuils")
    p = sous.add_parser("instance", help="contrôles d'une instance lancée")
    p.add_argument("nom", choices=INSTANCES)
    p.add_argument("--sortie", type=Path, required=True)
    p.add_argument("--sans-corpus", action="store_true")
    args = parser.parse_args(argv)

    if args.commande == "etat":
        etat = mesures.etat_hote()
        ecarts = mesures.seuils_tenus(etat)
        print(json.dumps(etat | {"seuils_non_tenus": ecarts}, ensure_ascii=False))
        return 1 if ecarts else 0

    resultat = controler_instance(args.nom, balayer_corpus=not args.sans_corpus)
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(
        json.dumps(resultat, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: resultat[k] for k in ("conformite", "vecteurs", "determinisme")},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
