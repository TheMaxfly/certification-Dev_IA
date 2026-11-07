"""Les métriques Prometheus de l'API (`/metrics`), dans un registre qui lui est propre.

Requêtes par route, méthode et statut ; latence par route ; erreurs par code ; débit
refusé par nom de clé (cardinalité bornée par le trousseau) ; l'encodage en service.
Les routes inconnues comptent sous une seule étiquette : `inconnue`.

Le modèle de langage (E3, étape 3) : appels, jetons, coût estimé et latence par profil
et rôle d'appel, profil actif et profils disponibles — étiquettes bornées par les
profils déclarés. Les réponses servies, par refus et motif.

Pour la collecte (E3, étape 5) : les étiquettes sont fermées, la méthode HTTP comprise ;
et les séries du modèle de langage sont créées à 0 dès le démarrage. Sans cela, le
premier appel d'un profil ne se verrait pas : `increase()` ne compte pas le passage
d'une série absente à sa première valeur.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from api_modele.configuration import Reseau

#: Bornes de l'histogramme de latence, en secondes.
SEUILS_S = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
#: Celles d'un appel au modèle de langage : de 0,25 s à 30 s (le délai par défaut).
SEUILS_LLM_S = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
#: Les méthodes comptées telles quelles ; toute autre compte sous `autre`.
METHODES = frozenset({"GET", "POST", "HEAD", "OPTIONS"})
ROLES = ("reformulation", "reponse")
TYPES_DE_JETONS = ("entree", "sortie", "raisonnement")
#: Les motifs de `/v1/reponse`, ceux de `sortie_invalide` et de `motif_refus`.
MOTIFS_SORTIE = (
    "schema",
    "serie_inconnue",
    "fragment_inconnu",
    "refus_incoherent",
    "pseudonyme",
    "disponibilite",
    "demographie",
    "complement_pseudonyme",
    "complement_disponibilite",
    "complement_metadonnee",
    "complement_marque",
)
#: Les modes de la route de réponse (1.4.0) : une étiquette des appels au modèle.
MODES = ("strict", "enrichi")
MOTIFS_REFUS = ("aucun_passage", "hors_catalogue", "inconnu")


class Metriques:
    def __init__(self) -> None:
        self.registre = CollectorRegistry(auto_describe=True)
        self.requetes = Counter(
            "api_modele_requetes",
            "Requêtes servies, par route, méthode et statut.",
            ["route", "methode", "statut"],
            registry=self.registre,
        )
        self.duree = Histogram(
            "api_modele_duree_requete_secondes",
            "Durée d'une requête, mesurée par l'API, par route.",
            ["route"],
            buckets=SEUILS_S,
            registry=self.registre,
        )
        self.erreurs = Counter(
            "api_modele_erreurs",
            "Réponses d'erreur, par code stable.",
            ["code"],
            registry=self.registre,
        )
        self.debit_refuse = Counter(
            "api_modele_debit_refuse",
            "Requêtes refusées pour débit dépassé, par nom de clé.",
            ["cle"],
            registry=self.registre,
        )
        self.encodage = Gauge(
            "api_modele_encodage_en_service",
            "L'encodage servi (1), par corpus et encodage.",
            ["corpus_id", "encodage_id"],
            registry=self.registre,
        )
        self.llm_appels = Counter(
            "api_modele_llm_appels",
            "Appels au modèle de langage, par profil, rôle, statut (ok ou code) et"
            " mode de la réponse (strict, enrichi).",
            ["profil", "role", "statut", "mode"],
            registry=self.registre,
        )
        self.llm_jetons = Counter(
            "api_modele_llm_jetons",
            "Jetons d'un appel réussi, par profil, rôle et type (entree, sortie,"
            " raisonnement).",
            ["profil", "role", "type"],
            registry=self.registre,
        )
        self.llm_cout = Counter(
            "api_modele_llm_cout_usd",
            "Coût estimé des appels réussis, en dollars, par profil.",
            ["profil"],
            registry=self.registre,
        )
        self.llm_latence = Histogram(
            "api_modele_llm_latence_secondes",
            "Durée d'un appel au modèle de langage, par profil et rôle.",
            ["profil", "role"],
            buckets=SEUILS_LLM_S,
            registry=self.registre,
        )
        self.profil_actif = Gauge(
            "api_modele_profil_actif",
            "Le profil qui rédige (1), les autres profils déclarés (0).",
            ["profil"],
            registry=self.registre,
        )
        self.profil_disponible = Gauge(
            "api_modele_profil_disponible",
            "Profil disponible (1 : sa clé est présente) ou non (0).",
            ["profil"],
            registry=self.registre,
        )
        self.sortie_invalide = Counter(
            "api_modele_sortie_invalide",
            "Sorties du modèle refusées par la route de réponse, par motif.",
            ["motif"],
            registry=self.registre,
        )
        self.reponses = Counter(
            "api_modele_reponses",
            "Réponses rendues par /v1/reponse (statut 200) : refus (vrai, faux), et son"
            " motif (aucun si ce n'est pas un refus).",
            ["refus", "motif"],
            registry=self.registre,
        )

    def precreer(self, profils: Iterable[str]) -> None:
        """Les séries du modèle de langage, à 0 : le premier appel se voit."""
        for profil in profils:
            self.llm_cout.labels(profil=profil)
            for role in ROLES:
                for mode in MODES:
                    self.llm_appels.labels(
                        profil=profil, role=role, statut="ok", mode=mode
                    )
                self.llm_latence.labels(profil=profil, role=role)
                for type_ in TYPES_DE_JETONS:
                    self.llm_jetons.labels(profil=profil, role=role, type=type_)
        for motif in MOTIFS_SORTIE:
            self.sortie_invalide.labels(motif=motif)
        self.reponses.labels(refus="faux", motif="aucun")
        for motif in MOTIFS_REFUS:
            self.reponses.labels(refus="vrai", motif=motif)

    def exposer(self) -> tuple[bytes, str]:
        return generate_latest(self.registre), CONTENT_TYPE_LATEST


def methode_fermee(methode: str) -> str:
    """L'étiquette d'une méthode HTTP : le serveur en admet de quelconques."""
    return methode if methode in METHODES else "autre"


def autorisee(adresse: str | None, reseaux: Iterable[Reseau]) -> bool:
    """L'adresse du client est-elle dans l'un des réseaux admis à lire `/metrics` ?"""
    if adresse is None:
        return False
    try:
        ip = ipaddress.ip_address(adresse)
    except ValueError:
        return False
    return any(ip in reseau for reseau in reseaux)
