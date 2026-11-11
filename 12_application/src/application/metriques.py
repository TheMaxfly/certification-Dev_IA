"""Les métriques : un registre propre au module, `/metrics` sur le bouclage seulement.

Matière de l'étape de surveillance : requêtes par route et statut, appels à l'API et
leurs erreurs par code, latence, et l'état de l'API vu d'ici : deux lectures, deux
jauges, écrites à une page ou à un `/sante`. Aucune ne se relit seule : la panne de
l'API se lit sur la cible de Prometheus, pas ici.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from application.config import Reseau

SEAUX_LATENCE = (0.05, 0.25, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 45.0, 75.0)
#: Les états de l'API vus d'ici : `VueApi.etat`.
ETATS_API = ("ok", "ko", "cle")
#: Les états du catalogue (l'API data du 02) vus d'ici, posés à chaque appel.
ETATS_CATALOGUE = ("ok", "indisponible", "non_configure", "cle")
#: L'issue d'une question au conseiller (E4, lot 2b) : une liste fermée.
ISSUES_QUESTION = (
    "reponse",
    "refus",
    "quota",
    "debit",
    "plafond_cout",
    "api_erreur",
    "api_indisponible",
)
#: Les modes d'une question : ceux de la table des coûts (027) ; tout autre, `autre`.
MODES_QUESTION = ("strict", "enrichi")
#: Les motifs d'un refus par une borne : quota de session, quota du compte, débit de
#: la clé publique, budget du jour, inscriptions de la session.
MOTIFS_REFUS = ("session", "client", "debit", "cout", "inscription")
#: L'état du plafond de coût à sa dernière lecture.
ETATS_PLAFOND = ("ok", "atteint", "inconnu")
#: Les gestes sur les listes du client (E4, lot 4) qui écrivent : un ajout, un
#: changement d'état, un retrait.
ACTIONS_LISTES = ("ajout", "changement", "retrait")
#: L'issue d'un test des clés demandé par l'administration (E4, lot 5).
ISSUES_VERIFICATION = ("teste", "route_absente", "trop_tot", "indisponible")


class Metriques:
    def __init__(self) -> None:
        self.registre = CollectorRegistry()
        self.requetes = Counter(
            "application_requetes",
            "Requêtes servies, par gabarit de route et statut.",
            ["route", "statut"],
            registry=self.registre,
        )
        self.api_appels = Counter(
            "application_api_appels",
            "Appels à l'API du modèle, par route de l'API et statut.",
            ["route_api", "statut"],
            registry=self.registre,
        )
        self.api_erreurs = Counter(
            "application_api_erreurs",
            "Erreurs rendues par l'API du modèle ou en l'appelant, par code.",
            ["code"],
            registry=self.registre,
        )
        self.latence = Histogram(
            "application_latence_secondes",
            "Durée des requêtes servies, par gabarit de route.",
            ["route"],
            buckets=SEAUX_LATENCE,
            registry=self.registre,
        )
        self.api_info_etat = Gauge(
            "application_api_info_etat",
            "L'état de l'API à la dernière lecture de /v1/info (1 sur l'état constaté)"
            " : ok, ko (elle ne répond pas), cle (clé refusée).",
            ["etat"],
            registry=self.registre,
        )
        self.base_etat = Gauge(
            "application_base_etat",
            "L'état de la base des comptes à sa dernière lecture (1 sur l'état"
            " constaté)"
            " : ok, indisponible (elle ne répond pas), non_configuree (aucun DSN).",
            ["etat"],
            registry=self.registre,
        )
        self.catalogue_appels = Counter(
            "application_catalogue_appels",
            "Appels à l'API data du catalogue (module 02), par route et issue : ok,"
            " introuvable, indisponible, cle (clé refusée), corps (hors contrat).",
            ["route_catalogue", "statut"],
            registry=self.registre,
        )
        self.catalogue_etat = Gauge(
            "application_catalogue_etat",
            "L'état du catalogue au dernier appel (1 sur l'état constaté) : ok,"
            " indisponible, non_configure (aucune clé), cle (clé refusée).",
            ["etat"],
            registry=self.registre,
        )
        self.critiques = Counter(
            "application_critiques",
            "Critiques de libraires écrites par l'application (E4, lot 3), par action :"
            " publiee, modifiee, retiree (par son auteur ou par la modération).",
            ["action"],
            registry=self.registre,
        )
        self.questions = Counter(
            "application_questions",
            "Questions au conseiller (E4, lot 2b), par mode et issue : reponse, refus,"
            " quota, debit, plafond_cout, api_erreur, api_indisponible.",
            ["mode", "issue"],
            registry=self.registre,
        )
        self.quota_refus = Counter(
            "application_quota_refus",
            "Refus par une borne du conseiller, par motif : session (quota d'un"
            " visiteur), client (quota d'un compte client), debit (clé publique), cout"
            " (budget du jour), inscription.",
            ["motif"],
            registry=self.registre,
        )
        self.cout_jour = Gauge(
            "application_cout_jour_usd",
            "Coût estimé du jour (UTC), en dollars, à sa dernière lecture dans"
            " application.couts_jour (avant chaque question).",
            registry=self.registre,
        )
        self.plafond_cout = Gauge(
            "application_plafond_cout_etat",
            "Le plafond de coût du jour à sa dernière lecture (1 sur l'état constaté) :"
            " ok, atteint, inconnu (base indisponible : non vérifiable).",
            ["etat"],
            registry=self.registre,
        )
        self.listes = Counter(
            "application_listes",
            "Gestes écrits sur les listes du client (E4, lot 4), par action : ajout,"
            " changement (d'état), retrait. Un geste sans effet n'est pas compté.",
            ["action"],
            registry=self.registre,
        )
        self.comptes_supprimes = Counter(
            "application_comptes_supprimes",
            "Comptes clients supprimés par leur titulaire (E4, lot 4, droit à"
            " l'effacement).",
            registry=self.registre,
        )
        for action in ACTIONS_LISTES:
            self.listes.labels(action=action)
        for motif in MOTIFS_REFUS:
            self.quota_refus.labels(motif=motif)
        for mode in MODES_QUESTION:
            for issue in ISSUES_QUESTION:
                self.questions.labels(mode=mode, issue=issue)
        self.profil_actif_info = Gauge(
            "application_profil_actif_info",
            "Le profil IA actif de l'application (E4, lot 5) : 1 sur le profil qui part"
            " avec une question sans choix, 0 sur les autres profils de /v1/info ;"
            " rafraîchie à chaque lecture du réglage.",
            ["profil"],
            registry=self.registre,
        )
        self.verifications_cles = Counter(
            "application_verifications_cles",
            "Tests des clés demandés par l'administration (E4, lot 5), par issue :"
            " teste, route_absente (API antérieure à 1.5.0), trop_tot (la borne d'un"
            " test par minute), indisponible.",
            ["issue"],
            registry=self.registre,
        )
        for issue in ISSUES_VERIFICATION:
            self.verifications_cles.labels(issue=issue)
        self.apparence_info = Gauge(
            "application_apparence_info",
            "L'apparence de l'application (E4, lot 7) : 1 sur la couleur réglée de"
            " chaque espace (par son nom d'origine), 0 sur les cinq autres ;"
            " rafraîchie à chaque lecture du réglage.",
            ["espace", "couleur"],
            registry=self.registre,
        )
        self.api_sante_ok = Gauge(
            "application_api_sante_ok",
            "1 si /v1/sante a répondu 200 à sa dernière lecture (démarrage, /sante).",
            registry=self.registre,
        )

    def etat_catalogue(self, etat: str) -> None:
        """Pose l'état du catalogue : 1 sur l'état constaté, 0 sur les autres."""
        for possible in ETATS_CATALOGUE:
            self.catalogue_etat.labels(etat=possible).set(1 if possible == etat else 0)

    def question(self, mode: str, issue: str) -> None:
        """Une question et son issue ; un mode hors de la liste compte sous `autre`."""
        etiquette = mode if mode in MODES_QUESTION else "autre"
        self.questions.labels(mode=etiquette, issue=issue).inc()

    def etat_plafond(self, etat: str) -> None:
        for possible in ETATS_PLAFOND:
            self.plafond_cout.labels(etat=possible).set(1 if possible == etat else 0)

    def profil_actif(self, actif: str | None, profils: Iterable[str]) -> None:
        """1 sur le profil actif, 0 sur les autres profils connus de `/v1/info`."""
        for nom in profils:
            self.profil_actif_info.labels(profil=nom).set(1 if nom == actif else 0)

    def apparence(self, couleurs: Mapping[str, str], palette: Iterable[str]) -> None:
        """1 sur la couleur réglée de chaque espace, 0 sur les autres de la palette."""
        connues = tuple(palette)
        for espace, reglee in couleurs.items():
            for couleur in connues:
                self.apparence_info.labels(espace=espace, couleur=couleur).set(
                    1 if couleur == reglee else 0
                )

    def exposer(self) -> tuple[bytes, str]:
        return generate_latest(self.registre), CONTENT_TYPE_LATEST


def autorisee(adresse: str | None, reseaux: Iterable[Reseau]) -> bool:
    """L'adresse du client est-elle dans l'un des réseaux admis à lire `/metrics` ?"""
    if adresse is None:
        return False
    try:
        ip = ipaddress.ip_address(adresse)
    except ValueError:
        return False
    return any(ip in reseau for reseau in reseaux)
