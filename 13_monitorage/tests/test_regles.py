"""Les alertes : `promtool` ne lit pas les règles de Grafana ; ce test les lit.

Quatre règles, `uid` uniques, chaque requête nomme une métrique inventoriée, seuils
égaux aux valeurs documentées, `noDataState` fixé partout ; aucune adresse, aucun
secret.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml
from conftest import MODULE

ALERTING = MODULE / "grafana" / "provisioning" / "alerting"
ATTENDU = {
    "A — service ou API indisponible": {
        "expr": 'up{job=~"embedding|api"}',
        "evaluateur": ("lt", 1),
        "for": "1m",
        "noData": "Alerting",
    },
    "B — sorties invalides": {
        "expr": "sum(increase(api_modele_sortie_invalide_total[15m]))",
        "evaluateur": ("gt", 3),
        "for": "0s",
        "noData": "OK",
    },
    "C — coût": {
        "expr": "sum(increase(api_modele_llm_cout_usd_total[1h]))",
        "evaluateur": ("gt", 0.5),
        "for": "0s",
        "noData": "OK",
    },
    "D — équivalence": {
        "expr": "modele_sonde_equivalence_cosinus_min",
        "evaluateur": ("lt", 0.999),
        "for": "20m",
        "noData": "OK",
    },
}


def lire(nom: str) -> dict[str, Any]:
    return yaml.safe_load((ALERTING / nom).read_text(encoding="utf-8"))


def regles() -> list[dict[str, Any]]:
    (groupe,) = lire("regles.yml")["groups"]
    return groupe["rules"]


def test_un_groupe_evalue_toutes_les_30_secondes():
    (groupe,) = lire("regles.yml")["groups"]
    assert groupe["interval"] == "30s"


def test_quatre_regles_aux_uid_uniques():
    assert {r["title"] for r in regles()} == set(ATTENDU)
    uids = [r["uid"] for r in regles()]
    assert len(uids) == 4
    assert len(set(uids)) == 4


@pytest.mark.parametrize("titre", ATTENDU)
def test_la_requete_le_seuil_la_duree_sont_ceux_documentes(titre: str):
    (regle,) = [r for r in regles() if r["title"] == titre]
    attendu = ATTENDU[titre]
    donnees = {d["refId"]: d for d in regle["data"]}
    assert regle["condition"] == "C"
    assert donnees["A"]["model"]["expr"] == attendu["expr"]
    assert donnees["A"]["datasourceUid"] == "prometheus"
    evaluateur = donnees["C"]["model"]["conditions"][0]["evaluator"]
    assert (evaluateur["type"], evaluateur["params"][0]) == attendu["evaluateur"]
    assert regle["for"] == attendu["for"]
    assert regle["noDataState"] == attendu["noData"]
    assert regle["execErrState"] == "Alerting"


def test_l_alerte_du_direct_ne_filtre_pas_sur_zero():
    """`== 0` rendrait un vecteur vide en marche normale : No Data, donc Alerting."""
    (regle,) = [r for r in regles() if r["title"].startswith("A ")]
    assert "==" not in regle["data"][0]["model"]["expr"]


@pytest.mark.parametrize("titre", ATTENDU)
def test_chaque_resume_dit_quoi_faire_en_francais(titre: str):
    (regle,) = [r for r in regles() if r["title"] == titre]
    resume = regle["annotations"]["summary"]
    assert len(resume) > 40
    assert re.search(r"[Vv]érifier|Lire", resume)


def test_les_etiquettes_sont_fermees():
    for regle in regles():
        assert set(regle["labels"]) == {"severite"}
        assert regle["labels"]["severite"] in {"critique", "attention"}


def test_le_courriel_lit_sa_variable_et_ne_porte_aucune_adresse():
    points = {p["name"]: p for p in lire("canaux.yml")["contactPoints"]}
    assert set(points) == {"courriel-exploitation", "webhook-essai"}
    (courriel,) = points["courriel-exploitation"]["receivers"]
    assert courriel["type"] == "email"
    assert courriel["settings"]["addresses"] == "$MONITORAGE_COURRIEL"
    (essai,) = points["webhook-essai"]["receivers"]
    assert essai["type"] == "webhook"
    assert essai["settings"]["url"] == "http://sonde:9108/webhook-essai"


def test_le_resume_de_l_equivalence_nomme_l_etat_connu():
    (regle,) = [r for r in regles() if r["title"].startswith("D ")]
    resume = regle["annotations"]["summary"]
    assert "487295" in resume
    assert "491847" in resume
    assert "état connu" in resume
    assert "cause non établie" in resume


def test_la_politique_regroupe_30_s_et_repete_4_h():
    (politique,) = lire("politiques.yml")["policies"]
    assert politique["group_wait"] == "30s"
    assert politique["repeat_interval"] == "4h"
    assert politique["receiver"] == "courriel-exploitation"


@pytest.mark.parametrize("nom", ["regles.yml", "canaux.yml", "politiques.yml"])
def test_aucune_adresse_autre_que_la_sonde_et_aucun_secret(nom: str):
    texte = (ALERTING / nom).read_text(encoding="utf-8")
    assert set(re.findall(r"https?://[^\s\"']+", texte)) <= {
        "http://sonde:9108/webhook-essai"
    }
    assert not re.search(r"[A-Za-z0-9._-]+@[A-Za-z0-9-]+\.[a-z]{2,}", texte)
