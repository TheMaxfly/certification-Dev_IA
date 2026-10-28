"""Ce que les fournisseurs rendent vraiment : le relevé brut de chaque appel.

La route ne rend que ce que son contrat déclare ; le banc a besoin de plus pour
lever les dettes de l'étape (où `usage` range les jetons de raisonnement, quels
réglages un fournisseur accepte, le rapport réel caractères / jetons) et pour tenir
le plafond sur TOUS les appels, y compris ceux dont la sortie est refusée par les
contrôles de la route.

`Espion` enveloppe l'envoi du client asynchrone de httpx (celui de la couche des
fournisseurs, et lui seul dans ce processus) le temps d'un `with`. Il ne garde que
des nombres et des noms : les paramètres envoyés hors des messages, la taille des
messages en caractères, le statut, le modèle rendu, l'arbre numérique de `usage`, la
raison de fin, et pour un refus le code et le message d'erreur du fournisseur
(tronqué). Jamais un message, un passage, une sortie, ni un en-tête.
"""

from __future__ import annotations

import json
import math
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from api_modele.configuration import Profil
from api_modele.fournisseurs import ADAPTATEURS, cout_usd

#: Les paramètres d'un corps d'appel relevés (aucun ne porte de texte).
PARAMETRES = (
    "model",
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "reasoning_effort",
)
MESSAGE_ERREUR_MAX = 300


def nombres(valeur: object) -> object:
    """L'arbre numérique d'un objet JSON : les nombres, et les objets qui en
    contiennent ; tout le reste (texte compris) est écarté."""
    if isinstance(valeur, bool):
        return None
    if isinstance(valeur, int | float):
        return valeur
    if isinstance(valeur, dict):
        sortie: dict[str, object] = {}
        for cle, v in cast(dict[str, object], valeur).items():
            n = nombres(v)
            if n is not None and n != {}:
                sortie[cle] = n
        return sortie
    return None


@dataclass
class Releve:
    """Un appel à un fournisseur, en nombres et en noms."""

    hote: str
    role: str
    parametres: dict[str, object]
    caracteres_envoyes: int
    statut: int = 0
    modele_rendu: str | None = None
    usage: dict[str, object] = field(default_factory=dict[str, object])
    fin: str | None = None
    erreur: dict[str, str] | None = None

    def entier(self, *chemin: str) -> int | None:
        valeur: object = self.usage
        for cle in chemin:
            valeur = objet(valeur).get(cle)
        return (
            valeur if isinstance(valeur, int) and not isinstance(valeur, bool) else None
        )

    def jetons(
        self, profil: Profil, caracteres_par_jeton: float
    ) -> tuple[int, int, int, bool]:
        """(entrée, sortie visible, raisonnement, estimés), comme la couche des
        fournisseurs les compte."""
        entree = self.entier("prompt_tokens")
        sortie = self.entier("completion_tokens")
        if entree is None or sortie is None:
            return math.ceil(self.caracteres_envoyes / caracteres_par_jeton), 0, 0, True
        adaptateur = ADAPTATEURS[profil.fournisseur]
        chemin = adaptateur.chemin_raisonnement
        raisonnement = (self.entier(*chemin) if chemin else None) or 0
        if adaptateur.raisonnement_inclus:
            sortie = max(sortie - raisonnement, 0)
        return entree, sortie, raisonnement, False

    def cout(self, profil: Profil, caracteres_par_jeton: float) -> float:
        """Le coût de l'appel, en dollars ; 0 pour un appel refusé (non facturé)."""
        if not 200 <= self.statut < 300:
            return 0.0
        entree, sortie, raisonnement, _ = self.jetons(profil, caracteres_par_jeton)
        return cout_usd(profil, entree, sortie, raisonnement) or 0.0


def objet(valeur: object) -> dict[str, object]:
    """Un objet JSON, ou un objet vide."""
    return cast(dict[str, object], valeur) if isinstance(valeur, dict) else {}


def _role(corps: dict[str, object]) -> str:
    format_ = objet(objet(corps.get("response_format")).get("json_schema"))
    requis = objet(format_.get("schema")).get("required")
    return "reformulation" if requis == ["question"] else "reponse"


def _caracteres(corps: dict[str, object]) -> int:
    messages = corps.get("messages")
    if not isinstance(messages, list):
        return 0
    contenus = (objet(m).get("content") for m in cast(list[object], messages))
    return sum(len(c) for c in contenus if isinstance(c, str))


def _erreur(reponse: httpx.Response) -> dict[str, str]:
    try:
        donnees = objet(reponse.json())
    except ValueError:
        return {}
    erreur: object = donnees.get("error", donnees)
    if not isinstance(erreur, dict):
        return {"message": str(erreur)[:MESSAGE_ERREUR_MAX]}
    e = objet(cast(object, erreur))
    sortie = {
        k: str(e[k])[:MESSAGE_ERREUR_MAX]
        for k in ("code", "type", "param", "message")
        if e.get(k) is not None
    }
    if not sortie and "detail" in e:
        sortie["message"] = json.dumps(e["detail"], ensure_ascii=False)[
            :MESSAGE_ERREUR_MAX
        ]
    return sortie


class Espion:
    """Les relevés des appels faits pendant le `with` (`Espion.ecouter`)."""

    def __init__(self) -> None:
        self.releves: list[Releve] = []

    @contextmanager
    def ecouter(self) -> Generator[Espion]:
        envoyer = httpx.AsyncClient.send
        releves = self.releves

        async def relever(
            client: httpx.AsyncClient, requete: httpx.Request, **options: Any
        ) -> httpx.Response:
            try:
                corps = objet(json.loads(requete.content or b"{}"))
            except ValueError:
                corps = {}
            releve = Releve(
                hote=requete.url.host,
                role=_role(corps),
                parametres={k: corps[k] for k in PARAMETRES if k in corps},
                caracteres_envoyes=_caracteres(corps),
            )
            releves.append(releve)
            reponse = await envoyer(client, requete, **options)
            await reponse.aread()
            releve.statut = reponse.status_code
            if not 200 <= reponse.status_code < 300:
                releve.erreur = _erreur(reponse)
                return reponse
            try:
                donnees = objet(reponse.json())
            except ValueError:
                return reponse
            modele = donnees.get("model")
            releve.modele_rendu = modele if isinstance(modele, str) else None
            releve.usage = objet(nombres(donnees.get("usage")))
            choix = donnees.get("choices")
            if isinstance(choix, list) and choix:
                premier = cast(list[object], choix)[0]
                fin = objet(premier).get("finish_reason")
                releve.fin = fin if isinstance(fin, str) else None
            return reponse

        setattr(httpx.AsyncClient, "send", relever)  # noqa: B010
        try:
            yield self
        finally:
            setattr(httpx.AsyncClient, "send", envoyer)  # noqa: B010
