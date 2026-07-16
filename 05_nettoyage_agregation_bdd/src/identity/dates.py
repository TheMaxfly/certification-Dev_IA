"""Parsing des dates françaises de Manga Sanctuary.

Pendant de `normaliser()` pour les dates : une seule implémentation, testée,
plutôt qu'une seconde en SQL. `to_date('27 nov. 2012', 'DD mon YYYY')` dépend
du `lc_time` du serveur — la même migration donnerait deux résultats sur deux
machines, et un `NULL` silencieux sur celle qui n'est pas en français.

Reprend la convention du notebook `analyse_ms_reviews_step2` (module 05), qui a
produit les `review_date_iso` déjà en base : mois abrégés ou complets, préfixe
de jour de semaine, « 1er », et forme numérique jour/mois/année.

Ce que le parseur ne fait PAS : deviner. La source produit des valeurs qui ne
sont pas des dates — la sentinelle « Date inconnue », le « A paraître » d'un
tome annoncé, et surtout des dates tronquées au seul jour de la semaine
(« jeu. »). Toutes donnent None, jamais une date approchée : une date fausse
est bien pire qu'une date absente pour un tri chronologique.
"""

from __future__ import annotations

import re
from datetime import date

MOIS = {
    "janvier": 1,
    "janv": 1,
    "fevrier": 2,
    "février": 2,
    "fevr": 2,
    "févr": 2,
    "mars": 3,
    "avril": 4,
    "avr": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "juil": 7,
    "aout": 8,
    "août": 8,
    "septembre": 9,
    "sept": 9,
    "octobre": 10,
    "oct": 10,
    "novembre": 11,
    "nov": 11,
    "decembre": 12,
    "décembre": 12,
    "dec": 12,
    "déc": 12,
}

# « mar. 27 nov. 2012 » -> « 27 nov. 2012 ». Le jour de la semaine n'apporte
# rien : il est redondant avec la date quand elle est là, et seul quand elle
# manque.
PREFIXE_JOUR = re.compile(r"^(lun|mar|mer|jeu|ven|sam|dim)\.?\s*", re.IGNORECASE)
JOUR_MOIS_ANNEE = re.compile(r"^(\d{1,2})\s+([^\s\d]+)\s+(\d{4})$")
NUMERIQUE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$")


def parser_date_fr(valeur: str | None) -> date | None:
    """Date française -> `date`, ou None si la valeur n'en est pas une.

    >>> parser_date_fr("mar. 27 nov. 2012")
    datetime.date(2012, 11, 27)
    >>> parser_date_fr("1er avril 2015")
    datetime.date(2015, 4, 1)
    >>> parser_date_fr("jeu.") is None      # tronquée par la source
    True
    >>> parser_date_fr("Date inconnue") is None
    True
    """
    if valeur is None:
        return None
    texte = str(valeur).strip()
    if not texte:
        return None

    texte = PREFIXE_JOUR.sub("", texte).lower().strip()
    if not texte:  # la valeur n'était QUE le jour de la semaine
        return None
    texte = texte.replace("1er", "1")

    correspondance = JOUR_MOIS_ANNEE.match(texte)
    if correspondance:
        jour, mois_brut, annee = correspondance.groups()
        mois = MOIS.get(mois_brut.rstrip("."))
        if mois is None:
            return None
        return _date_ou_none(int(annee), mois, int(jour))

    correspondance = NUMERIQUE.match(texte)
    if correspondance:
        jour, mois, annee = (int(g) for g in correspondance.groups())
        if annee < 100:  # « 27/11/12 » : siècle courant, comme le notebook
            annee += 2000
        return _date_ou_none(annee, mois, jour)

    return None


def _date_ou_none(annee: int, mois: int, jour: int) -> date | None:
    """Une date impossible (« 31 nov. ») est une donnée fausse, pas une date."""
    try:
        return date(annee, mois, jour)
    except ValueError:
        return None


def iso_ou_none(valeur: str | None) -> str | None:
    """Forme ISO 8601 attendue par le staging, ou None."""
    parsee = parser_date_fr(valeur)
    return parsee.isoformat() if parsee else None
