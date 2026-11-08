"""Les comptes et la connexion (E4, lot 1, spec 02, § 9) sur une base jetable, sous le
rôle de test membre de `app_rw` : création et journal, refus au motif borné,
vérification factice d'un pseudo inconnu, verrou au cinquième échec, fin du verrou,
nouveau hachage quand les paramètres changent."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import psycopg
import pytest
from argon2 import PasswordHasher
from conftest import HACHEUR_RAPIDE

from application import comptes
from application.base import Base
from application.comptes import Compte, PseudoPris, Refus
from application.metriques import Metriques
from application.motdepasse import Hacheur

MOT_DE_PASSE = "mot de passe factice"
SEUIL, VERROU_S = 5, 900


class Espion(Hacheur):
    """Le hacheur rapide, qui compte ses vérifications (vraies et factices)."""

    def __init__(self) -> None:
        super().__init__(HACHEUR_RAPIDE.moteur)
        self.verifications = 0
        self.factices = 0

    def verifier(self, empreinte: str, mot_de_passe: str) -> bool:
        self.verifications += 1
        return super().verifier(empreinte, mot_de_passe)

    def verifier_factice(self, mot_de_passe: str) -> bool:
        self.factices += 1
        return super().verifier_factice(mot_de_passe)


def lancer[T](coro: Callable[[], Coroutine[Any, Any, T]]) -> T:
    return asyncio.run(coro())


@pytest.fixture
def base_comptes(base_app) -> Base:
    return Base(base_app, 2.0, Metriques())


def creer(base: Base, pseudo: str = "client_essai", role: str = "client") -> Compte:
    return lancer(
        lambda: comptes.creer(
            base,
            HACHEUR_RAPIDE,
            pseudo=pseudo,
            nom_affiche=f"Nom {pseudo}",
            role=role,  # type: ignore[arg-type]
            mot_de_passe=MOT_DE_PASSE,
            doit_changer_mdp=False,
            par_compte_id=None,
        )
    )


def connecter(base: Base, hacheur: Hacheur, pseudo: str, mot_de_passe: str):
    return lancer(
        lambda: comptes.authentifier(
            base,
            hacheur,
            pseudo=pseudo,
            mot_de_passe=mot_de_passe,
            seuil=SEUIL,
            duree_verrou_s=VERROU_S,
        )
    )


def etat(dsn: str, ident: int) -> tuple[int, bool, str]:
    with psycopg.connect(dsn) as cx:
        return cx.execute(
            "SELECT echecs_connexion, verrou_jusqu_a IS NOT NULL, hachage"
            " FROM application.comptes WHERE id = %s",
            (ident,),
        ).fetchone()


def changer(base: Base, ident: int, ancien: str, nouveau: str = "un autre factice"):
    return lancer(
        lambda: comptes.changer_mot_de_passe(
            base,
            HACHEUR_RAPIDE,
            ident,
            ancien,
            nouveau,
            seuil=SEUIL,
            duree_verrou_s=VERROU_S,
        )
    )


def lignes_du_journal(dsn: str) -> list[tuple[object, ...]]:
    with psycopg.connect(dsn) as cx:
        return cx.execute(
            "SELECT action, par_compte_id, objet_id"
            " FROM application.journal_actions ORDER BY id"
        ).fetchall()


# --------------------------------------------------------------------------- #
#  Création et lecture
# --------------------------------------------------------------------------- #
def test_un_compte_cree_est_journalise_par_identifiants(base_comptes, base):
    compte = creer(base_comptes)

    assert compte.role == "client" and compte.actif and compte.generation == 0
    assert lancer(lambda: comptes.lire(base_comptes, compte.id)) == compte
    with psycopg.connect(base) as cx:
        journal = cx.execute(
            "SELECT par_compte_id, action, objet_type, objet_id, details"
            " FROM application.journal_actions"
        ).fetchall()
        (empreinte,) = cx.execute("SELECT hachage FROM application.comptes").fetchone()
    assert journal == [(None, "compte_cree", "compte", compte.id, {})]
    assert empreinte.startswith("$argon2id$") and MOT_DE_PASSE not in empreinte


def test_un_pseudo_deja_pris_est_refuse_par_une_erreur_nommee(base_comptes):
    creer(base_comptes)
    with pytest.raises(PseudoPris):
        creer(base_comptes)


def test_un_compte_inconnu_se_lit_none(base_comptes):
    assert lancer(lambda: comptes.lire(base_comptes, 999_999)) is None


# --------------------------------------------------------------------------- #
#  La connexion et son verrou
# --------------------------------------------------------------------------- #
def test_le_bon_mot_de_passe_connecte_et_remet_le_compteur_a_zero(
    base_comptes, base_app
):
    compte = creer(base_comptes)
    connecter(base_comptes, HACHEUR_RAPIDE, "client_essai", "faux")
    assert etat(base_app, compte.id)[0] == 1

    resultat = connecter(base_comptes, HACHEUR_RAPIDE, "client_essai", MOT_DE_PASSE)

    assert resultat == compte
    assert etat(base_app, compte.id)[:2] == (0, False)


def test_un_pseudo_inconnu_passe_par_la_verification_factice(base_comptes):
    espion = Espion()
    resultat = connecter(base_comptes, espion, "inconnu_essai", MOT_DE_PASSE)

    assert resultat == Refus("mot_de_passe", None)
    assert (espion.factices, espion.verifications) == (1, 1)


def test_cinq_echecs_posent_le_verrou_et_le_bon_mot_de_passe_est_refuse(
    base_comptes, base_app
):
    compte = creer(base_comptes)
    resultats = [
        connecter(base_comptes, HACHEUR_RAPIDE, "client_essai", f"faux {i}")
        for i in range(SEUIL)
    ]

    assert [r.motif for r in resultats] == ["mot_de_passe"] * SEUIL
    assert [r.verrou_pose for r in resultats] == [False] * (SEUIL - 1) + [True]
    assert etat(base_app, compte.id)[:2] == (0, True)

    espion = Espion()
    pendant = connecter(base_comptes, espion, "client_essai", MOT_DE_PASSE)
    assert pendant == Refus("verrou", compte.id)
    assert espion.verifications == 1, "le temps de réponse trahirait le verrou"
    assert etat(base_app, compte.id)[:2] == (0, True), (
        "un refus pendant le verrou compte"
    )


def test_a_la_fin_du_verrou_la_connexion_repart(base_comptes, base_app):
    compte = creer(base_comptes)
    for i in range(SEUIL):
        connecter(base_comptes, HACHEUR_RAPIDE, "client_essai", f"faux {i}")
    with psycopg.connect(base_app) as cx:
        cx.execute(
            "UPDATE application.comptes"
            " SET verrou_jusqu_a = now() - interval '1 second'"
        )

    assert (
        connecter(base_comptes, HACHEUR_RAPIDE, "client_essai", MOT_DE_PASSE) == compte
    )
    assert etat(base_app, compte.id)[:2] == (0, False)


def test_un_compte_inactif_est_refuse_meme_avec_le_bon_mot_de_passe(
    base_comptes, base_app
):
    compte = creer(base_comptes)
    with psycopg.connect(base_app) as cx:
        cx.execute("UPDATE application.comptes SET actif = false")

    assert connecter(
        base_comptes, HACHEUR_RAPIDE, "client_essai", MOT_DE_PASSE
    ) == Refus("inactif", compte.id)


def test_des_parametres_changes_rehachent_a_la_connexion(base_comptes, base_app):
    compte = creer(base_comptes)
    avant = etat(base_app, compte.id)[2]
    autres = Hacheur(PasswordHasher(time_cost=2, memory_cost=8, parallelism=1))

    assert connecter(base_comptes, autres, "client_essai", MOT_DE_PASSE) == compte

    apres = etat(base_app, compte.id)[2]
    assert apres != avant and "t=2" in apres and "t=1" in avant
    assert connecter(base_comptes, autres, "client_essai", MOT_DE_PASSE) == compte


# --------------------------------------------------------------------------- #
#  Même travail, quel que soit le pseudo ; aucun verrou tenu pendant le calcul
# --------------------------------------------------------------------------- #
class Sonde(Espion):
    """Pendant la vérification, une autre connexion essaie de verrouiller la ligne
    (`NOWAIT`) — elle échouerait si la connexion la tenait — puis joue `geste`."""

    def __init__(self, dsn: str, geste: str | None = None) -> None:
        super().__init__()
        self.dsn, self.geste, self.ligne_libre = dsn, geste, False

    def verifier(self, empreinte: str, mot_de_passe: str) -> bool:
        with psycopg.connect(self.dsn) as cx:
            cx.execute(
                "SELECT id FROM application.comptes WHERE pseudo = 'client_essai'"
                " FOR UPDATE NOWAIT"
            )
            self.ligne_libre = True
        if self.geste:
            with psycopg.connect(self.dsn) as cx:
                cx.execute(self.geste)
        return super().verifier(empreinte, mot_de_passe)


def test_aucune_ligne_n_est_verrouillee_pendant_la_verification(base_comptes, base_app):
    creer(base_comptes)
    sonde = Sonde(base_app)

    assert isinstance(
        connecter(base_comptes, sonde, "client_essai", MOT_DE_PASSE), Compte
    )
    assert sonde.ligne_libre


@pytest.mark.parametrize(
    ("geste", "attendu"),
    [
        (
            "UPDATE application.comptes SET verrou_jusqu_a = now() + interval '1 hour'",
            "verrou",
        ),
        ("UPDATE application.comptes SET actif = false", "verrou"),
    ],
    ids=["verrouillé pendant la vérification", "désactivé pendant la vérification"],
)
def test_un_changement_pendant_la_verification_l_emporte(
    base_comptes, base_app, geste, attendu
):
    compte = creer(base_comptes)

    resultat = connecter(
        base_comptes, Sonde(base_app, geste), "client_essai", MOT_DE_PASSE
    )

    assert resultat == Refus(attendu, compte.id)


def test_un_echec_sur_un_compte_verrouille_entre_temps_ne_compte_pas(
    base_comptes, base_app
):
    compte = creer(base_comptes)
    geste = "UPDATE application.comptes SET verrou_jusqu_a = now() + interval '1 hour'"

    resultat = connecter(base_comptes, Sonde(base_app, geste), "client_essai", "faux")

    assert resultat == Refus("verrou", compte.id)
    assert etat(base_app, compte.id)[0] == 0


def test_tous_les_chemins_font_deux_transactions(base_comptes, base_app, monkeypatch):
    compte = creer(base_comptes)
    creer(base_comptes, "inactif_essai")
    with psycopg.connect(base_app) as cx:
        cx.execute(
            "UPDATE application.comptes SET actif = false WHERE pseudo ="
            " 'inactif_essai'"
        )
    transactions: list[int] = []
    ouvrir = base_comptes.transaction

    def compter():
        transactions[-1] += 1
        return ouvrir()

    monkeypatch.setattr(base_comptes, "transaction", compter)
    cas = [
        ("inconnu_essai", MOT_DE_PASSE),
        ("inactif_essai", MOT_DE_PASSE),
        ("client_essai", "faux"),
        ("client_essai", MOT_DE_PASSE),
    ]
    for pseudo, mot_de_passe in cas:
        transactions.append(0)
        connecter(base_comptes, HACHEUR_RAPIDE, pseudo, mot_de_passe)
    transactions.append(0)  # la préparation du verrou : hors du compte
    for i in range(SEUIL):
        connecter(base_comptes, HACHEUR_RAPIDE, "client_essai", f"faux {i}")
    transactions[-1] = 0
    verrouille = connecter(base_comptes, HACHEUR_RAPIDE, "client_essai", MOT_DE_PASSE)

    assert verrouille == Refus("verrou", compte.id)
    assert transactions == [2, 2, 2, 2, 2]


# --------------------------------------------------------------------------- #
#  La création : pseudo normalisé, refus nommés sans valeur
# --------------------------------------------------------------------------- #
def test_le_pseudo_est_ramene_en_minuscules_et_l_inscription_s_attribue_le_compte(
    base_comptes, base_app
):
    compte = lancer(
        lambda: comptes.creer(
            base_comptes,
            HACHEUR_RAPIDE,
            pseudo="  Client_Essai ",
            nom_affiche="client_essai",
            role="client",
            mot_de_passe=MOT_DE_PASSE,
            doit_changer_mdp=False,
            par_compte_id="soi",
        )
    )
    with psycopg.connect(base_app) as cx:
        (pseudo,) = cx.execute("SELECT pseudo FROM application.comptes").fetchone()
        (auteur,) = cx.execute(
            "SELECT par_compte_id FROM application.journal_actions"
        ).fetchone()

    assert pseudo == "client_essai" and auteur == compte.id
    assert (
        connecter(base_comptes, HACHEUR_RAPIDE, "CLIENT_ESSAI", MOT_DE_PASSE) == compte
    )


@pytest.mark.parametrize(
    ("pseudo", "nom", "role"),
    [
        ("ab", "Nom", "client"),
        ("avec espace", "Nom", "client"),
        ("client_essai", "", "client"),
        ("client_essai", "n" * 61, "client"),
        ("client_essai", "Nom\x00nul", "client"),
        ("client_essai", "Nom\nligne", "client"),
        ("client_essai", "Nom", "lecteur"),
    ],
    ids=[
        "pseudo court",
        "pseudo hors forme",
        "nom vide",
        "nom trop long",
        "nom à octet nul",
        "nom à saut de ligne",
        "rôle inconnu",
    ],
)
def test_une_valeur_hors_regle_est_refusee_sans_rien_dire_d_elle(
    base_comptes, pseudo, nom, role
):
    with pytest.raises(comptes.CompteInvalide) as refus:
        lancer(
            lambda: comptes.creer(
                base_comptes,
                HACHEUR_RAPIDE,
                pseudo=pseudo,
                nom_affiche=nom,
                role=role,  # type: ignore[arg-type]
                mot_de_passe=MOT_DE_PASSE,
                doit_changer_mdp=False,
                par_compte_id=None,
            )
        )
    assert str(refus.value) == "" and refus.value.__cause__ is None


def test_le_mot_de_passe_d_un_compte_inactif_ne_change_pas(base_comptes, base_app):
    compte = creer(base_comptes)
    with psycopg.connect(base_app) as cx:
        cx.execute("UPDATE application.comptes SET actif = false")

    assert changer(base_comptes, compte.id, MOT_DE_PASSE) == Refus("inactif", compte.id)


@pytest.mark.parametrize(
    "pseudo",
    ["client\x00essai", "\x00", "c" * 10_000, "client essai"],
    ids=["octet nul", "octet nul seul", "très long", "avec espace"],
)
def test_un_pseudo_hors_forme_n_atteint_pas_la_base_et_se_refuse_comme_un_inconnu(
    base_comptes, pseudo
):
    creer(base_comptes)
    espion = Espion()

    resultat = connecter(base_comptes, espion, pseudo, MOT_DE_PASSE)

    assert resultat == Refus("mot_de_passe", None)
    assert espion.factices == 1, "le même chemin, le même temps qu'un inconnu"


# --------------------------------------------------------------------------- #
#  Le mot de passe changé : l'ancien sous le même compteur que la connexion
# --------------------------------------------------------------------------- #
def test_le_mot_de_passe_change_releve_la_generation_et_se_journalise(
    base_comptes, base_app
):
    compte = creer(base_comptes)

    change = changer(base_comptes, compte.id, MOT_DE_PASSE)

    assert isinstance(change, Compte)
    assert change.generation == compte.generation + 1
    assert lignes_du_journal(base_app)[-1] == (
        "mot_de_passe_change",
        compte.id,
        compte.id,
    )
    assert connecter(base_comptes, HACHEUR_RAPIDE, "client_essai", "un autre factice")


def test_un_mauvais_ancien_compte_un_echec_et_le_seuil_pose_le_verrou(
    base_comptes, base_app
):
    compte = creer(base_comptes)

    refus = [changer(base_comptes, compte.id, f"faux {i}") for i in range(SEUIL)]

    assert [r.motif for r in refus] == ["mot_de_passe"] * SEUIL
    assert [r.verrou_pose for r in refus] == [False] * (SEUIL - 1) + [True]
    assert etat(base_app, compte.id)[:2] == (0, True)
    # Pendant le verrou, le bon ancien ne change rien, et la connexion est refusée.
    assert changer(base_comptes, compte.id, MOT_DE_PASSE) == Refus("verrou", compte.id)
    assert connecter(
        base_comptes, HACHEUR_RAPIDE, "client_essai", MOT_DE_PASSE
    ) == Refus("verrou", compte.id)
    assert "mot_de_passe_change" not in [
        ligne[0] for ligne in lignes_du_journal(base_app)
    ]


# --------------------------------------------------------------------------- #
#  L'état d'un compte, la liste
# --------------------------------------------------------------------------- #
def test_un_changement_d_etat_sans_effet_ne_laisse_aucune_trace(base_comptes, base_app):
    admin = creer(base_comptes, "admin_essai", "admin")
    cible = creer(base_comptes)

    def etat_change(actif: bool, ident: int = cible.id) -> str:
        return lancer(
            lambda: comptes.changer_etat(
                base_comptes, ident, actif=actif, par_compte_id=admin.id
            )
        )

    assert etat_change(True) == "inchange"
    assert etat_change(False) == "fait"
    assert etat_change(False) == "inchange"
    assert etat_change(True) == "fait"
    assert etat_change(False, 999_999) == "inconnu"
    actions = [ligne[0] for ligne in lignes_du_journal(base_app)]
    assert actions == [
        "compte_cree",
        "compte_cree",
        "compte_desactive",
        "compte_reactive",
    ]
    with psycopg.connect(base_app) as cx:
        (generation,) = cx.execute(
            "SELECT generation FROM application.comptes WHERE id = %s", (cible.id,)
        ).fetchone()
    assert generation == cible.generation + 1, "seule la désactivation relève"


@pytest.mark.parametrize(
    ("demandee", "rendue"),
    [(0, 1), (-3, 1), (2, 2), (3, 3), (4, 3), (10**17, 3)],
)
def test_la_page_demandee_est_ramenee_entre_la_premiere_et_la_derniere(
    base_comptes, demandee, rendue
):
    for i in range(5):
        creer(base_comptes, f"client_{i}")

    lignes, total, page = lancer(
        lambda: comptes.lister(base_comptes, role=None, page=demandee, par_page=2)
    )

    assert (total, page) == (5, rendue)
    assert len(lignes) == (1 if rendue == 3 else 2)


def test_une_liste_vide_rend_la_premiere_page(base_comptes):
    assert lancer(
        lambda: comptes.lister(base_comptes, role="admin", page=7, par_page=2)
    ) == ([], 0, 1)
