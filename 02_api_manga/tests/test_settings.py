from __future__ import annotations

import pytest
from psycopg.conninfo import conninfo_to_dict

from app.database import build_conninfo
from app.main import create_app
from app.security import resolve_consumer
from app.settings import Settings, parse_api_keys


def test_conninfo_escapes_special_characters() -> None:
    settings = Settings(db_user="manga user", db_password="quote' and space")

    parsed = conninfo_to_dict(build_conninfo(settings))

    assert parsed["user"] == "manga user"
    assert parsed["password"] == "quote' and space"


def test_settings_validate_pool_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "6")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "5")

    with pytest.raises(ValueError, match="DB_POOL_MIN_SIZE"):
        Settings.from_env()


def test_settings_allow_an_empty_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "0")

    assert Settings.from_env().db_pool_min_size == 0


# --- Trousseau d'autorisation : défaillance fermée -------------------------
#
# Contrôle 5. Ce qui est vérifié ici n'est pas qu'une valeur invalide est
# signalée, mais qu'elle EMPÊCHE le démarrage. Une API de données qui démarre
# sans trousseau est une API ouverte ; il ne doit exister aucun chemin de code
# qui y mène — ni variable absente, ni valeur vide, ni entrée malformée.

CLE_VALIDE = "a" * 32


def test_un_trousseau_valide_est_analyse() -> None:
    trousseau = parse_api_keys(f"app_backend:{CLE_VALIDE},batch_rag:{'b' * 40}")

    assert [entree.label for entree in trousseau] == ["app_backend", "batch_rag"]
    assert trousseau[0].secret == CLE_VALIDE


def test_les_espaces_autour_des_entrees_sont_tolerés() -> None:
    """Un `.env` réécrit à la main ne doit pas fermer le service pour un blanc."""
    trousseau = parse_api_keys(f"  app_backend : {CLE_VALIDE}  ")

    assert trousseau[0].label == "app_backend"
    assert trousseau[0].secret == CLE_VALIDE


@pytest.mark.parametrize(
    ("valeur", "cas"),
    [
        (None, "variable absente"),
        ("", "valeur vide"),
        ("   ", "valeur blanche"),
        (f"app_backend:{CLE_VALIDE},", "virgule surnuméraire"),
        (f"{CLE_VALIDE}", "pas de séparateur"),
        (f":{CLE_VALIDE}", "nom vide"),
        (f"App_Backend:{CLE_VALIDE}", "majuscules hors du charset"),
        (f"app backend:{CLE_VALIDE}", "espace dans le nom"),
        ("app_backend:trop-courte", "clé de moins de 32 caractères"),
        (f"app_backend:{CLE_VALIDE},app_backend:{'b' * 32}", "nom en double"),
        (f"app_backend:{CLE_VALIDE},batch_rag:{CLE_VALIDE}", "clé en double"),
    ],
)
def test_un_trousseau_invalide_interdit_le_demarrage(
    valeur: str | None, cas: str
) -> None:
    with pytest.raises(ValueError, match="API_KEYS"):
        parse_api_keys(valeur)


@pytest.mark.parametrize("valeur", [None, "", "app_backend:trop-courte"])
def test_l_application_refuse_de_se_construire_sans_trousseau_valide(
    monkeypatch: pytest.MonkeyPatch, valeur: str | None
) -> None:
    """La preuve de bout en bout : `create_app()` lève, l'app n'existe pas."""
    if valeur is None:
        monkeypatch.delenv("API_KEYS", raising=False)
    else:
        monkeypatch.setenv("API_KEYS", valeur)

    with pytest.raises(ValueError, match="API_KEYS"):
        create_app()


def test_aucun_message_d_erreur_ne_cite_une_cle() -> None:
    """Ces messages partent dans les journaux de démarrage."""
    secret = "clé-trop-courte-mais-secrete"

    with pytest.raises(ValueError) as erreur:
        parse_api_keys(f"app_backend:{secret}")

    assert secret not in str(erreur.value)
    assert "app_backend" in str(erreur.value)


def test_la_configuration_de_base_seule_n_exige_aucun_trousseau(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`database_from_env` sert les outils qui ne répondent à aucune requête.

    Le témoin de lecture seule du harnais ouvre une connexion sous le rôle de
    l'API pour prouver que la base lui refuse l'écriture. Lui imposer une clé
    d'API ajouterait au harnais un secret qui n'ouvre rien.
    """
    monkeypatch.delenv("API_KEYS", raising=False)

    settings = Settings.database_from_env()

    assert settings.api_keys == ()
    # Et un trousseau vide n'autorise personne.
    assert resolve_consumer(settings.api_keys, "n'importe quoi") is None


def test_la_configuration_de_l_application_exige_le_trousseau(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La contre-épreuve : le point d'entrée de l'API, lui, ne cède pas."""
    monkeypatch.delenv("API_KEYS", raising=False)

    with pytest.raises(ValueError, match="API_KEYS"):
        Settings.from_env()
