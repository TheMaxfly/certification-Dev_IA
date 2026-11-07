"""Le complément du mode enrichi : ce que le modèle ajoute de ses connaissances
générales, étiqueté « non vérifié », et les contrôles qui l'empêchent de contredire
le catalogue (E3, étape 6, spec 06, décisions E2 et question 33).

Contrôles, dans l'ordre, après ceux de la partie stricte — chacun : 503
`sortie_modele_invalide`, un motif préfixé `complement_`, son compteur ; pas de
second essai ; un complément vide est valide (rien n'est rendu) :

- `complement_pseudonyme` : un nom de membre, par la même méthode que la réponse
  (homonymes : même règle), dans le texte ou les titres ;
- `complement_disponibilite` : une forme affirmative de disponibilité en France (la
  liste de la réponse, `formes_affirmations.toml`) sur un titre du catalogue ;
- `complement_metadonnee` : un nombre de tomes, « terminé / en cours », un public, un
  auteur, affirmés sur un titre du catalogue (formes versionnées, conservatrices :
  mieux vaut un 503 de trop qu'une affirmation fausse ; une négation, une question ne
  sont pas des affirmations — une comparaison, si : « les lecteurs de X aiment ses 27
  tomes » est refusé) ;
- `complement_marque` : un `[S:…]` ou `[F:…]` dans le texte ou les titres.

**Un titre du catalogue** : le titre d'une série rendue par la recherche (elle a un
`serie_id`), ou un titre de `complement_titres` que le catalogue porte
(`manga.ms_series_enriched`), comparés sans casse, sans accents ni signes (lettres et
chiffres seuls). Un titre du catalogue que le texte nomme sans le mettre dans
`complement_titres`, et que la recherche n'a pas rendu, n'est pas reconnu. Un titre
hors catalogue n'est pas contrôlé sur ces deux points : le complément peut s'y
tromper, et l'étiquette le dit.

**À quel titre une forme se rapporte** (la règle de la réponse, sur des titres au
lieu de marques) : le titre nommé le plus près avant elle dans sa phrase ; sinon le
premier nommé après elle dans la phrase ; sinon le dernier nommé dans les phrases
d'avant ; sans titre nommé, le seul titre de `complement_titres` s'il n'y en a qu'un.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from api_modele import affirmations
from api_modele.affirmations import FICHIER, affirmee, aplatir, bornes_de_phrase
from api_modele.base import titre_comparable as cle

#: Le texte fixe de l'étiquette, dans la réponse de l'API (`complement_etiquette`).
ETIQUETTE = "non vérifié — connaissances du modèle, sans source"
MARQUE = re.compile(r"\[\s*[SsFf]\s*:")

type Motif = Literal[
    "complement_pseudonyme",
    "complement_disponibilite",
    "complement_metadonnee",
    "complement_marque",
]


def charger_negations(fichier: Path = FICHIER) -> re.Pattern[str]:
    """Les réserves « négation » seules (`[reserves].negations`)."""
    donnees = tomllib.loads(fichier.read_text(encoding="utf-8"))
    return re.compile(donnees["reserves"]["negations"])


def charger_metadonnees(fichier: Path = FICHIER) -> re.Pattern[str]:
    """Les formes des métadonnées (tomes, statut, auteur) et celles d'un public
    affirmé (les formes de la réponse, tous publics) en un motif ; une forme fautive
    empêche le démarrage."""
    donnees = tomllib.loads(fichier.read_text(encoding="utf-8"))
    demographie = donnees["demographie"]
    publics = "|".join(f"(?:{m})" for m in demographie["publics"].values())
    formes = [
        *donnees["complement_metadonnee"]["formes"],
        *(f.replace("{public}", f"(?:{publics})") for f in demographie["formes"]),
    ]
    return re.compile("|".join(formes))


METADONNEES = charger_metadonnees()
NEGATIONS = charger_negations()


def affirmee_sans_comparaison(texte: str, forme: re.Match[str]) -> bool:
    """Ni une question, ni une négation dans la forme ou devant elle, dans sa
    proposition : la règle d'`affirmations.affirmee`, sans l'excuse de la
    comparaison (la métadonnée est conservatrice, spec 06, C.6)."""
    debut, fin = bornes_de_phrase(texte, forme.start(), forme.end())
    if texte[debut:fin].rstrip().endswith("?"):
        return False
    avant = affirmations.MARQUE.sub(" ", texte[debut : forme.start()])
    proposition = affirmations.BORNE_DE_PROPOSITION.split(avant)[-1]
    return NEGATIONS.search(f"{proposition} {forme.group(0)}") is None


@dataclass(frozen=True, slots=True)
class Mention:
    """Un titre nommé dans le texte aplati, et s'il est du catalogue."""

    debut: int
    titre: str
    catalogue: bool


def mentions(texte: str, titres: dict[str, bool]) -> list[Mention]:
    """Où chaque titre est nommé dans le texte aplati, les plus longs d'abord : un
    titre contenu dans un autre ne compte pas deux fois. Deux écritures d'un même
    titre (aplaties égales) n'en font qu'une, du catalogue si l'une l'est."""
    formes: dict[str, bool] = {}
    for titre, catalogue in titres.items():
        forme = aplatir(titre).strip()
        if forme:
            formes[forme] = formes.get(forme, False) or catalogue
    trouvees: list[Mention] = []
    prises: list[tuple[int, int]] = []
    for forme in sorted(formes, key=len, reverse=True):
        for m in re.finditer(rf"(?<!\w){re.escape(forme)}(?!\w)", texte):
            if any(a < m.end() and m.start() < b for a, b in prises):
                continue
            prises.append((m.start(), m.end()))
            trouvees.append(Mention(m.start(), forme, formes[forme]))
    return sorted(trouvees, key=lambda x: x.debut)


def titre_vise(
    texte: str, forme: re.Match[str], noms: Sequence[Mention], seul: Mention | None
) -> Mention | None:
    """Le titre auquel une forme se rapporte (règle du module)."""
    debut, fin = bornes_de_phrase(texte, forme.start(), forme.end())
    avant = [m for m in noms if debut <= m.debut < forme.start()]
    if avant:
        return avant[-1]
    apres = [m for m in noms if forme.start() <= m.debut < fin]
    if apres:
        return apres[0]
    precedentes = [m for m in noms if m.debut < debut]
    if precedentes:
        return precedentes[-1]
    return seul


def vise_le_catalogue(
    texte: str,
    motif: re.Pattern[str],
    noms: Sequence[Mention],
    seul: Mention | None,
    affirmer: Callable[[str, re.Match[str]], bool],
) -> bool:
    """Une forme affirmée dont le titre visé est du catalogue."""
    for forme in motif.finditer(texte):
        if not affirmer(texte, forme):
            continue
        vise = titre_vise(texte, forme, noms, seul)
        if vise is not None and vise.catalogue:
            return True
    return False


def fautif(
    complement: str,
    titres: Sequence[str],
    titres_series: Sequence[str],
    du_catalogue: set[str],
) -> Motif | None:
    """Le motif du premier contrôle de sens que le complément ne tient pas, dans
    l'ordre : disponibilité, métadonnée, marque (le pseudonyme se contrôle avant, par
    la liste des membres). `titres_series` : ceux des séries du catalogue rendues par
    la recherche ; `du_catalogue` : les clés (`cle`) des titres de `titres` que le
    catalogue porte."""
    des_series = {cle(t) for t in titres_series}
    connus = dict.fromkeys(titres_series, True) | {
        t: cle(t) in du_catalogue or cle(t) in des_series for t in titres
    }
    texte = aplatir(complement)
    noms = mentions(texte, connus)
    seul = Mention(-1, titres[0], connus[titres[0]]) if len(titres) == 1 else None
    formes = affirmations.FORMES.disponibilite
    if vise_le_catalogue(texte, formes, noms, seul, affirmee):
        return "complement_disponibilite"
    if vise_le_catalogue(texte, METADONNEES, noms, seul, affirmee_sans_comparaison):
        return "complement_metadonnee"
    if MARQUE.search(complement) or any(MARQUE.search(t) for t in titres):
        return "complement_marque"
    return None
