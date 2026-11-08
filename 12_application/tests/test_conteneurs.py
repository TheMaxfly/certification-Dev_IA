"""L'image et le Compose du module : lus comme du texte (aucune dépendance de plus)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
DOCKERFILE = (RACINE / "Dockerfile").read_text(encoding="utf-8")
FUSION = (RACINE / "compose.yaml").read_text(encoding="utf-8")
ESSAI = (RACINE / "compose.integration.yml").read_text(encoding="utf-8")
DEMO = (RACINE / "compose.demo.yaml").read_text(encoding="utf-8")
MONITORAGE = (RACINE.parent / "13_monitorage" / "compose.yaml").read_text(
    encoding="utf-8"
)
IGNORE = (RACINE / "Dockerfile.dockerignore").read_text(encoding="utf-8")


def test_chaque_image_de_base_est_epinglee_par_empreinte():
    bases = re.findall(r"^FROM (\S+)", DOCKERFILE, re.M)
    externes = [b for b in bases if "/" in b or ":" in b]

    assert len(externes) == 4
    assert all(re.search(r"@sha256:[0-9a-f]{64}$", b) for b in externes), externes


@pytest.mark.parametrize("cible", ["doublure", "application"])
def test_les_images_ne_tournent_pas_en_root(cible):
    etape = DOCKERFILE.split(f" AS {cible}\n")[1].split("\nFROM ")[0]

    assert "USER 10001" in etape


def test_le_contexte_est_une_liste_blanche_sans_secret():
    lignes = [x for x in IGNORE.splitlines() if x and not x.startswith("#")]

    assert lignes[0] == "*"
    assert not [x for x in lignes if ".env" in x or "pgpass" in x]
    assert "!12_application/src/" in lignes


def bloc_du_service(nom: str) -> str:
    """Le texte d'un service du fichier de fusion, jusqu'au service suivant."""
    return FUSION.split(f"\n  {nom}:\n")[1].split("\n\n")[0]


def test_le_compose_ne_publie_que_sur_le_bouclage_et_ne_porte_aucune_cle():
    assert '"127.0.0.1:${APPLICATION_PORT_PUBLIE:-8020}:8020"' in FUSION
    assert '"127.0.0.1:${CATALOGUE_PORT_PUBLIE:-8000}:8000"' in FUSION
    assert "APPLICATION_CLE_API: ${APPLICATION_CLE_API:-}" in FUSION
    assert "APPLICATION_CLE_CATALOGUE: ${APPLICATION_CLE_CATALOGUE:-}" in FUSION
    assert "API_KEYS: ${API_KEYS:-}" in FUSION
    # Trois « ports: » : l'application et le catalogue sur le bouclage, l'API sans port.
    assert FUSION.count("ports:") == 3 and "ports: !reset []" in FUSION
    publications = re.findall(r'^ +- "([^"]+:\d+)"$', FUSION, re.M)
    assert publications and all(p.startswith("127.0.0.1:") for p in publications)


def test_le_catalogue_lit_la_base_par_un_pgpass_monte_jamais_par_un_mot_de_passe():
    catalogue = bloc_du_service("catalogue")

    assert 'DB_PASSWORD: ""' in catalogue
    assert "PGPASSFILE: /run/secrets/pgpass_catalogue" in catalogue
    assert "secrets: [pgpass_catalogue]" in catalogue
    assert "DB_USER: manga_api" in catalogue
    assert 'user: "${HOTE_UID:-1000}:${HOTE_GID:-1000}"' in catalogue
    # Le secret désigne un fichier hors dépôt, nommé par une variable surchargeable.
    assert "file: ${CATALOGUE_PGPASS:-../02_api_manga/.pgpass.conteneur}" in FUSION
    assert "image: api-catalogue:0.5.0" in catalogue


def test_le_catalogue_sort_vers_la_base_par_un_sous_reseau_fixe():
    reseaux = FUSION.split("\nnetworks:\n")[-1]

    assert "networks: [applicatif, catalogue_sortie]" in bloc_du_service("catalogue")
    assert (
        "catalogue_sortie:\n    ipam:\n      config:\n        - subnet: 172.30.2.0/24"
        in (reseaux)
    )


def test_l_application_attend_le_catalogue_et_le_joint_par_son_nom():
    application = bloc_du_service("application")

    assert "catalogue:\n        condition: service_healthy" in application
    assert "CATALOGUE_ADRESSE: http://catalogue:8000" in application


def test_le_catalogue_n_entre_pas_dans_le_monitorage():
    """Le fichier du 13 ne touche que l'embedding, l'API et l'application : le catalogue
    n'expose pas de métriques."""
    assert "catalogue" not in MONITORAGE


def test_le_mode_demonstration_ne_publie_l_api_que_sur_le_bouclage():
    assert DEMO.count("ports:") == 1
    assert '- "127.0.0.1:8010:8010"' in DEMO
    assert 'API_MODELE_DOCUMENTATION: "vrai"' in DEMO
    # Aucun appel au fournisseur au lancement.
    assert 'API_MODELE_VERIFIER_MODELES: "false"' in DEMO


def test_le_mode_demonstration_ne_porte_aucune_cle():
    variables = re.findall(r"^ +(\w+): \S", DEMO, re.M)

    assert variables == ["API_MODELE_DOCUMENTATION", "API_MODELE_VERIFIER_MODELES"]


def test_l_application_ne_joint_que_l_api():
    assert "API_MODELE_ADRESSE: http://api:8010" in FUSION
    assert "networks: [applicatif, publication]" in FUSION
    assert "networks: [interne, sortie, applicatif]" in FUSION
    bloc = FUSION.split("networks:\n")[-1]
    assert "applicatif:\n    internal: true" in bloc


def test_l_essai_tourne_sans_sortie_et_sans_port_publie():
    assert "internal: true" in ESSAI
    assert "ports:" not in ESSAI
    assert "APPLICATION_CLE_API: cle-jetable-essai" in ESSAI
    assert "API_MODELE_ADRESSE: http://doublure:8010" in ESSAI
