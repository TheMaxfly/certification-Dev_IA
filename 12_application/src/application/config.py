"""La configuration : `config/reglages.toml` (versionné) et l'environnement.

Tout est lu et validé au démarrage ; un écart empêche de démarrer, et le message nomme
la variable ou le réglage, jamais une valeur. La clé de l'API n'est jamais dans un
fichier suivi, et son `repr` ne la montre pas (même forme que `Cle` du module 11,
réécrite : ce module ne connaît le 11 que par HTTP).
"""

from __future__ import annotations

import ipaddress
import string
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

RACINE_MODULE = Path(__file__).resolve().parents[2]
FICHIER_REGLAGES = RACINE_MODULE / "config" / "reglages.toml"
VARIABLE_CLE = "APPLICATION_CLE_API"

type Reseau = ipaddress.IPv4Network | ipaddress.IPv6Network
type Table = dict[str, object]

#: Chaque texte des écrans, et les variables qu'il admet : une seule source, le
#: fichier ; un texte absent, en trop, ou une variable inconnue empêchent de démarrer.
TEXTES: Mapping[str, frozenset[str]] = {
    **{
        cle: frozenset()
        for cle in (
            "titre_application nom_librairie evitement mention_courte mention_complete"
            " etat_ko etat_cle lien_a_propos conversation_titre role_libraire"
            " role_assistant question_titre question_etiquette bouton"
            " nouvelle_conversation reglages_titre profil_etiquette suffixe_actif"
            " suffixe_apercu temperature_legende reformuler_etiquette reformuler_aide"
            " mode_etiquette erreur_question_vide erreur_profil erreur_temperature"
            " aucun_profil reponse_titre refus_titre motif_aucun_passage"
            " motif_hors_catalogue motif_inconnu ligne_sans_appel reformulation_sans"
            " reformulation_ignoree ligne_cout_inconnu carte_auteurs carte_tomes"
            " carte_statut carte_public carte_disponibilite valeur_inconnue"
            " statut_termine statut_en_cours dispo_france dispo_japon dispo_inconnue"
            " passage_critique passage_resume passage_synopsis carte_sans_passage"
            " mention_carte mention_dispo details_titre details_estimes"
            " role_appel_reformulation role_appel_reponse non_verifie"
            " non_verifie_titres encart_titre degrade_injoignable degrade_delai"
            " degrade_recherche conversation_invalide conversation_longueur"
            " sortie_schema sortie_serie_inconnue sortie_fragment_inconnu"
            " sortie_refus_incoherent sortie_pseudonyme sortie_disponibilite"
            " sortie_demographie apropos_titre apropos_quoi_titre apropos_quoi"
            " apropos_transmis_titre apropos_transmis apropos_profils_titre"
            " apropos_colonne_profil apropos_colonne_fournisseur"
            " apropos_colonne_hebergement apropos_liste_inconnue"
            " apropos_conserve_titre apropos_conserve apropos_registre_titre"
            " apropos_registre apropos_licences_titre apropos_licences"
            " erreur_404_titre erreur_404_texte erreur_405_titre erreur_405_texte"
            " erreur_413_titre erreur_413_texte erreur_500_titre erreur_500_texte"
            " erreur_retour en_construction"
        ).split()
    },
    "etat_ok": frozenset({"profil", "modele"}),
    "pied_versions": frozenset({"version", "version_api"}),
    "pied_versions_ko": frozenset({"version"}),
    "question_aide": frozenset({"max"}),
    "profil_option": frozenset({"nom", "fournisseur", "hebergement"}),
    "erreur_question_longue": frozenset({"max", "n"}),
    "refus_motif": frozenset({"motif"}),
    "ligne_profil": frozenset({"profil", "fournisseur", "modele"}),
    "ligne_date": frozenset({"date"}),
    "reformulation_faite": frozenset({"question"}),
    "ligne_cout": frozenset({"cout"}),
    "ligne_latence": frozenset({"latence"}),
    "tomes_au_moins": frozenset({"n"}),
    "details_appel": frozenset({"role", "entree", "sortie", "raisonnement", "latence"}),
    "details_refus_debit": frozenset({"role"}),
    "details_ids": frozenset({"corpus_id", "encodage_id", "identifiant"}),
    "degrade_debit": frozenset({"n"}),
    "degrade_saturation": frozenset({"n"}),
    "degrade_modele": frozenset({"profil"}),
    "degrade_longueur": frozenset({"profil"}),
    "degrade_sortie": frozenset({"motif"}),
    "degrade_profil": frozenset({"profil"}),
    "degrade_reglage": frozenset({"profil", "reglage"}),
    "degrade_service": frozenset({"identifiant"}),
    "conversation_terminee": frozenset({"echanges"}),
    "erreur_identifiant": frozenset({"identifiant"}),
}


class ConfigurationInvalide(Exception):
    """La configuration ne tient pas : l'application refuse de démarrer."""

    def __init__(self, ecarts: list[str]) -> None:
        self.ecarts = ecarts
        super().__init__(
            "configuration invalide :\n" + "\n".join(f"  - {e}" for e in ecarts)
        )


@dataclass(frozen=True, slots=True)
class Delais:
    connexion_s: float
    reponse_s: float
    info_s: float
    sante_s: float


@dataclass(frozen=True, slots=True)
class Position:
    """Une position de température : sa valeur, son nom, ce que l'écran en dit."""

    valeur: float
    nom: str
    etiquette: str


@dataclass(frozen=True, slots=True)
class Limites:
    question_caracteres_max: int
    echanges: int
    tours_max: int
    caracteres_max: int
    tour_caracteres_max: int


@dataclass(frozen=True, slots=True)
class Fournisseur:
    nom: str
    hebergement: str


@dataclass(frozen=True, slots=True)
class Reglages:
    cle_api: str = field(repr=False)
    adresse_api: str
    identite: str
    delais: Delais
    attente_debit_defaut_s: int
    attente_fournisseur_defaut_s: int
    temperature_defaut: float
    reformuler_defaut: bool
    profils_exclus: frozenset[str]
    positions: tuple[Position, ...]
    limites: Limites
    corps_octets_max: int
    hote: str
    port: int
    metriques_reseaux: tuple[Reseau, ...]
    fournisseurs: Mapping[str, Fournisseur]
    textes: Mapping[str, str]


class _Lecteur:
    """Lit les valeurs du fichier ; chaque écart est noté, aucun n'arrête la lecture :
    un seul démarrage refusé les dit tous."""

    def __init__(self, ecarts: list[str]) -> None:
        self.ecarts = ecarts

    def _ecart(self, chemin: str, attendu: str) -> None:
        self.ecarts.append(f"reglages.toml : [{chemin}] {attendu}")

    def table(self, source: Table, cle: str, chemin: str = "") -> Table:
        valeur = source.get(cle)
        if isinstance(valeur, dict):
            return cast(Table, valeur)
        self._ecart(f"{chemin}{cle}", "table attendue")
        return {}

    def nombre(self, source: Table, cle: str, chemin: str) -> float:
        valeur = source.get(cle)
        if isinstance(valeur, bool) or not isinstance(valeur, int | float):
            self._ecart(f"{chemin}.{cle}", "nombre attendu")
            return 0.0
        return float(valeur)

    def positif(self, source: Table, cle: str, chemin: str) -> float:
        valeur = self.nombre(source, cle, chemin)
        if cle in source and valeur <= 0:
            self._ecart(f"{chemin}.{cle}", "doit être positif")
        return valeur

    def entier(self, source: Table, cle: str, chemin: str) -> int:
        valeur = source.get(cle)
        if isinstance(valeur, bool) or not isinstance(valeur, int) or valeur <= 0:
            self._ecart(f"{chemin}.{cle}", "entier positif attendu")
            return 1
        return valeur

    def texte(self, source: Table, cle: str, chemin: str) -> str:
        valeur = source.get(cle)
        if not isinstance(valeur, str) or not valeur.strip():
            self._ecart(f"{chemin}.{cle}", "texte attendu")
            return ""
        return valeur

    def booleen(self, source: Table, cle: str, chemin: str) -> bool:
        valeur = source.get(cle)
        if not isinstance(valeur, bool):
            self._ecart(f"{chemin}.{cle}", "booléen attendu")
            return False
        return valeur

    def textes(self, source: Table, cle: str, chemin: str) -> list[str]:
        valeur = source.get(cle)
        if not isinstance(valeur, list):
            self._ecart(f"{chemin}.{cle}", "liste attendue")
            return []
        liste: list[str] = []
        for element in cast(list[object], valeur):
            if isinstance(element, str):
                liste.append(element)
            else:
                self._ecart(f"{chemin}.{cle}", "liste de textes attendue")
        return liste


def variables_de(texte: str) -> set[str]:
    """Les variables `{nom}` d'un texte."""
    return {nom for _, nom, _, _ in string.Formatter().parse(texte) if nom}


def lire_textes(table: Table, ecarts: list[str]) -> dict[str, str]:
    """Les textes des écrans : ceux que le code emploie, avec leurs variables."""
    textes: dict[str, str] = {}
    for cle in sorted(set(TEXTES) - set(table)):
        ecarts.append(f"reglages.toml : [textes] texte absent : {cle}")
    for cle in sorted(set(table) - set(TEXTES)):
        ecarts.append(f"reglages.toml : [textes] texte inconnu : {cle}")
    for cle, valeur in table.items():
        if cle not in TEXTES:
            continue
        if not isinstance(valeur, str) or not valeur.strip():
            ecarts.append(f"reglages.toml : [textes] {cle} : texte attendu")
            continue
        try:
            inconnues = variables_de(valeur) - TEXTES[cle]
        except ValueError:
            ecarts.append(f"reglages.toml : [textes] {cle} : accolades invalides")
            continue
        if inconnues:
            ecarts.append(
                f"reglages.toml : [textes] {cle} : variable inconnue :"
                f" {', '.join(sorted(inconnues))}"
            )
            continue
        textes[cle] = valeur
    return textes


def lire_reseaux(valeurs: list[str], ecarts: list[str], origine: str) -> list[Reseau]:
    reseaux: list[Reseau] = []
    for valeur in valeurs:
        try:
            reseaux.append(ipaddress.ip_network(valeur.strip()))
        except ValueError:
            ecarts.append(f"{origine} : réseau invalide")
    return reseaux


def lire_ecoute(valeur: str, ecarts: list[str]) -> tuple[str, int] | None:
    """`hôte:port` ; None s'il ne tient pas."""
    hote, separateur, port = valeur.strip().rpartition(":")
    if not separateur or not hote or not port.isdigit() or not 0 < int(port) < 65536:
        ecarts.append("APPLICATION_ECOUTE : attendu hôte:port")
        return None
    return hote, int(port)


def charger(environ: Mapping[str, str], fichier: Path = FICHIER_REGLAGES) -> Reglages:
    """Le fichier et l'environnement, validés ; au moindre écart, une exception."""
    ecarts: list[str] = []
    donnees = cast(Table, tomllib.loads(fichier.read_text(encoding="utf-8")))
    lecteur = _Lecteur(ecarts)

    cle = environ.get(VARIABLE_CLE, "").strip()
    if not cle:
        ecarts.append(f"{VARIABLE_CLE} : absente ou vide — aucun accès à l'API")

    api = lecteur.table(donnees, "api")
    delais = lecteur.table(api, "delais", "api.")
    attentes = lecteur.table(api, "attentes", "api.")
    adresse = environ.get("API_MODELE_ADRESSE", "").strip() or lecteur.texte(
        api, "adresse", "api"
    )
    decoupee = urlsplit(adresse)
    if decoupee.scheme not in ("http", "https") or not decoupee.hostname:
        ecarts.append("adresse de l'API : attendu http(s)://hôte[:port]")

    reglages = lecteur.table(donnees, "reglages")
    positions: list[Position] = []
    brutes = reglages.get("positions")
    for brute in cast(list[object], brutes) if isinstance(brutes, list) else []:
        if not isinstance(brute, dict):
            ecarts.append("reglages.toml : [reglages.positions] table attendue")
            continue
        table = cast(Table, brute)
        positions.append(
            Position(
                valeur=lecteur.nombre(table, "valeur", "reglages.positions"),
                nom=lecteur.texte(table, "nom", "reglages.positions"),
                etiquette=lecteur.texte(table, "etiquette", "reglages.positions"),
            )
        )
    temperature = lecteur.nombre(reglages, "temperature_defaut", "reglages")
    if temperature not in [p.valeur for p in positions]:
        ecarts.append("reglages.toml : [reglages] le défaut n'est pas une position")

    conversation = lecteur.table(donnees, "conversation")
    ecoute = lecteur.table(donnees, "ecoute")
    hote, port = (
        lecteur.texte(ecoute, "hote", "ecoute"),
        lecteur.entier(ecoute, "port", "ecoute"),
    )
    surcharge = environ.get("APPLICATION_ECOUTE", "").strip()
    if surcharge:
        lue = lire_ecoute(surcharge, ecarts)
        if lue is not None:
            hote, port = lue

    metriques = lecteur.table(donnees, "metriques")
    reseaux_env = environ.get("APPLICATION_METRIQUES_RESEAUX", "").strip()
    reseaux = (
        lire_reseaux(reseaux_env.split(","), ecarts, "APPLICATION_METRIQUES_RESEAUX")
        if reseaux_env
        else lire_reseaux(
            lecteur.textes(metriques, "reseaux", "metriques"),
            ecarts,
            "reglages.toml : [metriques]",
        )
    )

    fournisseurs: dict[str, Fournisseur] = {}
    for nom, brut in lecteur.table(donnees, "fournisseurs").items():
        table = cast(Table, brut) if isinstance(brut, dict) else {}
        fournisseurs[nom] = Fournisseur(
            nom=lecteur.texte(table, "nom", f"fournisseurs.{nom}"),
            hebergement=lecteur.texte(table, "hebergement", f"fournisseurs.{nom}"),
        )

    resultat = Reglages(
        cle_api=cle,
        adresse_api=adresse,
        identite=lecteur.texte(api, "identite", "api"),
        delais=Delais(
            connexion_s=lecteur.positif(delais, "connexion_s", "api.delais"),
            reponse_s=lecteur.positif(delais, "reponse_s", "api.delais"),
            info_s=lecteur.positif(delais, "info_s", "api.delais"),
            sante_s=lecteur.positif(delais, "sante_s", "api.delais"),
        ),
        attente_debit_defaut_s=lecteur.entier(
            attentes, "debit_defaut_s", "api.attentes"
        ),
        attente_fournisseur_defaut_s=lecteur.entier(
            attentes, "fournisseur_defaut_s", "api.attentes"
        ),
        temperature_defaut=temperature,
        reformuler_defaut=lecteur.booleen(reglages, "reformuler_defaut", "reglages"),
        profils_exclus=frozenset(
            lecteur.textes(reglages, "profils_exclus", "reglages")
        ),
        positions=tuple(positions),
        limites=Limites(
            question_caracteres_max=lecteur.entier(
                conversation, "question_caracteres_max", "conversation"
            ),
            echanges=lecteur.entier(conversation, "echanges", "conversation"),
            tours_max=lecteur.entier(conversation, "tours_max", "conversation"),
            caracteres_max=lecteur.entier(
                conversation, "caracteres_max", "conversation"
            ),
            tour_caracteres_max=lecteur.entier(
                conversation, "tour_caracteres_max", "conversation"
            ),
        ),
        corps_octets_max=lecteur.entier(
            lecteur.table(donnees, "corps"), "octets_max", "corps"
        ),
        hote=hote,
        port=port,
        metriques_reseaux=tuple(reseaux),
        fournisseurs=fournisseurs,
        textes=lire_textes(lecteur.table(donnees, "textes"), ecarts),
    )
    if ecarts:
        raise ConfigurationInvalide(ecarts)
    return resultat
