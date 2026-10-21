"""Les valeurs posées d'avance, l'empreinte du jeu recalculée, et des contrôles
bornés au corpus vérifié."""

from __future__ import annotations

import psycopg
from test_corpus_cohabitation import corpus_v2
from test_encodage import doublure  # noqa: F401 — la fixture du service simulé

from service_embedding import verification
from service_embedding.encodage import encoder_corpus
from service_embedding.verification import (
    DOSSIER_JEU,
    EMPREINTE_JEU,
    controle,
    empreinte_fichiers,
)


def test_empreinte_du_jeu_v2_recalculee_depuis_les_fichiers():
    # Même règle que `evaluation.geler.empreinte` (module 05) : si elle
    # divergeait, le contrôle « empreinte inchangée » ne prouverait rien.
    assert empreinte_fichiers(DOSSIER_JEU) == EMPREINTE_JEU


def test_controle_compare_strictement():
    assert controle("x", 0, 0)["conforme"]
    assert not controle("x", 66_290, 66_289)["conforme"]
    assert not controle("x", [1, 2], [2, 1])["conforme"]


# --------------------------------------------------------------------------- #
#  Deux corpus, un encodage chacun : les contrôles ne lisent que ceux du corpus
# --------------------------------------------------------------------------- #


def test_les_controles_ne_lisent_que_les_encodages_du_corpus(
    base,
    doublure,  # noqa: F811
    monkeypatch,
):
    instance, client, service = doublure("bge-m3")
    encoder_corpus(instance, client, service, base)  # v1 : trois fragments
    corpus_v2(base)
    encoder_corpus(instance, client, service, base, corpus_id="v2")  # v2 : un
    with psycopg.connect(base) as cx:
        cx.execute(
            "INSERT INTO bench.eval_jeux (version, empreinte, gele_le, declaration)"
            " VALUES (%s, %s, now(), 'essai')",
            (verification.JEU, verification.EMPREINTE_JEU),
        )
    monkeypatch.setattr(verification, "FRAGMENTS", 3)
    monkeypatch.setattr(verification, "DOCUMENTS", 2)
    monkeypatch.setattr(verification, "ENCODAGES", 1)

    constats = {c["controle"]: c for c in verification.verifier(base)}

    assert constats["bench.vecteurs_bge_m3 : lignes"]["constate"] == 3
    assert constats["bench.vecteurs_bge_m3 : encodages distincts"]["constate"] == 1
    assert constats["bench.encodages : lignes"]["constate"] == 1
    assert constats["bench.vecteurs_bge_m3 : vecteur sans chunk_id"]["constate"] == 0
    assert all(
        c["conforme"]
        for nom, c in constats.items()
        if nom.startswith("bench.vecteurs_bge_m3") or nom.startswith("bench.encodages")
    )


def test_une_ligne_attendue_absente_est_un_ecart_pas_une_exception(base):
    """Sans la ligne du jeu dans bench.eval_jeux, le contrôle rapporte l'écart."""
    constats = {c["controle"]: c for c in verification.verifier(base)}
    jeu = constats[f"jeu {verification.JEU} : empreinte en base"]
    assert jeu["constate"] is None
    assert not jeu["conforme"]
