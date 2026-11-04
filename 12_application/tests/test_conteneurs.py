"""L'image et le Compose du module : lus comme du texte (aucune dépendance de plus)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
DOCKERFILE = (RACINE / "Dockerfile").read_text(encoding="utf-8")
FUSION = (RACINE / "compose.yaml").read_text(encoding="utf-8")
ESSAI = (RACINE / "compose.integration.yml").read_text(encoding="utf-8")
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


def test_le_compose_ne_publie_que_sur_le_bouclage_et_ne_porte_aucune_cle():
    assert '"127.0.0.1:${APPLICATION_PORT_PUBLIE:-8020}:8020"' in FUSION
    assert "APPLICATION_CLE_API: ${APPLICATION_CLE_API:-}" in FUSION
    assert FUSION.count("ports:") == 1


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
