"""Les configurations de la collecte, validées sans lancer les services.

`promtool` tourne dans l'image épinglée, en conteneur, sur le poste comme sur GitHub :
si Docker manque, le test échoue, il ne saute pas. La fusion Compose est lue avec un
fichier d'environnement vide : aucune valeur réelle n'est lue ni affichée.
"""

from __future__ import annotations

import os
import re
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import MODULE, RACINE

SOUS_RESEAU = "172.30.13.0/24"
#: Les aspects de secrets : ceux des hooks, et une adresse de courriel réelle.
SECRETS = re.compile(
    r"hf_[A-Za-z0-9]{34}|sk-[A-Za-z0-9_-]{40,}"
    r"|[A-Za-z0-9._-]+@(?!exemple\.invalid|host\.docker\.internal)[A-Za-z0-9-]+\.[a-z]{2,}"
    r"|(?:password|mot_de_passe)\s*[:=]\s*['\"]?[A-Za-z0-9+/_-]{8,}",
    re.IGNORECASE,
)
SUIVIS = (
    "compose.yaml",
    "prometheus/prometheus.yml",
    "prometheus/regles/enregistrement.yml",
    "grafana/provisioning/datasources/prometheus.yml",
    "grafana/provisioning/dashboards/tableaux.yml",
    ".env.example",
    "README.md",
)


def lire(relatif: str) -> Any:
    return yaml.safe_load((MODULE / relatif).read_text(encoding="utf-8"))


def fichier_compose() -> dict[str, Any]:
    return lire("compose.yaml")


@cache
def fusion() -> dict[str, Any]:
    """Les trois fichiers Compose, tels que `make monitorage` les fusionne."""
    environnement = {
        **os.environ,
        "API_MODELE_PGPASS": "/dev/null",
        "GRAFANA_MOT_DE_PASSE_FICHIER": "/dev/null",
        "GRAFANA_SMTP_MOT_DE_PASSE_FICHIER": "/dev/null",
        "MONITORAGE_PGPASS_FICHIER": "/dev/null",
    }
    sortie = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            "11_api_modele/docker-compose.yml",
            "-f",
            "12_application/compose.yaml",
            "-f",
            "13_monitorage/compose.yaml",
            "config",
        ],
        cwd=RACINE,
        env=environnement,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return yaml.safe_load(sortie)


def promtool(*arguments: str) -> subprocess.CompletedProcess[str]:
    """`promtool` dans l'image épinglée au Compose, dossier monté en lecture seule."""
    image = fichier_compose()["services"]["prometheus"]["image"]
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "promtool",
            "-v",
            f"{MODULE / 'prometheus'}:/etc/prometheus:ro",
            image,
            *arguments,
        ],
        capture_output=True,
        text=True,
    )


def test_promtool_valide_la_configuration():
    fini = promtool("check", "config", "/etc/prometheus/prometheus.yml")
    assert fini.returncode == 0, fini.stdout + fini.stderr


def test_promtool_valide_les_regles():
    fini = promtool("check", "rules", "/etc/prometheus/regles/enregistrement.yml")
    assert fini.returncode == 0, fini.stdout + fini.stderr


def test_les_cinq_cibles_et_la_scrutation():
    configuration = lire("prometheus/prometheus.yml")
    assert configuration["global"]["scrape_interval"] == "15s"
    cibles = {
        job["job_name"]: job["static_configs"][0]["targets"]
        for job in configuration["scrape_configs"]
    }
    assert cibles == {
        "embedding": ["embedding:8082"],
        "api": ["api:8010"],
        "application": ["application:8020"],
        "machine": ["node-exporter:9100"],
        "qualite": ["sonde:9108"],
    }


def test_la_retention_est_de_quinze_jours_et_les_donnees_sur_un_volume():
    prometheus = fichier_compose()["services"]["prometheus"]
    assert "--storage.tsdb.retention.time=15d" in prometheus["command"]
    assert "prometheus_donnees:/prometheus" in prometheus["volumes"]


@pytest.mark.parametrize("service", ["prometheus", "grafana", "node-exporter"])
def test_les_images_sont_epinglees_par_empreinte(service: str):
    image = fichier_compose()["services"][service]["image"]
    assert re.fullmatch(r"[a-z/-]+:[v0-9.]+@sha256:[0-9a-f]{64}", image), image


def test_quatre_ports_sont_publies_tous_sur_l_adresse_locale():
    publies = {
        nom: [(p["host_ip"], p["published"]) for p in service["ports"]]
        for nom, service in fusion()["services"].items()
        if service.get("ports")
    }
    assert publies == {
        "application": [("127.0.0.1", "8020")],
        # Le catalogue (l'API data du 02, E4 lot 2a), du fichier de fusion du 12.
        "catalogue": [("127.0.0.1", "8000")],
        "prometheus": [("127.0.0.1", "9090")],
        "grafana": [("127.0.0.1", "3000")],
    }


def test_l_exporteur_de_machine_n_est_pas_publie():
    assert "ports" not in fusion()["services"]["node-exporter"]


def test_le_service_d_embedding_reste_sans_sortie_et_hors_de_portee_de_l_application():
    services = fusion()["services"]
    assert set(services["embedding"]["networks"]) == {"interne", "collecte_embedding"}
    assert set(services["application"]["networks"]).isdisjoint(
        {"interne", "collecte_embedding"}
    )
    assert set(services["node-exporter"]["networks"]) == {"collecte"}
    assert fusion()["networks"]["interne"]["internal"] is True
    assert fusion()["networks"]["collecte_embedding"]["internal"] is True
    assert fusion()["networks"]["collecte"]["internal"] is True


def test_seul_le_reseau_de_collecte_lit_les_metriques():
    services = fusion()["services"]
    assert (
        fusion()["networks"]["collecte"]["ipam"]["config"][0]["subnet"] == SOUS_RESEAU
    )
    environnement_api = services["api"]["environment"]
    assert environnement_api["API_MODELE_METRIQUES_RESEAUX"] == SOUS_RESEAU
    environnement_app = services["application"]["environment"]
    assert environnement_app["APPLICATION_METRIQUES_RESEAUX"] == SOUS_RESEAU


def test_le_monitorage_n_appelle_aucun_fournisseur_au_demarrage():
    environnement = fusion()["services"]["api"]["environment"]
    assert environnement["API_MODELE_VERIFIER_MODELES"] == "false"


def test_grafana_sans_connexion_anonyme_et_mot_de_passe_par_fichier():
    environnement = fichier_compose()["services"]["grafana"]["environment"]
    assert environnement["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert environnement["GF_USERS_ALLOW_SIGN_UP"] == "false"
    assert (
        environnement["GF_SECURITY_ADMIN_PASSWORD__FILE"]
        == "/run/secrets/grafana_mot_de_passe"
    )
    assert "GF_SECURITY_ADMIN_PASSWORD" not in environnement


def test_grafana_ne_telephone_a_personne():
    environnement = fichier_compose()["services"]["grafana"]["environment"]
    for variable in (
        "GF_ANALYTICS_REPORTING_ENABLED",
        "GF_ANALYTICS_CHECK_FOR_UPDATES",
        "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES",
        "GF_NEWS_NEWS_FEED_ENABLED",
    ):
        assert environnement[variable] == "false"
    assert environnement["GF_PLUGINS_PREINSTALL_DISABLED"] == "true"


def test_le_courriel_passe_par_un_fichier_de_mot_de_passe_et_une_adresse_hors_depot():
    service = fichier_compose()["services"]["grafana"]
    environnement = service["environment"]
    assert environnement["GF_SMTP_ENABLED"] == "true"
    assert environnement["GF_SMTP_STARTTLS_POLICY"] == "MandatoryStartTLS"
    assert environnement["GF_SMTP_PASSWORD__FILE"] == "/run/secrets/smtp_mot_de_passe"
    assert "GF_SMTP_PASSWORD" not in environnement
    assert "smtp_mot_de_passe" in service["secrets"]
    for variable in ("GF_SMTP_USER", "GF_SMTP_FROM_ADDRESS", "MONITORAGE_COURRIEL"):
        assert environnement[variable].startswith("${MONITORAGE_COURRIEL:-")
    # L'environnement en tête de l'objet : celui de la cible, défaut de l'application.
    assert (
        environnement["MONITORAGE_ENVIRONNEMENT"]
        == "${APPLICATION_ENVIRONNEMENT:-developpement}"
    )


def test_la_sonde_lit_la_base_par_un_pgpass_et_un_dsn_sans_mot_de_passe():
    service = fichier_compose()["services"]["sonde"]
    environnement = service["environment"]
    assert environnement["PGPASSFILE"] == "/run/secrets/pgpass_lecture"
    assert environnement["MONITORAGE_LECTURE_BASE"] == "${MONITORAGE_LECTURE_BASE:-0}"
    assert environnement["MONITORAGE_DSN_LECTURE"] == "${MONITORAGE_DSN_LECTURE:-}"
    assert "pgpass_lecture" in service["secrets"]
    assert "host.docker.internal:host-gateway" in service["extra_hosts"]
    reseau = fusion()["networks"]["publication_monitorage"]
    assert reseau["ipam"]["config"][0]["subnet"] == "172.30.14.0/24"


def test_discord_est_ecarte():
    for relatif in (*SUIVIS, "grafana/provisioning/alerting/canaux.yml"):
        texte = (MODULE / relatif).read_text(encoding="utf-8").lower()
        assert "discord" not in texte, relatif


def test_la_source_de_donnees_est_provisionnee_par_le_nom_du_service():
    source = lire("grafana/provisioning/datasources/prometheus.yml")["datasources"][0]
    assert source["url"] == "http://prometheus:9090"
    assert source["type"] == "prometheus"
    assert source["uid"] == "prometheus"
    assert source["editable"] is False


def test_les_tableaux_se_chargent_par_fichiers():
    fournisseur = lire("grafana/provisioning/dashboards/tableaux.yml")["providers"][0]
    assert fournisseur["type"] == "file"
    assert fournisseur["options"]["path"] == "/etc/grafana/tableaux"
    assert fournisseur["allowUiUpdates"] is False


@pytest.mark.parametrize("relatif", SUIVIS)
def test_aucun_secret_dans_les_fichiers_du_module(relatif: str):
    texte = (MODULE / relatif).read_text(encoding="utf-8")
    assert SECRETS.search(texte) is None


def test_le_fichier_d_exemple_ne_porte_que_des_noms():
    lignes = [
        ligne
        for ligne in (MODULE / ".env.example").read_text(encoding="utf-8").splitlines()
        if ligne and not ligne.startswith("#")
    ]
    assert all(re.fullmatch(r"[A-Z_]+=", ligne) for ligne in lignes), lignes
    assert {ligne.removesuffix("=") for ligne in lignes} >= {
        "GRAFANA_MOT_DE_PASSE_FICHIER",
        "MONITORAGE_DSN_LECTURE",
    }


@pytest.mark.parametrize(
    "fichier", ["grafana_mot_de_passe", "smtp_mot_de_passe", "pgpass_lecture"]
)
def test_les_mots_de_passe_ne_sont_pas_suivis(fichier: str):
    fini = subprocess.run(
        ["git", "check-ignore", "-q", f"13_monitorage/.secrets/{fichier}"],
        cwd=RACINE,
    )
    assert fini.returncode == 0


def test_les_compose_de_l_application_et_de_la_demonstration_ne_lisent_pas_le_module():
    makefile = (RACINE / "Makefile").read_text(encoding="utf-8")
    for nom in ("APP_COMPOSE", "DEMO_COMPOSE"):
        bloc = re.search(rf"^{nom} = (?:.*\\\n)*.*$", makefile, re.MULTILINE)
        assert bloc is not None
        assert "13_monitorage" not in bloc.group(0)
    assert "MONITORAGE_COMPOSE = " in makefile
    assert Path(MODULE / "compose.yaml").is_file()
