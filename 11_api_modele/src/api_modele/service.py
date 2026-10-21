"""Le service d'embedding, par le client du module 09 — repris, pas recopié.

`ClientService` reçoit l'hôte de l'API (`CLIENT_HOTE`, sinon celui du module 09) et
un délai court (le client attend 300 s par défaut). La confrontation de `/info` à
l'encodage en service est `confronter` du module 09, seul endroit de la règle.

La borne de la question (contrat de l'étape 2, B.3) : avec le tokenizer servi (BPE à
repli sur octets, normalisation de l'espace en « ▁ », `<bos>` et `<eos>`), chaque
jeton couvre au moins un octet du texte normalisé, et un caractère en compte au plus
quatre. Une question de N caractères, préfixée, tient donc en au plus
octets(préfixe normalisé) + 4·N + 2 jetons.
"""

from __future__ import annotations

from typing import Any, cast

# Le paquet du module 09 n'est pas marqué typé (`py.typed`) : Pyright en lit les
# sources, sans stubs. Exception limitée à ces imports ; le module 09 ne change pas.
from service_embedding.client import (  # pyright: ignore[reportMissingTypeStubs]
    ClientService,
    ErreurService,
    ServiceNonConforme,
    confronter,  # pyright: ignore[reportUnknownVariableType] — rend un `dict` nu
)
from service_embedding.configuration import (  # pyright: ignore[reportMissingTypeStubs]
    Instance,
)
from service_embedding.configuration import (  # pyright: ignore[reportMissingTypeStubs]
    charger as charger_instance,
)

from api_modele.base import EncodageEnService
from api_modele.configuration import Reglages

__all__ = [
    "ClientService",
    "ErreurService",
    "Instance",
    "ServiceNonConforme",
    "charger_instance",
    "client",
    "confronter_au_registre",
    "jetons_au_plus",
]

#: Jetons spéciaux ajoutés à toute entrée : `<bos>` et `<eos>`.
JETONS_SPECIAUX = 2
#: Octets UTF-8 d'un caractère, au plus : autant de jetons d'octet au pire.
OCTETS_PAR_CARACTERE = 4


def client(instance: Instance, reglages: Reglages) -> ClientService:
    return ClientService(
        instance, hote=reglages.client_hote, delai=reglages.delai_service_s
    )


def confronter_au_registre(
    client_: ClientService, encodage: EncodageEnService
) -> dict[str, Any]:
    """`/info` du service, confronté à l'encodage en service (module 09).

    Lève `ServiceNonConforme` (écart) ou `ErreurService` (injoignable)."""
    return cast(
        dict[str, Any], confronter(client_, encodage.ligne_du_registre(), "requete")
    )


def jetons_au_plus(prefixe: str, caracteres: int) -> int:
    """Les jetons qu'une question de `caracteres` caractères peut coûter, au pire."""
    octets_prefixe = len(prefixe.replace(" ", "▁").encode("utf-8"))
    return octets_prefixe + OCTETS_PAR_CARACTERE * caracteres + JETONS_SPECIAUX
