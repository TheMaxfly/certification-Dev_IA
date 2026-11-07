"""Les étapes de la chaîne : ce qu'elles font, la commande existante qu'elles appellent,
quand elles s'exécutent, le code de leur arrêt — et la lecture de chaque sortie.

Aucune logique des modules 05, 09, 10, 11 n'est recopiée : chaque étape lance la
commande du module, dans son environnement (`uv run` dans le module), et lit son code
de sortie et sa sortie utile, dans le format lisible par machine du module quand il
existe (JSON). Sinon, la lecture d'un texte vit dans une seule fonction, testée sur la
sortie réelle.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chaine.candidat import Candidat
from chaine.codes import Arret, Code
from chaine.environnement import (
    RACINE_DEPOT,
    RACINE_MODULE,
    VARIABLE_DOSSIER,
    Cible,
    Commande,
    Sortie,
    chemin_lisible,
    uv_run,
)

M05 = RACINE_DEPOT / "05_nettoyage_agregation_bdd"
M09 = RACINE_DEPOT / "09_service_embedding"
M10 = RACINE_DEPOT / "10_mesures_recherche"
M11 = RACINE_DEPOT / "11_api_modele"
COMPOSE_11 = M11 / "docker-compose.yml"
#: Le Compose de la préproduction, posé sur celui du 11 : l'image du service `api`.
COMPOSE_PREPROD = RACINE_MODULE / "compose.preprod.yml"
VARIABLE_IMAGE = "CHAINE_IMAGE_PREPROD"
PROJET_PREPROD = "api-modele-preprod"
EXPERIENCE = "E3-chaine-preprod"
MLFLOW = M10 / "mlflow"


@dataclass(frozen=True)
class Etape:
    numero: int
    nom: str
    tache: str
    appelle: str
    condition: str
    arret: tuple[Code, ...]
    produit: str


ETAPES: tuple[Etape, ...] = (
    Etape(
        0,
        "Préalables",
        "cible = préproduction ; dossier d'export de la préproduction ; arbre propre et"
        " poussé ; candidat commité, inchangé, conforme ; pile du monitorage arrêtée ;"
        " instance en marche, /info conforme ; état de départ de la préproduction",
        "git, docker, /info de l'instance, psql",
        "toujours",
        (Code.PREREQUIS, Code.CANDIDAT_INVALIDE),
        "—",
    ),
    Etape(
        1,
        "Tester les données",
        "jeu gelé ; empreintes du corpus contre la référence ; corpus reconstruit à"
        " blanc contre sa règle, pseudonymes compris",
        "psql (empreintes_corpus.sql) ; 05 corpus.construire_v2 --dry-run",
        "toujours",
        (Code.DONNEES,),
        "lignes du rapport",
    ),
    Etape(
        2,
        "Construire",
        "la nouvelle version du corpus",
        "05 corpus.construire_v2",
        "le candidat déclare une nouvelle version",
        (Code.CONSTRUCTION,),
        "corpus",
    ),
    Etape(
        3,
        "Encoder",
        "nouvel encodage du corpus, sur la carte ; encodage_cree et encodage_id lus"
        " dans le bilan",
        "09 service_embedding.encodage",
        "le candidat demande un nouvel encodage",
        (Code.ENCODAGE,),
        "encodage inscrit, non promu",
    ),
    Etape(
        4,
        "Tester le modèle",
        "échantillon exporté dans le dossier de la préproduction, puis test"
        " d'équivalence (verdict lu dans le JSON)",
        "09 service_embedding.equivalence exporter, comparer",
        "toujours",
        (Code.TEST_MODELE,),
        "cosinus minimal, verdict",
    ),
    Etape(
        5,
        "Évaluer",
        "mesure sur le jeu gelé ; rejeu de la référence si le candidat en déclare une",
        "10 mesures_recherche.executer --enregistrer ; --rejeu-de",
        "toujours",
        (Code.MESURE,),
        "run MLflow, eval_runs, eval_mesures (préproduction)",
    ),
    Etape(
        6,
        "Valider",
        "la règle commitée avant la mesure",
        "10 mesures_recherche.promotion --run",
        "toujours",
        (Code.REFUS_REGLE, Code.PAS_DE_VERDICT),
        "verdict",
    ),
    Etape(
        7,
        "Packaging",
        "image de l'API construite au commit, empreinte notée",
        "11 docker compose build api",
        "verdict « promotion proposée »",
        (Code.PACKAGING,),
        "empreinte d'image",
    ),
    Etape(
        8,
        "Rapport et arrêt",
        "rapport de livraison versionné, pièce MLflow ; la chaîne s'arrête là",
        "14 ; mlflow artifacts log-artifact (environnement du 10)",
        "toujours, même après un arrêt",
        (),
        "livraisons/…md et .json, code de sortie",
    ),
)


# --------------------------------------------------------------------------- #
#  Les commandes existantes
# --------------------------------------------------------------------------- #


def dry_run(c: Candidat, cible: Cible, travail: Path) -> Commande:
    del c  # le seul corpus jugé est le v2 (candidat.CORPUS)
    return Commande(
        "05.construire_v2.dry_run",
        uv_run(
            "python",
            "-m",
            "corpus.construire_v2",
            "--dry-run",
            "--rapports",
            str(travail),
        ),
        dossier=M05,
        env={"DATABASE_URL": cible.dsn("postgres")},
        ecrit="rien (ROLLBACK) ; un rapport dans le dossier de travail",
    )


def construire(c: Candidat, cible: Cible, travail: Path) -> Commande:
    del c
    return Commande(
        "05.construire_v2",
        uv_run("python", "-m", "corpus.construire_v2", "--rapports", str(travail)),
        dossier=M05,
        env={"DATABASE_URL": cible.dsn("postgres")},
        ecrit="la version du corpus (préproduction)",
    )


def encoder(c: Candidat, cible: Cible, travail: Path) -> Commande:
    return Commande(
        "09.encodage",
        uv_run(
            "python",
            "-m",
            "service_embedding.encodage",
            c.instance,
            "--corpus",
            c.corpus,
            "--sortie",
            str(travail / "encodage.json"),
        ),
        dossier=M09,
        env={"DATABASE_URL": cible.dsn("postgres")},
        ecrit=(
            f"un encodage {c.modele} du corpus {c.corpus} et ses vecteurs"
            " (préproduction)"
            if c.modele
            else f"rien (le candidat réutilise l'encodage {c.encodage})"
        ),
    )


def exporter(cible: Cible, encodage: int | str) -> Commande:
    return Commande(
        "09.equivalence.exporter",
        uv_run(
            "python",
            "-m",
            "service_embedding.equivalence",
            "exporter",
            "--encodage",
            str(encodage),
        ),
        dossier=M09,
        env=cible.env_ecriture(),
        ecrit=f"l'échantillon, dans {chemin_lisible(cible.dossier_export)}",
    )


def comparer(cible: Cible, encodage: int | str) -> Commande:
    return Commande(
        "09.equivalence.comparer",
        uv_run(
            "python",
            "-m",
            "service_embedding.equivalence",
            "comparer",
            "--encodage",
            str(encodage),
        ),
        dossier=M09,
        env={VARIABLE_DOSSIER: str(cible.dossier_export)},
    )


def env_mlflow() -> dict[str, str]:
    return {
        "MLFLOW_TRACKING_URI": f"sqlite:///{MLFLOW / 'mlflow.db'}",
        "MLFLOW_DISABLE_AGENT_HINT": "1",
    }


def mesurer(c: Candidat, cible: Cible, encodage: int | str, travail: Path) -> Commande:
    return Commande(
        "10.executer",
        uv_run(
            "python",
            "-m",
            "mesures_recherche.executer",
            str(c.mesure),
            "--encodage",
            str(encodage),
            "--enregistrer",
            "--experience",
            EXPERIENCE,
            "--spec",
            f"E3 étape 6 — chaîne, {c.nom}",
            "--sortie",
            str(travail / "mesure.json"),
        ),
        dossier=M10,
        env={"DATABASE_URL": cible.dsn("postgres"), "MLFLOW_DISABLE_AGENT_HINT": "1"},
        ecrit=f"un run MLflow ({EXPERIENCE}), 1 ligne eval_runs, 104 eval_mesures"
        " (préproduction)",
    )


def piece_du_run(run: str) -> Path:
    """La pièce `resultat.json` d'un run du banc, dans le magasin MLflow du 10."""
    return MLFLOW / "pieces" / run / "artifacts" / "resultat.json"


def rejouer(
    c: Candidat, cible: Cible, encodage: int | str, run: str, travail: Path
) -> Commande:
    return Commande(
        "10.executer.rejeu",
        uv_run(
            "python",
            "-m",
            "mesures_recherche.executer",
            str(c.mesure),
            "--encodage",
            str(encodage),
            "--rejeu-de",
            str(piece_du_run(run)),
            "--sortie",
            str(travail / "rejeu.json"),
        ),
        dossier=M10,
        env={"DATABASE_URL": cible.dsn("postgres"), "MLFLOW_DISABLE_AGENT_HINT": "1"},
    )


def juger(cible: Cible, run: str, travail: Path) -> Commande:
    return Commande(
        "10.promotion",
        uv_run(
            "python",
            "-m",
            "mesures_recherche.promotion",
            "--run",
            run,
            "--sortie",
            str(travail / "verdict.json"),
        ),
        dossier=M10,
        env={"DATABASE_URL": cible.dsn("postgres"), "MLFLOW_DISABLE_AGENT_HINT": "1"},
    )


def image_de_l_api() -> str:
    """L'étiquette de l'image du service `api` du Compose du 11 (lue, pas recopiée)."""
    trouve = re.search(
        r"^\s+image:\s*(api-modele:\S+)\s*$", COMPOSE_11.read_text("utf-8"), re.M
    )
    if trouve is None:
        raise Arret(
            Code.PACKAGING, "image du service api introuvable dans le Compose du 11"
        )
    return trouve.group(1)


def image_de_preproduction() -> str:
    """L'étiquette de l'image de l'API de préproduction : celle du Compose du 11,
    suffixée `-preprod`. La chaîne ne construit ni ne lève jamais l'étiquette de la
    production (écart 04-4 de l'étape 6 : une construction sous ce nom l'avait
    déplacée)."""
    return f"{image_de_l_api()}-preprod"


def env_image() -> dict[str, str]:
    """La variable que lit `compose.preprod.yml` : l'image de la préproduction."""
    return {VARIABLE_IMAGE: image_de_preproduction()}


def compose_preprod(*action: str) -> tuple[str, ...]:
    """`docker compose` sur le projet de la préproduction : le Compose du 11, et
    par-dessus `compose.preprod.yml`, qui remplace l'image du service `api`."""
    return (
        "docker",
        "compose",
        "-p",
        PROJET_PREPROD,
        "-f",
        str(COMPOSE_11),
        "-f",
        str(COMPOSE_PREPROD),
        *action,
    )


#: Une étape de construction de `docker build --progress plain` (hors `FROM` et hors
#: étapes internes), et une étape reprise du cache.
ETAPE_DE_CONSTRUCTION = re.compile(r"^#(\d+) \[[^\]]*\d+/\d+\] (?!FROM )", re.M)
ETAPE_EN_CACHE = re.compile(r"^#(\d+) CACHED\s*$", re.M)


def cache_de(sortie: str) -> str:
    """Ce que la construction a repris du cache, dit en mots (étape 7 du rapport)."""
    etapes = set(ETAPE_DE_CONSTRUCTION.findall(sortie))
    if not etapes:
        return "inconnu (aucune étape lue dans la sortie)"
    en_cache = etapes & set(ETAPE_EN_CACHE.findall(sortie))
    if en_cache == etapes:
        return (
            f"toutes les étapes reprises du cache ({len(etapes)}), même contenu"
            " (l'empreinte peut changer : métadonnées)"
        )
    n = len(en_cache)
    pluriel = "s" if n > 1 else ""
    return f"{n} étape{pluriel} sur {len(etapes)} reprise{pluriel} du cache"


def construire_image() -> Commande:
    return Commande(
        "11.image",
        compose_preprod("--progress", "plain", "build", "api"),
        dossier=M11,
        env=env_image(),
        ecrit=f"l'image {image_de_preproduction()} (locale) ; l'étiquette de la"
        " production n'est pas touchée",
    )


def empreinte_image() -> Commande:
    return Commande(
        "docker.image",
        (
            "docker",
            "image",
            "inspect",
            image_de_preproduction(),
            "--format",
            "{{.Id}}",
        ),
    )


def attacher(md: Path, run: str) -> Commande:
    return Commande(
        "10.mlflow.log_artifact",
        uv_run("mlflow", "artifacts", "log-artifact", "-l", str(md), "-r", run),
        dossier=M10,
        env=env_mlflow(),
        ecrit="une pièce du run MLflow",
    )


# --------------------------------------------------------------------------- #
#  La lecture des sorties — une fonction par sortie, testée sur la sortie réelle
# --------------------------------------------------------------------------- #


def json_de(texte: str) -> dict[str, Any] | None:
    """Le dernier objet JSON d'une sortie : une ligne seule (executer, construire),
    ou le texte entier (comparer, promotion, en plusieurs lignes)."""
    try:
        entier = json.loads(texte)
        if isinstance(entier, dict):
            return entier  # type: ignore[return-value]
    except json.JSONDecodeError:
        pass
    for ligne in reversed(texte.strip().splitlines()):
        try:
            objet = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        if isinstance(objet, dict):
            return objet  # type: ignore[return-value]
    return None


def lire_dry_run(s: Sortie) -> dict[str, Any]:
    """`construire_v2 --dry-run` : « contrôles verts — ANNULÉ … » puis une ligne JSON ;
    le corpus est conforme si aucune écriture n'aurait eu lieu."""
    resume = json_de(s.stdout)
    if s.code != 0 or resume is None or "contrôles verts" not in s.stdout:
        raise Arret(Code.DONNEES, "reconstruction à blanc du corpus en échec", s.stderr)
    ecritures: dict[str, Any] = resume.get("ecritures", {})
    ecarts = {k: v for k, v in ecritures.items() if v}
    if ecarts:
        raise Arret(
            Code.DONNEES,
            f"le corpus reconstruit à blanc diffère du corpus clos : {sorted(ecarts)}",
        )
    return {"fragments": resume.get("fragments"), "documents": resume.get("documents")}


def lire_bilan(fichier: Path) -> dict[str, Any]:
    """Le bilan de l'encodeur (`--sortie`) : son code de sortie vaut 0 même quand il
    n'a rien créé ; seul le bilan le dit."""
    try:
        bilan: dict[str, Any] = json.loads(fichier.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise Arret(Code.ENCODAGE, "bilan de l'encodeur absent ou illisible") from exc
    if "arret" in bilan:
        raise Arret(Code.ENCODAGE, f"encodage arrêté : {bilan['arret']}")
    if not bilan.get("encodage_cree"):
        raise Arret(
            Code.ENCODAGE,
            f"encodage_cree: false — l'encodeur n'a rien créé (encodage"
            f" {bilan.get('encodage_id')} retrouvé) : un nouvel encodage était demandé",
        )
    if not isinstance(bilan.get("encodage_id"), int):
        raise Arret(Code.ENCODAGE, "bilan sans encodage_id")
    if not bilan.get("termine") or bilan.get("vecteurs_ecrits") != bilan.get(
        "fragments_corpus"
    ):
        raise Arret(
            Code.ENCODAGE,
            f"encodage incomplet : {bilan.get('vecteurs_ecrits')} vecteurs pour"
            f" {bilan.get('fragments_corpus')} fragments",
        )
    return bilan


MARQUES_PREALABLE = (
    "ServiceNonConforme",
    "ErreurService",
    "FileNotFoundError",
    "Connection refused",
    "ConnectionRefusedError",
    "refus :",
)


def lire_equivalence(s: Sortie) -> dict[str, Any]:
    """`comparer` : le verdict se lit dans le JSON. Son code 1 vaut pour un verdict
    « echec » ET pour toute exception : sans JSON, 20 si un préalable manque, 90
    sinon."""
    r = json_de(s.stdout)
    if r is not None and "verdict" in r:
        if r["verdict"] == "echec":
            raise Arret(
                Code.TEST_MODELE,
                f"équivalence sous 0,99 : cosinus minimal {r.get('cos_min')}",
            )
        return r
    if any(m in s.stderr for m in MARQUES_PREALABLE):
        raise Arret(
            Code.PREREQUIS,
            "test d'équivalence impossible : un préalable manque",
            s.stderr,
        )
    raise Arret(
        Code.INTERNE,
        f"test d'équivalence : sortie inattendue (code {s.code})",
        s.stderr,
    )


def lire_mesure(s: Sortie, encodage: int) -> dict[str, Any]:
    r = json_de(s.stdout)
    if s.code != 0 or r is None or not r.get("run_id"):
        raise Arret(Code.MESURE, f"mesure en échec (code {s.code})", s.stderr)
    if r.get("encodage_id") != encodage:
        raise Arret(
            Code.MESURE,
            f"la mesure a lu l'encodage {r.get('encodage_id')}, pas {encodage}",
        )
    return r


def lire_rejeu(s: Sortie) -> bool:
    r = json_de(s.stdout)
    rejeu: dict[str, Any] = (r or {}).get("rejeu") or {}
    if s.code not in (0, 1) or "identiques" not in rejeu:
        raise Arret(
            Code.MESURE, f"rejeu de la référence en échec (code {s.code})", s.stderr
        )
    return bool(rejeu["identiques"])


def lire_verdict(s: Sortie) -> dict[str, Any]:
    """`promotion` : un verdict en JSON (code 0) ; « pas de verdict » n'a pas de sortie
    machine — l'exception `VerdictRefuse`, sur la sortie d'erreur (code 1)."""
    if s.code == 0:
        r = json_de(s.stdout)
        if r is not None and r.get("verdict") in (
            "promotion proposée",
            "refus proposé",
        ):
            return r
    if s.code == 1 and "VerdictRefuse" in s.stderr:
        motif = next(
            (
                ligne.split("VerdictRefuse:", 1)[1].strip()
                for ligne in reversed(s.stderr.splitlines())
                if "VerdictRefuse:" in ligne
            ),
            "la règle refuse de juger",
        )
        raise Arret(Code.PAS_DE_VERDICT, f"pas de verdict : {motif}")
    raise Arret(Code.INTERNE, f"règle : sortie inattendue (code {s.code})", s.stderr)


def conditions_manquantes(verdict_complet: dict[str, Any]) -> list[str]:
    """Les conditions de la règle qui manquent, nommées avec leurs nombres (lues dans
    la sortie de la règle, `--sortie`)."""
    regle: dict[str, Any] = verdict_complet.get("regle", {})
    nouveau: dict[str, Any] = verdict_complet.get("nouveau", {})
    manquent: list[str] = []
    for cle, seuil in (
        ("global", "global_min"),
        ("reconnaissance", "reconnaissance_min"),
    ):
        if cle in nouveau and seuil in regle and nouveau[cle] < regle[seuil]:
            manquent.append(f"{cle} {nouveau[cle]} < {regle[seuil]}")
    return manquent


def empreinte_du_jeu() -> str:
    """L'empreinte du jeu gelé, lue dans la configuration du banc (module 10)."""
    config = tomllib.loads(
        (M10 / "config" / "mesures.toml").read_text(encoding="utf-8")
    )
    return str(config["jeu"]["empreinte"])


def port_instance(instance: str) -> int:
    """Le port de l'instance, lu dans sa configuration (module 09, versionnée)."""
    texte = (M09 / "config" / f"{instance}.env").read_text(encoding="utf-8")
    trouve = re.search(r"^PORT=(\d+)\s*$", texte, re.M)
    if trouve is None:
        raise Arret(Code.PREREQUIS, f"port de l'instance {instance} introuvable")
    return int(trouve.group(1))
