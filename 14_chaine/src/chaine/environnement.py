"""La base et le dossier visés, les commandes lancées, la copie de la préproduction.

LA PRÉPRODUCTION SEULE. La chaîne ne vise que la base `apimanga_preprod` ; la base de
production `apimanga` est refusée avant tout lancement (code 20), et toute autre base
aussi. Chaque commande appelée reçoit `DATABASE_URL` vers la préproduction —
l'environnement l'emporte sur `uv run --env-file`, et `--no-env-file` est passé par
sûreté.

LES FICHIERS SUIVENT LA BASE (question 38). Les échantillons d'équivalence de la
préproduction vont dans le dossier que donne `EQUIVALENCE_DOSSIER`, jamais dans celui
de la production (`09_service_embedding/mesures/equivalence/`, que lisent la sonde et
`verify-poste`) : sans la variable, ou avec ce dossier, refus.

LES MOTS DE PASSE. Chaque rôle trouve le sien dans `~/.pgpass`, que la chaîne n'ouvre
jamais : elle le vérifie par une connexion (« la base n'existe pas » : le mot de passe
est trouvé ; « pas de mot de passe » : la ligne manque), et donne la forme de la ligne
qui manque, sans mot de passe.

LES COMMANDES passent toutes par un `Lanceur` : le vrai lance un sous-processus, dans un
environnement vidé (comme `verification/verifier.sh`) ; les tests en donnent un factice.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 — commandes fixes, sans shell
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from chaine.codes import Arret, Code

RACINE_MODULE = Path(__file__).resolve().parents[2]
RACINE_DEPOT = RACINE_MODULE.parent

BASE_PRODUCTION = "apimanga"
BASE_PREPROD = "apimanga_preprod"
BASE_ENTRETIEN = "postgres"
HOTE = "localhost"
PORT = 5432
VARIABLE_BASE = "CHAINE_BASE"
VARIABLE_DOSSIER = "EQUIVALENCE_DOSSIER"
DOSSIER_PRODUCTION = RACINE_DEPOT / "09_service_embedding" / "mesures" / "equivalence"
DOSSIER_PREPROD = (
    RACINE_DEPOT / "09_service_embedding" / "mesures" / "equivalence_preprod"
)
REFERENCE_ETAT = RACINE_DEPOT / "verification" / "etat_apimanga.tsv"
EMPREINTES_SQL = RACINE_DEPOT / "database" / "outils" / "empreintes_corpus.sql"
PROJET_PILE = "api-modele"
LECTURE_SEULE = "-c default_transaction_read_only=on"


# --------------------------------------------------------------------------- #
#  Les commandes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Commande:
    """Une commande existante : son nom stable, ses arguments, son dossier, ce qu'elle
    reçoit de plus dans son environnement, et ce qu'elle écrirait."""

    nom: str
    argv: tuple[str, ...]
    dossier: Path = RACINE_DEPOT
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    ecrit: str = "rien"


@dataclass(frozen=True)
class Sortie:
    code: int
    stdout: str = ""
    stderr: str = ""


Lanceur = Callable[[Commande], Sortie]


def environnement_propre(ajouts: Mapping[str, str]) -> dict[str, str]:
    """L'environnement vidé de `verifier.sh` (ni VIRTUAL_ENV, ni DSN hérité), plus les
    ajouts de la commande. Messages de PostgreSQL en anglais, qu'on analyse."""
    base = {
        cle: os.environ[cle]
        for cle in ("HOME", "USER", "PATH", "DOCKER_HOST", "XDG_RUNTIME_DIR")
        if cle in os.environ
    }
    return base | {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"} | dict(ajouts)


def lancer_reel(commande: Commande) -> Sortie:
    """Le vrai lanceur : la commande, sans shell, dans son dossier."""
    r = subprocess.run(  # nosec B603 — arguments fixés par la chaîne, sans shell
        list(commande.argv),
        cwd=commande.dossier,
        env=environnement_propre(commande.env),
        capture_output=True,
        text=True,
        check=False,
    )
    return Sortie(r.returncode, r.stdout, r.stderr)


Http = Callable[[str, Mapping[str, str]], tuple[int, str]]


def http_reel(url: str, entetes: Mapping[str, str]) -> tuple[int, str]:
    """GET sur le bouclage (instance d'embedding, API de préproduction) : (statut,
    corps) ; (0, raison) si rien ne répond. Les en-têtes ne sont jamais affichés."""
    import urllib.error
    import urllib.request

    if not url.startswith("http://127.0.0.1:"):
        raise Arret(Code.INTERNE, "requête hors du bouclage refusée")
    requete = urllib.request.Request(url, headers=dict(entetes))  # noqa: S310
    try:
        with urllib.request.urlopen(requete, timeout=10) as r:  # nosec B310 — bouclage
            return int(r.status), r.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return int(exc.code), ""
    except (urllib.error.URLError, OSError) as exc:
        return 0, type(exc).__name__


def uv_run(*arguments: str, extra_dev: bool = False) -> tuple[str, ...]:
    """`uv run` dans l'environnement verrouillé du module (le dossier de la commande),
    sans fichier `.env`."""
    debut = ("uv", "run", "--locked", "--no-env-file")
    return debut + (("--extra", "dev") if extra_dev else ()) + arguments


# --------------------------------------------------------------------------- #
#  La cible : la base et le dossier
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Cible:
    """La base visée et le dossier des échantillons de la préproduction."""

    base: str
    dossier_export: Path

    def dsn(self, role: str, base: str | None = None) -> str:
        """Un DSN sans mot de passe : il est dans `~/.pgpass`."""
        return f"postgresql://{role}@{HOTE}:{PORT}/{base or self.base}"

    def env_ecriture(self) -> dict[str, str]:
        """Ce que reçoit une commande qui écrit en préproduction (sous `postgres`)."""
        return {
            "DATABASE_URL": self.dsn("postgres"),
            VARIABLE_DOSSIER: str(self.dossier_export),
        }


def cible(environ: Mapping[str, str]) -> Cible:
    """La cible de la chaîne, ou un refus (code 20) : la production, une autre base
    que la préproduction, un dossier absent ou chez la production."""
    base = environ.get(VARIABLE_BASE, BASE_PREPROD)
    if base == BASE_PRODUCTION:
        raise Arret(
            Code.PREREQUIS,
            f"la base {BASE_PRODUCTION} est la production : la chaîne ne la vise"
            " jamais (essais, mesures, promotions : en préproduction)",
        )
    if base != BASE_PREPROD:
        raise Arret(
            Code.PREREQUIS,
            f"base {base!r} : la chaîne ne vise que la préproduction ({BASE_PREPROD})",
        )
    valeur = environ.get(VARIABLE_DOSSIER, "")
    if not valeur:
        raise Arret(
            Code.PREREQUIS,
            f"{VARIABLE_DOSSIER} n'est pas posée : la préproduction écrit ses"
            f" échantillons dans son dossier ({chemin_lisible(DOSSIER_PREPROD)})",
        )
    dossier = Path(valeur).expanduser().resolve()
    if dossier.is_relative_to(DOSSIER_PRODUCTION.resolve()):
        raise Arret(
            Code.PREREQUIS,
            f"{VARIABLE_DOSSIER} désigne le dossier des échantillons de la production"
            f" ({chemin_lisible(DOSSIER_PRODUCTION)}), ou un dossier qui s'y trouve :"
            " refus",
        )
    return Cible(base, dossier)


def chemin_lisible(chemin: Path) -> str:
    """Un chemin relatif au dépôt quand il y est : aucun chemin personnel affiché."""
    try:
        return chemin.resolve().relative_to(RACINE_DEPOT).as_posix()
    except ValueError:
        return chemin.name


# --------------------------------------------------------------------------- #
#  psql, sous un rôle, en lecture seule
# --------------------------------------------------------------------------- #


def psql(
    nom: str,
    dsn: str,
    *,
    sql: str | None = None,
    fichier: Path | None = None,
    variables: Mapping[str, str] | None = None,
    lecture_seule: bool = True,
) -> Commande:
    argv = [
        "psql",
        dsn,
        "-X",
        "-q",
        "-A",
        "-t",
        "-w",
        "-F",
        "\t",
        "-v",
        "ON_ERROR_STOP=1",
    ]
    for cle, valeur in (variables or {}).items():
        argv += ["-v", f"{cle}={valeur}"]
    argv += ["-c", sql] if sql is not None else ["-f", str(fichier)]
    env = {"PGOPTIONS": LECTURE_SEULE} if lecture_seule else {}
    return Commande(nom, tuple(argv), env=env)


def lignes(sortie: Sortie) -> list[str]:
    return [ligne for ligne in sortie.stdout.splitlines() if ligne.strip()]


def connexion(sortie: Sortie) -> str:
    """Ce que dit une tentative de connexion : « ok », « base_absente » (le mot de
    passe a été trouvé), « mot_de_passe_absent », ou « autre ». La seule analyse des
    messages de connexion de PostgreSQL ; testée sur leurs textes réels."""
    if sortie.code == 0:
        return "ok"
    texte = sortie.stderr
    if "no password supplied" in texte or "password authentication failed" in texte:
        return "mot_de_passe_absent"
    if "does not exist" in texte and "database" in texte:
        return "base_absente"
    return "autre"


def ligne_pgpass(base: str, role: str) -> str:
    """La forme d'une ligne de `~/.pgpass`, sans mot de passe."""
    return f"{HOTE}:{PORT}:{base}:{role}:<mot de passe du rôle {role}>"


def verifier_mots_de_passe(lanceur: Lanceur, c: Cible) -> list[str]:
    """Les quatre connexions de la préproduction (question 39) ; rend les lignes qui
    manquent, sans mot de passe. `~/.pgpass` n'est jamais ouvert."""
    essais = [(BASE_ENTRETIEN, "postgres")] + [
        (c.base, role) for role in ("postgres", "verif_lecture", "modele_api")
    ]
    manquent: list[str] = []
    for base, role in essais:
        etat = connexion(
            lanceur(
                psql(f"psql.connexion.{role}.{base}", c.dsn(role, base), sql="SELECT 1")
            )
        )
        if etat == "mot_de_passe_absent":
            manquent.append(ligne_pgpass(base, role))
        elif etat == "autre":
            raise Arret(
                Code.PREREQUIS,
                f"connexion de {role} à {base} impossible (serveur, pg_hba.conf ?)",
            )
    return manquent


# --------------------------------------------------------------------------- #
#  L'état d'une base, contre la référence de la production
# --------------------------------------------------------------------------- #


def reference(chemin: Path = REFERENCE_ETAT) -> list[list[str]]:
    """`verification/etat_apimanga.tsv` : une liste de champs par ligne."""
    return [
        ligne.split("\t")
        for ligne in chemin.read_text(encoding="utf-8").splitlines()
        if ligne and not ligne.startswith("#")
    ]


def empreintes(lanceur: Lanceur, dsn: str, corpus: str) -> list[list[str]]:
    """Les empreintes d'un corpus (`database/outils/empreintes_corpus.sql`)."""
    s = lanceur(
        psql(
            f"psql.empreintes.{corpus}",
            dsn,
            fichier=EMPREINTES_SQL,
            variables={"corpus": corpus},
        )
    )
    if s.code != 0:
        raise Arret(Code.DONNEES, f"empreintes du corpus {corpus} illisibles", s.stderr)
    return [ligne.split("\t") for ligne in lignes(s)]


def valeur(lanceur: Lanceur, nom: str, dsn: str, sql: str) -> str:
    s = lanceur(psql(nom, dsn, sql=sql))
    if s.code != 0:
        raise Arret(Code.PREREQUIS, f"lecture impossible ({nom})", s.stderr)
    return "\n".join(lignes(s)).strip()


COMPTES = {
    "encodage_en_service": "SELECT encodage_id FROM bench.v_encodage_en_service",
    "promotions": "SELECT count(*) FROM bench.promotions",
    "eval_runs": "SELECT count(*) FROM bench.eval_runs",
    "migrations": "SELECT count(*) FROM public.schema_migrations",
}


def etat(lanceur: Lanceur, dsn: str) -> dict[str, list[list[str]] | str]:
    """Les empreintes des corpus v1 et v2 et quatre comptes, sous `verif_lecture`."""
    r: dict[str, list[list[str]] | str] = {
        corpus: empreintes(lanceur, dsn, corpus) for corpus in ("v1", "v2")
    }
    for cle, sql in COMPTES.items():
        r[cle] = valeur(lanceur, f"psql.{cle}", dsn, sql)
    return r


def ecarts_a_la_reference(
    constate: Mapping[str, list[list[str]] | str], ref: list[list[str]]
) -> list[str]:
    """Ce qui diffère de `etat_apimanga.tsv` : empreintes des corpus, `eval_mesures`,
    encodage en service, nombre de promotions, de runs et de migrations."""
    ecarts: list[str] = []
    for corpus in ("v1", "v2"):
        lu = constate[corpus]
        if not isinstance(lu, list):
            raise Arret(Code.INTERNE, f"empreintes du corpus {corpus} absentes")
        for champs in lu:
            if champs[0] == "eval_mesures":
                attendu = [c for c in ref if c[:2] == ["-", "eval_mesures"]]
                if corpus == "v1" and [["-", *champs]] != attendu:
                    ecarts.append(f"eval_mesures : {champs[1:]} au lieu de {attendu}")
                continue
            attendu = [c for c in ref if c[:2] == [corpus, champs[0]]]
            if [[corpus, *champs]] != attendu:
                ecarts.append(
                    f"{corpus} {champs[0]} : {champs[1]} au lieu de {attendu}"
                )
    comptes = {c[1]: c[2] for c in ref if c[0] == "-" and len(c) >= 3}
    for cle in COMPTES:
        if str(constate[cle]) != comptes.get(cle):
            ecarts.append(f"{cle} : {constate[cle]} au lieu de {comptes.get(cle)}")
    return ecarts


# --------------------------------------------------------------------------- #
#  La copie : la préproduction, créée ou recréée (déclencheur 7)
# --------------------------------------------------------------------------- #


def commandes_copie(dump: Path) -> list[Commande]:
    """Les quatre commandes de la copie, sous `postgres`. Le nom de la base supprimée
    puis recréée est une constante : `apimanga_preprod`, aucun autre."""
    connexion_admin = ("-w", "-h", HOTE, "-p", str(PORT), "-U", "postgres")
    entretien = f"--maintenance-db={BASE_ENTRETIEN}"
    return [
        Commande(
            "pg_dump",
            (
                "pg_dump",
                "-Fc",
                *connexion_admin,
                "-d",
                BASE_PRODUCTION,
                "-f",
                str(dump),
            ),
            ecrit="un fichier temporaire hors dépôt (lecture de la production)",
        ),
        Commande(
            "dropdb",
            ("dropdb", *connexion_admin, entretien, "--if-exists", BASE_PREPROD),
            ecrit=f"supprime {BASE_PREPROD} si elle existe",
        ),
        Commande(
            "createdb",
            ("createdb", *connexion_admin, entretien, "-T", "template0", BASE_PREPROD),
            ecrit=f"crée {BASE_PREPROD}",
        ),
        Commande(
            "pg_restore",
            (
                "pg_restore",
                *connexion_admin,
                "-d",
                BASE_PREPROD,
                "--exit-on-error",
                str(dump),
            ),
            ecrit=f"remplit {BASE_PREPROD}",
        ),
    ]


def creer_preproduction(
    lanceur: Lanceur,
    c: Cible,
    *,
    dossier_temporaire: Callable[[], tempfile.TemporaryDirectory[str]] = (
        lambda: tempfile.TemporaryDirectory(prefix="chaine_preprod_")
    ),
    horloge: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Copie la production dans `apimanga_preprod` (supprimée puis recréée si elle
    existe), puis contrôle la copie et la production contre la référence."""
    if c.base != BASE_PREPROD:
        raise Arret(Code.PREREQUIS, f"la copie ne crée que {BASE_PREPROD}")
    manquent = verifier_mots_de_passe(lanceur, c)
    if manquent:
        raise Arret(
            Code.PREREQUIS,
            "mot de passe introuvable : lignes à ajouter à ~/.pgpass (copie de la ligne"
            " apimanga du même rôle, seul le nom de la base change) :\n  "
            + "\n  ".join(manquent),
        )
    debut = horloge()
    with dossier_temporaire() as tmp:
        for commande in commandes_copie(Path(tmp) / "apimanga.dump"):
            s = lanceur(commande)
            if s.code != 0:
                raise Arret(
                    Code.PREREQUIS, f"copie : {commande.nom} a échoué", s.stderr
                )
    duree = round(horloge() - debut, 1)
    ref = reference()
    lecture_preprod = c.dsn("verif_lecture")
    ecarts_copie = ecarts_a_la_reference(etat(lanceur, lecture_preprod), ref)
    ecarts_production = ecarts_a_la_reference(
        etat(lanceur, c.dsn("verif_lecture", BASE_PRODUCTION)), ref
    )
    taille = valeur(
        lanceur,
        "psql.taille",
        lecture_preprod,
        "SELECT pg_size_pretty(pg_database_size(current_database()))",
    )
    if ecarts_production:
        raise Arret(
            Code.PREREQUIS,
            "la PRODUCTION diffère de sa référence après la copie : arrêt immédiat",
            "\n".join(ecarts_production),
        )
    if ecarts_copie:
        raise Arret(
            Code.PREREQUIS,
            "la copie diffère de la référence de la production",
            "\n".join(ecarts_copie),
        )
    return {"base": BASE_PREPROD, "duree_s": duree, "taille": taille}


# --------------------------------------------------------------------------- #
#  La pile du monitorage, et le dépôt
# --------------------------------------------------------------------------- #


def pile_en_marche(lanceur: Lanceur) -> list[str]:
    """Les conteneurs du projet Compose de la pile (`api-modele`) en marche."""
    s = lanceur(
        Commande(
            "docker.pile",
            (
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={PROJET_PILE}",
                "--format",
                "{{.Names}}",
            ),
        )
    )
    if s.code != 0:
        raise Arret(Code.PREREQUIS, "docker ne répond pas", s.stderr)
    return lignes(s)


def git(lanceur: Lanceur, nom: str, *arguments: str) -> Sortie:
    return lanceur(Commande(f"git.{nom}", ("git", *arguments)))


def exiger_arbre_propre_et_pousse(lanceur: Lanceur) -> str:
    """L'arbre propre et `HEAD` présent sur le distant ; rend le commit."""
    s = git(lanceur, "status", "status", "--porcelain")
    if s.code != 0 or s.stdout.strip():
        raise Arret(
            Code.PREREQUIS,
            "arbre non propre : commiter d'abord, mesurer ensuite (un passage = un"
            " rapport de livraison commité avant le suivant)",
        )
    commit = git(lanceur, "rev-parse", "rev-parse", "HEAD").stdout.strip()
    distant = git(lanceur, "contient", "branch", "-r", "--contains", "HEAD")
    if distant.code != 0 or not distant.stdout.strip():
        raise Arret(Code.PREREQUIS, f"HEAD {commit[:12]} n'est pas poussé")
    return commit


def relatif_au_depot(chemin: Path) -> str:
    try:
        return chemin.resolve().relative_to(RACINE_DEPOT).as_posix()
    except ValueError as exc:
        raise Arret(
            Code.PREREQUIS,
            f"{chemin.name} est hors du dépôt : il ne peut pas être suivi",
        ) from exc


def suivi_et_inchange(lanceur: Lanceur, chemin: Path) -> None:
    relatif = relatif_au_depot(chemin)
    if git(lanceur, "suivi", "ls-files", "--error-unmatch", relatif).code != 0:
        raise Arret(Code.PREREQUIS, f"{relatif} n'est pas suivi par git : le commiter")
    if git(lanceur, "inchange", "diff", "--quiet", "HEAD", "--", relatif).code != 0:
        raise Arret(Code.PREREQUIS, f"{relatif} diffère de HEAD : le commiter")


def pousse(lanceur: Lanceur, chemin: Path) -> str:
    """Le dernier commit du fichier, présent sur le distant ; sinon refus."""
    relatif = relatif_au_depot(chemin)
    suivi_et_inchange(lanceur, chemin)
    commit = git(lanceur, "commit-fichier", "log", "-1", "--format=%H", "--", relatif)
    empreinte = commit.stdout.strip()
    distant = git(lanceur, "fichier-pousse", "branch", "-r", "--contains", empreinte)
    if not empreinte or distant.code != 0 or not distant.stdout.strip():
        raise Arret(Code.PREREQUIS, f"{relatif} n'est pas poussé")
    return empreinte


def commande_affichee(commande: Commande, masques: Sequence[str] = ()) -> str:
    """La commande telle qu'on la lit : dossier, variables, arguments — tout chemin du
    dépôt relatif à sa racine, aucun chemin personnel."""
    texte = " ".join(
        [f"({chemin_lisible(commande.dossier)}/)"]
        + [f"{cle}={v}" for cle, v in commande.env.items()]
        + list(commande.argv)
    )
    texte = texte.replace(f"{RACINE_DEPOT}/", "").replace(str(RACINE_DEPOT), ".")
    for masque in masques:
        texte = texte.replace(masque, "[masqué]")
    return texte
