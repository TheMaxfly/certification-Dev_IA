#!/usr/bin/env python3
"""Collecte des personnages depuis AniList.

Acces par l'API GraphQL d'AniList. Le perimetre se derive de
`manga.work_identity.anilist_id`, alimente par le pont `mappings` de Kitsu : il
ne transite pas par Wikidata, et couvre 8 051 series contre 1 368 pour
Wikipedia francais.

Comme pour Wikipedia, aucune recherche par titre : le lien est un identifiant
publie.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from commun import rapport
from commun.collecteur import Collecteur, collecte, reconnaissance
from commun.limiteur import Limiteur
from commun.perimetre import Serie

RACINE = Path(__file__).resolve().parent
POINT_ACCES = "https://graphql.anilist.co"

#: Cadence prudente : 2 s entre requetes, soit 30 par minute. AniList a
#: documente 90 requetes/minute, valeur abaissee par le passe. La limite reelle
#: est a confirmer contre la documentation au moment de la collecte — d'ou une
#: valeur conservatrice ici, et une entree dans « ce que je n'ai pas pu etablir ».
CADENCE_S = 2.0


def recuperer(serie: Serie, limiteur: Limiteur) -> dict:
    """Recupere les personnages d'une oeuvre. Seam de la spec dediee.

    Non implemente : la spec de collecte AniList decrit la requete GraphQL, la
    pagination et les champs retenus. L'ossature ne les devine pas.
    """
    raise NotImplementedError(
        "extraction AniList : en attente de la spec de collecte dediee "
        f"(requete GraphQL et champs). Oeuvre visee : {serie.cle_source!r} "
        f"(serie {serie.series_id})."
    )


COLLECTEUR = Collecteur(
    nom="anilist",
    intervalle_minimal_s=CADENCE_S,
    motif_cadence=(
        "valeur conservatrice de 30 requetes/minute ; limite reelle a confirmer "
        "contre la documentation AniList au moment de la collecte"
    ),
    user_agent="(defini au lancement depuis CONTACT_COLLECTE)",
    recuperer=recuperer,
)

NON_ETABLI = [
    "Limite de cadence reelle d'AniList : a confirmer contre la documentation "
    "avant la collecte ; la valeur appliquee est volontairement conservatrice.",
    "Remplissage des descriptions de personnages chez AniList, et leur langue — "
    "le raw Kitsu donne 84,9 % de descriptions, a 94 % en anglais.",
    "Presence d'une graphie japonaise exploitable comme cle de fusion avec les "
    "personnages Kitsu et Wikipedia.",
    "Regime de licence et d'attribution des contenus AniList : a instruire et a "
    "porter au registre C4 avant exploitation.",
    "Recouvrement reel entre les 8 051 series du perimetre AniList et les 1 368 "
    "du perimetre Wikipedia — 1 362 partagent un QID, le reste est a mesurer.",
]


def _user_agent() -> str:
    contact = os.environ.get("CONTACT_COLLECTE", "").strip()
    if not contact:
        raise SystemExit(
            "CONTACT_COLLECTE non defini. Un User-Agent descriptif portant un "
            "moyen de contact est la regle du module, toutes sources confondues.\n"
            "  export CONTACT_COLLECTE='projet-manga (contact@exemple.org)'"
        )
    return f"certification-DevIA-enrichissement-personnages/0.1 ({contact})"


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument(
        "--dsn", default=os.environ.get("APIMANGA_DSN"), help="DSN PostgreSQL"
    )
    sous = analyseur.add_subparsers(dest="mode", required=True)
    sous.add_parser("reconnaissance", help="derive le perimetre et rapporte")
    p_collecte = sous.add_parser("collecte", help="collecte (verrouillee)")
    p_collecte.add_argument("--partition", required=True, help="ex. 2026-09")
    p_collecte.add_argument("--limite-series", type=int, default=None)
    args = analyseur.parse_args(argv)

    if not args.dsn:
        print("APIMANGA_DSN non defini (ou --dsn absent).", file=sys.stderr)
        return 2

    if args.mode == "reconnaissance":
        chemin = reconnaissance(
            COLLECTEUR,
            dsn=args.dsn,
            racine=RACINE,
            non_etabli=NON_ETABLI,
            environnement={"point d'acces": POINT_ACCES, "user-agent": _user_agent()},
        )
        print(f"rapport ecrit : {chemin}")
        print(f"statut : {rapport.statut(chemin)} — la collecte reste verrouillee.")
        return 0

    chemin = collecte(
        COLLECTEUR,
        dsn=args.dsn,
        racine=RACINE,
        partition=args.partition,
        quand=rapport.horodatage(),
        limite_series=args.limite_series,
    )
    print(f"rapport ecrit : {chemin}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as erreur:
        print(f"ECHEC : {type(erreur).__name__}: {erreur}", file=sys.stderr)
        sys.exit(1)
