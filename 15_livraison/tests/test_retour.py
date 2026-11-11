"""Le retour arrière : les Compose et images de l'étiquette visée, la santé à ses
versions, et jamais les migrations."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from conftest import E3, SERVIES, Bouclage, Poste

from livraison import deployer, images, retour
from livraison.codes import Arret, Code
from livraison.outils import Outils

MOTIF = "répétition du retour arrière, E4 lot 8"


@pytest.fixture
def vers_e3(poste: Poste, bouclage: Bouclage) -> None:
    poste.apres_up = dict(SERVIES[E3])
    poste.api.update(version_api="1.4.0")
    bouclage.reponses["http://127.0.0.1:8020/sante"] = (
        200,
        json.dumps({"application": "ok", "api": "ok", "version": "0.1.0"}),
    )


def temporaire(tmp_path: Path):
    return lambda: tempfile.TemporaryDirectory(dir=tmp_path)


def test_retour_a_chaine_livraison(o: Outils, poste: Poste, vers_e3, tmp_path):
    assert (
        retour.retour(o, "production", E3, MOTIF, temporaire=temporaire(tmp_path))
        == Code.OK
    )
    composes = [c for c in poste.appels if c.argv[:2] == ("docker", "compose")]
    assert [c.argv[-1] if "down" not in c.argv else "down" for c in composes] == [
        "down",
        "--no-build",
    ]
    for c in composes:
        a = c.argv
        assert a[a.index("--project-directory") + 1] == str(o.racine / "11_api_modele")
        fichiers = [a[i + 1] for i, x in enumerate(a) if x == "-f"]
        assert len(fichiers) == 3 and all(
            not f.startswith(str(o.racine)) for f in fichiers
        )
    # Les fichiers Compose sont ceux de l'étiquette visée.
    montres = [c.argv[2] for c in poste.appels if c.argv[:2] == ("git", "show")]
    assert f"{E3}:11_api_modele/docker-compose.yml" in montres
    (nom,) = [p.name for p in o.journal.glob("2*.md")]
    assert nom == "20261111T100000Z_production_chaine-livraison.md"
    texte = (o.journal / nom).read_text(encoding="utf-8")
    assert f"| Motif | {MOTIF} |" in texte
    assert "Les migrations de la base ne sont pas revenues (30 appliquées" in texte
    images = texte.split("## Images")[1].split("## Migrations")[0]
    assert "| `api-modele:1.4.0` |" in images and "api-catalogue" not in images
    assert "**santé verte**" in texte
    assert "| Quitté | arbre à `e4-livraison`" in texte
    for a in poste.argvs():
        if "migrate.py" in a:
            assert a[-1] == "status"
    assert not list(tmp_path.glob("tmp*"))


def test_une_etiquette_inconnue_est_refusee(o: Outils, poste: Poste):
    with pytest.raises(Arret, match="inconnue") as e:
        retour.retour(o, "production", "v0", MOTIF)
    assert e.value.code == Code.PREREQUIS
    assert not [a for a in poste.argvs() if a[:2] == ("docker", "compose")]


def test_le_motif_est_obligatoire(o: Outils):
    with pytest.raises(Arret, match="motif est obligatoire"):
        retour.retour(o, "production", E3, "  ")


def test_l_autre_cible_en_marche(o: Outils, poste: Poste):
    poste.en_marche = {"senpai-staging": {"api": "x"}}
    with pytest.raises(Arret, match="une cible à la fois"):
        retour.retour(o, "production", E3, MOTIF)


def test_images_absentes_construites_depuis_l_etiquette(
    o: Outils, poste: Poste, vers_e3, tmp_path
):
    del poste.images["application-conseiller:0.1.0"]
    poste.construction["application-conseiller:0.1.0"] = "sha256:" + "6" * 64
    assert (
        retour.retour(o, "production", E3, MOTIF, temporaire=temporaire(tmp_path))
        == Code.OK
    )
    a = poste.argvs()
    ajout = next(x for x in a if x[:3] == ("git", "worktree", "add"))
    assert ajout[-1] == E3
    assert any(x[:3] == ("git", "worktree", "remove") for x in a)
    build = next(x for x in a if x[:2] == ("docker", "build"))
    assert build[build.index("-f") + 1].startswith(ajout[4])
    texte = next(o.journal.glob("2*.md")).read_text(encoding="utf-8")
    assert "construites depuis l'étiquette : application-conseiller:0.1.0" in texte
    assert (
        "| `application-conseiller:0.1.0` | `sha256:" + "6" * 64 + "` | construite |"
        in texte
    )


def test_un_arbre_de_travail_impossible(o: Outils, poste: Poste, tmp_path):
    del poste.images["api-modele:1.4.0"]
    appel = Poste.__call__

    def refuse(c):
        if c.argv[:3] == ("git", "worktree", "add"):
            from livraison.outils import Sortie

            poste.appels.append(c)
            return Sortie(128, "", "refus")
        return appel(poste, c)

    o.lanceur = refuse
    with pytest.raises(Arret, match="arbre de travail") as e:
        retour.retour(o, "production", E3, MOTIF, temporaire=temporaire(tmp_path))
    assert e.value.code == Code.CONSTRUCTION


def test_une_construction_en_echec_retire_l_arbre(o: Outils, poste: Poste, tmp_path):
    del poste.images["api-modele:1.4.0"]
    poste.build_code = 1
    with pytest.raises(Arret, match="en échec"):
        retour.retour(o, "production", E3, MOTIF, temporaire=temporaire(tmp_path))
    assert any(a[:3] == ("git", "worktree", "remove") for a in poste.argvs())


def test_un_compose_absent_a_l_etiquette(o: Outils, poste: Poste, tmp_path):
    del poste.fichiers[E3]["13_monitorage/compose.yaml"]
    with pytest.raises(Arret, match="13_monitorage/compose.yaml absent"):
        retour.retour(o, "production", E3, MOTIF, temporaire=temporaire(tmp_path))


def test_la_sante_rouge_d_un_retour(o: Outils, poste: Poste, tmp_path):
    """La 0.9.1 qui resterait servie au lieu de la 0.1.0 : santé rouge, code 1."""
    assert (
        retour.retour(o, "production", E3, MOTIF, temporaire=temporaire(tmp_path))
        == Code.SANTE_ROUGE
    )


def test_la_soiree_de_livraison_ne_retire_aucune_image_livree(
    o: Outils, poste: Poste, bouclage: Bouclage, tmp_path
):
    """Déployer, arrêter, revenir à `chaine-livraison`, redéployer : `livrer` ne retire
    jamais une image livrée — celles de l'oral d'E3 (`api-modele:1.4.0`,
    `application-conseiller:0.1.0`) restent sur le poste, comme celles d'E4."""
    livrees = set(poste.images)
    assert deployer.deployer(o, "production") == Code.OK
    assert deployer.arreter(o, "production") == Code.OK
    poste.apres_up = dict(SERVIES[E3])
    poste.api.update(version_api="1.4.0")
    sante_e4 = bouclage.reponses["http://127.0.0.1:8020/sante"]
    bouclage.reponses["http://127.0.0.1:8020/sante"] = (
        200,
        json.dumps({"application": "ok", "api": "ok", "version": "0.1.0"}),
    )
    assert (
        retour.retour(o, "production", E3, MOTIF, temporaire=temporaire(tmp_path))
        == Code.OK
    )
    poste.apres_up = None
    poste.api.update(version_api="1.5.0")
    bouclage.reponses["http://127.0.0.1:8020/sante"] = sante_e4
    assert deployer.deployer(o, "production") == Code.OK
    retirees = [a[3] for a in poste.argvs() if a[:3] == ("docker", "image", "rm")]
    assert all(r.endswith(images.SUFFIXE_CANDIDAT) for r in retirees)
    assert livrees <= set(poste.images)
