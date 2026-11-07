"""La séquence (`verify 14`) : étapes factices, aucun service, aucune base."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import (
    CANDIDATS,
    INFO_GEMMA,
    REELLES,
    Monde,
    apres,
    bilan_encodeur,
    comparer_rend,
    mesure_rend,
    rejeu_rend,
    verdict_rend,
)

from chaine import environnement, executer, rapport
from chaine.codes import Code
from chaine.environnement import Commande, Sortie

REFUS = CANDIDATS / "refus_bgem3_v2.toml"
REQUALIFICATION = CANDIDATS / "requalification_v2.toml"


def lancer(m: Monde, candidat: Path = REFUS) -> executer.Passage:
    return executer.lancer(
        candidat, m.environ, m.outils, ecrire_rapport=rapport.ecrire_et_attacher
    )


def requalification(m: Monde) -> None:
    m.lanceur.reponses |= {
        "psql.en_service": Sortie(0, "2\n"),
        "10.executer": mesure_rend(3, run="a" * 32),
        "10.promotion": verdict_rend("promotion proposée", 31, 21),
    }
    m.http.reponses = {"http://127.0.0.1:8082/info": (200, json.dumps(INFO_GEMMA))}


# --------------------------------------------------------------------------- #
#  L'enchaînement
# --------------------------------------------------------------------------- #


def test_le_passage_refuse_va_jusqu_au_rapport_et_rend_10(monde: Monde):
    p = lancer(monde)
    assert p.code == Code.REFUS_REGLE
    assert [r.numero for r in p.resultats] == [0, 1, 2, 3, 4, 5, 6]
    assert [r.statut for r in p.resultats] == [
        "tenue",
        "tenue",
        "non lancée",
        "tenue",
        "tenue",
        "tenue",
        "refus",
    ]
    assert p.conditions == ["global 22 < 31", "reconnaissance 16 < 20"]
    assert "11.image" not in monde.lanceur.noms(), "pas d'image sur un refus"
    assert monde.lanceur.noms()[-1] == "10.mlflow.log_artifact"
    d = json.loads((monde.outils.livraisons / f"{p.nom_livraison}.json").read_text())
    assert (d["code"], d["code_nom"], d["verdict"]) == (
        10,
        "REFUS_REGLE",
        "refus proposé",
    )
    assert d["environnement"] == "préproduction" and d["base"] == "apimanga_preprod"
    assert d["encodage"] == 4 and d["run"] == "f" * 32
    assert d["piece_mlflow"] == "attachée"
    assert any("Code de sortie : 10 (REFUS_REGLE)" in ligne for ligne in monde.ecran)


def test_la_requalification_va_jusqu_a_l_image_et_rend_0(monde: Monde):
    requalification(monde)
    p = lancer(monde, REQUALIFICATION)
    assert p.code == Code.PROMOTION_PROPOSEE
    assert [r.statut for r in p.resultats] == [
        "tenue",
        "tenue",
        "non lancée",
        "non lancée",
        "tenue",
        "tenue",
        "tenue",
        "tenue",
    ]
    noms = monde.lanceur.noms()
    assert "09.encodage" not in noms and "05.construire_v2" not in noms
    assert noms.index("10.executer") < noms.index("10.executer.rejeu")
    assert noms.index("10.promotion") < noms.index("11.image")
    assert p.encodage == 3 and p.image == "sha256:" + "ab" * 32
    assert any("make chaine-promouvoir" in ligne for ligne in monde.ecran)


def test_lancer_n_appelle_jamais_la_promotion(monde: Monde):
    for verdict in ("refus proposé", "promotion proposée"):
        monde.lanceur.lancees.clear()
        if verdict == "promotion proposée":
            requalification(monde)
            lancer(monde, REQUALIFICATION)
        else:
            lancer(monde)
        for c in monde.lanceur.lancees:
            assert "journal_promotions" not in " ".join(c.argv), c.nom
            assert not c.nom.startswith("10.journal"), c.nom


def test_une_nouvelle_version_lance_la_construction(
    monde: Monde, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(environnement, "RACINE_DEPOT", tmp_path)
    candidat = tmp_path / "nouvelle_v2.toml"
    candidat.write_text(
        REFUS.read_text("utf-8")
        .replace('"refus_bgem3_v2"', '"nouvelle_v2"')
        .replace("nouvelle_version = false", "nouvelle_version = true"),
        "utf-8",
    )
    p = lancer(monde, candidat)
    assert p.resultats[2].statut == "tenue"
    noms = monde.lanceur.noms()
    assert noms.index("05.construire_v2.dry_run") < noms.index("05.construire_v2")
    assert noms.index("05.construire_v2") < noms.index("09.encodage")


# --------------------------------------------------------------------------- #
#  L'arrêt au premier échec, à chaque rang
# --------------------------------------------------------------------------- #


ARRETS = [
    ("git.status", Sortie(0, " M fichier\n"), 0, Code.PREREQUIS),
    ("git.contient", Sortie(0, ""), 0, Code.PREREQUIS),
    ("git.suivi", Sortie(1, "", "error: pathspec"), 0, Code.PREREQUIS),
    ("git.inchange", Sortie(1), 0, Code.PREREQUIS),
    ("docker.pile", Sortie(0, "api-modele-api-1\n"), 0, Code.PREREQUIS),
    ("psql.en_service", Sortie(0, "2\n"), 0, Code.PREREQUIS),
    ("psql.jeu", Sortie(0, "0" * 64 + "\n"), 1, Code.DONNEES),
    ("psql.empreintes.v2", Sortie(0, "docs\t1\tx\nfragments\t1\ty\n"), 1, Code.DONNEES),
    ("05.construire_v2.dry_run", Sortie(1, "", "ERREUR : garde"), 1, Code.DONNEES),
    ("09.encodage", Sortie(1, "", "Traceback"), 3, Code.ENCODAGE),
    ("09.equivalence.exporter", Sortie(1, "", "Traceback"), 4, Code.PREREQUIS),
    ("09.equivalence.comparer", comparer_rend("echec", 0.95), 4, Code.TEST_MODELE),
    ("10.executer", Sortie(1, "", "EnregistrementRefuse"), 5, Code.MESURE),
    (
        "10.promotion",
        Sortie(1, "", (REELLES / "promotion_verdict_refuse.txt").read_text("utf-8")),
        6,
        Code.PAS_DE_VERDICT,
    ),
]


@pytest.mark.parametrize(
    "nom, sortie, rang, code", ARRETS, ids=[a[0] + "-" + str(a[2]) for a in ARRETS]
)
def test_un_echec_arrete_la_sequence_a_son_rang(monde, nom, sortie, rang, code):
    monde.lanceur.reponses[nom] = sortie
    p = lancer(monde)
    assert p.code == code
    assert p.resultats[-1].numero == rang and p.resultats[-1].code == code
    lancees = monde.lanceur.noms()
    position = max(i for i, n in enumerate(lancees) if n == nom)
    suite = [n for n in lancees[position + 1 :] if not n.startswith("psql.pseudonymes")]
    assert all(n == "10.mlflow.log_artifact" for n in suite), suite
    assert (monde.outils.livraisons / f"{p.nom_livraison}.md").is_file()
    d = json.loads((monde.outils.livraisons / f"{p.nom_livraison}.json").read_text())
    assert d["code"] == int(code) and d["etapes"][-1]["numero"] == rang


def test_l_image_en_echec_rend_80(monde: Monde):
    requalification(monde)
    monde.lanceur.reponses["11.image"] = Sortie(1, "", "erreur de construction")
    assert lancer(monde, REQUALIFICATION).code == Code.PACKAGING


def test_un_candidat_hors_du_depot_rend_20(monde: Monde, tmp_path: Path):
    candidat = tmp_path / "refus_bgem3_v2.toml"
    candidat.write_text(REFUS.read_text("utf-8"), "utf-8")
    p = lancer(monde, candidat)
    assert p.code == Code.PREREQUIS and "hors du dépôt" in p.resultats[0].motif


def test_une_panne_de_l_orchestrateur_rend_90(monde: Monde):
    def panne(_c: Commande) -> Sortie:
        raise RuntimeError("panne")

    monde.lanceur.reponses["10.executer"] = panne
    p = lancer(monde)
    assert p.code == Code.INTERNE and p.resultats[-1].numero == 5


def test_l_instance_injoignable_ou_non_conforme_rend_20(monde: Monde):
    monde.http.reponses = {}
    assert lancer(monde).code == Code.PREREQUIS
    monde.http.reponses = {"http://127.0.0.1:8081/info": (200, json.dumps(INFO_GEMMA))}
    p = lancer(monde)
    assert p.code == Code.PREREQUIS and "ne sert pas" in p.resultats[0].motif


def test_un_encodage_existant_absent_du_registre_rend_20(monde: Monde):
    requalification(monde)
    monde.lanceur.reponses["psql.encodages"] = Sortie(0, "1\tBAAI/bge-m3\tx\ty\tz\n")
    assert lancer(monde, REQUALIFICATION).code == Code.PREREQUIS


# --------------------------------------------------------------------------- #
#  Le bilan de l'encodeur, l'équivalence, la mesure : lus, jamais supposés
# --------------------------------------------------------------------------- #


def test_encodage_cree_false_rend_50_sur_le_bilan_reel(monde: Monde):
    def rejeu(c: Commande) -> Sortie:
        bilan = (REELLES / "rejeu_bge-m3.json").read_text("utf-8")
        Path(apres(c, "--sortie")).write_text(bilan, "utf-8")
        return Sortie(0, bilan)

    monde.lanceur.reponses["09.encodage"] = rejeu
    p = lancer(monde)
    assert p.code == Code.ENCODAGE and "encodage_cree: false" in p.resultats[-1].motif


def test_le_numero_de_l_encodage_est_lu_dans_le_bilan(monde: Monde):
    monde.lanceur.reponses |= {
        "09.encodage": bilan_encodeur(7),
        "10.executer": mesure_rend(7),
    }
    p = lancer(monde)
    assert p.encodage == 7
    for nom in ("09.equivalence.exporter", "09.equivalence.comparer", "10.executer"):
        assert apres(monde.lanceur.une(nom), "--encodage") == "7", nom


def test_la_vue_qui_bouge_pendant_l_encodage_rend_50(monde: Monde):
    reponses = iter(["3\n", "4\n"])
    monde.lanceur.reponses["psql.en_service"] = lambda _c: Sortie(0, next(reponses))
    assert lancer(monde).code == Code.ENCODAGE


@pytest.mark.parametrize(
    "stderr, code",
    [
        ("service_embedding.client.ServiceNonConforme: révision", Code.PREREQUIS),
        ("FileNotFoundError: encodage_4.json", Code.PREREQUIS),
        ("KeyError: 'echantillon'", Code.INTERNE),
    ],
)
def test_comparer_sans_verdict_ne_rend_pas_60(monde: Monde, stderr: str, code: Code):
    monde.lanceur.reponses["09.equivalence.comparer"] = Sortie(1, "", stderr)
    p = lancer(monde)
    assert p.code == code and p.code != Code.TEST_MODELE


def test_un_echantillon_hors_du_dossier_de_la_preproduction_rend_20(monde: Monde):
    monde.lanceur.reponses["09.equivalence.exporter"] = Sortie(
        0, str(environnement.DOSSIER_PRODUCTION / "encodage_4.json")
    )
    p = lancer(monde)
    assert p.code == Code.PREREQUIS and p.resultats[-1].numero == 4


def test_une_equivalence_a_surveiller_passe_et_se_rapporte(monde: Monde):
    monde.lanceur.reponses["09.equivalence.comparer"] = comparer_rend(
        "a_surveiller", 0.9988
    )
    p = lancer(monde)
    assert p.resultats[4].details["verdict"] == "a_surveiller"
    assert p.resultats[4].details["cos_min"] == 0.9988


def test_un_rejeu_non_identique_rend_70(monde: Monde):
    requalification(monde)
    monde.lanceur.reponses["10.executer.rejeu"] = rejeu_rend(False)
    p = lancer(monde, REQUALIFICATION)
    assert p.code == Code.MESURE and "rejeu" in p.resultats[-1].motif


def test_une_mesure_d_un_autre_encodage_rend_70(monde: Monde):
    monde.lanceur.reponses["10.executer"] = mesure_rend(9)
    assert lancer(monde).code == Code.MESURE


# --------------------------------------------------------------------------- #
#  La production protégée, la base et le dossier transmis
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "environ",
    [
        {"CHAINE_BASE": "apimanga"},
        {"CHAINE_BASE": "autre"},
        {},
        {environnement.VARIABLE_DOSSIER: str(environnement.DOSSIER_PRODUCTION)},
        {environnement.VARIABLE_DOSSIER: str(environnement.DOSSIER_PRODUCTION / "x")},
    ],
    ids=["production", "autre-base", "sans-dossier", "dossier-production", "dedans"],
)
def test_la_production_et_son_dossier_sont_refuses_sans_rien_lancer(
    monde: Monde, environ: dict[str, str]
):
    monde.environ = (
        environ
        if environnement.VARIABLE_DOSSIER in environ
        else monde.environ | environ
    )
    if not environ:
        monde.environ = {}
    p = lancer(monde)
    assert p.code == Code.PREREQUIS and p.resultats[-1].numero == 0
    assert monde.lanceur.lancees == [], "aucune commande lancée"


def test_chaque_commande_recoit_la_preproduction_et_son_dossier(monde: Monde):
    lancer(monde)
    preprod = str(monde.preprod.resolve())
    for c in monde.lanceur.lancees:
        if c.nom.startswith(("05.", "09.", "10.")):
            assert "--no-env-file" in c.argv, c.nom
        if "DATABASE_URL" in c.env:
            assert c.env["DATABASE_URL"].endswith("@localhost:5432/apimanga_preprod")
        if c.nom.startswith("09.equivalence"):
            assert c.env[environnement.VARIABLE_DOSSIER] == preprod, c.nom
        if c.nom.startswith("psql."):
            assert "/apimanga_preprod" in c.argv[1] and "verif_lecture" in c.argv[1]


def test_la_base_transmise_l_emporte_sur_un_environnement_herite(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost:5432/apimanga")
    monkeypatch.setenv("VIRTUAL_ENV", "/ailleurs")
    env = environnement.environnement_propre(
        {"DATABASE_URL": "postgresql://postgres@localhost:5432/apimanga_preprod"}
    )
    assert env["DATABASE_URL"].endswith("/apimanga_preprod")
    assert "VIRTUAL_ENV" not in env and env["LANG"] == "C.UTF-8"


# --------------------------------------------------------------------------- #
#  Le passage à blanc
# --------------------------------------------------------------------------- #


def test_le_passage_a_blanc_ne_lance_rien_et_affiche_la_sequence(monde: Monde):
    ecran: list[str] = []
    code = executer.a_blanc(REFUS, monde.environ, ecran.append)
    assert code == Code.PROMOTION_PROPOSEE
    assert monde.lanceur.lancees == []
    texte = "\n".join(ecran)
    assert "Base visée : apimanga_preprod" in texte
    for numero in range(9):
        assert f"[{numero}] " in texte
    assert "service_embedding.encodage bge-m3 --corpus v2" in texte
    assert "mesures_recherche.executer 7 --encodage <n, lu dans le bilan>" in texte
    assert "DATABASE_URL=postgresql://postgres@localhost:5432/apimanga_preprod" in texte
    assert str(environnement.RACINE_DEPOT) not in texte, "aucun chemin personnel"


def test_le_passage_a_blanc_refuse_la_production(monde: Monde):
    with pytest.raises(environnement.Arret) as e:
        executer.a_blanc(REFUS, {"CHAINE_BASE": "apimanga"}, print)
    assert e.value.code == Code.PREREQUIS


def test_le_passage_a_blanc_de_la_requalification_montre_le_rejeu(monde: Monde):
    ecran: list[str] = []
    executer.a_blanc(REQUALIFICATION, monde.environ, ecran.append)
    texte = "\n".join(ecran)
    assert "--rejeu-de" in texte and "a3b7f0e025e4476ea4b60e7d0498876b" in texte
    assert "[3] Encoder — non" in texte


# --------------------------------------------------------------------------- #
#  Le rapport
# --------------------------------------------------------------------------- #


def test_le_rapport_porte_ses_champs_et_aucun_secret_ni_pseudonyme(monde: Monde):
    fuite = (
        "connexion postgresql://postgres:motdepassesecret@localhost:5432/apimanga_preprod\n"
        "clé sk-" + "A" * 44 + "\n"
        "jeton hf_" + "b" * 34 + "\n"
        "X-API-Key: cledelapplicationfactice\n"
        "Zorglub a écrit une critique ; mlle factice aussi\n"
    )
    monde.lanceur.reponses["10.executer"] = Sortie(1, "", fuite)
    p = lancer(monde)
    md = (monde.outils.livraisons / f"{p.nom_livraison}.md").read_text("utf-8")
    js = (monde.outils.livraisons / f"{p.nom_livraison}.json").read_text("utf-8")
    for texte in (md, js):
        for secret in (
            "motdepassesecret",
            "sk-AAAA",
            "hf_bbbb",
            "cledelapplicationfactice",
            "Zorglub",
            "mlle factice",
        ):
            assert secret.lower() not in texte.lower(), secret
        assert "[masqué]" in texte
    d = json.loads(js)
    for cle in (
        "livraison",
        "environnement",
        "base",
        "dossier_echantillons",
        "candidat",
        "commit",
        "encodage",
        "run",
        "mesure",
        "regle",
        "verdict",
        "conditions_manquantes",
        "image",
        "etapes",
        "code",
        "code_nom",
        "sens",
        "piece_mlflow",
    ):
        assert cle in d, cle
    assert d["piece_mlflow"] == "non attachée : aucun run"
    assert "## Les étapes" in md and "| N° | Étape |" in md


def test_un_extrait_sans_pseudonymes_lisibles_est_omis(monde: Monde):
    monde.lanceur.reponses |= {
        "10.executer": Sortie(1, "", "une erreur"),
        "psql.pseudonymes": Sortie(2, "", "refus"),
    }
    p = lancer(monde)
    d = json.loads((monde.outils.livraisons / f"{p.nom_livraison}.json").read_text())
    assert d["etapes"][-1]["sortie"].startswith("(omise")


def test_une_piece_mlflow_refusee_est_dite(monde: Monde):
    monde.lanceur.reponses["10.mlflow.log_artifact"] = Sortie(1, "", "erreur")
    p = lancer(monde)
    d = json.loads((monde.outils.livraisons / f"{p.nom_livraison}.json").read_text())
    assert d["piece_mlflow"] == "non attachée : code 1"
    md = (monde.outils.livraisons / f"{p.nom_livraison}.md").read_text("utf-8")
    assert "Pièce MLflow : non attachée" in md
    commande = monde.lanceur.une("10.mlflow.log_artifact")
    assert apres(commande, "-r") == "f" * 32
    assert commande.env["MLFLOW_TRACKING_URI"].startswith("sqlite:///")


# --------------------------------------------------------------------------- #
#  La lecture des sorties, sur leurs formes d'erreur
# --------------------------------------------------------------------------- #


def test_la_lecture_du_dry_run_reel_et_de_ses_ecarts():
    from chaine import etapes

    reel = Sortie(0, (REELLES / "construire_v2_dry_run.txt").read_text("utf-8"))
    assert etapes.lire_dry_run(reel)["fragments"] == 77006
    ecart = Sortie(
        0, 'contrôles verts — ANNULÉ\n{"ecritures": {"docs ajoutés": {"x": 1}}}\n'
    )
    with pytest.raises(environnement.Arret) as e:
        etapes.lire_dry_run(ecart)
    assert e.value.code == Code.DONNEES and "diffère" in e.value.motif


@pytest.mark.parametrize(
    "bilan, motif",
    [
        (None, "absent"),
        ("{pas du json", "absent"),
        ('{"arret": "swap écrit 30 s"}', "arrêté"),
        ('{"encodage_cree": true, "encodage_id": "4"}', "sans encodage_id"),
        (
            '{"encodage_cree": true, "encodage_id": 4, "termine": false,'
            ' "vecteurs_ecrits": 10, "fragments_corpus": 77006}',
            "incomplet",
        ),
    ],
)
def test_un_bilan_d_encodeur_insuffisant_rend_50(tmp_path: Path, bilan, motif):
    from chaine import etapes

    fichier = tmp_path / "encodage.json"
    if bilan is not None:
        fichier.write_text(bilan, "utf-8")
    with pytest.raises(environnement.Arret) as e:
        etapes.lire_bilan(fichier)
    assert e.value.code == Code.ENCODAGE and motif in e.value.motif


def test_la_lecture_du_verdict_et_du_rejeu_sur_leurs_formes_inattendues():
    from chaine import etapes

    with pytest.raises(environnement.Arret) as e:
        etapes.lire_verdict(Sortie(0, '{"verdict": "autre chose"}'))
    assert e.value.code == Code.INTERNE
    with pytest.raises(environnement.Arret) as e:
        etapes.lire_rejeu(Sortie(2, "", "Traceback"))
    assert e.value.code == Code.MESURE
    reel = json.loads((REELLES / "promotion_mesure_8.json").read_text("utf-8"))
    assert etapes.conditions_manquantes(reel) == []
    assert etapes.json_de("texte\n[1, 2]\n") is None


def test_une_configuration_introuvable_est_un_refus_nomme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from chaine import etapes

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "bge-m3.env").write_text("SANS_PORT=1\n", "utf-8")
    monkeypatch.setattr(etapes, "M09", tmp_path)
    with pytest.raises(environnement.Arret) as e:
        etapes.port_instance("bge-m3")
    assert e.value.code == Code.PREREQUIS
    compose = tmp_path / "compose.yml"
    compose.write_text("services:\n  api:\n    build: .\n", "utf-8")
    monkeypatch.setattr(etapes, "COMPOSE_11", compose)
    with pytest.raises(environnement.Arret) as e:
        etapes.image_de_l_api()
    assert e.value.code == Code.PACKAGING
