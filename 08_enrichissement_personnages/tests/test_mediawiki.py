"""Resolution des titres MediaWiki, sans reseau.

`_appliquer_resolution` relie ce que l'API rend (normalisation, redirection,
pages) au titre DEMANDE : c'est lui que porte le sitelink, et c'est sous lui que
l'appelant retrouve son article. Les reponses ci-dessous ont la forme de
`action=query&prop=revisions&redirects=1&formatversion=2`.
"""

import pytest

from commun.mediawiki import Article, ClientMediaWiki


def _resultats(*titres: str) -> dict[str, Article]:
    return {t: Article(titre_demande=t) for t in titres}


def test_un_article_present_porte_pageid_et_revid() -> None:
    resultats = _resultats("Naruto")
    donnees = {
        "query": {
            "pages": [{"pageid": 42, "title": "Naruto", "revisions": [{"revid": 9001}]}]
        }
    }
    ClientMediaWiki._appliquer_resolution(donnees, resultats)
    a = resultats["Naruto"]
    assert (a.existe, a.redirige, a.titre_servi) == (True, False, "Naruto")
    assert (a.pageid, a.revid, a.motif_absence) == (42, 9001, None)


def test_normalisation_puis_redirection_remontent_au_titre_demande() -> None:
    """Deux sauts distincts cote API : `one piece` est normalise en `One piece`,
    qui redirige vers `One Piece`. L'article reste range sous le titre demande,
    et les deux titres sont conserves."""
    resultats = _resultats("one piece")
    donnees = {
        "query": {
            "normalized": [{"from": "one piece", "to": "One piece"}],
            "redirects": [{"from": "One piece", "to": "One Piece"}],
            "pages": [
                {"pageid": 7, "title": "One Piece", "revisions": [{"revid": 123}]}
            ],
        }
    }
    ClientMediaWiki._appliquer_resolution(donnees, resultats)
    a = resultats["one piece"]
    assert a.titre_demande == "one piece" and a.titre_servi == "One Piece"
    assert a.redirige and a.existe
    assert (a.pageid, a.revid) == (7, 123)


def test_une_normalisation_seule_n_est_pas_une_redirection() -> None:
    resultats = _resultats("naruto")
    donnees = {
        "query": {
            "normalized": [{"from": "naruto", "to": "Naruto"}],
            "pages": [{"pageid": 1, "title": "Naruto", "revisions": [{"revid": 2}]}],
        }
    }
    ClientMediaWiki._appliquer_resolution(donnees, resultats)
    assert resultats["naruto"].existe and not resultats["naruto"].redirige


def test_une_page_absente_dit_pourquoi() -> None:
    resultats = _resultats("Titre fantome")
    donnees = {"query": {"pages": [{"title": "Titre fantome", "missing": True}]}}
    ClientMediaWiki._appliquer_resolution(donnees, resultats)
    a = resultats["Titre fantome"]
    assert not a.existe and a.motif_absence == "page absente"
    assert a.titre_servi == "Titre fantome"
    assert (a.pageid, a.revid) == (None, None)


def test_une_page_sans_revision_existe_sans_revid() -> None:
    resultats = _resultats("Bleach")
    donnees = {"query": {"pages": [{"pageid": 3, "title": "Bleach"}]}}
    ClientMediaWiki._appliquer_resolution(donnees, resultats)
    assert resultats["Bleach"].existe and resultats["Bleach"].revid is None


def test_une_page_non_demandee_est_ignoree() -> None:
    """L'API ne rend que ce qu'on lui demande ; si elle rendait autre chose, on
    n'inventerait pas d'entree."""
    resultats = _resultats("Naruto")
    donnees = {"query": {"pages": [{"pageid": 9, "title": "Bleach"}]}}
    ClientMediaWiki._appliquer_resolution(donnees, resultats)
    assert set(resultats) == {"Naruto"}
    assert resultats["Naruto"] == Article(titre_demande="Naruto")


@pytest.mark.parametrize("donnees", [{}, {"query": None}, {"query": {"pages": []}}])
def test_une_reponse_vide_ne_touche_a_rien(donnees) -> None:
    resultats = _resultats("Naruto")
    ClientMediaWiki._appliquer_resolution(donnees, resultats)
    assert resultats["Naruto"] == Article(titre_demande="Naruto")


def test_plusieurs_titres_d_un_meme_lot_sont_resolus_chacun() -> None:
    resultats = _resultats("Naruto", "Bleach", "Absent")
    donnees = {
        "query": {
            "redirects": [{"from": "Bleach", "to": "Bleach (manga)"}],
            "pages": [
                {"pageid": 1, "title": "Naruto", "revisions": [{"revid": 10}]},
                {"pageid": 2, "title": "Bleach (manga)", "revisions": [{"revid": 20}]},
                {"title": "Absent", "missing": True},
            ],
        }
    }
    ClientMediaWiki._appliquer_resolution(donnees, resultats)
    assert [(a.existe, a.redirige, a.revid) for a in resultats.values()] == [
        (True, False, 10),
        (True, True, 20),
        (False, False, None),
    ]


@pytest.mark.parametrize("agent", ["", "python-requests/2.32"])
def test_un_user_agent_sans_contact_est_refuse(agent) -> None:
    """Etiquette Wikimedia : un agent generique ou absent est un motif de blocage."""
    with pytest.raises(ValueError, match="User-Agent descriptif"):
        ClientMediaWiki(agent)
