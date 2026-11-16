"""L'incident du 13 novembre (E5, lot 1, spec 04) : une dépendance tombe ou revient, et
**aucune requête** ne touche l'application — la jauge doit la suivre quand même.

Le défaut mesuré en staging : l'application ne relisait l'état d'une dépendance qu'au
passage d'une requête. Serveur PostgreSQL arrêté, aucune jauge n'a bougé pendant 4 min
08 s ; revenu, la jauge est restée à `indisponible` 4 min 15 s. Même constat pour le
catalogue, l'API du modèle et le coût du jour.

Ces tests sont écrits pour tourner **sur le code d'avant comme sur celui d'après** : ils
ne lisent que ce qui existait en 0.9.1 (les jauges, le registre, la configuration par
son fichier). La période de la sonde est réduite à 0,05 s par une copie du fichier de
réglages ; sur 0.9.1, la table `[sonde]` est ignorée et aucune passe ne vient : le test
attend son échéance, puis constate la jauge figée. Après le correctif, il attend deux
passes terminées (la jauge de la dernière passe), jamais une durée fixe.

La base « arrêtée » : un relais TCP du bouclage, entre l'application et la base
jetable, qui refuse les connexions quand il est coupé — la connexion refusée que le
serveur arrêté rendait le 13 novembre. Aucune requête HTTP n'est faite entre l'arrêt
et le constat.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
import pytest
from conftest import (
    ADRESSE_MEMOIRE,
    CLE_SESSION_ESSAI,
    ENVIRONNEMENT,
    HACHEUR_RAPIDE,
    Navigateur,
    bornes_larges,
)
from doublure_api import CLE_CATALOGUE_ATTENDUE, Doublure
from fastapi import FastAPI
from psycopg import conninfo

from application.app import creer_application
from application.config import FICHIER_REGLAGES, Reglages, charger

#: La période de l'essai : assez courte pour ne rien attendre, assez longue pour qu'une
#: passe (quelques millisecondes ici) s'y termine.
PERIODE_ESSAI_S = 0.05
#: L'échéance de l'attente : sans sonde (0.9.1), on l'atteint ; avec, deux passes
#: viennent bien avant.
ECHEANCE_S = 3.0
#: Un instant fixe pour le jour des coûts (UTC) : le test ne dépend pas de minuit.
INSTANT = datetime(2026, 11, 13, 12, 0, tzinfo=UTC).timestamp()
JOUR = datetime.fromtimestamp(INSTANT, UTC).date()


def reglages_a_periode(dossier: Path, periode_s: float) -> Reglages:
    """Les réglages du dépôt, la période de la sonde remplacée dans une copie du
    fichier — la même voie qu'en service."""
    texte = FICHIER_REGLAGES.read_text(encoding="utf-8")
    texte = re.sub(r"(?ms)^\[sonde\]\n.*?(?=^\[)", "", texte)
    texte += f'\n[sonde]\nperiode_s = {periode_s}\nmotif = "essai"\n'
    copie = dossier / "reglages.toml"
    copie.write_text(texte, encoding="utf-8")
    return bornes_larges(
        replace(charger(ENVIRONNEMENT, copie), adresse_api=ADRESSE_MEMOIRE)
    )


def valeur(app: FastAPI, nom: str, **etiquettes: str) -> float | None:
    return app.state.metriques.registre.get_sample_value(nom, etiquettes)


def etat_constate(app: FastAPI, jauge: str, etats: tuple[str, ...]) -> str | None:
    """L'état qui vaut 1 sur la jauge ; None : aucun (jamais lue)."""
    for etat in etats:
        if valeur(app, jauge, etat=etat) == 1:
            return etat
    return None


def attendre_deux_passes(app: FastAPI) -> int:
    """Deux passes terminées après l'appel : la seconde a commencé après lui. Rend le
    nombre de passes vues ; sans sonde, 0 à l'échéance."""
    nom = "application_sonde_derniere_passe_timestamp"
    debut = time.monotonic()
    vues = 0
    derniere = valeur(app, nom)
    while vues < 2 and time.monotonic() - debut < ECHEANCE_S:
        time.sleep(0.005)
        lue = valeur(app, nom)
        if lue is not None and lue != derniere:
            vues += 1
            derniere = lue
    return vues


class Relais:
    """Un relais TCP du bouclage vers la base jetable : coupé, le port ne répond plus
    (connexion refusée) ; rétabli, sur le même port, il relaie de nouveau."""

    def __init__(self, hote: str, port: int) -> None:
        self._cible = (hote, port)
        self._verrou = threading.Lock()
        self._ouvertes: list[socket.socket] = []
        self._ecoute: socket.socket | None = None
        self.port = 0
        self.retablir()

    def retablir(self) -> None:
        ecoute = socket.socket()
        ecoute.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ecoute.bind(("127.0.0.1", self.port))
        ecoute.listen(16)
        ecoute.settimeout(0.02)
        self.port = ecoute.getsockname()[1]
        self._ecoute = ecoute
        threading.Thread(target=self._accepter, args=(ecoute,), daemon=True).start()

    def couper(self) -> None:
        ecoute, self._ecoute = self._ecoute, None
        if ecoute is not None:
            ecoute.close()
        with self._verrou:
            for prise in self._ouvertes:
                prise.close()
            self._ouvertes.clear()

    def _accepter(self, ecoute: socket.socket) -> None:
        while self._ecoute is ecoute:
            try:
                client, _ = ecoute.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                amont = socket.create_connection(self._cible, timeout=5)
            except OSError:
                client.close()
                continue
            client.settimeout(None)
            amont.settimeout(None)
            with self._verrou:
                self._ouvertes += [client, amont]
            for source, puits in ((client, amont), (amont, client)):
                threading.Thread(
                    target=self._pomper, args=(source, puits), daemon=True
                ).start()

    @staticmethod
    def _pomper(source: socket.socket, puits: socket.socket) -> None:
        try:
            while donnees := source.recv(65536):
                puits.sendall(donnees)
        except OSError:
            pass
        finally:
            for prise in (source, puits):
                try:
                    prise.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


@pytest.fixture
def relais(base_app: str) -> Iterator[tuple[Relais, str]]:
    """La base du test derrière un relais ; le DSN de l'application vise le relais."""
    parametres = conninfo.conninfo_to_dict(base_app)
    hote = str(parametres.get("host") or "127.0.0.1")
    relais = Relais(hote, int(str(parametres["port"])))
    parametres.update(host="127.0.0.1", port=str(relais.port))
    yield relais, conninfo.make_conninfo(**parametres)
    relais.couper()


def demarrer(
    reglages: Reglages, doublure: Doublure, **valeurs: object
) -> tuple[FastAPI, Navigateur]:
    application = creer_application(
        replace(reglages, cle_session=CLE_SESSION_ESSAI, **valeurs),  # type: ignore[arg-type]
        transport=httpx.ASGITransport(app=doublure.app),
        hacheur=HACHEUR_RAPIDE,
        horloge=lambda: INSTANT,
    )
    return application, Navigateur(application, client=("127.0.0.1", 50000))


def test_la_base_arretee_puis_revenue_se_voit_sans_requete(
    tmp_path: Path, doublure: Doublure, relais: tuple[Relais, str]
):
    coupe, dsn = relais
    reglages = reglages_a_periode(tmp_path, PERIODE_ESSAI_S)
    application, client = demarrer(reglages, doublure, base_dsn=dsn)
    etats = ("ok", "indisponible", "non_configuree")
    with client:
        # Le point de départ commun aux deux versions : une page lue, la base aussi.
        assert client.get("/").status_code == 200
        assert etat_constate(application, "application_base_etat", etats) == "ok"

        coupe.couper()  # la base « arrêtée » ; plus aucune requête
        passes = attendre_deux_passes(application)
        constate = etat_constate(application, "application_base_etat", etats)
        assert constate == "indisponible", (
            f"base arrêtée, aucune requête : la jauge dit {constate!r} après"
            f" {passes} passe(s) de la sonde ({ECHEANCE_S} s au plus), attendu"
            " 'indisponible' à la première passe"
        )

        coupe.retablir()  # la base « revenue » ; toujours aucune requête
        passes = attendre_deux_passes(application)
        constate = etat_constate(application, "application_base_etat", etats)
        assert constate == "ok", (
            f"base revenue, aucune requête : la jauge dit {constate!r} après"
            f" {passes} passe(s), attendu 'ok'"
        )


def test_le_catalogue_arrete_puis_revenu_se_voit_sans_requete(
    tmp_path: Path, doublure: Doublure
):
    reglages = reglages_a_periode(tmp_path, PERIODE_ESSAI_S)
    catalogue = replace(reglages.catalogue, cle=CLE_CATALOGUE_ATTENDUE)
    application, client = demarrer(reglages, doublure, catalogue=catalogue)
    etats = ("ok", "indisponible", "non_configure", "cle")
    with client:
        assert client.get("/catalogue", params={"q": "essai"}).status_code == 200
        assert etat_constate(application, "application_catalogue_etat", etats) == "ok"

        doublure.choisir(catalogue="indisponible")  # sa base ne répond plus
        passes = attendre_deux_passes(application)
        constate = etat_constate(application, "application_catalogue_etat", etats)
        assert constate == "indisponible", (
            f"catalogue arrêté, aucune requête : la jauge dit {constate!r} après"
            f" {passes} passe(s), attendu 'indisponible'"
        )

        doublure.choisir(catalogue="nominal")
        passes = attendre_deux_passes(application)
        constate = etat_constate(application, "application_catalogue_etat", etats)
        assert constate == "ok", (
            f"catalogue revenu, aucune requête : la jauge dit {constate!r} après"
            f" {passes} passe(s), attendu 'ok'"
        )


def test_l_api_degradee_puis_revenue_se_voit_sans_requete(
    tmp_path: Path, doublure: Doublure
):
    reglages = reglages_a_periode(tmp_path, PERIODE_ESSAI_S)
    application, client = demarrer(reglages, doublure)
    with client:
        # Lue au démarrage, sur les deux versions.
        assert valeur(application, "application_api_sante_ok") == 1

        doublure.choisir(sante="degrade")  # `/v1/sante` en 503 : la base de l'API
        passes = attendre_deux_passes(application)
        lue = valeur(application, "application_api_sante_ok")
        assert lue == 0, (
            f"API dégradée, aucune requête : application_api_sante_ok vaut {lue}"
            f" après {passes} passe(s), attendu 0"
        )

        doublure.choisir(sante="ok")
        passes = attendre_deux_passes(application)
        lue = valeur(application, "application_api_sante_ok")
        assert lue == 1, (
            f"API revenue, aucune requête : application_api_sante_ok vaut {lue}"
            f" après {passes} passe(s), attendu 1"
        )


def test_le_cout_du_jour_suit_la_table_sans_requete(
    tmp_path: Path, doublure: Doublure, base: str, base_app: str
):
    reglages = reglages_a_periode(tmp_path, PERIODE_ESSAI_S)
    application, client = demarrer(reglages, doublure, base_dsn=base_app)
    etats = ("ok", "atteint", "inconnu")
    with client, psycopg.connect(base, autocommit=True) as cx:
        # Deux lignes du jour, comme deux profils : 0,42 $ en tout, sous le plafond.
        for profil, cout in (("profil-essai-a", "0.30"), ("profil-essai-b", "0.12")):
            cx.execute(
                "INSERT INTO application.couts_jour (jour, profil, mode, appels,"
                " cout_usd) VALUES (%s, %s, 'strict', 1, %s)",
                (JOUR, profil, cout),
            )
        passes = attendre_deux_passes(application)
        lu = valeur(application, "application_cout_jour_usd")
        assert lu == pytest.approx(0.42), (
            f"coût du jour posé en base, aucune question : la jauge vaut {lu} après"
            f" {passes} passe(s), attendu 0,42"
        )
        assert etat_constate(application, "application_plafond_cout_etat", etats) == (
            "ok"
        )

        # La remise à zéro de la panne D (13 novembre) : la jauge était restée 51 min.
        cx.execute("DELETE FROM application.couts_jour WHERE jour = %s", (JOUR,))
        passes = attendre_deux_passes(application)
        lu = valeur(application, "application_cout_jour_usd")
        assert lu == 0, (
            f"coût du jour remis à zéro, aucune question : la jauge vaut {lu} après"
            f" {passes} passe(s), attendu 0"
        )
