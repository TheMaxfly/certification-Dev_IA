"""Le catalogue, pour CONFIRMER une réponse écrite — jamais pour la chercher.

Trois voies, toutes par égalité stricte :

  titre   `normaliser(texte)` = une forme du catalogue (titre ou alias de
          `ms_formes`, ou le titre de `ms_series_enriched`) ;
  auteur  `normaliser(texte)` = `normaliser(dessinateur)` ou
          `normaliser(scenariste)` ;
  id      `series_id` présent au catalogue.

`normaliser` est celle de la cascade d'identité (`identity.wikidata_dump`),
source unique de toutes les colonnes `*_norm` : l'égalité a le même sens ici et
dans le référentiel. Elle retire casse, accents latins, ponctuation et article
initial. Ce n'est PAS une recherche : aucune distance, aucun trigramme, aucun
préfixe.

Pourquoi c'est une règle et pas une précaution. Un outil de vérification flou
serait lui-même un bras lexical ; toute réponse qu'il trouverait serait, par
construction, trouvable lexicalement, et les familles F2 et F4 pencheraient
d'avance vers ce bras. Une réponse que ces trois voies ne confirment pas met la
question de côté : on ne cherche pas autrement.

Ce module ne lit que `manga` — jamais `bench`, ni le corpus, ni un index.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import psycopg

from identity.wikidata_dump import normaliser

#: Tout ce que ce module lit. Le rapport de confirmation le cite.
TABLES_LUES = (
    "manga.ms_series_enriched",
    "manga.ms_formes",
    "manga.kitsu_formes",
    "manga.kitsu_staff",
    "manga.work_identity",
    "manga.wd_formes",
    "manga.wd_auteurs",
)


@dataclass(frozen=True)
class Serie:
    series_id: int
    titre: str
    dessinateur: str | None
    scenariste: str | None


class Catalogue:
    """Index en mémoire du catalogue : 14 670 séries, quelques dizaines de
    milliers de formes. Chargé une fois, interrogé par égalité."""

    def __init__(self, series: list[Serie], formes: list[tuple[int, str, str]]):
        self.series = {s.series_id: s for s in series}
        self._formes: dict[str, dict[int, str]] = defaultdict(dict)
        self._auteurs: dict[str, dict[int, str]] = defaultdict(dict)
        for s in series:
            self._formes[normaliser(s.titre or "")].setdefault(s.series_id, "title")
            for role, nom in (
                ("dessinateur", s.dessinateur),
                ("scenariste", s.scenariste),
            ):
                if nom:
                    self._auteurs[normaliser(nom)].setdefault(s.series_id, role)
        for series_id, forme_norm, forme_type in formes:
            if series_id in self.series:
                self._formes[forme_norm].setdefault(series_id, forme_type)
        self._formes.pop("", None)
        self._auteurs.pop("", None)

    @classmethod
    def charger(cls, cx: psycopg.Connection) -> Catalogue:
        series = [
            Serie(*ligne)
            for ligne in cx.execute(
                "SELECT series_id, series_title, series_dessinateur, series_scenariste"
                " FROM manga.ms_series_enriched ORDER BY series_id"
            )
        ]
        formes = cx.execute(
            "SELECT series_id, forme_norm, forme_type FROM manga.ms_formes"
            " ORDER BY series_id, forme_norm"
        ).fetchall()
        return cls(series, formes)

    def par_titre(self, texte: str) -> dict[int, str]:
        """series_id → type de forme (`title` ou `alias`) égale au titre écrit."""
        return dict(self._formes.get(normaliser(texte), {}))

    def par_auteur(self, texte: str) -> dict[int, str]:
        """series_id → rôle (`dessinateur` ou `scenariste`) de l'auteur écrit."""
        return dict(self._auteurs.get(normaliser(texte), {}))

    def par_id(self, texte: str) -> Serie | None:
        return self.series.get(int(texte)) if texte.strip().isdigit() else None


# --------------------------------------------------------------------------- #
#  Hors du catalogue : Kitsu et Wikidata, toujours par égalité de forme
# --------------------------------------------------------------------------- #


def kitsu_par_titre(cx: psycopg.Connection, texte: str) -> list[int]:
    return [
        k
        for (k,) in cx.execute(
            "SELECT DISTINCT kitsu_id FROM manga.kitsu_formes WHERE forme_norm = %s"
            " ORDER BY kitsu_id",
            (normaliser(texte),),
        )
    ]


def rattaches_au_catalogue(cx: psycopg.Connection, kitsu_ids: list[int]) -> list[int]:
    """Séries du catalogue auxquelles la cascade rattache ces œuvres Kitsu."""
    return [
        s
        for (s,) in cx.execute(
            "SELECT series_id FROM manga.work_identity"
            " WHERE kitsu_id = ANY(%s) AND series_id IS NOT NULL ORDER BY series_id",
            ([str(k) for k in kitsu_ids],),
        )
    ]


def presences_hors_catalogue(
    cx: psycopg.Connection, voie: str, texte: str
) -> list[str]:
    """Les référentiels hors catalogue où le titre ou l'auteur écrit existe."""
    norme = normaliser(texte)
    requetes = {
        "titre": (
            ("kitsu_formes", "SELECT 1 FROM manga.kitsu_formes WHERE forme_norm = %s"),
            ("wd_formes", "SELECT 1 FROM manga.wd_formes WHERE forme_norm = %s"),
        ),
        "auteur": (
            ("kitsu_staff", "SELECT 1 FROM manga.kitsu_staff WHERE personne_norm = %s"),
            ("wd_auteurs", "SELECT 1 FROM manga.wd_auteurs WHERE auteur_norm = %s"),
        ),
    }[voie]
    return [
        nom
        for nom, sql in requetes
        if cx.execute(sql + " LIMIT 1", (norme,)).fetchone() is not None
    ]
