"""Déployer, arrêter, l'état : une cible à la fois, jamais de migration, toujours un
journal."""

from __future__ import annotations

import re

import pytest
from conftest import (
    CIBLES,
    E4,
    RELEVE_AVANT,
    SERVIES,
    Bouclage,
    Poste,
    cible_prometheus,
    cibles,
    cibles_sauf,
    sante_application,
)

from livraison import deployer
from livraison.codes import Arret, Code
from livraison.outils import Outils


def composes(poste: Poste) -> list[tuple[str, ...]]:
    return [a for a in poste.argvs() if a[:2] == ("docker", "compose")]


def journaux(o: Outils) -> list[str]:
    return sorted(p.name for p in o.journal.glob("2*.md")) if o.journal.exists() else []


def jamais_migre(poste: Poste) -> None:
    for a in poste.argvs():
        if "migrate.py" in a:
            assert a[-1] == "status"


def test_deployer_la_production(o: Outils, poste: Poste, ecran):
    assert deployer.deployer(o, "production") == Code.OK
    (up,) = composes(poste)
    assert up[-4:] == ("up", "-d", "--no-build", "--remove-orphans")
    assert up[up.index("-p") + 1] == "api-modele"
    assert "13_monitorage/compose.yaml" in " ".join(up)
    (nom,) = journaux(o)
    assert nom == "20261111T100000Z_production_e4-livraison.md"
    texte = (o.journal / nom).read_text(encoding="utf-8")
    assert "**santé verte**" in texte
    assert "sha256:" + "3" * 64 in texte
    assert "4 appels, 0.012300 $" in texte
    assert "30 appliquées, 0 en attente (apimanga)" in texte
    assert "La cible tournait" in texte
    assert any("santé verte" in ligne for ligne in ecran)
    jamais_migre(poste)


def test_l_autre_cible_en_marche_est_un_refus(o: Outils, poste: Poste):
    """Décision 3 : la production tourne, le staging est refusé — et inversement."""
    o.environ = {"PREPROD_PGPASS_CONTENEUR": str(o.racine / "pgpass")}
    (o.racine / "pgpass").write_text("", encoding="utf-8")
    (o.racine / "12_application/.env.staging").write_text("", encoding="utf-8")
    with pytest.raises(Arret, match="une cible à la fois sur le poste") as e:
        deployer.deployer(o, "staging")
    assert e.value.code == Code.PREREQUIS
    assert "livrer arreter --cible production" in e.value.motif
    poste.en_marche = {"senpai-staging": dict(SERVIES[E4])}
    with pytest.raises(Arret, match="la cible staging tourne"):
        deployer.deployer(o, "production")
    assert composes(poste) == [] and journaux(o) == []


def test_le_staging_exige_sa_cle_de_session_et_le_pgpass_de_la_preproduction(
    o: Outils, poste: Poste
):
    poste.en_marche = {}
    with pytest.raises(Arret, match=r"\.env\.staging"):
        deployer.deployer(o, "staging")
    (o.racine / "12_application/.env.staging").write_text("", encoding="utf-8")
    with pytest.raises(Arret, match="PREPROD_PGPASS_CONTENEUR"):
        deployer.deployer(o, "staging")
    o.environ = {"PREPROD_PGPASS_CONTENEUR": str(o.racine / "absent")}
    with pytest.raises(Arret, match="PREPROD_PGPASS_CONTENEUR"):
        deployer.deployer(o, "staging")
    assert composes(poste) == []


def test_le_staging_se_deploie(o: Outils, poste: Poste, bouclage: Bouclage):
    poste.en_marche = {}
    (o.racine / "pgpass").write_text("", encoding="utf-8")
    (o.racine / "12_application/.env.staging").write_text("", encoding="utf-8")
    o.environ = {"PREPROD_PGPASS_CONTENEUR": str(o.racine / "pgpass")}
    bouclage.reponses = {
        "http://127.0.0.1:8021/sante": (200, sante_application(cles_api="une")),
        "http://127.0.0.1:8001/health": bouclage.reponses[
            "http://127.0.0.1:8000/health"
        ],
    }
    assert deployer.deployer(o, "staging") == Code.OK
    (up,) = composes(poste)
    fichiers = [up[i + 1] for i, x in enumerate(up) if x == "--env-file"]
    assert fichiers[-2].endswith("12_application/.env.staging")
    assert fichiers[-1].endswith("cibles/staging.env")
    assert "13_monitorage" not in " ".join(up)
    assert poste.appels[[c.argv for c in poste.appels].index(up)].env == {
        "API_MODELE_PGPASS": str(o.racine / "pgpass")
    }
    assert "non relevé (pile arrêtée)" in (o.journal / journaux(o)[0]).read_text(
        encoding="utf-8"
    )


def test_un_fichier_d_environnement_absent(o: Outils):
    (o.racine / "02_api_manga/.env").unlink()
    with pytest.raises(Arret, match="02_api_manga/.env"):
        deployer.deployer(o, "production")


def test_images_absentes(o: Outils, poste: Poste):
    del poste.images["application-conseiller:0.9.1"]
    with pytest.raises(Arret, match="livrer construire"):
        deployer.deployer(o, "production")


def test_des_migrations_en_attente_sont_un_refus_jamais_une_migration(
    o: Outils, poste: Poste
):
    poste.migrations = (27, 3)
    with pytest.raises(Arret, match="migrations en attente sur apimanga : 3") as e:
        deployer.deployer(o, "production")
    assert "livrer ne migre jamais" in e.value.motif
    assert composes(poste) == []
    jamais_migre(poste)


def test_une_sante_rouge_laisse_la_pile_et_le_dit(
    o: Outils, poste: Poste, bouclage: Bouclage, ecran
):
    bouclage.reponses["http://127.0.0.1:8020/sante"] = (
        200,
        sante_application(cles_api="une"),
    )
    assert deployer.deployer(o, "production") == Code.SANTE_ROUGE
    assert not [a for a in composes(poste) if "down" in a]
    assert poste.en_marche.get("api-modele")
    texte = (o.journal / journaux(o)[0]).read_text(encoding="utf-8")
    assert "**santé rouge**" in texte and "**manque**" in texte
    assert deployer.PHRASE_RETOUR in texte
    assert ecran[-1] == deployer.PHRASE_RETOUR


def test_une_pile_qui_ne_se_leve_pas_est_journalisee(o: Outils, poste: Poste):
    poste.compose_code = 1
    assert deployer.deployer(o, "production") == Code.DEPLOIEMENT
    texte = (o.journal / journaux(o)[0]).read_text(encoding="utf-8")
    assert "échec : compose.production.up" in texte


def test_une_pile_qui_ne_repond_pas_dans_le_delai(o: Outils, bouclage: Bouclage):
    bouclage.reponses.pop("http://127.0.0.1:8020/sante")
    assert deployer.deployer(o, "production") == Code.DEPLOIEMENT
    texte = (o.journal / journaux(o)[0]).read_text(encoding="utf-8")
    assert "ne répond pas après 180 s" in texte


def test_a_blanc_rien_ne_se_leve(o: Outils, poste: Poste, ecran):
    poste.branche, poste.head = "feature/livraison", None
    assert deployer.deployer(o, "staging", a_blanc=True) == Code.OK
    assert composes(poste) == [] and journaux(o) == []
    texte = "\n".join(ecran)
    assert "refuserait : étiquette" in texte
    assert "refuserait : autre cible arrêtée" in texte
    ligne = next(x for x in ecran if "docker compose" in x)
    assert ligne.index("cibles/staging.env") < ligne.index("-p senpai-staging")
    assert "API_MODELE_PGPASS=<chemin>" in ligne


def test_a_blanc_tout_tenu(o: Outils, poste: Poste, ecran):
    assert deployer.deployer(o, "production", a_blanc=True) == Code.OK
    assert sum("préalable tenu" in x for x in ecran) == 5


def test_le_compteur_par_prometheus_en_production(
    o: Outils, poste: Poste, bouclage: Bouclage
):
    import json

    poste.metriques = {"statut": 404}
    q = "http://127.0.0.1:9090/api/v1/query?query="
    bouclage.reponses[q + "sum(api_modele_llm_appels_total)"] = (
        200,
        json.dumps({"data": {"result": [{"value": [0, "6"]}]}}),
    )
    bouclage.reponses[q + "sum(api_modele_llm_cout_usd_total)"] = (
        200,
        json.dumps({"data": {"result": []}}),
    )
    assert (
        deployer.compteur(o, deployer.cibles.lire("production"))
        == "6 appels, 0.000000 $"
    )
    bouclage.reponses.pop(q + "sum(api_modele_llm_cout_usd_total)")
    assert "Prometheus ne répond pas" in deployer.compteur(
        o, deployer.cibles.lire("production")
    )
    poste.en_marche = {"senpai-staging": dict(SERVIES[E4])}
    assert "illisible" in deployer.compteur(o, deployer.cibles.lire("staging"))


def test_arreter_et_completer_le_journal(o: Outils, poste: Poste, ecran):
    deployer.deployer(o, "production")
    assert deployer.arreter(o, "production") == Code.OK
    (down,) = [a for a in composes(poste) if "down" in a]
    assert down[-2:] == ("down", "--remove-orphans")
    texte = (o.journal / journaux(o)[0]).read_text(encoding="utf-8")
    assert "## Arrêt — 2026-11-11 10:00:00 UTC" in texte
    assert "4 conteneurs avant, 0 après" in texte


def test_arreter_sans_journal_ou_en_echec(o: Outils, poste: Poste):
    poste.en_marche = {"senpai-staging": dict(SERVIES[E4])}
    assert deployer.arreter(o, "staging") == Code.OK
    poste.compose_code = 1
    with pytest.raises(Arret, match="ne s'arrête pas"):
        deployer.arreter(o, "production")


def test_sante_seule(o: Outils, bouclage: Bouclage, ecran):
    assert deployer.verifier_sante(o, "production") == Code.OK
    assert ecran[-1] == "Santé production (e4-livraison) : verte."
    bouclage.reponses.pop("http://127.0.0.1:8000/health")
    assert deployer.verifier_sante(o, "production") == Code.SANTE_ROUGE


def test_etat(o: Outils, ecran):
    deployer.deployer(o, "production")
    ecran.clear()
    assert deployer.etat(o) == Code.OK
    assert ecran[0] == "staging (senpai-staging) : arrêtée"
    assert ecran[2].startswith("production (api-modele) : en marche, 4 conteneurs")
    assert "application application-conseiller:0.9.1" in ecran[3]
    assert ecran[4].endswith("_production_e4-livraison.md")


def test_docker_qui_ne_repond_pas(o: Outils):
    from livraison.outils import Sortie

    o.lanceur = lambda c: Sortie(1, "", "absent")
    with pytest.raises(Arret, match="docker ne répond pas"):
        deployer.en_marche(o, "api-modele")


def test_j_le_journal_porte_pile_prete_et_duree_et_l_attente(
    o: Outils, bouclage: Bouclage
):
    """Écart 25 (0.1.2) : un déploiement attend un relevé postérieur au lever ; le
    journal porte « Pile prête » (Compose → /sante 200) et « Durée » (le geste entier),
    et l'attente de Prometheus."""
    bouclage.reponses[CIBLES] = [
        cibles_sauf(application=cible_prometheus("application", "down", RELEVE_AVANT)),
        cibles(),
    ]
    assert deployer.deployer(o, "production") == Code.OK
    texte = (o.journal / journaux(o)[0]).read_text(encoding="utf-8")
    pret = re.search(r"\| Pile prête \| (\d+) s \|", texte)
    duree = re.search(r"\| Durée \| (\d+) s \|", texte)
    assert pret and duree and int(pret.group(1)) < int(duree.group(1))
    assert "**santé verte**" in texte
    assert re.search(
        r"- Prometheus : \d+ s d'attente d'un relevé postérieur au lever", texte
    )


def test_j_une_pile_qui_ne_repond_pas_n_est_jamais_prete(o: Outils, bouclage: Bouclage):
    bouclage.reponses.pop("http://127.0.0.1:8020/sante")
    assert deployer.deployer(o, "production") == Code.DEPLOIEMENT
    texte = (o.journal / journaux(o)[0]).read_text(encoding="utf-8")
    assert "| Pile prête | — |" in texte
    assert "Prometheus :" not in texte
