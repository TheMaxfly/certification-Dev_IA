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

PROMETHEUS JUGE SUR UN RELEVÉ POSTÉRIEUR AU LEVER (0.1.2, écart 25). Après un lever,
`/api/v1/targets` porte encore, un temps, le relevé d'avant : celui du conteneur quitté,
`down` comme `up`. Une cible n'est donc jugée que sur un relevé commencé après l'instant
de référence (`depuis` : `/sante` à 200, à la fin de l'attente du déploiement) ; tant
qu'il en manque un, la santé relit, jusqu'à une borne dérivée de Prometheus lui-même
(le plus grand intervalle plus délai de relevé lus, et une marge). Sans instant de
référence (`livrer sante` seul), une seule lecture. Deux horloges : `depuis` et
`lastScrape` sont des heures murales UTC ; la borne se mesure à l'horloge monotone.

L'API N'EST PAS PUBLIÉE (on l'atteint par l'application) : ses deux routes sont lues
**dans son conteneur** (`docker exec`), par un court programme Python ; `/v1/info`
exige une clé, que ce programme prend dans l'environnement du conteneur — elle n'en sort
pas : `livrer` ne la lit ni ne l'affiche, il ne reçoit que quatre champs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from livraison import depot, images
from livraison.cibles import Cible
from livraison.codes import Arret, Code
from livraison.outils import Commande, Outils, json_objet, lignes, liste, objet

ESSAIS = 3
PAUSE_S = 2.0
PORT_PROMETHEUS = 9090
#: Ajoutée à la borne lue : le temps d'une réponse de Prometheus et d'une lecture.
MARGE_RELEVE_S = 5.0
NON_RELEVEE = "non relevée depuis le lever"
CHEMIN_PROMETHEUS = "13_monitorage/prometheus/prometheus.yml"
PHRASE_SANS_BORNE = (
    "Prometheus : intervalle de relevé illisible (ni dans ses cibles, ni dans"
    f" {CHEMIN_PROMETHEUS}) — jugé sans attendre un relevé postérieur au lever"
)
#: Les durées de Prometheus (`15s`, `1m30s`, `500ms`).
UNITES_S = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, "w": 604800.0}
DUREE = re.compile(r"(\d+)(ms|s|m|h|d|w)")
INTERVALLE = re.compile(r"^\s*scrape_interval:\s*[\"']?([0-9a-z]+)", re.MULTILINE)
DELAI = re.compile(r"^\s*scrape_timeout:\s*[\"']?([0-9a-z]+)", re.MULTILINE)
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


@dataclass(frozen=True)
class CiblePrometheus:
    """Une cible active de `/api/v1/targets`, réduite à ce que `livrer` lit."""

    job: str
    sante: str  # `health` : up, down, unknown
    releve: datetime | None  # `lastScrape` : le début du dernier relevé
    intervalle_s: float | None  # `scrapeInterval`
    delai_s: float | None  # `scrapeTimeout`


def heure(texte: object) -> datetime | None:
    """Une heure de Prometheus (RFC 3339, neuf décimales, `Z`) ; `None` pour une cible
    jamais relevée (l'heure nulle, an 1) ou une forme illisible."""
    try:
        lue = datetime.fromisoformat(str(texte))
    except ValueError:
        return None
    return lue if lue.tzinfo is not None and lue.year > 1 else None


def duree_s(texte: object) -> float | None:
    """Une durée de Prometheus en secondes ; `None` si elle ne se lit pas en entier."""
    brut = str(texte).strip()
    morceaux = DUREE.findall(brut)
    if not morceaux or "".join(n + u for n, u in morceaux) != brut:
        return None
    return sum(int(n) * UNITES_S[u] for n, u in morceaux)


def lire_cibles(o: Outils) -> list[CiblePrometheus]:
    """Une lecture de `/api/v1/targets` : les cibles actives (aucune si Prometheus ne
    répond pas)."""
    statut, corps = lire_json(o, f"http://127.0.0.1:{PORT_PROMETHEUS}/api/v1/targets")
    actives = liste(objet(corps.get("data")).get("activeTargets")) if statut else []
    lues: list[CiblePrometheus] = []
    for brute in actives:
        c = objet(brute)
        lues.append(
            CiblePrometheus(
                job=str(objet(c.get("labels")).get("job", "?")),
                sante=str(c.get("health")),
                releve=heure(c.get("lastScrape")),
                intervalle_s=duree_s(c.get("scrapeInterval")),
                delai_s=duree_s(c.get("scrapeTimeout")),
            )
        )
    return lues


def posterieur(c: CiblePrometheus, depuis: datetime) -> bool:
    """Le dernier relevé de la cible a-t-il commencé après l'instant de référence ?"""
    return c.releve is not None and c.releve > depuis


def borne_attente(
    o: Outils, lues: list[CiblePrometheus], reference: str
) -> tuple[float, str] | None:
    """La borne de l'attente d'un relevé postérieur, et d'où elle vient. Elle se dérive
    de Prometheus, elle ne se recopie pas : le plus grand intervalle plus délai de ses
    cibles ; à défaut, l'intervalle écrit dans sa configuration, à l'étiquette jugée ;
    à défaut, `None` (la santé juge sans attendre, et le dit)."""
    lus = [
        c.intervalle_s + c.delai_s
        for c in lues
        if c.intervalle_s is not None and c.delai_s is not None
    ]
    if lus:
        return max(lus) + MARGE_RELEVE_S, "cibles de Prometheus"
    texte = depot.fichier_a(o, reference, CHEMIN_PROMETHEUS) or ""
    intervalles = [d for d in map(duree_s, INTERVALLE.findall(texte)) if d is not None]
    if not intervalles:
        return None
    delais = [d for d in map(duree_s, DELAI.findall(texte)) if d is not None]
    # Sans délai écrit, l'intervalle le borne : Prometheus refuse un délai plus long.
    return (
        max(intervalles) + max(delais or intervalles) + MARGE_RELEVE_S,
        f"{CHEMIN_PROMETHEUS} à {reference}",
    )


def cibles_prometheus(
    o: Outils,
    reference: str,
    depuis: datetime | None = None,
    notes: list[str] | None = None,
) -> list[tuple[str, str]]:
    """(job, état) de chaque cible. Avec un instant de référence, l'état est celui d'un
    relevé postérieur ; une cible qui n'en a pas à la borne est « non relevée depuis le
    lever ». L'attente est notée (`notes`, le journal)."""
    lues = lire_cibles(o)
    if depuis is None or not lues:
        return [(c.job, c.sante) for c in lues]
    borne = borne_attente(o, lues, reference)
    if borne is None:
        if notes is not None:
            notes.append(PHRASE_SANS_BORNE)
        return [(c.job, c.sante) for c in lues]
    limite, origine = borne
    debut = o.horloge()
    while True:
        attente = o.horloge() - debut
        if all(posterieur(c, depuis) for c in lues) or attente >= limite:
            break
        o.dormir(PAUSE_S)
        lues = lire_cibles(o)
    if notes is not None:
        notes.append(
            f"Prometheus : {attente:.0f} s d'attente d'un relevé postérieur au lever"
            f" (borne {limite:.0f} s, {origine})"
        )
    return [(c.job, c.sante if posterieur(c, depuis) else NON_RELEVEE) for c in lues]


def verifier(
    o: Outils,
    cible: Cible,
    at: Attendus,
    depuis: datetime | None = None,
    notes: list[str] | None = None,
) -> list[Controle]:
    """Tous les contrôles ; aucun ne s'arrête au premier manque. `depuis` : l'instant où
    la pile a répondu (un déploiement, un retour) ; `None` pour `livrer sante` seul."""
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
        prom = cibles_prometheus(o, at.reference, depuis, notes)
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
