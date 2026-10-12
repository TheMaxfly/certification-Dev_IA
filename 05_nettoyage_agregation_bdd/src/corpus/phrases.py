"""Découpage par phrases — la règle du corpus v2 (E3, étape 1).

    uv run python -m corpus.phrases                # les réglages lus
    uv run python -m corpus.phrases --enregistrer  # inscrit la stratégie nommée

Validée par Max le 2026-10-09 sur simulation de tout le corpus v2. Réglages lus
dans `config/decoupage_phrases.toml` : taille, recouvrement, abréviations.

LE REPÉRAGE DES PHRASES — une règle écrite, sans modèle

Le texte, épuré de ses espaces de bord comme au découpage v1, est PARTITIONNÉ en
segments contigus : un segment = une phrase + les espaces qui la suivent. La
concaténation des segments rend le texte à l'identique.

Une fin de phrase est posée :
  R1  après un saut de ligne, toujours ;
  R2  après 。！？ (ponctuation japonaise) et les fermants collés, sans condition ;
  R3  après une suite de . ! ? … (« ... » compris) et ses guillemets ou
      parenthèses fermants, SI elle termine le texte, OU si elle est suivie d'une
      espace et que le premier caractère non blanc qui suit ouvre une phrase :
      majuscule, chiffre, guillemet ou parenthèse ouvrants, lettre d'une écriture
      sans casse ;
  R4  SAUF quand la suite se réduit à un point qui clôt une abréviation de la
      liste, ou une initiale (une lettre seule : « J. K. »).

Effets voulus : « Pourquoi ? » demanda-t-il » ne coupe pas (minuscule après le
fermant) ; « vol. 3 », « T. 12 », « M. Dupont », « J. K. Rowling » non plus.
Limite connue : une phrase qui finit par une lettre seule (« le plan B. Puis »)
n'est pas coupée là.

LE REGROUPEMENT
  - un fragment = une suite de segments entiers, tant que sa longueur (espaces de
    fin retirées) ne dépasse pas la TAILLE ;
  - recouvrement : le dernier segment du fragment précédent, s'il tient dans le
    RECOUVREMENT ; sinon aucun. S'il empêche la première phrase neuve de tenir, il
    est abandonné pour ce fragment (compté) ;
  - une phrase plus longue que la TAILLE est coupée à la ponctuation (; : ,) suivie
    d'une espace la plus proche de la limite, à défaut à la dernière espace ;
    jamais au milieu d'un mot. Le cas d'un mot plus long que la TAILLE, sans
    espace, serait compté (`coupe_milieu_de_mot`) : il n'existe pas dans le
    corpus v2 (simulation du bloc A).

Un fragment est la tranche exacte `texte[debut:fin]` du texte épuré ; `nouveau`
marque où commence sa partie qui n'est pas un recouvrement. Aucune troncature.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tomllib
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

FICHIER_REGLAGES = Path(__file__).resolve().parents[2] / "config/decoupage_phrases.toml"

FINS = ".!?…"
FINS_JAPONAISES = "。！？"
FERMANTS = '»"”’)]』」›'
OUVRANTS = '«"“‘([『「‹¿¡'
ESPACES = " \t  "
_MOT_AVANT = re.compile(r"([\w.]+)$")


@dataclass(frozen=True)
class Reglages:
    nom: str
    taille: int
    recouvrement: int
    abreviations: frozenset[str]
    empreinte: str  # sha256 du fichier de réglages


def charger_reglages(chemin: Path = FICHIER_REGLAGES) -> Reglages:
    brut = chemin.read_bytes()
    s = tomllib.loads(brut.decode("utf-8"))["strategie"]
    if not 0 < s["recouvrement"] < s["taille"]:
        raise ValueError(f"{chemin.name} : 0 < recouvrement < taille exigé")
    return Reglages(
        nom=s["nom"],
        taille=s["taille"],
        recouvrement=s["recouvrement"],
        abreviations=frozenset(s["abreviations"]),
        empreinte=hashlib.sha256(brut).hexdigest(),
    )


# --------------------------------------------------------------------------- #
#  Les phrases
# --------------------------------------------------------------------------- #


def _ouvre_phrase(c: str) -> bool:
    if c.isupper() or c.isdigit() or c in OUVRANTS:
        return True
    return c.isalpha() and not c.islower()  # écriture sans casse


def _abreviation(texte: str, point: int, abreviations: frozenset[str]) -> bool:
    m = _MOT_AVANT.search(texte, 0, point)
    mot = m.group(1).lower().strip(".") if m else ""
    if len(mot) == 1 and mot.isalpha():
        return True  # initiale
    return mot in abreviations


def bornes(texte: str, abreviations: frozenset[str]) -> list[int]:
    """Début de chaque segment, 0 compris."""
    debuts, n, i = [0], len(texte), 0
    while i < n:
        c, fin = texte[i], None
        if c == "\n":  # R1
            fin = i + 1
        elif c in FINS_JAPONAISES:  # R2
            fin = i + 1
            while fin < n and texte[fin] in FERMANTS:
                fin += 1
        elif c in FINS:  # R3
            j = i
            while j < n and texte[j] in FINS:
                j += 1
            k = j  # fermants, collés ou précédés d'une espace (« ! » »)
            while k < n and (
                texte[k] in FERMANTS
                or (texte[k] == " " and k + 1 < n and texte[k + 1] in FERMANTS)
            ):
                k += 1
            if k >= n:
                fin = n
            elif texte[k].isspace():
                s = k
                while s < n and texte[s] in ESPACES:
                    s += 1
                ouvre = s >= n or texte[s] == "\n" or _ouvre_phrase(texte[s])
                abrege = texte[i:j] == "." and _abreviation(
                    texte, i, abreviations
                )  # R4
                if ouvre and not abrege:
                    fin = k
            if fin is None:
                i = j
                continue
        if fin is not None:
            while fin < n and texte[fin] in ESPACES:
                fin += 1
            if fin < n:
                debuts.append(fin)
            i = fin
            continue
        i += 1
    return debuts


def segments(texte: str, abreviations: frozenset[str]) -> list[tuple[int, int]]:
    d = bornes(texte, abreviations) + [len(texte)]
    return [(d[k], d[k + 1]) for k in range(len(d) - 1)]


# --------------------------------------------------------------------------- #
#  Les fragments
# --------------------------------------------------------------------------- #


def _longueur(texte: str, debut: int, fin: int) -> int:
    return len(texte[debut:fin].rstrip())


def _couper_longue(
    texte: str, debut: int, fin: int, taille: int, bilan: Counter
) -> list[tuple[int, int]]:
    morceaux = []
    while _longueur(texte, debut, fin) > taille:
        fenetre = texte[debut : debut + taille]
        ponct = [m.end() for m in re.finditer(r"[;:,](?=\s)", fenetre)]
        if ponct:
            coupe = ponct[-1]
            bilan["coupe_ponctuation"] += 1
        else:
            espace = fenetre.rfind(" ", 1)
            if espace > 0:
                coupe = espace
                bilan["coupe_espace"] += 1
            else:
                coupe = taille
                bilan["coupe_milieu_de_mot"] += 1
        c = debut + coupe
        while c < fin and texte[c].isspace():
            c += 1
        morceaux.append((debut, c))
        debut = c
    morceaux.append((debut, fin))
    return morceaux


@dataclass(frozen=True)
class Fragment:
    debut: int
    fin: int
    texte: str
    nouveau: int  # début de la partie qui n'est pas un recouvrement


def decouper(texte: str | None, reglages: Reglages) -> tuple[list[Fragment], Counter]:
    """Les fragments du texte épuré, dans l'ordre, et le bilan des coupes."""
    bilan: Counter = Counter()
    texte = (texte or "").strip()
    if not texte:
        return [], bilan
    taille, recouvrement = reglages.taille, reglages.recouvrement
    unites: list[tuple[int, int]] = []
    segs = segments(texte, reglages.abreviations)
    bilan["phrases"] += len(segs)
    for d, f in segs:
        if _longueur(texte, d, f) > taille:
            bilan["phrases_coupees"] += 1
            unites.extend(_couper_longue(texte, d, f, taille, bilan))
        else:
            unites.append((d, f))

    def etendre(depart: int, i: int) -> int:
        j = i
        while j < len(unites) and _longueur(texte, depart, unites[j][1]) <= taille:
            j += 1
        return j

    fragments: list[Fragment] = []
    i, precedente = 0, None
    while i < len(unites):
        depart = unites[i][0]
        j = None
        if precedente is not None:
            if _longueur(texte, *unites[precedente]) <= recouvrement:
                j = etendre(unites[precedente][0], i)
                if j > i:
                    depart = unites[precedente][0]
                    bilan["recouvrement"] += 1
                else:
                    j = None
                    bilan["recouvrement_abandonne"] += 1
            else:
                bilan["sans_recouvrement_phrase_trop_longue"] += 1
        if j is None:
            j = etendre(depart, i)
        fin = depart + _longueur(texte, depart, unites[j - 1][1])
        fragments.append(Fragment(depart, fin, texte[depart:fin], unites[i][0]))
        precedente, i = j - 1, j
    return fragments, bilan


def reconstituer(texte: str | None, fragments: list[Fragment]) -> str:
    """Le texte épuré, recomposé à partir des fragments, recouvrements retirés."""
    texte = (texte or "").strip()
    nouveaux = [f.nouveau for f in fragments] + [len(texte)]
    return "".join(texte[nouveaux[k] : nouveaux[k + 1]] for k in range(len(fragments)))


def dans_un_mot(texte: str, position: int) -> bool:
    """Une coupe en `position` tombe-t-elle entre deux caractères de mot ?"""
    if position <= 0 or position >= len(texte):
        return False
    return all(
        unicodedata.category(c)[0] in "LN" for c in texte[position - 1 : position + 1]
    )


# --------------------------------------------------------------------------- #
#  La stratégie nommée, dans `bench.chunking_strategies`
# --------------------------------------------------------------------------- #


def notes(reglages: Reglages) -> str:
    return (
        "Découpage par phrases (corpus.phrases, module 05) ; règle validée le"
        " 2026-10-09 ; recouvrement = la dernière phrase si elle tient ;"
        f" réglages config/decoupage_phrases.toml sha256 {reglages.empreinte}"
    )


def enregistrer_strategie(cx, reglages: Reglages) -> int:
    """La ligne de la stratégie : inscrite si elle manque ; si elle existe, elle
    doit être exactement celle des réglages — une règle changée change de nom."""
    ligne = cx.execute(
        "SELECT chunking_id, chunk_size, chunk_overlap, notes"
        " FROM bench.chunking_strategies WHERE name = %s",
        (reglages.nom,),
    ).fetchone()
    attendu = (reglages.taille, reglages.recouvrement, notes(reglages))
    if ligne is None:
        return cx.execute(
            "INSERT INTO bench.chunking_strategies (name, chunk_size, chunk_overlap,"
            " notes) VALUES (%s, %s, %s, %s) RETURNING chunking_id",
            (reglages.nom, *attendu),
        ).fetchone()[0]
    if tuple(ligne[1:]) != attendu:
        raise ValueError(
            f"stratégie {reglages.nom} déjà inscrite avec d'autres réglages :"
            " une règle changée porte un autre nom"
        )
    return ligne[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Découpage par phrases (corpus v2)")
    parser.add_argument(
        "--enregistrer",
        action="store_true",
        help="inscrire la stratégie dans bench.chunking_strategies (DATABASE_URL)",
    )
    args = parser.parse_args(argv)
    r = charger_reglages()
    sortie = {
        "nom": r.nom,
        "taille": r.taille,
        "recouvrement": r.recouvrement,
        "abreviations": len(r.abreviations),
        "empreinte": r.empreinte,
    }
    if args.enregistrer:
        import psycopg

        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise SystemExit("DATABASE_URL absente")
        with psycopg.connect(dsn) as cx:
            sortie["chunking_id"] = enregistrer_strategie(cx, r)
    print(json.dumps(sortie, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
