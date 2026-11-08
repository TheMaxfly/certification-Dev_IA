"""La configuration : refus de démarrer, aucune valeur de clé, textes complets."""

from __future__ import annotations

import ipaddress
import json
import re
import tomllib
from pathlib import Path

import doublure_api as d
import pytest

from application import journal
from application.__main__ import principal
from application.config import (
    FICHIER_REGLAGES,
    TEXTES,
    ConfigurationInvalide,
    charger,
    lire_ecoute,
    variables_de,
)

ENV = {"APPLICATION_CLE_API": d.CLE_ATTENDUE}


def ecarts_de(environ: dict[str, str], fichier: Path = FICHIER_REGLAGES) -> list[str]:
    with pytest.raises(ConfigurationInvalide) as exc:
        charger(environ, fichier)
    return exc.value.ecarts


@pytest.mark.parametrize("environ", [{}, {"APPLICATION_CLE_API": "   "}])
def test_sans_cle_l_application_refuse_de_demarrer(environ):
    ecarts = ecarts_de(environ)

    assert len(ecarts) == 1 and ecarts[0].startswith("APPLICATION_CLE_API :")


def test_une_cle_hors_ascii_empeche_de_demarrer_sans_la_montrer():
    ecarts = ecarts_de({"APPLICATION_CLE_API": "clé-factice-accentuée"})

    assert ecarts == ["APPLICATION_CLE_API : caractères hors ASCII — clé illisible"]


def test_la_cle_des_comptes_est_facultative_et_jamais_montree():
    assert charger(ENV).cle_api_comptes is None
    avec = charger({**ENV, "APPLICATION_CLE_API_COMPTES": "k" * 40})
    assert avec.cle_api_comptes == "k" * 40
    assert "k" * 40 not in repr(avec)
    ecarts = ecarts_de({**ENV, "APPLICATION_CLE_API_COMPTES": "clé-é" * 8})
    assert ecarts == [
        "APPLICATION_CLE_API_COMPTES : caractères hors ASCII — clé illisible"
    ]


def test_les_bornes_du_conseiller_sont_celles_decidees():
    bornes = charger(ENV).bornes
    assert (
        bornes.quota_session_questions,
        bornes.quota_session_fenetre_min,
        bornes.quota_client_questions_jour,
        bornes.debit_public_par_minute,
        bornes.cout_jour_max_usd,
        bornes.inscriptions_session_max,
        bornes.inscriptions_fenetre_min,
    ) == (10, 60, 10, 10, 0.50, 3, 60)


def test_le_lancement_sans_cle_nomme_la_variable_et_rend_2(monkeypatch, capsys):
    monkeypatch.setattr("application.__main__.journal.configurer", lambda: None)
    monkeypatch.delenv("APPLICATION_CLE_API", raising=False)

    assert principal() == 2
    assert "APPLICATION_CLE_API" in capsys.readouterr().err


def test_le_lancement_avec_cle_sert_sur_l_ecoute_configuree(monkeypatch):
    vu: dict[str, object] = {}
    monkeypatch.setattr("application.__main__.journal.configurer", lambda: None)
    monkeypatch.setenv("APPLICATION_CLE_API", d.CLE_ATTENDUE)
    monkeypatch.setattr(
        "application.__main__.uvicorn.run", lambda _app, **kw: vu.update(kw)
    )

    assert principal() == 0
    assert (vu["host"], vu["port"]) == ("127.0.0.1", 8020)


def test_le_journal_est_du_json_avec_ses_champs(capsys):
    journal_racine = journal.JOURNAL
    sorties, niveau, propage = (
        journal_racine.handlers[:],
        journal_racine.level,
        journal_racine.propagate,
    )
    try:
        journal.configurer()
        journal.evenement("essai", {"nombre": 3})
        try:
            raise ValueError("détail")
        except ValueError:
            journal_racine.exception("rate", extra={"champs": {}})
    finally:
        journal_racine.handlers = sorties
        journal_racine.setLevel(niveau)
        journal_racine.propagate = propage

    lignes = [json.loads(x) for x in capsys.readouterr().err.splitlines()]
    assert lignes[0]["evenement"] == "essai" and lignes[0]["nombre"] == 3
    assert lignes[0]["niveau"] == "INFO" and lignes[0]["horodatage"]
    assert "ValueError" in lignes[1]["trace"]


def test_le_repr_ne_montre_pas_la_cle():
    reglages = charger(ENV)

    assert d.CLE_ATTENDUE not in repr(reglages)
    assert reglages.cle_api == d.CLE_ATTENDUE


def test_les_valeurs_par_defaut_sont_celles_decidees():
    r = charger(ENV)

    assert r.adresse_api == "http://127.0.0.1:8010"
    assert (r.hote, r.port) == ("127.0.0.1", 8020)
    assert r.identite == "libraire-demo"
    assert r.profils_exclus == {"mistral-large-4"}
    assert [(p.valeur, p.nom) for p in r.positions] == [
        (0.0, "stable"),
        (0.3, "modérée"),
        (0.7, "variée"),
    ]
    assert r.temperature_defaut == 0.0 and r.reformuler_defaut is True
    assert r.delais.reponse_s == 75.0 and r.delais.connexion_s == 5.0
    assert r.limites.echanges == 5 and r.corps_octets_max == 16384
    assert r.textes["nom_librairie"] == "Librairie de démonstration"
    assert r.fournisseurs["mistral"].hebergement == "Union européenne"


def test_les_surcharges_viennent_de_l_environnement():
    r = charger(
        {
            **ENV,
            "API_MODELE_ADRESSE": "http://api:8010",
            "APPLICATION_ECOUTE": "0.0.0.0:9000",
            "APPLICATION_METRIQUES_RESEAUX": "172.30.0.0/24, 127.0.0.1/32",
        }
    )

    assert r.adresse_api == "http://api:8010"
    assert (r.hote, r.port) == ("0.0.0.0", 9000)
    assert ipaddress.ip_network("172.30.0.0/24") in r.metriques_reseaux


@pytest.mark.parametrize(
    ("variable", "valeur", "attendu"),
    [
        ("API_MODELE_ADRESSE", "ftp://api", "adresse de l'API"),
        ("APPLICATION_ECOUTE", "sansport", "APPLICATION_ECOUTE"),
        ("APPLICATION_ECOUTE", "hote:99999", "APPLICATION_ECOUTE"),
        ("APPLICATION_METRIQUES_RESEAUX", "pas-un-reseau", "réseau invalide"),
    ],
)
def test_une_surcharge_fautive_empeche_de_demarrer_sans_dire_sa_valeur(
    variable, valeur, attendu
):
    ecarts = ecarts_de({**ENV, variable: valeur})

    assert any(attendu in e for e in ecarts)
    assert not any(valeur in e for e in ecarts)


def test_lire_ecoute_accepte_une_adresse_valide():
    assert lire_ecoute("127.0.0.1:8020", []) == ("127.0.0.1", 8020)


def fichier_modifie(tmp_path: Path, avant: str, apres: str) -> Path:
    texte = FICHIER_REGLAGES.read_text(encoding="utf-8")
    assert avant in texte, avant
    chemin = tmp_path / "reglages.toml"
    chemin.write_text(texte.replace(avant, apres, 1), encoding="utf-8")
    return chemin


@pytest.mark.parametrize(
    ("avant", "apres", "attendu"),
    [
        ('adresse = "http://127.0.0.1:8010"\n', "", "api.adresse"),
        ("reponse_s = 75.0", "reponse_s = 0", "api.delais.reponse_s"),
        ("reponse_s = 75.0", 'reponse_s = "75"', "api.delais.reponse_s"),
        ("port = 8020", "port = -1", "ecoute.port"),
        ("reformuler_defaut = true", 'reformuler_defaut = "oui"', "reformuler_defaut"),
        (
            "temperature_defaut = 0.0",
            "temperature_defaut = 0.9",
            "n'est pas une position",
        ),
        (
            'profils_exclus = ["mistral-large-4"]',
            'profils_exclus = "x"',
            "profils_exclus",
        ),
        (
            'profils_exclus = ["mistral-large-4"]',
            "profils_exclus = [1]",
            "profils_exclus",
        ),
        ('reseaux = ["127.0.0.1/32", "::1/128"]', 'reseaux = ["x"]', "réseau invalide"),
        ('bouton = "Demander"\n', "", "texte absent : bouton"),
        ('bouton = "Demander"', 'bouton = "Demander"\ninconnu = "x"', "texte inconnu"),
        ('bouton = "Demander"', "bouton = 3", "bouton : texte attendu"),
        ('bouton = "Demander"', 'bouton = "{variable}"', "variable inconnue"),
        ('bouton = "Demander"', 'bouton = "{"', "accolades invalides"),
        # E4, lot 2b : les bornes du conseiller, positives.
        (
            "quota_session_questions = 10",
            "quota_session_questions = 0",
            "conseiller.limites.quota_session_questions",
        ),
        (
            "quota_client_questions_jour = 10",
            "quota_client_questions_jour = 2.5",
            "conseiller.limites.quota_client_questions_jour",
        ),
        (
            "cout_jour_max_usd = 0.50",
            "cout_jour_max_usd = 0",
            "conseiller.limites.cout_jour_max_usd",
        ),
        ("[conseiller.limites]", "[conseiller.autres]", "conseiller.limites"),
    ],
)
def test_un_fichier_fautif_empeche_de_demarrer(tmp_path, avant, apres, attendu):
    ecarts = ecarts_de(ENV, fichier_modifie(tmp_path, avant, apres))

    assert any(attendu in e for e in ecarts), ecarts


def test_une_section_ou_une_position_absente_empeche_de_demarrer(tmp_path):
    ecarts = ecarts_de(
        ENV,
        fichier_modifie(
            tmp_path,
            "[[reglages.positions]]\nvaleur = 0.0",
            "[[reglages.positions]]\nvaleur = true",
        ),
    )
    assert any("reglages.positions.valeur" in e for e in ecarts)

    texte = FICHIER_REGLAGES.read_text(encoding="utf-8")
    chemin = tmp_path / "sans.toml"
    chemin.write_text(
        'corps = "x"\n' + texte.split("[corps]")[1].split("\n", 2)[2], encoding="utf-8"
    )
    assert ecarts_de(ENV, chemin)


def test_chaque_texte_du_fichier_est_declare_et_inversement():
    donnees = tomllib.loads(FICHIER_REGLAGES.read_text(encoding="utf-8"))

    assert set(donnees["textes"]) == set(TEXTES)
    for cle, texte in donnees["textes"].items():
        assert variables_de(texte) <= TEXTES[cle], cle


def test_la_mention_ia_est_le_texte_decide():
    textes = charger(ENV).textes

    assert textes["mention_courte"] == "Vous parlez à une IA."
    assert textes["mention_complete"] == (
        "Vous parlez à une IA. Les réponses sont rédigées par un modèle de langage à"
        " partir de critiques et de résumés du catalogue ; elles peuvent être"
        " incomplètes."
    )


def test_les_limites_configurees_egalent_le_contrat():
    """Le 2 000 d'un tour n'est pas dans `/v1/info` : il se compare au contrat ici."""
    schemas = d.CONTRAT["components"]["schemas"]
    limites = charger(ENV).limites

    assert schemas["Tour"]["properties"]["contenu"]["maxLength"] == (
        limites.tour_caracteres_max
    )
    assert schemas["DemandeReponse"]["properties"]["question"]["maxLength"] == (
        limites.question_caracteres_max
    )
    assert d.CONTRAT["components"]["schemas"]["DemandeReponse"]["properties"][
        "historique"
    ]["description"].startswith(
        f"Les tours précédents, dans l'ordre : au plus {limites.tours_max} tours et"
        f" {limites.caracteres_max} caractères"
    )


def test_le_fichier_d_exemple_ne_porte_que_des_noms():
    texte = (FICHIER_REGLAGES.parents[1] / ".env.example").read_text(encoding="utf-8")

    affectations = re.findall(r"^(\w+)=(.*)$", texte, re.M)
    assert {nom for nom, _ in affectations} == {
        "APPLICATION_CLE_API",
        "API_MODELE_ADRESSE",
        "APPLICATION_ECOUTE",
        "APPLICATION_METRIQUES_RESEAUX",
        # E4, lot 1 : l'environnement, la base, la clé des sessions.
        "APPLICATION_ENVIRONNEMENT",
        "APPLICATION_BASE_DSN",
        "APPLICATION_CLE_SESSION",
        # E4, lot 2a : le catalogue (l'API data du 02).
        "APPLICATION_CLE_CATALOGUE",
        "CATALOGUE_ADRESSE",
    }
    assert all(valeur == "" for _, valeur in affectations)


def test_aucune_valeur_de_cle_dans_les_reglages():
    texte = FICHIER_REGLAGES.read_text(encoding="utf-8")

    assert "APPLICATION_CLE_API" not in texte
    assert d.CLE_ATTENDUE not in texte
    assert json.dumps(sorted(charger(ENV).fournisseurs)) == '["mistral", "openai"]'
