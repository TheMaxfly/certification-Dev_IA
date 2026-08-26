"""Recalcule tous les chiffres du rapport de couverture de la cascade.

Usage :

    DATABASE_URL='postgresql://manga_api@localhost:5432/apimanga' \
        uv run python -m identity.mesure_couverture_cascade \
        --output /tmp/couverture.json

Le JSON est déterministe : il ne contient pas d'horodatage. La connexion doit
être celle de ``manga_api`` et la mesure s'exécute dans une transaction que le
serveur confirme en lecture seule. Le contrat des relations est contrôlé via
``pg_catalog`` avant les requêtes métier.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from typing import Annotated

import psycopg
import typer

from identity.mesure_formes_auteur import mesurer as mesurer_formes_auteur

MODULE = Path(__file__).resolve().parents[2]
REQUETE = Path(__file__).resolve().parent / "sql" / "mesure_couverture_cascade.sql"
ECHANTILLON = (
    MODULE
    / "data"
    / "rapports"
    / "etage_r"
    / "grille_c3_20260721T162953Z"
    / "echantillon_c3_grille.csv"
)

HISTORIQUE_SECTION_2 = {
    "identites_automatiques": 8_413,
    "en_revue": 952,
    "orphelines": 5_304,
    "rejetees": 1,
    "catalogue": 14_670,
    "precision_confirmee": 100,
}
HISTORIQUE_SECTION_6 = {
    "pivot_wikidata": 8_214,
    "wiki_ja_pct": 57.9,
    "wiki_en_pct": 44.6,
    "wiki_fr_pct": 22.0,
    "wiki_ja_seul": 1_664,
}
HISTORIQUE_SECTION_7 = {
    "undecidable": 53,
    "conflits_multi_candidats": 54,
    "fusibles": 6,
    "collisions_unicite": 56,
    "same_work_moyenne_non_promus": 516,
}
HISTORIQUE_SECTION_44 = {
    "series_decidees": 1_058,
    "series_concordantes": 1_058,
    "series_avec_forme_latine": 1_058,
    "part_forme_latine_pct": 100.0,
    "series_concordantes_nom_d0_seul": 21,
}

# Les trois libellés sont ceux de la grille humaine versionnée. Les expliciter
# évite de transformer après coup tout texte non vide en confirmation.
VERDICTS_HUMAINS_CONFIRMES = {
    "ok",
    "ok ( non d’auteur monkey punch est son pseudo)",
    "page wiki existante seulement en italien",
}

CONTRAT = {
    "kitsu_mappings": {"kitsu_id", "external_site", "external_id"},
    "kitsu_formes": {"kitsu_id", "forme_norm"},
    "llm_avis": {
        "avis_id",
        "series_id",
        "phase",
        "candidat_type",
        "candidat_id",
        "verdict",
        "confiance",
        "dossier_partiel",
        "pre_validation_bandes",
    },
    "match_decision": {"decision_id", "series_id", "method", "status", "details"},
    "ms_series_enriched": {
        "series_id",
        "series_scenariste",
        "series_dessinateur",
        "series_genres",
        "kitsu_genres_json",
        "series_genres_enriched",
    },
    "v_match_current": {"decision_id", "series_id", "method", "status"},
    "wd_auteurs": {"qid", "auteur_qid", "auteur_norm"},
    "wd_auteurs_formes": {"auteur_qid", "forme_norm"},
    "wd_pivot": {"qid", "mal_id", "anilist_id", "wiki_fr", "wiki_en", "wiki_ja"},
    "work_identity": {"series_id", "wikidata_qid", "kitsu_id", "mal_id", "anilist_id"},
}

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurMesure(Exception):
    """Mesure refusée parce qu'un prérequis de certification n'est pas tenu."""


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurMesure(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://manga_api@localhost:5432/apimanga'"
        )
    return url


def pourcentage(numerateur: int, denominateur: int, decimales: int = 1) -> float:
    if not denominateur:
        return 0.0
    return round(100.0 * numerateur / denominateur, decimales)


def mesurer_echantillon(chemin: Path = ECHANTILLON) -> dict[str, int | float]:
    with chemin.open(encoding="utf-8", newline="") as fichier:
        lignes = list(csv.DictReader(fichier))

    verdicts = [ligne["VERDICT_HUMAIN"].strip().casefold() for ligne in lignes]
    confirmes = sum(v in VERDICTS_HUMAINS_CONFIRMES for v in verdicts)
    non_classes = sorted(set(verdicts) - VERDICTS_HUMAINS_CONFIRMES)
    if non_classes:
        raise ErreurMesure(
            "Verdict humain non classé dans l'échantillon : " + ", ".join(non_classes)
        )
    strates = {
        nom: sum(ligne["strate"] == nom for ligne in lignes)
        for nom in ("historique", "score_bas", "pont", "standard")
    }
    return {
        "decisions_arbitrees": len(lignes),
        "decisions_confirmees": confirmes,
        "precision_pct": pourcentage(confirmes, len(lignes), 1),
        "strate_historique": strates["historique"],
        "strate_score_bas": strates["score_bas"],
        "strate_pont": strates["pont"],
        "strate_standard": strates["standard"],
    }


def verifier_session(cur) -> dict[str, str]:
    cur.execute(
        "SELECT current_user, current_setting('default_transaction_read_only'), "
        "current_setting('transaction_read_only')"
    )
    utilisateur, defaut_lecture_seule, transaction_lecture_seule = cur.fetchone()
    if utilisateur != "manga_api":
        raise ErreurMesure(
            f"Rôle courant {utilisateur!r}, attendu 'manga_api' — mesure refusée."
        )
    if defaut_lecture_seule != "on" or transaction_lecture_seule != "on":
        raise ErreurMesure("La transaction n'est pas confirmée en lecture seule.")
    return {
        "database_user": utilisateur,
        "default_transaction_read_only": defaut_lecture_seule,
        "transaction_read_only": transaction_lecture_seule,
    }


def verifier_contrat(cur) -> None:
    cur.execute(
        "SELECT c.relname, a.attname "
        "FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid "
        "WHERE n.nspname = 'manga' AND a.attnum > 0 AND NOT a.attisdropped"
    )
    colonnes: dict[str, set[str]] = {}
    for relation, colonne in cur.fetchall():
        colonnes.setdefault(relation, set()).add(colonne)

    manquants = {
        relation: sorted(attendues - colonnes.get(relation, set()))
        for relation, attendues in CONTRAT.items()
        if attendues - colonnes.get(relation, set())
    }
    if manquants:
        detail = "; ".join(f"{r}: {', '.join(c)}" for r, c in manquants.items())
        raise ErreurMesure(f"Contrat de colonnes incomplet — {detail}")


def mesurer_bdd(cur) -> dict:
    cur.execute(REQUETE.read_text(encoding="utf-8"))
    colonnes = [description.name for description in cur.description]
    return dict(zip(colonnes, cur.fetchone(), strict=True))


def comparaison(attendus: dict, mesures: dict) -> dict:
    return {
        nom: {
            "attendu_2026_07_21": attendu,
            "mesure_2026_08_26": mesures[nom],
            "ecart": round(mesures[nom] - attendu, 3),
        }
        for nom, attendu in attendus.items()
    }


def verifier_regle_arret(section_2: dict[str, int | float]) -> None:
    if (
        section_2["identites_automatiques"]
        + section_2["en_revue"]
        + section_2["orphelines"]
        + section_2["rejetees"]
        != section_2["catalogue"]
    ):
        raise ErreurMesure("La somme de contrôle de la section 2 ne tombe pas.")

    for nom, attendu in HISTORIQUE_SECTION_2.items():
        mesure = section_2[nom]
        ecart_relatif = abs(mesure - attendu) / attendu * 100 if attendu else 0
        if ecart_relatif > 1:
            raise ErreurMesure(
                f"Écart de {ecart_relatif:.2f} % sur {nom}, supérieur au seuil de 1 %."
            )


def assembler_mesure(
    bdd: dict, echantillon: dict[str, int | float], formes: dict
) -> dict:
    section_2 = {
        "identites_automatiques": bdd["identites_auto"],
        "en_revue": bdd["needs_review"],
        "orphelines": bdd["orphelines"],
        "rejetees": bdd["rejected"],
        "catalogue": bdd["catalogue_total"],
        "precision_confirmee": echantillon["decisions_confirmees"],
        "precision_denominateur": echantillon["decisions_arbitrees"],
        "precision_pct": echantillon["precision_pct"],
        "somme_controle": (
            bdd["identites_auto"]
            + bdd["needs_review"]
            + bdd["orphelines"]
            + bdd["rejected"]
        ),
    }
    verifier_regle_arret(section_2)

    section_6 = {
        "pivot_wikidata": bdd["pivot_total"],
        "wiki_ja": bdd["wiki_ja"],
        "wiki_ja_pct": pourcentage(bdd["wiki_ja"], bdd["pivot_total"]),
        "wiki_en": bdd["wiki_en"],
        "wiki_en_pct": pourcentage(bdd["wiki_en"], bdd["pivot_total"]),
        "wiki_fr": bdd["wiki_fr"],
        "wiki_fr_pct": pourcentage(bdd["wiki_fr"], bdd["pivot_total"]),
        "wiki_ja_seul": bdd["wiki_ja_seul"],
        "identites_qid": bdd["identites_qid"],
        "identites_kitsu": bdd["identites_kitsu"],
        "identites_mal": bdd["identites_mal"],
        "identites_anilist": bdd["identites_anilist"],
    }
    section_7 = {
        "undecidable": bdd["undecidable"],
        "conflits_multi_candidats": bdd["conflits_multi"],
        "fusibles": bdd["fusibles"],
        "collisions_unicite": bdd["collisions_unicite"],
        "same_work_moyenne_non_promus": bdd["same_work_moyenne"],
    }

    return {
        "schema_version": 1,
        "section_2": section_2,
        "section_2_comparaison": comparaison(HISTORIQUE_SECTION_2, section_2),
        "section_3": {
            "kitsu_bridge_initial": bdd["kitsu_bridge_initial"],
            "kitsu_bridge_courant": bdd["kitsu_bridge_current"],
            "exact": bdd["exact"],
            "exact_author": bdd["exact_author"],
            "exact_kitsu": bdd["exact_kitsu"],
            "exact_kitsu_author": bdd["exact_kitsu_author"],
            "llm_review": bdd["llm_review"],
            "trgm_auto": bdd["trgm_auto"],
            "human_review_rejected": bdd["human_review_rejected"],
            "human_review_rejected_series": bdd["human_review_rejected_series"],
            "kitsu_formes": bdd["kitsu_formes_total"],
            "llm_seau_adjacent": bdd["llm_seau_adjacent"],
            "llm_auteur_pseudonyme": bdd["llm_auteur_pseudonyme"],
            "llm_autres": bdd["llm_autres"],
        },
        "section_4": echantillon,
        "section_6": section_6,
        "section_6_comparaison": comparaison(HISTORIQUE_SECTION_6, section_6),
        "section_7": section_7,
        "section_7_comparaison": comparaison(HISTORIQUE_SECTION_7, section_7),
        "section_38_genres": {
            "series_couvertes": bdd["genres_couvertes"],
            "series_couvertes_pct": pourcentage(
                bdd["genres_couvertes"], bdd["catalogue_total"]
            ),
            "sans_genre_dans_les_sources": bdd["sans_genre_source"],
            "sans_genre_dans_les_sources_pct": pourcentage(
                bdd["sans_genre_source"], bdd["catalogue_total"]
            ),
        },
        "section_44_formes_auteur": {
            "series_exact_author": formes["series_decidees"],
            "series_concordantes": formes["series_concordantes"],
            "concordances_non_retrouvees": (
                formes["series_decidees"] - formes["series_concordantes"]
            ),
            "part_decisions_retrouvees_pct": pourcentage(
                formes["series_concordantes"], formes["series_decidees"], 2
            ),
            "series_avec_forme_latine": formes["series_avec_latine"],
            "part_series_avec_forme_latine_pct": pourcentage(
                formes["series_avec_latine"], formes["series_concordantes"], 2
            ),
            "series_concordantes_nom_d0_seul": formes["series_d0_seul"],
            "graphies_auteur_ms_total": bdd["graphies_ms_total"],
            "graphies_auteur_ms_romanisees": bdd["graphies_ms_romanisees"],
            "graphies_auteur_ms_romanisees_pct": pourcentage(
                bdd["graphies_ms_romanisees"], bdd["graphies_ms_total"], 3
            ),
            "graphies_auteur_ms_japonaises": bdd["graphies_ms_japonaises"],
            "certification_2026_08_25": HISTORIQUE_SECTION_44,
        },
    }


def serialiser(mesure: dict) -> str:
    return json.dumps(mesure, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


@app.command()
def executer(
    output: Annotated[
        Path | None, typer.Option(help="Écrire le JSON à cet emplacement.")
    ] = None,
) -> None:
    """Mesure la cascade et émet son rapport confrontable."""
    echantillon = mesurer_echantillon()
    with psycopg.connect(dsn(), autocommit=True, connect_timeout=10) as connexion:
        connexion.execute("SET default_transaction_read_only = on")
        with connexion.transaction(), connexion.cursor() as cur:
            session = verifier_session(cur)
            verifier_contrat(cur)
            bdd = mesurer_bdd(cur)
            formes = mesurer_formes_auteur(cur)

    mesure = assembler_mesure(bdd, echantillon, formes)
    mesure["source"] = {
        **session,
        "echantillon": str(ECHANTILLON.relative_to(MODULE)),
        "requete": str(REQUETE.relative_to(MODULE)),
    }
    contenu = serialiser(mesure)
    if output is None:
        typer.echo(contenu, nl=False)
    else:
        output.write_text(contenu, encoding="utf-8")
        typer.echo(output)


def main() -> int:
    try:
        app()
    except (ErreurMesure, OSError, KeyError, ValueError) as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR base : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
