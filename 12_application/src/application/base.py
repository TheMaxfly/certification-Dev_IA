"""La base de l'application : le schéma `application`, sous le rôle `app_rw` seul.

- **Paresseuse** : rien n'est ouvert au démarrage ; une connexion par opération, une
  transaction courte, refermée aussitôt. Pas de pool : quelques requêtes par minute sur
  le poste, et une connexion de moins à tenir ouverte (le rôle a 10 connexions au plus).
- **Bornée** : le délai vient de la configuration — pour se connecter, pour une
  instruction (`statement_timeout`), pour attendre un verrou (`lock_timeout`).
- **Paramétrée** : les requêtes passent leurs valeurs à psycopg, jamais dans le texte.
- **Absente ou injoignable** : `BaseIndisponible` ; l'application démarre quand même et
  le conseiller fonctionne (E4, lot 1, décision 11). L'état est exposé à `/sante` et par
  la métrique `application_base_etat`, sans détail.

Le DSN ne porte jamais de mot de passe (`~/.pgpass` sur le poste, un fichier monté dans
un conteneur) ; il n'est ni journalisé ni montré.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import psycopg

from application import journal
from application.metriques import Metriques

#: Les états de la base vus d'ici, étiquettes bornées de la métrique.
ETATS_BASE = ("ok", "indisponible", "non_configuree")


class BaseIndisponible(Exception):
    """La base n'est pas configurée, ou ne répond pas : les comptes attendent."""


class Base:
    def __init__(self, dsn: str | None, delai_s: float, metriques: Metriques) -> None:
        self._dsn = dsn
        self._delai = max(1, round(delai_s))
        millisecondes = max(1, round(delai_s * 1000))
        self._options = (
            f"-c statement_timeout={millisecondes} -c lock_timeout={millisecondes}"
        )
        self._metriques = metriques
        self.etat = "inconnu"
        if dsn is None:
            self._noter("non_configuree")

    @property
    def configuree(self) -> bool:
        return self._dsn is not None

    def _noter(self, etat: str) -> None:
        self.etat = etat
        for valeur in ETATS_BASE:
            constate = 1 if valeur == etat else 0
            self._metriques.base_etat.labels(etat=valeur).set(constate)

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[psycopg.AsyncConnection]:
        """Une connexion, une transaction : validée en sortie, annulée sur erreur."""
        if self._dsn is None:
            raise BaseIndisponible("non_configuree")
        try:
            connexion = await psycopg.AsyncConnection.connect(
                self._dsn,
                connect_timeout=self._delai,
                application_name="application",
                options=self._options,
            )
        except psycopg.OperationalError:
            self._noter("indisponible")
            journal.evenement("base_indisponible", {"etape": "connexion"})
            raise BaseIndisponible("injoignable") from None
        try:
            async with connexion, connexion.transaction():
                yield connexion
        except (psycopg.OperationalError, psycopg.errors.QueryCanceled):
            # Connexion perdue, ou délai d'une instruction ou d'un verrou dépassé.
            self._noter("indisponible")
            journal.evenement("base_indisponible", {"etape": "requete"})
            raise BaseIndisponible("interrompue") from None
        self._noter("ok")

    async def sonder(self) -> str:
        """L'état, constaté par un `SELECT 1` : ok, indisponible, non_configuree."""
        try:
            async with self.transaction() as cx:
                await cx.execute("SELECT 1")
        except BaseIndisponible:
            pass
        return self.etat
