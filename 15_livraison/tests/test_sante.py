"""La santé : une ligne par contrôle ; chaque manque fait échouer le verdict."""

from __future__ import annotations

import json

import pytest
from conftest import E3, E4, SERVIES, Bouclage, Poste, sante_application

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
