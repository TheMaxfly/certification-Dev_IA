"""Configuration d'une instance du service : `config/<instance>.env`.

Le même fichier est lu par le service (variables documentées de Text Embeddings
Inference, via `env_file` dans compose.yml) et par ce module (clés `CLIENT_*`,
plus les quelques variables du service dont le client a besoin : modèle,
révision, précision, ports). Une seule source : le préfixe qu'applique le client
et la révision que sert l'instance ne peuvent pas diverger.

Le format est le sous-ensemble que compose et ce lecteur comprennent de la même
façon : `CLE=valeur` sans espace, ou `CLE="valeur"` quand la valeur porte des
espaces significatifs ; commentaires sur leur propre ligne.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

RACINE = Path(__file__).resolve().parents[2]
DOSSIER_CONFIG = RACINE / "config"
INSTANCES = ("bge-m3", "embeddinggemma")

Role = Literal["document", "requete"]

_LIGNE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


class ConfigurationInvalide(Exception):
    """Le fichier de configuration ne respecte pas son format ou ses règles."""


def lire_env(chemin: Path) -> dict[str, str]:
    """Lit un fichier `CLE=valeur` ; refuse ce qu'il ne saurait lire sans doute."""
    valeurs: dict[str, str] = {}
    lignes = chemin.read_text(encoding="utf-8").splitlines()
    for numero, brute in enumerate(lignes, start=1):
        if not brute.strip() or brute.lstrip().startswith("#"):
            continue
        correspondance = _LIGNE.match(brute)
        if correspondance is None:
            raise ConfigurationInvalide(f"{chemin.name}:{numero} : ligne illisible")
        cle, valeur = correspondance.groups()
        if valeur.startswith('"'):
            if len(valeur) < 2 or not valeur.endswith('"') or '"' in valeur[1:-1]:
                raise ConfigurationInvalide(
                    f"{chemin.name}:{numero} : guillemets mal fermés pour {cle}"
                )
            valeur = valeur[1:-1]
        elif valeur != valeur.strip() or any(c in valeur for c in " #\"'"):
            # Sans guillemets, compose et ce lecteur pourraient diverger (espace
            # final, commentaire en ligne) : on exige des guillemets.
            raise ConfigurationInvalide(
                f"{chemin.name}:{numero} : valeur de {cle} à mettre entre guillemets"
            )
        if cle in valeurs:
            raise ConfigurationInvalide(f"{chemin.name}:{numero} : {cle} en double")
        valeurs[cle] = valeur
    return valeurs


@dataclass(frozen=True)
class Instance:
    nom: str
    model_id: str
    revision: str
    dtype: str
    port: int
    port_metriques: int
    auto_truncate: bool
    max_batch_tokens: int
    plafond_lot: int
    taille_lot: int
    longueur_maximale: int
    longueur_requise: int
    dimension: int
    prefixe_document: str
    prefixe_requete: str

    def prefixe(self, role: Role) -> str:
        if role == "document":
            return self.prefixe_document
        if role == "requete":
            return self.prefixe_requete
        raise ValueError(f"rôle inconnu : {role!r}")


def _entier(valeurs: dict[str, str], cle: str, fichier: str) -> int:
    try:
        return int(valeurs[cle])
    except ValueError as exc:
        raise ConfigurationInvalide(f"{fichier} : {cle} n'est pas un entier") from exc


def charger(nom: str, dossier: Path | None = None) -> Instance:
    """Charge l'instance `nom` et vérifie les règles qui la rendent utilisable."""
    dossier = dossier if dossier is not None else DOSSIER_CONFIG
    chemin = dossier / f"{nom}.env"
    if not chemin.is_file():
        raise ConfigurationInvalide(f"configuration absente : {chemin}")
    v = lire_env(chemin)
    requises = (
        "MODEL_ID REVISION DTYPE PORT CLIENT_PORT_METRIQUES AUTO_TRUNCATE "
        "MAX_BATCH_TOKENS MAX_CLIENT_BATCH_SIZE CLIENT_TAILLE_LOT "
        "CLIENT_LONGUEUR_MAXIMALE CLIENT_LONGUEUR_REQUISE CLIENT_DIMENSION "
        "CLIENT_PREFIXE_DOCUMENT CLIENT_PREFIXE_REQUETE"
    ).split()
    manquantes = [c for c in requises if c not in v]
    if manquantes:
        raise ConfigurationInvalide(f"{chemin.name} : clés absentes {manquantes}")

    instance = Instance(
        nom=nom,
        model_id=v["MODEL_ID"],
        revision=v["REVISION"],
        dtype=v["DTYPE"],
        port=_entier(v, "PORT", chemin.name),
        port_metriques=_entier(v, "CLIENT_PORT_METRIQUES", chemin.name),
        auto_truncate=v["AUTO_TRUNCATE"] == "true",
        max_batch_tokens=_entier(v, "MAX_BATCH_TOKENS", chemin.name),
        plafond_lot=_entier(v, "MAX_CLIENT_BATCH_SIZE", chemin.name),
        taille_lot=_entier(v, "CLIENT_TAILLE_LOT", chemin.name),
        longueur_maximale=_entier(v, "CLIENT_LONGUEUR_MAXIMALE", chemin.name),
        longueur_requise=_entier(v, "CLIENT_LONGUEUR_REQUISE", chemin.name),
        dimension=_entier(v, "CLIENT_DIMENSION", chemin.name),
        prefixe_document=v["CLIENT_PREFIXE_DOCUMENT"],
        prefixe_requete=v["CLIENT_PREFIXE_REQUETE"],
    )

    if not re.fullmatch(r"[0-9a-f]{40}", instance.revision):
        raise ConfigurationInvalide(f"{chemin.name} : REVISION doit être un commit")
    if instance.dtype not in ("float16", "float32"):
        raise ConfigurationInvalide(f"{chemin.name} : DTYPE inconnu du service")
    if v["AUTO_TRUNCATE"] not in ("true", "false"):
        raise ConfigurationInvalide(f"{chemin.name} : AUTO_TRUNCATE vaut true|false")
    # La troncature automatique du service n'est admise que là où il l'impose :
    # longueur du modèle au-delà de MAX_BATCH_TOKENS, ramenée à MAX_BATCH_TOKENS.
    # Le client, lui, envoie toujours `truncate: false`.
    if instance.auto_truncate and instance.longueur_maximale != (
        instance.max_batch_tokens
    ):
        raise ConfigurationInvalide(
            f"{chemin.name} : AUTO_TRUNCATE=true seulement quand la longueur "
            "maximale est ramenée à MAX_BATCH_TOKENS"
        )
    if not 0 < instance.taille_lot <= instance.plafond_lot:
        raise ConfigurationInvalide(
            f"{chemin.name} : CLIENT_TAILLE_LOT hors de ]0, MAX_CLIENT_BATCH_SIZE]"
        )
    if not (
        instance.longueur_requise
        <= instance.longueur_maximale
        <= instance.max_batch_tokens
    ):
        raise ConfigurationInvalide(
            f"{chemin.name} : il faut longueur requise ≤ longueur maximale ≤ "
            "MAX_BATCH_TOKENS"
        )
    return instance
