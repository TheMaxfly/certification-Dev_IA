"""La sonde des dépendances (E5, lot 1, spec 04), sans attente réelle : `passe()` est
appelée directement, `tourner()` avec une attente injectée qui ne dort pas.

Le test de l'incident lui-même (une dépendance qui tombe ou revient, aucune requête)
est dans `test_sonde_incident.py`."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from conftest import ENVIRONNEMENT, HACHEUR_RAPIDE, Navigateur, journal_complet
from doublure_api import CLE_CATALOGUE_ATTENDUE, Doublure

from application.app import creer_application
from application.client_catalogue import ClientCatalogue
from application.config import (
    FICHIER_REGLAGES,
    ConfigurationInvalide,
    Reglages,
    charger,
)
from application.metriques import Metriques
from application.sonde import ETAT_ERREUR, ETAT_INCONNU, SondeDependances


def derniere_passe(metriques: Metriques) -> float | None:
    return metriques.registre.get_sample_value(
        "application_sonde_derniere_passe_timestamp"
    )


def evenements(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        dict(getattr(r, "champs", {}))
        for r in caplog.records
        if r.getMessage() == "dependance_etat"
    ]


# --------------------------------------------------------------------------- #
#  Une passe
# --------------------------------------------------------------------------- #


def test_une_passe_lit_tout_et_journalise_un_seul_evenement_par_changement(caplog):
    etats = iter(["ok", "ok", "indisponible", "indisponible", "ok"])
    metriques = Metriques()

    async def base() -> str:
        return next(etats)

    async def api() -> str:
        return "ok"

    sonde = SondeDependances({"base": base, "api": api}, 15.0, metriques)

    async def cinq_passes() -> None:
        for _ in range(5):
            await sonde.passe()

    with caplog.at_level(logging.INFO, logger="application"):
        asyncio.run(cinq_passes())

    assert sonde.passes == 5
    assert sonde.etats == {"base": "ok", "api": "ok"}
    # Base : inconnu → ok, ok → indisponible, indisponible → ok ; API : inconnu → ok.
    assert evenements(caplog) == [
        {"dependance": "base", "avant": ETAT_INCONNU, "etat": "ok"},
        {"dependance": "api", "avant": ETAT_INCONNU, "etat": "ok"},
        {"dependance": "base", "avant": "ok", "etat": "indisponible"},
        {"dependance": "base", "avant": "indisponible", "etat": "ok"},
    ]
    niveaux = [r.levelno for r in caplog.records if r.getMessage() == "dependance_etat"]
    assert niveaux[2] == logging.WARNING and niveaux[3] == logging.INFO


def test_les_lectures_d_une_passe_se_font_en_meme_temps():
    """Trois lectures de 0,2 s : la passe dure 0,2 s, pas 0,6 s."""
    en_cours = 0
    maximum = 0

    async def lente() -> str:
        nonlocal en_cours, maximum
        en_cours += 1
        maximum = max(maximum, en_cours)
        await asyncio.sleep(0.2)
        en_cours -= 1
        return "ok"

    sonde = SondeDependances(
        {"base": lente, "catalogue": lente, "api": lente}, 15.0, Metriques()
    )
    asyncio.run(sonde.passe())
    assert maximum == 3


def test_deux_passes_ne_se_chevauchent_jamais():
    """Deux appels simultanés de `passe()` : la seconde attend la fin de la première."""
    en_cours = 0
    maximum = 0

    async def lecture() -> str:
        nonlocal en_cours, maximum
        en_cours += 1
        maximum = max(maximum, en_cours)
        await asyncio.sleep(0.05)
        en_cours -= 1
        return "ok"

    sonde = SondeDependances({"api": lecture}, 15.0, Metriques())

    async def ensemble() -> None:
        await asyncio.gather(sonde.passe(), sonde.passe())

    asyncio.run(ensemble())
    assert maximum == 1
    assert sonde.passes == 2


def test_une_lecture_qui_leve_n_empeche_ni_les_autres_ni_la_passe(caplog):
    metriques = Metriques()
    lues: list[str] = []

    async def cassee() -> str:
        raise RuntimeError("panne interne")

    async def api() -> str:
        lues.append("api")
        metriques.api_sante_ok.set(1)
        return "ok"

    sonde = SondeDependances({"base": cassee, "api": api}, 15.0, metriques)

    async def deux_passes() -> None:
        await sonde.passe()
        await sonde.passe()

    with caplog.at_level(logging.INFO, logger="application"):
        asyncio.run(deux_passes())

    assert lues == ["api", "api"]
    assert sonde.passes == 2
    assert sonde.etats["base"] == ETAT_ERREUR
    # L'erreur au journal une fois, par son type, jamais son message.
    erreurs = [e for e in evenements(caplog) if e["dependance"] == "base"]
    assert erreurs == [
        {
            "dependance": "base",
            "avant": ETAT_INCONNU,
            "etat": ETAT_ERREUR,
            "erreur": "RuntimeError",
        }
    ]
    assert "panne interne" not in journal_complet(caplog)


def test_la_jauge_de_la_derniere_passe_avance():
    instants = iter([100.0, 115.0, 130.0])
    metriques = Metriques()

    async def api() -> str:
        return "ok"

    sonde = SondeDependances(
        {"api": api}, 15.0, metriques, horloge=lambda: next(instants)
    )
    assert derniere_passe(metriques) == 0

    async def jouer() -> list[float | None]:
        sonde.lancer()  # posée au lancement : 100
        vues = [derniere_passe(metriques)]
        await sonde.arreter()
        await sonde.passe()
        vues.append(derniere_passe(metriques))
        await sonde.passe()
        vues.append(derniere_passe(metriques))
        return vues

    assert asyncio.run(jouer()) == [100.0, 115.0, 130.0]


# --------------------------------------------------------------------------- #
#  La tâche
# --------------------------------------------------------------------------- #


class Arret(Exception):
    """Lancée par l'attente injectée pour finir la boucle d'un test."""


def test_la_tache_joue_une_passe_par_periode_et_survit_a_une_exception(caplog):
    attentes: list[float] = []
    temps = [0.0]

    async def attendre(secondes: float) -> None:
        if len(attentes) == 4:
            raise Arret
        attentes.append(secondes)

    appels = 0

    async def lecture() -> str:
        nonlocal appels
        appels += 1
        temps[0] += 4.0  # la passe dure 4 s
        return "ok"

    sonde = SondeDependances(
        {"api": lecture},
        15.0,
        Metriques(),
        monotone=lambda: temps[0],
        attendre=attendre,
    )

    async def casser_une_fois() -> None:
        await original()
        if sonde.passes == 2:
            raise RuntimeError("journal plein")

    original = sonde.passe
    sonde.passe = casser_une_fois  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO, logger="application"):
        with pytest.raises(Arret):
            asyncio.run(sonde.tourner())

    # Une période avant la première passe ; ensuite, la période moins la passe.
    assert attentes == [15.0, 11.0, 11.0, 11.0]
    assert appels == 4
    assert "sonde_passe_en_echec" in caplog.messages
    assert "journal plein" not in journal_complet(caplog)


def test_une_passe_plus_longue_que_la_periode_enchaine_sans_chevaucher():
    attentes: list[float] = []
    temps = [0.0]

    async def attendre(secondes: float) -> None:
        if len(attentes) == 3:
            raise Arret
        attentes.append(secondes)

    async def lecture() -> str:
        temps[0] += 20.0
        return "ok"

    sonde = SondeDependances(
        {"api": lecture},
        15.0,
        Metriques(),
        monotone=lambda: temps[0],
        attendre=attendre,
    )
    with pytest.raises(Arret):
        asyncio.run(sonde.tourner())
    assert attentes == [15.0, 0.0, 0.0]


def test_la_sonde_tourne_avec_l_application_et_s_arrete_avec_elle(
    doublure: Doublure, reglages: Reglages
):
    application = creer_application(
        reglages,
        transport=httpx.ASGITransport(app=doublure.app),
        hacheur=HACHEUR_RAPIDE,
    )
    sonde: SondeDependances = application.state.sonde
    assert sonde.tache is None
    with Navigateur(application, client=("127.0.0.1", 50000)):
        assert sonde.tache is not None and not sonde.tache.done()
        # Posée au lancement : rien n'a manqué au démarrage (l'alerte J ne part pas).
        assert derniere_passe(application.state.metriques) > 0
    assert sonde.tache.done()
    assert sonde.tache.cancelled()


def test_ce_qui_n_est_pas_configure_n_est_pas_lu(
    doublure: Doublure, reglages: Reglages
):
    transport = httpx.ASGITransport(app=doublure.app)
    seule = creer_application(reglages, transport=transport, hacheur=HACHEUR_RAPIDE)
    assert seule.state.sonde.dependances == ("api",)
    complete = creer_application(
        replace(
            reglages,
            base_dsn="postgresql://essai@127.0.0.1:1/apimanga_essai",
            catalogue=replace(reglages.catalogue, cle=CLE_CATALOGUE_ATTENDUE),
        ),
        transport=transport,
        hacheur=HACHEUR_RAPIDE,
    )
    assert complete.state.sonde.dependances == (
        "base",
        "cout_du_jour",
        "catalogue",
        "api",
    )


# --------------------------------------------------------------------------- #
#  La lecture du catalogue : `/health`, aucun appel compté, la clé refusée gardée
# --------------------------------------------------------------------------- #


def test_la_lecture_du_catalogue_ne_compte_aucun_appel_et_garde_la_cle_refusee(
    doublure: Doublure, reglages: Reglages
):
    metriques = Metriques()
    client = ClientCatalogue(
        reglages.catalogue.adresse,
        CLE_CATALOGUE_ATTENDUE,
        reglages.catalogue.delais,
        metriques,
        httpx.ASGITransport(app=doublure.app),
    )

    def etat() -> dict[str, float | None]:
        return {
            e: metriques.registre.get_sample_value(
                "application_catalogue_etat", {"etat": e}
            )
            for e in ("ok", "indisponible", "cle")
        }

    async def jouer() -> list[str]:
        vus = [await client.sonder()]
        doublure.choisir(catalogue="indisponible")
        vus.append(await client.sonder())
        doublure.choisir(catalogue="nominal")
        client.dernier_etat = "cle"  # un appel de données a vu la clé refusée
        vus.append(await client.sonder())
        await client.fermer()
        return vus

    assert asyncio.run(jouer()) == ["ok", "indisponible", "cle"]
    assert etat() == {"ok": 0, "indisponible": 0, "cle": 1}
    assert doublure.appels_catalogue.get("/health") == 3
    assert "/series" not in doublure.appels_catalogue
    assert (
        metriques.registre.get_sample_value(
            "application_catalogue_appels_total",
            {"route_catalogue": "series", "statut": "ok"},
        )
        or 0
    ) == 0


# --------------------------------------------------------------------------- #
#  La période : en configuration, avec sa règle
# --------------------------------------------------------------------------- #


def test_la_periode_vient_de_la_configuration(reglages: Reglages):
    assert reglages.sonde.periode_s == 15.0


@pytest.mark.parametrize("valeur", ["0", "-5", '"15"'])
def test_une_periode_fautive_empeche_de_demarrer(tmp_path: Path, valeur: str):
    texte = FICHIER_REGLAGES.read_text(encoding="utf-8").replace(
        "periode_s = 15.0", f"periode_s = {valeur}"
    )
    fichier = tmp_path / "reglages.toml"
    fichier.write_text(texte, encoding="utf-8")
    with pytest.raises(ConfigurationInvalide, match=r"\[sonde\.periode_s\]"):
        charger(ENVIRONNEMENT, fichier)


def test_la_periode_depasse_la_duree_d_une_passe(reglages: Reglages):
    """La règle avant le chiffre : une passe dure au plus la plus longue de ses
    lectures, chacune bornée par son délai — l'API (connexion + santé), le catalogue
    (connexion), la base (connexion + instruction). La période la dépasse ; mesurée
    serveur arrêté le 2026-11-13 : 5,0 s."""
    borne = max(
        reglages.delais.connexion_s + reglages.delais.sante_s,
        reglages.catalogue.delais.connexion_s,
        2 * reglages.comptes.delai_base_s,
    )
    assert borne == 10.0
    assert reglages.sonde.periode_s > borne
