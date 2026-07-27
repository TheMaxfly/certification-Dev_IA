"""Exécuteur — affiche la commande, demande confirmation, stream, verdict.

Le jury doit voir la VRAIE commande avant qu'elle parte, la sortie en direct
pendant qu'elle tourne, et un verdict explicite (code retour + durée) à la fin.
Aucune sortie n'est masquée ou reformulée : la console est une télécommande,
pas un filtre.

Sécurité : une action qui écrit en base exige une confirmation TAPÉE EN TOUTES
LETTRES. Un « oui » distrait ne suffit pas.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass

from .actions import Action, Impact

MOT_DE_CONFIRMATION = "ECRIRE"


@dataclass
class Resultat:
    action: Action
    code: int
    duree_s: float
    interrompu: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.interrompu


class RefusEcriture(RuntimeError):
    """Une action d'écriture a été demandée en mode lecture seule."""


def environnement(action: Action) -> dict[str, str]:
    """L'env du sous-processus : celui du shell + les ajouts de l'action.

    DATABASE_URL n'est jamais fabriquée ici — elle vient de l'environnement,
    comme partout dans le dépôt (aucun identifiant en dur).
    """
    env = os.environ.copy()
    env.update(action.env)
    return env


def executer(
    action: Action,
    *,
    lecture_seule: bool,
    ecrire_ligne=print,
) -> Resultat:
    """Lance l'action et streame sa sortie. Ne fabrique aucune logique métier."""
    if action.impact is Impact.DOCUMENTE:
        raise RefusEcriture(
            f"{action.cle} est une action documentée : elle s'affiche, "
            "elle ne s'exécute pas depuis la console."
        )
    if lecture_seule and action.ecrit_en_base:
        raise RefusEcriture(
            f"{action.cle} écrit en base : indisponible en mode lecture seule."
        )

    debut = time.perf_counter()
    interrompu = False
    try:
        processus = subprocess.Popen(  # noqa: S603 — argv figé par le registre
            action.argv,
            cwd=action.cwd,
            env=environnement(action),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert processus.stdout is not None
        for ligne in processus.stdout:
            ecrire_ligne(ligne.rstrip("\n"))
        code = processus.wait()
    except KeyboardInterrupt:
        interrompu = True
        processus.terminate()
        code = 130
    except FileNotFoundError as erreur:
        ecrire_ligne(f"commande introuvable : {erreur}")
        code = 127
    return Resultat(action, code, time.perf_counter() - debut, interrompu)


def texte_confirmation(action: Action) -> str:
    """Ce qu'on demande à l'opérateur avant de lancer — proportionné au risque."""
    if action.ecrit_en_base:
        return (
            f"Cette action ÉCRIT DANS LA BASE ({action.libelle}).\n"
            f"Tapez {MOT_DE_CONFIRMATION} en majuscules pour confirmer"
        )
    if action.impact is Impact.FICHIERS:
        return "Cette action écrit des fichiers reconstructibles (hors base). Lancer ?"
    return "Lancer ?"


def confirmation_valide(action: Action, saisie: str) -> bool:
    """Une écriture base exige le mot exact ; le reste accepte o/oui/y/yes."""
    saisie = saisie.strip()
    if action.ecrit_en_base:
        return saisie == MOT_DE_CONFIRMATION
    return saisie.lower() in {"o", "oui", "y", "yes", ""}
