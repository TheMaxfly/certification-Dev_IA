"""Les mots de passe : Argon2id, aux paramètres par défaut d'`argon2-cffi`.

`hacher`, `verifier`, `doit_rehacher`, et une vérification FACTICE pour un pseudo qui
n'existe pas : le temps d'une connexion refusée ne dit pas si le compte existe. Aucun
hachage écrit ici : `argon2.PasswordHasher` seul. Les paramètres effectifs (temps,
mémoire, parallélisme) sont relevés au README ; les tests passent un moteur aux
paramètres réduits, jamais la production (`Hacheur()` sans argument).

Le hachage coûte (64 Mio, quelques dizaines de millisecondes) : l'application l'appelle
hors de la boucle d'événements (`run_in_threadpool`), et **quatre calculs au plus**
tournent à la fois (`CALCULS_SIMULTANES`) : la mémoire reste bornée (256 Mio) quel que
soit le nombre de connexions envoyées ensemble.
"""

from __future__ import annotations

import secrets
import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

#: Les calculs Argon2id simultanés, au plus (chacun prend la mémoire de ses paramètres).
CALCULS_SIMULTANES = 4


class Hacheur:
    def __init__(self, moteur: PasswordHasher | None = None) -> None:
        self.moteur = moteur if moteur is not None else PasswordHasher()
        self._reference: str | None = None
        self._places = threading.BoundedSemaphore(CALCULS_SIMULTANES)

    def preparer(self) -> str:
        """L'empreinte de référence de la vérification factice, d'un secret jeté :
        calculée au démarrage de l'application (son cycle de vie), une fois."""
        if self._reference is None:
            self._reference = self.moteur.hash(secrets.token_urlsafe(24))
        return self._reference

    def parametres(self) -> dict[str, object]:
        """Les paramètres effectifs, pour le README et les tests ; aucun secret."""
        p = self.moteur
        return {
            "type": p.type.name,
            "temps": p.time_cost,
            "memoire_kio": p.memory_cost,
            "parallelisme": p.parallelism,
            "longueur_empreinte": p.hash_len,
            "longueur_sel": p.salt_len,
        }

    def hacher(self, mot_de_passe: str) -> str:
        with self._places:
            return self.moteur.hash(mot_de_passe)

    def verifier(self, empreinte: str, mot_de_passe: str) -> bool:
        with self._places:
            try:
                return self.moteur.verify(empreinte, mot_de_passe)
            except (VerificationError, InvalidHashError):
                return False

    def doit_rehacher(self, empreinte: str) -> bool:
        return self.moteur.check_needs_rehash(empreinte)

    def verifier_factice(self, mot_de_passe: str) -> bool:
        """Le même travail qu'une vraie vérification, et toujours faux."""
        self.verifier(self.preparer(), mot_de_passe)
        return False
