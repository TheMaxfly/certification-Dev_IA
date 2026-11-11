"""Construire à l'étiquette : une étiquette = une construction."""

from __future__ import annotations

import pytest
from conftest import E3, E4, Poste, pyproject

from livraison import images
from livraison.codes import Arret, Code
from livraison.outils import Outils


def refs(liste: list[images.Image]) -> list[str]:
    return [i.ref for i in liste]


def test_les_images_d_une_etiquette_viennent_de_ses_pyproject(o: Outils):
    assert refs(images.images_de(o, E4)) == [
        "api-modele:1.5.0",
        "api-catalogue:0.7.0",
        "application-conseiller:0.9.1",
    ]
    # À chaine-livraison, pas de catalogue dans la pile, quelle que soit la version
    # du 02.
    assert refs(images.images_de(o, E3)) == [
        "api-modele:1.4.0",
        "application-conseiller:0.1.0",
    ]


def test_un_compose_et_un_pyproject_en_desaccord_sont_refuses(o: Outils, poste: Poste):
    poste.fichiers[E4]["12_application/pyproject.toml"] = pyproject("0.9.0")
    with pytest.raises(Arret, match="le Compose désigne 0.9.1, le pyproject 0.9.0"):
        images.images_de(o, E4)


@pytest.mark.parametrize(
    ("absent", "motif"),
    [
        ("12_application/compose.yaml", "compose.yaml absent"),
        ("11_api_modele/pyproject.toml", "pyproject.toml absent"),
    ],
)
def test_un_fichier_absent_a_l_etiquette_est_refuse(
    o: Outils, poste: Poste, absent, motif
):
    del poste.fichiers[E4][absent]
    with pytest.raises(Arret, match=motif):
        images.images_de(o, E4)


def test_un_pyproject_sans_version_est_refuse():
    with pytest.raises(Arret, match="sans version"):
        images.version_du_projet('[project]\nname = "x"\n')


def test_la_commande_de_construction(o: Outils):
    app = images.images_de(o, E4)[2]
    argv = images.commande_construction(app, o.racine).argv
    assert argv == (
        "docker",
        "build",
        "-f",
        str(o.racine / "12_application/Dockerfile"),
        "--target",
        "application",
        "-t",
        "application-conseiller:0.9.1-candidat",
        str(o.racine / "."),
    )
    cat = images.images_de(o, E4)[1]
    assert "--target" not in images.commande_construction(cat, o.racine).argv


def rm(poste: Poste) -> list[str]:
    return [a[3] for a in poste.argvs() if a[:3] == ("docker", "image", "rm")]


def tags(poste: Poste) -> list[str]:
    return [a[3] for a in poste.argvs() if a[:2] == ("docker", "tag")]


def test_meme_empreinte_rien_ne_change(o: Outils, poste: Poste):
    faites = images.construire(o, images.images_de(o, E4))
    assert [f.statut for f in faites] == ["inchangée"] * 3
    assert tags(poste) == []
    assert all(r.endswith("-candidat") for r in rm(poste)) and len(rm(poste)) == 3


def test_absente_l_image_est_etiquetee(o: Outils, poste: Poste):
    del poste.images["api-catalogue:0.7.0"]
    poste.construction["api-catalogue:0.7.0"] = "sha256:neuve"
    faites = images.construire(o, images.images_de(o, E4))
    assert [f.statut for f in faites] == ["inchangée", "construite", "inchangée"]
    assert tags(poste) == ["api-catalogue:0.7.0"]
    assert poste.images["api-catalogue:0.7.0"] == "sha256:neuve"


def test_une_autre_empreinte_est_refusee_avant_toute_reecriture(
    o: Outils, poste: Poste
):
    poste.construction["application-conseiller:0.9.1"] = "sha256:" + "9" * 64
    avant = poste.images["application-conseiller:0.9.1"]
    with pytest.raises(Arret, match="une étiquette = une construction") as e:
        images.construire(o, images.images_de(o, E4))
    assert e.value.code == Code.PREREQUIS
    assert "--reconstruire" in e.value.motif
    assert tags(poste) == []
    assert poste.images["application-conseiller:0.9.1"] == avant
    assert all(r.endswith("-candidat") for r in rm(poste))


def test_reconstruire_reecrit_et_le_dit(o: Outils, poste: Poste):
    ancienne = poste.images["application-conseiller:0.9.1"]
    poste.construction["application-conseiller:0.9.1"] = "sha256:" + "9" * 64
    faites = images.construire(o, images.images_de(o, E4), reconstruire=True)
    assert faites[2].statut == f"réécrite (ancienne empreinte {ancienne})"
    assert faites[2].empreinte == "sha256:" + "9" * 64
    assert tags(poste) == ["application-conseiller:0.9.1"]


def test_une_construction_en_echec(o: Outils, poste: Poste):
    poste.build_code = 1
    with pytest.raises(Arret, match="construction de api-modele:1.5.0 en échec") as e:
        images.construire(o, images.images_de(o, E4))
    assert e.value.code == Code.CONSTRUCTION


def test_a_blanc_rien_n_est_lance(o: Outils, poste: Poste, ecran):
    liste = images.images_de(o, E4)
    poste.appels.clear()
    assert images.construire(o, liste, a_blanc=True) == []
    assert poste.appels == []
    assert any("docker build" in ligne for ligne in ecran)


def test_jamais_une_image_livree_supprimee(o: Outils, poste: Poste):
    """Les images de l'oral d'E3 (et toute autre) ne sont jamais retirées : seules les
    étiquettes provisoires le sont."""
    poste.construction["api-modele:1.5.0"] = "sha256:autre"
    with pytest.raises(Arret):
        images.construire(o, images.images_de(o, E4))
    images.construire(o, images.images_de(o, E3))
    assert rm(poste) and all(r.endswith(images.SUFFIXE_CANDIDAT) for r in rm(poste))
    assert (
        "api-modele:1.4.0" in poste.images
        and "application-conseiller:0.1.0" in poste.images
    )


def test_presentes_ou_refus(o: Outils, poste: Poste):
    liste = images.images_de(o, E4)
    assert [c.statut for c in images.presentes(o, liste)] == ["présente"] * 3
    del poste.images["api-modele:1.5.0"]
    with pytest.raises(Arret, match="livrer construire"):
        images.presentes(o, liste)


def test_la_construction_notee_revient_au_journal(o: Outils):
    liste = images.images_de(o, E4)
    faites = [
        images.Construite(liste[0], "sha256:" + "1" * 64, "réécrite (ancienne x)")
    ]
    images.noter_construction(o, E4, faites)
    presentes = images.presentes(o, liste)
    lues = images.avec_construction(o, E4, presentes)
    assert [c.statut for c in lues] == ["réécrite (ancienne x)", "présente", "présente"]
    assert images.avec_construction(o, E3, presentes) == presentes
    (o.constructions / f"{E4}.json").write_text("{", encoding="utf-8")
    assert images.avec_construction(o, E4, presentes) == presentes
