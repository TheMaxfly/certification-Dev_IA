"""Le résumé servi d'une série (contrat 0.6.0) : coupé avant le premier extrait de
critique, ramené à la fin de la phrase précédente ; au-dessus du plancher il reste le
principal, sinon le résumé Kitsu, sinon rien. Textes factices."""

from __future__ import annotations

import pytest

from app import resumes
from app.main import get_series
from tests.faux_pool import FakePool
from tests.test_api import LIGNE_SERIE

RESUME = (
    "Un jeune ninja rêve de devenir le chef de son village. Il apprend, il échoue, il "
    "recommence ; ses amis le suivent dans chaque épreuve.\n"
    "Franchement, ce manga est le meilleur que j'aie lu cette année, je le recommande."
)
CRITIQUE = (
    "Franchement, ce manga est le meilleur que j'aie lu cette année, je le recommande."
)
DEBUT = CRITIQUE[:60]
KITSU = "A young ninja dreams of becoming the leader of his village."


def test_la_coupe_tombe_avant_le_premier_extrait_a_la_fin_de_la_phrase() -> None:
    servi = resumes.resume_servi(RESUME, KITSU, [DEBUT], plancher=50)

    assert servi.source == "manga_sanctuary"
    assert servi.texte == RESUME.split("\n")[0]
    assert DEBUT not in servi.texte


def test_la_position_la_plus_basse_l_emporte() -> None:
    autre = "Il apprend, il échoue, il recommence ; ses amis le suivent dans"
    assert len(autre) >= 60

    position = resumes.coupure(RESUME, [DEBUT, autre])

    assert position == RESUME.index("Il apprend")
    assert resumes.couper(RESUME, position) == (
        "Un jeune ninja rêve de devenir le chef de son village."
    )


def test_la_coupe_trouve_un_extrait_malgre_la_casse_et_les_accents() -> None:
    resume = "Résumé factice. " + CRITIQUE.upper().replace("E", "É")

    position = resumes.coupure(resume, [DEBUT])

    assert position == len("Résumé factice. ")


def test_une_ligature_ne_decale_pas_les_positions() -> None:
    """`unaccent` change la longueur (« œ » → « oe ») : la coupe passe par la table du
    lot 2a, un caractère pour un — la position reste celle du texte d'origine."""
    resume = "Un cœur, un œil, une sœur. " + CRITIQUE

    position = resumes.coupure(resume, [DEBUT])

    assert resume[position:].startswith("Franchement")
    assert resumes.couper(resume, position) == "Un cœur, un œil, une sœur."


def test_un_debut_trop_court_n_est_pas_cherche() -> None:
    assert resumes.coupure(RESUME, ["Franchement"]) is None


def test_sans_phrase_avant_l_extrait_rien_ne_reste() -> None:
    assert resumes.couper(CRITIQUE, 0) == ""


@pytest.mark.parametrize(
    ("plancher", "kitsu", "attendu"),
    [
        (50, KITSU, ("manga_sanctuary", "coupé")),
        (500, KITSU, ("kitsu", KITSU)),
        (500, None, (None, None)),
        (500, "   ", (None, None)),
    ],
    ids=[
        "au_dessus_du_plancher",
        "sous_le_plancher_kitsu",
        "ni_l_un_ni_l_autre",
        "vide",
    ],
)
def test_le_plancher_puis_kitsu_puis_rien(
    plancher: int, kitsu: str | None, attendu: tuple[str | None, str | None]
) -> None:
    servi = resumes.resume_servi(RESUME, kitsu, [DEBUT], plancher)

    assert servi.source == attendu[0]
    if attendu[1] == "coupé":
        assert servi.texte is not None and DEBUT not in servi.texte
    else:
        assert servi.texte == attendu[1]


def test_un_resume_sans_extrait_reste_tel_quel_meme_court() -> None:
    servi = resumes.resume_servi("Court.", KITSU, [DEBUT], plancher=100)

    assert (servi.texte, servi.source) == ("Court.", "manga_sanctuary")


@pytest.mark.parametrize("manga_sanctuary", [None, "", "  \n"])
def test_sans_resume_manga_sanctuary_le_resume_kitsu(manga_sanctuary) -> None:
    servi = resumes.resume_servi(manga_sanctuary, KITSU, [], plancher=100)

    assert (servi.texte, servi.source) == (KITSU, "kitsu")


def test_la_fiche_sert_le_resume_coupe_sa_source_et_le_resume_kitsu() -> None:
    ligne = (
        LIGNE_SERIE[:14]
        + (RESUME, "Synopsis retenu")
        + LIGNE_SERIE[16:38]
        + (KITSU, [DEBUT])
    )
    pool = FakePool([ligne])

    reponse = get_series(pool, 8514, plancher=50)

    assert reponse.synopsis == RESUME, "le brut reste au contrat"
    assert reponse.synopsis_enriched == RESUME.split("\n")[0]
    assert (reponse.synopsis_source, reponse.synopsis_kitsu) == (
        "manga_sanctuary",
        KITSU,
    )
    sql, _ = pool.cursor.executions[0]
    assert "manga.work_identity w" in sql and "manga.kitsu_series_core k" in sql
    assert "left(btrim(r.review_body), 60)" in sql and "manga.ms_reviews_all r" in sql


def test_la_fiche_sans_aucun_resume_dit_null_et_null() -> None:
    ligne = LIGNE_SERIE[:14] + (None, None) + LIGNE_SERIE[16:38] + (None, [])

    reponse = get_series(FakePool([ligne]), 8514)

    assert (reponse.synopsis_enriched, reponse.synopsis_source) == (None, None)
    assert reponse.synopsis_kitsu is None


# --------------------------------------------------------------------------- #
#  La seconde coupe aux marques de critique (E4, lot 6, contrat 0.7.0)
# --------------------------------------------------------------------------- #
#: Un résumé d'éditeur factice, sans marque : il fait plus de 100 caractères.
EDITEUR = (
    "Dans une cité portuaire, une apprentie cartographe découvre une carte qui change "
    "chaque nuit. Avec un vieux marin, elle part chercher l'île qu'elle dessine."
)


@pytest.mark.parametrize(
    ("famille", "positif", "negatif"),
    [
        ("tomes_lus", "Lu les tomes 1 à 12 d'une traite.", "Une série en 12 tomes."),
        ("premiere_personne", "J'ai refermé le livre ému.", "Elle referme le livre."),
        ("note_chiffree", "Une note de 17/20 pour finir.", "Le 17 mai, tout bascule."),
        ("adresse_au_lecteur", "Foncez, sans hésiter !", "Ils foncent vers le port."),
        ("appreciation", "Un vrai coup de cœur.", "Un vrai coup de vent."),
    ],
)
def test_chaque_famille_a_son_motif(famille, positif, negatif) -> None:
    assert resumes.familles_touchees(positif) == {famille}
    assert famille not in resumes.familles_touchees(negatif)


def test_l_apostrophe_typographique_et_les_accents_sont_ramenes() -> None:
    assert resumes.familles_touchees("J’ai adoré, CHEF-D’ŒUVRE.") == {
        "premiere_personne",
        "appreciation",
    }


def test_une_seule_famille_ne_coupe_rien() -> None:
    texte = EDITEUR + " Je ne vous en dis pas plus."

    servi = resumes.resume_servi(texte, KITSU, [], plancher=100)

    assert (servi.texte, servi.source) == (texte, "manga_sanctuary")


def test_deux_familles_coupent_a_la_premiere_marque_en_fin_de_phrase() -> None:
    texte = EDITEUR + " Lu les tomes 1 à 12 : excellent, je le recommande."

    servi = resumes.resume_servi(texte, KITSU, [], plancher=100)

    assert resumes.position_premiere_marque(texte) == texte.index("tomes 1")
    assert (servi.texte, servi.source) == (EDITEUR, "manga_sanctuary")


def test_sous_le_plancher_la_seconde_coupe_passe_a_kitsu_sinon_a_rien() -> None:
    texte = "Une carte change chaque nuit. Foncez : un excellent récit d'aventure."

    assert resumes.resume_servi(texte, KITSU, [], plancher=100) == resumes.Resume(
        KITSU, "kitsu"
    )
    assert resumes.resume_servi(texte, None, [], plancher=100) == resumes.Resume(
        None, None
    )


def test_la_premiere_coupe_puis_la_seconde() -> None:
    critique = (
        "Franchement, ce manga est le meilleur que j'aie lu cette année, foncez donc."
    )
    texte = EDITEUR + " Foncez, c'est excellent.\n" + critique

    servi = resumes.resume_servi(texte, KITSU, [critique[:60]], plancher=100)

    assert resumes.coupure(texte, [critique[:60]]) == texte.index("Franchement")
    assert (servi.texte, servi.source) == (EDITEUR, "manga_sanctuary")


def test_la_fiche_sert_le_resume_coupe_aux_marques() -> None:
    texte = EDITEUR + " Lu les tomes 1 à 12 : excellent, je le recommande."
    ligne = (
        LIGNE_SERIE[:14] + (texte, "Synopsis retenu") + LIGNE_SERIE[16:38] + (KITSU, [])
    )

    reponse = get_series(FakePool([ligne]), 8514)

    assert reponse.synopsis == texte, "le brut reste au contrat"
    assert (reponse.synopsis_enriched, reponse.synopsis_source) == (
        EDITEUR,
        "manga_sanctuary",
    )
