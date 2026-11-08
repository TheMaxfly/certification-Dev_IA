"""La configuration : `config/reglages.toml` (versionné) et l'environnement.

Tout est lu et validé au démarrage ; un écart empêche de démarrer, et le message nomme
la variable ou le réglage, jamais une valeur. La clé de l'API n'est jamais dans un
fichier suivi, et son `repr` ne la montre pas (même forme que `Cle` du module 11,
réécrite : ce module ne connaît le 11 que par HTTP).

E4, lot 1 — la base et les sessions :
- `APPLICATION_ENVIRONNEMENT` : `developpement` (défaut), `preproduction`,
  `production` ;
- `APPLICATION_BASE_DSN` : facultatif, SANS mot de passe (`~/.pgpass` sur le poste, un
  fichier monté dans un conteneur) ; absent, le conseiller fonctionne et les comptes
  attendent (décision 11) ;
- `APPLICATION_CLE_SESSION` : 32 octets au moins ; obligatoire en préproduction et en
  production ; absente en développement, une clé éphémère est tirée au démarrage (les
  sessions se perdent au redémarrage ; journal d'avertissement) ;
- **la garde d'environnement** (décision 10) : la base nommée par le DSN doit être celle
  de l'environnement — `preproduction` ↔ `apimanga_preprod`, `production` ↔ `apimanga` ;
  en développement, `apimanga` est refusée. Le message nomme la base attendue, jamais
  le DSN.
Ni le DSN ni la clé de session n'entrent dans un `repr` ni dans le journal.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
import string
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from psycopg import ProgrammingError, conninfo

RACINE_MODULE = Path(__file__).resolve().parents[2]
FICHIER_REGLAGES = RACINE_MODULE / "config" / "reglages.toml"
VARIABLE_CLE = "APPLICATION_CLE_API"
VARIABLE_ENVIRONNEMENT = "APPLICATION_ENVIRONNEMENT"
VARIABLE_BASE = "APPLICATION_BASE_DSN"
VARIABLE_CLE_SESSION = "APPLICATION_CLE_SESSION"
VARIABLE_CLE_CATALOGUE = "APPLICATION_CLE_CATALOGUE"
VARIABLE_ADRESSE_CATALOGUE = "CATALOGUE_ADRESSE"
ENVIRONNEMENTS = ("developpement", "preproduction", "production")
#: L'environnement → la seule base qu'il admet ; le développement admet toute base,
#: sauf celle de la production.
BASES_EXIGEES = {"preproduction": "apimanga_preprod", "production": "apimanga"}
BASE_PRODUCTION = "apimanga"
CLE_SESSION_OCTETS_MIN = 32
#: Les bornes du texte d'une critique dans la base (migration 027,
#: `critiques_texte_longueur`) : celles de l'application doivent y tenir.
TEXTE_CRITIQUE_BASE = (1, 10000)
#: La forme d'un code de label dans la base (027, `critiques_label_forme`).
FORME_LABEL = re.compile(r"[a-z_]{1,40}")

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
            " erreur_400_titre erreur_400_texte erreur_404_titre erreur_404_texte"
            " erreur_405_titre erreur_405_texte erreur_401_titre erreur_401_texte"
            " erreur_403_titre erreur_403_texte erreur_csrf_titre erreur_csrf_texte"
            " erreur_comptes_titre erreur_comptes_texte"
            " menu_etiquette menu_conseiller menu_mon_compte menu_critiques"
            " menu_comptes"
            " menu_connexion menu_inscription menu_deconnexion champ_pseudo"
            " champ_mot_de_passe champ_confirmation champ_ancien champ_nouveau"
            " champ_nom_affiche champ_role champ_mot_de_passe_initial"
            " inscription_titre inscription_collecte inscription_collecte_lien"
            " inscription_bouton inscription_deja erreur_pseudo_pris"
            " erreur_confirmation connexion_titre connexion_bouton connexion_refus"
            " connexion_pas_de_compte mdp_titre mdp_bouton mdp_ancien_faux mdp_change"
            " mdp_a_changer admin_titre admin_creer_titre admin_creer_bouton"
            " admin_liste_titre admin_colonne_pseudo admin_colonne_nom"
            " admin_colonne_role admin_colonne_etat admin_colonne_cree"
            " admin_colonne_action admin_filtre_etiquette admin_filtre_tous"
            " admin_filtre_bouton compte_role_client compte_role_libraire"
            " compte_role_admin admin_actif admin_inactif admin_desactiver"
            " admin_reactiver admin_cree admin_desactive admin_reactive admin_soi"
            " admin_aucun admin_inconnu admin_precedente admin_suivante"
            " critiques_titre critiques_bientot apropos_comptes_titre"
            " apropos_deconnexion apropos_suppression erreur_role admin_inchange"
            " mdp_continuer"
            # E4, lot 2a : le catalogue.
            " menu_catalogue catalogue_titre catalogue_encart_titre"
            " catalogue_encart catalogue_vers_conseiller catalogue_champ"
            " catalogue_bouton catalogue_resultats_titre catalogue_pagination"
            " catalogue_precedente catalogue_suivante catalogue_aucun_conseiller"
            " catalogue_aucun_fautes catalogue_trouve_par_auteur"
            " serie_metadonnees serie_type serie_annee serie_note_etiquette"
            " serie_sans_note serie_reseau_titre serie_reseau_vide"
            " serie_listes_titre serie_listes serie_synopsis_titre"
            " serie_sans_synopsis serie_genres_titre serie_lien_catalogue"
            " serie_retour_catalogue"
            # E4, lot 3 : les critiques signées.
            " menu_moderation critiques_l5 critiques_vide critiques_vers_catalogue"
            " critiques_colonne_serie critiques_colonne_tome critiques_colonne_note"
            " critiques_colonne_label critiques_colonne_statut"
            " critiques_colonne_dates critiques_colonne_action critiques_serie_entiere"
            " critiques_statut_publiee critiques_statut_retiree"
            " critiques_titre_indisponible critiques_filtre_etiquette"
            " critiques_filtre_toutes critiques_filtre_bouton critiques_modifier"
            " critiques_retirer critiques_avis_publiee critiques_avis_modifiee"
            " critiques_avis_retiree critiques_avis_deja_retiree critiques_precedente"
            " critiques_suivante critique_nouvelle_titre critique_modifier_titre"
            " critique_serie_rappel critique_champ_tome critique_tome_serie"
            " critique_champ_texte critique_champ_note critique_note_choisir"
            " critique_champ_label critique_label_aucun critique_publier"
            " critique_enregistrer critique_annuler critique_tome_fixe"
            " critique_erreur_note critique_erreur_label critique_erreur_tome"
            " critique_erreur_controle critique_deja_publiee critique_deja_lien"
            " critique_serie_inconnue_titre critique_serie_inconnue"
            " critique_retiree_titre critique_retiree_texte critique_retirer_titre"
            " critique_retirer_texte critique_retirer_bouton critique_ecrire"
            " critique_moderer serie_reseau_indisponible serie_reseau_sans_avis"
            " admin_critiques_titre admin_critiques_vide admin_critiques_filtre"
            " admin_critiques_tous admin_critiques_colonne_libraire"
            " admin_critiques_colonne_debut admin_critiques_colonne_date"
            " admin_critique_retirer_titre admin_critique_retirer_texte"
            " admin_critiques_inactif"
            " erreur_413_titre erreur_413_texte erreur_500_titre erreur_500_texte"
            " erreur_retour demonstration_etiquette demo_rang demo_score"
            " demo_infobulle demo_absent trace_titre trace_phrase"
            " trace_classement_titre trace_colonne_rang trace_colonne_titre"
            " trace_colonne_score trace_colonne_citee trace_colonne_passages"
            " trace_citee trace_non_citee trace_aucune_serie trace_non_cites_titre"
            " trace_aucun_non_cite trace_question_titre trace_question_origine"
            " trace_question_reformulee trace_question_partie trace_partie_origine"
            " trace_partie_reformulee trace_reglages_titre trace_nombre_series"
            " trace_passages_par_serie trace_catalogue_seul trace_modele_embedding"
            " trace_revision trace_corpus trace_encodage trace_oui trace_non"
            " trace_modele_titre trace_profil trace_fournisseur trace_modele"
            " trace_temperature trace_longueur trace_cout trace_latence_recherche"
            " trace_latence_reformulation trace_latence_modele trace_latence_totale"
            " trace_identifiant trace_appels_titre"
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
    "details_sans_carte": frozenset({"ids"}),
    "passage_extrait": frozenset({"genre", "fragment"}),
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
    "trace_non_cites_resume": frozenset({"titre", "n"}),
    "trace_cout_valeur": frozenset({"cout"}),
    "menu_connecte": frozenset({"nom"}),
    "aide_pseudo": frozenset({"min", "max"}),
    "aide_mot_de_passe": frozenset({"min", "max"}),
    "erreur_pseudo_forme": frozenset({"min", "max"}),
    "erreur_mdp_longueur": frozenset({"min", "max"}),
    "erreur_nom_longueur": frozenset({"max"}),
    "admin_page": frozenset({"n", "total"}),
    "mdp_verrou": frozenset({"minutes"}),
    "connexion_refus_regles": frozenset({"seuil", "minutes"}),
    "catalogue_aide": frozenset({"min", "max"}),
    "catalogue_note": frozenset({"note"}),
    "catalogue_trouve_par": frozenset({"titre"}),
    "catalogue_aucun": frozenset({"q"}),
    "catalogue_page": frozenset({"n", "total"}),
    "catalogue_erreur_saisie": frozenset({"min", "max"}),
    "serie_note": frozenset({"note", "votes"}),
    "apropos_comptes": frozenset({"seuil", "minutes"}),
    "critique_bornes": frozenset({"min", "max"}),
    "critique_erreur_longueur": frozenset({"min", "max", "n"}),
    "critique_note": frozenset({"note"}),
    "critique_date": frozenset({"date"}),
    "critique_modifiee_le": frozenset({"date"}),
    "critiques_page": frozenset({"n", "total"}),
    "serie_reseau_moyenne": frozenset({"note", "n"}),
    "serie_reseau_moyenne_une": frozenset({"note"}),
    "critique_lien_modifier": frozenset({"serie"}),
    "critique_lien_retirer": frozenset({"serie"}),
    "critique_lien_moderer": frozenset({"nom"}),
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
class Comptes:
    """Les réglages des comptes et des sessions (`[comptes]`)."""

    duree_session_s: int
    echecs_avant_verrou: int
    duree_verrou_s: int
    pseudo_min: int
    pseudo_max: int
    mot_de_passe_min: int
    mot_de_passe_max: int
    nom_affiche_max: int
    delai_base_s: float
    par_page: int


@dataclass(frozen=True, slots=True)
class DelaisCatalogue:
    connexion_s: float
    reponse_s: float


@dataclass(frozen=True, slots=True)
class Catalogue:
    """L'API data du module 02 (`[catalogue]`) : son adresse, la clé de l'application
    (None : non configuré — développement seulement), ses délais, ses bornes."""

    adresse: str
    cle: str | None = field(repr=False)
    delais: DelaisCatalogue
    par_page: int
    q_min: int
    q_max: int


@dataclass(frozen=True, slots=True)
class Label:
    """Un label de critique : son code (gardé en base) et son libellé (affiché)."""

    code: str
    libelle: str


@dataclass(frozen=True, slots=True)
class Critiques:
    """Les règles des critiques signées (`[critiques]`) : bornes du texte, liste fixe
    des labels, taille d'une page de liste."""

    texte_min: int
    texte_max: int
    par_page: int
    labels: tuple[Label, ...]

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(label.code for label in self.labels)

    def libelle(self, code: str | None) -> str | None:
        """Le libellé d'un code de la liste ; None pour un code absent ou inconnu."""
        return next((lab.libelle for lab in self.labels if lab.code == code), None)


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
    mode_demonstration_defaut: bool
    profils_exclus: frozenset[str]
    positions: tuple[Position, ...]
    limites: Limites
    corps_octets_max: int
    hote: str
    port: int
    metriques_reseaux: tuple[Reseau, ...]
    fournisseurs: Mapping[str, Fournisseur]
    textes: Mapping[str, str]
    environnement: str
    base_dsn: str | None = field(repr=False)
    cle_session: str = field(repr=False)
    cle_session_ephemere: bool
    comptes: Comptes
    catalogue: Catalogue
    critiques: Critiques


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


def lire_environnement(environ: Mapping[str, str], ecarts: list[str]) -> str:
    valeur = environ.get(VARIABLE_ENVIRONNEMENT, "").strip() or "developpement"
    if valeur not in ENVIRONNEMENTS:
        ecarts.append(f"{VARIABLE_ENVIRONNEMENT} : attendu {', '.join(ENVIRONNEMENTS)}")
    return valeur


def garder_base(environnement: str, dsn: str | None, ecarts: list[str]) -> None:
    """La garde d'environnement : la base du DSN est celle de l'environnement."""
    if dsn is None:
        return
    try:
        parametres = conninfo.conninfo_to_dict(dsn)
    except ProgrammingError:
        ecarts.append(f"{VARIABLE_BASE} : illisible")
        return
    if parametres.get("password"):
        ecarts.append(
            f"{VARIABLE_BASE} : porte un mot de passe — il se range dans ~/.pgpass"
            " (ou un fichier monté), jamais dans le DSN"
        )
    nom = parametres.get("dbname")
    exigee = BASES_EXIGEES.get(environnement)
    if not nom:
        ecarts.append(f"{VARIABLE_BASE} : la base n'est pas nommée")
    elif exigee is not None and nom != exigee:
        ecarts.append(f"garde d'environnement : {environnement} exige la base {exigee}")
    elif exigee is None and nom == BASE_PRODUCTION:
        ecarts.append(
            "garde d'environnement : la base de production est refusée en développement"
        )


def lire_cle_session(
    environ: Mapping[str, str], environnement: str, ecarts: list[str]
) -> tuple[str, bool]:
    """La clé de signature des sessions, et si elle est éphémère."""
    cle = environ.get(VARIABLE_CLE_SESSION, "").strip()
    if not cle:
        if environnement in BASES_EXIGEES:
            ecarts.append(
                f"{VARIABLE_CLE_SESSION} : absente — obligatoire en {environnement}"
            )
        return secrets.token_urlsafe(48), True
    if len(cle.encode()) < CLE_SESSION_OCTETS_MIN:
        ecarts.append(
            f"{VARIABLE_CLE_SESSION} : trop courte"
            f" ({CLE_SESSION_OCTETS_MIN} octets au moins)"
        )
    return cle, False


#: La borne de `limit` de `GET /series` (contrat 0.5.0 du 02).
CATALOGUE_PAR_PAGE_MAX = 50


def lire_catalogue(
    lecteur: _Lecteur,
    donnees: Table,
    environ: Mapping[str, str],
    environnement: str,
    ecarts: list[str],
) -> Catalogue:
    """L'API data du 02. Sa clé est exigée si l'adresse est posée par l'environnement
    ou hors du développement ; absente en développement, le catalogue est « non
    configuré » : l'encart le dit, tout le reste fonctionne."""
    table = lecteur.table(donnees, "catalogue")
    delais = lecteur.table(table, "delais", "catalogue.")
    adresse_env = environ.get(VARIABLE_ADRESSE_CATALOGUE, "").strip()
    adresse = adresse_env or lecteur.texte(table, "adresse", "catalogue")
    decoupee = urlsplit(adresse)
    if decoupee.scheme not in ("http", "https") or not decoupee.hostname:
        ecarts.append("adresse du catalogue : attendu http(s)://hôte[:port]")
    cle: str | None = environ.get(VARIABLE_CLE_CATALOGUE, "").strip() or None
    if cle is None and (adresse_env or environnement in BASES_EXIGEES):
        ecarts.append(
            f"{VARIABLE_CLE_CATALOGUE} : absente — obligatoire"
            + (
                f" en {environnement}"
                if environnement in BASES_EXIGEES
                else f" quand {VARIABLE_ADRESSE_CATALOGUE} est posée"
            )
        )
    elif cle is not None and not cle.isascii():
        ecarts.append(
            f"{VARIABLE_CLE_CATALOGUE} : caractères hors ASCII — clé illisible"
        )
    par_page = lecteur.entier(table, "par_page", "catalogue")
    if par_page > CATALOGUE_PAR_PAGE_MAX:
        ecarts.append(
            f"reglages.toml : [catalogue.par_page] {CATALOGUE_PAR_PAGE_MAX} au plus"
            " (la borne de l'API data)"
        )
    q_min = lecteur.entier(table, "q_min", "catalogue")
    q_max = lecteur.entier(table, "q_max", "catalogue")
    if q_min > q_max:
        ecarts.append("reglages.toml : [catalogue] q_min au plus q_max")
    return Catalogue(
        adresse=adresse,
        cle=cle,
        delais=DelaisCatalogue(
            connexion_s=lecteur.positif(delais, "connexion_s", "catalogue.delais"),
            reponse_s=lecteur.positif(delais, "reponse_s", "catalogue.delais"),
        ),
        par_page=par_page,
        q_min=q_min,
        q_max=q_max,
    )


def lire_comptes(lecteur: _Lecteur, donnees: Table) -> Comptes:
    comptes = lecteur.table(donnees, "comptes")
    return Comptes(
        duree_session_s=lecteur.entier(comptes, "duree_session_s", "comptes"),
        echecs_avant_verrou=lecteur.entier(comptes, "echecs_avant_verrou", "comptes"),
        duree_verrou_s=lecteur.entier(comptes, "duree_verrou_s", "comptes"),
        pseudo_min=lecteur.entier(comptes, "pseudo_min", "comptes"),
        pseudo_max=lecteur.entier(comptes, "pseudo_max", "comptes"),
        mot_de_passe_min=lecteur.entier(comptes, "mot_de_passe_min", "comptes"),
        mot_de_passe_max=lecteur.entier(comptes, "mot_de_passe_max", "comptes"),
        nom_affiche_max=lecteur.entier(comptes, "nom_affiche_max", "comptes"),
        delai_base_s=lecteur.positif(comptes, "delai_base_s", "comptes"),
        par_page=lecteur.entier(comptes, "par_page", "comptes"),
    )


def lire_critiques(lecteur: _Lecteur, donnees: Table, ecarts: list[str]) -> Critiques:
    """Les bornes dans celles de la base (027), les labels conformes à sa forme, uniques
    et libellés ; une liste vide est refusée."""
    table = lecteur.table(donnees, "critiques")
    texte_min = lecteur.entier(table, "texte_min", "critiques")
    texte_max = lecteur.entier(table, "texte_max", "critiques")
    base_min, base_max = TEXTE_CRITIQUE_BASE
    if not base_min <= texte_min <= texte_max <= base_max:
        ecarts.append(
            "reglages.toml : [critiques] texte_min ≤ texte_max, entre"
            f" {base_min} et {base_max} (les bornes de la migration 027)"
        )
    labels: list[Label] = []
    brutes = table.get("labels")
    if not isinstance(brutes, list) or not brutes:
        ecarts.append("reglages.toml : [critiques.labels] liste non vide attendue")
        brutes = []
    for brute in cast(list[object], brutes):
        if not isinstance(brute, dict):
            ecarts.append("reglages.toml : [critiques.labels] table attendue")
            continue
        entree = cast(Table, brute)
        code = lecteur.texte(entree, "code", "critiques.labels")
        libelle = lecteur.texte(entree, "libelle", "critiques.labels")
        if code and not FORME_LABEL.fullmatch(code):
            ecarts.append(
                "reglages.toml : [critiques.labels] code hors forme"
                " (^[a-z_]{1,40}$, la forme de la migration 027)"
            )
        labels.append(Label(code, libelle))
    codes = [label.code for label in labels]
    if len(set(codes)) != len(codes):
        ecarts.append("reglages.toml : [critiques.labels] code en double")
    return Critiques(
        texte_min=texte_min,
        texte_max=texte_max,
        par_page=lecteur.entier(table, "par_page", "critiques"),
        labels=tuple(labels),
    )


def charger_comptes(
    environ: Mapping[str, str], fichier: Path = FICHIER_REGLAGES
) -> tuple[str, str, Comptes]:
    """Ce qu'il faut à la commande du premier administrateur, et rien d'autre (ni la
    clé de l'API ni celle des sessions) : l'environnement, le DSN — obligatoire ici —
    sous la garde d'environnement, et les règles des comptes."""
    ecarts: list[str] = []
    lecteur = _Lecteur(ecarts)
    donnees = cast(Table, tomllib.loads(fichier.read_text(encoding="utf-8")))
    environnement = lire_environnement(environ, ecarts)
    dsn = environ.get(VARIABLE_BASE, "").strip()
    if not dsn:
        ecarts.append(f"{VARIABLE_BASE} : absente — la commande écrit dans la base")
    garder_base(environnement, dsn or None, ecarts)
    comptes = lire_comptes(lecteur, donnees)
    if ecarts:
        raise ConfigurationInvalide(ecarts)
    return environnement, dsn, comptes


def charger(environ: Mapping[str, str], fichier: Path = FICHIER_REGLAGES) -> Reglages:
    """Le fichier et l'environnement, validés ; au moindre écart, une exception."""
    ecarts: list[str] = []
    donnees = cast(Table, tomllib.loads(fichier.read_text(encoding="utf-8")))
    lecteur = _Lecteur(ecarts)

    cle = environ.get(VARIABLE_CLE, "").strip()
    if not cle:
        ecarts.append(f"{VARIABLE_CLE} : absente ou vide — aucun accès à l'API")
    elif not cle.isascii():
        # Elle part dans un en-tête : le refus vient d'ici, sans la montrer.
        ecarts.append(f"{VARIABLE_CLE} : caractères hors ASCII — clé illisible")

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

    environnement = lire_environnement(environ, ecarts)
    base_dsn = environ.get(VARIABLE_BASE, "").strip() or None
    garder_base(environnement, base_dsn, ecarts)
    cle_session, ephemere = lire_cle_session(environ, environnement, ecarts)

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
        mode_demonstration_defaut=lecteur.booleen(
            reglages, "mode_demonstration_defaut", "reglages"
        ),
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
        environnement=environnement,
        base_dsn=base_dsn,
        cle_session=cle_session,
        cle_session_ephemere=ephemere,
        comptes=lire_comptes(lecteur, donnees),
        catalogue=lire_catalogue(lecteur, donnees, environ, environnement, ecarts),
        critiques=lire_critiques(lecteur, donnees, ecarts),
    )
    if ecarts:
        raise ConfigurationInvalide(ecarts)
    return resultat
