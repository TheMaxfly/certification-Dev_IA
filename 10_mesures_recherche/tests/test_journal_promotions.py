"""Le journal des promotions : décider, revenir en arrière (E3, étape 1, bloc H).

Sur base jetable : la fixture de la comparaison (deux runs MLflow, leurs lignes
`eval_runs`, l'encodage 1 en service ; le nouveau run, sur l'encodage 2, rend
« promotion proposée »).
"""

from __future__ import annotations

import json
import uuid

import psycopg
import pytest
from test_promotion import comparaison  # noqa: F401 — la fixture des deux runs

from mesures_recherche import executer, journal_promotions, promotion
from mesures_recherche.journal_promotions import DecisionRefusee


@pytest.fixture
def cas(comparaison):  # noqa: F811
    """dsn, stockage, règle, run de référence, nouveau run, encodage 1, encodage 2."""
    return comparaison


def vue(dsn) -> int | None:
    with psycopg.connect(dsn) as cx:
        e = journal_promotions.en_service(cx)
    return e["encodage_id"] if e else None


def designe(dsn) -> int:
    """L'encodage que lirait une mesure par le sens qui n'en désigne aucun."""
    with psycopg.connect(dsn) as cx:
        (modele,) = cx.execute("SELECT DISTINCT modele FROM bench.encodages").fetchone()
        return executer.encodage_designe(cx, modele)["encodage_id"]


def promouvoir(c, **kw):
    dsn, stockage, regle, _, nouveau, _, e2 = c
    return journal_promotions.decider(
        dsn,
        decision="promu",
        encodage_id=e2,
        run_id=nouveau,
        motif="essai",
        par="Max",
        regle=regle,
        stockage=stockage,
        **kw,
    )


def test_promouvoir_inscrit_une_ligne_et_la_vue_suit(cas):
    dsn, _, _, _, nouveau, e1, e2 = cas
    assert vue(dsn) == e1 and designe(dsn) == e1
    r = promouvoir(cas)
    assert r["verdict"] == "promotion proposée"
    ligne = r["ligne"]
    cle = ("decision", "encodage_id", "encodage_precedent_id")
    assert tuple(ligne[k] for k in cle) == ("promu", e2, e1)
    assert str(ligne["run_id"]) == str(uuid.UUID(nouveau))
    assert (ligne["decide_par"], ligne["derogation"]) == ("Max", False)
    assert r["avant"]["encodage_id"] == e1 and r["apres"]["encodage_id"] == e2
    assert vue(dsn) == e2 and designe(dsn) == e2, "la recherche suit la vue"


def test_apres_la_promotion_la_comparaison_ne_rend_plus_de_verdict(cas):
    """Le garde-fou de Max, par la vraie commande : l'encodage promu est en service,
    on ne le compare plus à lui-même."""
    dsn, stockage, regle, _, nouveau, _, _ = cas
    promouvoir(cas)
    with pytest.raises(promotion.VerdictRefuse, match="celui en service"):
        promotion.comparer(dsn, nouveau, regle, stockage)
    with pytest.raises(promotion.VerdictRefuse, match="celui en service"):
        promouvoir(cas)


def test_revenir_remet_le_precedent_et_la_recherche_suit(cas):
    dsn, *_, e1, e2 = cas
    promouvoir(cas)
    r = journal_promotions.revenir(dsn, motif="retour d'essai", par="Max")
    ligne = r["ligne"]
    cle = ("decision", "encodage_id", "encodage_precedent_id")
    assert tuple(ligne[k] for k in cle) == ("retour_arriere", e1, e2)
    assert ligne["run_id"] is None and ligne["derogation"] is False
    assert vue(dsn) == e1 and designe(dsn) == e1
    with psycopg.connect(dsn) as cx:
        assert cx.execute("SELECT count(*) FROM bench.promotions").fetchone()[0] == 3


def test_revenir_sans_precedent_est_refuse(cas):
    dsn, *_ = cas
    with pytest.raises(DecisionRefusee, match="pas de précédent"):
        journal_promotions.revenir(dsn, motif="essai", par="Max")


def test_revenir_vers_un_encodage_jamais_servi_est_refuse_par_la_base(cas):
    dsn, *_, e2 = cas
    with pytest.raises(psycopg.errors.RaiseException, match="jamais été en service"):
        journal_promotions.revenir(dsn, motif="essai", par="Max", vers=e2)
    assert vue(dsn) != e2


def test_une_decision_contraire_a_la_regle_exige_la_derogation(cas):
    dsn, stockage, regle, _, nouveau, e1, e2 = cas
    refus = {
        "decision": "refuse",
        "encodage_id": e2,
        "run_id": nouveau,
        "motif": "essai",
        "par": "Max",
        "regle": regle,
        "stockage": stockage,
    }
    with pytest.raises(DecisionRefusee, match="dérogation"):
        journal_promotions.decider(dsn, **refus)
    r = journal_promotions.decider(dsn, **refus, derogation=True)
    assert (r["ligne"]["decision"], r["ligne"]["derogation"]) == ("refuse", True)
    assert vue(dsn) == e1, "un refus laisse l'encodage en service"


def test_une_decision_conforme_ne_porte_pas_de_derogation(cas):
    dsn, *_, e1, _ = cas
    with pytest.raises(DecisionRefusee, match="pas une dérogation"):
        promouvoir(cas, derogation=True)
    assert vue(dsn) == e1


def test_le_run_doit_etre_celui_de_l_encodage_decide(cas):
    dsn, stockage, regle, _, nouveau, e1, _ = cas
    with pytest.raises(DecisionRefusee, match="a mesuré l'encodage"):
        journal_promotions.decider(
            dsn,
            decision="promu",
            encodage_id=e1,
            run_id=nouveau,
            motif="essai",
            par="Max",
            regle=regle,
            stockage=stockage,
        )
    with psycopg.connect(dsn) as cx:
        assert cx.execute("SELECT count(*) FROM bench.promotions").fetchone()[0] == 1


def test_la_commande_revenir_et_l_etat(cas, monkeypatch, capsys):
    dsn, *_, e1, e2 = cas
    promouvoir(cas)
    monkeypatch.setenv("DATABASE_URL", dsn)
    assert journal_promotions.main(["revenir", "--motif", "essai", "--par", "Max"]) == 0
    assert json.loads(capsys.readouterr().out)["apres"]["encodage_id"] == e1
    assert journal_promotions.main(["etat"]) == 0
    e = json.loads(capsys.readouterr().out)
    assert e["en_service"]["encodage_id"] == e1
    assert [j["decision"] for j in e["journal"]] == ["promu", "promu", "retour_arriere"]
