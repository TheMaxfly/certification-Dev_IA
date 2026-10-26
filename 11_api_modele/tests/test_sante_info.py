"""`/v1/sante` (sans clé, des états seulement) et `/v1/info` (avec clé)."""

from __future__ import annotations

import psycopg
from conftest import (
    CLE_FOURNISSEUR,
    BaseApi,
    Doublure,
    entetes,
    inscrire_encodage,
    promouvoir,
)

import api_modele

ETATS_OK = {
    "statut": "ok",
    "base": "ok",
    "service_embedding": "ok",
    "encodage": "ok",
}


def test_sante_tout_va_bien(api):
    r = api.get("/v1/sante")

    assert r.status_code == 200
    assert r.json() == ETATS_OK


def test_sante_ne_dit_ni_version_ni_modele(api):
    texte = api.get("/v1/sante").text

    assert "embeddinggemma" not in texte
    assert api_modele.__version__ not in texte


def test_sante_service_arrete(api, service_doublure):
    service_doublure.arreter()
    r = api.get("/v1/sante")

    assert r.status_code == 503
    assert r.json() == ETATS_OK | {
        "statut": "degrade",
        "service_embedding": "injoignable",
    }


def test_sante_service_incoherent(api):
    Doublure.info["model_sha"] = "0" * 40
    r = api.get("/v1/sante")

    assert r.status_code == 503
    assert r.json()["service_embedding"] == "incoherent"


def test_sante_base_injoignable(api):
    api.app.state.etat.pool.close()
    r = api.get("/v1/sante")

    assert r.status_code == 503
    assert r.json() == ETATS_OK | {
        "statut": "degrade",
        "base": "injoignable",
        "encodage": "inconnu",
    }


def test_sante_encodage_change_depuis_le_demarrage(
    api, base_api: BaseApi, service_doublure
):
    with psycopg.connect(base_api.dsn_admin) as cx:
        autre = inscrire_encodage(cx, service_doublure.instance, outil_version="1.9.5")
        promouvoir(cx, autre)
    r = api.get("/v1/sante")

    assert r.status_code == 503
    assert r.json()["encodage"] == "change"


def test_info_dit_ce_qui_est_servi(api, base_api: BaseApi, service_doublure):
    instance = service_doublure.instance
    r = api.get("/v1/info", headers=entetes())

    assert r.status_code == 200
    assert r.json() == {
        "version_api": api_modele.__version__,
        "modele": instance.model_id,
        "revision": instance.revision,
        "precision": "float32",
        "dimension": 768,
        "outil": "text-embeddings-inference",
        "outil_version": "1.9.4",
        "corpus_id": "v7",
        "encodage_id": base_api.encodage_id,
        "nb_fragments": 4,
        "reglages": {
            "nombre_series": {"defaut": 10, "min": 1, "max": 50},
            "passages_par_serie": {"defaut": 1, "min": 1, "max": 5},
            "catalogue_seul": {"defaut": True},
        },
        "question": {"longueur_max_caracteres": 500},
        "debit_par_minute": 60,
        "modele_langage": MODELE_LANGAGE,
    }
    assert CLE_FOURNISSEUR not in r.text


def profil(nom, fournisseur, statut, disponible, variable):
    return {
        "nom": nom,
        "fournisseur": fournisseur,
        "modele": nom,
        "statut": statut,
        "disponible": disponible,
        "reglages_absents": [],
        "variable_cle": variable,
    }


#: Le harnais n'a que la clé (factice) du profil actif : les profils OpenAI sont
#: indisponibles, sans que l'API refuse de démarrer.
MODELE_LANGAGE = {
    "profil_actif": "mistral-large-2512",
    "profils": [
        profil("mistral-large-2512", "mistral", "stable", True, "MISTRAL_API_KEY"),
        profil("mistral-large-4", "mistral", "apercu", True, "MISTRAL_API_KEY"),
        profil("gpt-6-luna", "openai", "stable", False, "OPENAI_API_KEY"),
        profil("gpt-6-sol", "openai", "stable", False, "OPENAI_API_KEY"),
    ],
    "reglages_reponse": {
        "temperature": {"defaut": 0.0, "min": 0.0, "max": 1.0},
        "longueur_max": {"defaut": 400, "min": 50, "max": 1000},
        "mode": {"defaut": "strict", "valeurs": ["strict"]},
        "historique": {"tours_max": 10, "caracteres_max": 4000},
        "reformuler": {"defaut": True},
        "nombre_series": {"defaut": 5, "min": 1, "max": 50},
        "passages_par_serie": {"defaut": 2, "min": 1, "max": 5},
        "catalogue_seul": {"defaut": True},
    },
}
