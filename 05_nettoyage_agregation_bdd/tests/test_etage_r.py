"""Étage R, jalon R-a — dossiers, étalonnage, échantillon, juge. ZÉRO RÉSEAU.

Aucun test de ce fichier ne contacte l'API Anthropic. Le client juge est
vérifié sur ses parties pures (forme des requêtes, identifiants, garde-fou de
clé) ; la partie qui appelle le réseau appartient au jalon R-b.

`apimanga` n'est jamais atteignable : les tests base tournent sur PostgreSQL
jetable.
"""

from __future__ import annotations

import json

import psycopg
import pytest
from conftest import lire

from identity import etage_r_dossiers as rd
from identity import etage_r_juge as rj

# --------------------------------------------------------------------------- #
#  Le garde-fou de la clé — le test qui protège d'un secret qui fuit
# --------------------------------------------------------------------------- #


def test_l_absence_de_cle_est_une_erreur_explicite(monkeypatch):
    """Un run mal configuré doit échouer en une seconde, pas après vingt
    minutes d'assemblage."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    with pytest.raises(rj.ErreurJuge, match="Aucune clé d'API"):
        rj.cle_api()


def test_le_message_d_erreur_ne_contient_aucune_cle(monkeypatch):
    """MUTATION : un message d'erreur qui échoterait la valeur lue la
    ferait fuiter dans les journaux."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SECRET-NE-DOIT-PAS-FUIR")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    assert rj.cle_api() == "sk-ant-SECRET-NE-DOIT-PAS-FUIR"

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    try:
        rj.cle_api()
    except rj.ErreurJuge as erreur:
        assert "SECRET" not in str(erreur)
        assert "sk-ant" not in str(erreur)


def test_le_jeton_alternatif_est_accepte(monkeypatch):
    """Un profil OAuth expose ANTHROPIC_AUTH_TOKEN, pas ANTHROPIC_API_KEY."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "oauth-token")

    assert rj.cle_api() == "oauth-token"


# --------------------------------------------------------------------------- #
#  Le contrat de sortie
# --------------------------------------------------------------------------- #


def test_le_schema_contraint_les_trois_verdicts():
    """Le schéma EST le contrat : pas de validation-relance côté client."""
    verdicts = rj.SCHEMA_VERDICT["properties"]["verdict"]["enum"]
    assert verdicts == ["same_work", "different_work", "undecidable"]
    assert rj.SCHEMA_VERDICT["properties"]["confiance"]["enum"] == [
        "haute",
        "moyenne",
    ]
    assert rj.SCHEMA_VERDICT["additionalProperties"] is False
    assert set(rj.SCHEMA_VERDICT["required"]) == {
        "verdict",
        "confiance",
        "justification",
    }


def test_la_requete_ne_porte_aucun_parametre_d_echantillonnage():
    """MUTATION : `temperature` renvoie une 400 sur un modèle à sorties
    structurées. Sa présence casserait tout le lot."""
    requete = rj.construire_requete("file|1|qid|Q1", "dossier")

    for interdit in ("temperature", "top_p", "top_k"):
        assert interdit not in requete["params"], (
            f"{interdit} est refusé par {rj.MODELE} — la stabilité vient du "
            "couple (modele, prompt_version), pas d'une température"
        )


def test_la_requete_porte_le_schema_et_le_prompt_systeme():
    requete = rj.construire_requete("file|1|qid|Q1", "le dossier")

    assert requete["custom_id"] == "file|1|qid|Q1"
    assert requete["params"]["model"] == rj.MODELE
    assert requete["params"]["output_config"]["format"]["schema"] is rj.SCHEMA_VERDICT
    assert requete["params"]["system"] == rj.PROMPT_SYSTEME
    assert requete["params"]["messages"][0]["content"] == "le dossier"


def test_l_identifiant_fait_l_aller_retour():
    """Les résultats d'un lot Batch arrivent DANS UN ORDRE QUELCONQUE : le
    rattachement se fait par cette clé, jamais par la position."""
    cle = rj.identifiant("etalonnage", 4242, "kitsu_id", "999")

    assert rj.relire_identifiant(cle) == {
        "phase": "etalonnage",
        "series_id": 4242,
        "candidat_type": "kitsu_id",
        "candidat_id": "999",
    }


def test_l_identifiant_survit_a_un_qid_contenant_un_separateur():
    """MUTATION : un split sans borne casserait sur un id exotique."""
    cle = rj.identifiant("file", 7, "qid", "Q1|bizarre")

    assert rj.relire_identifiant(cle)["candidat_id"] == "Q1|bizarre"


# --------------------------------------------------------------------------- #
#  Fabrication de l'étalonnage — en pur Python
# --------------------------------------------------------------------------- #


class CurseurFactice:
    def __init__(self, lignes):
        self._lignes = lignes

    def execute(self, *_a, **_k):
        return self

    def fetchall(self):
        return self._lignes


def _identites(n: int, annee_depart: int = 1990):
    return [
        (i, f"Serie {i}", f"Auteur {i}", annee_depart + (i % 30), f"Q{i}", str(i))
        for i in range(1, n + 1)
    ]


def test_l_etalonnage_est_moitie_vrai_moitie_faux():
    cas = rd.fabriquer_etalonnage(CurseurFactice(_identites(400)), 60, graine=1)

    assert len(cas) == 60
    assert sum(1 for c in cas if c["attendu"] == "same_work") == 30
    assert sum(1 for c in cas if c["attendu"] == "different_work") == 30


def test_les_faux_sont_des_leurres_pas_des_paires_au_hasard():
    """Le cœur méthodologique : un faux tiré au hasard est trivial à rejeter,
    et un juge qui y arrive n'a rien prouvé. Les leurres viennent de la même
    décennie — le juge doit distinguer deux œuvres confondables."""
    cas = rd.fabriquer_etalonnage(CurseurFactice(_identites(400)), 60, graine=1)

    faux = [c for c in cas if c["attendu"] == "different_work"]
    assert all(c["fabrication"].startswith("leurre_") for c in faux)
    assert all(c["leurre_de"] != c["series_id"] for c in faux), (
        "un leurre pris sur la série elle-même serait un VRAI étiqueté faux"
    )
    meme_decennie = sum(1 for c in faux if c["fabrication"] == "leurre_meme_decennie")
    assert meme_decennie >= len(faux) * 0.8


def test_l_etalonnage_est_reproductible_a_graine_egale():
    """Sans reproductibilité, un étalonnage raté serait inanalysable."""
    a = rd.fabriquer_etalonnage(CurseurFactice(_identites(400)), 60, graine=7)
    b = rd.fabriquer_etalonnage(CurseurFactice(_identites(400)), 60, graine=7)
    c = rd.fabriquer_etalonnage(CurseurFactice(_identites(400)), 60, graine=8)

    assert [x["series_id"] for x in a] == [x["series_id"] for x in b]
    assert [x["series_id"] for x in a] != [x["series_id"] for x in c]


def test_l_etalonnage_refuse_un_vivier_trop_maigre():
    """MUTATION : sans ce garde-fou on fabriquerait 12 cas au lieu de 60 et on
    mesurerait le juge sur presque rien."""
    with pytest.raises(rd.ErreurEtageR, match="identités sûres"):
        rd.fabriquer_etalonnage(CurseurFactice(_identites(10)), 60, graine=1)


# --------------------------------------------------------------------------- #
#  Mise en forme du dossier
# --------------------------------------------------------------------------- #


def _dossier(**surcharges):
    base = {
        "series_id": 1,
        "origine": "etage1",
        "method": "exact",
        "score": 0.9,
        "cas": "review_sans_signal",
        "ms": {
            "titre": "Berserk",
            "formes": "Berserk | ベルセルク",
            "auteurs": "Kentaro Miura",
            "annee": 1989,
            "synopsis": "Un guerrier...",
        },
        "candidats": [
            {
                "type": "qid",
                "id": "Q1",
                "label": "Berserk",
                "annee": 1989,
                "contexte": "Berserk",
                "formes": "Berserk",
                "auteurs": "Kentaro Miura",
                "signal_auteur": "concordant",
                "ecart_annee": 0,
                "confirme_par_kitsu": True,
            }
        ],
        "dossier_partiel": False,
        "pre_validation_bandes": False,
    }
    base.update(surcharges)
    return base


def test_le_dossier_porte_les_signaux_calcules():
    texte = rd.texte_dossier(_dossier())

    assert "Berserk" in texte
    assert "Kentaro Miura" in texte
    assert "signal auteur (calculé) : concordant" in texte
    assert "écart d'année (calculé) : 0" in texte
    assert "second référentiel (Kitsu) désigne indépendamment" in texte


def test_un_dossier_partiel_le_dit_au_juge():
    """Le drapeau doit ARRIVER au juge, pas rester en base : un avis rendu sur
    un dossier incomplet ne doit pas porter une confiance haute."""
    texte = rd.texte_dossier(_dossier(dossier_partiel=True))

    assert "DOSSIER PARTIEL" in texte
    assert "confiance haute" in texte


def test_un_dossier_sans_candidat_oriente_vers_undecidable():
    texte = rd.texte_dossier(_dossier(candidats=[]))

    assert "aucun candidat" in texte
    assert "undecidable" in texte


def test_la_volumetrie_mesure_le_texte_reellement_envoye():
    """MUTATION : mesurer autre chose que ce qui part donnerait un budget faux."""
    dossiers = [_dossier(series_id=i) for i in range(1, 11)]

    v = rd.volumetrie(dossiers)

    assert v["dossiers"] == 10
    assert v["caracteres_total"] == sum(len(rd.texte_dossier(d)) for d in dossiers)
    assert v["caracteres_median"] > 0


# --------------------------------------------------------------------------- #
#  Base jetable — stratification et périmètre
# --------------------------------------------------------------------------- #


def semer_auto(cur, series_id: int, method: str, score: float, cas: str | None):
    cur.execute(
        "INSERT INTO manga.ms_series_enriched (series_id, series_title, series_year) "
        "VALUES (%s, %s, 2000)",
        (series_id, f"Serie {series_id}"),
    )
    cur.execute(
        "INSERT INTO manga.work_identity (series_id, wikidata_qid) VALUES (%s, %s)",
        (series_id, f"Q{series_id}"),
    )
    cur.execute(
        "INSERT INTO manga.match_decision "
        "(series_id, method, status, score, details) VALUES (%s,%s,'auto',%s,%s)",
        (
            series_id,
            method,
            score,
            json.dumps({"case": cas}) if cas else None,
        ),
    )


def test_l_echantillon_respecte_exactement_les_strates(base):
    with psycopg.connect(base) as connexion:
        with connexion.cursor() as cur:
            n = 1
            for _ in range(60):
                semer_auto(cur, n, "exact_kitsu", 0.96, "auto_k_historique_confirme")
                n += 1
            for _ in range(60):
                semer_auto(cur, n, "exact_kitsu_author", 0.93, "autre")
                n += 1
            for _ in range(60):
                semer_auto(cur, n, "kitsu_bridge", 1.0, None)
                n += 1
            for _ in range(60):
                semer_auto(cur, n, "exact_author", 0.97, "autre")
                n += 1
            connexion.commit()

        with connexion.cursor() as cur:
            tirage = rd.echantillonner_c3(cur, rd.STRATES, graine=1)

    par_strate: dict[str, int] = {}
    for e in tirage:
        par_strate[e["strate"]] = par_strate.get(e["strate"], 0) + 1

    assert par_strate == rd.STRATES
    assert len({e["series_id"] for e in tirage}) == 100, "aucun doublon entre strates"


def test_l_echantillon_refuse_une_strate_insuffisante(base):
    """MUTATION : sans ce STOP, l'échantillon C3 serait silencieusement
    déséquilibré et sa mesure de précision invalide."""
    with psycopg.connect(base) as connexion:
        with connexion.cursor() as cur:
            semer_auto(cur, 1, "kitsu_bridge", 1.0, None)
        connexion.commit()

        with connexion.cursor() as cur:
            with pytest.raises(rd.ErreurEtageR, match="Strates insuffisantes"):
                rd.echantillonner_c3(cur, rd.STRATES, graine=1)


def test_une_file_inattendue_arrete_l_assemblage(base):
    """Étape 0 : un écart inexpliqué arrête, il ne s'accommode pas."""
    with psycopg.connect(base) as connexion, connexion.cursor() as cur:
        with pytest.raises(rd.ErreurEtageR, match="écart"):
            rd.construire_dossiers(cur, file_attendue=1932)


def test_l_assemblage_n_ecrit_rien(base):
    """Le régime avis-seulement commence à la préparation."""
    with psycopg.connect(base) as connexion:
        with connexion.cursor() as cur:
            semer_auto(cur, 1, "exact", 0.9, None)
        connexion.commit()

    avant = lire(base, "SELECT count(*) FROM manga.llm_avis")[0][0]

    with psycopg.connect(base) as connexion, connexion.cursor() as cur:
        with pytest.raises(rd.ErreurEtageR):
            rd.construire_dossiers(cur, file_attendue=1932)

    assert lire(base, "SELECT count(*) FROM manga.llm_avis")[0][0] == avant
    assert lire(base, "SELECT count(*) FROM manga.match_decision")[0][0] == 1
