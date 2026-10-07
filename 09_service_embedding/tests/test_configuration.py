"""La configuration versionnée : ses règles, et son accord avec compose.yml."""

from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from service_embedding.configuration import (
    DOSSIER_CONFIG,
    INSTANCES,
    RACINE,
    ConfigurationInvalide,
    charger,
    lire_env,
)

COMPOSE = yaml.safe_load((RACINE / "compose.yml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("nom", INSTANCES)
def test_chaque_instance_se_charge(nom):
    instance = charger(nom)
    assert instance.longueur_requise <= instance.longueur_maximale


def test_les_valeurs_figees_par_la_spec():
    bge, gemma = charger("bge-m3"), charger("embeddinggemma")
    assert (bge.model_id, bge.revision[:8], bge.dimension) == (
        "BAAI/bge-m3",
        "5617a9f6",
        1024,
    )
    assert (gemma.model_id, gemma.revision[:8], gemma.dimension) == (
        "google/embeddinggemma-300m",
        "57c266a7",
        768,
    )
    # Exigences de non-troncature (§2.5) : 529 jetons XLM-R, 586 jetons Gemma.
    assert bge.longueur_requise >= 529
    assert gemma.longueur_requise >= 586


def test_prefixes_exacts_espace_final_compris():
    gemma = charger("embeddinggemma")
    assert gemma.prefixe("document") == "title: none | text: "
    assert gemma.prefixe("requete") == "task: search result | query: "
    bge = charger("bge-m3")
    assert bge.prefixe("document") == bge.prefixe("requete") == ""


def test_les_deux_modeles_en_float32():
    # Décision du 2026-10-07 : comparaison à précision égale, et précision des
    # questions encodées sur CPU en production. Pour Gemma, la fiche du modèle
    # exclut de toute façon float16, seul autre choix du service.
    assert {charger(n).dtype for n in INSTANCES} == {"float32"}


@pytest.mark.parametrize("nom", INSTANCES)
def test_ports_de_compose_identiques_a_la_configuration(nom):
    instance = charger(nom)
    publies = COMPOSE["services"][nom]["ports"]
    assert publies == [f"127.0.0.1:{instance.port}:{instance.port}"]


@pytest.mark.parametrize("nom", INSTANCES)
def test_metriques_sur_le_port_du_service(nom):
    # Constaté sur 1.9.4 : PROMETHEUS_PORT n'est jamais ouvert, les métriques
    # ne sont servies que sur le port HTTP. Le régler ferait croire le contraire.
    instance = charger(nom)
    assert instance.port_metriques == instance.port
    assert "PROMETHEUS_PORT" not in lire_env(DOSSIER_CONFIG / f"{nom}.env")


def test_ports_distincts_entre_instances():
    ports = [charger(n).port for n in INSTANCES]
    assert len(ports) == len(set(ports))


@pytest.mark.parametrize("nom", INSTANCES)
def test_image_epinglee_par_digest_et_fichier_de_configuration(nom):
    service = COMPOSE["services"][nom]
    assert re.fullmatch(
        r"ghcr\.io/huggingface/text-embeddings-inference:86-[0-9.]+"
        r"@sha256:[0-9a-f]{64}",
        service["image"],
    )
    assert service["env_file"] == [f"config/{nom}.env"]
    assert service["profiles"] == [nom]


def test_le_jeton_ne_va_qu_a_l_instance_a_acces_controle():
    assert "HF_TOKEN" in COMPOSE["services"]["embeddinggemma"]["environment"]
    assert "environment" not in COMPOSE["services"]["bge-m3"]
    for nom in INSTANCES:
        assert "HF_TOKEN" not in lire_env(DOSSIER_CONFIG / f"{nom}.env")


def test_aucun_jeton_dans_les_fichiers_versionnables():
    # Suivis, ou candidats au prochain commit (non suivis et non ignorés).
    suivis = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "."],
        cwd=RACINE,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    for chemin in filter(None, suivis):
        contenu = (RACINE / chemin.decode()).read_bytes()
        assert not re.search(rb"hf_[A-Za-z0-9]{30,}", contenu), chemin


def test_env_exclu_du_depot():
    resultat = subprocess.run(
        ["git", "check-ignore", "-q", ".env"], cwd=RACINE, check=False
    )
    assert resultat.returncode == 0
    assert (RACINE / ".env.example").is_file()


def test_valeur_avec_espace_sans_guillemets_refusee(tmp_path):
    fichier = tmp_path / "x.env"
    fichier.write_text("CLIENT_PREFIXE_DOCUMENT=title: none\n", encoding="utf-8")
    with pytest.raises(ConfigurationInvalide, match="guillemets"):
        lire_env(fichier)


def test_cle_en_double_refusee(tmp_path):
    fichier = tmp_path / "x.env"
    fichier.write_text("PORT=1\nPORT=2\n", encoding="utf-8")
    with pytest.raises(ConfigurationInvalide, match="double"):
        lire_env(fichier)


def test_troncature_du_service_seulement_si_imposee():
    # BGE-M3 : 8 192 jetons déclarés > MAX_BATCH_TOKENS, le service l'impose.
    bge = charger("bge-m3")
    assert bge.auto_truncate is True
    assert bge.longueur_maximale == bge.max_batch_tokens
    # EmbeddingGemma : 2 048 jetons tiennent dans MAX_BATCH_TOKENS, elle est coupée.
    assert charger("embeddinggemma").auto_truncate is False


def test_troncature_automatique_non_imposee_refusee(tmp_path):
    texte = (DOSSIER_CONFIG / "embeddinggemma.env").read_text(encoding="utf-8")
    (tmp_path / "embeddinggemma.env").write_text(
        texte.replace("AUTO_TRUNCATE=false", "AUTO_TRUNCATE=true"), encoding="utf-8"
    )
    with pytest.raises(ConfigurationInvalide, match="AUTO_TRUNCATE"):
        charger("embeddinggemma", tmp_path)


def test_lot_au_dela_du_plafond_refuse(tmp_path):
    texte = (DOSSIER_CONFIG / "bge-m3.env").read_text(encoding="utf-8")
    (tmp_path / "bge-m3.env").write_text(
        texte.replace("CLIENT_TAILLE_LOT=16", "CLIENT_TAILLE_LOT=128"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationInvalide, match="CLIENT_TAILLE_LOT"):
        charger("bge-m3", tmp_path)
