"""Les gestes de Max (`promouvoir`, `revenir`, `etat`), avec des étapes factices."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import IMAGE, INFO_GEMMA, Monde, rejeu_rend

from chaine import deployer, environnement
from chaine.codes import Arret, Code
from chaine.environnement import Sortie

CLE = "cle-factice-de-l-application-0123456789abcdef"
RUN = "a" * 32


@pytest.fixture
def max_(monde: Monde, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Monde:
    """Le monde de la promotion : rapport poussé, clé, copie du pgpass, API factice."""
    env = tmp_path / "env_11"
    env.write_text(f"API_MODELE_PROFIL_ACTIF=x\nAPI_MODELE_CLES='app:{CLE},autre:zz'\n")
    monkeypatch.setattr(deployer, "ENV_11", env)
    assert deployer.ENV_11 == env, "jamais le vrai .env du 11"
    monkeypatch.setattr(deployer, "JOURNAL", tmp_path / "journal.jsonl")
    pgpass = tmp_path / "pgpass_preprod"
    pgpass.write_text("")
    monde.environ[deployer.VARIABLE_PGPASS] = str(pgpass)
    monkeypatch.setattr(environnement, "RACINE_DEPOT", tmp_path)
    monde.outils.livraisons = tmp_path / "14_chaine" / "livraisons"
    livrer(monde, "promotion proposée", 3)
    monde.lanceur.reponses |= {
        "git.commit-fichier": Sortie(0, "c0ffee" + "1" * 34 + "\n"),
        "git.fichier-pousse": Sortie(0, "  origin/feature/chaine-livraison\n"),
        "10.journal.promouvoir": Sortie(
            0, json.dumps({"verdict": "promotion proposée"})
        ),
        "10.journal.revenir": Sortie(
            0, json.dumps({"apres": {"encodage_id": 2}}, indent=1)
        ),
        "09.instances.arret": Sortie(0),
        "09.instance.embeddinggemma": Sortie(0),
        "11.api.up": Sortie(0),
        "11.api.down": Sortie(0),
        "11.egalite": Sortie(0, "1 passed"),
        "docker.image": Sortie(0, f"{IMAGE}\n"),
        "10.executer.rejeu": rejeu_rend(True),
    }
    monde.http.reponses = {
        "http://127.0.0.1:8011/v1/sante": (200, '{"statut": "ok"}'),
        "http://127.0.0.1:8011/v1/info": (200, json.dumps({"encodage_id": 3})),
        "http://127.0.0.1:8082/health": (200, ""),
        "http://127.0.0.1:8082/info": (200, json.dumps(INFO_GEMMA)),
    }
    return monde


def livrer(m: Monde, verdict: str, encodage: int, image: str = IMAGE) -> str:
    m.outils.livraisons.mkdir(parents=True, exist_ok=True)
    nom = "20261107T030000Z_requalification_v2"
    d = {"verdict": verdict, "encodage": encodage, "run": RUN, "image": image}
    (m.outils.livraisons / f"{nom}.json").write_text(json.dumps(d))
    (m.outils.livraisons / f"{nom}.md").write_text("# rapport\n")
    return nom


NOM = "20261107T030000Z_requalification_v2"


def test_la_promotion_suit_l_ordre_et_verifie_l_api(max_: Monde):
    code = deployer.promouvoir(
        NOM, "Chaîne, run x : règle D3 tenue.", max_.environ, max_.outils
    )
    assert code == Code.PROMOTION_PROPOSEE
    noms = [n for n in max_.lanceur.noms() if not n.startswith("git.")]
    assert noms == [
        "docker.pile",
        "10.journal.promouvoir",
        "09.instances.arret",
        "docker.image",
        "11.api.up",
        "11.api.down",
        "docker.image",
        "09.instance.embeddinggemma",
        "11.egalite",
        "09.instances.arret",
    ]
    journal = max_.lanceur.une("10.journal.promouvoir").argv
    motif = journal[journal.index("--motif") + 1]
    assert motif.startswith(
        "Chaîne, run x : règle D3 tenue. — rapport 14_chaine/livraisons/"
    )
    assert f"{NOM}.md (c0ffee1111" in motif
    assert journal[journal.index("--par") + 1] == "Max"
    assert journal[journal.index("--encodage") + 1] == "3"
    api = max_.lanceur.une("11.api.up")
    assert api.env["API_MODELE_DSN_CONTENEUR"].endswith("/apimanga_preprod")
    assert api.env["API_MODELE_PORT_PUBLIE"] == "8011"
    assert "-p" in api.argv and "api-modele-preprod" in api.argv
    envoyee = [
        h.get("X-API-Key") == CLE for u, h in max_.http.recues if u.endswith("/v1/info")
    ]
    assert envoyee == [True], "la clé factice est envoyée (jamais la vraie)"
    egalite = max_.lanceur.une("11.egalite")
    assert egalite.env["API_MODELE_EGALITE_DSN"].endswith("/apimanga_preprod")
    assert egalite.env["API_MODELE_EGALITE_EXIGEE"] == "1"
    journal_execution = deployer.JOURNAL.read_text("utf-8")
    for texte in ("\n".join(max_.ecran), journal_execution):
        assert CLE not in texte, "la clé n'est jamais affichée ni écrite"
    for c in max_.lanceur.lancees:
        assert CLE not in " ".join(c.argv) + json.dumps(dict(c.env))


@pytest.mark.parametrize(
    "preparer, motif_attendu",
    [
        (lambda m: livrer(m, "refus proposé", 4), "verdict"),
        (
            lambda m: m.lanceur.reponses.update({"git.fichier-pousse": Sortie(0, "")}),
            "poussé",
        ),
        (lambda m: m.environ.pop(deployer.VARIABLE_PGPASS), "PREPROD_PGPASS_CONTENEUR"),
        (
            lambda m: m.lanceur.reponses.update(
                {"docker.pile": Sortie(0, "api-modele-api-1")}
            ),
            "pile",
        ),
        (lambda m: livrer(m, "promotion proposée", 3, "sha256:" + "cd" * 32), "image"),
        (lambda m: livrer(m, "promotion proposée", 4), "aucune vérification"),
        (lambda m: m.environ.update({"CHAINE_BASE": "apimanga"}), "production"),
    ],
    ids=["refus", "non-pousse", "pgpass", "pile", "image", "encodage-4", "production"],
)
def test_la_promotion_refusee_n_ecrit_rien_au_journal(
    max_: Monde, preparer, motif_attendu
):
    preparer(max_)
    with pytest.raises(Arret) as e:
        deployer.promouvoir(NOM, "motif", max_.environ, max_.outils)
    assert e.value.code == Code.PREREQUIS and motif_attendu in e.value.motif
    if motif_attendu != "image":
        assert "10.journal.promouvoir" not in max_.lanceur.noms()


def test_un_motif_vide_est_refuse(max_: Monde):
    for geste in (
        lambda: deployer.promouvoir(NOM, "  ", max_.environ, max_.outils),
        lambda: deployer.revenir("", max_.environ, max_.outils),
    ):
        with pytest.raises(Arret) as e:
            geste()
        assert e.value.code == Code.PREREQUIS and "motif" in e.value.motif
    assert max_.lanceur.lancees == []


def test_un_rapport_absent_est_refuse(max_: Monde):
    with pytest.raises(Arret):
        deployer.promouvoir("20990101T000000Z_absent", "m", max_.environ, max_.outils)


def test_une_promotion_refusee_par_le_module_10_rend_20(max_: Monde):
    max_.lanceur.reponses["10.journal.promouvoir"] = Sortie(1, "", "DecisionRefusee")
    with pytest.raises(Arret) as e:
        deployer.promouvoir(NOM, "m", max_.environ, max_.outils)
    assert e.value.code == Code.PREREQUIS


def test_un_info_qui_ne_designe_pas_l_encodage_rend_80_et_arrete_l_api(max_: Monde):
    max_.http.reponses["http://127.0.0.1:8011/v1/info"] = (200, '{"encodage_id": 2}')
    with pytest.raises(Arret) as e:
        deployer.promouvoir(NOM, "m", max_.environ, max_.outils)
    assert e.value.code == Code.PACKAGING
    assert max_.lanceur.noms()[-1] == "11.api.down", "l'API est arrêtée quand même"


def test_une_api_qui_ne_se_leve_pas_rend_80(max_: Monde):
    max_.lanceur.reponses["11.api.up"] = Sortie(1, "", "erreur")
    with pytest.raises(Arret) as e:
        deployer.promouvoir(NOM, "m", max_.environ, max_.outils)
    assert e.value.code == Code.PACKAGING


def test_une_api_muette_rend_80_apres_le_delai(max_: Monde):
    del max_.http.reponses["http://127.0.0.1:8011/v1/sante"]
    with pytest.raises(Arret) as e:
        deployer.promouvoir(NOM, "m", max_.environ, max_.outils)
    assert e.value.code == Code.PACKAGING and "ne répond pas" in e.value.motif


def test_une_verification_non_tenue_rend_70(max_: Monde):
    max_.lanceur.reponses["11.egalite"] = Sortie(1, "1 failed", "AssertionError")
    with pytest.raises(Arret) as e:
        deployer.promouvoir(NOM, "m", max_.environ, max_.outils)
    assert e.value.code == Code.MESURE
    assert max_.lanceur.noms()[-1] == "09.instances.arret"


def test_le_retour_arriere_verifie_l_encodage_2_par_le_rejeu(max_: Monde):
    max_.http.reponses["http://127.0.0.1:8011/v1/info"] = (200, '{"encodage_id": 2}')
    code = deployer.revenir(
        "Retour arrière réel en préproduction.", max_.environ, max_.outils
    )
    assert code == Code.PROMOTION_PROPOSEE
    revenir = max_.lanceur.une("10.journal.revenir").argv
    assert (
        revenir[revenir.index("--motif") + 1] == "Retour arrière réel en préproduction."
    )
    rejeu = max_.lanceur.une("10.executer.rejeu").argv
    assert "28aa0e60e8014b078bf48f5c7c2ce78a" in rejeu[rejeu.index("--rejeu-de") + 1]
    assert rejeu[rejeu.index("--encodage") + 1] == "2"
    assert "11.egalite" not in max_.lanceur.noms()
    assert (
        json.loads(deployer.JOURNAL.read_text("utf-8").splitlines()[-1])["geste"]
        == "revenir"
    )


def test_un_retour_arriere_refuse_par_le_module_10_rend_20(max_: Monde):
    max_.lanceur.reponses["10.journal.revenir"] = Sortie(1, "", "DecisionRefusee")
    with pytest.raises(Arret) as e:
        deployer.revenir("m", max_.environ, max_.outils)
    assert e.value.code == Code.PREREQUIS


def test_un_rejeu_non_identique_apres_le_retour_rend_70(max_: Monde):
    max_.http.reponses["http://127.0.0.1:8011/v1/info"] = (200, '{"encodage_id": 2}')
    max_.lanceur.reponses["10.executer.rejeu"] = rejeu_rend(False)
    with pytest.raises(Arret) as e:
        deployer.revenir("m", max_.environ, max_.outils)
    assert e.value.code == Code.MESURE


def test_un_retour_vers_un_encodage_sans_verification_rend_70(max_: Monde):
    max_.lanceur.reponses["10.journal.revenir"] = Sortie(
        0, '{"apres": {"encodage_id": 1}}'
    )
    max_.http.reponses["http://127.0.0.1:8011/v1/info"] = (200, '{"encodage_id": 1}')
    with pytest.raises(Arret) as e:
        deployer.revenir("m", max_.environ, max_.outils)
    assert e.value.code == Code.MESURE


def test_l_etat_montre_le_journal_avec_ses_motifs_et_les_livraisons(max_: Monde):
    journal = {
        "en_service": {"encodage_id": 3},
        "journal": [
            {
                "promotion_id": 3,
                "decide_le": "2026-11-07 04:00:00+01",
                "decision": "retour_arriere",
                "encodage_id": 2,
                "encodage_precedent_id": 3,
                "run_id": None,
                "decide_par": "Max",
                "derogation": False,
            }
        ],
    }
    max_.lanceur.reponses |= {
        "10.journal.etat": Sortie(0, json.dumps(journal, indent=1)),
        "psql.motifs": Sortie(0, "3\tRetour arrière réel, prévu.\n"),
    }
    assert deployer.etat(max_.environ, max_.outils) == Code.PROMOTION_PROPOSEE
    texte = "\n".join(max_.ecran)
    assert "encodage en service : 3" in texte
    assert "retour_arriere · encodage 2 (précédent 3)" in texte
    assert "Retour arrière réel, prévu." in texte
    assert f"{NOM}.json : promotion proposée" in texte


def test_un_etat_illisible_rend_20(max_: Monde):
    max_.lanceur.reponses["10.journal.etat"] = Sortie(1, "", "erreur")
    with pytest.raises(Arret):
        deployer.etat(max_.environ, max_.outils)


@pytest.mark.parametrize(
    "texte",
    [
        "API_MODELE_PROFIL_ACTIF=x\n",
        "API_MODELE_CLES=\n",
        "API_MODELE_CLES=sans_deux_points\n",
    ],
)
def test_une_cle_absente_rend_20(tmp_path: Path, texte: str):
    env = tmp_path / "env"
    env.write_text(texte)
    with pytest.raises(Arret) as e:
        deployer.cle_api(env)
    assert e.value.code == Code.PREREQUIS


def test_un_env_absent_rend_20(tmp_path: Path):
    with pytest.raises(Arret):
        deployer.cle_api(tmp_path / "absent")


def test_la_cle_se_lit_sous_ses_formes(tmp_path: Path):
    env = tmp_path / "env"
    env.write_text('export API_MODELE_CLES="app:secret1, b:secret2"\n')
    assert deployer.cle_api(env) == "secret1"
