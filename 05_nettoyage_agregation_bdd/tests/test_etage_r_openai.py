"""Étage R — juge OpenAI, jalon R-b. Tests À SEC : ZÉRO RÉSEAU, ZÉRO appel payant.

Aucun test ne contacte l'API OpenAI. On vérifie les parties pures : garde-fou
de clé (non-fuite), forme de la requête Batch, refus tant que le modèle n'est
pas choisi, réutilisation du contrat neutre, comptage de tokens. L'appel réel
appartient au run, gardé derrière la validation humaine du modèle.
"""

from __future__ import annotations

import pytest

from identity import etage_r_contrat as contrat
from identity import etage_r_juge as rj
from identity import etage_r_juge_openai as ro

# --------------------------------------------------------------------------- #
#  Le garde-fou de la clé — même discipline que côté Anthropic
# --------------------------------------------------------------------------- #


def test_l_absence_de_cle_est_une_erreur_explicite(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_AUTH_TOKEN", raising=False)

    with pytest.raises(ro.ErreurJuge, match="Aucune clé d'API OpenAI"):
        ro.cle_api()


def test_le_message_d_erreur_ne_contient_aucune_cle(monkeypatch):
    """MUTATION : un message qui échoterait la valeur lue la ferait fuiter."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-SECRET-NE-DOIT-PAS-FUIR")
    monkeypatch.delenv("OPENAI_AUTH_TOKEN", raising=False)

    assert ro.cle_api() == "sk-proj-SECRET-NE-DOIT-PAS-FUIR"

    monkeypatch.delenv("OPENAI_API_KEY")
    try:
        ro.cle_api()
    except ro.ErreurJuge as erreur:
        assert "SECRET" not in str(erreur)
        assert "sk-proj" not in str(erreur)


def test_le_jeton_alternatif_est_accepte(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_AUTH_TOKEN", "oauth-token")

    assert ro.cle_api() == "oauth-token"


# --------------------------------------------------------------------------- #
#  Le contrat est le MÊME objet que côté Anthropic — « INTACT » vérifiable
# --------------------------------------------------------------------------- #


def test_le_contrat_est_partage_avec_le_juge_anthropic():
    """Le protocole reste INTACT au changement de fournisseur : ce n'est pas une
    affirmation, c'est le MÊME objet Python des deux côtés."""
    assert ro.SCHEMA_VERDICT is rj.SCHEMA_VERDICT is contrat.SCHEMA_VERDICT
    assert ro.PROMPT_SYSTEME is rj.PROMPT_SYSTEME is contrat.PROMPT_SYSTEME
    assert ro.PROMPT_VERSION == contrat.PROMPT_VERSION


def test_l_identifiant_fait_l_aller_retour():
    """Réutilisé du contrat : rattachement par clé, jamais par position."""
    cle = ro.identifiant("etalonnage", 4242, "kitsu_id", "999")
    assert ro.relire_identifiant(cle) == {
        "phase": "etalonnage",
        "series_id": 4242,
        "candidat_type": "kitsu_id",
        "candidat_id": "999",
    }


# --------------------------------------------------------------------------- #
#  La requête Batch OpenAI
# --------------------------------------------------------------------------- #


def test_la_requete_refuse_tant_que_le_modele_n_est_pas_choisi():
    """Cœur du protocole R-b, point 2 : pas de modèle en dur, pas d'appel
    payant avant validation humaine. La requête REFUSE de se construire."""
    assert ro.MODELE is None
    with pytest.raises(ro.ErreurJuge, match="Aucun modèle choisi"):
        ro.construire_requete("file|1|qid|Q1", "dossier")


def test_la_requete_porte_le_schema_strict_et_le_prompt_systeme():
    requete = ro.construire_requete("file|1|qid|Q1", "le dossier", modele="un-modele")

    assert requete["custom_id"] == "file|1|qid|Q1"
    assert requete["method"] == "POST"
    assert requete["url"] == "/v1/chat/completions"
    body = requete["body"]
    assert body["model"] == "un-modele"
    fmt = body["response_format"]["json_schema"]
    assert fmt["strict"] is True
    assert fmt["schema"] is ro.SCHEMA_VERDICT
    assert body["messages"][0] == {"role": "system", "content": ro.PROMPT_SYSTEME}
    assert body["messages"][1] == {"role": "user", "content": "le dossier"}


def test_la_requete_ne_porte_aucun_parametre_d_echantillonnage():
    """MUTATION : `temperature` est refusée sur les modèles de raisonnement et
    casserait le lot. Sa présence — comme top_p/top_k — est interdite."""
    body = ro.construire_requete("file|1|qid|Q1", "d", modele="m")["body"]

    for interdit in ("temperature", "top_p", "top_k"):
        assert interdit not in body, (
            f"{interdit} interdit : la stabilité vient du couple "
            "(modele, prompt_version), pas d'une température"
        )


def test_la_requete_plafonne_par_max_completion_tokens_pas_max_tokens():
    """MUTATION : `max_tokens` est refusé par les modèles de raisonnement ;
    la forme correcte est `max_completion_tokens`."""
    body = ro.construire_requete("file|1|qid|Q1", "d", modele="m")["body"]

    assert body["max_completion_tokens"] == ro.MAX_TOKENS_SORTIE
    assert "max_tokens" not in body


def test_l_effort_de_raisonnement_est_transmis_quand_demande():
    sans = ro.construire_requete("c", "d", modele="m")["body"]
    avec = ro.construire_requete("c", "d", modele="m", effort_raisonnement="medium")[
        "body"
    ]

    assert "reasoning_effort" not in sans
    assert avec["reasoning_effort"] == "medium"


# --------------------------------------------------------------------------- #
#  Volumétrie : tiktoken redevient le bon outil côté OpenAI
# --------------------------------------------------------------------------- #


def test_le_comptage_de_tokens_est_deterministe_et_monotone():
    assert ro.compter_tokens("") == 0
    court = ro.compter_tokens("Berserk")
    long = ro.compter_tokens("Berserk, de Kentaro Miura, publié en 1989 au Japon.")
    assert court > 0
    assert long > court
    # Déterministe : deux appels, même chiffre.
    assert ro.compter_tokens("ベルセルク") == ro.compter_tokens("ベルセルク")
