"""Le banc de la réponse en mode enrichi (E3 étape 6, spec 06, E) : sans appel réel.

Le mode passe à la route, l'estimation compte les questions sans passage (question
33), le complément est gardé pour la lecture de Max et compté (présence, refus,
titres, sorties invalides, titres du catalogue nommés sans être déclarés) ; le run
part dans l'expérience demandée, étiqueté `mode`. Tout ce qui parle à un fournisseur
passe par les doublures du bouclage.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import psycopg
import pytest
from conftest import (
    CLE_APPLICATION,
    QUESTION,
    BaseApi,
    DoublureFournisseur,
    Doublures,
)
from fastapi.testclient import TestClient

from api_modele import recherche
from api_modele import reponse as route
from api_modele.banc_reponse import __main__ as cli
from api_modele.banc_reponse import enregistrement, jeu
from api_modele.banc_reponse.espion import Espion
from api_modele.banc_reponse.mesure import (
    Banc,
    Run,
    Tour,
    corps_de_requete,
    essai_appel,
    estimer,
    estimer_scenarios,
    mesurer,
    metriques,
    verdict,
)
from api_modele.fournisseurs import cout_usd
from api_modele.modeles import Serie
from api_modele.recherche import Resultat
from api_modele.reponse import INVITE_REPONSE, invite_de, message_de_reponse

COURT = "famille:F7,issue:hors_catalogue"
RUN_ID = "0123456789abcdef0123456789abcdef"
EXPERIENCE = "E3-reponse-enrichi-preprod"
TITRE_DU_CATALOGUE = "Le Titre du Catalogue !"
COMPLEMENT = "Le titre du catalogue plaira aussi, comme Titre inventé."


def enrichie(**champs: object) -> str:
    """Une sortie du mode enrichi : une réponse fondée, et un complément qui nomme un
    titre déclaré (hors catalogue) et un titre du catalogue non déclaré."""
    base: dict[str, object] = {
        "refus": False,
        "motif_refus": None,
        "reponse": "Une aventure de pirates [S:1].",
        "series_citees": [{"serie_id": 1, "fragment_ids": [702]}],
        "complement": COMPLEMENT,
        "complement_titres": ["Titre inventé"],
    }
    return json.dumps(base | champs)


def refus_enrichi(**champs: object) -> str:
    return enrichie(
        refus=True,
        motif_refus="inconnu",
        reponse="Les passages ne parlent pas de cette série.",
        series_citees=[],
        **champs,
    )


def ajouter_au_catalogue(dsn_admin: str, serie_id: int, titre: str) -> None:
    with psycopg.connect(dsn_admin) as cx:
        cx.execute(
            "INSERT INTO manga.ms_series_enriched (series_id, series_title)"
            " VALUES (%s, %s)",
            (serie_id, titre),
        )


@pytest.fixture
def banc_(api_reponse: TestClient, base_api: BaseApi) -> Iterator[Banc]:
    """Le banc contre l'API et sa base jetable ; le catalogue lu comme `ouvrir` le
    lit, par la connexion de l'API."""
    ajouter_au_catalogue(base_api.dsn_admin, 3, TITRE_DU_CATALOGUE)
    etat = api_reponse.app.state.etat
    espion = Espion()
    with espion.ecouter():
        yield Banc(
            api_reponse,
            CLE_APPLICATION,
            etat.reglages.modele_langage,
            etat.pseudonymes,
            espion,
            pause_s=0.0,
            dormir=lambda _: None,
            catalogue=lambda: cli.lire_le_catalogue(etat),
        )


@pytest.fixture
def sans_passage(monkeypatch: pytest.MonkeyPatch) -> None:
    vraie = recherche.rechercher

    def vide(*args: Any) -> Resultat:
        return vraie(*args).model_copy(update={"series": []})

    monkeypatch.setattr(recherche, "rechercher", vide)


def requetes_de_reponse(doublures: Doublures) -> list[dict[str, Any]]:
    return [
        r["corps"] for r in doublures.recus() if r["chemin"] == "/v1/chat/completions"
    ]


@contextmanager
def ouvreur_de(banc_: Banc) -> Iterator[Banc]:
    yield banc_


# --------------------------------------------------------------------------- #
#  La requête et l'estimation
# --------------------------------------------------------------------------- #
def test_en_strict_la_requete_du_banc_reste_celle_d_avant(banc_: Banc):
    p = banc_.profil("factice-a")
    assert corps_de_requete(QUESTION, p, "strict") == {
        "question": QUESTION,
        "profil": "factice-a",
    }
    assert corps_de_requete(QUESTION, p, "enrichi")["mode"] == "enrichi"


def test_l_estimation_enrichie_compte_l_invite_et_le_bloc(banc_: Banc):
    p = banc_.profil("factice-a")
    r = banc_.api.post(
        "/v1/recherche",
        json={"question": QUESTION, "nombre_series": 5, "passages_par_serie": 2},
        headers=banc_.entetes,
    )
    series = [Serie.model_validate(s) for s in r.json()["series"]]
    caracteres = len(invite_de("enrichi")) + len(
        message_de_reponse("", QUESTION, series)
    )
    entree = math.ceil(caracteres / banc_.modele_langage.caracteres_par_jeton)
    e = estimer(banc_, p, [QUESTION], mode="enrichi")
    assert (e.appels, e.sans_passage, e.jetons_entree) == (1, 0, entree)
    assert e.cout_usd == pytest.approx(cout_usd(p, entree, 400, 0))
    assert len(invite_de("enrichi")) > len(INVITE_REPONSE)
    assert e.cout_usd > estimer(banc_, p, [QUESTION]).cout_usd


def test_sans_passage_l_estimation_enrichie_compte_un_appel(
    banc_: Banc, sans_passage: None
):
    p = banc_.profil("factice-a")
    strict = estimer(banc_, p, [QUESTION, QUESTION])
    assert (strict.appels, strict.sans_passage, strict.cout_usd) == (0, 2, 0.0)
    caracteres = len(invite_de("enrichi")) + len(message_de_reponse("", QUESTION, []))
    entree = math.ceil(caracteres / banc_.modele_langage.caracteres_par_jeton)
    e = estimer(banc_, p, [QUESTION, QUESTION], mode="enrichi")
    assert (e.questions, e.appels, e.sans_passage) == (2, 2, 2)
    assert e.cout_usd == pytest.approx(2 * cout_usd(p, entree, 400, 0))
    assert e.en_dict()["sans_passage"] == 2


def test_l_estimation_des_scenarios_dit_ses_tours_sans_passage(
    banc_: Banc, sans_passage: None
):
    tours = [
        Tour("S1", 1, QUESTION, QUESTION, None),
        Tour("S1", 2, "et ?", QUESTION, None),
    ]
    assert estimer_scenarios(banc_, banc_.profil("factice-a"), tours).sans_passage == 2


# --------------------------------------------------------------------------- #
#  L'essai, la mesure, les métriques
# --------------------------------------------------------------------------- #
def test_l_essai_enrichi_rend_le_complement_et_ses_chiffres(
    banc_: Banc, doublures: Doublures
):
    DoublureFournisseur.contenu = enrichie()
    chiffres, appel = essai_appel(
        banc_, banc_.profil("factice-a"), QUESTION, mode="enrichi"
    )
    assert (chiffres["statut"], chiffres["mode"]) == (200, "enrichi")
    assert appel.complement == COMPLEMENT
    assert chiffres["complement_present"] is True
    assert chiffres["complement_titres"] == 1
    assert chiffres["complement_titres_non_declares"] == [TITRE_DU_CATALOGUE]
    # Des nombres et des titres du catalogue, jamais le texte du complément.
    assert COMPLEMENT not in json.dumps(chiffres, ensure_ascii=False)
    (requete,) = requetes_de_reponse(doublures)
    schema = requete["response_format"]["json_schema"]["schema"]
    assert "complement" in schema["properties"]


def test_la_mesure_enrichie_garde_le_complement_et_le_compte(
    banc_: Banc, doublures: Doublures
):
    DoublureFournisseur.contenu = enrichie()
    question = jeu.Question("Q", QUESTION, "F9", "au_catalogue", (1,))
    run = mesurer(banc_, banc_.profil("factice-a"), [question], 0.5, "enrichi")
    (x,) = run.par_question
    assert x["complement_present"] is True and x["complement_longueur"] == len(
        COMPLEMENT
    )
    assert x["complement_titres_non_declares"] == [TITRE_DU_CATALOGUE]
    assert x["complement_titres_hors_controle"] == [TITRE_DU_CATALOGUE]
    assert run.textes["Q (complément, non vérifié)"] == COMPLEMENT
    m = metriques(run)
    assert (m["complement_present"], m["complement_titre_non_declare"]) == (1, 1)
    assert (m["complement_titre_hors_controle"], m["complement_titres_moyen"]) == (1, 1)
    # En strict, ni le mode dans la requête, ni les champs, ni les métriques.
    DoublureFournisseur.contenu = json.dumps(
        {k: v for k, v in json.loads(enrichie()).items() if "complement" not in k}
    )
    strict = mesurer(banc_, banc_.profil("factice-a"), [question], 0.5)
    assert "complement_present" not in strict.par_question[0]
    assert "complement_present" not in metriques(strict)
    assert (
        "complement"
        not in requetes_de_reponse(doublures)[-1]["response_format"]["json_schema"][
            "schema"
        ]["properties"]
    )


def test_une_serie_rendue_nommee_sans_etre_declaree_est_hors_de_la_limite(
    banc_: Banc, doublures: Doublures
):
    """« Série 1 », rendue par la recherche, est contrôlée par la route : comptée
    comme non déclarée, pas comme hors contrôle."""
    DoublureFournisseur.contenu = enrichie(
        complement="Série 1 et le titre du catalogue, à lire.", complement_titres=[]
    )
    chiffres, _ = essai_appel(
        banc_, banc_.profil("factice-a"), QUESTION, mode="enrichi"
    )
    assert chiffres["statut"] == 200
    non_declares = chiffres["complement_titres_non_declares"]
    assert non_declares == ["Série 1", TITRE_DU_CATALOGUE]
    assert chiffres["complement_titres_hors_controle"] == [TITRE_DU_CATALOGUE]


def test_le_catalogue_se_lit_avant_le_premier_appel(banc_: Banc, doublures: Doublures):
    def en_panne() -> list[str]:
        raise RuntimeError("base en défaut")

    panne = replace(banc_, catalogue=en_panne)
    question = jeu.Question("Q", QUESTION, "F9", "au_catalogue", (1,))
    with pytest.raises(RuntimeError, match="base en défaut"):
        mesurer(panne, panne.profil("factice-a"), [question], 0.5, "enrichi")
    with pytest.raises(RuntimeError, match="base en défaut"):
        essai_appel(panne, panne.profil("factice-a"), QUESTION, mode="enrichi")
    assert requetes_de_reponse(doublures) == []
    # En strict, le catalogue n'est pas lu.
    mesurer(panne, panne.profil("factice-a"), [question], 0.5)


def test_un_complement_refuse_est_compte_et_garde_pour_la_lecture(
    banc_: Banc, doublures: Doublures
):
    DoublureFournisseur.contenu = enrichie(
        complement="Série 2 compte 12 tomes.", complement_titres=[]
    )
    question = jeu.Question("Q", QUESTION, "F9", "au_catalogue", (1,))
    run = mesurer(banc_, banc_.profil("factice-a"), [question], 0.5, "enrichi")
    (x,) = run.par_question
    assert (x["statut"], x["motif"]) == (503, "complement_metadonnee")
    garde = run.textes["Q (sortie refusée, complement_metadonnee)"]
    assert garde.endswith("Complément refusé : Série 2 compte 12 tomes.")
    m = metriques(run)
    assert m["sorties_invalides_complement"] == 1
    assert m["sorties_invalides_complement_metadonnee"] == 1
    assert m["complement_present"] == 0


def test_un_complement_refuse_portant_un_nom_de_membre_n_est_pas_garde(
    banc_: Banc, doublures: Doublures
):
    from conftest import AUTEURS

    DoublureFournisseur.contenu = enrichie(
        complement=f"Les lecteurs comme {AUTEURS[0]} l'aiment.", complement_titres=[]
    )
    question = jeu.Question("Q", QUESTION, "F9", "au_catalogue", (1,))
    run = mesurer(banc_, banc_.profil("factice-a"), [question], 0.5, "enrichi")
    (x,) = run.par_question
    assert (x["statut"], x["motif"]) == (503, "complement_pseudonyme")
    assert run.textes == {}
    (trouve,) = x["noms_trouves"]
    assert set(trouve) == {"empreinte", "dans_le_message_envoye", "homonyme_admis"}
    assert AUTEURS[0] not in json.dumps(x, ensure_ascii=False)
    assert metriques(run)["sorties_invalides_complement_pseudonyme"] == 1


def test_le_banc_recompte_les_noms_dans_le_complement(
    banc_: Banc, doublures: Doublures, monkeypatch: pytest.MonkeyPatch
):
    """Si la route laissait passer un nom de membre dans le complément, le banc le
    compterait et ne garderait aucun texte."""

    from conftest import AUTEURS

    DoublureFournisseur.contenu = enrichie(
        complement=f"Les lecteurs comme {AUTEURS[0]} l'aiment.",
        complement_titres=[],
    )

    async def complement_du_modele(*args: object) -> tuple[str, list[str]]:
        return (f"Les lecteurs comme {AUTEURS[0]} l'aiment.", [])

    monkeypatch.setattr(route, "controler_complement", complement_du_modele)
    _, appel = essai_appel(banc_, banc_.profil("factice-a"), QUESTION, mode="enrichi")
    assert appel.statut == 200 and appel.pseudonymes == 1
    assert appel.complement is None and appel.texte is None


def test_les_metriques_du_complement_et_le_critere():
    def ligne(**champs: object) -> dict[str, object]:
        base: dict[str, object] = {
            "question_id": "Q",
            "famille": "F7",
            "issue": "reconnue_hors_catalogue",
            "statut": 200,
            "code": None,
            "motif": None,
            "pseudonymes": 0,
            "refus": True,
            "mention_hors_catalogue": False,
            "appels": [],
            "complement_present": False,
            "complement_titres": 0,
            "complement_titres_non_declares": [],
        }
        return base | champs

    lignes = [
        ligne(complement_present=True, complement_titres=2),
        ligne(
            complement_present=True,
            complement_titres=1,
            complement_titres_non_declares=["Ciel"],
        ),
        ligne(),
        ligne(refus=False, complement_present=True, complement_titres=3),
        ligne(statut=503, code="sortie_modele_invalide", motif="complement_marque"),
        ligne(statut=503, code="sortie_modele_invalide", motif="pseudonyme"),
    ]
    lignes[2] |= {"famille": "F9", "issue": "au_catalogue"}
    m = metriques(Run("p", par_question=lignes, mode="enrichi"))
    assert m["complement_present"] == 3
    assert (m["refus_rendus"], m["complement_quand_refus"]) == (3, 2)
    assert (m["refus_rendus_F7"], m["complement_quand_refus_F7"]) == (2, 2)
    assert m["complement_quand_refus_hors_catalogue"] == 2
    assert m["complement_titres_moyen"] == 2
    assert m["complement_titre_non_declare"] == 1
    assert m["sorties_invalides_complement"] == 1
    assert m["sorties_invalides_complement_marque"] == 1
    # Le critère lit la partie stricte : une sortie refusée pour un nom de membre
    # le fait tomber, les métriques du complément n'y entrent pas.
    v = verdict(m)
    assert v["pseudonymes"] is False
    sans = {
        k: x for k, x in m.items() if "complement" not in k and "refus_rendus" not in k
    }
    assert verdict(sans) == v
    autres = m | {k: 99 for k in m if k.startswith("complement")}
    assert verdict(autres) == v
    # Un nom rendu dans un complément entre au critère, comme dans la réponse.
    rendu = metriques(Run("p", par_question=[ligne(pseudonymes=1)], mode="enrichi"))
    assert verdict(rendu)["pseudonymes"] is False
    vide = metriques(Run("p", par_question=[ligne(statut=503)], mode="enrichi"))
    assert "complement_titres_moyen" not in vide


def test_les_titres_du_catalogue_nommes_sans_etre_declares():
    lus: list[int] = []

    def catalogue() -> list[str]:
        lus.append(1)
        return ["Ciel", "One Piece", "One", TITRE_DU_CATALOGUE, "", "!!"]

    b = Banc(None, "", None, None, None, catalogue=catalogue)  # type: ignore[arg-type]
    texte = "One Piece, puis Ciel bleu ; et le titre du catalogue, encore Ciel."
    assert b.titres_non_declares(texte, ["ONE PIECE"]) == ["Ciel", TITRE_DU_CATALOGUE]
    assert b.titres_non_declares("Rien ici.", []) == []
    assert b.titres_non_declares("Cielo, Onze.", []) == []
    # « déclaré » se juge par la clé : « OnePiece » déclare « One Piece ».
    assert b.titres_non_declares("One Piece, encore.", ["OnePiece"]) == []
    # Un titre du catalogue dans un titre déclaré ne compte pas ; ailleurs, si.
    assert b.titres_non_declares("Lisez One Shot.", ["One Shot"]) == []
    assert b.titres_non_declares("One, puis One Shot.", ["One Shot"]) == ["One"]
    assert lus == [1]  # lu une fois


def test_le_catalogue_se_lit_par_la_connexion_de_l_api(
    api_reponse: TestClient, base_api: BaseApi
):
    ajouter_au_catalogue(base_api.dsn_admin, 3, TITRE_DU_CATALOGUE)
    titres = cli.lire_le_catalogue(api_reponse.app.state.etat)
    assert TITRE_DU_CATALOGUE in titres


# --------------------------------------------------------------------------- #
#  La ligne de commande
# --------------------------------------------------------------------------- #
def test_un_run_enrichi_par_la_ligne_de_commande(
    banc_: Banc,
    doublures: Doublures,
    base_api: BaseApi,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    DoublureFournisseur.contenu = refus_enrichi()
    DoublureFournisseur.usage = {"prompt_tokens": 1000, "completion_tokens": 20}
    sorties = tmp_path / "sorties"
    monkeypatch.setattr(enregistrement, "SORTIES", sorties)
    etat_git = {"commit": "c" * 40, "branche": "b", "arbre_propre": "True"}
    monkeypatch.setattr(enregistrement, "exiger_commit", lambda: etat_git)
    charges: list[dict[str, Any]] = []

    def executer(commande: list[str]) -> str:
        charges.append(json.loads(Path(commande[-1]).read_text(encoding="utf-8")))
        return RUN_ID

    monkeypatch.setattr(enregistrement, "executer", executer)
    ouvreur = lambda _: ouvreur_de(banc_)  # noqa: E731
    assert (
        cli.principal(["estimer", "factice-a", COURT, "--mode", "enrichi"], {}, ouvreur)
        == 0
    )
    estimation = json.loads(capsys.readouterr().out)
    assert estimation["mode"] == "enrichi" and "sans_passage" in estimation
    code = cli.principal(
        [
            "mesurer",
            "factice-a",
            COURT,
            "--mode",
            "enrichi",
            "--experience",
            EXPERIENCE,
        ],
        environ={"DATABASE_URL": base_api.dsn_admin},
        ouvreur=ouvreur,
    )
    assert code == 0
    for requete in requetes_de_reponse(doublures):
        schema = requete["response_format"]["json_schema"]["schema"]
        assert "complement" in schema["properties"]
    (dossier,) = sorties.iterdir()
    assert dossier.name.startswith("factice-a-enrichi-F7+hors_catalogue-")
    resume = json.loads((dossier / "resume.json").read_text(encoding="utf-8"))
    assert resume["verdict"]["tenu"] is True
    assert resume["metriques"]["complement_quand_refus"] == 10
    assert resume["parametres"]["mode"] == "enrichi"
    (charge,) = charges
    assert charge["experience"] == EXPERIENCE
    assert charge["etiquettes"]["mode"] == "enrichi"
    assert COMPLEMENT not in json.dumps(charge, ensure_ascii=False)
    assert COMPLEMENT in (dossier / "reponses.md").read_text(encoding="utf-8")
    with psycopg.connect(base_api.dsn_admin) as cx:
        (note,) = cx.execute(
            "SELECT note FROM bench.eval_runs WHERE run_id = %s", (RUN_ID,)
        ).fetchone() or ("",)
    assert note.startswith("E3 étape 6, spec 06 — réponse rédigée, mode enrichi")

    # Reprendre : même expérience (dans la charge), même note.
    autre = "fedcba9876543210fedcba9876543210"
    reprises: list[dict[str, Any]] = []

    def reexecuter(commande: list[str]) -> str:
        reprises.append(json.loads(Path(commande[-1]).read_text(encoding="utf-8")))
        return autre

    monkeypatch.setattr(enregistrement, "executer", reexecuter)
    assert (
        cli.principal(
            ["reprendre", str(dossier)], environ={"DATABASE_URL": base_api.dsn_admin}
        )
        == 0
    )
    with psycopg.connect(base_api.dsn_admin) as cx:
        (note,) = cx.execute(
            "SELECT note FROM bench.eval_runs WHERE run_id = %s", (autre,)
        ).fetchone() or ("",)
    assert "mode enrichi" in note
    assert [c["experience"] for c in reprises] == [EXPERIENCE]


def test_un_run_strict_garde_l_experience_l_etiquette_et_la_note_d_avant(
    banc_: Banc,
    doublures: Doublures,
    base_api: BaseApi,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    DoublureFournisseur.contenu = json.dumps(
        {k: v for k, v in json.loads(refus_enrichi()).items() if "complement" not in k}
    )
    monkeypatch.setattr(enregistrement, "SORTIES", tmp_path / "sorties")
    etat_git = {"commit": "c" * 40, "branche": "b", "arbre_propre": "True"}
    monkeypatch.setattr(enregistrement, "exiger_commit", lambda: etat_git)
    charges: list[dict[str, Any]] = []

    def executer(commande: list[str]) -> str:
        charges.append(json.loads(Path(commande[-1]).read_text(encoding="utf-8")))
        return RUN_ID

    monkeypatch.setattr(enregistrement, "executer", executer)
    code = cli.principal(
        ["mesurer", "factice-a", COURT],
        environ={"DATABASE_URL": base_api.dsn_admin},
        ouvreur=lambda _: ouvreur_de(banc_),
    )
    assert code == 0
    (charge,) = charges
    assert (charge["experience"], charge["etiquettes"]["mode"]) == (
        "E3-reponse",
        "strict",
    )
    with psycopg.connect(base_api.dsn_admin) as cx:
        (note,) = cx.execute(
            "SELECT note FROM bench.eval_runs WHERE run_id = %s", (RUN_ID,)
        ).fetchone() or ("",)
    assert note.startswith("E3 étape 3, spec 06 — réponse rédigée — factice-a-F7+")


def test_l_essai_enrichi_par_la_ligne_de_commande(
    banc_: Banc,
    doublures: Doublures,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    DoublureFournisseur.contenu = enrichie()
    monkeypatch.setattr(enregistrement, "SORTIES", tmp_path)
    ouvreur = lambda _: ouvreur_de(banc_)  # noqa: E731
    argv = ["essai", "factice-a", QUESTION, "--mode", "enrichi"]
    assert cli.principal(argv, {}, ouvreur) == 0
    sortie = capsys.readouterr().out
    assert "--- complément (non vérifié" in sortie and COMPLEMENT in sortie
    (dossier,) = (tmp_path / "essais").iterdir()
    assert dossier.name.endswith("_enrichi")
    assert COMPLEMENT in (dossier / "reponses.md").read_text(encoding="utf-8")
    assert COMPLEMENT not in (dossier / "essai.json").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "argv",
    [
        ["mesurer", "factice-a", COURT, "--mode", "autre"],
        ["mesurer", "factice-a", COURT, "--experience", "nom invalide !"],
        ["essai", "factice-a", QUESTION, "--mode", "Enrichi"],
    ],
    ids=["mode", "experience", "casse"],
)
def test_les_options_refusees(argv: list[str]):
    with pytest.raises(SystemExit) as e:
        cli.analyser(argv)
    assert e.value.code == 2
