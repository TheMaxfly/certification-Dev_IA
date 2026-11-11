"""`livrer sante --cible <nom>` : une ligne par contrôle, code 1 si l'un manque.

LES ATTENDUS viennent de l'étiquette déployée : versions des `pyproject` (02, 11, 12),
présence du catalogue dans son Compose, comptes servis par la pile (son Compose transmet
`APPLICATION_BASE_DSN`). Ainsi la santé d'un retour arrière à `chaine-livraison` vérifie
l'API 1.4.0 et l'application 0.1.0, sans catalogue ni comptes.

LES CONTRÔLES : les images servies (une par service, à la version attendue) ; `/sante`
de l'application (version, API, base, catalogue, `cles_api`, profil actif) ; `/health`
du catalogue (base, `normalisation` = `fonction`) ; `/v1/sante` et `/v1/info` de l'API ;
l'encodage servi égal à l'encodage en service de la base ; `migrate.py status` (0 en
attente) ; en production, les cibles de Prometheus.

L'API N'EST PAS PUBLIÉE (on l'atteint par l'application) : ses deux routes sont lues
**dans son conteneur** (`docker exec`), par un court programme Python ; `/v1/info`
exige une clé, que ce programme prend dans l'environnement du conteneur — elle n'en sort
pas : `livrer` ne la lit ni ne l'affiche, il ne reçoit que quatre champs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from livraison import depot, images
from livraison.cibles import Cible
from livraison.codes import Arret, Code
from livraison.outils import Commande, Outils, json_objet, lignes, liste, objet

ESSAIS = 3
PAUSE_S = 2.0
PORT_PROMETHEUS = 9090
#: Lu dans le conteneur de l'API ; rend quatre champs de `/v1/info`, jamais la clé.
PROGRAMME_API = """
import json, os, urllib.request as u
def lire(chemin, entetes):
    try:
        q = u.Request("http://127.0.0.1:8010" + chemin, headers=entetes)
        r = u.urlopen(q, timeout=5)
        return r.status, json.load(r)
    except Exception as e:
        return getattr(e, "code", 0), {}
s, sante = lire("/v1/sante", {})
trousseau = os.environ.get("API_MODELE_CLES", "").split(",")
cles = [p.partition(":")[2] for p in trousseau if ":" in p]
i, info = lire("/v1/info", {"X-API-Key": cles[0]}) if cles else (0, {})
champs = ("version_api", "encodage_id", "corpus_id", "nb_fragments")
print(json.dumps({"sante": s, "statut": sante.get("statut"), "info": i,
                  **{k: info.get(k) for k in champs}}))
"""


@dataclass(frozen=True)
class Controle:
    service: str
    objet: str
    attendu: str
    constate: str
    tenu: bool


@dataclass(frozen=True)
class Attendus:
    reference: str
    versions: Mapping[str, str]
    comptes: bool
    cles_api: frozenset[str]

    @property
    def catalogue(self) -> bool:
        return "api-catalogue" in self.versions


def attendus(o: Outils, reference: str, cible: Cible) -> Attendus:
    lues = images.images_de(o, reference)
    compose_12 = depot.fichier_a(o, reference, "12_application/compose.yaml") or ""
    return Attendus(
        reference=reference,
        versions={i.recette.nom: i.version for i in lues},
        comptes="APPLICATION_BASE_DSN" in compose_12,
        # La clé des comptes est exigée en production ; en staging, « une » est admise
        # tant qu'elle n'est pas posée (spec 01, § 2).
        cles_api=frozenset({"deux"} if cible.nom == "production" else {"une", "deux"}),
    )


def controle(service: str, objet: str, attendu: str, constate: object) -> Controle:
    lu = "—" if constate is None else str(constate)
    return Controle(service, objet, attendu, lu, lu == attendu)


def lire_json(o: Outils, url: str) -> tuple[int, dict[str, object]]:
    """Trois essais espacés ; (statut, corps JSON) ou (0, {})."""
    statut, corps = 0, ""
    for essai in range(ESSAIS):
        statut, corps = o.http(url)
        if statut == 200:
            break
        if essai < ESSAIS - 1:
            o.dormir(PAUSE_S)
    return statut, json_objet(corps)


def servies(o: Outils, projet: str) -> dict[str, str]:
    """Service Compose → image servie, pour les conteneurs du projet en marche."""
    s = o.lanceur(
        Commande(
            "docker.servies",
            (
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={projet}",
                "--format",
                '{{.Label "com.docker.compose.service"}}\t{{.Image}}',
            ),
            dossier=o.racine,
        )
    )
    if s.code != 0:
        raise Arret(Code.PREREQUIS, "docker ne répond pas", s.stderr)
    return dict(ligne.split("\t", 1) for ligne in lignes(s) if "\t" in ligne)


def migrations(o: Outils, base: str) -> tuple[int, int]:
    """(appliquées, en attente), par `migrate.py status` ; jamais `up`."""
    s = o.lanceur(
        Commande(
            "migrate.status",
            (
                "uv",
                "run",
                "--locked",
                "--quiet",
                "--no-env-file",
                "python",
                "migrate.py",
                "status",
            ),
            dossier=o.racine / "database",
            env={"DATABASE_URL": f"postgresql://postgres@localhost:5432/{base}"},
        )
    )
    m = re.search(r"(\d+) appliquée\(s\), (\d+) en attente", s.stdout)
    if s.code != 0 or m is None:
        raise Arret(Code.PREREQUIS, f"migrate.py status illisible sur {base}", s.stderr)
    return int(m.group(1)), int(m.group(2))


def encodage_en_service(o: Outils, base: str) -> str | None:
    s = o.lanceur(
        Commande(
            "psql.encodage",
            (
                "psql",
                f"postgresql://verif_lecture@localhost:5432/{base}",
                "-X",
                "-q",
                "-A",
                "-t",
                "-w",
                "-c",
                "SELECT encodage_id FROM bench.v_encodage_en_service",
            ),
            dossier=o.racine,
            env={"PGOPTIONS": "-c default_transaction_read_only=on"},
        )
    )
    lu = s.stdout.strip()
    return lu if s.code == 0 and lu else None


def api_dans_le_conteneur(o: Outils, projet: str) -> dict[str, object]:
    s = o.lanceur(
        Commande(
            "docker.exec.api",
            ("docker", "exec", f"{projet}-api-1", "python", "-c", PROGRAMME_API),
            dossier=o.racine,
        )
    )
    return json_objet(s.stdout) if s.code == 0 else {}


def cibles_prometheus(o: Outils) -> list[tuple[str, str]]:
    statut, corps = lire_json(o, f"http://127.0.0.1:{PORT_PROMETHEUS}/api/v1/targets")
    actives = liste(objet(corps.get("data")).get("activeTargets")) if statut else []
    return [
        (
            str(objet(objet(c).get("labels")).get("job", "?")),
            str(objet(c).get("health")),
        )
        for c in actives
    ]


def verifier(o: Outils, cible: Cible, at: Attendus) -> list[Controle]:
    """Tous les contrôles ; aucun ne s'arrête au premier manque."""
    c: list[Controle] = []
    lues = servies(o, cible.projet)
    for recette in images.RECETTES:
        if recette.nom in at.versions:
            attendue = f"{recette.nom}:{at.versions[recette.nom]}"
            c.append(
                controle(
                    recette.service, "image servie", attendue, lues.get(recette.service)
                )
            )
    port = cible.port_application
    statut, app = lire_json(o, f"http://127.0.0.1:{port}/sante")
    c.append(controle("application", "/sante", "200", statut))
    c.append(
        controle(
            "application",
            "version",
            at.versions.get("application-conseiller", "—"),
            app.get("version"),
        )
    )
    c.append(controle("application", "api", "ok", app.get("api")))
    if at.comptes:
        c.append(controle("application", "base", "ok", app.get("base")))
        lu = app.get("cles_api")
        c.append(
            Controle(
                "application",
                "cles_api",
                " ou ".join(sorted(at.cles_api)),
                "—" if lu is None else str(lu),
                lu in at.cles_api,
            )
        )
        profil = app.get("profil_actif")
        c.append(
            Controle(
                "application", "profil_actif", "lu", str(profil), profil is not None
            )
        )
    if at.catalogue:
        c.append(controle("application", "catalogue", "ok", app.get("catalogue")))
        statut, cat = lire_json(o, f"http://127.0.0.1:{cible.port_catalogue}/health")
        c.append(controle("catalogue", "/health", "200", statut))
        c.append(controle("catalogue", "db", "ok", cat.get("db")))
        c.append(
            controle("catalogue", "normalisation", "fonction", cat.get("normalisation"))
        )
    api = api_dans_le_conteneur(o, cible.projet)
    c.append(controle("api", "/v1/sante", "ok", api.get("statut")))
    c.append(controle("api", "/v1/info", "200", api.get("info")))
    c.append(
        controle(
            "api",
            "version_api",
            at.versions.get("api-modele", "—"),
            api.get("version_api"),
        )
    )
    servi = api.get("encodage_id")
    en_base = encodage_en_service(o, cible.base)
    c.append(
        Controle(
            "api",
            "encodage servi = encodage en service",
            str(en_base or "—"),
            str(servi),
            en_base is not None and str(servi) == en_base,
        )
    )
    try:
        _, attente = migrations(o, cible.base)
        c.append(
            controle("base", f"migrations en attente ({cible.base})", "0", attente)
        )
    except Arret as arret:
        c.append(Controle("base", "migrations en attente", "0", arret.motif, False))
    if cible.avec_monitorage:
        prom = cibles_prometheus(o)
        hors = [f"{job}:{etat}" for job, etat in prom if etat != "up"]
        c.append(
            Controle(
                "prometheus",
                "cibles up",
                "toutes",
                f"{len(prom) - len(hors)}/{len(prom)} up"
                + (f" ({', '.join(hors)})" if hors else ""),
                bool(prom) and not hors,
            )
        )
    return c


def verte(controles: list[Controle]) -> bool:
    return bool(controles) and all(c.tenu for c in controles)


def lignes_verdict(controles: list[Controle]) -> list[str]:
    return [
        f"{'tenu ' if c.tenu else 'MANQUE'}  {c.service} · {c.objet} : {c.constate}"
        + ("" if c.tenu else f" (attendu {c.attendu})")
        for c in controles
    ]
