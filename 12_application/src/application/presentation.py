"""Ce que les gabarits reçoivent : la réponse de l'API et sa description, en forme.

Tout est piloté par `/v1/info` : profils disponibles, bornes, modes. Aucun texte ici :
ils sont dans `[textes]` de `config/reglages.toml`. Aucun HTML non plus : les gabarits
échappent tout.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from application import __version__
from application.client_api import EtatApi, Info, ProfilDecrit
from application.config import Fournisseur, Position, Reglages


def dire(textes: Mapping[str, str], cle: str, **valeurs: object) -> str:
    return textes[cle].format(**valeurs)


def fournisseur_de(reglages: Reglages, nom: str) -> Fournisseur:
    """Le nom et l'hébergement d'un fournisseur, s'ils sont connus de la configuration :
    `/v1/info` ne dit pas l'hébergement."""
    connu = reglages.fournisseurs.get(nom)
    if connu is not None:
        return connu
    return Fournisseur(nom=nom, hebergement=reglages.textes["valeur_inconnue"])


def profils_proposes(info: Info, reglages: Reglages) -> list[ProfilDecrit]:
    """Les profils disponibles que la configuration n'exclut pas, dans l'ordre reçu."""
    return [
        profil
        for profil in info.modele_langage.profils
        if profil.disponible and profil.nom not in reglages.profils_exclus
    ]


def profil_par_defaut(info: Info, reglages: Reglages) -> str | None:
    """Le profil actif s'il est proposé, sinon le premier proposé, sinon aucun."""
    proposes = [p.nom for p in profils_proposes(info, reglages)]
    actif = info.modele_langage.profil_actif
    if actif in proposes:
        return actif
    return proposes[0] if proposes else None


@dataclass(frozen=True, slots=True)
class OptionProfil:
    nom: str
    etiquette: str
    choisi: bool


def options_profils(
    info: Info, reglages: Reglages, choisi: str | None
) -> list[OptionProfil]:
    """Chaque option dit son fournisseur et son hébergement : ils se lisent au moment du
    choix."""
    proposes = profils_proposes(info, reglages)
    noms = [p.nom for p in proposes]
    retenu = choisi if choisi in noms else profil_par_defaut(info, reglages)
    t = reglages.textes
    options: list[OptionProfil] = []
    for profil in proposes:
        fournisseur = fournisseur_de(reglages, profil.fournisseur)
        etiquette = dire(
            t,
            "profil_option",
            nom=profil.nom,
            fournisseur=fournisseur.nom,
            hebergement=fournisseur.hebergement,
        )
        if profil.nom == info.modele_langage.profil_actif:
            etiquette += f" {t['suffixe_actif']}"
        if profil.statut == "apercu":
            etiquette += f" {t['suffixe_apercu']}"
        options.append(OptionProfil(profil.nom, etiquette, profil.nom == retenu))
    return options


def positions_proposees(info: Info | None, reglages: Reglages) -> tuple[Position, ...]:
    """Les positions de température dans les bornes de l'API."""
    if info is None:
        return reglages.positions
    borne = info.modele_langage.reglages_reponse.temperature
    return tuple(p for p in reglages.positions if borne.min <= p.valeur <= borne.max)


def modes_proposes(info: Info | None) -> list[str]:
    """Les modes publiés par l'API ; plusieurs : le sélecteur apparaît."""
    return [] if info is None else info.modele_langage.reglages_reponse.mode.valeurs


def etat_du_service(etat: EtatApi, info: Info | None, reglages: Reglages) -> str:
    t = reglages.textes
    if etat == "cle":
        return t["etat_cle"]
    if etat == "ko" or info is None:
        return t["etat_ko"]
    actif = info.modele_langage.profil_actif
    modele = next(
        (p.modele for p in info.modele_langage.profils if p.nom == actif),
        t["valeur_inconnue"],
    )
    return dire(t, "etat_ok", profil=actif, modele=modele)


def pied_de_page(etat: EtatApi, info: Info | None, reglages: Reglages) -> str:
    t = reglages.textes
    if etat == "ok" and info is not None:
        return dire(
            t, "pied_versions", version=__version__, version_api=info.version_api
        )
    return dire(t, "pied_versions_ko", version=__version__)


@dataclass(frozen=True, slots=True)
class LigneProfil:
    nom: str
    fournisseur: str
    hebergement: str
    actif: bool


def lignes_a_propos(info: Info | None, reglages: Reglages) -> list[LigneProfil]:
    """Tous les profils du sélecteur, avec leur hébergement : la page ne dit pas
    seulement celui du profil actif, qui mentirait dès qu'on en choisit un autre."""
    if info is None:
        return []
    lignes: list[LigneProfil] = []
    for profil in profils_proposes(info, reglages):
        fournisseur = fournisseur_de(reglages, profil.fournisseur)
        lignes.append(
            LigneProfil(
                nom=profil.nom,
                fournisseur=fournisseur.nom,
                hebergement=fournisseur.hebergement,
                actif=profil.nom == info.modele_langage.profil_actif,
            )
        )
    return lignes
