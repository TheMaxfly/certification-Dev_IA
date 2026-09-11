"""Derivation du perimetre depuis la base, en lecture seule.

Les deux sources partent du meme endroit — le catalogue — et portent le meme
identifiant de serie. Seule change la colonne qui designe l'oeuvre chez la
source. La derivation est donc une fonction parametree, pas deux fonctions.

Aucune recherche par titre : le lien est un identifiant publie, jamais un
rapprochement approximatif.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class Serie:
    """Une serie du perimetre, portant deja sa cle catalogue.

    Le rattachement n'est pas un probleme a resoudre en aval : c'est la
    condition d'entree du perimetre.
    """

    series_id: int
    cle_source: str
    rang_popularite: int


@dataclass(frozen=True)
class Derivation:
    """Comment une source derive son perimetre du catalogue."""

    nom: str
    colonne_source: str
    definition: str
    sql: str


# Le tri porte `series_id` en second critere : 4 rangs de popularite sont en
# doublon dans le catalogue, et un parcours ordonne doit etre rejouable.
_TRI = "order by e.series_popularity_rank, w.series_id"

DERIVATIONS: dict[str, Derivation] = {
    "wikipedia_fr": Derivation(
        nom="wikipedia_fr",
        colonne_source="manga.wd_pivot.wiki_fr",
        definition=(
            "Series du catalogue appariees a un QID dont le sitelink frwiki "
            "porte un titre d'article non vide."
        ),
        sql=f"""
            select w.series_id, p.wiki_fr, e.series_popularity_rank
            from manga.work_identity w
            join manga.wd_pivot p on p.qid = w.wikidata_qid
            join manga.ms_series_enriched e on e.series_id = w.series_id
            where p.wiki_fr is not null and btrim(p.wiki_fr) <> ''
            {_TRI}
        """,
    ),
    "anilist": Derivation(
        nom="anilist",
        colonne_source=(
            "manga.work_identity.anilist_id UNION manga.kitsu_mappings"
            " (external_site='anilist/manga')"
        ),
        definition=(
            "Series du catalogue atteignant un identifiant AniList par l'un des "
            "deux ponts. Mesure du 2026-09-12 : le referentiel seul rend 8 051 "
            "series, le pont Kitsu 4 958, et leur union 8 441 — le referentiel "
            "n'est donc pas un sur-ensemble, 390 series ne sont atteintes que "
            "par kitsu_mappings. Prendre l'union, pas la colonne du referentiel."
        ),
        sql="""
            with ponts as (
                select w.series_id, w.anilist_id
                from manga.work_identity w
                where w.anilist_id is not null and btrim(w.anilist_id) <> ''
                union
                select e.series_id, m.external_id
                from manga.ms_series_enriched e
                join manga.kitsu_mappings m on m.kitsu_id = e.kitsu_id
                where m.external_site = 'anilist/manga'
                  and m.external_id is not null and btrim(m.external_id) <> ''
            ),
            un_par_serie as (
                select distinct on (p.series_id)
                       p.series_id, p.anilist_id, e.series_popularity_rank
                from ponts p
                join manga.ms_series_enriched e on e.series_id = p.series_id
                order by p.series_id, p.anilist_id
            )
            select w.series_id, w.anilist_id, w.series_popularity_rank
            from un_par_serie w
            order by w.series_popularity_rank, w.series_id
        """,
    ),
}


class PerimetreInexploitable(RuntimeError):
    """Une serie du perimetre n'a pas de cle exploitable — arret, pas de silence."""


def deriver(dsn: str, source: str) -> list[Serie]:
    """Rend le perimetre d'une source, ordonne par popularite decroissante.

    La connexion est ouverte en lecture seule : ce module ne modifie jamais la
    base. Toute serie sans cle source ou sans rang de popularite interrompt la
    derivation — un trou silencieux serait pire qu'un echec.
    """
    if source not in DERIVATIONS:
        raise KeyError(
            f"source inconnue : {source!r} (connues : {sorted(DERIVATIONS)})"
        )
    derivation = DERIVATIONS[source]

    with psycopg.connect(dsn) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(derivation.sql)
            lignes = cur.fetchall()

    return _valider(lignes, derivation)


def _valider(lignes: list[tuple], derivation: Derivation) -> list[Serie]:
    """Controle de rattachement complet : zero exception toleree."""
    series: list[Serie] = []
    sans_cle: list[int] = []
    sans_rang: list[int] = []

    for series_id, cle_source, rang in lignes:
        if series_id is None:
            raise PerimetreInexploitable(
                f"{derivation.nom} : ligne sans identifiant catalogue"
            )
        if cle_source is None or not str(cle_source).strip():
            sans_cle.append(series_id)
            continue
        if rang is None:
            sans_rang.append(series_id)
            continue
        series.append(
            Serie(
                series_id=int(series_id),
                cle_source=str(cle_source).strip(),
                rang_popularite=int(rang),
            )
        )

    if sans_cle:
        raise PerimetreInexploitable(
            f"{derivation.nom} : {len(sans_cle)} series sans cle source "
            f"({derivation.colonne_source}) — ex. {sans_cle[:5]}"
        )
    if sans_rang:
        raise PerimetreInexploitable(
            f"{derivation.nom} : {len(sans_rang)} series sans rang de popularite, "
            f"le parcours ordonne ne serait pas rejouable — ex. {sans_rang[:5]}"
        )
    return series
