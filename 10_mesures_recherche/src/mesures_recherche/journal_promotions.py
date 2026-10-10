"""Inscrire une décision au journal des promotions — et revenir en arrière.

    uv run python -m mesures_recherche.journal_promotions etat
    uv run python -m mesures_recherche.journal_promotions promouvoir --encodage 3 \\
        --run <run_id> --motif "…" --par Max
    uv run python -m mesures_recherche.journal_promotions refuser --encodage 3 \\
        --run <run_id> --motif "…" --par Max
    uv run python -m mesures_recherche.journal_promotions revenir --motif "…" --par Max

UNE COMMANDE, UNE LIGNE. Chaque décision ajoute une ligne à `bench.promotions`, et
rien d'autre : ni encodage, ni vecteur, ni fragment ne sont touchés. La vue
`bench.v_encodage_en_service` en tire l'encodage que lisent les mesures et la
recherche. La base contrôle chaque ligne à l'insertion (migration 021) : encodage
terminé, précédent = encodage en service, pas déjà en service.

LA RÈGLE, AVANT LA DÉCISION. `promouvoir` et `refuser` comparent d'abord le run à la
mesure 8 (`promotion.comparer`, règle commitée, mêmes garde-fous) :
  - le run doit être celui de l'encodage décidé (`bench.eval_runs`) ;
  - une décision contraire au verdict de la règle n'est inscrite qu'avec
    `--derogation`, et une décision conforme ne la porte pas.
La décision reste celle de Max ; le journal dit si elle suit la règle.

LE RETOUR ARRIÈRE remet en service l'encodage que la décision en service a remplacé
(son `encodage_precedent_id`), ou celui que `--vers` désigne ; la base vérifie qu'il
a déjà été servi. Les vecteurs des deux encodages restent en base : revenir, c'est
une ligne au journal.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import psycopg

from mesures_recherche import promotion
from mesures_recherche.enregistrement import STOCKAGE

DECISIONS = {"promouvoir": "promu", "refuser": "refuse"}


class DecisionRefusee(RuntimeError):
    """La décision n'est pas inscrite : rien n'a été écrit."""


def en_service(cx) -> dict | None:
    r = cx.execute(
        "SELECT encodage_id, corpus_id, modele, promotion_id, decision"
        " FROM bench.v_encodage_en_service"
    )
    ligne = r.fetchone()
    return (
        dict(zip([c.name for c in r.description], ligne, strict=True))
        if ligne
        else None
    )


def inscrire(
    cx,
    *,
    decision: str,
    encodage_id: int,
    precedent: int | None,
    run_id: str | None,
    motif: str,
    par: str,
    derogation: bool,
) -> dict:
    r = cx.execute(
        "INSERT INTO bench.promotions (decision, encodage_id, encodage_precedent_id,"
        " run_id, motif, decide_par, derogation)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)"
        " RETURNING promotion_id, decide_le, decision, encodage_id,"
        " encodage_precedent_id, run_id, decide_par, derogation, motif",
        (
            decision,
            encodage_id,
            precedent,
            uuid.UUID(run_id) if run_id else None,
            motif,
            par,
            derogation,
        ),
    )
    return dict(zip([c.name for c in r.description], r.fetchone(), strict=True))


def decider(
    dsn: str,
    *,
    decision: str,
    encodage_id: int,
    run_id: str,
    motif: str,
    par: str,
    derogation: bool = False,
    regle: dict | None = None,
    stockage: Path = STOCKAGE,
) -> dict:
    """Promouvoir (`promu`) ou refuser (`refuse`) l'encodage d'un run mesuré."""
    if decision not in DECISIONS.values():
        raise ValueError(decision)
    comparaison = promotion.comparer(
        dsn, run_id, regle or promotion.charger_regle(), stockage
    )
    if comparaison["run"]["encodage_id"] != str(encodage_id):
        raise DecisionRefusee(
            f"le run {run_id} a mesuré l'encodage {comparaison['run']['encodage_id']},"
            f" pas l'encodage {encodage_id}"
        )
    conforme = (decision == "promu") == (comparaison["verdict"] == "promotion proposée")
    if conforme and derogation:
        raise DecisionRefusee(
            f"« {comparaison['verdict']} » : la décision suit la règle, ce n'est pas"
            " une dérogation"
        )
    if not conforme and not derogation:
        raise DecisionRefusee(
            f"« {comparaison['verdict']} » : la décision s'en écarte —"
            " une dérogation, à déclarer (--derogation) avec son motif"
        )
    with psycopg.connect(dsn) as cx:
        avant = en_service(cx)
        ligne = inscrire(
            cx,
            decision=decision,
            encodage_id=encodage_id,
            precedent=avant["encodage_id"] if avant else None,
            run_id=run_id,
            motif=motif,
            par=par,
            derogation=derogation,
        )
        apres = en_service(cx)
    return {
        "verdict": comparaison["verdict"],
        "avant": avant,
        "ligne": ligne,
        "apres": apres,
    }


def revenir(dsn: str, *, motif: str, par: str, vers: int | None = None) -> dict:
    """Le retour arrière : une ligne `retour_arriere` au journal."""
    with psycopg.connect(dsn) as cx:
        avant = en_service(cx)
        if avant is None:
            raise DecisionRefusee("aucun encodage en service : rien à défaire")
        if vers is None:
            (vers,) = cx.execute(
                "SELECT encodage_precedent_id FROM bench.promotions"
                " WHERE promotion_id = %s",
                (avant["promotion_id"],),
            ).fetchone()
            if vers is None:
                raise DecisionRefusee(
                    f"la décision en service (promotion {avant['promotion_id']}) n'a"
                    " pas de précédent : désigner l'encodage (--vers)"
                )
        ligne = inscrire(
            cx,
            decision="retour_arriere",
            encodage_id=vers,
            precedent=avant["encodage_id"],
            run_id=None,
            motif=motif,
            par=par,
            derogation=False,
        )
        apres = en_service(cx)
    return {"avant": avant, "ligne": ligne, "apres": apres}


def etat(dsn: str) -> dict:
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as cx:
        r = cx.execute(
            "SELECT promotion_id, decide_le, decision, encodage_id,"
            " encodage_precedent_id, run_id, decide_par, derogation"
            " FROM bench.promotions ORDER BY promotion_id"
        )
        noms = [c.name for c in r.description]
        journal = [dict(zip(noms, ligne, strict=True)) for ligne in r.fetchall()]
        return {"en_service": en_service(cx), "journal": journal}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sous = parser.add_subparsers(dest="commande", required=True)
    sous.add_parser("etat")
    for nom in DECISIONS:
        p = sous.add_parser(nom)
        p.add_argument("--encodage", type=int, required=True)
        p.add_argument("--run", required=True, help="run_id MLflow de la mesure")
        p.add_argument("--derogation", action="store_true")
    p = sous.add_parser("revenir")
    p.add_argument("--vers", type=int, default=None)
    for nom in (*DECISIONS, "revenir"):
        sous.choices[nom].add_argument("--motif", required=True)
        sous.choices[nom].add_argument("--par", required=True)
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL absente")
    if args.commande == "etat":
        r = etat(dsn)
    elif args.commande == "revenir":
        r = revenir(dsn, motif=args.motif, par=args.par, vers=args.vers)
    else:
        r = decider(
            dsn,
            decision=DECISIONS[args.commande],
            encodage_id=args.encodage,
            run_id=args.run,
            motif=args.motif,
            par=args.par,
            derogation=args.derogation,
        )
    print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
