"""La couche des fournisseurs : un profil, une requête HTTP directe, des traces.

Tout passe par les doublures du bouclage (`conftest.doublures`) et deux profils
factices ; la garde `aucun_appel_hors_du_bouclage` fait échouer toute requête vers
un autre hôte. Aucun test n'appelle un fournisseur réel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import socket
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

import pytest
from conftest import (
    BOUCLAGE,
    CLE_A,
    CLE_B,
    CLES_FACTICES,
    DELAI_FACTICE_S,
    ENVIRONNEMENT_FACTICE,
    DoublureFournisseur,
    Doublures,
    dsn_du_role,
    environnement,
    reglages_factices,
)
from fastapi.testclient import TestClient

from api_modele.application import creer_application
from api_modele.configuration import ModeleLangage, charger
from api_modele.demarrage import DemarrageRefuse
from api_modele.fournisseurs import (
    ADAPTATEURS,
    Demandes,
    ErreurFournisseur,
    Fournisseurs,
    Messages,
    Sortie,
    adresse_modeles,
    cout_usd,
    estimer_cout,
    estimer_jetons,
)
from api_modele.journal import FormatJson
from api_modele.metriques import Metriques

MESSAGES = Messages(
    systeme="Réponds en français, d'après les seuls passages.",
    utilisateur="Question : une aventure de pirates ?",
)
#: Aucun réglage demandé : les défauts du fichier.
SANS_DEMANDE = Demandes()
SCHEMA = {
    "type": "object",
    "properties": {"refus": {"type": "boolean"}},
    "required": ["refus"],
    "additionalProperties": False,
}


def modele_langage(tmp_path, doublures: Doublures, **env: str) -> ModeleLangage:
    fichier = reglages_factices(tmp_path, doublures)
    return charger(ENVIRONNEMENT_FACTICE | env, fichier).modele_langage


def jouer[T](
    ml: ModeleLangage,
    scenario: Callable[[Fournisseurs], Awaitable[T]],
    *,
    environ: dict[str, str] | None = None,
    metriques: Metriques | None = None,
) -> T:
    """Un scénario sur une couche neuve, dans une boucle à lui ; la couche fermée."""

    async def deroule() -> T:
        couche = Fournisseurs(
            ml,
            CLES_FACTICES if environ is None else environ,
            metriques or Metriques(),
        )
        try:
            return await scenario(couche)
        finally:
            await couche.fermer()

    return asyncio.run(deroule())


def appeler(
    profil: str, demandes: Demandes = SANS_DEMANDE
) -> Callable[[Fournisseurs], Awaitable[Sortie]]:
    async def scenario(couche: Fournisseurs) -> Sortie:
        return await couche.appeler(
            profil, "req-essai", "reponse", MESSAGES, demandes, SCHEMA
        )

    return scenario


def erreur(ml: ModeleLangage, profil: str, **options: Any) -> ErreurFournisseur:
    demandes = options.pop("demandes", SANS_DEMANDE)
    with pytest.raises(ErreurFournisseur) as exc:
        jouer(ml, appeler(profil, demandes), **options)
    return exc.value


# --------------------------------------------------------------------------- #
#  2.10 : le changement de modèle est un geste de configuration
# --------------------------------------------------------------------------- #
def test_les_profils_factices_sont_sur_le_bouclage(tmp_path, doublures):
    ml = modele_langage(tmp_path, doublures)

    assert [p.nom for p in ml.profils] == ["factice-a", "factice-b"]
    adresses = {urlsplit(p.adresse).netloc for p in ml.profils}
    assert len(adresses) == 2  # adresses distinctes
    assert all(urlsplit(p.adresse).hostname in BOUCLAGE for p in ml.profils)


def test_changer_le_profil_actif_change_le_profil_appele_sans_autre_modification(
    tmp_path, doublures
):
    for env, port, cle in (
        ({}, doublures.port_a, CLE_A),
        ({"API_MODELE_PROFIL_ACTIF": "factice-b"}, doublures.port_b, CLE_B),
    ):
        DoublureFournisseur.recus = []
        ml = modele_langage(tmp_path, doublures, **env)

        async def actif(couche: Fournisseurs) -> Sortie:
            return await appeler(couche.profil_actif)(couche)

        jouer(ml, actif)

        assert [r["port"] for r in doublures.recus()] == [port]
        assert doublures.recus()[0]["autorisation"] == f"Bearer {cle}"


def test_un_profil_sans_cle_est_indisponible_et_n_est_pas_appele(tmp_path, doublures):
    ml = charger(
        {k: v for k, v in ENVIRONNEMENT_FACTICE.items() if k != "CLE_FACTICE_B"},
        reglages_factices(tmp_path, doublures),
    ).modele_langage

    exc = erreur(ml, "factice-b")

    assert (exc.code, exc.details) == ("profil_indisponible", {"profil": "factice-b"})
    assert doublures.recus() == []


def test_une_cle_retiree_depuis_le_demarrage_rend_le_profil_indisponible(
    tmp_path, doublures
):
    ml = modele_langage(tmp_path, doublures)

    exc = erreur(ml, "factice-a", environ={"CLE_FACTICE_A": " "})

    assert exc.code == "profil_indisponible"
    assert doublures.recus() == []


def test_un_profil_inconnu_n_est_pas_appele(tmp_path, doublures):
    exc = erreur(modele_langage(tmp_path, doublures), "absent")

    assert exc.code == "profil_inconnu"
    assert exc.details == {"profil": "absent", "profils": ["factice-a", "factice-b"]}
    assert doublures.recus() == []


# --------------------------------------------------------------------------- #
#  2.10 : un réglage déclaré absent n'est ni envoyé ni ignoré
# --------------------------------------------------------------------------- #
def test_un_reglage_absent_demande_refuse_l_appel(tmp_path, doublures):
    exc = erreur(
        modele_langage(tmp_path, doublures),
        "factice-b",
        demandes=Demandes(temperature=0.7),
    )

    assert exc.code == "reglage_non_expose"
    assert exc.details == {"reglage": "temperature", "profil": "factice-b"}
    assert doublures.recus() == []


def test_un_reglage_absent_non_demande_n_est_pas_envoye(tmp_path, doublures):
    sortie = jouer(modele_langage(tmp_path, doublures), appeler("factice-b"))

    (recu,) = doublures.recus()
    assert "temperature" not in recu["corps"]
    assert sortie.appliques.temperature is None


def test_les_reglages_demandes_sont_envoyes_sinon_les_defauts(tmp_path, doublures):
    ml = modele_langage(tmp_path, doublures)
    defaut = jouer(ml, appeler("factice-a"))
    demande = jouer(
        ml, appeler("factice-a", Demandes(temperature=0.7, longueur_max=120))
    )

    premier, second = doublures.recus()
    assert (premier["corps"]["temperature"], premier["corps"]["max_tokens"]) == (
        0.0,
        400,
    )
    assert (second["corps"]["temperature"], second["corps"]["max_tokens"]) == (
        0.7,
        120,
    )
    assert (defaut.appliques.temperature, demande.appliques.longueur_max) == (0, 120)


# --------------------------------------------------------------------------- #
#  Les écarts de forme vivent dans les adaptateurs, pas dans les invites
# --------------------------------------------------------------------------- #
def test_la_forme_de_chaque_fournisseur_et_des_invites_identiques(tmp_path, doublures):
    ml = modele_langage(tmp_path, doublures)
    jouer(ml, appeler("factice-a"))
    jouer(ml, appeler("factice-b"))

    mistral, openai = (r["corps"] for r in doublures.recus())
    enveloppe = {
        "type": "json_schema",
        "json_schema": {"name": "sortie", "schema": SCHEMA, "strict": True},
    }
    assert (
        mistral["messages"]
        == openai["messages"]
        == [
            {"role": "system", "content": MESSAGES.systeme},
            {"role": "user", "content": MESSAGES.utilisateur},
        ]
    )
    assert mistral["response_format"] == openai["response_format"] == enveloppe
    assert mistral == {
        "model": "modele-a",
        "messages": mistral["messages"],
        "response_format": enveloppe,
        "temperature": 0.0,
        "max_tokens": 400,
        "top_p": 1.0,
    }
    assert openai == {
        "model": "modele-b",
        "messages": openai["messages"],
        "response_format": enveloppe,
        "max_completion_tokens": 400,
        "top_p": 1.0,
        "reasoning_effort": "none",
    }
    assert set(ADAPTATEURS) == {"mistral", "openai"}


# --------------------------------------------------------------------------- #
#  La sortie, ses jetons, son coût
# --------------------------------------------------------------------------- #
def test_une_sortie_avec_usage_et_raisonnement(tmp_path, doublures):
    DoublureFournisseur.usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 300,
        "total_tokens": 1300,
        "completion_tokens_details": {"reasoning_tokens": 100},
    }
    DoublureFournisseur.modele_rendu = "modele-b-2026-10-01"

    sortie = jouer(modele_langage(tmp_path, doublures), appeler("factice-b"))

    assert sortie.texte == DoublureFournisseur.contenu
    j = sortie.jetons
    # OpenAI compte le raisonnement dans `completion_tokens` : 300 = 200 + 100.
    assert (j.entree, j.sortie, j.raisonnement, j.estimes) == (1000, 200, 100, False)
    # 1 000 × 1 $ + (200 + 100) × 4 $, par million.
    assert sortie.cout_estime_usd == pytest.approx(0.0022)
    assert sortie.modele_rendu == "modele-b-2026-10-01"
    assert sortie.date_appel.utcoffset() is not None
    assert sortie.date_appel.utcoffset().total_seconds() == 0
    assert sortie.latence_ms >= 0


@pytest.mark.parametrize(
    ("details", "jetons"),
    [({}, (1000, 300, 0)), ({"reasoning_tokens": 100}, (1000, 200, 100))],
)
def test_une_sortie_mistral_et_son_raisonnement(tmp_path, doublures, details, jetons):
    # Relevé à l'appel d'essai : Mistral range le raisonnement comme OpenAI, compris
    # dans `completion_tokens`. Le coût ne change pas : même prix pour les deux.
    DoublureFournisseur.usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 300,
        "completion_tokens_details": details,
    }

    sortie = jouer(modele_langage(tmp_path, doublures), appeler("factice-a"))

    j = sortie.jetons
    assert (j.entree, j.sortie, j.raisonnement, j.estimes) == (*jetons, False)
    assert sortie.cout_estime_usd == pytest.approx((1000 * 2 + 300 * 8) / 1e6)
    assert sortie.modele_rendu is None  # le modèle rendu est celui demandé


def test_sans_usage_les_jetons_sont_estimes_par_caracteres(tmp_path, doublures):
    sortie = jouer(modele_langage(tmp_path, doublures), appeler("factice-a"))

    entree = math.ceil(len(MESSAGES.systeme + MESSAGES.utilisateur) / 3.8)
    sortie_ = math.ceil(len(DoublureFournisseur.contenu) / 3.8)
    j = sortie.jetons
    assert (j.entree, j.sortie, j.raisonnement, j.estimes) == (entree, sortie_, 0, True)


def test_un_prix_absent_rend_un_cout_nul(tmp_path, doublures):
    ml = modele_langage(tmp_path, doublures)
    sans_prix = tuple(replace(p, prix=None) for p in ml.profils)

    sortie = jouer(replace(ml, profils=sans_prix), appeler("factice-a"))

    assert sortie.cout_estime_usd is None


def test_estimer_cout_sur_un_contexte_connu():
    ml = charger(ENVIRONNEMENT_FACTICE | {"MISTRAL_API_KEY": "x"}).modele_langage
    actif = ml.profil("mistral-large-2512")
    sol = ml.profil("gpt-6-sol")
    assert actif is not None and sol is not None

    # Pire cas de la route (§ 7) : 3 500 jetons d'entrée, 400 de sortie au plus.
    assert estimer_cout(actif, 3500, 400) == pytest.approx(0.00235)  # 1750 + 600
    assert estimer_cout(sol, 3500, 400) == pytest.approx(0.011)  # 7000 + 4000
    assert cout_usd(actif, 1000, 100, 50) == pytest.approx(0.000725)
    assert estimer_jetons("x" * 35, 3.5) == 10
    assert estimer_jetons("x" * 36, 3.5) == 11
    assert estimer_jetons("", 3.5) == 0


# --------------------------------------------------------------------------- #
#  Erreurs : explicites, sans corps brut, sans nouvel essai
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("statut", "corps", "code_fournisseur"),
    [
        (
            401,
            {"error": {"message": "x", "code": "invalid_api_key"}},
            "invalid_api_key",
        ),
        (
            429,
            {"object": "error", "type": "rate_limited", "message": "x"},
            "rate_limited",
        ),
        (500, {"error": {"code": "Une phrase, pas un code"}}, None),
        (503, "pas du json", None),
    ],
)
def test_un_refus_du_fournisseur(tmp_path, doublures, statut, corps, code_fournisseur):
    DoublureFournisseur.statut = statut
    DoublureFournisseur.corps_brut = (
        corps.encode() if isinstance(corps, str) else json.dumps(corps).encode()
    )

    exc = erreur(modele_langage(tmp_path, doublures), "factice-a")

    attendu: dict[str, object] = {"profil": "factice-a", "role": "reponse"}
    attendu["statut"] = statut
    if code_fournisseur is not None:
        attendu["code_fournisseur"] = code_fournisseur
    assert (exc.code, exc.details) == ("fournisseur_refus", attendu)
    assert len(doublures.recus()) == 1  # pas de nouvel essai, 429 compris


@pytest.mark.parametrize(
    "corps",
    [b"pas du json", b'{"id": "x"}', b'{"choices": []}', b'{"choices": [{}]}'],
)
def test_un_corps_inattendu_est_un_refus(tmp_path, doublures, corps):
    DoublureFournisseur.corps_brut = corps

    exc = erreur(modele_langage(tmp_path, doublures), "factice-a")

    assert exc.code == "fournisseur_refus"
    assert exc.details["motif"] == "corps_inattendu"
    assert exc.details["statut"] == 200


@pytest.mark.parametrize("contenu", ['{"refus": fal', None])
def test_la_longueur_atteinte_est_dite(tmp_path, doublures, contenu):
    # Sortie coupée à la borne (le raisonnement l'a épuisée, ou le texte) : le motif
    # le dit, même quand un texte partiel est rendu.
    DoublureFournisseur.corps_brut = json.dumps(
        {
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": contenu},
                    "finish_reason": "length",
                }
            ],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 400},
        }
    ).encode()

    exc = erreur(modele_langage(tmp_path, doublures), "factice-a")

    assert exc.code == "fournisseur_refus"
    assert (exc.details["motif"], exc.details["statut"]) == ("longueur_atteinte", 200)


def test_une_reponse_trop_lente_depasse_le_delai(tmp_path, doublures):
    DoublureFournisseur.retard_s = DELAI_FACTICE_S * 3

    exc = erreur(modele_langage(tmp_path, doublures), "factice-a")

    assert exc.code == "fournisseur_delai"
    assert exc.details == {"profil": "factice-a", "role": "reponse"}


def test_une_reponse_goutte_a_goutte_depasse_le_delai_de_l_appel_entier(
    tmp_path, doublures
):
    """Chaque lecture tient sous le délai, l'appel entier non : refusé quand même."""
    DoublureFournisseur.contenu = "x" * 400
    DoublureFournisseur.goutte_s = DELAI_FACTICE_S * 0.6

    exc = erreur(modele_langage(tmp_path, doublures), "factice-a")

    assert exc.code == "fournisseur_delai"


def test_un_port_ferme_est_injoignable(tmp_path, doublures):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    ml = modele_langage(tmp_path, doublures)
    ferme = tuple(
        replace(p, adresse=f"http://127.0.0.1:{port}/v1/chat/completions")
        for p in ml.profils
    )

    exc = erreur(replace(ml, profils=ferme), "factice-a")

    assert exc.code == "fournisseur_injoignable"
    assert exc.__cause__ is None and exc.__suppress_context__


def test_une_connexion_coupee_est_injoignable(tmp_path, doublures, monkeypatch):
    import httpx

    async def couper(*args: object, **kwargs: object) -> httpx.Response:
        raise httpx.RemoteProtocolError("coupée")

    monkeypatch.setattr(httpx.AsyncClient, "post", couper)

    exc = erreur(modele_langage(tmp_path, doublures), "factice-a")

    assert exc.code == "fournisseur_injoignable"


def test_une_erreur_imprevue_se_trace_comme_telle(tmp_path, doublures, monkeypatch):
    metriques = Metriques()

    def casser(*args: object, **kwargs: object) -> Sortie:
        raise RuntimeError("imprévu")

    monkeypatch.setattr(Fournisseurs, "_sortie", casser)

    with pytest.raises(RuntimeError):
        jouer(
            modele_langage(tmp_path, doublures),
            appeler("factice-a"),
            metriques=metriques,
        )

    texte = metriques.exposer()[0].decode()
    assert 'statut="erreur_interne"} 1.0' in texte


# --------------------------------------------------------------------------- #
#  Aucun secret dans une sortie ; la clé lue au moment de l'appel
# --------------------------------------------------------------------------- #
def test_la_cle_n_apparait_nulle_part(tmp_path, doublures, caplog):
    caplog.set_level(logging.DEBUG, logger="api_modele")
    metriques = Metriques()
    reglages = charger(ENVIRONNEMENT_FACTICE, reglages_factices(tmp_path, doublures))
    ml = reglages.modele_langage
    sortie = jouer(ml, appeler("factice-a"), metriques=metriques)
    # Un refus dont le corps recopie l'en-tête reçu : la couche ne le rend pas.
    DoublureFournisseur.statut = 401
    DoublureFournisseur.corps_brut = json.dumps(
        {"error": {"message": f"clé refusée : Bearer {CLE_A}", "code": "x"}}
    ).encode()
    refus = erreur(ml, "factice-a", metriques=metriques)
    DoublureFournisseur.corps_brut = None
    DoublureFournisseur.statut = 200
    DoublureFournisseur.retard_s = DELAI_FACTICE_S * 3
    delai = erreur(ml, "factice-a", metriques=metriques)

    lignes = "\n".join(FormatJson().format(r) for r in caplog.records)
    assert sum(r.getMessage() == "appel_modele" for r in caplog.records) == 3
    assert '"variable_cle": "CLE_FACTICE_A"' in lignes
    sorties = [
        lignes,
        metriques.exposer()[0].decode(),
        repr(reglages),
        repr(sortie),
        repr(Fournisseurs(ml, CLES_FACTICES, Metriques())),
        *(f"{e} {e!r} {e.details}" for e in (refus, delai)),
    ]
    for texte in sorties:
        assert CLE_A not in texte and CLE_B not in texte
    # La clé est partie dans l'en-tête, et là seulement.
    assert {r["autorisation"] for r in doublures.recus()} == {f"Bearer {CLE_A}"}


def test_ni_message_ni_sortie_au_journal(tmp_path, doublures, caplog):
    caplog.set_level(logging.DEBUG, logger="api_modele")
    DoublureFournisseur.contenu = '{"reponse": "Un texte rendu par le modèle."}'

    jouer(modele_langage(tmp_path, doublures), appeler("factice-a"))

    lignes = "\n".join(FormatJson().format(r) for r in caplog.records)
    for texte in (MESSAGES.systeme, MESSAGES.utilisateur, "rendu par le modèle"):
        assert texte not in lignes


def test_la_cle_est_lue_au_moment_de_l_appel(tmp_path, doublures):
    ml = modele_langage(tmp_path, doublures)
    environ = dict(CLES_FACTICES)

    async def deux_appels(couche: Fournisseurs) -> None:
        await appeler("factice-a")(couche)
        environ["CLE_FACTICE_A"] = "factice-cle-a-renouvelee"
        await appeler("factice-a")(couche)

    jouer(ml, deux_appels, environ=environ)

    assert [r["autorisation"] for r in doublures.recus()] == [
        f"Bearer {CLE_A}",
        "Bearer factice-cle-a-renouvelee",
    ]


def test_la_trace_d_un_appel(tmp_path, doublures, caplog):
    caplog.set_level(logging.INFO, logger="api_modele.appels")
    DoublureFournisseur.usage = {"prompt_tokens": 10, "completion_tokens": 5}

    jouer(modele_langage(tmp_path, doublures), appeler("factice-a"))

    (ligne,) = [
        json.loads(FormatJson().format(r))
        for r in caplog.records
        if r.getMessage() == "appel_modele"
    ]
    assert {
        k: v
        for k, v in ligne.items()
        if k not in ("horodatage", "latence_ms", "date_appel")
    } == {
        "niveau": "INFO",
        "evenement": "appel_modele",
        "identifiant_requete": "req-essai",
        "role": "reponse",
        "profil": "factice-a",
        "fournisseur": "mistral",
        "modele_demande": "modele-a",
        "modele_rendu": None,
        "statut": "ok",
        "temperature": 0.0,
        "longueur_max": 400,
        "top_p": 1.0,
        "reasoning_effort": None,
        "jetons_entree": 10,
        "jetons_sortie": 5,
        "jetons_raisonnement": 0,
        "jetons_estimes": False,
        "cout_estime_usd": pytest.approx((10 * 2 + 5 * 8) / 1e6),
        "cout_estime_avant_usd": pytest.approx(
            (estimer_jetons(MESSAGES.systeme + MESSAGES.utilisateur, 3.8) * 2 + 400 * 8)
            / 1e6
        ),
        "variable_cle": "CLE_FACTICE_A",
    }
    assert ligne["date_appel"].endswith("+00:00")


# --------------------------------------------------------------------------- #
#  Métriques (C11)
# --------------------------------------------------------------------------- #
def test_les_metriques_apres_une_sequence_connue(tmp_path, doublures):
    metriques = Metriques()
    ml = modele_langage(tmp_path, doublures)
    DoublureFournisseur.usage = {"prompt_tokens": 100, "completion_tokens": 20}

    async def sequence(couche: Fournisseurs) -> None:
        await couche.demarrer()
        await appeler("factice-a")(couche)
        await appeler("factice-a")(couche)
        DoublureFournisseur.statut = 500
        with pytest.raises(ErreurFournisseur):
            await appeler("factice-a")(couche)
        DoublureFournisseur.statut = 200
        DoublureFournisseur.retard_s = DELAI_FACTICE_S * 3
        with pytest.raises(ErreurFournisseur):
            await appeler("factice-a")(couche)

    jouer(ml, sequence, metriques=metriques)

    texte = metriques.exposer()[0].decode()
    a = 'profil="factice-a",role="reponse"'
    for ligne in (
        f'api_modele_llm_appels_total{{{a},statut="ok"}} 2.0',
        f'api_modele_llm_appels_total{{{a},statut="fournisseur_refus"}} 1.0',
        f'api_modele_llm_appels_total{{{a},statut="fournisseur_delai"}} 1.0',
        f'api_modele_llm_jetons_total{{{a},type="entree"}} 200.0',
        f'api_modele_llm_jetons_total{{{a},type="sortie"}} 40.0',
        f'api_modele_llm_jetons_total{{{a},type="raisonnement"}} 0.0',
        f"api_modele_llm_latence_secondes_count{{{a}}} 4.0",
        'api_modele_profil_actif{profil="factice-a"} 1.0',
        'api_modele_profil_actif{profil="factice-b"} 0.0',
        'api_modele_profil_disponible{profil="factice-a"} 1.0',
        'api_modele_profil_disponible{profil="factice-b"} 1.0',
        "# TYPE api_modele_sortie_invalide_total counter",
    ):
        assert ligne in texte, ligne
    # 2 × (100 × 2 $ + 20 × 8 $) / 10⁶
    cout = next(
        float(ligne.split()[-1])
        for ligne in texte.splitlines()
        if ligne.startswith('api_modele_llm_cout_usd_total{profil="factice-a"}')
    )
    assert cout == pytest.approx(0.00072)


# --------------------------------------------------------------------------- #
#  Le contrôle d'exploitation : jamais par défaut
# --------------------------------------------------------------------------- #
def test_sans_le_reglage_aucun_controle_n_appelle(tmp_path, doublures):
    ml = modele_langage(tmp_path, doublures, API_MODELE_VERIFIER_MODELES="0")

    async def demarrer(couche: Fournisseurs) -> None:
        await couche.demarrer()

    jouer(ml, demarrer)

    assert ml.verifier_modeles is False
    assert doublures.recus() == []


def test_le_controle_rend_indisponible_un_identifiant_absent(
    tmp_path, doublures, caplog
):
    caplog.set_level(logging.INFO, logger="api_modele")
    DoublureFournisseur.modeles = ["modele-a"]
    ml = modele_langage(tmp_path, doublures, API_MODELE_VERIFIER_MODELES="1")

    async def demarrer(couche: Fournisseurs) -> dict[str, bool]:
        await couche.demarrer()
        return {n: p.disponible for n, p in couche.profils.items()}

    assert jouer(ml, demarrer) == {"factice-a": True, "factice-b": False}
    assert sorted(r["chemin"] for r in doublures.recus()) == ["/v1/models"] * 2
    assert {r["autorisation"] for r in doublures.recus()} == {
        f"Bearer {CLE_A}",
        f"Bearer {CLE_B}",
    }
    evenements = [r for r in caplog.records if r.getMessage() == "verification_modeles"]
    assert len(evenements) == 2


@pytest.mark.parametrize(
    ("statut", "corps"),
    [(401, None), (200, b"pas du json")],
)
def test_une_liste_illisible_rend_le_profil_indisponible(
    tmp_path, doublures, monkeypatch, statut, corps
):
    DoublureFournisseur.statut_modeles = statut
    DoublureFournisseur.modeles = ["modele-a", "modele-b"]
    if corps is not None:
        monkeypatch.setattr(
            DoublureFournisseur,
            "_envoyer",
            _remplacer_corps(DoublureFournisseur, corps),
        )
    ml = modele_langage(tmp_path, doublures, API_MODELE_VERIFIER_MODELES="1")

    async def verifier(couche: Fournisseurs) -> dict[str, bool]:
        return await couche.verifier_modeles()

    assert jouer(ml, verifier) == {"factice-a": False, "factice-b": False}


def _remplacer_corps(classe: type[DoublureFournisseur], corps: bytes):
    envoyer = classe._envoyer

    def envoyer_autre(self: DoublureFournisseur, statut: int, _donnees: bytes) -> None:
        envoyer(self, statut, corps)

    return envoyer_autre


def test_une_liste_injoignable_rend_le_profil_indisponible(tmp_path, doublures):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    ml = modele_langage(tmp_path, doublures)
    ferme = tuple(
        replace(p, adresse=f"http://127.0.0.1:{port}/v1/chat/completions")
        for p in ml.profils
    )

    async def verifier(couche: Fournisseurs) -> dict[str, bool]:
        return await couche.verifier_modeles()

    assert jouer(replace(ml, profils=ferme), verifier) == {
        "factice-a": False,
        "factice-b": False,
    }


def test_le_profil_actif_ecarte_par_le_controle_refuse_le_demarrage(
    tmp_path, doublures
):
    DoublureFournisseur.modeles = ["modele-b"]
    ml = modele_langage(tmp_path, doublures, API_MODELE_VERIFIER_MODELES="1")

    async def demarrer(couche: Fournisseurs) -> None:
        await couche.demarrer()

    with pytest.raises(ErreurFournisseur, match="« factice-a » est indisponible"):
        jouer(ml, demarrer)


def test_l_adresse_des_modeles():
    assert (
        adresse_modeles("https://api.mistral.ai/v1/chat/completions")
        == "https://api.mistral.ai/v1/models"
    )


# --------------------------------------------------------------------------- #
#  Dans l'application : posée au démarrage, publiée en métrique
# --------------------------------------------------------------------------- #
def test_l_api_demarre_avec_le_controle_et_publie_le_profil_actif(
    tmp_path, doublures, base_api, pgpass, service_doublure
):
    DoublureFournisseur.modeles = ["modele-a", "modele-b"]
    env = environnement(dsn_du_role(base_api.dsn_admin)) | CLES_FACTICES
    env["API_MODELE_VERIFIER_MODELES"] = "1"
    reglages = replace(
        charger(env, reglages_factices(tmp_path, doublures)), pool_attente_s=2.0
    )
    application = creer_application(
        reglages, instance=service_doublure.instance, environ=env
    )

    with TestClient(application, client=("127.0.0.1", 50000)) as client:
        texte = client.get("/metrics").text

    assert 'api_modele_profil_actif{profil="factice-a"} 1.0' in texte
    assert 'api_modele_profil_disponible{profil="factice-b"} 1.0' in texte
    assert len(doublures.recus()) == 2


def test_l_api_refuse_de_demarrer_si_le_controle_ecarte_le_profil_actif(
    tmp_path, doublures, base_api, pgpass, service_doublure
):
    DoublureFournisseur.modeles = ["modele-b"]
    env = environnement(dsn_du_role(base_api.dsn_admin)) | CLES_FACTICES
    env["API_MODELE_VERIFIER_MODELES"] = "1"
    reglages = replace(
        charger(env, reglages_factices(tmp_path, doublures)), pool_attente_s=2.0
    )
    application = creer_application(
        reglages, instance=service_doublure.instance, environ=env
    )

    with (
        pytest.raises(DemarrageRefuse, match="« factice-a » est indisponible"),
        TestClient(application),
    ):
        pass
