"""Normalisation de préfixe de titre pour le contrôle de complétude.

Le gold `completude_par_prefixe` compte les séries par « 2 premiers caractères
normalisés » du titre. On veut qu'un déficit localisé (le trou de crawl « Di »)
saute aux yeux : la normalisation doit donc être stable et insensible à la
casse/aux accents, sans dépendre du `normaliser()` riche du module 05 (couplage
inutile — ici on ne rapproche pas des œuvres, on range des titres en rayons).

Expression Spark native (pas d'UDF) + jumelle Python pour les tests.
"""

from __future__ import annotations

import unicodedata

from pyspark.sql import Column
from pyspark.sql import functions as F

# Translittération des accents/ligatures FR+latin courants (source → cible,
# même longueur). Suffit pour ranger des titres ; l'exhaustivité Unicode n'est
# pas le but (le préfixe sert de tiroir, pas de clé d'identité).
_ACCENTS_SRC = "àâäáãåçéèêëíìîïóòôöõøúùûüýÿñ"
_ACCENTS_DST = "aaaaaaceeeeiiiioooooouuuuyyn"


def prefixe_titre(titre: str | None, n: int = 2) -> str:
    """Préfixe normalisé (jumelle Python de `expr_prefixe`)."""
    if not titre:
        return ""
    base = unicodedata.normalize("NFKD", titre.strip().lower())
    base = "".join(c for c in base if not unicodedata.combining(c))
    base = "".join(c for c in base if c.isalnum() and c.isascii())
    return base[:n]


def expr_prefixe(colonne: Column, n: int = 2) -> Column:
    """Colonne des `n` premiers caractères alphanumériques normalisés."""
    base = F.lower(F.trim(colonne.cast("string")))
    base = F.translate(base, _ACCENTS_SRC, _ACCENTS_DST)
    base = F.regexp_replace(base, "[^a-z0-9]", "")
    return F.substring(base, 1, n)
