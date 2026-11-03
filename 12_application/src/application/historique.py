"""L'historique de la conversation : il vit dans la page, dans un champ caché.

Le serveur n'en garde rien ; chaque requête le rapporte, et il est revalidé ici, selon
les règles de l'API : un JSON, une liste de tours, le premier du libraire, le dernier
de l'assistant, alternés, dans les bornes. Modifié par le navigateur, il n'affecte que
son auteur. Son texte n'est jamais journalisé : son nombre de tours et sa taille
seulement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, cast

from application.config import Limites

type Role = Literal["libraire", "assistant"]


class HistoriqueInvalide(Exception):
    """L'historique porté par la page ne tient pas les règles de l'API."""


@dataclass(frozen=True, slots=True)
class Tour:
    role: Role
    contenu: str


def taille(tours: list[Tour]) -> int:
    return sum(len(t.contenu) for t in tours)


def _tour_de(element: object) -> Tour:
    if not isinstance(element, dict):
        raise HistoriqueInvalide("tour")
    champs = cast(dict[str, object], element)
    role, contenu = champs.get("role"), champs.get("contenu")
    if set(champs) != {"role", "contenu"}:
        raise HistoriqueInvalide("champs")
    if role != "libraire" and role != "assistant":
        raise HistoriqueInvalide("role")
    if not isinstance(contenu, str) or not contenu:
        raise HistoriqueInvalide("contenu")
    return Tour(cast(Role, role), contenu)


def lire(brut: str, limites: Limites) -> list[Tour]:
    """Les tours portés par la page ; vide : une conversation neuve."""
    if not brut.strip():
        return []
    try:
        donnees: object = json.loads(brut)
    except ValueError:
        raise HistoriqueInvalide("json") from None
    if not isinstance(donnees, list):
        raise HistoriqueInvalide("liste")
    tours = [_tour_de(e) for e in cast(list[object], donnees)]
    if len(tours) > limites.tours_max:
        raise HistoriqueInvalide("trop_de_tours")
    if taille(tours) > limites.caracteres_max:
        raise HistoriqueInvalide("trop_long")
    if any(len(t.contenu) > limites.tour_caracteres_max for t in tours):
        raise HistoriqueInvalide("tour_trop_long")
    if not tours:
        return []
    if tours[0].role != "libraire":
        raise HistoriqueInvalide("premier_tour_non_libraire")
    if any(a.role == b.role for a, b in zip(tours, tours[1:], strict=False)):
        raise HistoriqueInvalide("roles_non_alternes")
    if tours[-1].role != "assistant":
        raise HistoriqueInvalide("dernier_tour_non_assistant")
    return tours


def en_liste(tours: list[Tour]) -> list[dict[str, str]]:
    """Les tours tels que le contrat les décrit."""
    return [{"role": t.role, "contenu": t.contenu} for t in tours]


def ecrire(tours: list[Tour]) -> str:
    """Le champ caché : le texte rendu par l'API, jamais le HTML."""
    if not tours:
        return ""
    return json.dumps(en_liste(tours), ensure_ascii=False, separators=(",", ":"))


def depasse(tours: list[Tour], limites: Limites) -> bool:
    """Le nouvel historique ne tiendrait plus les bornes de l'API : la conversation est
    close, jamais coupée en silence."""
    return taille(tours) > limites.caracteres_max or any(
        len(t.contenu) > limites.tour_caracteres_max for t in tours
    )
