"""Les tableaux de bord : des fichiers du dépôt, comparés à une liste attendue.

Chaque panneau a son titre, son unité, ses requêtes, et une description qui dit les
trois choses : ce qu'il montre, sa valeur normale, ce qu'on fait si elle bouge. Aucune
adresse, aucun identifiant de source de données en dur, aucune valeur libre.
"""

from __future__ import annotations

import json
import re
import tomllib
from typing import Any

import jsonschema
import pytest
from conftest import MODULE, RACINE

TABLEAUX = MODULE / "grafana" / "tableaux"

#: Le titre de chaque panneau et les métriques qu'il lit.
MODELE = {
    "Profil par défaut": {"api_modele_profil_actif"},
    "Profils disponibles": {"api_modele_profil_disponible"},
    "Appels par profil et rôle — qui rédige vraiment": {"api_modele_llm_appels_total"},
    "Appels en échec par statut": {"api_modele_llm_appels_total"},
    "Jetons par profil et type": {"api_modele_llm_jetons_total"},
    "Coût estimé par heure": {"api_modele_llm_cout_usd_total"},
    "Latence du modèle, p50 et p95": {"api_modele_llm_latence_secondes_bucket"},
    "Sorties invalides par motif": {"api_modele_sortie_invalide_total"},
    "Réponses et refus": {"api_modele_reponses_total"},
    "Sonde d'équivalence : cosinus minimal": {"modele_sonde_equivalence_cosinus_min"},
    "Âge et échecs de la sonde": {
        "modele_sonde_derniere_execution_timestamp",
        "modele_sonde_tranches_sous_seuil",
        "modele_sonde_erreurs_total",
    },
    "Dernière mesure : taux de réussite à 10": {"modele_derniere_mesure_hit_rate_10"},
    "Âge de la dernière mesure et lecture de la base": {
        "modele_derniere_mesure_timestamp",
        "modele_exporteur_base_activee",
    },
    "Encodage en service": {"api_modele_encodage_en_service"},
}
SERVICE = {
    "Cibles": {"up"},
    "Requêtes de l'API par route et statut": {"api_modele_requetes_total"},
    "Erreurs de l'API par code": {"api_modele_erreurs_total"},
    "Latence de l'API, p95": {"api_modele_duree_requete_secondes_bucket"},
    "Débit refusé par clé": {"api_modele_debit_refuse_total"},
    "Requêtes de l'application": {"application_requetes_total"},
    "Latence de l'application, p95": {"application_latence_secondes_bucket"},
    "Erreurs vues de l'application": {"application_api_erreurs_total"},
    "Service d'embedding : requêtes et file": {"te_request_count", "te_queue_size"},
    "Service d'embedding : durée moyenne": {
        "te_request_duration_sum",
        "te_request_duration_count",
    },
    "Refus du service d'embedding": {"te_request_failure"},
    "Machine : mémoire et processeur": {
        "node_memory_MemAvailable_bytes",
        "node_cpu_seconds_total",
    },
    "Machine : disque": {"node_filesystem_avail_bytes"},
}
#: Le tableau « application » (E5, lot 1) : l'application vue d'elle-même.
APPLICATION = {
    "Cible de l'application": {"up"},
    "Dépendances vues de l'application": {
        "application_base_etat",
        "application_catalogue_etat",
        "application_api_sante_ok",
    },
    "Réponses 503 de l'application": {"application_requetes_total"},
    "Questions au conseiller par mode et issue": {"application_questions_total"},
    "Refus par borne": {"application_quota_refus_total"},
    "Coût du jour et plafond": {
        "application_cout_jour_usd",
        "application_plafond_cout_etat",
    },
    "Latence du conseiller, p50 et p95": {"application_latence_secondes_bucket"},
    "Appels au catalogue par route et statut": {"application_catalogue_appels_total"},
    "Appels à l'API du modèle vus de l'application": {"application_api_appels_total"},
    "Gestes des comptes": {
        "application_critiques_total",
        "application_listes_total",
        "application_comptes_supprimes_total",
    },
    "Profil actif et tests des clés": {
        "application_profil_actif_info",
        "application_verifications_cles_total",
    },
}
TOUS = {"modele": MODELE, "service": SERVICE, "application": APPLICATION}
#: Les étiquettes que les légendes du tableau « application » peuvent citer : les
#: listes fermées de `metriques.py` du 12 ; aucune ne porte une donnée personnelle.
ETIQUETTES_APPLICATION = {
    "route",
    "etat",
    "mode",
    "issue",
    "motif",
    "route_catalogue",
    "statut",
    "route_api",
    "action",
    "profil",
}
#: Les seuils tracés et nommés : une série `vector(…)`, son nom dans la légende.
SEUILS = {
    "Coût estimé par heure": ("0.5", "0,50 $"),
    "Sorties invalides par motif": ("3", "3 en 15 min"),
    "Sonde d'équivalence : cosinus minimal": ("0.999", "0,999"),
    "Dernière mesure : taux de réussite à 10": ("0.5254", "31 / 59"),
    "Machine : mémoire et processeur": ("6900000000", "6,9 Go"),
}
#: La provisoire du commit « métriques » du module 11 : un `revert` de ce commit laisse
#: le panneau vide, il ne casse pas ce module.
PROVISOIRES = {"api_modele_reponses_total"}
SCHEMA = {
    "type": "object",
    "required": ["uid", "title", "panels", "templating", "time", "refresh"],
    "properties": {
        "uid": {"type": "string", "pattern": "^[a-z]+$"},
        "title": {"type": "string", "minLength": 1},
        "refresh": {"const": "15s"},
        "time": {"const": {"from": "now-1h", "to": "now"}},
        "panels": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": [
                    "id",
                    "type",
                    "title",
                    "description",
                    "targets",
                    "gridPos",
                    "fieldConfig",
                ],
                "properties": {
                    "type": {"enum": ["timeseries", "stat"]},
                    "title": {"type": "string", "minLength": 1},
                    "description": {"type": "string", "minLength": 40},
                    "targets": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["refId", "expr", "datasource"],
                            "properties": {"expr": {"type": "string", "minLength": 1}},
                        },
                    },
                },
            },
        },
    },
}


def tableau(nom: str) -> dict[str, Any]:
    return json.loads((TABLEAUX / f"{nom}.json").read_text(encoding="utf-8"))


def panneaux(nom: str) -> list[dict[str, Any]]:
    return tableau(nom)["panels"]


def noms_de_metriques(expr: str) -> set[str]:
    """Les noms de métriques d'une expression, sans fonctions ni étiquettes."""
    sans_etiquettes = re.sub(r"\{[^}]*\}|\[[^\]]*\]", " ", expr)
    sans_clauses = re.sub(r"\bby\s*\([^)]*\)", " ", sans_etiquettes)
    noms: set[str] = set()
    for mot in re.finditer(r"[a-zA-Z_:][a-zA-Z0-9_:]*", sans_clauses):
        if not sans_clauses[mot.end() :].lstrip().startswith("("):
            noms.add(mot.group(0))
    return noms


def declarees(chemin: str) -> set[str]:
    """Les noms déclarés par un module, tels que Prometheus les relève."""
    texte = (RACINE / chemin).read_text(encoding="utf-8")
    source = re.findall(r'^\s+"((?:api_modele|application)_[a-z0-9_]+)",$', texte, re.M)
    compteurs = re.findall(r'Counter\(\s*"([a-z0-9_]+)"', texte)
    histogrammes = re.findall(r'Histogram\(\s*"([a-z0-9_]+)"', texte)
    return (
        set(source)
        | {f"{c}_total" for c in compteurs}
        | {f"{h}_bucket" for h in histogrammes}
    )


@pytest.mark.parametrize("nom", list(TOUS))
def test_la_forme_du_tableau(nom: str):
    jsonschema.validate(tableau(nom), SCHEMA)
    assert tableau(nom)["uid"] == nom


@pytest.mark.parametrize(("nom", "attendu"), list(TOUS.items()))
def test_les_panneaux_sont_ceux_de_la_liste(nom: str, attendu: dict[str, set[str]]):
    trouves: dict[str, set[str]] = {}
    for panneau in panneaux(nom):
        noms: set[str] = set()
        for cible in panneau["targets"]:
            noms |= noms_de_metriques(cible["expr"])
        trouves[panneau["title"]] = noms
    assert trouves == attendu


def test_les_identifiants_de_panneau_sont_uniques():
    for nom in TOUS:
        ids = [p["id"] for p in panneaux(nom)]
        assert len(ids) == len(set(ids))


@pytest.mark.parametrize("nom", list(TOUS))
def test_chaque_description_dit_les_trois_choses(nom: str):
    for panneau in panneaux(nom):
        description = panneau["description"]
        assert "Ce qu'il montre" in description, panneau["title"]
        assert "Normal" in description, panneau["title"]


def test_les_seuils_sont_traces_et_nommes():
    tous = {p["title"]: p for nom in ("modele", "service") for p in panneaux(nom)}
    for titre, (valeur, nommé) in SEUILS.items():
        cibles = [c for c in tous[titre]["targets"] if c["expr"] == f"vector({valeur})"]
        assert len(cibles) == 1, titre
        assert nommé in cibles[0]["legendFormat"], titre


def test_aucun_etat_n_est_porte_par_la_seule_couleur():
    tous = {p["title"]: p for nom in ("modele", "service") for p in panneaux(nom)}
    for titre, mots in {
        "Cibles": {"en marche", "arrêtée"},
        "Profils disponibles": {"disponible", "absent"},
    }.items():
        correspondances = tous[titre]["fieldConfig"]["defaults"]["mappings"]
        textes = {v["text"] for c in correspondances for v in c["options"].values()}
        assert textes == mots, titre
    for panneau in tous.values():
        if panneau["type"] == "stat":
            assert panneau["options"]["colorMode"] == "none", panneau["title"]


def test_le_profil_par_defaut_ne_se_donne_pas_pour_celui_qui_redige():
    titres = [p["title"] for p in panneaux("modele")]
    assert "Profil par défaut" in titres
    assert not any("qui répond" in t for t in titres)
    assert any("qui rédige vraiment" in t for t in titres)


def test_le_cout_dit_qu_il_repart_de_zero_et_qu_il_est_estime():
    (cout,) = [p for p in panneaux("modele") if p["title"] == "Coût estimé par heure"]
    assert "repart de zéro" in cout["description"]
    assert "pas la facture" in cout["description"]


@pytest.mark.parametrize("nom", list(TOUS))
def test_les_variables_et_la_periode(nom: str):
    donnees = tableau(nom)
    variables = {v["name"]: v for v in donnees["templating"]["list"]}
    assert set(variables) == {"source", "profil", "intervalle"}
    assert variables["source"]["type"] == "datasource"
    assert variables["profil"]["includeAll"] is True


@pytest.mark.parametrize("nom", list(TOUS))
def test_aucun_identifiant_de_source_ni_adresse_en_dur(nom: str):
    texte = (TABLEAUX / f"{nom}.json").read_text(encoding="utf-8")
    assert "http://" not in texte
    assert "https://" not in texte
    assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", texte)
    donnees = tableau(nom)
    for panneau in donnees["panels"]:
        assert panneau["datasource"]["uid"] == "${source}"
        for cible in panneau["targets"]:
            assert cible["datasource"]["uid"] == "${source}"


def test_les_metriques_lues_existent_aux_modules_11_et_12():
    """Un tableau ne peut pas citer une métrique disparue."""
    api = declarees("11_api_modele/src/api_modele/metriques.py")
    application = declarees("12_application/src/application/metriques.py")
    lues = {m for attendu in TOUS.values() for noms in attendu.values() for m in noms}
    for metrique in lues:
        if metrique.startswith("api_modele_") and metrique not in PROVISOIRES:
            assert metrique in api, metrique
        if metrique.startswith("application_"):
            assert metrique in application, metrique


def config_du_panneau(nom: str, titre: str) -> dict[str, Any]:
    (panneau,) = [p for p in panneaux(nom) if p.get("title") == titre]
    return panneau["fieldConfig"]["defaults"]


def test_les_trois_defauts_d_affichage_sont_corriges():
    """Relevés à l'étape 5 : graduations du disque toutes égales faute de décimale ;
    axe du taux de réussite zoomé sur une ligne plate ; trous de la durée moyenne
    reliés par une diagonale."""
    assert config_du_panneau("service", "Machine : disque")["decimals"] == 3
    taux = config_du_panneau("modele", "Dernière mesure : taux de réussite à 10")
    assert (taux["min"], taux["max"]) == (0, 1)
    duree = config_du_panneau("service", "Service d'embedding : durée moyenne")
    assert duree["custom"]["spanNulls"] is False


# --------------------------------------------------------------------------- #
#  Le tableau « application » (E5, lot 1)
# --------------------------------------------------------------------------- #

REGLAGES_12 = RACINE / "12_application" / "config" / "reglages.toml"


def plafond_du_jour() -> float:
    """Le plafond de coût du jour de l'application, lu dans sa configuration."""
    reglages = tomllib.loads(REGLAGES_12.read_text(encoding="utf-8"))
    return float(reglages["conseiller"]["limites"]["cout_jour_max_usd"])


def test_le_plafond_et_le_seuil_de_h_sont_traces_et_nommes_depuis_la_configuration():
    """La règle avant le chiffre : le plafond et le seuil de l'alerte H (80 %) se
    dérivent de `cout_jour_max_usd` ; changer le plafond sans le tableau échoue."""
    (cout,) = [
        p for p in panneaux("application") if p["title"] == "Coût du jour et plafond"
    ]
    vecteurs = {
        float(m.group(1)): c["legendFormat"]
        for c in cout["targets"]
        if (m := re.fullmatch(r"vector\(([0-9.]+)\)", c["expr"]))
    }
    plafond = plafond_du_jour()
    assert set(vecteurs) == {plafond, round(0.8 * plafond, 6)}
    assert vecteurs[plafond].startswith("Plafond du jour : ")
    assert vecteurs[round(0.8 * plafond, 6)].startswith("Seuil de l'alerte H : ")
    assert "0,50 $" in vecteurs[plafond] and "0,40 $" in vecteurs[0.4]


def test_les_dependances_disent_qu_elles_ne_se_relisent_pas_seules():
    (deps,) = [
        p
        for p in panneaux("application")
        if p["title"] == "Dépendances vues de l'application"
    ]
    assert "à la dernière requête qui a touché la dépendance" in deps["description"]
    assert "ne se relit pas seule" in deps["description"]


def test_les_etats_de_l_application_sont_en_toutes_lettres():
    tous = {p["title"]: p for p in panneaux("application")}

    def textes(config: dict[str, Any]) -> set[str]:
        return {v["text"] for c in config for v in c["options"].values()}

    cible = tous["Cible de l'application"]["fieldConfig"]["defaults"]["mappings"]
    assert textes(cible) == {"en marche", "arrêtée"}
    deps = tous["Dépendances vues de l'application"]["fieldConfig"]
    assert textes(deps["defaults"]["mappings"]) == {"état à la dernière requête"}
    (api,) = deps["overrides"]
    assert api["matcher"]["options"] == "C"
    assert textes(api["properties"][0]["value"]) == {
        "répondait à la dernière lecture",
        "ne répondait pas à la dernière lecture",
    }
    for panneau in tous.values():
        if panneau["type"] == "stat":
            assert panneau["options"]["colorMode"] == "none", panneau["title"]


def test_aucune_legende_ne_porte_une_donnee_personnelle():
    """Les légendes ne citent que des étiquettes des listes fermées du 12 ;
    l'apparence n'est pas affichée."""
    for panneau in panneaux("application"):
        for cible in panneau["targets"]:
            citees = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", cible["legendFormat"]))
            assert citees <= ETIQUETTES_APPLICATION, panneau["title"]
            assert "apparence" not in cible["expr"]


def test_la_latence_est_celle_de_la_question_au_conseiller():
    (latence,) = [
        p
        for p in panneaux("application")
        if p["title"] == "Latence du conseiller, p50 et p95"
    ]
    assert all('route="/question"' in c["expr"] for c in latence["targets"])
