"""Les pseudonymes des membres, en mémoire : le contrôle « aucun nom de membre ».

Décision du 26 octobre 2026 (§ 7 bis du document de décisions de l'étape 3). Au
démarrage, l'API lit les noms de `manga.v_pseudonymes` (migration 024 : les noms
distincts, jamais qui a écrit quelle critique) sous son rôle, et la liste des
homonymes admis de l'étape 1 (`database/donnees/corpus_homonymes_admis.csv`, des
empreintes, aucun nom). Ni l'une ni l'autre n'est journalisée ni rendue : seuls leurs
nombres sortent. Vue illisible ou vide, liste illisible : l'API refuse de démarrer.

La méthode est celle du corpus v2 : `corpus/pseudonymes.py` du module 05 (le nom en
mot entier, casse ignorée ; son empreinte, 16 caractères du SHA-256 ; la lecture des
listes versionnées), chargée par son chemin comme le fait
`verification/pseudonymes_passages.py` — pas de copie, le module 05 ne change pas.

Règle des homonymes : un nom trouvé dans une sortie du modèle n'est admis que s'il
figure dans un passage envoyé POUR CETTE REQUÊTE et que le couple (document de ce
passage, empreinte du nom) est dans la liste admise. Le modèle peut répéter un nom de
personnage qu'on lui a donné ; il ne peut en produire aucun autre.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from psycopg import Error as ErreurBase
from psycopg_pool import PoolTimeout

from api_modele import base
from api_modele.base import Pool
from api_modele.configuration import Reglages
from api_modele.demarrage import DemarrageRefuse

RACINE_DEPOT = Path(__file__).resolve().parents[3]
METHODE = (
    RACINE_DEPOT / "05_nettoyage_agregation_bdd" / "src" / "corpus" / "pseudonymes.py"
)
FICHIER_HOMONYMES = RACINE_DEPOT / "database" / "donnees" / "corpus_homonymes_admis.csv"
#: Le nom sous lequel la méthode est chargée (celui de `verification/`).
NOM_METHODE = "pseudonymes_du_corpus_v2"
VUE = "manga.v_pseudonymes"
SQL_NOMS = "SELECT review_author FROM manga.v_pseudonymes"

type Couple = tuple[str, str]  # (doc_key, empreinte)


def methode() -> ModuleType:
    """`corpus/pseudonymes.py` du module 05, par son chemin ; une fois par processus."""
    deja = sys.modules.get(NOM_METHODE)
    if deja is not None:
        return deja
    spec = importlib.util.spec_from_file_location(NOM_METHODE, METHODE)
    if spec is None or spec.loader is None:
        raise ImportError(f"méthode des pseudonymes introuvable : {METHODE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[NOM_METHODE] = module  # les dataclasses du module le demandent
    spec.loader.exec_module(module)
    return module


def empreinte(nom: str) -> str:
    return cast(Callable[[str], str], methode().empreinte)(nom)


def motif(nom: str) -> re.Pattern[str]:
    return cast(Callable[[str], re.Pattern[str]], methode().motif)(nom)


def lire_homonymes(fichier: Path = FICHIER_HOMONYMES) -> frozenset[Couple]:
    """Les couples (document, empreinte) admis, lus et vérifiés par la méthode du 05."""
    m = methode()
    lire = cast(Callable[[Path, str, tuple[str, ...]], list[Any]], m.lire_liste)
    entrees = lire(fichier, "motif", cast(tuple[str, ...], m.MOTIFS_HOMONYMIE))
    return frozenset((str(e.doc_key), str(e.empreinte)) for e in entrees)


class Pseudonymes:
    """Les noms (un motif par empreinte) et les homonymes admis. `repr` ne montre que
    des nombres ; le contrôle rend un nombre, jamais un nom."""

    __slots__ = ("_admis", "_motifs")

    def __init__(self, noms: Iterable[str], admis: Iterable[Couple]) -> None:
        self._motifs = {empreinte(n): motif(n) for n in noms}
        self._admis = frozenset(admis)

    def __repr__(self) -> str:
        return f"Pseudonymes(noms={self.nb_noms}, homonymes_admis={self.nb_admis})"

    @property
    def nb_noms(self) -> int:
        return len(self._motifs)

    @property
    def nb_admis(self) -> int:
        return len(self._admis)

    def fautifs(self, texte: str, passages: Iterable[tuple[str, str]]) -> int:
        """Le nombre de noms présents dans `texte` et non admis. `passages` : les
        passages envoyés pour cette requête, `(doc_key, texte)`."""
        envoyes = list(passages)
        n = 0
        for h, m in self._motifs.items():
            if m.search(texte) is None:
                continue
            if any((doc, h) in self._admis and m.search(t) for doc, t in envoyes):
                continue
            n += 1
        return n


def charger(
    pool: Pool, reglages: Reglages, fichier_homonymes: Path = FICHIER_HOMONYMES
) -> Pseudonymes:
    """La liste du démarrage ; `DemarrageRefuse` (le nom de la vue ou du fichier,
    jamais un contenu) si elle ne se lit pas ou si elle est vide."""
    try:
        with base.transaction(pool, reglages) as cx:
            noms = [str(n) for (n,) in cx.execute(SQL_NOMS).fetchall() if n]
    except (ErreurBase, PoolTimeout) as exc:
        raise DemarrageRefuse(
            f"{VUE} illisible sous le rôle de l'API ({type(exc).__name__})"
        ) from None
    if not noms:
        raise DemarrageRefuse(
            f"{VUE} est vide : le contrôle « aucun nom de membre » serait sans objet"
        )
    erreur_liste = cast(type[Exception], methode().ListeInvalide)
    try:
        admis = lire_homonymes(fichier_homonymes)
    except (OSError, erreur_liste):
        raise DemarrageRefuse(
            f"liste des homonymes admis illisible : {fichier_homonymes.name}"
        ) from None
    return Pseudonymes(noms, admis)
