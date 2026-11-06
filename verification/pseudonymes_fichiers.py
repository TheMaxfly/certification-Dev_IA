"""Les pseudonymes dans les fichiers suivis par git — une étape de l'état d'apimanga
(`make verify-poste`), sous `verif_ro`.

    (database/) uv run --locked python ../verification/pseudonymes_fichiers.py

Le dépôt est public : aucun pseudonyme de lecteur n'y a sa place, ni dans un fichier,
ni dans une sortie de notebook, ni dans un exemple. Chaque fichier suivi (son chemin et
son contenu) est relu avec la méthode du corpus v2 : le pseudonyme en mot entier, casse
ignorée. Les seules occurrences admises sont les homonymes de
`pseudonymes_fichiers_admis.tsv`, par fichier, empreinte et nombre : un mangaka, un
titre d'œuvre, un mot courant.

Une occurrence hors de cette liste est un échec. Une entrée qui ne correspond plus
exactement à ce qui est trouvé aussi : une liste qui dérive finirait par tolérer ce
qu'elle ne décrit plus, et c'est aussi le témoin du contrôle (une liste de pseudonymes
vide ou fausse ne retrouverait pas les homonymes admis).

Les pseudonymes sont lus dans `manga.ms_reviews_all` et ne quittent pas la mémoire : la
sortie ne porte que des chemins, des nombres et des empreintes. Les fichiers compressés
(images) ne sont pas lisibles ainsi : leur relecture reste humaine.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pseudonymes_passages as passages  # noqa: E402

RACINE = Path(__file__).resolve().parents[1]
FICHIER_ADMIS = RACINE / "verification" / "pseudonymes_fichiers_admis.tsv"
COLONNES = ["chemin", "empreinte_pseudo", "nombre", "motif"]
MOTIFS = ("mangaka", "titre_oeuvre", "mot_courant")

Cle = tuple[str, str]  # (chemin, empreinte)
Motif = Callable[[str], re.Pattern[str]]


def lire_admises(chemin: Path) -> dict[Cle, int]:
    """La liste versionnée : {(chemin, empreinte) : nombre}, entrées contrôlées."""
    try:
        lignes = chemin.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise passages.ControleImpossible(f"liste illisible : {chemin.name}") from exc
    utiles = [ligne for ligne in lignes if ligne and not ligne.startswith("#")]
    if not utiles or utiles[0].split("\t") != COLONNES:
        raise passages.ControleImpossible(f"{chemin.name} : en-tête attendu {COLONNES}")
    admises: dict[Cle, int] = {}
    for ligne in utiles[1:]:
        champs = ligne.split("\t")
        if len(champs) != len(COLONNES):
            raise passages.ControleImpossible(f"{chemin.name} : ligne mal formée")
        fichier, empreinte, nombre, motif = champs
        if not re.fullmatch(r"[0-9a-f]{16}", empreinte):
            raise passages.ControleImpossible(f"{chemin.name} : empreinte mal formée")
        if not nombre.isdecimal() or int(nombre) < 1:
            raise passages.ControleImpossible(f"{chemin.name} : nombre mal formé")
        if motif not in MOTIFS:
            raise passages.ControleImpossible(f"{chemin.name} : motif inconnu")
        if (fichier, empreinte) in admises:
            raise passages.ControleImpossible(f"{chemin.name} : doublon")
        admises[(fichier, empreinte)] = int(nombre)
    return admises


def fichiers_suivis(racine: Path) -> list[str]:
    sortie = subprocess.run(
        ["git", "ls-files", "-z"], cwd=racine, capture_output=True, check=True
    ).stdout
    return [nom for nom in sortie.decode("utf-8").split("\0") if nom]


def lire_fichiers(racine: Path, chemins: Iterable[str]) -> dict[str, str]:
    """Chemin → contenu ; les octets hors UTF-8 ne cachent pas le texte autour."""
    textes: dict[str, str] = {}
    for nom in chemins:
        chemin = racine / nom
        if chemin.is_symlink() or not chemin.is_file():
            continue
        textes[nom] = chemin.read_bytes().decode("utf-8", "replace")
    return textes


def compter(
    fichiers: Mapping[str, str], pseudos: Mapping[str, str], motif: Motif
) -> dict[Cle, int]:
    """Les occurrences par (chemin, empreinte), chemin du fichier compris."""
    motifs = {empreinte: motif(p) for empreinte, p in pseudos.items()}
    comptes: dict[Cle, int] = {}
    for nom, texte in fichiers.items():
        for empreinte, m in motifs.items():
            nombre = len(m.findall(texte)) + len(m.findall(nom))
            if nombre:
                comptes[(nom, empreinte)] = nombre
    return comptes


def controler(
    comptes: Mapping[Cle, int],
    admises: Mapping[Cle, int],
    fichiers: int,
    pseudonymes: int,
) -> dict[str, object]:
    """`hors_liste` et `ecarts` doivent rester vides."""
    if not pseudonymes:
        raise passages.ControleImpossible("aucun pseudonyme lu : contrôle vide")
    if not fichiers:
        raise passages.ControleImpossible("aucun fichier à contrôler")
    hors = sorted(
        (nom, empreinte, nombre)
        for (nom, empreinte), nombre in comptes.items()
        if (nom, empreinte) not in admises
    )
    ecarts = sorted(
        (nom, empreinte, attendu, comptes.get((nom, empreinte), 0))
        for (nom, empreinte), attendu in admises.items()
        if comptes.get((nom, empreinte), 0) != attendu
    )
    return {
        "fichiers": fichiers,
        "pseudonymes": pseudonymes,
        "occurrences": sum(comptes.values()),
        "admises": sum(admises.values()),
        "hors_liste": [list(h) for h in hors],
        "ecarts": [list(e) for e in ecarts],
    }


def principal(argv: list[str]) -> int:
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    import psycopg  # l'environnement de database/ ; absent des essais

    methode = passages.charger_methode()
    try:
        admises = lire_admises(FICHIER_ADMIS)
        dsn = os.environ.get("APIMANGA_VERIF_DSN")
        if not dsn:
            raise passages.ControleImpossible("APIMANGA_VERIF_DSN n'est pas posée")
        with psycopg.connect(dsn) as cx:
            lecture_seule, membre = cx.execute(passages.SQL_SESSION).fetchone() or (
                None,
                None,
            )
            if lecture_seule != "on" or membre is not True:
                raise passages.ControleImpossible(
                    "session hors de verif_ro ou en écriture"
                )
            pseudos = {
                methode.empreinte(a): a
                for (a,) in cx.execute(passages.SQL_AUTEURS).fetchall()
            }
        textes = lire_fichiers(RACINE, fichiers_suivis(RACINE))
        bilan = controler(
            compter(textes, pseudos, methode.motif), admises, len(textes), len(pseudos)
        )
    except (passages.ControleImpossible, psycopg.Error, OSError) as exc:
        print(f"pseudonymes des fichiers suivis : impossible — {exc}", file=sys.stderr)
        return 1
    print(json.dumps(bilan, ensure_ascii=False))
    if bilan["hors_liste"] or bilan["ecarts"]:
        print(
            "pseudonymes des fichiers suivis : occurrence hors de la liste, ou entrée"
            " de la liste qui ne tient plus (chemin, empreinte, nombres)",
            file=sys.stderr,
        )
        return 1
    print(
        f"pseudonymes des fichiers suivis : {bilan['fichiers']} fichiers,"
        f" {bilan['occurrences']} occurrence(s), toutes admises ; hors de la liste : 0"
    )
    return 0


if __name__ == "__main__":
    sys.exit(principal(sys.argv[1:]))
