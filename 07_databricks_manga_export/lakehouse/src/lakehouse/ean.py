"""Validité EAN-13 — RÉIMPLÉMENTÉ À L'IDENTIQUE depuis le module 05 (« B2 »).

Même algorithme que `05_.../src/identity/ean.py::ean13_valide` : 13 chiffres,
poids alternés 1 et 3 sur les 12 premiers, le 13e est la clé de contrôle
`(10 - somme % 10) % 10`. Ne répare rien — signale invalide, l'appelant décide.

Deux formes du même calcul, testées l'une contre l'autre :
  - `ean13_valide` : Python pur (pour les tests contre valeurs de référence) ;
  - `expr_ean13_valide` : expression Spark NATIVE (pas d'UDF Python : reste
    dans la JVM, vectorisé) pour la couche silver sur 100 k+ lignes.
"""

from __future__ import annotations

from pyspark.sql import Column
from pyspark.sql import functions as F


def ean13_valide(valeur: str | None) -> bool:
    """La chaîne est-elle un EAN-13 dont la clé de contrôle tombe juste ?

    >>> ean13_valide("9782355929489")
    True
    >>> ean13_valide("9782355929488")   # dernier chiffre faux
    False
    >>> ean13_valide("978235592948")    # 12 chiffres
    False
    """
    if valeur is None:
        return False
    chaine = valeur.strip()
    if len(chaine) != 13 or not chaine.isdigit():
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(chaine[:12]))
    return (10 - total % 10) % 10 == int(chaine[12])


def expr_ean13_valide(colonne: Column) -> Column:
    """Colonne booléenne Spark : même règle que `ean13_valide`, sans UDF.

    Reconstruit la somme pondérée par `substring` (indexé à partir de 1) sur
    les 12 premiers chiffres, compare la clé calculée au 13e chiffre. Une
    valeur non conforme au motif `^[0-9]{13}$` rend False (jamais null).
    """
    chaine = F.trim(colonne.cast("string"))
    conforme = chaine.rlike("^[0-9]{13}$")

    total: Column | None = None
    for i in range(12):
        chiffre = F.substring(chaine, i + 1, 1).cast("int")
        poids = 1 if i % 2 == 0 else 3
        terme = chiffre * F.lit(poids)
        total = terme if total is None else total + terme

    cle_calculee = (F.lit(10) - (total % F.lit(10))) % F.lit(10)
    dernier = F.substring(chaine, 13, 1).cast("int")
    return F.when(conforme, cle_calculee == dernier).otherwise(F.lit(False))
