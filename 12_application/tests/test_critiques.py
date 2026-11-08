"""Les règles et l'accès aux critiques signées (E4, lot 3, spec 01) : la configuration
`[critiques]`, le module `critiques` sur une base jetable (rôle de test membre de
`app_rw` seul), les volumes d'une série par le client du catalogue.

Un test nommé par critère d'acceptation (préfixes `test_l2_`, `test_l3_`, `test_l4_`,
`test_a5_`, `test_v3_`) ; textes factices et neutres, jamais une vraie critique.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Coroutine
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from conftest import ENVIRONNEMENT, HACHEUR_RAPIDE
from doublure_api import CLE_CATALOGUE_ATTENDUE, TOTAL_BEAUCOUP, Doublure

from application import client_catalogue, comptes, critiques
from application.base import Base, BaseIndisponible
from application.client_catalogue import (
    CatalogueIndisponible,
    ClientCatalogue,
    CorpsInattendu,
    Volume,
    libelle_de,
    libelles_de,
)
from application.comptes import Compte
from application.config import (
    TEXTE_CRITIQUE_BASE,
    ConfigurationInvalide,
    DelaisCatalogue,
    charger,
)
from application.critiques import (
    CritiqueIntrouvable,
    CritiqueInvalide,
    DejaPubliee,
    PasLAuteur,
)
from application.metriques import Metriques

RACINE = Path(__file__).resolve().parents[1]
MIGRATION_027 = RACINE.parent / "database" / "migrations" / "027_schema_application.sql"
REGLES = charger(ENVIRONNEMENT).critiques
TEXTE = (
    "Texte factice d'une critique de libraire, assez long pour tenir dans les bornes"
    " de l'application."
)
AUTRE = (
    "Un autre texte factice, modifié, assez long lui aussi pour tenir dans les bornes."
)
TOME = "https://catalogue.invalid/serie/101/volume/1"
LIBELLE = "Tome 1 — Premier tome factice (2001)"


def lancer[T](coro: Callable[[], Coroutine[Any, Any, T]]) -> T:
    return asyncio.run(coro())


@pytest.fixture
def base_critiques(base_app) -> Base:
    return Base(base_app, 2.0, Metriques())


def compte(base: Base, pseudo: str, role: str = "libraire") -> Compte:
    return lancer(
        lambda: comptes.creer(
            base,
            HACHEUR_RAPIDE,
            pseudo=pseudo,
            nom_affiche=f"Nom {pseudo}",
            role=role,  # type: ignore[arg-type]
            mot_de_passe="mot de passe factice 1",
            doit_changer_mdp=False,
            par_compte_id=None,
        )
    )


def publier(
    base: Base,
    auteur: Compte,
    series_id: int = 101,
    volume_url: str | None = None,
    libelle: str | None = None,
    texte: str = TEXTE,
    note: int = 8,
    label: str | None = None,
) -> int:
    return lancer(
        lambda: critiques.publier(
            base,
            auteur,
            series_id,
            volume_url,
            libelle,
            texte,
            note,
            label,
            regles=REGLES,
        )
    )


def modifier(base: Base, auteur: Compte, ident: int, **champs: Any) -> None:
    valeurs = {"texte": AUTRE, "note": 9, "label": "valeur_sure"} | champs
    lancer(lambda: critiques.modifier(base, auteur, ident, **valeurs, regles=REGLES))


def retirer(base: Base, par: Compte, ident: int, en_tant_que: str = "auteur") -> str:
    return lancer(lambda: critiques.retirer(base, par, ident, en_tant_que))  # type: ignore[arg-type]


def lignes(
    dsn: str, requete: str, params: tuple[Any, ...] = ()
) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as cx:
        return cx.execute(requete, params).fetchall()


def etat_des_critiques(dsn: str) -> tuple[list[tuple[Any, ...]], int]:
    """Ce qu'un refus ne doit pas changer : les critiques et le journal des actions."""
    critiques_ = lignes(
        dsn,
        "SELECT id, compte_id, series_id, volume_url, volume_libelle, texte, note,"
        " label, statut, publie_le, modifie_le FROM application.critiques ORDER BY id",
    )
    (journal,) = lignes(dsn, "SELECT count(*) FROM application.journal_actions")[0]
    return critiques_, journal


def journal(dsn: str) -> list[tuple[Any, ...]]:
    return lignes(
        dsn,
        "SELECT action, par_compte_id, objet_type, objet_id, details"
        " FROM application.journal_actions WHERE objet_type = 'critique' ORDER BY id",
    )


# --------------------------------------------------------------------------- #
#  La configuration
# --------------------------------------------------------------------------- #
def test_les_bornes_du_texte_et_la_forme_des_labels_sont_dans_celles_de_la_027():
    sql = MIGRATION_027.read_text(encoding="utf-8")
    bornes = re.search(r"char_length\(texte\) BETWEEN (\d+) AND (\d+)", sql)
    forme = re.search(r"label ~ '\^\[a-z_\]\{(\d+),(\d+)\}\$'", sql)
    assert bornes and forme
    base_min, base_max = int(bornes.group(1)), int(bornes.group(2))

    assert (base_min, base_max) == TEXTE_CRITIQUE_BASE
    assert base_min <= REGLES.texte_min <= REGLES.texte_max <= base_max
    assert (REGLES.texte_min, REGLES.texte_max) == (50, 2000)
    for label in REGLES.labels:
        assert re.fullmatch(r"[a-z_]{{{},{}}}".format(*forme.groups()), label.code)
    assert [label.code for label in REGLES.labels] == [
        "coup_de_coeur",
        "decouverte_du_mois",
        "a_offrir",
        "pour_debuter",
        "valeur_sure",
    ]
    assert REGLES.libelle("coup_de_coeur") == "Coup de cœur"
    assert REGLES.libelle("inconnu") is None


def configuration_avec(tmp_path: Path, avant: str, apres: str) -> list[str]:
    source = (RACINE / "config" / "reglages.toml").read_text(encoding="utf-8")
    assert avant in source
    fichier = tmp_path / "reglages.toml"
    fichier.write_text(source.replace(avant, apres, 1), encoding="utf-8")
    with pytest.raises(ConfigurationInvalide) as refus:
        charger(ENVIRONNEMENT, fichier)
    return refus.value.ecarts


@pytest.mark.parametrize(
    ("avant", "apres", "attendu"),
    [
        (
            '{ code = "a_offrir",',
            '{ code = "coup_de_coeur",',
            "code en double",
        ),
        ('{ code = "a_offrir",', '{ code = "A-offrir",', "code hors forme"),
        ("texte_max = 2000", "texte_max = 20000", "les bornes de la migration 027"),
        ("texte_min = 50", "texte_min = 3000", "les bornes de la migration 027"),
        ('libelle = "À offrir"', 'libelle = ""', "critiques.labels.libelle"),
    ],
    ids=["double", "hors_forme", "max_hors_027", "min_au_dela_du_max", "libelle_vide"],
)
def test_des_labels_ou_des_bornes_fautifs_empechent_de_demarrer(
    tmp_path, avant, apres, attendu
):
    ecarts = configuration_avec(tmp_path, avant, apres)

    assert any(attendu in e for e in ecarts), ecarts


def test_une_liste_de_labels_vide_est_refusee(tmp_path):
    source = (RACINE / "config" / "reglages.toml").read_text(encoding="utf-8")
    debut = source.index("labels = [\n  { code")
    fin = source.index("]\n", debut) + 2
    fichier = tmp_path / "reglages.toml"
    fichier.write_text(
        source[:debut] + "labels = []\n" + source[fin:], encoding="utf-8"
    )

    with pytest.raises(ConfigurationInvalide) as refus:
        charger(ENVIRONNEMENT, fichier)
    assert any("liste non vide" in e for e in refus.value.ecarts)


# --------------------------------------------------------------------------- #
#  Le texte
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("x" * 49, {"texte": "longueur"}),
        ("x" * 50, {}),
        ("x" * 2000, {}),
        ("x" * 2001, {"texte": "longueur"}),
        ("x" * 60 + "\x00", {"texte": "controle"}),
        ("x" * 60 + "\t", {"texte": "controle"}),
        ("x" * 30 + "\r" + "x" * 30, {"texte": "controle"}),
        ("x" * 30 + "\n\n" + "x" * 30, {}),
    ],
    ids=["49", "50", "2000", "2001", "nul", "tabulation", "retour_isole", "lignes"],
)
def test_l2_le_texte_est_borne_et_sans_caractere_de_controle(texte, attendu):
    assert critiques.erreurs(REGLES, texte, 5, None) == attendu


def test_l2_le_texte_est_normalise_et_garde_tel_quel():
    assert critiques.normaliser_texte("  Ligne un\r\nLigne deux\r\n\r\n  ") == (
        "Ligne un\nLigne deux"
    )
    assert critiques.normaliser_texte("<b>balisage</b>") == "<b>balisage</b>"


@pytest.mark.parametrize("note", [-1, 11, None, True, "8", 7.5])
def test_l2_la_note_est_un_entier_de_0_a_10_obligatoire(note):
    assert critiques.erreurs(REGLES, TEXTE, note, None) == {"note": "note"}


def test_l2_le_label_est_facultatif_et_pris_dans_la_liste():
    assert critiques.erreurs(REGLES, TEXTE, 0, None) == {}
    assert critiques.erreurs(REGLES, TEXTE, 10, "valeur_sure") == {}
    assert critiques.erreurs(REGLES, TEXTE, 5, "inconnu") == {"label": "label"}


# --------------------------------------------------------------------------- #
#  L2 — publier
# --------------------------------------------------------------------------- #
def test_l2_publier_sur_la_serie_puis_sur_un_tome(base_critiques, base):
    auteur = compte(base_critiques, "libraire_un")
    serie = publier(base_critiques, auteur, label="coup_de_coeur")
    tome = publier(base_critiques, auteur, volume_url=TOME, libelle=LIBELLE, note=6)

    lues = lignes(
        base,
        "SELECT id, compte_id, series_id, volume_url, volume_libelle, note, label,"
        " statut, publie_le = modifie_le FROM application.critiques ORDER BY id",
    )
    assert lues == [
        (serie, auteur.id, 101, None, None, 8, "coup_de_coeur", "publiee", True),
        (tome, auteur.id, 101, TOME, LIBELLE, 6, None, "publiee", True),
    ]
    assert [(a, p, o, i, d) for a, p, o, i, d in journal(base)] == [
        (
            "critique_publiee",
            auteur.id,
            "critique",
            serie,
            {"series_id": 101, "tome": False, "par": "auteur"},
        ),
        (
            "critique_publiee",
            auteur.id,
            "critique",
            tome,
            {"series_id": 101, "tome": True, "par": "auteur"},
        ),
    ]


def test_l2_plusieurs_tomes_et_plusieurs_libraires_sur_une_serie(base_critiques, base):
    un = compte(base_critiques, "libraire_un")
    deux = compte(base_critiques, "libraire_deux")
    publier(base_critiques, un, volume_url=TOME, libelle=LIBELLE)
    publier(base_critiques, un, volume_url=TOME + "0", libelle="Tome 10")
    publier(base_critiques, deux)
    publier(base_critiques, deux, volume_url=TOME, libelle=LIBELLE)

    (n,) = lignes(base, "SELECT count(*) FROM application.critiques")[0]
    assert n == 4


def test_l2_une_seule_critique_publiee_par_libraire_serie_et_tome(base_critiques, base):
    auteur = compte(base_critiques, "libraire_un")
    premiere = publier(base_critiques, auteur)
    tome = publier(base_critiques, auteur, volume_url=TOME, libelle=LIBELLE)
    avant = etat_des_critiques(base)

    with pytest.raises(DejaPubliee) as serie:
        publier(base_critiques, auteur, note=3)
    with pytest.raises(DejaPubliee) as du_tome:
        publier(base_critiques, auteur, volume_url=TOME, libelle=LIBELLE)

    assert (serie.value.ident, du_tome.value.ident) == (premiere, tome)
    assert serie.value.__cause__ is None and serie.value.__suppress_context__
    assert etat_des_critiques(base) == avant
    assert (
        lancer(
            lambda: critiques.publiee_existante(base_critiques, auteur.id, 101, None)
        )
        == premiere
    )
    assert (
        lancer(
            lambda: critiques.publiee_existante(base_critiques, auteur.id, 102, None)
        )
        is None
    )


def test_l2_le_journal_est_ecrit_dans_la_meme_transaction(
    base_critiques, base, monkeypatch
):
    auteur = compte(base_critiques, "libraire_un")

    async def en_panne(*_: object) -> None:
        raise RuntimeError("panne après l'écriture")

    monkeypatch.setattr(critiques, "_journaliser", en_panne)
    with pytest.raises(RuntimeError):
        publier(base_critiques, auteur)

    assert lignes(base, "SELECT count(*) FROM application.critiques") == [(0,)]
    assert journal(base) == []


def test_l2_un_tome_incomplet_est_refuse(base_critiques, base):
    auteur = compte(base_critiques, "libraire_un")
    for url, libelle in ((TOME, None), (None, LIBELLE), ("", ""), (TOME, "")):
        with pytest.raises(CritiqueInvalide) as refus:
            publier(base_critiques, auteur, volume_url=url, libelle=libelle)
        assert (refus.value.champ, refus.value.motif) == ("volume_url", "tome")
    assert lignes(base, "SELECT count(*) FROM application.critiques") == [(0,)]


def test_l2_une_saisie_hors_regle_est_refusee_sans_ligne(base_critiques, base):
    auteur = compte(base_critiques, "libraire_un")
    for champs, attendu in (
        ({"texte": "x" * 10}, ("texte", "longueur")),
        ({"note": 11}, ("note", "note")),
        ({"label": "inconnu"}, ("label", "label")),
    ):
        with pytest.raises(CritiqueInvalide) as refus:
            publier(base_critiques, auteur, **champs)
        assert (refus.value.champ, refus.value.motif) == attendu
    assert etat_des_critiques(base) == ([], 1)  # la seule ligne : compte_cree


def test_l2_un_label_hors_liste_n_entre_jamais_en_base(base_critiques, base):
    """Mutation E4R-03 : la forme du code tient la contrainte de la 027 ; seule la liste
    de la configuration le refuse."""
    auteur = compte(base_critiques, "libraire_un")
    with pytest.raises(CritiqueInvalide):
        publier(base_critiques, auteur, label="label_hors_liste")
    ident = publier(base_critiques, auteur)
    with pytest.raises(CritiqueInvalide):
        modifier(base_critiques, auteur, ident, label="label_hors_liste")

    assert lignes(base, "SELECT label FROM application.critiques") == [(None,)]


def test_l2_une_violation_de_la_base_devient_une_erreur_nommee(base_critiques, base):
    auteur = compte(base_critiques, "libraire_un")
    larges = replace(REGLES, texte_max=20000)
    with pytest.raises(CritiqueInvalide) as refus:
        lancer(
            lambda: critiques.publier(
                base_critiques,
                auteur,
                101,
                None,
                None,
                "x" * 10001,
                5,
                None,
                regles=larges,
            )
        )

    assert (refus.value.champ, refus.value.motif) == ("texte", "base")
    assert refus.value.__cause__ is None and refus.value.__suppress_context__
    assert "x" * 50 not in str(refus.value)


def test_l2_le_texte_est_garde_tel_quel_balisage_compris(base_critiques, base):
    auteur = compte(base_critiques, "libraire_un")
    texte = "<script>alert(1)</script> " + TEXTE + "\r\n\r\n<b>gras</b> " + TEXTE
    publier(base_critiques, auteur, texte="  " + texte + "  ")

    assert lignes(base, "SELECT texte FROM application.critiques") == [
        (texte.replace("\r\n", "\n"),)
    ]


@pytest.mark.parametrize("role", ["client", "admin"])
def test_l2_seul_un_libraire_publie(base_critiques, base, role):
    autre = compte(base_critiques, f"{role}_un", role)
    with pytest.raises(PasLAuteur):
        publier(base_critiques, autre)
    assert lignes(base, "SELECT count(*) FROM application.critiques") == [(0,)]


# --------------------------------------------------------------------------- #
#  L3 — modifier, retirer
# --------------------------------------------------------------------------- #
def test_l3_l_auteur_modifie_texte_note_et_label_seulement(base_critiques, base):
    auteur = compte(base_critiques, "libraire_un")
    ident = publier(base_critiques, auteur, volume_url=TOME, libelle=LIBELLE)
    modifier(base_critiques, auteur, ident)

    lue = lancer(lambda: critiques.lire(base_critiques, ident))
    assert lue is not None
    assert (lue.texte, lue.note, lue.label, lue.statut) == (
        AUTRE,
        9,
        "valeur_sure",
        "publiee",
    )
    assert (lue.series_id, lue.volume_url, lue.volume_libelle) == (101, TOME, LIBELLE)
    assert lue.modifiee and lue.modifie_le > lue.publie_le
    assert lue.nom_affiche == "Nom libraire_un"
    assert journal(base)[-1] == (
        "critique_modifiee",
        auteur.id,
        "critique",
        ident,
        {"series_id": 101, "tome": True, "par": "auteur"},
    )


def test_l3_un_autre_libraire_ne_modifie_pas_et_la_base_est_inchangee(
    base_critiques, base
):
    """Mutation E4R-01 : la garde d'auteur retirée de la modification."""
    auteur = compte(base_critiques, "libraire_un")
    autre = compte(base_critiques, "libraire_deux")
    ident = publier(base_critiques, auteur)
    avant = etat_des_critiques(base)

    with pytest.raises(PasLAuteur):
        modifier(base_critiques, autre, ident)
    assert etat_des_critiques(base) == avant


def test_a5_un_admin_ne_modifie_aucune_critique_pas_meme_a_son_nom(
    base_critiques, base
):
    """Mutation E4R-08 : la garde contournée pour le rôle admin. La critique est posée à
    son nom directement en base (un compte passé de libraire à admin)."""
    admin = compte(base_critiques, "admin_un", "admin")
    (ident,) = lignes(
        base,
        "INSERT INTO application.critiques (compte_id, series_id, texte, note)"
        " VALUES (%s, 101, %s, 5) RETURNING id",
        (admin.id, TEXTE),
    )[0]
    avant = etat_des_critiques(base)

    with pytest.raises(PasLAuteur):
        modifier(base_critiques, admin, ident)
    assert etat_des_critiques(base) == avant


def test_l3_une_critique_retiree_ou_inconnue_ne_se_modifie_pas(base_critiques, base):
    auteur = compte(base_critiques, "libraire_un")
    ident = publier(base_critiques, auteur)
    retirer(base_critiques, auteur, ident)
    avant = etat_des_critiques(base)

    for cible in (ident, 999_999):
        with pytest.raises(CritiqueIntrouvable):
            modifier(base_critiques, auteur, cible)
    assert etat_des_critiques(base) == avant


def test_l3_retirer_par_l_auteur_pose_le_statut_sans_supprimer(base_critiques, base):
    """Mutation E4R-02 : le retrait devenu une suppression (la ligne doit rester,
    `retiree` ; `app_rw` refuserait d'ailleurs la suppression, 42501)."""
    auteur = compte(base_critiques, "libraire_un")
    ident = publier(base_critiques, auteur)

    assert retirer(base_critiques, auteur, ident) == "fait"
    assert lignes(
        base, "SELECT id, statut, modifie_le > publie_le FROM application.critiques"
    ) == [(ident, "retiree", True)]
    assert journal(base)[-1] == (
        "critique_retiree",
        auteur.id,
        "critique",
        ident,
        {"series_id": 101, "tome": False, "par": "auteur"},
    )


def test_l3_retirer_deux_fois_ne_laisse_qu_une_ligne_de_journal(base_critiques, base):
    auteur = compte(base_critiques, "libraire_un")
    admin = compte(base_critiques, "admin_un", "admin")
    ident = publier(base_critiques, auteur)
    retirer(base_critiques, auteur, ident)

    assert retirer(base_critiques, auteur, ident) == "deja_retiree"
    assert retirer(base_critiques, admin, ident, "admin") == "deja_retiree"
    assert [a for a, *_ in journal(base)] == ["critique_publiee", "critique_retiree"]


def test_l3_apres_retrait_une_nouvelle_critique_sur_le_meme_couple(
    base_critiques, base
):
    auteur = compte(base_critiques, "libraire_un")
    premiere = publier(base_critiques, auteur)
    retirer(base_critiques, auteur, premiere)
    seconde = publier(base_critiques, auteur, note=4)

    assert seconde != premiere
    assert lignes(base, "SELECT id, statut FROM application.critiques ORDER BY id") == [
        (premiere, "retiree"),
        (seconde, "publiee"),
    ]


def test_l3_un_autre_libraire_ne_retire_pas(base_critiques, base):
    auteur = compte(base_critiques, "libraire_un")
    autre = compte(base_critiques, "libraire_deux")
    admin = compte(base_critiques, "admin_un", "admin")
    ident = publier(base_critiques, auteur)
    avant = etat_des_critiques(base)

    with pytest.raises(PasLAuteur):
        retirer(base_critiques, autre, ident)
    with pytest.raises(PasLAuteur):  # un libraire ne modère pas
        retirer(base_critiques, autre, ident, "admin")
    with pytest.raises(PasLAuteur):  # un admin ne retire pas « en auteur »
        retirer(base_critiques, admin, ident, "auteur")
    with pytest.raises(CritiqueIntrouvable):
        retirer(base_critiques, auteur, 999_999)
    assert etat_des_critiques(base) == avant


def test_a5_l_admin_retire_n_importe_laquelle_a_son_nom(base_critiques, base):
    auteur = compte(base_critiques, "libraire_un")
    admin = compte(base_critiques, "admin_un", "admin")
    ident = publier(base_critiques, auteur, volume_url=TOME, libelle=LIBELLE)

    assert retirer(base_critiques, admin, ident, "admin") == "fait"
    assert lignes(base, "SELECT statut FROM application.critiques") == [("retiree",)]
    assert journal(base)[-1] == (
        "critique_retiree",
        admin.id,
        "critique",
        ident,
        {"series_id": 101, "tome": True, "par": "admin"},
    )
    with pytest.raises(CritiqueIntrouvable):
        retirer(base_critiques, admin, 999_999, "admin")


# --------------------------------------------------------------------------- #
#  Lire : la fiche, la moyenne, les listes
# --------------------------------------------------------------------------- #
def test_v3_les_publiees_de_la_serie_les_plus_recentes_d_abord(base_critiques, base):
    un = compte(base_critiques, "libraire_un")
    deux = compte(base_critiques, "libraire_deux")
    premiere = publier(base_critiques, un)
    seconde = publier(base_critiques, deux)
    retiree = publier(base_critiques, deux, volume_url=TOME, libelle=LIBELLE)
    retirer(base_critiques, deux, retiree)
    publier(base_critiques, un, series_id=102)

    lues = lancer(lambda: critiques.publiees_de_la_serie(base_critiques, 101))
    assert [(c.id, c.nom_affiche) for c in lues] == [
        (seconde, "Nom libraire_deux"),
        (premiere, "Nom libraire_un"),
    ]


def test_v3_la_moyenne_exclut_les_retirees_et_s_arrondit_au_dixieme(
    base_critiques, base
):
    """Mutation E4R-04 : la moyenne qui compterait les retirées."""
    un = compte(base_critiques, "libraire_un")
    deux = compte(base_critiques, "libraire_deux")
    trois = compte(base_critiques, "libraire_trois")
    assert lancer(lambda: critiques.moyenne_de_la_serie(base_critiques, 101)) is None
    publier(base_critiques, un, note=9)
    publier(base_critiques, deux, note=6)
    publier(base_critiques, trois, note=4)
    retiree = publier(base_critiques, un, volume_url=TOME, libelle=LIBELLE, note=0)
    retirer(base_critiques, un, retiree)

    moyenne = lancer(lambda: critiques.moyenne_de_la_serie(base_critiques, 101))
    assert moyenne == critiques.MoyenneLibraires(6.3, 3)
    assert lancer(lambda: critiques.moyenne_de_la_serie(base_critiques, 102)) is None


def test_l4_les_miennes_publiees_et_retirees_paginees(base_critiques, base):
    auteur = compte(base_critiques, "libraire_un")
    autre = compte(base_critiques, "libraire_deux")
    idents = [publier(base_critiques, auteur, series_id=s) for s in (101, 102, 103)]
    retirer(base_critiques, auteur, idents[0])
    publier(base_critiques, autre)

    def miennes(page: int, statut: str | None = None):
        return lancer(
            lambda: critiques.les_miennes(
                base_critiques,
                auteur.id,
                page,
                par_page=2,
                statut=statut,  # type: ignore[arg-type]
            )
        )

    page1, total, rendue = miennes(1)
    page2, _, _ = miennes(2)
    assert (total, rendue) == (3, 1)
    # Les plus récemment changées d'abord : la retirée (changée en dernier) en tête.
    assert [c.id for c in page1 + page2] == [idents[0], idents[2], idents[1]]
    assert miennes(99)[2] == 2
    publiees, n, _ = miennes(1, "publiee")
    retirees, m, _ = miennes(1, "retiree")
    assert (n, m) == (2, 1)
    assert {c.statut for c in publiees} == {"publiee"}
    assert [c.id for c in retirees] == [idents[0]]
    vide = lancer(lambda: critiques.les_miennes(base_critiques, 999_999, 1, par_page=2))
    assert vide == ([], 0, 1)


def test_a5_toutes_les_publiees_paginees_et_filtrees_par_libraire(base_critiques, base):
    un = compte(base_critiques, "libraire_un")
    deux = compte(base_critiques, "libraire_deux")
    a = publier(base_critiques, un)
    b = publier(base_critiques, deux)
    c = publier(base_critiques, un, series_id=102)
    retirer(base_critiques, un, a)

    def toutes(page: int, compte_id: int | None = None):
        return lancer(
            lambda: critiques.toutes_publiees(
                base_critiques, page, par_page=1, compte_id=compte_id
            )
        )

    assert [toutes(p)[0][0].id for p in (1, 2)] == [c, b]
    assert toutes(1)[1] == 2
    assert [x.id for x in toutes(1, un.id)[0]] == [c]
    assert toutes(1, un.id)[1] == 1
    libraires = lancer(lambda: critiques.libraires(base_critiques))
    assert [(x.id, x.actif) for x in libraires] == [(deux.id, True), (un.id, True)]


def test_aucun_texte_de_critique_n_entre_au_journal(base_critiques, base):
    auteur = compte(base_critiques, "libraire_un")
    admin = compte(base_critiques, "admin_un", "admin")
    ident = publier(base_critiques, auteur, label="coup_de_coeur")
    modifier(base_critiques, auteur, ident)
    retirer(base_critiques, admin, ident, "admin")

    for _, _, _, _, details in journal(base):
        assert set(details) == {"series_id", "tome", "par"}
        assert TEXTE not in json.dumps(details) and AUTRE not in json.dumps(details)
    tout = json.dumps(
        lignes(base, "SELECT * FROM application.journal_actions"), default=str
    )
    assert TEXTE[:30] not in tout and AUTRE[:30] not in tout


def test_le_module_ne_contient_aucune_suppression():
    source = (RACINE / "src" / "application" / "critiques.py").read_text(
        encoding="utf-8"
    )
    assert "DELETE" not in source.upper().replace("SUPPRIME", "")


def test_la_base_indisponible_se_propage(base_app):
    absente = Base(None, 1.0, Metriques())
    with pytest.raises(BaseIndisponible):
        lancer(lambda: critiques.publiees_de_la_serie(absente, 101))
    with pytest.raises(BaseIndisponible):
        lancer(lambda: critiques.moyenne_de_la_serie(absente, 101))


# --------------------------------------------------------------------------- #
#  Les volumes d'une série, par le catalogue
# --------------------------------------------------------------------------- #
@pytest.fixture
def catalogue(doublure: Doublure) -> ClientCatalogue:
    return ClientCatalogue(
        "http://127.0.0.1:8000",
        CLE_CATALOGUE_ATTENDUE,
        DelaisCatalogue(connexion_s=0.5, reponse_s=0.5),
        Metriques(),
        httpx.ASGITransport(app=doublure.app),
    )


def test_l2_les_volumes_et_leurs_libelles_dans_l_ordre_du_catalogue(catalogue):
    volumes = lancer(lambda: catalogue.volumes(101))

    assert volumes is not None
    assert list(libelles_de(volumes).values()) == [
        "Tome 1 — Premier tome factice (2001)",
        "Tome 1 — Premier tome factice (2010)",
        "Hors-série factice",
    ]


def test_l2_toutes_les_pages_de_volumes_sont_lues(catalogue, doublure):
    doublure.choisir(catalogue="beaucoup")
    volumes = lancer(lambda: catalogue.volumes(101))

    assert volumes is not None and len(volumes) == TOTAL_BEAUCOUP
    assert doublure.lectures_volumes == [(101, 200, 0), (101, 200, 200)]
    assert volumes[0].number == 1 and volumes[-1].number == TOTAL_BEAUCOUP


def test_l2_au_dela_de_la_borne_la_liste_est_tronquee_et_le_journal_le_dit(
    catalogue, doublure, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger="application")
    doublure.choisir(catalogue="beaucoup")
    monkeypatch.setattr(client_catalogue, "VOLUMES_MAX", 100)
    monkeypatch.setattr(client_catalogue, "VOLUMES_PAR_APPEL", 60)
    volumes = lancer(lambda: catalogue.volumes(101))

    assert volumes is not None and len(volumes) == 100
    assert "catalogue_volumes_tronques" in [r.getMessage() for r in caplog.records]


def test_l2_sans_volume_une_liste_vide_et_une_serie_inconnue_none(catalogue, doublure):
    doublure.choisir(catalogue="sans_volume")
    assert lancer(lambda: catalogue.volumes(101)) == []
    doublure.choisir(catalogue="serie_inconnue")
    assert lancer(lambda: catalogue.volumes(101)) is None


@pytest.mark.parametrize(
    ("scenario", "erreur"),
    [
        ("indisponible", CatalogueIndisponible),
        ("cle_refusee", CatalogueIndisponible),
        ("corps_hors_contrat", CorpsInattendu),
    ],
)
def test_un_catalogue_en_echec_sur_les_volumes_se_nomme(
    catalogue, doublure, scenario, erreur
):
    doublure.choisir(catalogue=scenario)
    with pytest.raises(erreur):
        lancer(lambda: catalogue.volumes(101))


@pytest.mark.parametrize(
    ("numero", "titre", "annee", "avec_annee", "attendu"),
    [
        (3, "Un titre", 2001, False, "Tome 3 — Un titre"),
        (3, "Un titre", 2001, True, "Tome 3 — Un titre (2001)"),
        (3, None, None, True, "Tome 3"),
        (None, "Hors-série", 2001, False, "Hors-série"),
        (None, None, 2004, False, "Volume sans titre (2004)"),
        (None, "  ", None, False, "Volume sans titre"),
    ],
)
def test_l2_le_libelle_d_un_volume(numero, titre, annee, avec_annee, attendu):
    volume = Volume(
        volume_url="https://catalogue.invalid/v",
        title=titre,
        number=numero,
        publication_date=None if annee is None else f"{annee}-01-01",  # type: ignore[arg-type]
    )
    assert libelle_de(volume, avec_annee=avec_annee) == attendu


def test_l2_deux_editions_d_un_meme_numero_la_meme_annee_restent_sans_annee():
    volumes = [
        Volume(volume_url=f"u{i}", title="T", number=1, publication_date=None)
        for i in range(2)
    ]
    assert list(libelles_de(volumes).values()) == ["Tome 1 — T", "Tome 1 — T"]
