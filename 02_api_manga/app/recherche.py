"""La normalisation de la recherche du catalogue (`GET /series`) : deux chemins, une
règle.

**La fonction** (E4, lot 2b, migration 028) : `manga.normaliser(text)`, `lower` et
`unaccent`, immuable, indexée en trigrammes sur le titre, les autres titres et les
auteurs. La saisie est normalisée par la même fonction, en base (`SAISIE_FONCTION`),
avant d'être liée : les deux côtés passent par le même dictionnaire. Elle seule permet
les **suggestions** (« Vouliez-vous dire… ? », `pg_trgm`).

**Le repli** (la table du lot 2a) : quand la base n'a pas la 028 — la production,
jusqu'au lot de livraison continue —, `translate(lower(<colonne>), %(de)s,
%(vers)s)`, ses deux
chaînes LIÉES en paramètres, jamais écrites dans la requête, et la même table en Python
sur la saisie (`normaliser`). Minuscules d'abord, puis la table : elle ne porte que des
minuscules accentuées, et « ÉTÉ » doit devenir « ete » des deux côtés. Gardée jusqu'à ce
que la production ait la 028 ; elle sert aussi, caractère pour caractère, à la coupe des
résumés (`resumes`), là où `unaccent` change la longueur (« œ » → « oe »).

Le chemin est détecté au démarrage (`to_regprocedure('manga.normaliser(text)')`), dit à
`/health` et au journal.
"""

from __future__ import annotations

from typing import Literal

type Normalisation = Literal["fonction", "repli"]
FONCTION: Normalisation = "fonction"
REPLI: Normalisation = "repli"

#: La détection : la fonction de la 028 existe-t-elle dans la base ?
DETECTION = "SELECT to_regprocedure('manga.normaliser(text)') IS NOT NULL"
#: La saisie normalisée par la fonction : telle quelle (rang exact, ressemblance), et
#: échappée pour `LIKE` (le dictionnaire ne touche ni `%`, ni `_`, ni `\\`).
SAISIE_FONCTION = "SELECT manga.normaliser(%(saisie)s), manga.normaliser(%(litterale)s)"

#: Les lettres accentuées, et leur lettre sans accent, à la même position.
ACCENTS = (
    "àâäáãåāèéêëēìíîïīòóôöõøōùúûüūçñýÿ",
    "aaaaaaaeeeeeiiiiiooooooouuuuucnyy",
)
_TABLE = str.maketrans(*ACCENTS)

#: Les jokers de `LIKE` et le caractère d'échappement (`ESCAPE '\\'`, qui est aussi
#: celui, par défaut, de `LIKE ALL`).
_JOKERS = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


def normaliser(texte: str) -> str:
    """La forme comparée du repli : minuscules, sans accent (la table `ACCENTS`)."""
    return texte.lower().translate(_TABLE)


def normaliser_a_longueur_egale(texte: str) -> str:
    """La table du repli, caractère pour caractère : la longueur ne change jamais (un
    caractère dont la minuscule en fait deux reste tel quel) — les positions trouvées
    se reportent sur le texte d'origine."""
    sortie: list[str] = []
    for caractere in texte:
        minuscule = caractere.lower()
        sortie.append(minuscule.translate(_TABLE) if len(minuscule) == 1 else caractere)
    return "".join(sortie)


def echapper(texte: str) -> str:
    """Une saisie rendue littérale dans un motif `LIKE … ESCAPE '\\'` : `%`, `_` et `\\`
    ne sont plus des jokers."""
    return texte.translate(_JOKERS)


def colonne(expression: str, normalisation: Normalisation) -> str:
    """Une colonne normalisée, dans le SQL : la fonction (l'expression même des index de
    la 028, sans `coalesce`, pour que l'index serve), ou la table liée du repli."""
    if normalisation == FONCTION:
        return f"manga.normaliser({expression})"
    return f"translate(lower(coalesce({expression}, '')), %(de)s, %(vers)s)"


def motifs(exacte: str, litterale: str) -> dict[str, object]:
    """Les paramètres liés, la saisie déjà normalisée : exacte, en début, contenue ; et
    chacun de ses mots contenu (`mots`, `LIKE ALL`) — un auteur se trouve dans un ordre
    ou dans l'autre (« Urasawa Naoki », « Naoki Urasawa »)."""
    return {
        "exact": exacte,
        "debut": f"{litterale}%",
        "contient": f"%{litterale}%",
        "mots": [f"%{mot}%" for mot in litterale.split()],
    }


def parametres(saisie: str) -> dict[str, object]:
    """Les paramètres liés du repli : la saisie normalisée par la table, et les deux
    chaînes de la table."""
    forme = normaliser(saisie)
    litterale = echapper(forme)
    return {
        "de": ACCENTS[0],
        "vers": ACCENTS[1],
        **motifs(forme, litterale),
    }
