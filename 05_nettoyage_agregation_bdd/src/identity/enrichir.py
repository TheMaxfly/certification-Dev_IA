"""Recalcul des colonnes dérivées de manga.ms_series_enriched.

    uv run python -m identity.enrichir --dry-run
    uv run python -m identity.enrichir
    uv run python -m identity.enrichir --colonne genres

L'ÉTAPE QUI MANQUAIT AU PIPELINE. Les colonnes `*_enriched` existaient parce
qu'un notebook avait tourné une fois en juin 2026 ; aucun code exécutable du
dépôt ne savait les reproduire. Elles ont donc PÉRIMÉ en silence : le sélecteur
XPath des genres de Manga Sanctuary était cassé au moment de ce calcul (corrigé
depuis par `317a658`), `series_genres` était vide à 100 %, et la valeur Kitsu a
été prise partout. Le re-crawl de juillet a réparé la source ; la dérivée, elle,
n'a jamais été rafraîchie. Ce n'est pas un écrasement, c'est une péremption —
et une péremption ne se corrige pas, elle se recalcule.

CE QUE CE CLI ÉCRIT, ET RIEN D'AUTRE
  - `series_genres_enriched`  : des CODES de `manga.genre_ref`, jamais des
    libellés (les deux vocabulaires sources ne partagent que 5 libellés sur
    99 x 55 : un COALESCE produirait un champ bilingue infiltrable) ;
  - `series_synopsis_enriched`: COALESCE(MS, Kitsu) — le synopsis n'a pas
    besoin d'un vocabulaire, la règle d'origine reste valide pour lui.

Jamais `match_method`, `kitsu_id`, `work_uid`, `needs_review` ni aucune colonne
de décision. Jamais de DELETE ni de TRUNCATE : un UPDATE ciblé, donc les quatre
FK `ON DELETE CASCADE` (llm_avis, ms_formes, ms_kitsu_map, ms_kitsu_ambiguous)
restent hors d'atteinte par construction.

`series_tags_enriched` est HORS PÉRIMÈTRE, décision explicite : les tags n'ont
aucune table de correspondance (1 114 libellés MS contre 203 Kitsu, UN seul
commun). Les recalculer sans référentiel ne ferait que déplacer le problème.

LES DEUX RÈGLES SUR LES LIBELLÉS NON COUVERTS
  - libellé ABSENT de `genre_mapping` -> ÉCHEC, avec la liste des fautifs. Sans
    ce garde-fou, un libellé nouveau apparu chez une source disparaîtrait
    silencieusement de la dérivée : c'est exactement ce qui s'est produit en
    juin, et ce qui a mis deux mois à se voir ;
  - libellé présent en `statut='inconnu'` -> ne bloque pas, ne produit aucun
    code, mais est COMPTÉ et rapporté. Une décision d'attente reste visible.

LA DÉRIVATION HIÉRARCHIQUE se fait ici, à l'écriture. Une série portant `yaoi`
reçoit aussi `lgbt` : le filtre générique doit trouver les séries que la source
a décrites plus finement. La lecture, elle, n'a pas à connaître l'arbre.
"""

from __future__ import annotations

import os
import sys
import time

import psycopg
import typer

COLONNES = ("genres", "synopsis")

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurEnrichissement(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurEnrichissement(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'"
        )
    return url


# ---------------------------------------------------------------------------
# Le calcul, en SQL — une CHAÎNE DE CTE, pas une requête complète : mesurer et
# écrire doivent partir exactement de la même expression. La dupliquer ferait
# diverger ce qu'on écrit de ce qu'on annonce, et c'est précisément le genre
# d'écart qui a mis deux mois à se voir.
#
# 1. les libellés bruts des DEUX sources, à plat ;
# 2. leur traduction en codes via genre_mapping (statut='mappe' seulement) ;
# 3. UNION des deux ensembles — pas un COALESCE : les deux sources
#    contribuent, MS n'efface pas Kitsu et Kitsu n'efface pas MS ;
# 4. remontée transitive des ancêtres par genre_ref.parent. `UNION` sans ALL
#    dans la partie récursive : c'est lui qui fait terminer la récursion quand
#    plusieurs enfants partagent un ancêtre.
CTES = """
brut AS (
    SELECT s.series_id, 'ms' AS source, v AS libelle
      FROM manga.ms_series_enriched s,
           LATERAL jsonb_array_elements_text(
               COALESCE(s.series_genres, '[]'::jsonb)) v
    UNION ALL
    SELECT s.series_id, 'kitsu', v
      FROM manga.ms_series_enriched s,
           LATERAL jsonb_array_elements_text(
               COALESCE(s.kitsu_genres_json, '[]'::jsonb)) v
),
direct AS (
    SELECT DISTINCT b.series_id, b.source, m.code
      FROM brut b
      JOIN manga.genre_mapping m
        ON m.source = b.source AND m.libelle_brut = b.libelle
     WHERE m.statut = 'mappe'
),
arbre AS (
    SELECT series_id, code, TRUE AS de_la_source FROM direct
     UNION
    SELECT a.series_id, r.parent, FALSE
      FROM arbre a
      JOIN manga.genre_ref r ON r.code = a.code
     WHERE r.parent IS NOT NULL
),
codes AS (
    SELECT series_id, code, bool_or(de_la_source) AS de_la_source
      FROM arbre GROUP BY series_id, code
)
"""


def requete(corps: str) -> str:
    """La chaîne de CTE, suivie du SELECT ou de l'UPDATE qui l'exploite."""
    return f"WITH RECURSIVE {CTES.strip()}\n{corps}"


def verifier_prerequis(curseur) -> None:
    curseur.execute(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'manga' AND table_name = 'genre_ref' "
        "AND column_name IN ('type', 'parent')"
    )
    if curseur.fetchone()[0] != 2:
        raise ErreurEnrichissement(
            "manga.genre_ref.parent manque : jouer la migration 014.\n"
            "  cd database && uv run python migrate.py up"
        )
    curseur.execute("SELECT count(*) FROM manga.genre_mapping")
    if curseur.fetchone()[0] == 0:
        raise ErreurEnrichissement(
            "manga.genre_mapping est vide : charger le référentiel d'abord.\n"
            "  uv run python -m identity.charger_genres"
        )


def libelles_non_couverts(curseur) -> list[tuple[str, str, int]]:
    """Libellés observés qu'AUCUNE ligne de genre_mapping ne connaît.

    C'est le garde-fou central. Un libellé nouveau chez une source doit faire
    ÉCHOUER le recalcul, pas disparaître de la dérivée."""
    curseur.execute(
        """
        WITH brut AS (
            SELECT 'ms' AS source, v AS libelle, count(*) AS n
              FROM manga.ms_series_enriched s,
                   LATERAL jsonb_array_elements_text(
                       COALESCE(s.series_genres, '[]'::jsonb)) v
             GROUP BY 1, 2
            UNION ALL
            SELECT 'kitsu', v, count(*)
              FROM manga.ms_series_enriched s,
                   LATERAL jsonb_array_elements_text(
                       COALESCE(s.kitsu_genres_json, '[]'::jsonb)) v
             GROUP BY 1, 2
        )
        SELECT b.source, b.libelle, b.n
          FROM brut b
         WHERE NOT EXISTS (
                   SELECT 1 FROM manga.genre_mapping m
                    WHERE m.source = b.source AND m.libelle_brut = b.libelle)
         ORDER BY b.n DESC, b.source, b.libelle
        """
    )
    return curseur.fetchall()


def occurrences_inconnues(curseur) -> list[tuple[str, str, int]]:
    """Libellés connus mais non tranchés : comptés, jamais bloquants."""
    curseur.execute(
        """
        WITH brut AS (
            SELECT 'ms' AS source, v AS libelle
              FROM manga.ms_series_enriched s,
                   LATERAL jsonb_array_elements_text(
                       COALESCE(s.series_genres, '[]'::jsonb)) v
            UNION ALL
            SELECT 'kitsu', v
              FROM manga.ms_series_enriched s,
                   LATERAL jsonb_array_elements_text(
                       COALESCE(s.kitsu_genres_json, '[]'::jsonb)) v
        )
        SELECT b.source, b.libelle, count(*) AS n
          FROM brut b
          JOIN manga.genre_mapping m
            ON m.source = b.source AND m.libelle_brut = b.libelle
         WHERE m.statut = 'inconnu'
         GROUP BY 1, 2
         ORDER BY n DESC, 1, 2
        """
    )
    return curseur.fetchall()


def mesurer_genres(curseur) -> dict[str, int]:
    """Ce que le recalcul produirait, sans rien écrire."""
    curseur.execute(
        requete(
            """
            SELECT count(DISTINCT series_id) FILTER (WHERE de_la_source),
                   count(DISTINCT series_id),
                   count(*) FILTER (WHERE de_la_source),
                   count(*) FILTER (WHERE NOT de_la_source),
                   count(*)
              FROM codes
            """
        )
    )
    ligne = curseur.fetchone()
    return {
        "series_depuis_sources": ligne[0],
        "series_couvertes": ligne[1],
        "codes_des_sources": ligne[2],
        "codes_derives": ligne[3],
        "codes_total": ligne[4],
    }


def couverture_actuelle(curseur) -> int:
    curseur.execute(
        "SELECT count(*) FROM manga.ms_series_enriched "
        "WHERE COALESCE(series_genres_enriched, '[]'::jsonb) <> '[]'::jsonb"
    )
    return curseur.fetchone()[0]


def couverture_source(curseur) -> int:
    """Séries qu'AU MOINS UNE source documente en genres.

    Le dénominateur du contrôle de non-régression : la dérivée ne peut pas
    couvrir moins de séries que ce que les sources décrivent, aux libellés
    `exclu` et `inconnu` près."""
    curseur.execute(
        """
        SELECT count(*) FROM manga.ms_series_enriched
         WHERE COALESCE(series_genres, '[]'::jsonb) <> '[]'::jsonb
            OR COALESCE(kitsu_genres_json, '[]'::jsonb) <> '[]'::jsonb
        """
    )
    return curseur.fetchone()[0]


def regressions(curseur) -> int:
    """Séries couvertes AVANT et qui ne le seraient plus APRÈS.

    Le contrôle qui doit rester à zéro : un recalcul a le droit d'ajouter, pas
    de retirer. C'est précisément ce que personne n'avait mesuré en juin."""
    curseur.execute(
        requete(
            """
            SELECT count(*)
              FROM manga.ms_series_enriched s
             WHERE COALESCE(s.series_genres_enriched, '[]'::jsonb) <> '[]'::jsonb
               AND NOT EXISTS (
                       SELECT 1 FROM codes c WHERE c.series_id = s.series_id)
            """
        )
    )
    return curseur.fetchone()[0]


def ecrire_genres(curseur) -> int:
    """UPDATE ciblé. `IS DISTINCT FROM` : une valeur identique n'est pas
    réécrite, donc le rejeu annonce zéro — mesuré, pas promis."""
    curseur.execute(
        requete(
            """
            , agrege AS (
                SELECT c.series_id,
                       jsonb_agg(c.code ORDER BY r.ordre NULLS LAST, c.code) AS liste
                  FROM codes c
                  JOIN manga.genre_ref r ON r.code = c.code
                 GROUP BY c.series_id
            )
            UPDATE manga.ms_series_enriched s
               SET series_genres_enriched = COALESCE(a.liste, '[]'::jsonb)
              FROM (SELECT s2.series_id, g.liste
                      FROM manga.ms_series_enriched s2
                      LEFT JOIN agrege g ON g.series_id = s2.series_id) a
             WHERE a.series_id = s.series_id
               AND s.series_genres_enriched
                   IS DISTINCT FROM COALESCE(a.liste, '[]'::jsonb)
            """
        )
    )
    return curseur.rowcount


def ecrire_synopsis(curseur) -> int:
    """MS d'abord, Kitsu si MS est vide. La règle d'origine, qui n'a jamais été
    fautive — seule sa fraîcheur l'était."""
    curseur.execute(
        """
        UPDATE manga.ms_series_enriched s
           SET series_synopsis_enriched = COALESCE(
                   NULLIF(btrim(s.series_synopsis), ''),
                   NULLIF(btrim(s.kitsu_synopsis_clean), ''))
         WHERE s.series_synopsis_enriched IS DISTINCT FROM COALESCE(
                   NULLIF(btrim(s.series_synopsis), ''),
                   NULLIF(btrim(s.kitsu_synopsis_clean), ''))
        """
    )
    return curseur.rowcount


def repartition_sources(curseur) -> dict[str, int]:
    """Le témoin de la correction : la dérivée contient-elle enfin du MS ?"""
    curseur.execute(
        """
        WITH brut AS (
            SELECT s.series_id, 'ms' AS source, v AS libelle
              FROM manga.ms_series_enriched s,
                   LATERAL jsonb_array_elements_text(
                       COALESCE(s.series_genres, '[]'::jsonb)) v
            UNION ALL
            SELECT s.series_id, 'kitsu', v
              FROM manga.ms_series_enriched s,
                   LATERAL jsonb_array_elements_text(
                       COALESCE(s.kitsu_genres_json, '[]'::jsonb)) v
        ),
        direct AS (
            SELECT DISTINCT b.series_id, b.source, m.code
              FROM brut b
              JOIN manga.genre_mapping m
                ON m.source = b.source AND m.libelle_brut = b.libelle
             WHERE m.statut = 'mappe'
        )
        SELECT count(DISTINCT series_id) FILTER (WHERE source = 'ms'),
               count(DISTINCT series_id) FILTER (WHERE source = 'kitsu'),
               count(*) FILTER (WHERE source = 'ms'),
               count(*) FILTER (WHERE source = 'kitsu')
          FROM direct
        """
    )
    ligne = curseur.fetchone()
    return {
        "series_ms": ligne[0],
        "series_kitsu": ligne[1],
        "codes_ms": ligne[2],
        "codes_kitsu": ligne[3],
    }


@app.command()
def enrichir(
    # noqa: B008 — `typer.Option()` en défaut est l'API de Typer, pas un oubli.
    colonne: str = typer.Option(  # noqa: B008
        "toutes", help=f"Colonne à recalculer : {' | '.join(COLONNES)} | toutes."
    ),
    dry_run: bool = typer.Option(  # noqa: B008
        False, help="Mesure et affiche le delta, puis ROLLBACK : n'écrit rien."
    ),
) -> None:
    """Recalcule les colonnes dérivées depuis les sources et le référentiel."""
    if colonne not in (*COLONNES, "toutes"):
        raise ErreurEnrichissement(
            f"--colonne « {colonne} » inconnue. Attendu : "
            f"{' | '.join(COLONNES)} | toutes."
        )
    debut = time.monotonic()

    with psycopg.connect(dsn()) as connexion:
        with connexion.cursor() as curseur:
            verifier_prerequis(curseur)

            fautifs = libelles_non_couverts(curseur)
            if fautifs:
                detail = "\n".join(
                    f"    {source:>5} · {libelle!r} — {n} occurrences"
                    for source, libelle, n in fautifs[:20]
                )
                reste = (
                    f"\n    … et {len(fautifs) - 20} autres"
                    if len(fautifs) > 20
                    else ""
                )
                raise ErreurEnrichissement(
                    f"{len(fautifs)} libellé(s) absent(s) de manga.genre_mapping.\n"
                    "Le recalcul est ABANDONNÉ : sans correspondance, ces "
                    "libellés disparaîtraient de la colonne dérivée sans que "
                    "rien ne le signale.\n"
                    f"{detail}{reste}\n"
                    "  → compléter database/donnees/genre_mapping.csv, puis "
                    "recharger avec identity.charger_genres."
                )

            if colonne in ("genres", "toutes"):
                avant = couverture_actuelle(curseur)

                # CONTRÔLE DE NON-RÉGRESSION SOURCE -> DÉRIVÉE.
                #
                # C'est le contrôle qui manquait, et son absence est toute
                # l'histoire de 4a : en juillet, les sources décrivaient 12 652
                # séries pendant que la dérivée n'en couvrait que 4 819 — un
                # ratio de 38 %, visible par quiconque l'aurait mesuré. Personne
                # ne le mesurait.
                #
                # Deux mesures, deux rôles, et il ne faut pas les confondre :
                #   - `regressions` est BLOQUANTE et son seuil est zéro. Une
                #     série couverte aujourd'hui qui cesserait de l'être arrête
                #     le recalcul. C'est un invariant, pas un réglage.
                #   - le ratio source -> dérivée est RAPPORTÉ, pas imposé. Il
                #     ne peut pas atteindre 100 % par construction : les séries
                #     dont tous les libellés sont `exclu` ou `inconnu` sont
                #     décrites par une source sans produire de code. Le seuil
                #     d'alerte proposé est 95 % (l'état de juillet, 38 %,
                #     l'aurait franchi de très loin) ; le poser en échec
                #     demande d'abord de stabiliser les 22 `inconnu`.
                source = couverture_source(curseur)
                perdues = regressions(curseur)
                if perdues:
                    raise ErreurEnrichissement(
                        f"{perdues} série(s) couvertes aujourd'hui ne le "
                        "seraient plus après recalcul. Un recalcul ajoute, il "
                        "ne retire pas — abandon."
                    )

                mesure = mesurer_genres(curseur)
                sources = repartition_sources(curseur)
                ratio = 100 * mesure["series_couvertes"] / max(source, 1)
                typer.echo("→ genres")
                typer.echo(
                    f"  couverture : {avant} → {mesure['series_couvertes']} séries "
                    f"({mesure['series_couvertes'] - avant:+d})"
                )
                typer.echo(
                    f"  vs sources : {mesure['series_couvertes']} / {source} "
                    f"décrites par au moins une source = {ratio:.1f} %"
                    + (
                        f" — {source - mesure['series_couvertes']} séries "
                        "n'ont que des libellés exclus ou non tranchés"
                        if source > mesure["series_couvertes"]
                        else ""
                    )
                )
                typer.echo(
                    f"  codes      : {mesure['codes_total']} au total — "
                    f"{mesure['codes_des_sources']} issus des sources, "
                    f"{mesure['codes_derives']} ajoutés par la hiérarchie"
                )
                typer.echo(
                    f"  sources    : MS {sources['series_ms']} séries / "
                    f"{sources['codes_ms']} codes · Kitsu "
                    f"{sources['series_kitsu']} séries / "
                    f"{sources['codes_kitsu']} codes"
                )

                inconnus = occurrences_inconnues(curseur)
                total = sum(n for _, _, n in inconnus)
                typer.echo(
                    f"  inconnu    : {total} occurrences sur "
                    f"{len(inconnus)} libellés non tranchés"
                )
                for source, libelle, n in inconnus[:5]:
                    typer.echo(f"               {source:>5} · {libelle!r} — {n}")
                if len(inconnus) > 5:
                    typer.echo(f"               … et {len(inconnus) - 5} autres")

                modifiees = ecrire_genres(curseur)
                typer.echo(f"  ✓ {modifiees} lignes modifiées")

            if colonne in ("synopsis", "toutes"):
                modifiees = ecrire_synopsis(curseur)
                curseur.execute(
                    "SELECT count(*) FROM manga.ms_series_enriched "
                    "WHERE COALESCE(series_synopsis_enriched, '') <> ''"
                )
                typer.echo(
                    f"→ synopsis\n  ✓ {modifiees} lignes modifiées, "
                    f"{curseur.fetchone()[0]} séries avec synopsis"
                )

        if dry_run:
            connexion.rollback()
            typer.echo("⚠ DRY-RUN : transaction annulée, aucune écriture en base.")
        else:
            connexion.commit()

    typer.echo(f"Terminé en {time.monotonic() - debut:.1f} s.")


def main() -> int:
    try:
        app()
    except ErreurEnrichissement as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
