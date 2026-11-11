"""Les deux cibles, un fichier chacune (`cibles/<nom>.env`), sans secret.

LE FICHIER DE CIBLE ne contient que ce qui diffère d'une cible à l'autre : le nom du
projet Compose, le nom de la base (dans les DSN des conteneurs et `DB_NAME`), les ports
publiés, l'environnement de l'application, et la présence du monitorage. Il est
passé **en dernier** à `docker compose --env-file` : il l'emporte sur les fichiers des
modules.

L'ORDRE DES FICHIERS D'ENVIRONNEMENT : ceux des modules (11, 12, 02 ; 13 pour une cible
avec monitorage, s'il existe), puis `12_application/.env.<cible>` s'il existe (la clé
de session du staging : une par environnement), puis le fichier de cible. `livrer` ne
les ouvre jamais : il vérifie qu'ils existent.

CE QUE LE FICHIER NE PEUT PAS SURCHARGER : le contenu d'un fichier de mots de passe
monté. Pour le staging, l'API lit la base de préproduction avec le fichier de la chaîne,
dont le chemin vient de `PREPROD_PGPASS_CONTENEUR` (variable du terminal, fichier jamais
ouvert) et entre par l'environnement du processus (`API_MODELE_PGPASS`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from livraison.codes import Arret, Code
from livraison.outils import RACINE_MODULE, Commande, Outils

NOMS = ("staging", "production")
DOSSIER_CIBLES = RACINE_MODULE / "cibles"
COMPOSE_11 = "11_api_modele/docker-compose.yml"
COMPOSE_12 = "12_application/compose.yaml"
COMPOSE_13 = "13_monitorage/compose.yaml"
ENV_MODULES = ("11_api_modele/.env", "12_application/.env", "02_api_manga/.env")
ENV_13 = "13_monitorage/.env"
VARIABLE_PGPASS_PREPROD = "PREPROD_PGPASS_CONTENEUR"
#: L'environnement de l'application → la seule base qu'il admet (la garde du 12).
BASES = {"preproduction": "apimanga_preprod", "production": "apimanga"}
#: Les seules variables admises dans un fichier de cible (décision 2 du cadre).
ADMISES = frozenset(
    {
        "COMPOSE_PROJECT_NAME",
        "LIVRER_MONITORAGE",
        "DB_NAME",
        "API_MODELE_DSN_CONTENEUR",
        "APPLICATION_BASE_DSN_CONTENEUR",
        "APPLICATION_ENVIRONNEMENT",
        "APPLICATION_PORT_PUBLIE",
        "CATALOGUE_PORT_PUBLIE",
        "API_MODELE_VERIFIER_MODELES",
    }
)


@dataclass(frozen=True)
class Cible:
    nom: str
    projet: str
    fichier_env: Path
    base: str
    environnement: str
    port_application: int
    port_catalogue: int
    avec_monitorage: bool
    compose: tuple[str, ...]

    @property
    def autre(self) -> str:
        return NOMS[1 - NOMS.index(self.nom)]

    @property
    def env_propre(self) -> str:
        """`12_application/.env.<cible>` : exigé en staging, facultatif ailleurs."""
        return f"12_application/.env.{self.nom}"


def valeurs(texte: str) -> dict[str, str]:
    """`CLE=valeur` par ligne, commentaires et lignes vides ignorés, sans guillemets."""
    lu: dict[str, str] = {}
    for brute in texte.splitlines():
        ligne = brute.strip()
        if not ligne or ligne.startswith("#"):
            continue
        cle, egal, valeur = ligne.partition("=")
        if not egal:
            raise Arret(Code.PREREQUIS, f"fichier de cible : ligne illisible ({cle})")
        lu[cle.strip()] = valeur.strip()
    return lu


def lire(nom: str, dossier: Path | None = None) -> Cible:
    """La cible `nom`, lue et contrôlée ; sinon refus."""
    if nom not in NOMS:
        raise Arret(Code.PREREQUIS, f"cible {nom!r} : {' ou '.join(NOMS)} seulement")
    fichier = (dossier or DOSSIER_CIBLES) / f"{nom}.env"
    if not fichier.is_file():
        raise Arret(Code.PREREQUIS, f"fichier de cible absent : cibles/{nom}.env")
    v = valeurs(fichier.read_text(encoding="utf-8"))
    hors = sorted(set(v) - ADMISES)
    if hors:
        raise Arret(
            Code.PREREQUIS,
            f"cibles/{nom}.env : variables non admises ({', '.join(hors)}) — le fichier"
            " ne porte que ce qui diffère d'une cible à l'autre, jamais un secret",
        )
    manquent = sorted(ADMISES - {"API_MODELE_VERIFIER_MODELES"} - set(v))
    if manquent:
        raise Arret(
            Code.PREREQUIS, f"cibles/{nom}.env : manquent {', '.join(manquent)}"
        )
    base, environnement = v["DB_NAME"], v["APPLICATION_ENVIRONNEMENT"]
    if BASES.get(environnement) != base:
        raise Arret(
            Code.PREREQUIS,
            f"cibles/{nom}.env : l'environnement {environnement} n'admet pas la base"
            f" {base}",
        )
    for cle in ("API_MODELE_DSN_CONTENEUR", "APPLICATION_BASE_DSN_CONTENEUR"):
        dsn = v[cle]
        if not dsn.endswith(f"/{base}") or ":" in dsn.split("//", 1)[-1].split("@")[0]:
            raise Arret(
                Code.PREREQUIS,
                f"cibles/{nom}.env : {cle} doit viser {base}, sans mot de passe",
            )
    try:
        ports = int(v["APPLICATION_PORT_PUBLIE"]), int(v["CATALOGUE_PORT_PUBLIE"])
    except ValueError as exc:
        raise Arret(Code.PREREQUIS, f"cibles/{nom}.env : port illisible") from exc
    monitorage = v["LIVRER_MONITORAGE"] == "oui"
    return Cible(
        nom=nom,
        projet=v["COMPOSE_PROJECT_NAME"],
        fichier_env=fichier,
        base=base,
        environnement=environnement,
        port_application=ports[0],
        port_catalogue=ports[1],
        avec_monitorage=monitorage,
        compose=(COMPOSE_11, COMPOSE_12, *((COMPOSE_13,) if monitorage else ())),
    )


def fichiers_env(cible: Cible, racine: Path) -> list[Path]:
    """Les fichiers d'environnement, dans l'ordre où Compose les lit (le dernier
    l'emporte). Aucun n'est ouvert ici."""
    fichiers = [racine / f for f in ENV_MODULES]
    if cible.avec_monitorage and (racine / ENV_13).is_file():
        fichiers.append(racine / ENV_13)
    if (racine / cible.env_propre).is_file():
        fichiers.append(racine / cible.env_propre)
    return [*fichiers, cible.fichier_env]


def env_processus(cible: Cible, o: Outils) -> dict[str, str]:
    """Ce que la commande Compose reçoit de plus dans son environnement : pour le
    staging, le chemin du fichier de mots de passe de la préproduction."""
    if cible.nom == "staging":
        return {"API_MODELE_PGPASS": o.environ.get(VARIABLE_PGPASS_PREPROD, "")}
    return {}


def commande_compose(
    cible: Cible,
    o: Outils,
    *action: str,
    fichiers_compose: tuple[Path, ...] | None = None,
) -> Commande:
    """`docker compose` pour la cible : fichiers d'environnement, projet, fichiers
    Compose (ceux de l'arbre, ou ceux d'une étiquette extraits ailleurs — les chemins
    relatifs restent ceux du dépôt, par `--project-directory`)."""
    argv: list[str] = ["docker", "compose"]
    for f in fichiers_env(cible, o.racine):
        argv += ["--env-file", str(f)]
    argv += ["-p", cible.projet]
    if fichiers_compose is None:
        fichiers = tuple(o.racine / c for c in cible.compose)
    else:
        fichiers = fichiers_compose
        argv += ["--project-directory", str(o.racine / "11_api_modele")]
    for f in fichiers:
        argv += ["-f", str(f)]
    return Commande(
        f"compose.{cible.nom}.{action[0]}",
        (*argv, *action),
        dossier=o.racine,
        env=env_processus(cible, o),
    )
