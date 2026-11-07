"""Les gestes de Max, en préproduction : promouvoir, revenir, lire l'état.

DÉPLOYER = PROMOUVOIR PUIS RECHARGER. L'API charge les fragments de l'encodage en
service au démarrage : une ligne au journal ne change ce qu'elle sert qu'après un
lever. Le déploiement est fait quand `/v1/info` désigne l'encodage promu ; la
vérification suit, selon l'encodage : 3 → équivalence de l'API contre `a3b7f0e0`
(69 / 69) ; 2 → mesure 8 rejouée contre `28aa0e60` (104 / 104).

UNE INSTANCE À LA FOIS SUR LA CARTE. L'ordre : préalables (aucune écriture) → ligne au
journal → instances du 09 arrêtées → API de préproduction levée (son propre service
d'embedding) → `/v1/sante`, `/v1/info` → API arrêtée → instance du 09 levée pour la
vérification → vérification → instance arrêtée. L'API de préproduction réutilise le nom
de conteneur et le sous-réseau de la pile : pile arrêtée, sinon refus.

LA CLÉ DE L'API est lue dans `API_MODELE_CLES` du `.env` du 11, envoyée dans
`X-API-Key`, jamais affichée ni écrite. Le fichier de mots de passe du conteneur est la
copie à part de Max : son chemin vient de `PREPROD_PGPASS_CONTENEUR`, jamais du dépôt,
et le fichier n'est jamais ouvert ici.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chaine import etapes
from chaine.codes import Arret, Code
from chaine.environnement import (
    RACINE_MODULE,
    Cible,
    Commande,
    chemin_lisible,
    cible,
    lignes,
    pile_en_marche,
    pousse,
    psql,
    uv_run,
)
from chaine.executer import Outils

COMPOSE_09 = etapes.M09 / "compose.yml"
ENV_11 = etapes.M11 / ".env"
PORT_PREPROD = 8011
VARIABLE_PGPASS = "PREPROD_PGPASS_CONTENEUR"
JOURNAL = RACINE_MODULE / ".journal" / "execution.jsonl"
DELAI_SANTE_S = 300.0
DELAI_INSTANCE_S = 180.0


@dataclass(frozen=True)
class Verification:
    nature: str  # « api » ou « rejeu »
    run: str
    libelle: str
    mesure: int = 8


VERIFICATIONS: dict[int, Verification] = {
    3: Verification(
        "api", "a3b7f0e025e4476ea4b60e7d0498876b", "équivalence de l'API, 69 / 69"
    ),
    2: Verification(
        "rejeu", "28aa0e60e8014b078bf48f5c7c2ce78a", "mesure 8 rejouée, 104 / 104"
    ),
}


# --------------------------------------------------------------------------- #
#  Les préalables
# --------------------------------------------------------------------------- #


def cle_api(fichier: Path | None = None) -> str:
    """Un secret du trousseau `API_MODELE_CLES` (`nom:secret,…`) — jamais affiché.
    Le chemin est lu à l'appel (jamais figé à la définition) : un test le remplace."""
    fichier = fichier or ENV_11
    try:
        texte = fichier.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise Arret(Code.PREREQUIS, "le .env du module 11 est absent") from exc
    for ligne in texte.splitlines():
        cle, _, valeur = ligne.strip().removeprefix("export ").partition("=")
        if cle.strip() == "API_MODELE_CLES":
            paires = valeur.strip().strip("'\"").split(",")
            secrets = [p.partition(":")[2].strip() for p in paires if ":" in p]
            if secrets and secrets[0]:
                return secrets[0]
    raise Arret(Code.PREREQUIS, "API_MODELE_CLES absente du .env du module 11")


def pgpass_conteneur(environ: Mapping[str, str]) -> str:
    chemin = environ.get(VARIABLE_PGPASS, "")
    if not chemin or not Path(chemin).is_file():
        raise Arret(
            Code.PREREQUIS,
            f"{VARIABLE_PGPASS} : le chemin de la copie à part du fichier de mots de"
            " passe"
            " du conteneur (point d'arrêt 2 b) — absente, ou le fichier n'existe pas",
        )
    return chemin


def prealables_communs(environ: Mapping[str, str], o: Outils) -> tuple[Cible, str, str]:
    cb = cible(environ)
    pile = pile_en_marche(o.lanceur)
    if pile:
        raise Arret(
            Code.PREREQUIS,
            "la pile du monitorage tourne : make monitorage-arret d'abord (l'API de"
            " préproduction reprend son nom de conteneur et son sous-réseau)",
        )
    return cb, pgpass_conteneur(environ), cle_api()


# --------------------------------------------------------------------------- #
#  Les instances, l'API de préproduction
# --------------------------------------------------------------------------- #


def arreter_instances() -> Commande:
    return Commande(
        "09.instances.arret",
        (
            "docker",
            "compose",
            "-f",
            str(COMPOSE_09),
            "--profile",
            "bge-m3",
            "--profile",
            "embeddinggemma",
            "down",
        ),
        dossier=etapes.M09,
    )


def lever_instance(instance: str) -> Commande:
    return Commande(
        f"09.instance.{instance}",
        ("docker", "compose", "-f", str(COMPOSE_09), "--profile", instance, "up", "-d"),
        dossier=etapes.M09,
    )


def compose_api(cb: Cible, pgpass: str, *action: str) -> Commande:
    return Commande(
        f"11.api.{action[0]}",
        (
            "docker",
            "compose",
            "-p",
            etapes.PROJET_PREPROD,
            "-f",
            str(etapes.COMPOSE_11),
            *action,
        ),
        dossier=etapes.M11,
        env={
            "API_MODELE_DSN_CONTENEUR": f"postgresql://modele_api@host.docker.internal:5432/{cb.base}",
            "API_MODELE_PGPASS": pgpass,
            "API_MODELE_PORT_PUBLIE": str(PORT_PREPROD),
        },
    )


def attendre(
    o: Outils, url: str, delai: float, entetes: Mapping[str, str] | None = None
) -> str:
    debut = o.horloge()
    while True:
        statut, corps = o.http(url, entetes or {})
        if statut == 200:
            return corps
        if o.horloge() - debut > delai:
            raise Arret(
                Code.PACKAGING,
                f"{url.split('/', 3)[-1]} ne répond pas après {int(delai)} s",
            )
        o.dormir(3.0)


def deployer(
    o: Outils, cb: Cible, pgpass: str, cle: str, encodage: int, image: str | None
) -> dict[str, Any]:
    """Instances arrêtées, API de préproduction levée, `/v1/info` lu, API arrêtée."""
    o.lanceur(arreter_instances())
    if image is not None:
        lu = o.lanceur(etapes.empreinte_image()).stdout.strip()
        if lu != image:
            raise Arret(
                Code.PREREQUIS,
                f"l'image locale ({lu[:19]}…) n'est pas celle du rapport"
                f" ({image[:19]}…)",
            )
    s = o.lanceur(compose_api(cb, pgpass, "up", "-d", "--no-build"))
    try:
        if s.code != 0:
            raise Arret(
                Code.PACKAGING, "l'API de préproduction ne se lève pas", s.stderr
            )
        sante = attendre(o, f"http://127.0.0.1:{PORT_PREPROD}/v1/sante", DELAI_SANTE_S)
        statut, corps = o.http(
            f"http://127.0.0.1:{PORT_PREPROD}/v1/info", {"X-API-Key": cle}
        )
        info = etapes.json_de(corps) or {}
        if statut != 200 or info.get("encodage_id") != encodage:
            raise Arret(
                Code.PACKAGING,
                f"/v1/info désigne l'encodage {info.get('encodage_id')}"
                f" (statut {statut}), pas {encodage}",
            )
    finally:
        o.lanceur(compose_api(cb, pgpass, "down"))
    return {
        "sante": (etapes.json_de(sante) or {}).get("statut"),
        "info": {
            k: info.get(k)
            for k in ("encodage_id", "corpus_id", "nb_fragments", "version_api")
        },
        "image": o.lanceur(etapes.empreinte_image()).stdout.strip(),
    }


def verifier(o: Outils, cb: Cible, encodage: int) -> dict[str, Any]:
    """La vérification de l'encodage déployé, sur l'instance du 09, levée puis
    arrêtée."""
    v = VERIFICATIONS[encodage]
    o.lanceur(lever_instance("embeddinggemma"))
    try:
        attendre(
            o,
            f"http://127.0.0.1:{etapes.port_instance('embeddinggemma')}/health",
            DELAI_INSTANCE_S,
        )
        if v.nature == "api":
            s = o.lanceur(
                Commande(
                    "11.egalite",
                    uv_run(
                        "pytest",
                        "-q",
                        "-p",
                        "no:cacheprovider",
                        "tests/test_egalite_banc.py",
                        extra_dev=True,
                    ),
                    dossier=etapes.M11,
                    env={
                        "API_MODELE_EGALITE_DSN": cb.dsn("modele_api"),
                        "API_MODELE_EGALITE_EXIGEE": "1",
                    },
                )
            )
            tenue = s.code == 0
        else:
            travail = o.travail / "verification"
            travail.mkdir(parents=True, exist_ok=True)
            s = o.lanceur(
                Commande(
                    "10.executer.rejeu",
                    uv_run(
                        "python",
                        "-m",
                        "mesures_recherche.executer",
                        str(v.mesure),
                        "--encodage",
                        str(encodage),
                        "--rejeu-de",
                        str(etapes.piece_du_run(v.run)),
                        "--sortie",
                        str(travail / f"rejeu_{encodage}.json"),
                    ),
                    dossier=etapes.M10,
                    env={
                        "DATABASE_URL": cb.dsn("postgres"),
                        "MLFLOW_DISABLE_AGENT_HINT": "1",
                    },
                )
            )
            tenue = etapes.lire_rejeu(s)
    finally:
        o.lanceur(arreter_instances())
    if not tenue:
        raise Arret(
            Code.MESURE,
            f"vérification non tenue : {v.libelle} attendue (référence {v.run[:8]})",
            s.stderr,
        )
    return {"verification": v.libelle, "reference": v.run, "tenue": True}


def journaliser(o: Outils, entree: dict[str, Any], journal: Path | None = None) -> None:
    """Une ligne au journal d'exécution (hors dépôt, ignoré par git)."""
    journal = journal or JOURNAL
    journal.parent.mkdir(parents=True, exist_ok=True)
    ligne = {"date": o.maintenant().strftime("%Y-%m-%dT%H:%M:%SZ")} | entree
    with journal.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ligne, ensure_ascii=False, default=str) + "\n")


def journal_des_promotions(cb: Cible, *arguments: str) -> Commande:
    return Commande(
        f"10.journal.{arguments[0]}",
        uv_run("python", "-m", "mesures_recherche.journal_promotions", *arguments),
        dossier=etapes.M10,
        env={"DATABASE_URL": cb.dsn("postgres"), "MLFLOW_DISABLE_AGENT_HINT": "1"},
        ecrit="une ligne au journal des promotions (préproduction)"
        if arguments[0] != "etat"
        else "rien",
    )


# --------------------------------------------------------------------------- #
#  Les trois gestes
# --------------------------------------------------------------------------- #


def fichier_livraison(nom: str, dossier: Path) -> Path:
    base = nom.removesuffix(".json").removesuffix(".md")
    return dossier / f"{Path(base).name}.json"


def promouvoir(
    livraison: str, motif: str, environ: Mapping[str, str], o: Outils
) -> Code:
    """La promotion de Max : le rapport commité et poussé, au verdict « promotion
    proposée » ; puis l'API de préproduction et la vérification."""
    if not motif.strip():
        raise Arret(
            Code.PREREQUIS, 'le motif est obligatoire (MOTIF="…"), et il est de Max'
        )
    js = fichier_livraison(livraison, o.livraisons)
    if not js.is_file():
        raise Arret(Code.PREREQUIS, f"rapport de livraison introuvable : {js.name}")
    d: dict[str, Any] = json.loads(js.read_text(encoding="utf-8"))
    commit = pousse(o.lanceur, js)
    pousse(o.lanceur, js.with_suffix(".md"))
    if d.get("verdict") != "promotion proposée":
        raise Arret(
            Code.PREREQUIS,
            f"verdict « {d.get('verdict')} » : la chaîne ne promeut que sur « promotion"
            " proposée » (une dérogation passe par le module 10, pas par la chaîne)",
        )
    encodage, run = d.get("encodage"), d.get("run")
    if not isinstance(encodage, int) or not run or encodage not in VERIFICATIONS:
        raise Arret(
            Code.PREREQUIS, f"encodage {encodage} : aucune vérification connue pour lui"
        )
    cb, pgpass, cle = prealables_communs(environ, o)
    complet = (
        f"{motif.strip()} — rapport 14_chaine/livraisons/{js.stem}.md ({commit[:12]})"
    )
    s = o.lanceur(
        journal_des_promotions(
            cb,
            "promouvoir",
            "--encodage",
            str(encodage),
            "--run",
            str(run),
            "--motif",
            complet,
            "--par",
            "Max",
        )
    )
    ligne = etapes.json_de(s.stdout)
    if s.code != 0 or ligne is None:
        raise Arret(Code.PREREQUIS, "le module 10 refuse la promotion", s.stderr)
    o.afficher(
        f"Journal : promotion de l'encodage {encodage} inscrite (préproduction)."
    )
    resultat = deployer(o, cb, pgpass, cle, encodage, d.get("image"))
    o.afficher(f"API de préproduction : /v1/info désigne l'encodage {encodage}.")
    resultat |= verifier(o, cb, encodage)
    o.afficher(f"Vérification tenue : {resultat['verification']}.")
    journaliser(
        o,
        {"geste": "promouvoir", "livraison": js.stem, "encodage": encodage, "run": run}
        | resultat,
    )
    return Code.PROMOTION_PROPOSEE


def revenir(motif: str, environ: Mapping[str, str], o: Outils) -> Code:
    """Le retour arrière de Max : une ligne au journal, puis l'API et la
    vérification."""
    if not motif.strip():
        raise Arret(
            Code.PREREQUIS, 'le motif est obligatoire (MOTIF="…"), et il est de Max'
        )
    cb, pgpass, cle = prealables_communs(environ, o)
    s = o.lanceur(
        journal_des_promotions(cb, "revenir", "--motif", motif.strip(), "--par", "Max")
    )
    r = etapes.json_de(s.stdout)
    apres: dict[str, Any] = (r or {}).get("apres") or {}
    encodage = apres.get("encodage_id")
    if s.code != 0 or not isinstance(encodage, int):
        raise Arret(Code.PREREQUIS, "le module 10 refuse le retour arrière", s.stderr)
    o.afficher(
        f"Journal : retour arrière inscrit, encodage {encodage} en service"
        " (préproduction)."
    )
    resultat = deployer(o, cb, pgpass, cle, encodage, None)
    o.afficher(f"API de préproduction : /v1/info désigne l'encodage {encodage}.")
    if encodage not in VERIFICATIONS:
        raise Arret(Code.MESURE, f"encodage {encodage} : aucune vérification connue")
    resultat |= verifier(o, cb, encodage)
    o.afficher(f"Vérification tenue : {resultat['verification']}.")
    journaliser(o, {"geste": "revenir", "encodage": encodage} | resultat)
    return Code.PROMOTION_PROPOSEE


def etat(environ: Mapping[str, str], o: Outils) -> Code:
    """Le journal des promotions de la préproduction, motifs compris, et les trois
    dernières livraisons."""
    cb = cible(environ)
    s = o.lanceur(journal_des_promotions(cb, "etat"))
    r = etapes.json_de(s.stdout)
    if s.code != 0 or r is None:
        raise Arret(Code.PREREQUIS, "journal des promotions illisible", s.stderr)
    motifs = dict(
        ligne.split("\t", 1)
        for ligne in lignes(
            o.lanceur(
                psql(
                    "psql.motifs",
                    cb.dsn("verif_lecture"),
                    sql="SELECT promotion_id, motif FROM bench.promotions ORDER BY 1",
                )
            )
        )
        if "\t" in ligne
    )
    en_service: dict[str, Any] = r.get("en_service") or {}
    o.afficher(
        f"Préproduction ({cb.base}) — encodage en service :"
        f" {en_service.get('encodage_id')}"
    )
    for ligne in r.get("journal", []):
        o.afficher(
            f"  n° {ligne['promotion_id']} · {str(ligne['decide_le'])[:19]}"
            f" · {ligne['decision']} · encodage {ligne['encodage_id']}"
            f" (précédent {ligne['encodage_precedent_id']})"
            f" · run {str(ligne['run_id'] or '—')[:8]} · {ligne['decide_par']}"
            f" · {motifs.get(str(ligne['promotion_id']), '')}"
        )
    for js in sorted(o.livraisons.glob("*.json"), reverse=True)[:3]:
        d = json.loads(js.read_text(encoding="utf-8"))
        o.afficher(
            f"  livraison {chemin_lisible(js)} : {d.get('verdict') or '—'},"
            f" code {d.get('code')}"
        )
    return Code.PROMOTION_PROPOSEE
