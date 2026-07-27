"""Écran d'état — collecteurs STRICTEMENT en lecture.

Aucune écriture, nulle part : que des `SELECT count(*)`, des `stat()` de
fichiers et un `git log -1`. C'est l'écran qu'on laisse affiché à l'ouverture
d'une soutenance ; il doit être vrai, instantané et inoffensif.

Chaque collecteur rend une structure « molle » : une panne (base éteinte,
lakehouse absent) devient un message affiché, jamais une exception qui ferme
la console au mauvais moment.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .actions import RACINE

DSN_EXEMPLE = "postgresql://postgres@localhost:5432/apimanga"


def dsn() -> str | None:
    """La convention du dépôt : DATABASE_URL, jamais d'identifiant en dur."""
    return os.environ.get("DATABASE_URL")


@dataclass
class Bloc:
    """Un panneau de l'écran d'état."""

    titre: str
    lignes: list[tuple[str, str, str]]  # (libellé, valeur, niveau)
    note: str = ""


def _niveau(ok: bool, attention: bool = False) -> str:
    if attention:
        return "attention"
    return "ok" if ok else "erreur"


def bloc_migrations() -> Bloc:
    """Appliquées vs présentes dans le dépôt (public.schema_migrations)."""
    fichiers = sorted((RACINE / "database" / "migrations").glob("*.sql"))
    total = len(fichiers)
    url = dsn()
    if not url:
        return Bloc(
            "Migrations",
            [("dans le dépôt", str(total), "ok"), ("appliquées", "?", "attention")],
            note="DATABASE_URL non définie — état base indisponible.",
        )
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=5) as cx, cx.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.schema_migrations")
            appliquees = cur.fetchone()[0]
    except Exception as erreur:  # noqa: BLE001 — on affiche, on ne plante pas
        return Bloc(
            "Migrations",
            [("dans le dépôt", str(total), "ok")],
            note=f"base injoignable : {type(erreur).__name__}",
        )
    en_attente = total - appliquees
    return Bloc(
        "Migrations",
        [
            ("dans le dépôt", str(total), "ok"),
            ("appliquées", str(appliquees), "ok"),
            ("en attente", str(en_attente), _niveau(en_attente == 0, en_attente > 0)),
        ],
    )


_REQUETES_VOLUMETRIE = [
    ("séries (référentiel MS)", "SELECT count(*) FROM manga.ms_series_enriched"),
    ("volumes", "SELECT count(*) FROM manga.ms_volumes_enriched"),
    # `ms_reviews_all` est le référentiel des critiques. `ms_reviews` est la
    # table HÉRITAGE du corpus RAG (3 187 documents) : l'afficher ici ferait
    # croire à un effondrement du volume de critiques.
    ("critiques (référentiel)", "SELECT count(*) FROM manga.ms_reviews_all"),
    ("décisions journalisées", "SELECT count(*) FROM manga.match_decision"),
]

# Valeurs de référence du snapshot courant — l'écran de démo subit la même
# discipline que le reste : un chiffre qui dérive doit se voir, pas passer.
# Les trois premiers comptes sont figés par le snapshot 2026-07 ; le journal
# des décisions est append-only, donc borné par le bas seulement.
COMPTES_REFERENCE: dict[str, int] = {
    "séries (référentiel MS)": 14_670,
    "volumes": 104_107,
    "critiques (référentiel)": 11_074,  # 11 052 collectées + 22 conservées (B2)
}
DECISIONS_MINIMUM = 10_347


def bloc_base() -> Bloc:
    """Volumétrie + couverture d'identité (SELECT count only)."""
    url = dsn()
    if not url:
        return Bloc("Base apimanga", [], note="DATABASE_URL non définie.")
    lignes: list[tuple[str, str, str]] = []
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=5) as cx, cx.cursor() as cur:
            for libelle, sql in _REQUETES_VOLUMETRIE:
                cur.execute(sql)
                lignes.append(
                    (libelle, f"{cur.fetchone()[0]:,}".replace(",", " "), "ok")
                )
            cur.execute(
                "SELECT count(*) FILTER (WHERE status='auto'),"
                "       count(*) FILTER (WHERE status='needs_review'),"
                "       count(*) FILTER (WHERE status='rejected')"
                "  FROM manga.v_match_current"
            )
            auto, revue, rejete = cur.fetchone()
            cur.execute("SELECT count(*) FROM manga.ms_series_enriched")
            total_series = cur.fetchone()[0] or 1
            pct = 100.0 * auto / total_series
            lignes += [
                (
                    "identités auto",
                    f"{auto:,}".replace(",", " ") + f"  ({pct:.1f} %)",
                    "ok",
                ),
                ("en revue humaine", str(revue), _niveau(True, revue > 0)),
                ("rejetées", str(rejete), "ok"),
            ]
    except Exception as erreur:  # noqa: BLE001
        return Bloc("Base apimanga", lignes, note=f"lecture interrompue : {erreur}")
    return Bloc("Base apimanga", lignes)


def _taille_lisible(octets: int) -> str:
    for unite in ("o", "ko", "Mo", "Go"):
        if octets < 1024 or unite == "Go":
            return f"{octets:.0f} {unite}" if unite == "o" else f"{octets:.1f} {unite}"
        octets /= 1024.0
    return f"{octets:.1f} Go"


def bloc_raw() -> Bloc:
    """Snapshots présents sous data/raw (l'archive immuable du E)."""
    base = RACINE / "04_scraping_manga_sanctuary" / "data" / "raw"
    lignes: list[tuple[str, str, str]] = []
    if not base.is_dir():
        return Bloc("Raw (archive immuable)", [], note=f"absent : {base}")
    for snapshot in sorted(p for p in base.iterdir() if p.is_dir()):
        total = sum(f.stat().st_size for f in snapshot.glob("*.jsonl"))
        nb = len(list(snapshot.glob("*.jsonl")))
        lignes.append(
            (snapshot.name, f"{nb} fichiers · {_taille_lisible(total)}", "ok")
        )
    return Bloc("Raw (archive immuable)", lignes, note="monté en lecture seule")


def bloc_lakehouse() -> Bloc:
    """Tables gold présentes et dernier rapport qualité généré."""
    gold = RACINE / "07_databricks_manga_export" / "data" / "lakehouse" / "gold"
    rapports = RACINE / "07_databricks_manga_export" / "rapports"
    lignes: list[tuple[str, str, str]] = []
    if gold.is_dir():
        tables = sorted(p.name for p in gold.iterdir() if p.is_dir())
        lignes.append(("tables gold", f"{len(tables)} : " + ", ".join(tables), "ok"))
    else:
        lignes.append(("tables gold", "aucune (lancer le pipeline)", "attention"))
    derniers = (
        sorted(rapports.glob("rapport_qualite_*.md")) if rapports.is_dir() else []
    )
    if derniers:
        lignes.append(("dernier rapport", derniers[-1].name, "ok"))
    else:
        lignes.append(("dernier rapport", "aucun", "attention"))
    return Bloc("Lakehouse (module 07)", lignes)


def bloc_git() -> Bloc:
    """Dernier commit — situer la démo dans l'histoire du dépôt."""
    try:
        sortie = subprocess.run(
            ["git", "log", "-1", "--pretty=%h · %s"],
            cwd=RACINE,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except Exception as erreur:  # noqa: BLE001
        return Bloc("Dépôt", [], note=f"git indisponible : {type(erreur).__name__}")
    return Bloc("Dépôt", [("dernier commit", sortie, "ok")])


def collecter() -> list[Bloc]:
    return [
        bloc_migrations(),
        bloc_base(),
        bloc_raw(),
        bloc_lakehouse(),
        bloc_git(),
    ]


def chemin_racine() -> Path:
    return RACINE
