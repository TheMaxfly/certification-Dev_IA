"""La santé : une ligne par contrôle ; chaque manque fait échouer le verdict."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest
from conftest import (
    CIBLES,
    DEPUIS,
    E3,
    E4,
    JAMAIS,
    JOBS,
    RELEVE_AVANT,
    SERVIES,
    Bouclage,
    Poste,
    cible_prometheus,
    cibles_sauf,
    sante_application,
)
from conftest import cibles as cibles_reponse

from livraison import cibles, deployer, sante
from livraison.outils import Outils

PRODUCTION = "production"


def verifier(
    o: Outils, nom: str = PRODUCTION, reference: str = E4
) -> list[sante.Controle]:
    cible = cibles.lire(nom)
    return sante.verifier(o, cible, sante.attendus(o, reference, cible))


def manques(controles: list[sante.Controle]) -> list[str]:
    return [f"{c.service} · {c.objet}" for c in controles if not c.tenu]


def test_la_production_saine_est_verte(o: Outils):
    c = verifier(o)
    assert manques(c) == [] and sante.verte(c)
    objets = {f"{x.service} · {x.objet}" for x in c}
    assert {
        "api · image servie",
        "catalogue · image servie",
        "application · image servie",
        "application · version",
        "application · cles_api",
        "catalogue · normalisation",
        "api · version_api",
        "api · encodage servi = encodage en service",
        "prometheus · cibles up",
    } <= objets
    assert any(x.objet.startswith("migrations en attente") for x in c)


@pytest.mark.parametrize(
    ("panne", "manque"),
    [
        (
            lambda p, b: p.en_marche["api-modele"].pop("catalogue"),
            "catalogue · image servie",
        ),
        (
            lambda p, b: p.en_marche["api-modele"].update(api="api-modele:1.4.0"),
            "api · image servie",
        ),
        (
            lambda p, b: b.reponses.update({"http://127.0.0.1:8020/sante": (0, "")}),
            "application · /sante",
        ),
        (
            lambda p, b: b.reponses.update(
                {
                    "http://127.0.0.1:8020/sante": (
                        200,
                        sante_application(version="0.9.0"),
                    )
                }
            ),
            "application · version",
        ),
        (
            lambda p, b: b.reponses.update(
                {
                    "http://127.0.0.1:8020/sante": (
                        200,
                        sante_application(api="indisponible"),
                    )
                }
            ),
            "application · api",
        ),
        (
            lambda p, b: b.reponses.update(
                {
                    "http://127.0.0.1:8020/sante": (
                        200,
                        sante_application(base="non_configuree"),
                    )
                }
            ),
            "application · base",
        ),
        (
            lambda p, b: b.reponses.update(
                {
                    "http://127.0.0.1:8020/sante": (
                        200,
                        sante_application(cles_api="une"),
                    )
                }
            ),
            "application · cles_api",
        ),
        (
            lambda p, b: b.reponses.update(
                {
                    "http://127.0.0.1:8020/sante": (
                        200,
                        sante_application(catalogue="indisponible"),
                    )
                }
            ),
            "application · catalogue",
        ),
        (
            lambda p, b: b.reponses.update(
                {
                    "http://127.0.0.1:8020/sante": (
                        200,
                        json.dumps(
                            {
                                "version": "0.9.1",
                                "api": "ok",
                                "base": "ok",
                                "catalogue": "ok",
                                "cles_api": "deux",
                            }
                        ),
                    )
                }
            ),
            "application · profil_actif",
        ),
        (
            lambda p, b: b.reponses.update(
                {
                    "http://127.0.0.1:8000/health": (
                        503,
                        json.dumps({"status": "degraded", "db": "error"}),
                    )
                }
            ),
            "catalogue · db",
        ),
        (
            lambda p, b: b.reponses.update(
                {
                    "http://127.0.0.1:8000/health": (
                        200,
                        json.dumps(
                            {"status": "ok", "db": "ok", "normalisation": "repli"}
                        ),
                    )
                }
            ),
            "catalogue · normalisation",
        ),
        (lambda p, b: p.api.update(statut="degrade"), "api · /v1/sante"),
        (lambda p, b: p.api.update(info=401), "api · /v1/info"),
        (lambda p, b: p.api.update(version_api="1.4.0"), "api · version_api"),
        (
            lambda p, b: p.api.update(encodage_id=2),
            "api · encodage servi = encodage en service",
        ),
        (
            lambda p, b: setattr(p, "encodage", ""),
            "api · encodage servi = encodage en service",
        ),
        (
            lambda p, b: setattr(p, "migrations", (27, 3)),
            "base · migrations en attente (apimanga)",
        ),
        (
            lambda p, b: b.reponses.update(
                {
                    "http://127.0.0.1:9090/api/v1/targets": (
                        200,
                        json.dumps(
                            {
                                "data": {
                                    "activeTargets": [
                                        {"labels": {"job": "api"}, "health": "down"}
                                    ]
                                }
                            }
                        ),
                    )
                }
            ),
            "prometheus · cibles up",
        ),
        (
            lambda p, b: b.reponses.update(
                {"http://127.0.0.1:9090/api/v1/targets": (0, "")}
            ),
            "prometheus · cibles up",
        ),
    ],
)
def test_chaque_manque_fait_echouer(
    o: Outils, poste: Poste, bouclage: Bouclage, panne, manque
):
    panne(poste, bouclage)
    c = verifier(o)
    assert manque in manques(c)
    assert not sante.verte(c)
    assert any(ligne.startswith("MANQUE") for ligne in sante.lignes_verdict(c))


def test_une_api_illisible_dans_son_conteneur(o: Outils, poste: Poste):
    poste.api = {}
    assert "api · version_api" in manques(verifier(o))


def test_migrate_illisible_est_un_manque(o: Outils, poste: Poste, monkeypatch):
    def illisible(c):
        if "migrate.py" in c.argv:
            from livraison.outils import Sortie

            return Sortie(1, "", "erreur")
        return Poste.__call__(poste, c)

    o.lanceur = illisible
    assert "base · migrations en attente" in manques(verifier(o))


def test_le_staging_admet_une_seule_cle(o: Outils, poste: Poste, bouclage: Bouclage):
    poste.en_marche = {"senpai-staging": dict(SERVIES[E4])}
    bouclage.reponses = {
        k.replace(":8020/", ":8021/").replace(":8000/", ":8001/"): v
        for k, v in bouclage.reponses.items()
        if "9090" not in k
    }
    bouclage.reponses["http://127.0.0.1:8021/sante"] = (
        200,
        sante_application(cles_api="une"),
    )
    c = verifier(o, "staging")
    assert manques(c) == []
    assert not [x for x in c if x.service == "prometheus"]


def test_aux_versions_d_une_etiquette_anterieure(
    o: Outils, poste: Poste, bouclage: Bouclage
):
    """chaine-livraison : API 1.4.0, application 0.1.0, ni catalogue ni comptes."""
    poste.en_marche = {"api-modele": dict(SERVIES[E3])}
    poste.api.update(version_api="1.4.0")
    bouclage.reponses["http://127.0.0.1:8020/sante"] = (
        200,
        json.dumps({"application": "ok", "api": "ok", "version": "0.1.0"}),
    )
    c = verifier(o, reference=E3)
    assert manques(c) == []
    objets = {f"{x.service} · {x.objet}" for x in c}
    assert "application · cles_api" not in objets
    assert not [x for x in c if x.service == "catalogue"]
    # La version 0.9.1 servie à la place de 0.1.0 est un échec.
    bouclage.reponses["http://127.0.0.1:8020/sante"] = (200, sante_application())
    assert "application · version" in manques(verifier(o, reference=E3))


def test_l_api_est_attendue_trois_fois(o: Outils, bouclage: Bouclage):
    bouclage.reponses["http://127.0.0.1:8020/sante"] = (0, "")
    verifier(o)
    assert bouclage.demandes.count("http://127.0.0.1:8020/sante") == sante.ESSAIS


def test_le_programme_de_l_api_ne_rend_jamais_la_cle():
    """Le programme lu dans le conteneur n'imprime que quatre champs de /v1/info."""
    imprime = sante.PROGRAMME_API.split("print(")[1]
    assert "cles" not in imprime and "API_MODELE_CLES" not in imprime
    assert deployer.PROGRAMME_METRIQUES.count("API_MODELE_CLES") == 0


def test_lire_json_tolere_un_corps_illisible(o: Outils, bouclage: Bouclage):
    bouclage.reponses["http://127.0.0.1:1/x"] = (200, "{pas du json")
    assert sante.lire_json(o, "http://127.0.0.1:1/x") == (200, {})


def test_docker_qui_ne_repond_pas(o: Outils):
    from livraison.codes import Arret
    from livraison.outils import Sortie

    o.lanceur = lambda c: Sortie(1, "", "docker absent")
    with pytest.raises(Arret, match="docker ne répond pas"):
        sante.servies(o, "api-modele")


# --------------------------------------------------------------------------- #
#  Écart 25 (0.1.2) : Prometheus jugé sur un relevé postérieur au lever
# --------------------------------------------------------------------------- #


def prometheus(
    o: Outils, depuis: datetime | None = DEPUIS, reference: str = E4
) -> tuple[sante.Controle, list[str]]:
    """Le contrôle « cibles up » d'une vérification, et les notes qu'elle laisse."""
    cible = cibles.lire(PRODUCTION)
    notes: list[str] = []
    c = sante.verifier(o, cible, sante.attendus(o, reference, cible), depuis, notes)
    (p,) = [x for x in c if x.service == "prometheus"]
    return p, notes


def lectures(bouclage: Bouclage) -> int:
    return bouclage.demandes.count(CIBLES)


def test_25a_des_cibles_down_avant_le_lever_puis_up_au_releve_suivant(
    o: Outils, bouclage: Bouclage
):
    bouclage.reponses[CIBLES] = [
        cibles_sauf(
            api=cible_prometheus("api", "down", RELEVE_AVANT),
            application=cible_prometheus("application", "down", RELEVE_AVANT),
        ),
        cibles_reponse(),
    ]
    p, notes = prometheus(o)
    assert p.tenu and p.constate == "5/5 up"
    assert lectures(bouclage) == 2
    (note,) = notes
    assert re.fullmatch(
        r"Prometheus : \d+ s d'attente d'un relevé postérieur au lever"
        r" \(borne 30 s, cibles de Prometheus\)",
        note,
    )


def test_25b_une_cible_down_sur_un_releve_posterieur_est_un_manque(
    o: Outils, bouclage: Bouclage
):
    bouclage.reponses[CIBLES] = [
        cibles_sauf(api=cible_prometheus("api", "down")),
        cibles_reponse(),
    ]
    p, _ = prometheus(o)
    assert not p.tenu and p.constate == "4/5 up (api:down)"
    assert lectures(bouclage) == 1


def test_25c_une_cible_jamais_relevee_apres_le_lever_jusqu_a_la_borne(
    o: Outils, bouclage: Bouclage
):
    bouclage.reponses[CIBLES] = cibles_sauf(
        application=cible_prometheus("application", "up", RELEVE_AVANT)
    )
    p, notes = prometheus(o)
    assert not p.tenu
    assert p.constate == f"4/5 up (application:{sante.NON_RELEVEE})"
    assert "non relevée depuis le lever" in p.constate and "down" not in p.constate
    assert lectures(bouclage) > 2
    assert re.match(r"Prometheus : (3\d) s d'attente", notes[0])


def test_25d_le_up_de_l_ancien_conteneur_ne_compte_pas(o: Outils, bouclage: Bouclage):
    bouclage.reponses[CIBLES] = [
        cibles_sauf(api=cible_prometheus("api", "up", RELEVE_AVANT)),
        cibles_sauf(api=cible_prometheus("api", "down")),
    ]
    p, _ = prometheus(o)
    assert not p.tenu and p.constate == "4/5 up (api:down)"
    assert lectures(bouclage) == 2


def test_25e_livrer_sante_seul_une_lecture_sans_attente(
    o: Outils, bouclage: Bouclage, ecran
):
    bouclage.reponses[CIBLES] = cibles_reponse(
        *[cible_prometheus(j, releve=RELEVE_AVANT) for j in JOBS]
    )
    p, notes = prometheus(o, depuis=None)
    assert p.tenu and notes == [] and lectures(bouclage) == 1
    bouclage.demandes.clear()
    assert deployer.verifier_sante(o, PRODUCTION) == 0
    assert lectures(bouclage) == 1


def test_25f_la_borne_est_le_plus_grand_intervalle_lu(o: Outils, bouclage: Bouclage):
    bouclage.reponses[CIBLES] = cibles_sauf(
        api=cible_prometheus("api", "up", RELEVE_AVANT, "1m", "10s"),
    )
    p, notes = prometheus(o)
    assert not p.tenu
    assert "(borne 75 s, cibles de Prometheus)" in notes[0]


def test_25f_sans_champs_la_borne_vient_de_la_configuration_a_l_etiquette(
    o: Outils, poste: Poste, bouclage: Bouclage
):
    sans = [cible_prometheus(j, intervalle=None, delai=None) for j in JOBS]
    sans[0] = cible_prometheus("api", "up", RELEVE_AVANT, None, None)
    bouclage.reponses[CIBLES] = cibles_reponse(*sans)
    poste.fichiers[E4][sante.CHEMIN_PROMETHEUS] = (
        "global:\n  scrape_interval: 15s\n  evaluation_interval: 15s\n"
    )
    p, notes = prometheus(o)
    assert not p.tenu
    # Aucun délai écrit : l'intervalle le borne (15 + 15 + la marge).
    assert f"(borne 35 s, {sante.CHEMIN_PROMETHEUS} à {E4})" in notes[0]
    poste.fichiers[E4][sante.CHEMIN_PROMETHEUS] = (
        "global:\n  scrape_interval: 15s\n  scrape_timeout: 5s\n"
    )
    _, notes = prometheus(o)
    assert "(borne 25 s," in notes[0]


def test_25f_sans_champs_ni_configuration_jugement_immediat_et_dit(
    o: Outils, bouclage: Bouclage
):
    bouclage.reponses[CIBLES] = cibles_reponse(
        *[cible_prometheus(j, "up", RELEVE_AVANT, None, None) for j in JOBS]
    )
    p, notes = prometheus(o)
    assert p.tenu and lectures(bouclage) == 1
    assert notes == [sante.PHRASE_SANS_BORNE]


def test_25g_les_heures_et_les_durees_de_prometheus():
    assert sante.heure("2026-11-12T22:41:20.450771275Z") == datetime(
        2026, 11, 12, 22, 41, 20, 450771, tzinfo=UTC
    )
    assert sante.heure(JAMAIS) is None
    assert sante.heure(None) is None and sante.heure("2026-11-12T22:41:20") is None
    assert sante.duree_s("15s") == 15.0 and sante.duree_s("1m30s") == 90.0
    assert sante.duree_s("500ms") == 0.5
    assert sante.duree_s(None) is None and sante.duree_s("15 s") is None


def test_25g_une_cible_jamais_relevee_se_lit_sans_erreur(o: Outils, bouclage: Bouclage):
    bouclage.reponses[CIBLES] = [
        cibles_sauf(qualite=cible_prometheus("qualite", "unknown", JAMAIS)),
        cibles_reponse(),
    ]
    (lue,) = [c for c in sante.lire_cibles(o) if c.job == "qualite"]
    assert lue.releve is None and lue.sante == "unknown"
    p, _ = prometheus(o)
    assert p.tenu and lectures(bouclage) == 2


def test_prometheus_muet_apres_le_lever_est_un_manque_sans_attente(
    o: Outils, bouclage: Bouclage
):
    bouclage.reponses[CIBLES] = (0, "")
    p, notes = prometheus(o)
    assert not p.tenu and p.constate == "0/0 up" and notes == []
