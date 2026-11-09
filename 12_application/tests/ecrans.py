"""Les écrans et les cas de l'application : un seul catalogue, pour les tests de
balisage (sans navigateur) et pour l'audit axe (dans Chromium)."""

from __future__ import annotations

import json
from dataclasses import dataclass

import doublure_api as d


def echanges(n: int) -> str:
    tours = []
    for i in range(n):
        tours += [
            {"role": "libraire", "contenu": f"Question factice {i} ?"},
            {"role": "assistant", "contenu": f"Série factice A [S:101], réponse {i}."},
        ]
    return json.dumps(tours, ensure_ascii=False)


@dataclass(frozen=True)
class Ecran:
    nom: str
    chemin: str = "/"
    #: Une question : la page vient d'un `POST /question`.
    question: str | None = None
    historique: str = ""
    info: str = "nominale"
    reponse: str = "nominale"
    profil: str = "mistral-large-2512"
    temperature: str = "0.0"
    #: L'application pointe une adresse où rien n'écoute.
    api_fermee: bool = False
    #: L'application n'a jamais lu de liste : une instance neuve.
    neuve: bool = False
    #: Un délai de réponse réduit : la doublure le dépasse.
    delai_court: bool = False
    #: La case « Mode démonstration », cochée dans le formulaire envoyé.
    demonstration: bool = False
    #: Le scénario du catalogue (l'API data du 02) dans la doublure.
    catalogue: str = "nominal"
    #: Le rôle du compte connecté (E4, lot 2b : l'écran du conseiller selon le rôle) ;
    #: None : un visiteur. Un rôle demande une base jetable.
    role: str | None = None
    #: Une borne atteinte avant l'écran (E4, lot 2b) : `session`, `client`, `debit`,
    #: `budget`, `inscription`. Son application la serre à une unité
    #: (`BORNES_SERREES`) ; l'écran vient après un premier passage. Une base jetable.
    borne: str | None = None


QUESTION = "Un manga factice pour débuter ?"
LONGUE = json.dumps(
    [
        {"role": "libraire", "contenu": "y" * 1990},
        {"role": "assistant", "contenu": "y" * 1990},
    ]
)
#: Les cas dégradés : les scénarios de `/v1/reponse` qui ne rendent pas une réponse.
DEGRADES = sorted(
    nom
    for nom, scenario in d.SCENARIOS_REPONSE.items()
    if nom != "delai" and (scenario().statut != 200 or scenario().brut is not None)
)

ECRANS: tuple[Ecran, ...] = (
    Ecran("page_vide"),
    Ecran("deux_modes", info="deux_modes", role="libraire"),
    Ecran("aucun_profil", info="aucun_profil"),
    Ecran("cle_refusee", info="cle_invalide", neuve=True),
    Ecran("api_indisponible", api_fermee=True),
    Ecran("reponse", question=QUESTION),
    Ecran("reponse_reformulee", question=QUESTION, reponse="reformulee"),
    Ecran("reponse_titres", question=QUESTION, reponse="titre_formes"),
    Ecran(
        "reponse_metadonnees_nulles", question=QUESTION, reponse="metadonnees_nulles"
    ),
    Ecran("reponse_sans_passage", question=QUESTION, reponse="sans_fragment"),
    Ecran("reponse_refus_debit", question=QUESTION, reponse="refus_debit_rejoue"),
    Ecran(
        "reponse_complement", question=QUESTION, reponse="complement", role="libraire"
    ),
    Ecran("refus_aucun_passage", question=QUESTION, reponse="refus_aucun_passage"),
    Ecran("refus_hors_catalogue", question=QUESTION, reponse="refus_hors_catalogue"),
    Ecran("refus_inconnu", question=QUESTION, reponse="refus_inconnu"),
    Ecran("reponse_demonstration", question=QUESTION, demonstration=True, role="admin"),
    Ecran(
        "refus_demonstration",
        question=QUESTION,
        reponse="refus_aucun_passage",
        demonstration=True,
        role="admin",
    ),
    Ecran("conversation_3_tours", question=QUESTION, historique=echanges(1)),
    Ecran("conversation_5_tours", question=QUESTION, historique=echanges(2)),
    Ecran("conversation_close", question=QUESTION, historique=echanges(4)),
    Ecran("conversation_trop_longue", question=QUESTION, historique=LONGUE),
    Ecran("conversation_pleine", question=QUESTION, historique=echanges(5)),
    Ecran("conversation_invalide", question=QUESTION, historique="{cassé"),
    Ecran("erreur_question_vide", question=""),
    Ecran("erreur_question_longue", question="x" * 501),
    Ecran("erreur_profil", question=QUESTION, profil="profil-inconnu", role="libraire"),
    Ecran("erreur_temperature", question=QUESTION, temperature="0.9", role="libraire"),
    *(Ecran(f"degrade_{nom}", question=QUESTION, reponse=nom) for nom in DEGRADES),
    Ecran("degrade_api_injoignable", question=QUESTION, api_fermee=True),
    Ecran("degrade_delai", question=QUESTION, reponse="delai", delai_court=True),
    Ecran("a_propos", chemin="/a-propos"),
    Ecran("a_propos_sans_api", chemin="/a-propos", api_fermee=True),
    Ecran("page_404", chemin="/route-inconnue"),
    # E4, lot 2a : le catalogue et la fiche de série.
    Ecran("catalogue", chemin="/catalogue"),
    Ecran(
        "catalogue_resultats",
        chemin="/catalogue?q=factice&page=2",
        catalogue="plusieurs_pages",
    ),
    Ecran("catalogue_vide", chemin="/catalogue?q=zz", catalogue="vide"),
    Ecran("catalogue_erreur_saisie", chemin="/catalogue?q=a"),
    Ecran(
        "catalogue_indisponible",
        chemin="/catalogue?q=factice",
        catalogue="indisponible",
    ),
    Ecran("serie", chemin="/series/101"),
    Ecran("serie_sans_note", chemin="/series/101", catalogue="serie_sans_note"),
    Ecran("serie_introuvable", chemin="/series/101", catalogue="serie_inconnue"),
    # E4, lot 3 : sans base, la fiche s'affiche avec l'encart des critiques du réseau.
    Ecran("serie_critiques_indisponibles", chemin="/series/102"),
    # E4, lot 2b : l'écran du conseiller selon le rôle, le pont fiche ↔ conseiller.
    Ecran("conseiller_public"),
    Ecran("conseiller_client", role="client"),
    Ecran("conseiller_libraire", info="deux_modes", role="libraire"),
    Ecran("conseiller_admin", info="deux_modes", role="admin"),
    Ecran("conseiller_prerempli", chemin="/?serie=101"),
    Ecran("reponse_refus_avec_pont", question=QUESTION, reponse="refus"),
    Ecran("reponse_sans_carte_avec_pont", question=QUESTION, reponse="sans_carte"),
    # E4, lot 2b : « Vouliez-vous dire… ? » et les sources du résumé (contrat 0.6.0).
    Ecran(
        "catalogue_suggestions", chemin="/catalogue?q=narutp", catalogue="suggestions"
    ),
    Ecran("serie_resume_kitsu", chemin="/series/101", catalogue="resume_kitsu"),
    Ecran("serie_deux_resumes", chemin="/series/101", catalogue="deux_resumes"),
    Ecran("serie_sans_resume", chemin="/series/101", catalogue="sans_resume"),
    # E4, lot 2b : chaque borne du conseiller, atteinte.
    Ecran("conseiller_quota_atteint", question=QUESTION, borne="session"),
    Ecran(
        "conseiller_quota_client_atteint",
        question=QUESTION,
        borne="client",
        role="client",
    ),
    Ecran("conseiller_debit_atteint", question=QUESTION, borne="debit"),
    Ecran("conseiller_budget_atteint", question=QUESTION, borne="budget"),
    Ecran("inscription_bornee", chemin="/inscription", borne="inscription"),
    # E4, lot 4 : les listes du client — la zone de la fiche selon le rôle, le geste sur
    # les cartes du conseiller. Les pages du compte (« Mes listes », la fiche avec un
    # état, la suppression) sont des écrans des comptes (`test_audit_axe`), comme au
    # lot 3 ; leur balisage est éprouvé par `test_listes` et `test_effacement`.
    Ecran("serie_listes_visiteur", chemin="/series/101"),
    Ecran("serie_listes_client", chemin="/series/102", role="client"),
    Ecran("reponse_cartes_client", question=QUESTION, role="client"),
)

#: La borne → ce que son application serre (`Bornes`) : une unité, la page qui suit le
#: premier passage la dépasse.
BORNES_SERREES: dict[str, dict[str, float]] = {
    "session": {"quota_visiteur_questions_jour": 1},
    "client": {"quota_client_questions_jour": 1},
    "debit": {"debit_public_par_minute": 1},
    "budget": {"cout_jour_max_usd": 0.0001},
    "inscription": {"inscriptions_session_max": 1},
}
#: Le mot de passe des inscriptions des écrans bornés.
MDP_INSCRIPTION = "mot de passe factice de l'inscription bornée"
