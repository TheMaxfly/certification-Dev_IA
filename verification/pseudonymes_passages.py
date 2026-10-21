"""Les pseudonymes dans les passages rendus par l'API du modèle — une étape de l'état
d'apimanga (`make verify-poste`), sous `verif_ro`.

    (database/) uv run --locked python ../verification/pseudonymes_passages.py <bilan>

Règle 3.1 de l'étape 1 (corpus v2, décision D2) : les occurrences de pseudonymes dans
le texte des critiques sont exactement celles déjà connues — les références de
`corpus_references_masquees.csv` et les homonymes de `corpus_homonymes_admis.csv`
(`database/donnees/`). L'API rend le texte même des fragments : chaque passage qu'elle
a rendu pendant l'étape d'équivalence (les 69 questions du jeu gelé, au catalogue puis
sur tout le corpus) est relu ici, et une occurrence d'un pseudonyme hors de ces listes
est un échec.

La méthode est celle du corpus v2 : `corpus/pseudonymes.py` du module 05 (le pseudonyme
en mot entier, casse ignorée ; son empreinte, 16 caractères du SHA-256), chargé par son
chemin — le module 05 ne change pas. Témoin positif : sur la même base, chaque
référence connue est retrouvée dans les fragments de son document ; sans lui, une liste
de pseudonymes vide ou fausse ferait passer le contrôle à vide.

Les pseudonymes sont lus dans `manga.ms_reviews_all` et ne quittent pas la mémoire : la
sortie ne porte que des nombres, des identifiants et des empreintes.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import ModuleType

RACINE = Path(__file__).resolve().parents[1]
METHODE = RACINE / "05_nettoyage_agregation_bdd" / "src" / "corpus" / "pseudonymes.py"
DONNEES = RACINE / "database" / "donnees"
NOM_METHODE = "pseudonymes_du_corpus_v2"

SQL_SESSION = (
    "SELECT current_setting('default_transaction_read_only'),"
    " pg_has_role(current_user, 'verif_ro', 'MEMBER')"
)
SQL_EN_SERVICE = "SELECT encodage_id, corpus_id FROM bench.v_encodage_en_service"
SQL_FRAGMENTS = (
    "SELECT chunk_id, doc_key, chunk_text FROM bench.corpus_chunks"
    " WHERE corpus_id = %s AND chunk_id = ANY(%s)"
)
SQL_DOCUMENTS = (
    "SELECT chunk_id, doc_key, chunk_text FROM bench.corpus_chunks"
    " WHERE corpus_id = %s AND doc_key = ANY(%s)"
)
SQL_AUTEURS = (
    "SELECT DISTINCT review_author FROM manga.ms_reviews_all"
    " WHERE review_author IS NOT NULL"
)

Fragments = Mapping[int, tuple[str, str]]  # chunk_id → (doc_key, texte)
Couple = tuple[str, str]  # (doc_key, empreinte)
Motif = Callable[[str], re.Pattern[str]]


class ControleImpossible(Exception):
    """Une entrée manque ou ne tient pas : le contrôle ne peut pas conclure."""


def charger_methode() -> ModuleType:
    """`corpus/pseudonymes.py` du module 05, par son chemin (bibliothèque standard
    seule)."""
    deja = sys.modules.get(NOM_METHODE)
    if deja is not None:
        return deja
    spec = importlib.util.spec_from_file_location(NOM_METHODE, METHODE)
    if spec is None or spec.loader is None:
        raise ControleImpossible(f"méthode introuvable : {METHODE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[NOM_METHODE] = module  # les dataclasses du module le demandent
    spec.loader.exec_module(module)
    return module


def lire_bilan(chemin: Path) -> tuple[str, int, list[int]]:
    """(corpus, encodage, fragments) des passages que l'API a rendus."""
    try:
        bilan = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ControleImpossible(f"bilan illisible : {chemin} ({exc})") from exc
    passages = bilan.get("passages") if isinstance(bilan, dict) else None
    if not isinstance(passages, dict):
        raise ControleImpossible(f"{chemin.name} : pas de passages rendus")
    corpus, encodage, fragments = (
        passages.get("corpus_id"),
        passages.get("encodage_id"),
        passages.get("fragments"),
    )
    if not isinstance(corpus, str) or not isinstance(encodage, int):
        raise ControleImpossible(f"{chemin.name} : corpus ou encodage absent")
    if (
        not isinstance(fragments, list)
        or not fragments
        or not all(isinstance(f, int) and not isinstance(f, bool) for f in fragments)
    ):
        raise ControleImpossible(f"{chemin.name} : aucun fragment rendu")
    return corpus, encodage, sorted(set(fragments))


def occurrences(
    fragments: Fragments, pseudos: Mapping[str, str], motif: Motif
) -> dict[int, set[Couple]]:
    """Pour chaque fragment, les couples (document, empreinte) qu'il porte."""
    motifs = {h: motif(p) for h, p in pseudos.items()}
    trouves: dict[int, set[Couple]] = {}
    for chunk_id, (doc_key, texte) in fragments.items():
        couples = {(doc_key, h) for h, m in motifs.items() if m.search(texte)}
        if couples:
            trouves[chunk_id] = couples
    return trouves


def reunir(trouves: Mapping[int, set[Couple]]) -> set[Couple]:
    vus: set[Couple] = set()
    for couples in trouves.values():
        vus |= couples
    return vus


def controler(
    fragments: Fragments,
    pseudos: Mapping[str, str],
    connus: Iterable[Couple],
    motif: Motif,
) -> dict[str, object]:
    """Les occurrences des passages, contre ce qui est connu ; `hors_liste` doit
    rester vide."""
    if not pseudos:
        raise ControleImpossible("aucun pseudonyme lu : le contrôle serait vide")
    if not fragments:
        raise ControleImpossible("aucun passage à contrôler")
    admis = set(connus)
    trouves = occurrences(fragments, pseudos, motif)
    couples = reunir(trouves)
    hors = sorted(
        (chunk_id, doc_key, h)
        for chunk_id, cs in trouves.items()
        for doc_key, h in cs
        if (doc_key, h) not in admis
    )
    return {
        "passages": len(fragments),
        "documents": len({d for d, _ in fragments.values()}),
        "pseudonymes": len(pseudos),
        "passages_avec_pseudonyme": len(trouves),
        "couples_trouves": len(couples),
        "couples_connus": len(couples & admis),
        "hors_liste": [list(h) for h in hors],
    }


def temoin(
    fragments: Fragments,
    pseudos: Mapping[str, str],
    references: Iterable[Couple],
    motif: Motif,
) -> list[Couple]:
    """Les références connues que la méthode ne retrouve PAS dans leur document."""
    return sorted(set(references) - reunir(occurrences(fragments, pseudos, motif)))


def principal(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    import psycopg  # l'environnement de database/ ; absent des essais

    methode = charger_methode()
    try:
        corpus, encodage, ids = lire_bilan(Path(argv[0]))
        masquage, homonymes = methode.lire_listes(DONNEES)
        connus = {(e.doc_key, e.empreinte) for e in [*masquage, *homonymes]}
        references = {(e.doc_key, e.empreinte) for e in masquage}
        dsn = os.environ.get("APIMANGA_VERIF_DSN")
        if not dsn:
            raise ControleImpossible("APIMANGA_VERIF_DSN n'est pas posée")
        with psycopg.connect(dsn) as cx:
            lecture_seule, membre = cx.execute(SQL_SESSION).fetchone() or (None, None)
            if lecture_seule != "on" or membre is not True:
                raise ControleImpossible("session hors de verif_ro ou en écriture")
            en_service = cx.execute(SQL_EN_SERVICE).fetchone()
            if en_service != (encodage, corpus):
                raise ControleImpossible(
                    f"passages rendus sous l'encodage {encodage} (corpus {corpus}) ;"
                    f" en service : {en_service} — rejouer l'équivalence"
                )
            rendus = {
                int(c): (str(d), str(t))
                for c, d, t in cx.execute(SQL_FRAGMENTS, (corpus, ids)).fetchall()
            }
            if len(rendus) != len(ids):
                raise ControleImpossible(
                    f"{len(ids) - len(rendus)} fragment(s) rendu(s) absent(s) du"
                    f" corpus {corpus}"
                )
            docs_references = sorted({d for d, _ in references})
            fragments_references = {
                int(c): (str(d), str(t))
                for c, d, t in cx.execute(
                    SQL_DOCUMENTS, (corpus, docs_references)
                ).fetchall()
            }
            pseudos = {
                methode.empreinte(a): a for (a,) in cx.execute(SQL_AUTEURS).fetchall()
            }
        manquees = temoin(fragments_references, pseudos, references, methode.motif)
        if manquees:
            raise ControleImpossible(
                f"témoin : {len(manquees)} référence(s) connue(s) non retrouvée(s)"
                f" dans leur document : {manquees}"
            )
        bilan = controler(rendus, pseudos, connus, methode.motif)
    except (ControleImpossible, methode.ListeInvalide, psycopg.Error) as exc:
        print(f"pseudonymes des passages : impossible — {exc}", file=sys.stderr)
        return 1
    bilan["temoin_references_retrouvees"] = len(references)
    print(json.dumps(bilan, ensure_ascii=False))
    hors = bilan["hors_liste"]
    if hors:
        print(
            f"pseudonymes des passages : {len(hors)} occurrence(s) hors de la liste"
            " connue (fragment, document, empreinte)",
            file=sys.stderr,
        )
        return 1
    print(
        f"pseudonymes des passages : {bilan['passages']} passages rendus,"
        f" {bilan['couples_trouves']} occurrence(s) (document, pseudonyme), toutes"
        f" connues ; hors de la liste : 0 ; témoin : {len(references)} références"
        " retrouvées"
    )
    return 0


if __name__ == "__main__":
    sys.exit(principal(sys.argv[1:]))
