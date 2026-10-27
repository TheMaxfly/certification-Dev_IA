"""L'exécution conteneurisée, lue telle qu'elle est écrite (spec 05, B).

Ce que le Compose et le Dockerfile promettent, et que l'essai d'intégration ne voit
pas (il ne publie aucun port et remplace le modèle par une doublure) : seule l'API
publie un port, sur l'adresse locale par défaut ; le service d'embedding est celui du
module 09 (image et configuration), sur un réseau interne ; l'API l'attend sain ;
images épinglées par empreinte ; utilisateur non root ; secrets hors du DSN. L'essai
d'intégration (`compose.integration.yml`) éprouve le reste, en fonctionnement.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import RACINE_DEPOT, RACINE_MODULE, harnais

COMPOSE = RACINE_MODULE / "docker-compose.yml"
COMPOSE_CPU = RACINE_MODULE / "compose.cpu.yml"
COMPOSE_INTEGRATION = RACINE_MODULE / "compose.integration.yml"
DOCKERFILE = RACINE_MODULE / "Dockerfile"
DOCKERIGNORE = RACINE_MODULE / "Dockerfile.dockerignore"
COMPOSE_09 = RACINE_DEPOT / "09_service_embedding" / "compose.yml"
EMPREINTE = re.compile(r"@sha256:[0-9a-f]{64}$")
DEFAUT = re.compile(r"\$\{([A-Z0-9_]+):-([^}]*)\}")


class Lecteur(yaml.SafeLoader):
    """YAML sûr, plus les étiquettes de fusion de Compose (`!reset`, `!override`)."""


def _etiquette(loader: yaml.SafeLoader, noeud: yaml.Node) -> Any:
    if isinstance(noeud, yaml.MappingNode):
        return {"!": noeud.tag, **loader.construct_mapping(noeud)}
    if isinstance(noeud, yaml.SequenceNode):
        return {"!": noeud.tag, "valeur": loader.construct_sequence(noeud)}
    return {"!": noeud.tag}


Lecteur.add_constructor("!reset", _etiquette)
Lecteur.add_constructor("!override", _etiquette)


def lire(chemin: Path) -> dict[str, Any]:
    return yaml.load(chemin.read_text(encoding="utf-8"), Loader=Lecteur)  # noqa: S506


def par_defaut(valeur: str) -> str:
    """La valeur qu'une chaîne Compose prend quand ses variables sont absentes."""
    return DEFAUT.sub(lambda m: m.group(2), valeur)


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return lire(COMPOSE)


def test_seule_l_api_publie_un_port_sur_l_adresse_locale_par_defaut(compose):
    services = compose["services"]
    publies = {nom: s["ports"] for nom, s in services.items() if "ports" in s}

    assert list(publies) == ["api"]
    assert [par_defaut(p) for p in publies["api"]] == ["127.0.0.1:8010:8010"]


def test_le_service_d_embedding_est_celui_du_module_09(compose):
    embedding = compose["services"]["embedding"]
    image_09 = lire(COMPOSE_09)["x-tei"]["image"]

    assert embedding["image"] == image_09
    assert EMPREINTE.search(embedding["image"])
    assert embedding["env_file"] == [
        "../09_service_embedding/config/embeddinggemma.env"
    ]
    assert embedding["environment"] == {"HF_HUB_OFFLINE": "1"}
    # Les gardes « une instance à la fois » cherchent `tei-…`.
    assert embedding["container_name"].startswith("tei-")


def test_le_service_d_embedding_n_est_que_sur_le_reseau_interne(compose):
    assert compose["networks"]["interne"] == {"internal": True}
    assert compose["services"]["embedding"]["networks"] == ["interne"]
    assert set(compose["services"]["api"]["networks"]) == {"interne", "sortie"}


def test_l_api_attend_le_service_sain_et_chacun_a_son_controle_de_sante(compose):
    api, embedding = compose["services"]["api"], compose["services"]["embedding"]

    assert api["depends_on"] == {"embedding": {"condition": "service_healthy"}}
    assert "/health" in " ".join(embedding["healthcheck"]["test"])
    texte = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^HEALTHCHECK .*\n.*/v1/sante", texte, flags=re.MULTILINE)


def test_l_api_lit_la_base_sans_mot_de_passe_dans_le_dsn(compose):
    environnement = compose["services"]["api"]["environment"]
    dsn = par_defaut(environnement["API_MODELE_DSN"])

    assert dsn == "postgresql://modele_api@host.docker.internal:5432/apimanga"
    assert environnement["PGPASSFILE"] == "/run/secrets/pgpass"
    assert compose["services"]["api"]["secrets"] == ["pgpass"]
    assert par_defaut(environnement["API_MODELE_CLES"]) == ""
    assert environnement["CLIENT_HOTE"] == "embedding"


def test_les_cles_des_fournisseurs_ne_sont_lues_que_dans_le_env(compose):
    """Aucune valeur écrite dans le Compose ; l'essai jetable n'a qu'une clé
    factice, celle qui laisse démarrer le profil actif (il n'appelle personne)."""
    environnement = compose["services"]["api"]["environment"]
    for variable in ("MISTRAL_API_KEY", "OPENAI_API_KEY"):
        assert environnement[variable] == f"${{{variable}:-}}", variable
    essai = lire(COMPOSE_INTEGRATION)["services"]["api"]["environment"]
    cles = {v for v in essai if v.endswith("_API_KEY")}
    assert cles == {"MISTRAL_API_KEY"}
    assert essai["MISTRAL_API_KEY"].startswith("cle-factice-")


def test_les_conteneurs_ne_tournent_pas_en_root(compose):
    for nom in ("api", "embedding"):
        assert par_defaut(compose["services"][nom]["user"]) == "1000:1000", nom
    lignes = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    utilisateurs = [ligne.split()[1] for ligne in lignes if ligne.startswith("USER ")]
    assert utilisateurs == ["10001"]


def test_les_images_de_base_sont_epinglees_par_empreinte():
    texte = DOCKERFILE.read_text(encoding="utf-8")
    images = re.findall(r"^FROM (\S+)", texte, flags=re.MULTILINE)

    assert len(images) == 3
    assert all(EMPREINTE.search(i) for i in images), images


def test_uv_dans_l_image_est_celui_de_la_chaine():
    texte = DOCKERFILE.read_text(encoding="utf-8")
    workflow = (RACINE_DEPOT / ".github" / "workflows" / "verify.yml").read_text(
        encoding="utf-8"
    )
    dans_l_image = re.search(r"astral-sh/uv:([0-9.]+)@", texte)
    dans_la_chaine = re.search(r'version: "([0-9.]+)"', workflow)

    assert dans_l_image and dans_la_chaine
    assert dans_l_image.group(1) == dans_la_chaine.group(1)


def test_le_contexte_de_construction_est_une_liste_blanche():
    lignes = [
        ligne
        for ligne in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if ligne and not ligne.startswith("#")
    ]

    assert lignes[0] == "*"
    admis = [ligne[1:] for ligne in lignes if ligne.startswith("!")]
    # Une seule donnée : les homonymes admis (des empreintes, aucun nom), nommée.
    donnees = [a for a in admis if a.startswith("database/")]
    assert donnees == ["database/donnees/corpus_homonymes_admis.csv"]
    assert all(
        a.startswith(("05_", "09_", "10_", "11_")) and "data" not in a
        for a in admis
        if a not in donnees
    )
    assert not any(".env" in a and "embeddinggemma" not in a for a in admis)


def test_le_mode_processeur_change_l_image_et_rien_d_autre():
    cpu = lire(COMPOSE_CPU)
    embedding = cpu["services"]["embedding"]

    assert list(cpu["services"]) == ["embedding"]
    assert set(embedding) == {"image", "deploy"}
    assert re.search(r":cpu-1\.9\.4@sha256:[0-9a-f]{64}$", embedding["image"])
    assert embedding["deploy"]["!"] == "!reset"


def test_l_essai_d_integration_ne_publie_rien_et_prend_la_base_du_harnais():
    essai = lire(COMPOSE_INTEGRATION)

    assert essai["name"] != lire(COMPOSE)["name"]
    assert essai["services"]["api"]["ports"]["!"] == "!reset"
    assert essai["services"]["db"]["image"] == harnais.IMAGE_POSTGRES


def test_chaque_variable_du_compose_est_documentee_dans_env_example():
    # `$${…}` est échappé : une variable du conteneur, pas une interpolation.
    variables = set(re.findall(r"(?<!\$)\$\{([A-Z0-9_]+)", COMPOSE.read_text("utf-8")))
    exemple = (RACINE_MODULE / ".env.example").read_text(encoding="utf-8")

    assert variables, "aucune variable lue"
    absentes = {v for v in variables if not re.search(rf"^#? ?{v}=", exemple, re.M)}
    assert absentes == set()
    assert re.search(
        r"^# COMPOSE_FILE=docker-compose\.yml:compose\.cpu\.yml$", exemple, re.M
    )
