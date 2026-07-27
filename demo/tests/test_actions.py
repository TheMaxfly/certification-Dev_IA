"""Le registre ne doit jamais mentir : chaque entrée pointe une CLI existante.

C'est le test qui protège la démo. Si quelqu'un renomme ou déplace un module
identity, un job lakehouse ou le runner de migrations, ces tests tombent — et
le guide comme la console cessent d'annoncer une commande qui n'existe plus.
"""

from __future__ import annotations

import pytest

from manga_pipeline.actions import (
    ACTIONS,
    ACTIONS_PAR_CLE,
    Impact,
    Phase,
)


@pytest.mark.parametrize("action", ACTIONS, ids=lambda a: a.cle)
def test_la_cible_existe(action):
    """Le fichier de la CLI visée est bien là (garde-fou anti-renommage)."""
    assert action.cible.exists(), (
        f"{action.cle} vise {action.cible}, qui n'existe pas. "
        "Une CLI a été renommée ou déplacée : corriger le registre ET le guide."
    )


@pytest.mark.parametrize("action", ACTIONS, ids=lambda a: a.cle)
def test_le_repertoire_de_travail_existe(action):
    assert action.cwd.is_dir(), f"{action.cle} : cwd introuvable ({action.cwd})"


@pytest.mark.parametrize("action", ACTIONS, ids=lambda a: a.cle)
def test_chaque_action_est_documentee(action):
    """Une action sans phrase orale ni vérification est inutilisable en jury."""
    assert action.explication.strip(), f"{action.cle} : pas de phrase d'explication"
    assert action.verification.strip(), f"{action.cle} : pas de critère de vérification"


def test_les_cles_sont_uniques():
    assert len(ACTIONS_PAR_CLE) == len(ACTIONS)


def test_aucune_action_destructive():
    """Aucune commande du registre ne peut détruire des données.

    On interdit les verbes destructeurs dans l'argv : la console de démo ne doit
    proposer NI truncate, NI drop, NI suppression de fichiers.
    """
    interdits = {"truncate", "drop", "delete", "rm", "reset", "--force"}
    for action in ACTIONS:
        mots = {mot.lower() for mot in action.argv}
        assert not (mots & interdits), f"{action.cle} contient un verbe destructeur"


def test_les_commandes_passent_par_uv_ou_bash():
    """Convention du dépôt : tout Python passe par `uv run` (jamais pip/python nu)."""
    for action in ACTIONS:
        assert action.argv[0] in {"uv", "bash"}, (
            f"{action.cle} démarre par {action.argv[0]!r} : "
            "la convention du dépôt est `uv run` (ou bash pour un script shell)."
        )


def test_la_commande_affichee_contient_les_variables_denv():
    """Ce qu'on montre au jury doit être copiable-collable tel quel."""
    action = ACTIONS_PAR_CLE["load-ms"]
    assert action.commande.startswith("PYTHONPATH=src ")
    assert "uv run python -m identity.charger_ms" in action.commande


def test_extract_est_documente_jamais_execute():
    """Les actions Extract sortent sur le réseau : la console ne les lance pas."""
    for action in ACTIONS:
        if action.phase is Phase.EXTRACT:
            assert action.impact is Impact.DOCUMENTE
            assert not action.executable
