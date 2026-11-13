"""Déployer, arrêter, l'état : une cible à la fois, jamais de migration, toujours un
journal."""

from __future__ import annotations

import dataclasses
import json
import re
import tempfile

import pytest
from conftest import (
    APPELS,
    CIBLES,
    DEPUIS,
    E3,
    E4,
    RELEVE_AVANT,
    SERVIES,
    Bouclage,
    Poste,
    cible_prometheus,
    cibles,
    cibles_sauf,
    requete,
    sante_application,
)

from livraison import __main__ as principal
from livraison import deployer, journal, retour
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
    poste.en_marche, poste.reseaux = {}, {}
    with pytest.raises(Arret, match=r"\.env\.staging"):
        deployer.deployer(o, "staging")
    (o.racine / "12_application/.env.staging").write_text("", encoding="utf-8")
    with pytest.raises(Arret, match="PREPROD_PGPASS_CONTENEUR"):
        deployer.deployer(o, "staging")
    o.environ = {"PREPROD_PGPASS_CONTENEUR": str(o.racine / "absent")}
    with pytest.raises(Arret, match="PREPROD_PGPASS_CONTENEUR"):
        deployer.deployer(o, "staging")
    assert composes(poste) == []


def test_le_staging_se_deploie_avec_son_monitorage(
    o: Outils, poste: Poste, bouclage: Bouclage
):
    """0.1.3 : le staging lève le 13 ; sa santé juge les cibles de Prometheus et son
    compteur passe par Prometheus, comme en production."""
    poste.en_marche, poste.reseaux = {}, {}
    (o.racine / "pgpass").write_text("", encoding="utf-8")
    (o.racine / "12_application/.env.staging").write_text("", encoding="utf-8")
    o.environ = {"PREPROD_PGPASS_CONTENEUR": str(o.racine / "pgpass")}
    bouclage.reponses.pop("http://127.0.0.1:8020/sante")
    bouclage.reponses["http://127.0.0.1:8021/sante"] = (
        200,
        sante_application(cles_api="une"),
    )
    bouclage.reponses["http://127.0.0.1:8001/health"] = bouclage.reponses.pop(
        "http://127.0.0.1:8000/health"
    )
    assert deployer.deployer(o, "staging") == Code.OK
    (up,) = composes(poste)
    fichiers = [up[i + 1] for i, x in enumerate(up) if x == "--env-file"]
    assert fichiers[-2].endswith("12_application/.env.staging")
    assert fichiers[-1].endswith("cibles/staging.env")
    assert "13_monitorage/compose.yaml" in " ".join(up)
    assert poste.appels[[c.argv for c in poste.appels].index(up)].env == {
        "API_MODELE_PGPASS": str(o.racine / "pgpass")
    }
    texte = (o.journal / journaux(o)[0]).read_text(encoding="utf-8")
    assert "non relevé (pile arrêtée)" in texte
    assert "| prometheus | cibles up | toutes | 5/5 up | tenu |" in texte
    assert "- après : 4 appels, 0.012300 $" in texte


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
    # Hors monitorage, /metrics illisible dans le conteneur : rien à lire ailleurs.
    poste.en_marche = {"senpai-staging": dict(SERVIES[E4])}
    poste.collecte = set()
    staging = deployer.cibles.lire("staging")
    sans = dataclasses.replace(staging, avec_monitorage=False)
    assert "illisible" in deployer.compteur(o, sans)


def test_arreter_et_completer_le_journal(o: Outils, poste: Poste, ecran):
    deployer.deployer(o, "production")
    assert deployer.arreter(o, "production") == Code.OK
    (down,) = [a for a in composes(poste) if "down" in a]
    assert down[-2:] == ("down", "--remove-orphans")
    texte = (o.journal / journaux(o)[0]).read_text(encoding="utf-8")
    assert "## Arrêt — 2026-11-11 10:00:00 UTC" in texte
    assert "4 conteneurs (dont 4 en marche) et 9 réseaux avant, 0 et 0 après" in texte


def test_arreter_sans_journal_ou_en_echec(o: Outils, poste: Poste):
    poste.en_marche = {"senpai-staging": dict(SERVIES[E4])}
    assert deployer.arreter(o, "staging") == Code.OK
    poste.compose_code = 1
    with pytest.raises(Arret, match="ne s'arrête pas"):
        deployer.arreter(o, "production")


def test_sante_seule(o: Outils, bouclage: Bouclage, ecran):
    assert deployer.verifier_sante(o, "production") == Code.OK
    assert ecran[-1] == (
        "Santé production (e4-livraison — étiquette de HEAD, aucun journal pour la"
        " cible) : verte."
    )
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


# --------------------------------------------------------------------------- #
#  Écart 27 (0.1.2) : `livrer sante` juge ce qui a été déployé
# --------------------------------------------------------------------------- #


def test_27a_apres_un_retour_la_sante_juge_l_etiquette_visee(
    o: Outils, poste: Poste, bouclage: Bouclage, ecran, tmp_path
):
    """HEAD reste à l'étiquette d'E4 ; la pile sert `chaine-livraison` : c'est elle
    que juge `livrer sante`, par le dernier journal de la cible."""
    poste.apres_up = dict(SERVIES[E3])
    poste.api.update(version_api="1.4.0")
    bouclage.reponses["http://127.0.0.1:8020/sante"] = (
        200,
        json.dumps({"application": "ok", "api": "ok", "version": "0.1.0"}),
    )
    temporaire = lambda: tempfile.TemporaryDirectory(dir=tmp_path)  # noqa: E731
    assert retour.retour(o, "production", E3, "essai", temporaire=temporaire) == 0
    assert poste.head == E4
    ecran.clear()
    assert deployer.verifier_sante(o, "production") == Code.OK
    assert ecran[-1] == (
        "Santé production (chaine-livraison — dernier journal de la cible) : verte."
    )


def test_27b_l_etiquette_demandee(o: Outils, ecran):
    deployer.deployer(o, "production")
    ecran.clear()
    assert deployer.verifier_sante(o, "production", E3) == Code.SANTE_ROUGE
    assert ecran[-1] == "Santé production (chaine-livraison — demandée) : ROUGE."
    assert (
        principal.principal(["sante", "--cible", "production", "--etiquette", E4], o)
        == 0
    )
    assert ecran[-1] == "Santé production (e4-livraison — demandée) : verte."


def test_27c_sans_journal_l_etiquette_de_head_dite(o: Outils, ecran):
    assert journal.dernier(o, "production") is None
    assert deployer.verifier_sante(o, "production") == Code.OK
    assert ecran[-1].startswith(
        "Santé production (e4-livraison — étiquette de HEAD, aucun journal pour la"
        " cible)"
    )


def test_27d_sans_journal_ni_etiquette_de_head_refus(o: Outils, poste: Poste):
    poste.head = None
    with pytest.raises(Arret, match="--etiquette") as e:
        deployer.verifier_sante(o, "production")
    assert e.value.code == Code.PREREQUIS


def test_27e_l_etiquette_du_dernier_journal_n_existe_plus(o: Outils, poste: Poste):
    o.journal.mkdir(parents=True)
    (o.journal / "20261111T100000Z_production_e4-disparue.md").write_text(
        "# Déploiement\n", encoding="utf-8"
    )
    poste.legeres.add("e4-legere")
    with pytest.raises(Arret, match="'e4-disparue'") as e:
        deployer.verifier_sante(o, "production")
    assert e.value.code == Code.PREREQUIS
    (o.journal / "20261111T110000Z_production_e4-legere.md").write_text(
        "# Déploiement\n", encoding="utf-8"
    )
    with pytest.raises(Arret, match="'e4-legere' inconnue, ou légère"):
        deployer.verifier_sante(o, "production")


def test_l_etiquette_lue_dans_le_nom_du_journal(tmp_path):
    chemin = tmp_path / "20261112T223702Z_production_e4-livraison-journal.md"
    assert journal.etiquette_de(chemin, "production") == "e4-livraison-journal"


# --------------------------------------------------------------------------- #
#  Écart 28 (0.1.2) : le compteur « après » ne vient que du conteneur levé
# --------------------------------------------------------------------------- #

PRODUCTION = deployer.cibles.lire("production")


def test_28a_metrics_ferme_dans_le_conteneur_l_api_relevee_apres_le_lever(o: Outils):
    assert deployer.compteur(o, PRODUCTION, DEPUIS) == "4 appels, 0.012300 $"


def test_28b_sans_releve_posterieur_de_l_api_jamais_le_chiffre_d_avant(
    o: Outils, bouclage: Bouclage
):
    bouclage.reponses[APPELS] = requete("2")
    bouclage.reponses[CIBLES] = cibles_sauf(
        api=cible_prometheus("api", "up", RELEVE_AVANT)
    )
    lu = deployer.compteur(o, PRODUCTION, DEPUIS)
    assert lu == deployer.NON_RELEVE_APRES and "2 appels" not in lu
    bouclage.reponses[CIBLES] = cibles_sauf(api=cible_prometheus("api", "down"))
    assert deployer.compteur(o, PRODUCTION, DEPUIS) == deployer.NON_RELEVE_APRES
    bouclage.reponses[CIBLES] = (0, "")
    assert deployer.compteur(o, PRODUCTION, DEPUIS) == deployer.NON_RELEVE_APRES


def test_28c_le_staging_et_le_compteur_avant_inchanges(
    o: Outils, poste: Poste, bouclage: Bouclage
):
    bouclage.reponses[CIBLES] = cibles_sauf(
        api=cible_prometheus("api", "up", RELEVE_AVANT)
    )
    # Avant toute commande, Prometheus parle du conteneur en marche : accepté.
    assert deployer.compteur(o, PRODUCTION) == "4 appels, 0.012300 $"
    # Le staging, sous le monitorage depuis la 0.1.3 : comme la production.
    poste.en_marche = {"senpai-staging": dict(SERVIES[E4])}
    staging = deployer.cibles.lire("staging")
    assert deployer.compteur(o, staging, DEPUIS) == deployer.NON_RELEVE_APRES
    bouclage.reponses[CIBLES] = cibles_sauf()
    assert deployer.compteur(o, staging, DEPUIS) == "4 appels, 0.012300 $"
    # Hors monitorage, la lecture dans le conteneur.
    poste.collecte = set()
    poste.metriques = {"statut": 200, "appels": 7, "cout": 0.5}
    sans = dataclasses.replace(staging, avec_monitorage=False)
    assert deployer.compteur(o, sans, DEPUIS) == "7 appels, 0.500000 $"


def test_28_un_deploiement_dont_l_api_n_est_jamais_relevee_apres_le_lever(
    o: Outils, bouclage: Bouclage
):
    bouclage.reponses[APPELS] = requete("2")
    bouclage.reponses[CIBLES] = cibles_sauf(
        api=cible_prometheus("api", "up", RELEVE_AVANT)
    )
    assert deployer.deployer(o, "production") == Code.SANTE_ROUGE
    texte = (o.journal / journaux(o)[0]).read_text(encoding="utf-8")
    assert "- avant : 2 appels, 0.012300 $" in texte
    assert f"- après : {deployer.NON_RELEVE_APRES}" in texte
    assert f"api:{deployer.sante.NON_RELEVEE}" in texte


def test_28_le_compteur_d_une_pile_qui_ne_se_leve_pas(o: Outils, poste: Poste):
    """La pile n'a jamais répondu : l'instant de référence du compteur est la fin des
    commandes Compose."""
    poste.compose_code = 1
    assert deployer.deployer(o, "production") == Code.DEPLOIEMENT
    texte = (o.journal / journaux(o)[0]).read_text(encoding="utf-8")
    assert "- après : 4 appels, 0.012300 $" in texte


# --------------------------------------------------------------------------- #
#  0.1.3 : l'autre cible arrêtée ET retirée
# --------------------------------------------------------------------------- #


def staging_pret(o: Outils) -> None:
    (o.racine / "pgpass").write_text("", encoding="utf-8")
    (o.racine / "12_application/.env.staging").write_text("", encoding="utf-8")
    o.environ = {"PREPROD_PGPASS_CONTENEUR": str(o.racine / "pgpass")}


def test_la_production_eteinte_avec_le_poste_refuse_le_staging_et_nomme_le_geste(
    o: Outils, poste: Poste
):
    """Après une extinction du poste : les conteneurs de la production arrêtés mais
    présents, ses réseaux aussi (relevé du 12 novembre). Le staging partage ses
    sous-réseaux : refus, avant toute commande Compose."""
    staging_pret(o)
    poste.arretes = {"api-modele": list(poste.en_marche.pop("api-modele"))}
    with pytest.raises(Arret, match="arrêtée mais pas retirée") as e:
        deployer.deployer(o, "staging")
    assert e.value.code == Code.PREREQUIS
    assert "4 conteneurs arrêtés, 9 réseaux" in e.value.motif
    assert e.value.motif.endswith("(livrer arreter --cible production)")
    assert composes(poste) == [] and journaux(o) == []


def test_des_reseaux_seuls_suffisent_au_refus(o: Outils, poste: Poste):
    staging_pret(o)
    poste.en_marche = {}
    with pytest.raises(Arret, match="0 conteneurs arrêtés, 9 réseaux"):
        deployer.deployer(o, "staging")
    poste.reseaux = {}
    staging_pret(o)
    poste.en_marche = {}
    deployer.exiger_autre_arretee(o, deployer.cibles.lire("staging"))


def test_le_geste_du_matin_puis_le_staging(o: Outils, poste: Poste, bouclage, ecran):
    """`livrer arreter --cible production` sur la production éteinte avec le poste : ses
    conteneurs et ses réseaux retirés, comptés ; le staging se lève ensuite."""
    staging_pret(o)
    poste.arretes = {"api-modele": list(poste.en_marche.pop("api-modele"))}
    assert deployer.arreter(o, "production") == Code.OK
    assert ecran[-1] == (
        "Cible production arrêtée et retirée : 4 conteneurs (dont 0 en marche) et"
        " 9 réseaux avant, 0 et 0 après."
    )
    bouclage.reponses["http://127.0.0.1:8021/sante"] = (
        200,
        sante_application(cles_api="une"),
    )
    bouclage.reponses["http://127.0.0.1:8001/health"] = bouclage.reponses[
        "http://127.0.0.1:8000/health"
    ]
    assert deployer.deployer(o, "staging") == Code.OK


def test_un_arret_qui_laisse_des_reseaux_est_un_echec(o: Outils, poste: Poste):
    appel = Poste.__call__

    def garde_les_reseaux(c):
        sortie = appel(poste, c)
        if c.argv[:2] == ("docker", "compose") and "down" in c.argv:
            poste.reseaux["api-modele"] = ["api-modele_collecte"]
        return sortie

    o.lanceur = garde_les_reseaux
    assert deployer.arreter(o, "production") == Code.DEPLOIEMENT


def test_docker_qui_ne_dit_pas_ce_qui_reste(o: Outils, poste: Poste):
    from livraison.outils import Sortie

    appel = Poste.__call__

    def muet(c):
        if c.argv[:3] == ("docker", "network", "ls"):
            return Sortie(1, "", "absent")
        return appel(poste, c)

    o.lanceur = muet
    with pytest.raises(Arret, match="docker ne répond pas"):
        deployer.restes(o, "api-modele")
