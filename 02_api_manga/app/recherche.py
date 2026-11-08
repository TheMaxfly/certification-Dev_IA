"""La normalisation de la recherche du catalogue (`GET /series`) : une table, deux
usages.

La même table sert en SQL, `translate(lower(<colonne>), %(de)s, %(vers)s)` — ses
deux chaînes LIÉES en paramètres, jamais écrites dans la requête —, et en Python, sur
la saisie avant de la lier (`normaliser`). Minuscules d'abord, puis la table : elle ne
porte que des minuscules accentuées, et « ÉTÉ » doit devenir « ete » des deux côtés.

Sans extension ni migration : le lot 2b remplacera cette table par `unaccent` et les
trigrammes (`pg_trgm`, migration 028), avec la tolérance aux fautes de frappe.
"""

from __future__ import annotations

#: Les lettres accentuées, et leur lettre sans accent, à la même position.
ACCENTS = (
    "àâäáãåāèéêëēìíîïīòóôöõøōùúûüūçñýÿ",
    "aaaaaaaeeeeeiiiiiooooooouuuuucnyy",
)
_TABLE = str.maketrans(*ACCENTS)

#: Les jokers de `LIKE` et le caractère d'échappement (`ESCAPE '\\'`).
_JOKERS = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


def normaliser(texte: str) -> str:
    """La forme comparée : minuscules, sans accent (la table `ACCENTS`)."""
    return texte.lower().translate(_TABLE)


def echapper(texte: str) -> str:
    """Une saisie rendue littérale dans un motif `LIKE … ESCAPE '\\'` : `%`, `_` et `\\`
    ne sont plus des jokers."""
    return texte.translate(_JOKERS)


def parametres(saisie: str) -> dict[str, str]:
    """Les paramètres liés de la recherche : la saisie normalisée, exacte (rang 1),
    en début (rang 2), contenue (rangs 3 et 4) ; et les deux chaînes de la table."""
    forme = normaliser(saisie)
    litterale = echapper(forme)
    return {
        "de": ACCENTS[0],
        "vers": ACCENTS[1],
        "exact": forme,
        "debut": f"{litterale}%",
        "contient": f"%{litterale}%",
    }
