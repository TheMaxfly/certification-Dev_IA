"""Registre des actions — SOURCE UNIQUE du mapping menu → commande réelle.

Principe dur de cette console : elle **n'implémente aucune logique métier**.
Chaque action est un sous-processus vers une CLI DÉJÀ TESTÉE du dépôt, dont la
commande exacte est affichée avant exécution (le jury voit la vraie commande,
pas une abstraction).

Chaque entrée porte une `cible` : le fichier qui doit exister pour que la
commande ait un sens. Le test `test_actions.py` échoue si une CLI est renommée
ou déplacée — le registre ne peut pas mentir en silence.

Toutes les commandes de ce fichier ont été relevées sur le `--help` réel de
chaque CLI (inventaire Étape 0), jamais devinées.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

RACINE = Path(__file__).resolve().parents[3]


class Impact(StrEnum):
    """Ce que l'action touche — c'est ce qui pilote la sécurité de démo."""

    DOCUMENTE = "documenté"  # jamais exécuté : on affiche la commande et on explique
    LECTURE = "lecture"  # n'écrit nulle part
    FICHIERS = "fichiers"  # écrit des fichiers reconstructibles, hors base
    BASE = "base"  # écrit dans PostgreSQL — masqué en lecture seule


class Phase(StrEnum):
    EXTRACT = "Extract"
    LOAD = "Load"
    TRANSFORM = "Transform"
    QUALITE = "Qualité"
    BASE = "Base"


@dataclass(frozen=True)
class Action:
    cle: str
    phase: Phase
    libelle: str
    argv: list[str]
    cible: Path  # fichier qui doit exister (garde-fou testé)
    impact: Impact
    explication: str  # la phrase à dire au jury (1-2 lignes)
    verification: str  # comment on sait que ça a marché
    cwd: Path = RACINE
    env: dict[str, str] = field(default_factory=dict)
    duree_typique: str = ""

    @property
    def commande(self) -> str:
        """La commande telle qu'on la taperait — c'est ce qu'on affiche."""
        prefixe = " ".join(f"{c}={v}" for c, v in sorted(self.env.items()))
        base = " ".join(self.argv)
        return f"{prefixe} {base}".strip()

    @property
    def ecrit_en_base(self) -> bool:
        return self.impact is Impact.BASE

    @property
    def executable(self) -> bool:
        return self.impact is not Impact.DOCUMENTE


_M05 = RACINE / "05_nettoyage_agregation_bdd"
_M04 = RACINE / "04_scraping_manga_sanctuary"
_M07 = RACINE / "07_databricks_manga_export" / "lakehouse"
_DB = RACINE / "database"

# PYTHONPATH=src : le module 05 est en layout src/ sans [project.scripts].
_ENV05 = {"PYTHONPATH": "src"}


def _id(module: str) -> Path:
    return _M05 / "src" / "identity" / f"{module}.py"


ACTIONS: list[Action] = [
    # ───────────────────────── Extract ──────────────────────────
    # Le canari 04 est une suite de scripts numérotés, PAS une CLI : on affiche
    # donc la commande documentée sans jamais l'exécuter (elle sort sur le
    # réseau — hors de question pendant une soutenance).
    Action(
        cle="extract-crawl",
        phase=Phase.EXTRACT,
        libelle="Crawl Manga Sanctuary (smoke 5 items)",
        argv=["uv", "run", "python", "scripts/run_scrape.py", "--smoke", "5"],
        cible=_M04 / "scripts" / "run_scrape.py",
        impact=Impact.DOCUMENTE,
        cwd=_M04,
        explication=(
            "Le crawl écrit un snapshot daté sous data/raw/. Il est reprenable "
            "(JOBDIR) et ne remplace le snapshot mensuel qu'après validation de "
            "seuils — un crawl tronqué ne peut pas écraser une bonne référence."
        ),
        verification="Un dossier data/raw/<AAAA-MM>/ contient les .jsonl attendus.",
        duree_typique="quelques secondes en smoke, ~7 h en full",
    ),
    Action(
        cle="extract-canari",
        phase=Phase.EXTRACT,
        libelle="Canari de collecte (échantillon → re-scrape → comparaison)",
        argv=["uv", "run", "python", "canari/01_echantillon.py"],
        cible=_M04 / "canari" / "01_echantillon.py",
        impact=Impact.DOCUMENTE,
        cwd=_M04,
        explication=(
            "Le canari re-scrape un échantillon et compare au snapshot : c'est "
            "lui qui a fait tomber le bug de sélecteur des critiques et les "
            "alias tronqués, AVANT de relancer un crawl complet."
        ),
        verification="canari/rapport.md liste les écarts constatés.",
        duree_typique="~2 min (réseau)",
    ),
    # ────────────────────────── Load ────────────────────────────
    # Aucun chargeur n'expose --dry-run (vérifié au --help) : ce sont donc des
    # actions d'écriture pleines, masquées en lecture seule.
    Action(
        cle="load-ms",
        phase=Phase.LOAD,
        libelle="Charger Manga Sanctuary (staging → promotion manga.*)",
        argv=["uv", "run", "python", "-m", "identity.charger_ms"],
        cible=_id("charger_ms"),
        impact=Impact.BASE,
        cwd=_M05,
        env=_ENV05,
        explication=(
            "C'est le L de ELT : le fichier brut entre d'abord en staging "
            "TOUT-TEXT, sans typage — on charge d'abord, on transforme ensuite. "
            "La promotion typée vers manga.* est un upsert-merge, jamais un DELETE."
        ),
        verification=(
            "manga.ms_series_enriched / ms_volumes_enriched : comptes mis à jour."
        ),
        duree_typique="~3 min",
    ),
    Action(
        cle="load-kitsu",
        phase=Phase.LOAD,
        libelle="Charger le catalogue Kitsu (formes + mappings)",
        argv=["uv", "run", "python", "-m", "identity.charger_kitsu"],
        cible=_id("charger_kitsu"),
        impact=Impact.BASE,
        cwd=_M05,
        env=_ENV05,
        explication=(
            "Kitsu enrichit, il ne remplace pas. On charge ses formes de titres "
            "et ses mappings vers les autres plateformes — la matière du "
            "rapprochement, pas une source de vérité concurrente."
        ),
        verification="manga.kitsu_formes et manga.kitsu_mappings sont peuplées.",
        duree_typique="~4 min",
    ),
    Action(
        cle="load-wikidata",
        phase=Phase.LOAD,
        libelle="Charger Wikidata (pivot, formes, auteurs)",
        argv=["uv", "run", "python", "-m", "identity.charger_wikidata"],
        cible=_id("charger_wikidata"),
        impact=Impact.BASE,
        cwd=_M05,
        env=_ENV05,
        explication=(
            "Wikidata sert de pivot d'identité : c'est lui qui porte le QID, "
            "l'identifiant stable qu'aucune plateforme manga ne partage."
        ),
        verification="manga.wd_pivot / wd_formes / wd_auteurs peuplées.",
        duree_typique="~1 min",
    ),
    Action(
        cle="load-mi",
        phase=Phase.LOAD,
        libelle="Recharger Manga Insight depuis un raw daté",
        argv=["uv", "run", "python", "-m", "identity.charger_mi"],
        cible=_id("charger_mi"),
        impact=Impact.BASE,
        cwd=_M05,
        env=_ENV05,
        explication=(
            "Rechargement complet transactionnel avec un plancher de qualité : "
            "si le fichier est trop pauvre, la transaction est annulée. Le "
            "rollback a été prouvé par test."
        ),
        verification="manga.mi_sorties recompté ; plancher 90 % respecté.",
        duree_typique="~1 min",
    ),
    # ──────────────────────── Transform ─────────────────────────
    Action(
        cle="transform-pont-dry",
        phase=Phase.TRANSFORM,
        libelle="Étage 0 — pont Kitsu (--dry-run, ROLLBACK garanti)",
        argv=["uv", "run", "python", "-m", "identity.pont_kitsu", "--dry-run"],
        cible=_id("pont_kitsu"),
        impact=Impact.FICHIERS,
        cwd=_M05,
        env=_ENV05,
        explication=(
            "L'étage 0 relie MS à Wikidata SANS regarder les titres : il passe "
            "par les identifiants externes que Kitsu publie. Des identités "
            "vraies trouvées sans lire un seul titre — score 1.0."
        ),
        verification=(
            "Le rapport annonce les décisions ; la base est intacte (ROLLBACK)."
        ),
        duree_typique="~0,3 s (mesuré, cascade déjà appliquée)",
    ),
    Action(
        cle="transform-etage1-dry",
        phase=Phase.TRANSFORM,
        libelle="Étage 1 — exact (--dry-run, ROLLBACK garanti)",
        argv=["uv", "run", "python", "-m", "identity.etage1_exact", "--dry-run"],
        cible=_id("etage1_exact"),
        impact=Impact.FICHIERS,
        cwd=_M05,
        env=_ENV05,
        explication=(
            "Rapprochement sur forme normalisée EXACTE, plus signal auteur et "
            "fenêtre d'année. Une seule normalisation en Python alimente tout : "
            "le chemin de décision ne contient aucune normalisation SQL."
        ),
        verification="Le rapport donne la matrice ; rien n'est écrit (ROLLBACK).",
        duree_typique="~1,1 s (mesuré, cascade déjà appliquée)",
    ),
    Action(
        cle="transform-mesure",
        phase=Phase.TRANSFORM,
        libelle="Mesure de couverture (n'écrit RIEN en base)",
        argv=["uv", "run", "python", "-m", "identity.mesure_349"],
        cible=_id("mesure_349"),
        impact=Impact.FICHIERS,
        cwd=_M05,
        env=_ENV05,
        explication=(
            "La mesure est séparée de la décision : ce job lit, compte et écrit "
            "un CSV d'analyse. Il ne touche jamais la base — on ne mesure pas "
            "avec l'outil qui écrit."
        ),
        verification="Un CSV de mesure est produit ; aucun INSERT.",
        duree_typique="~0,4 s (mesuré)",
    ),
    Action(
        cle="transform-promotion-dry",
        phase=Phase.TRANSFORM,
        libelle="Étage R — promotion des verdicts LLM (dry-run par défaut)",
        argv=["uv", "run", "python", "-m", "identity.etage_r_promotion"],
        cible=_id("etage_r_promotion"),
        impact=Impact.FICHIERS,
        cwd=_M05,
        env=_ENV05,
        explication=(
            "Le juge LLM ne décide pas seul : il émet un avis, et seule une "
            "promotion bornée et journalisée transforme cet avis en décision. "
            "Sans --appliquer, ce job calcule et n'écrit rien."
        ),
        verification="L'entonnoir de promotion est affiché ; base inchangée.",
        duree_typique="~40 s",
    ),
    # ───────────────────────── Qualité ──────────────────────────
    Action(
        cle="qualite-verifier",
        phase=Phase.QUALITE,
        libelle="Lakehouse — vérifications imposées (12 contrôles)",
        argv=["uv", "run", "lakehouse", "verifier"],
        cible=_M07 / "src" / "lakehouse" / "cli.py",
        impact=Impact.LECTURE,
        cwd=_M07,
        explication=(
            "Confronte le lakehouse aux comptes attendus et aux trois incidents "
            "historiques. Douze contrôles, verdict binaire : c'est la preuve "
            "que les métriques disent encore ce qu'elles prétendent."
        ),
        verification="Sortie « TOUT VERT » et code retour 0.",
        duree_typique="~24 s (mesuré)",
    ),
    Action(
        cle="qualite-synthese",
        phase=Phase.QUALITE,
        libelle="Lecteur externe DuckDB (hors JVM, ~0,1 s)",
        argv=["uv", "run", "lakehouse", "synthese"],
        cible=_M07 / "src" / "lakehouse" / "reader.py",
        impact=Impact.LECTURE,
        cwd=_M07,
        explication=(
            "Le lakehouse est lu SANS Spark : DuckDB lit le format Delta "
            "directement. Le cycle mensuel consulte ce tableau avant promotion — "
            "une alerte peut motiver un NO-GO humain."
        ),
        verification="La synthèse s'affiche en quelques dizaines de ms.",
        duree_typique="~0,4 s (mesuré)",
    ),
    Action(
        cle="qualite-rapport",
        phase=Phase.QUALITE,
        libelle="Lakehouse — générer le rapport qualité (markdown)",
        argv=["uv", "run", "lakehouse", "rapport"],
        cible=_M07 / "src" / "lakehouse" / "report.py",
        impact=Impact.FICHIERS,
        cwd=_M07,
        explication=(
            "Le rapport est généré EN LISANT les tables gold : il rejoue les "
            "trois incidents réels du projet, désormais détectés par système et "
            "non plus par accident."
        ),
        verification="Un rapport_qualite_<ts>.md apparaît dans 07_.../rapports/.",
        duree_typique="~30 s",
    ),
    Action(
        cle="qualite-pipeline",
        phase=Phase.QUALITE,
        libelle="Lakehouse — pipeline complet bronze→silver→gold→rapport",
        argv=["uv", "run", "lakehouse", "pipeline"],
        cible=_M07 / "src" / "lakehouse" / "cli.py",
        impact=Impact.FICHIERS,
        cwd=_M07,
        explication=(
            "Le médaillon complet : le raw daté devient des tables Delta, puis "
            "des métriques comparées entre snapshots. Idempotent — le relancer "
            "ne duplique rien."
        ),
        verification="Comptes bronze inchangés au rejeu ; 5 tables gold écrites.",
        duree_typique="~1 min 29 (mesuré, conteneur)",
    ),
    Action(
        cle="qualite-fidelite",
        phase=Phase.QUALITE,
        libelle="Preuve de fidélité — rejeu des migrations sur PG jetable",
        argv=["bash", "outils/fidelite.sh"],
        cible=_DB / "outils" / "fidelite.sh",
        impact=Impact.LECTURE,
        cwd=_DB,
        explication=(
            "On rejoue TOUTES les migrations sur une base jetable et on compare "
            "son schéma à la vraie. Un diff vide prouve que le dépôt sait "
            "reconstruire la base — sinon, la baseline mentirait."
        ),
        verification="Diff vide. Nécessite Docker et pg_dump.",
        duree_typique="~1 min",
    ),
    # ────────────────────────── Base ────────────────────────────
    Action(
        cle="base-migrate-status",
        phase=Phase.BASE,
        libelle="Migrations — statut (appliquées / en attente)",
        argv=["uv", "run", "python", "migrate.py", "status"],
        cible=_DB / "migrate.py",
        impact=Impact.LECTURE,
        cwd=_DB,
        explication=(
            "Le schéma est versionné comme du code : des migrations numérotées, "
            "un runner à checksums. On sait toujours ce qui est appliqué."
        ),
        verification="La liste des migrations et le nombre en attente s'affichent.",
        duree_typique="~0,2 s (mesuré)",
    ),
    Action(
        cle="base-migrate-up",
        phase=Phase.BASE,
        libelle="Migrations — appliquer les migrations en attente",
        argv=["uv", "run", "python", "migrate.py", "up"],
        cible=_DB / "migrate.py",
        impact=Impact.BASE,
        cwd=_DB,
        explication=(
            "Applique les migrations en attente, dans l'ordre, avec vérification "
            "de checksum : une migration déjà appliquée mais modifiée est "
            "refusée plutôt que rejouée en silence."
        ),
        verification="`migrate.py status` annonce 0 en attente.",
        duree_typique="quelques secondes",
    ),
]


ACTIONS_PAR_CLE: dict[str, Action] = {a.cle: a for a in ACTIONS}


def actions_visibles(lecture_seule: bool) -> list[Action]:
    """Le menu réellement proposé. En lecture seule, AUCUNE action qui écrit
    en base n'est proposée — elle n'est pas seulement grisée, elle est absente
    (on ne peut pas déclencher par erreur ce qui n'est pas au menu)."""
    if lecture_seule:
        return [a for a in ACTIONS if not a.ecrit_en_base]
    return list(ACTIONS)


def par_phase(actions: list[Action]) -> dict[Phase, list[Action]]:
    groupes: dict[Phase, list[Action]] = {}
    for phase in Phase:
        membres = [a for a in actions if a.phase is phase]
        if membres:
            groupes[phase] = membres
    return groupes
