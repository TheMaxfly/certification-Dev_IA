"""Les contrôles `disponibilite` et `demographie` : la liste de formes, l'attribution à
une série, ce qui n'est pas une affirmation. Sans base ni fournisseur."""

from __future__ import annotations

import re

import pytest

from api_modele.affirmations import FICHIER, aplatir, charger_formes, contredite
from api_modele.modeles import Metadonnees, Serie


def serie(
    serie_id: int,
    disponibilite: str | None = "edite_en_france",
    demographie: str | None = "Seinen",
) -> Serie:
    return Serie(
        rang=serie_id,
        entite=f"serie:{serie_id}",
        serie_id=serie_id,
        kitsu_id=None,
        titre=f"Série {serie_id}",
        score=0.5,
        passages=[],
        metadonnees=Metadonnees.model_validate(
            dict.fromkeys(Metadonnees.model_fields)
            | {"disponibilite": disponibilite, "demographie": demographie}
        ),
    )


def controler(
    texte: str,
    *series: Serie,
    citees: tuple[int, ...] = (1,),
) -> tuple[str, int] | None:
    return contredite(texte, series or (serie(1),), citees)


JAPON = serie(1, disponibilite="seulement_au_japon")


@pytest.mark.parametrize(
    "texte",
    [
        "Titre [S:1] est disponible en France.",
        "Titre [S:1] est disponible en français.",
        "Titre [S:1] est disponible directement en France.",
        "Titre [S:1] a été publiée en France.",
        "Titre [S:1] est éditée en France.",
        "Titre [S:1] est sortie en France en 2019.",
        "Titre [S:1] est parue en France.",
        "Titre [S:1] est commandable en France.",
        "Titre [S:1] a été traduite en français.",
        "Titre [S:1] existe en français.",
        "Titre [S:1] existe en édition française.",
        "Titre [S:1] a une édition française.",
        # L’apostrophe typographique, les majuscules, les accents.
        "TITRE [S:1] EST ÉDITÉE EN FRANCE.",
        # La forme devant la marque, dans la phrase.
        "Disponible en français, Titre [S:1] plaira.",
        # La forme dans la phrase d'après : la dernière marque d'avant.
        "Titre [S:1] plaira. Elle est éditée en France.",
        # Sans marque, la seule série citée.
        "Elle est publiée en France.",
        # Une réserve dans une autre proposition ne compte pas.
        "Titre [S:1] n'est pas terminée, mais elle est disponible en France.",
    ],
)
def test_une_disponibilite_affirmee(texte):
    assert controler(texte, JAPON) == ("disponibilite", 1)


@pytest.mark.parametrize("ligne", ["seulement_au_japon", "inconnue", None])
def test_les_trois_lignes_qui_contredisent(ligne):
    assert controler(
        "Titre [S:1] est disponible en France.", serie(1, disponibilite=ligne)
    ) == ("disponibilite", 1)


@pytest.mark.parametrize(
    "texte",
    [
        "Titre [S:1] n'est pas disponible en France.",
        "Titre [S:1] n’est pas disponible en France.",
        "Titre [S:1] n'a jamais été publiée en France.",
        "Titre [S:1] n'a aucune édition française.",
        "Aucune édition française de Titre [S:1].",
        "Les passages ne disent pas si Titre [S:1] est disponible en France.",
        "Je ne peux pas garantir que Titre [S:1] soit disponible en France.",
        "Titre [S:1] est-elle disponible en France ?",
        "Il reste à vérifier que Titre [S:1] est éditée en France.",
        "Titre [S:1] pourrait être disponible en France.",
        "Titre [S:1] est seulement au Japon.",
        "Titre [S:1] est disponible au Japon.",
        "Titre [S:1] est une bonne lecture.",
    ],
)
def test_ce_qui_n_est_pas_une_disponibilite_affirmee(texte):
    assert controler(texte, JAPON) is None


def test_une_disponibilite_conforme_a_la_ligne():
    assert controler("Titre [S:1] est disponible en France.", serie(1)) is None


def test_la_disponibilite_se_rapporte_a_la_marque_la_plus_proche():
    japon, france = JAPON, serie(2)
    citees = (1, 2)

    assert (
        controler(
            "Titre [S:1] plaira, et Autre [S:2] est disponible en France.",
            japon,
            france,
            citees=citees,
        )
        is None
    )
    assert controler(
        "Autre [S:2] plaira, et Titre [S:1] est disponible en France.",
        japon,
        france,
        citees=citees,
    ) == ("disponibilite", 1)


@pytest.mark.parametrize(
    ("texte", "citees"),
    [
        # Une marque hors des séries rendues : aucune ligne à confronter.
        ("Titre [S:99] est disponible en France.", (1,)),
        # Aucune marque, plusieurs séries citées : à laquelle ?
        ("Elle est disponible en France.", (1, 2)),
        # Aucune marque, aucune série citée (un refus).
        ("Elle est disponible en France.", ()),
    ],
)
def test_sans_serie_visee_rien_ne_se_controle(texte, citees):
    assert controler(texte, JAPON, citees=citees) is None


@pytest.mark.parametrize(
    "texte",
    [
        "Titre [S:1] est un shōnen.",
        "Titre [S:1] est un shonen.",
        "Titre [S:1] est un shounen.",
        "Titre [S:1] est un shônen.",
        "Titre [S:1] est un excellent shōnen.",
        "Titre [S:1] est un manga shōjo.",
        "Titre [S:1] est une série josei.",
        "Titre [S:1] reste un shōjo.",
        "Il s'agit d'un josei [S:1].",
        "C'est un shoujo [S:1].",
        "Ce shōjo [S:1] plaira.",
        "Cette série josei [S:1] plaira.",
        "Une série shōjo, Titre [S:1], plaira.",
        "Titre [S:1] est destinée à un public josei.",
        "Titre [S:1] — public : Shojo.",
    ],
)
def test_un_public_affirme_different_de_la_ligne(texte):
    assert controler(texte) == ("demographie", 1)


@pytest.mark.parametrize(
    "texte",
    [
        "Titre [S:1] est un seinen.",
        "Titre [S:1] n'est pas un shōnen.",
        "Titre [S:1] n'est ni un shōnen ni un shōjo.",
        "Titre [S:1] est proche d'un shōnen.",
        "Titre [S:1] est un récit proche du shōnen.",
        "Titre [S:1] plaira aux amateurs de shōnen.",
        "Titre [S:1] est un seinen qui plaira aux lecteurs de shōjo.",
        "Comme ce shōnen, Titre [S:1] plaira.",
        "Titre [S:1] est-il un shōnen ?",
        "Titre [S:1] est un shōnen-aï.",
        "Titre [S:1] est un shōjo-aï.",
        "Titre [S:1] mêle shōnen et seinen.",
    ],
)
def test_ce_qui_n_est_pas_un_public_affirme_different(texte):
    assert controler(texte) is None


@pytest.mark.parametrize("ligne", [None, "Yuri", "Yaoi", "Shojo-Aï", "Ecchi-Hentai"])
def test_une_ligne_sans_l_un_des_quatre_publics_ne_contredit_rien(ligne):
    """« Inconnu » ou un autre libellé du catalogue ne dit pas le contraire."""
    assert controler("Titre [S:1] est un seinen.", serie(1, demographie=ligne)) is None


def test_chaque_public_se_rapporte_a_sa_marque():
    seinen, shojo = serie(1), serie(2, demographie="Shojo")
    citees = (1, 2)

    assert (
        controler(
            "Titre [S:1] est un seinen, tandis qu'Autre [S:2] est un shōjo.",
            seinen,
            shojo,
            citees=citees,
        )
        is None
    )
    assert controler(
        "Titre [S:1] est un shōjo, tandis qu'Autre [S:2] est un shōjo.",
        seinen,
        shojo,
        citees=citees,
    ) == ("demographie", 1)


def test_la_disponibilite_d_abord():
    assert controler("Titre [S:1] est un shōnen, disponible en France.", JAPON) == (
        "disponibilite",
        1,
    )


def test_aplatir():
    assert aplatir("L’Été à Shōnen-Aï") == "l'ete a shonen-ai"


def test_la_liste_versionnee_se_lit_et_une_forme_fautive_est_refusee(tmp_path):
    assert set(charger_formes(FICHIER).publics) == {
        "Shonen",
        "Seinen",
        "Shojo",
        "Josei",
    }
    fautif = tmp_path / "formes.toml"
    fautif.write_text(
        FICHIER.read_text(encoding="utf-8").replace("disponible|", "(disponible|", 1),
        encoding="utf-8",
    )
    with pytest.raises(re.error):
        charger_formes(fautif)
