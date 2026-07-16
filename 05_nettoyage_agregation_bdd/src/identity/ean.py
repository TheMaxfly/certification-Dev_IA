"""Validation de la clé de contrôle EAN-13.

Vit en Python, comme `normaliser()` et `parser_date_fr()`, et pour la raison
déjà écrite dans la migration 001 : « Le calcul n'a pas sa place en SQL : il
appartient au pipeline, qui sait aussi quoi faire d'un EAN invalide ».

Ce que la fonction ne fait PAS : réparer. Un EAN à 12 chiffres n'est pas
complété, un EAN à clé fausse n'est pas corrigé — les deux sont signalés
invalides et l'appelant décide. Sur le snapshot 2026-07 : 64 259 EAN
renseignés, dont 63 627 valides (99,02 %) — le ~1 % restant est une donnée
source imparfaite, pas un bug à masquer.
"""

from __future__ import annotations


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
    # Poids alternés 1 et 3 sur les 12 premiers chiffres ; le 13e est la clé.
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(chaine[:12]))
    return (10 - total % 10) % 10 == int(chaine[12])


def isbn13_ou_none(valeur: str | None) -> str | None:
    """L'EAN normalisé s'il est valide, sinon None.

    Le zéro de tête est significatif : on rend une chaîne, jamais un entier
    (cf. le CHECK `isbn13 ~ '^[0-9]{13}$'` de manga.volume_identity).
    """
    if valeur is None:
        return None
    chaine = valeur.strip()
    return chaine if ean13_valide(chaine) else None
