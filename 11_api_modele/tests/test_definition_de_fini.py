"""La définition de fini de la route de réponse (spec 04 de l'étape 3, B).

Ce qui doit exister, et le prouver : les tests « aucun nom de membre » et « deux
profils » sont présents ; aucune requête ne quitte le poste pendant la suite ; ni
`reglages.toml` ni `.env.example` ne portent une valeur de clé (motifs des hooks,
rejoués) ; la clé n'apparaît dans aucune sortie de la route ; le journal, les
métriques et le `contexte` disent la même chose d'une même requête.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from conftest import (
    BOUCLAGE,
    CLE_A,
    CLE_B,
    DELAI_FACTICE_S,
    QUESTION,
    RACINE_DEPOT,
    RACINE_MODULE,
    DoublureFournisseur,
    entetes,
)

from api_modele.configuration import FICHIER_REGLAGES
from api_modele.journal import FormatJson

TESTS = Path(__file__).resolve().parent
HOOK_CLES = RACINE_DEPOT / ".githooks" / "cles-fournisseurs"
CONVERSATION = [
    {"role": "libraire", "contenu": "Vous auriez des mangas de pirates ?"},
    {"role": "assistant", "contenu": "Oui, une série d'aventure en mer."},
]


def fonctions_de_test(fichier: str) -> set[str]:
    arbre = ast.parse((TESTS / fichier).read_text(encoding="utf-8"))
    return {
        n.name
        for n in ast.walk(arbre)
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
    }


# --------------------------------------------------------------------------- #
#  B4, B5 : les tests qui doivent exister
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("fichier", "noms"),
    [
        (
            "test_reponse.py",
            {
                # Aucun nom de membre (B4)
                "test_une_sortie_hors_contrat",  # dont le motif `pseudonyme`
                "test_un_nom_de_membre_dans_la_question_reformulee",
                "test_un_homonyme_admis_present_dans_un_passage_envoye",
                "test_le_meme_homonyme_absent_des_passages_de_la_requete",
                "test_un_nom_non_admis_present_dans_un_passage",
                # Deux profils factices, par le champ `profil` (B5)
                "test_deux_profils_factices_deux_doublures",
            },
        ),
        (
            "test_fournisseurs.py",
            # Deux profils factices, par la seule configuration (B5)
            {
                "test_changer_le_profil_actif_change_le_profil_appele_sans_autre_modification"
            },
        ),
    ],
)
def test_les_tests_de_la_definition_de_fini_existent(fichier, noms):
    assert noms <= fonctions_de_test(fichier)


def test_le_nom_de_membre_dans_la_reponse_est_un_cas_du_controle_de_sortie():
    texte = (TESTS / "test_reponse.py").read_text(encoding="utf-8")

    assert re.search(r'sortie\(reponse=f"Selon \{NOM\}.*"\), "pseudonyme"\)', texte)


# --------------------------------------------------------------------------- #
#  B6 : aucun appel réel
# --------------------------------------------------------------------------- #
def test_chaque_profil_du_harnais_pointe_le_bouclage(api_reponse):
    profils = api_reponse.app.state.etat.fournisseurs.profils  # type: ignore[attr-defined]

    assert set(profils) == {"factice-a", "factice-b"}
    for profil in profils.values():
        assert urlsplit(profil.adresse).hostname in BOUCLAGE, profil.nom


def test_la_garde_reseau_couvre_chaque_test_de_la_suite(request):
    sans_garde = [
        item.nodeid
        for item in request.session.items
        if "aucun_appel_hors_du_bouclage" not in getattr(item, "fixturenames", ())
    ]

    assert request.session.items
    assert sans_garde == []


def test_la_garde_reseau_bloque_une_requete_vers_un_fournisseur(
    aucun_appel_hors_du_bouclage,
):
    async def tenter() -> None:
        async with httpx.AsyncClient() as client:
            await client.get("https://api.mistral.ai/v1/models")

    with pytest.raises(AssertionError, match="hors du bouclage : api.mistral.ai"):
        asyncio.run(tenter())

    assert aucun_appel_hors_du_bouclage == ["api.mistral.ai"]
    aucun_appel_hors_du_bouclage.clear()  # la garde a fait son office


def motifs_des_hooks() -> dict[str, re.Pattern[str]]:
    """Les motifs d'aspect de `.githooks/cles-fournisseurs` (`CLES_ASPECT`), traduits
    des expressions POSIX du shell en expressions de Python."""
    texte = HOOK_CLES.read_text(encoding="utf-8")
    bloc = texte[texte.index("declare -A CLES_ASPECT=(") :]
    bloc = bloc[: bloc.index("\n)")]
    motifs: dict[str, re.Pattern[str]] = {}
    for variable, guillemet, motif in re.findall(
        r"^\s*\[(\w+)\]=([\"'])(.*)\2\s*$", bloc, flags=re.MULTILINE
    ):
        if guillemet == '"':
            motif = motif.replace('\\"', '"')
        motifs[variable] = re.compile(motif.replace("[[:space:]]", r"\s"))
    return motifs


def test_les_motifs_des_hooks_sont_lus_et_reconnaissent_une_cle():
    motifs = motifs_des_hooks()

    assert set(motifs) == {"MISTRAL_API_KEY", "OPENAI_API_KEY", "HF_TOKEN"}
    # Des formes de clés fabriquées ici (jamais écrites en clair dans le dépôt).
    assert motifs["MISTRAL_API_KEY"].search("MISTRAL" + "_API_KEY=" + "Ab1" * 8)
    assert motifs["OPENAI_API_KEY"].search(" s" + "k-" + "a1" * 21)
    assert motifs["HF_TOKEN"].search("h" + "f_" + "x9" * 17)


@pytest.mark.parametrize(
    "fichier", [FICHIER_REGLAGES, RACINE_MODULE / ".env.example"], ids=lambda p: p.name
)
def test_aucune_valeur_de_cle_dans_les_fichiers_versionnes(fichier):
    texte = fichier.read_text(encoding="utf-8")

    for variable, motif in motifs_des_hooks().items():
        assert not motif.search(texte), (fichier.name, variable)
    # Une variable de clé n'y reçoit jamais de valeur.
    affectations = re.findall(
        r"^[ \t]*#?[ \t]*(\w+_API_KEY)[ \t]*=[ \t]*([^\s#]*)", texte, re.M
    )
    assert all(valeur == "" for _, valeur in affectations), affectations


def test_env_example_nomme_les_cles_sans_valeur():
    texte = (RACINE_MODULE / ".env.example").read_text(encoding="utf-8")

    for variable in ("MISTRAL_API_KEY", "OPENAI_API_KEY"):
        assert re.search(rf"^{variable}=$", texte, re.M), variable


# --------------------------------------------------------------------------- #
#  B7 : la clé n'apparaît dans aucune sortie de la route
# --------------------------------------------------------------------------- #
def test_la_cle_n_apparait_dans_aucune_sortie_de_la_route(api_reponse, caplog):
    caplog.set_level(logging.DEBUG, logger="api_modele")
    corps: list[str] = []
    poster = lambda **champs: api_reponse.post(  # noqa: E731
        "/v1/reponse", json={"question": QUESTION, **champs}, headers=entetes()
    )
    corps.append(poster(historique=CONVERSATION).text)  # 200, deux appels
    corps.append(poster(profil="factice-b").text)
    DoublureFournisseur.statut = 401  # un refus dont le corps recopie la clé reçue
    DoublureFournisseur.corps_brut = json.dumps(
        {"error": {"message": f"clé refusée : Bearer {CLE_A}", "code": "x"}}
    ).encode()
    corps.append(poster().text)
    DoublureFournisseur.corps_brut, DoublureFournisseur.statut = None, 200
    DoublureFournisseur.retard_s = DELAI_FACTICE_S * 3
    corps.append(poster().text)

    sorties = [
        *corps,
        "\n".join(FormatJson().format(r) for r in caplog.records),
        api_reponse.get("/metrics").text,
        api_reponse.get("/v1/info", headers=entetes()).text,
    ]
    for texte in sorties:
        assert CLE_A not in texte and CLE_B not in texte
    assert [json.loads(c).get("code") for c in corps[2:]] == [
        "fournisseur_refus",
        "fournisseur_delai",
    ]


# --------------------------------------------------------------------------- #
#  B8 : traçabilité — le journal, les métriques et le contexte disent la même chose
# --------------------------------------------------------------------------- #
def valeur(metriques: str, nom: str, **etiquettes: str) -> float:
    entre = ",".join(f'{k}="{v}"' for k, v in etiquettes.items())
    for ligne in metriques.splitlines():
        if ligne.startswith(f"{nom}{{{entre}}} "):
            return float(ligne.split()[-1])
    raise AssertionError(f"{nom}{{{entre}}} absente")


def test_une_requete_complete_se_trace_de_la_meme_facon_partout(api_reponse, caplog):
    caplog.set_level(logging.INFO, logger="api_modele")
    DoublureFournisseur.usage = {"prompt_tokens": 120, "completion_tokens": 30}
    DoublureFournisseur.contenu_reformulation = json.dumps(
        {"question": "Une aventure de pirates en mer convient-elle à un adolescent ?"}
    )

    r = api_reponse.post(
        "/v1/reponse",
        json={"question": QUESTION, "historique": CONVERSATION},
        headers=entetes(),
    )

    assert r.status_code == 200
    contexte = r.json()["contexte"]
    lignes = [json.loads(FormatJson().format(e)) for e in caplog.records]
    appels = [x for x in lignes if x["evenement"] == "appel_modele"]
    (requete,) = [x for x in lignes if x.get("route") == "/v1/reponse"]
    metriques = api_reponse.get("/metrics").text
    profil, modele = contexte["profil"], contexte["modele_langage"]

    # Le même profil et le même modèle.
    assert {a["profil"] for a in appels} == {profil} == {requete["reponse"]["profil"]}
    assert {a["modele_demande"] for a in appels} == {modele}
    # Les mêmes jetons, appel par appel : contexte, journal, métriques.
    assert [(a["role"], a["jetons_entree"], a["jetons_sortie"]) for a in appels] == [
        (a["role"], a["jetons_entree"], a["jetons_sortie"]) for a in contexte["appels"]
    ]
    for a in contexte["appels"]:
        etiquettes = {"profil": profil, "role": a["role"]}
        assert (
            valeur(
                metriques, "api_modele_llm_jetons_total", **etiquettes, type="entree"
            )
            == a["jetons_entree"]
        )
        assert (
            valeur(
                metriques, "api_modele_llm_jetons_total", **etiquettes, type="sortie"
            )
            == a["jetons_sortie"]
        )
        assert (
            valeur(metriques, "api_modele_llm_appels_total", **etiquettes, statut="ok")
            == 1
        )
    # Le même coût : la somme des appels, partout.
    cout = contexte["cout_estime_usd"]
    assert cout == pytest.approx(2 * (120 * 2 + 30 * 8) / 1e6)
    assert sum(a["cout_estime_usd"] for a in appels) == pytest.approx(cout)
    assert requete["reponse"]["cout_estime_usd"] == pytest.approx(cout)
    assert valeur(metriques, "api_modele_llm_cout_usd_total", profil=profil) == (
        pytest.approx(cout)
    )
    # Le même identifiant de requête.
    assert (
        {a["identifiant_requete"] for a in appels}
        == {contexte["identifiant_requete"]}
        == {requete["identifiant_requete"]}
    )
