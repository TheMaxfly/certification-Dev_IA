"""Configuration de l'API, chargée depuis les variables d'environnement."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace

from . import API_VERSION

API_KEYS_ENV = "API_KEYS"

# Le libellé nomme le CONSOMMATEUR (« app_backend »), jamais un rôle humain :
# le client final ne détient aucune clé, c'est le back-end qui la porte. Charset
# restreint parce que ce libellé finit dans les journaux : il doit y être lisible
# et ne rien pouvoir y injecter.
API_KEY_LABEL_PATTERN = re.compile(r"^[a-z0-9_-]+$")

# 32 caractères : un secret plus court se force. La borne porte sur la longueur
# et non sur l'entropie réelle, que le code ne peut pas mesurer ; la procédure de
# génération est dans le README (`openssl rand -base64 32`).
API_KEY_MIN_LENGTH = 32


@dataclass(frozen=True, slots=True)
class ApiKey:
    """Une clé d'accès et le libellé du consommateur qui la porte."""

    label: str
    secret: str


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} doit être un entier") from exc

    if value < minimum:
        raise ValueError(f"{name} doit être supérieur ou égal à {minimum}")
    return value


def parse_api_keys(raw_value: str | None) -> tuple[ApiKey, ...]:
    """Analyse `API_KEYS` au format « nom:clé,nom2:clé2 ».

    DÉFAILLANCE FERMÉE : toute anomalie lève `ValueError`, ce qui empêche
    l'application de démarrer. Il n'existe aucun chemin par lequel l'API
    démarrerait en accès ouvert — ni variable absente, ni valeur vide, ni entrée
    malformée. Un service de données qui démarre sans trousseau est un service
    ouvert : c'est l'incident, pas le mode dégradé.

    Aucun message d'erreur ne cite une clé. Ils désignent un libellé ou un rang
    d'entrée — ces messages partent dans les journaux de démarrage.
    """
    if raw_value is None:
        raise ValueError(
            f"{API_KEYS_ENV} est absente : l'API refuse de démarrer sans "
            "trousseau. Format attendu « nom:clé,nom2:clé2 »."
        )

    if not raw_value.strip():
        raise ValueError(
            f"{API_KEYS_ENV} est vide : l'API refuse de démarrer sans trousseau."
        )

    api_keys: list[ApiKey] = []
    seen_labels: set[str] = set()
    seen_secrets: set[str] = set()

    for position, raw_entry in enumerate(raw_value.split(","), start=1):
        entry = raw_entry.strip()
        if not entry:
            raise ValueError(
                f"{API_KEYS_ENV} : entrée n°{position} vide — une virgule "
                "surnuméraire ne peut pas être devinée, elle est refusée."
            )

        label, separator, secret = entry.partition(":")
        if not separator:
            raise ValueError(
                f"{API_KEYS_ENV} : entrée n°{position} malformée, "
                "format attendu « nom:clé »."
            )

        label = label.strip()
        secret = secret.strip()

        if not API_KEY_LABEL_PATTERN.match(label):
            raise ValueError(
                f"{API_KEYS_ENV} : entrée n°{position}, le nom de consommateur "
                "doit être non vide et composé de [a-z0-9_-]."
            )
        if len(secret) < API_KEY_MIN_LENGTH:
            raise ValueError(
                f"{API_KEYS_ENV} : la clé du consommateur « {label} » fait moins "
                f"de {API_KEY_MIN_LENGTH} caractères."
            )
        if label in seen_labels:
            raise ValueError(
                f"{API_KEYS_ENV} : le nom de consommateur « {label} » est en "
                "double ; les journaux ne pourraient plus les distinguer."
            )
        if secret in seen_secrets:
            raise ValueError(
                f"{API_KEYS_ENV} : deux consommateurs partagent la même clé, "
                f"dont « {label} » ; une clé identifie un consommateur et un seul."
            )

        seen_labels.add(label)
        seen_secrets.add(secret)
        api_keys.append(ApiKey(label=label, secret=secret))

    return tuple(api_keys)


@dataclass(frozen=True, slots=True)
class Settings:
    """Paramètres nécessaires à FastAPI et au pool PostgreSQL."""

    app_name: str = "API Manga"
    app_version: str = API_VERSION
    app_env: str = "development"
    db_host: str = "host.docker.internal"
    db_port: int = 5432
    db_name: str = "apimanga"
    # Rôle de consultation, sans aucun droit d'écriture : il hérite ses
    # privilèges de `manga_ro` (migration 012). Il doit exister avant le
    # démarrage — cf. `database/outils/creer_role_lecture.sh`.
    db_user: str = "manga_api"
    db_password: str = ""
    db_connect_timeout: int = 5
    db_pool_timeout: int = 5
    db_pool_min_size: int = 1
    db_pool_max_size: int = 5
    # Trousseau d'autorisation. Vide par défaut pour que `Settings()` reste
    # utilisable en test unitaire ; `from_env` ne l'accepte JAMAIS vide.
    api_keys: tuple[ApiKey, ...] = ()

    @classmethod
    def database_from_env(cls) -> Settings:
        """Configuration de la BASE seule, sans trousseau d'autorisation.

        RÉSERVÉ aux outils qui ouvrent une connexion PostgreSQL sans jamais
        servir de requête HTTP — le témoin de lecture seule du harnais, par
        exemple. Ils doivent se connecter EXACTEMENT comme l'API, donc partager
        `build_conninfo` et ces réglages ; exiger d'eux une clé d'API n'aurait
        aucun sens, et leur en fabriquer une décorative diluerait le sens du
        trousseau.

        L'application, elle, passe par `from_env` — jamais par cette méthode.
        Le trousseau y est vide, ce qui n'autorise rien : `resolve_consumer`
        sur un trousseau vide refuse toute clé.
        """
        defaults = cls()
        settings = cls(
            app_name=os.getenv("APP_NAME", defaults.app_name),
            app_version=os.getenv("APP_VERSION", defaults.app_version),
            app_env=os.getenv("APP_ENV", defaults.app_env),
            db_host=os.getenv("DB_HOST", defaults.db_host),
            db_port=_env_int("DB_PORT", defaults.db_port),
            db_name=os.getenv("DB_NAME", defaults.db_name),
            db_user=os.getenv("DB_USER", defaults.db_user),
            db_password=os.getenv("DB_PASSWORD", defaults.db_password),
            db_connect_timeout=_env_int(
                "DB_CONNECT_TIMEOUT", defaults.db_connect_timeout
            ),
            db_pool_timeout=_env_int("DB_POOL_TIMEOUT", defaults.db_pool_timeout),
            db_pool_min_size=_env_int(
                "DB_POOL_MIN_SIZE", defaults.db_pool_min_size, minimum=0
            ),
            db_pool_max_size=_env_int("DB_POOL_MAX_SIZE", defaults.db_pool_max_size),
        )
        if settings.db_pool_min_size > settings.db_pool_max_size:
            raise ValueError(
                "DB_POOL_MIN_SIZE doit être inférieur ou égal à DB_POOL_MAX_SIZE"
            )
        return settings

    @classmethod
    def from_env(cls) -> Settings:
        """Construit la configuration de l'API, trousseau compris.

        C'est le seul point d'entrée de l'application. Il LÈVE si `API_KEYS`
        est absente, vide ou malformée : sans trousseau valide, pas
        d'application — c'est la défaillance fermée.
        """
        return replace(
            cls.database_from_env(),
            api_keys=parse_api_keys(os.getenv(API_KEYS_ENV)),
        )
