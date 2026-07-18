"""Hydratation des noms d'auteurs Wikidata (étape D0-1).

    uv run python -m identity.hydrater_auteurs extraire   # réseau -> raw daté
    uv run python -m identity.hydrater_auteurs charger    # raw -> manga.wd_auteurs

`wd_auteurs` ne porte qu'un Q-id d'auteur : Wikidata désigne l'auteur par son
entité, pas par son nom. Sans nom, l'étage « exact_author » de la cascade ne
peut pas exister — d'où cette hydratation, préalable à l'étage 1.

Pattern ELT : `extraire` écrit le brut daté sous data/raw/wikidata/<date>/
auteurs/ et ne fait rien d'autre ; `charger` relit ce brut et écrit en base.
Le brut est rejouable sans re-télécharger (reprise par fichier existant).

Choix du nom : ja > en > fr > premier disponible. Le nom NATIF est le meilleur
disambiguateur contre le staff Kitsu, lui aussi majoritairement natif. La langue
retenue est stockée (auteur_lang) pour rendre le choix auditable.

Les alias vont dans manga.wd_auteurs_formes : un auteur porte souvent son nom
natif ET sa romanisation, et les DEUX servent la désambiguïsation — garder le
seul nom retenu perdrait la moitié du signal.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import psycopg
import typer

from identity.wikidata_dump import BATCH_SIZE, PAUSE_S, WB_API, _get, normaliser

MODULE = Path(__file__).resolve().parents[2]
RAW_BASE = MODULE / "data" / "raw" / "wikidata"

# Priorité de choix du nom : le natif d'abord (cf. docstring).
LANGUES = ("ja", "en", "fr")

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurHydratation(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurHydratation(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'"
        )
    return url


def auteurs_a_hydrater(connexion) -> list[str]:
    """Les Q-id d'auteurs DISTINCTS : un auteur porte souvent plusieurs œuvres,
    l'appeler une fois par œuvre serait du gaspillage réseau."""
    with connexion.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT auteur_qid FROM manga.wd_auteurs "
            "WHERE auteur_qid IS NOT NULL ORDER BY auteur_qid"
        )
        return [q for (q,) in cur.fetchall()]


def dossier_brut(jour: str | None = None) -> Path:
    return RAW_BASE / (jour or date.today().isoformat()) / "auteurs"


def dernier_dossier_brut() -> Path:
    """Le dossier auteurs/ daté le plus récent."""
    candidats = sorted(
        d / "auteurs" for d in RAW_BASE.iterdir() if (d / "auteurs").is_dir()
    )
    if not candidats:
        raise ErreurHydratation(
            f"Aucun brut d'auteurs sous {RAW_BASE}. Lancer `extraire` d'abord."
        )
    return candidats[-1]


# --------------------------------------------------------------------------- #
# Extraction (réseau)
# --------------------------------------------------------------------------- #
@app.command()
def extraire(
    jour: str = typer.Option(  # noqa: B008
        None, help="Date du brut (défaut : aujourd'hui)."
    ),
) -> None:
    """Télécharge labels et alias des auteurs vers le brut daté."""
    debut = time.monotonic()
    with psycopg.connect(dsn()) as connexion:
        qids = auteurs_a_hydrater(connexion)
        with connexion.cursor() as cur:
            cur.execute("SELECT count(*) FROM manga.wd_auteurs")
            lignes = cur.fetchone()[0]

    cible = dossier_brut(jour)
    cible.mkdir(parents=True, exist_ok=True)
    lots = (len(qids) + BATCH_SIZE - 1) // BATCH_SIZE
    typer.echo(
        f"→ {lignes} lignes wd_auteurs → {len(qids)} Q-id distincts → {lots} lots"
    )

    telecharges = 0
    for i in range(0, len(qids), BATCH_SIZE):
        lot = qids[i : i + BATCH_SIZE]
        fichier = cible / f"auteurs_{i:06d}.json"
        if fichier.exists():  # reprise : jamais deux fois le même lot
            continue
        reponse = _get(
            WB_API,
            {
                "action": "wbgetentities",
                "ids": "|".join(lot),
                # labels|aliases seulement : ni claims ni sitelinks ici, on ne
                # veut que des noms. Charge réseau divisée d'autant.
                "props": "labels|aliases",
                "languages": "|".join(LANGUES),
                "format": "json",
            },
        )
        fichier.write_text(reponse.text, encoding="utf-8")
        telecharges += 1
        if telecharges % 10 == 0:
            typer.echo(f"  lot {i // BATCH_SIZE + 1}/{lots}")
        time.sleep(PAUSE_S)

    typer.echo(
        f"  ✓ {telecharges} lots téléchargés, "
        f"{lots - telecharges} déjà présents → {cible}"
    )
    typer.echo(f"Terminé en {time.monotonic() - debut:.1f} s.")


# --------------------------------------------------------------------------- #
# Lecture du brut (fonctions pures, testables sans réseau ni base)
# --------------------------------------------------------------------------- #
def choisir_nom(labels: dict) -> tuple[str | None, str | None]:
    """(nom, langue) selon la priorité ja > en > fr > premier disponible."""
    for langue in LANGUES:
        valeur = (labels.get(langue) or {}).get("value")
        if valeur:
            return valeur, langue
    for langue, label in labels.items():
        valeur = (label or {}).get("value")
        if valeur:
            return valeur, langue
    return None, None


def formes_d_un_auteur(entite: dict) -> list[tuple[str, str, str]]:
    """(forme, forme_type, langue) : tous les labels puis tous les alias.

    Le natif et sa romanisation sont deux formes distinctes du même auteur :
    les garder toutes deux est l'objet même de wd_auteurs_formes.
    """
    formes = []
    for langue, label in (entite.get("labels") or {}).items():
        valeur = (label or {}).get("value")
        if valeur:
            formes.append((valeur, "label", langue))
    for langue, alias in (entite.get("aliases") or {}).items():
        for element in alias or []:
            valeur = (element or {}).get("value")
            if valeur:
                formes.append((valeur, "alias", langue))
    return formes


def lire_brut(dossier: Path) -> dict[str, dict]:
    """{auteur_qid: entité} depuis les lots téléchargés."""
    lots = sorted(dossier.glob("auteurs_*.json"))
    if not lots:
        raise ErreurHydratation(f"Aucun lot d'auteurs dans {dossier}.")
    entites: dict[str, dict] = {}
    for fichier in lots:
        data = json.loads(fichier.read_text(encoding="utf-8"))
        for qid, entite in (data.get("entities") or {}).items():
            if "missing" in entite:
                continue
            entites[qid] = entite
    return entites


# --------------------------------------------------------------------------- #
# Chargement (base)
# --------------------------------------------------------------------------- #
@app.command()
def charger(
    source: Path = typer.Option(  # noqa: B008
        None, help="Dossier auteurs/ (défaut : le brut daté le plus récent)."
    ),
) -> None:
    """Relit le brut, choisit un nom par auteur et remplit manga.wd_auteurs."""
    debut = time.monotonic()
    dossier = Path(source) if source else dernier_dossier_brut()
    entites = lire_brut(dossier)
    typer.echo(f"→ {len(entites)} entités auteurs lues depuis {dossier}")

    noms: list[tuple[str, str, str, str]] = []  # qid, nom, norm, langue
    sans_label: list[str] = []
    formes: list[tuple[str, str, str, str, str]] = []
    vues: set[tuple[str, str]] = set()

    for qid, entite in entites.items():
        nom, langue = choisir_nom(entite.get("labels") or {})
        if nom is None:
            sans_label.append(qid)
        else:
            noms.append((qid, nom, normaliser(nom), langue))
        for forme, forme_type, langue_forme in formes_d_un_auteur(entite):
            norm = normaliser(forme)
            if not norm or (qid, norm) in vues:
                continue
            vues.add((qid, norm))
            formes.append((qid, forme, norm, forme_type, langue_forme))

    with psycopg.connect(dsn()) as connexion:
        with connexion.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE noms_stage "
                "(auteur_qid text PRIMARY KEY, auteur text, "
                " auteur_norm text, auteur_lang text) ON COMMIT DROP"
            )
            with cur.copy(
                "COPY noms_stage (auteur_qid, auteur, auteur_norm, auteur_lang) "
                "FROM STDIN"
            ) as copie:
                for ligne in noms:
                    copie.write_row(ligne)
            # Idempotent : seules les lignes réellement différentes sont écrites.
            cur.execute(
                "UPDATE manga.wd_auteurs a "
                "SET auteur = n.auteur, auteur_norm = n.auteur_norm, "
                "    auteur_lang = n.auteur_lang "
                "FROM noms_stage n "
                "WHERE a.auteur_qid = n.auteur_qid AND ("
                "  a.auteur IS DISTINCT FROM n.auteur OR "
                "  a.auteur_norm IS DISTINCT FROM n.auteur_norm OR "
                "  a.auteur_lang IS DISTINCT FROM n.auteur_lang)"
            )
            lignes_maj = cur.rowcount

            cur.executemany(
                "INSERT INTO manga.wd_auteurs_formes "
                "  (auteur_qid, forme, forme_norm, forme_type, langue) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (auteur_qid, forme_norm) DO NOTHING",
                formes,
            )
            mesures = _mesurer(cur)
        connexion.commit()

    _rapport(mesures, len(entites), len(noms), sans_label, lignes_maj, len(formes))
    typer.echo(f"Terminé en {time.monotonic() - debut:.1f} s.")


def _mesurer(cur) -> dict:
    cur.execute(
        "SELECT count(*), count(auteur), count(DISTINCT auteur_qid) "
        "FROM manga.wd_auteurs"
    )
    lignes, avec_nom, distincts = cur.fetchone()
    cur.execute(
        "SELECT coalesce(auteur_lang, '(aucun)'), count(DISTINCT auteur_qid) "
        "FROM manga.wd_auteurs GROUP BY 1 ORDER BY 2 DESC"
    )
    repartition = cur.fetchall()
    cur.execute(
        "SELECT count(*), count(DISTINCT auteur_qid) FROM manga.wd_auteurs_formes"
    )
    formes_total, formes_auteurs = cur.fetchone()
    cur.execute(
        "SELECT DISTINCT ON (auteur_qid) auteur_qid, auteur, auteur_lang "
        "FROM manga.wd_auteurs WHERE auteur IS NOT NULL "
        "ORDER BY auteur_qid, md5(auteur_qid) LIMIT 10"
    )
    exemples = cur.fetchall()
    return {
        "lignes": lignes,
        "avec_nom": avec_nom,
        "distincts": distincts,
        "repartition": repartition,
        "formes_total": formes_total,
        "formes_auteurs": formes_auteurs,
        "exemples": exemples,
    }


def _rapport(m, lus, nommes, sans_label, lignes_maj, formes_candidates) -> None:
    lignes, avec_nom = m["lignes"], m["avec_nom"]
    pct = f"{100 * avec_nom / lignes:.1f}%" if lignes else "—"
    typer.echo("")
    typer.echo("HYDRATATION DES AUTEURS")
    typer.echo("─" * 54)
    typer.echo(
        f"  {lus:>5} entités lues, {nommes} nommées, {len(sans_label)} sans label"
    )
    typer.echo(f"  {lignes_maj:>5} lignes wd_auteurs mises à jour ce run")
    typer.echo(f"  {avec_nom:>5} / {lignes} lignes avec un nom  ({pct})")
    typer.echo(f"  {m['distincts']:>5} Q-id d'auteurs distincts")
    typer.echo("─" * 54)
    typer.echo("  Répartition de la langue retenue (auteurs distincts) :")
    for langue, nombre in m["repartition"]:
        typer.echo(f"    {langue:<8} {nombre:>5}")
    typer.echo("─" * 54)
    typer.echo(
        f"  wd_auteurs_formes : {m['formes_total']} formes "
        f"sur {m['formes_auteurs']} auteurs ({formes_candidates} candidates)"
    )
    if sans_label:
        typer.echo(f"  Q-id sans aucun label : {', '.join(sans_label[:10])}")
    typer.echo("\n  10 auteurs hydratés (qid, nom retenu, langue) :")
    for qid, auteur, langue in m["exemples"]:
        typer.echo(f"    {qid:12s} {auteur[:34]:34s} {langue}")


def main() -> int:
    try:
        app()
    except ErreurHydratation as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
