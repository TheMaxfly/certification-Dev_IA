"""Client HTTP d'une instance du service d'embedding.

C'est ici, et nulle part ailleurs, que les préfixes s'appliquent : le service
n'en ajoute aucun (pas de `DEFAULT_PROMPT_NAME`). Un fragment passe par
`encoder(..., role="document")`, une question par `role="requete"` ; le préfixe
vient de la configuration de l'instance.

Chaque requête d'encodage demande `normalize: true` et `truncate: false` : les
vecteurs sont unitaires, et un texte trop long fait échouer la requête au lieu
d'être tronqué en silence.

Bibliothèque standard seulement (urllib) : le service écoute en local, une
connexion par requête ne coûte rien face au calcul.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from collections.abc import Sequence

from service_embedding.configuration import Instance, Role


class ErreurService(Exception):
    """Le service a refusé la requête, ou rendu une réponse inattendue."""


class ClientService:
    def __init__(self, instance: Instance, hote: str = "127.0.0.1", delai=300.0):
        self.instance = instance
        self.hote = hote
        self.delai = delai

    @property
    def url(self) -> str:
        return f"http://{self.hote}:{self.instance.port}"

    @property
    def url_metriques(self) -> str:
        return f"http://{self.hote}:{self.instance.port_metriques}/metrics"

    def _appeler(self, url: str, corps: object | None = None) -> bytes:
        donnees = None if corps is None else json.dumps(corps).encode("utf-8")
        requete = urllib.request.Request(  # nosec B310 — URL locale, construite ici
            url,
            data=donnees,
            headers={"Content-Type": "application/json"} if donnees else {},
            method="GET" if donnees is None else "POST",
        )
        try:
            with urllib.request.urlopen(requete, timeout=self.delai) as reponse:  # nosec B310
                return reponse.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise ErreurService(f"{url} : HTTP {exc.code} — {detail}") from exc
        except urllib.error.URLError as exc:
            raise ErreurService(f"{url} : injoignable — {exc.reason}") from exc

    def sante(self) -> bool:
        try:
            self._appeler(f"{self.url}/health")
        except ErreurService:
            return False
        return True

    def info(self) -> dict:
        return json.loads(self._appeler(f"{self.url}/info"))

    def metriques(self) -> str:
        return self._appeler(self.url_metriques).decode("utf-8")

    def prefixer(self, textes: Sequence[str], role: Role) -> list[str]:
        prefixe = self.instance.prefixe(role)
        return [prefixe + t for t in textes]

    def encoder(self, textes: Sequence[str], role: Role) -> list[list[float]]:
        """Vecteurs normalisés, dans l'ordre des textes. Vérifie leur dimension."""
        if not textes:
            return []
        if len(textes) > self.instance.plafond_lot:
            raise ValueError(
                f"{len(textes)} textes : au-delà du plafond du service "
                f"({self.instance.plafond_lot})"
            )
        corps = {
            "inputs": self.prefixer(textes, role),
            "normalize": True,
            "truncate": False,
        }
        vecteurs = json.loads(self._appeler(f"{self.url}/embed", corps))
        if not isinstance(vecteurs, list) or len(vecteurs) != len(textes):
            raise ErreurService(
                f"{len(textes)} textes envoyés, réponse de forme inattendue"
            )
        for v in vecteurs:
            if len(v) != self.instance.dimension:
                raise ErreurService(
                    f"dimension {len(v)}, attendu {self.instance.dimension}"
                )
        return vecteurs

    def compter_jetons(self, textes: Sequence[str], role: Role) -> list[int]:
        """Jetons par texte préfixé, selon le tokenizer DU SERVICE (spéciaux inclus)."""
        if not textes:
            return []
        corps = {"inputs": self.prefixer(textes, role), "add_special_tokens": True}
        jetons = json.loads(self._appeler(f"{self.url}/tokenize", corps))
        if len(jetons) != len(textes):
            raise ErreurService("réponse de /tokenize de forme inattendue")
        return [len(j) for j in jetons]


def norme(vecteur: Sequence[float]) -> float:
    return math.sqrt(math.fsum(x * x for x in vecteur))


def cosinus(a: Sequence[float], b: Sequence[float]) -> float:
    return math.fsum(x * y for x, y in zip(a, b, strict=True)) / (norme(a) * norme(b))
