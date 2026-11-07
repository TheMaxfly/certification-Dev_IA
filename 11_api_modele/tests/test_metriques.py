"""`/metrics` : réseau interne seulement ; requêtes, latence, erreurs, encodage."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import get_args

from conftest import entetes
from fastapi.testclient import TestClient

from api_modele import reponse
from api_modele.affirmations import Motif
from api_modele.complement import Motif as MotifComplement
from api_modele.fournisseurs import Role
from api_modele.metriques import (
    MOTIFS_REFUS,
    MOTIFS_SORTIE,
    ROLES,
    TYPES_DE_JETONS,
    autorisee,
    methode_fermee,
)
from api_modele.modeles import MotifRefus


def test_metriques_du_bouclage(api, base_api):
    api.get("/v1/sante")
    api.get("/v1/info")
    api.get("/v1/inconnue")
    r = api.get("/metrics")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    texte = r.text
    assert (
        'api_modele_requetes_total{methode="GET",route="/v1/sante",statut="200"} 1.0'
        in texte
    )
    assert (
        'api_modele_requetes_total{methode="GET",route="/v1/info",statut="401"} 1.0'
        in texte
    )
    assert 'route="inconnue",statut="404"' in texte
    assert 'api_modele_erreurs_total{code="cle_invalide"} 1.0' in texte
    assert 'api_modele_erreurs_total{code="route_inconnue"} 1.0' in texte
    assert 'api_modele_duree_requete_secondes_count{route="/v1/sante"} 1.0' in texte
    assert (
        "api_modele_encodage_en_service"
        f'{{corpus_id="v7",encodage_id="{base_api.encodage_id}"}} 1.0' in texte
    )
    # Le profil actif, publié au démarrage (exigence 2.10) ; un profil sans clé : 0.
    assert 'api_modele_profil_actif{profil="mistral-large-2512"} 1.0' in texte
    assert 'api_modele_profil_actif{profil="gpt-6-sol"} 0.0' in texte
    assert 'api_modele_profil_disponible{profil="mistral-large-4"} 1.0' in texte
    assert 'api_modele_profil_disponible{profil="gpt-6-luna"} 0.0' in texte


def test_metriques_refusees_hors_du_reseau_interne_comme_une_route_inconnue(api):
    exterieur = TestClient(api.app, client=("10.1.2.3", 50000))
    r = exterieur.get("/metrics")

    assert r.status_code == 404
    assert r.json()["code"] == "route_inconnue"
    assert exterieur.get("/v1/info", headers=entetes()).status_code == 200


def test_metriques_hors_du_contrat_public(api):
    chemins = api.get("/v1/openapi.json").json()["paths"]

    assert "/metrics" not in chemins


def test_autorisee():
    reseaux = (ipaddress.ip_network("127.0.0.1/32"), ipaddress.ip_network("::1/128"))

    assert autorisee("127.0.0.1", reseaux)
    assert autorisee("::1", reseaux)
    assert not autorisee("127.0.0.2", reseaux)
    assert not autorisee("testclient", reseaux)
    assert not autorisee(None, reseaux)


def test_la_methode_est_une_liste_fermee():
    for methode in ("GET", "POST", "HEAD", "OPTIONS"):
        assert methode_fermee(methode) == methode
    assert methode_fermee("PROPFIND") == "autre"
    assert methode_fermee("") == "autre"


def test_une_methode_quelconque_compte_sous_autre(api):
    assert api.request("PROPFIND", "/v1/sante").status_code == 405
    texte = api.get("/metrics").text
    assert 'api_modele_requetes_total{methode="autre",' in texte
    assert "PROPFIND" not in texte


def test_les_series_du_modele_de_langage_existent_des_le_demarrage(api):
    """Aucun appel, aucune réponse : chaque série est là, à 0, sans clé comprise."""
    texte = api.get("/metrics").text
    for profil in ("mistral-large-2512", "mistral-large-4", "gpt-6-luna", "gpt-6-sol"):
        assert f'api_modele_llm_cout_usd_total{{profil="{profil}"}} 0.0' in texte
        for role in ROLES:
            a = f'profil="{profil}",role="{role}"'
            for mode in ("strict", "enrichi"):
                appels = f'mode="{mode}",{a},statut="ok"'
                assert f"api_modele_llm_appels_total{{{appels}}} 0.0" in texte
            assert f"api_modele_llm_latence_secondes_count{{{a}}} 0.0" in texte
            for type_ in TYPES_DE_JETONS:
                assert f'api_modele_llm_jetons_total{{{a},type="{type_}"}} 0.0' in texte
    for motif in MOTIFS_SORTIE:
        assert f'api_modele_sortie_invalide_total{{motif="{motif}"}} 0.0' in texte
    assert 'api_modele_reponses_total{motif="aucun",refus="faux"} 0.0' in texte
    for motif in MOTIFS_REFUS:
        assert f'api_modele_reponses_total{{motif="{motif}",refus="vrai"}} 0.0' in texte


def test_les_listes_fermees_suivent_le_code():
    """Un motif ou un rôle de plus au code doit entrer dans la liste des séries."""
    assert ROLES == get_args(Role.__value__)
    assert MOTIFS_REFUS == get_args(MotifRefus)
    source = Path(reponse.__file__).read_text(encoding="utf-8")
    motifs = set(re.findall(r'sortie_invalide\(etat, "([a-z_]+)"', source))
    assert motifs | set(get_args(Motif.__value__)) | set(
        get_args(MotifComplement.__value__)
    ) == set(MOTIFS_SORTIE)
