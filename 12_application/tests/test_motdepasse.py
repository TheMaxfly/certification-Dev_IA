"""Argon2id (E4, lot 1, décision 6) : les paramètres par défaut de la bibliothèque en
production, la vérification, le nouveau hachage quand les paramètres changent, et la
vérification factice d'un pseudo inconnu."""

from __future__ import annotations

from argon2 import PasswordHasher, Type
from conftest import HACHEUR_RAPIDE

from application.motdepasse import Hacheur

MOT_DE_PASSE = "une phrase de passe factice"


def test_la_production_prend_les_parametres_par_defaut_d_argon2id():
    defauts = PasswordHasher()
    parametres = Hacheur().parametres()

    assert parametres == {
        "type": "ID",
        "temps": defauts.time_cost,
        "memoire_kio": defauts.memory_cost,
        "parallelisme": defauts.parallelism,
        "longueur_empreinte": defauts.hash_len,
        "longueur_sel": defauts.salt_len,
    }
    assert defauts.type is Type.ID
    # Les valeurs écrites au README (argon2-cffi 25.1.0, profil RFC 9106 « bas »).
    assert (
        parametres["temps"],
        parametres["memoire_kio"],
        parametres["parallelisme"],
    ) == (
        3,
        65536,
        4,
    )


def test_un_mot_de_passe_haché_se_vérifie_et_un_autre_non():
    empreinte = HACHEUR_RAPIDE.hacher(MOT_DE_PASSE)

    assert empreinte.startswith("$argon2id$")
    assert MOT_DE_PASSE not in empreinte
    assert HACHEUR_RAPIDE.verifier(empreinte, MOT_DE_PASSE)
    assert not HACHEUR_RAPIDE.verifier(empreinte, MOT_DE_PASSE + "x")


def test_deux_hachages_du_même_mot_de_passe_diffèrent_par_leur_sel():
    assert HACHEUR_RAPIDE.hacher(MOT_DE_PASSE) != HACHEUR_RAPIDE.hacher(MOT_DE_PASSE)


def test_une_empreinte_illisible_ne_se_vérifie_pas_et_ne_lève_rien():
    assert not HACHEUR_RAPIDE.verifier("pas une empreinte", MOT_DE_PASSE)


def test_des_paramètres_changés_demandent_un_nouveau_hachage():
    ancienne = HACHEUR_RAPIDE.hacher(MOT_DE_PASSE)
    production = Hacheur()

    assert production.doit_rehacher(ancienne)
    assert not HACHEUR_RAPIDE.doit_rehacher(ancienne)


def test_la_vérification_factice_fait_le_travail_et_rend_toujours_faux():
    hacheur = Hacheur(PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))
    assert hacheur._reference is None  # rien n'est calculé avant le démarrage

    hacheur.preparer()
    reference = hacheur._reference
    hacheur.preparer()  # une seule fois

    assert reference is not None and reference.startswith("$argon2id$")
    assert hacheur._reference == reference
    assert hacheur.verifier_factice(MOT_DE_PASSE) is False


def test_la_vérification_factice_se_prépare_seule_au_besoin():
    hacheur = Hacheur(PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))
    assert hacheur.verifier_factice(MOT_DE_PASSE) is False
    assert hacheur._reference is not None
