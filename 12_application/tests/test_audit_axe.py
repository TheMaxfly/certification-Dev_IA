"""L'audit d'accessibilité : axe-core dans Chromium, sur chaque écran et chaque cas.

Sauté hors de `make verify-poste` (`AUDIT_AXE=1`), et compté : il demande un
navigateur, installé une fois (`uv run --extra audit playwright install chromium`). La
politique de sécurité du contenu reste active : axe entre par le protocole du
navigateur, toute violation lue en console est un échec. Les `<details>` sont ouverts,
sinon leur contenu n'est pas audité ; le formulaire est soumis, sinon une page issue
d'un `POST` n'existe pas.

Verdict : aucune violation `serious` ou `critical`. Le test réécrit `rapport_axe.json`
et `rapport_axe.md` ; un diff après un passage signale un changement à commiter.

E4, lot 1 : les écrans des comptes (inscription, connexion, mot de passe,
administration, critiques) et leurs refus, et les pages neuves d'erreur (403 de rôle,
formulaire expiré, comptes indisponibles), sur une base jetable ; les connexions se font
dans le navigateur, par les formulaires eux-mêmes.

E4, lot 2b : les écrans du conseiller d'un rôle connecté (`Ecran.role`) passent par
l'application à base, chaque rôle dans son contexte.

E4, lot 4 : les listes du client (« Mes listes » vide, garnie, sans catalogue ; la
fiche avec un état ; la zone de la fiche d'un visiteur et d'un client ; les cartes d'un
client) et la suppression du compte (confirmation, refus, interdite à un libraire,
l'accueil après).

E4, lot 3 : les écrans des critiques (« Mes critiques » pleine, vide, sans catalogue ;
écrire, refus, modifier, retirer, accès refusé ; la fiche avec critiques ; la
modération, sa liste vide, sa confirmation), sur la même base, où trois critiques
factices sont posées ; le catalogue y est servi par la doublure, son scénario choisi
écran par écran.
"""

from __future__ import annotations

import json
import os
import socket
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from conftest import HACHEUR_RAPIDE, ServeurLocal
from doublure_api import CLE_CATALOGUE_ATTENDUE, Doublure
from ecrans import BORNES_SERREES, ECRANS, MDP_INSCRIPTION, Ecran

from application.app import creer_application

ACCESSIBILITE = Path(__file__).resolve().parents[1] / "accessibilite"
ETIQUETTES = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]
GRAVES = ("serious", "critical")
#: Les défauts modérés ou mineurs laissés, avec leur motif : écrits au rapport.
INSCRITS: dict[str, str] = {}
#: Ce qu'axe ne sait pas juger, avec ce qui le règle : écrit au rapport.
A_REVOIR = {
    "color-contrast": "le chevron d'une liste déroulante ou l'icône d'un champ invalide"
    " est une image de fond : axe ne déduit pas la couleur du fond. Le couple de ces"
    " champs est fixé par `theme.css`, et son contraste calculé par un test"
    " (`test_wcag_1_4_3_…`). À relire à l'œil.",
}
#: L'élément qui devrait avoir le focus l'a-t-il, une fois la page chargée ?
FOCUS = """() => {
  const attendu = document.querySelector("[autofocus]");
  return attendu === null ? null : attendu === document.activeElement;
}"""
PREPARER = """(valeurs) => {
  const form = document.querySelector("main form");
  form.noValidate = true;
  const question = document.querySelector("#question");
  question.removeAttribute("maxlength");
  question.value = valeurs.question;
  document.querySelector("input[name=historique]").value = valeurs.historique;
  const profil = document.querySelector("#profil");
  if (profil) {
    const option = document.createElement("option");
    option.value = valeurs.profil;
    profil.append(option);
    profil.value = valeurs.profil;
  }
  const radio = document.querySelector("input[name=temperature]:checked");
  if (radio) radio.value = valeurs.temperature;
  if (valeurs.demonstration) {
    document.querySelector("#mode_demonstration").checked = true;
  }
}"""
OUVRIR = "() => document.querySelectorAll('details').forEach((d) => { d.open = true; })"
LANCER = """(etiquettes) => axe.run(document, {
  runOnly: {type: "tag", values: etiquettes},
  resultTypes: ["violations", "incomplete"],
})"""


def ouvrir_ecran(page: Any, adresse: str, ecran: Ecran) -> None:
    if ecran.question is None:
        page.goto(adresse + ecran.chemin)
        return
    page.goto(adresse + "/")
    page.evaluate(
        PREPARER,
        {
            "question": ecran.question,
            "historique": ecran.historique,
            "profil": ecran.profil,
            "temperature": ecran.temperature,
            "demonstration": ecran.demonstration,
        },
    )
    with page.expect_navigation():
        page.click("button[type=submit]")


def inscrire_dans(page: Any, adresse: str, pseudo: str) -> None:
    page.goto(adresse + "/inscription")
    page.fill("#pseudo", pseudo)
    page.fill("#mot_de_passe", MDP_INSCRIPTION)
    page.fill("#confirmation", MDP_INSCRIPTION)
    with page.expect_navigation():
        page.click("main form button[type=submit]")


def ouvrir_borne(page: Any, adresse: str, ecran: Ecran) -> None:
    """Un écran borné (E4, lot 2b) : un premier passage, puis la page qui dépasse la
    borne de son application (serrée à une unité)."""
    if ecran.borne == "inscription":
        inscrire_dans(page, adresse, "inscrit_borne_audit_1")
        with page.expect_navigation():
            page.click('header form[action="/deconnexion"] button')
        inscrire_dans(page, adresse, "inscrit_borne_audit_2")
        return
    ouvrir_ecran(page, adresse, ecran)
    ouvrir_ecran(page, adresse, ecran)


def resume(brut: dict[str, Any], adresse: str) -> dict[str, Any]:
    """La sortie d'axe, sans son horodatage ni le port du jour ; passées et non
    applicables en identifiants : le rapport ne change que si le résultat change."""
    texte = json.dumps(
        {
            "violations": brut["violations"],
            "incomplete": brut["incomplete"],
            "passes": sorted(r["id"] for r in brut["passes"]),
            "inapplicable": sorted(r["id"] for r in brut["inapplicable"]),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return json.loads(texte.replace(adresse, "http://application"))


def ecrire_rapport(
    pages: dict[str, dict[str, Any]],
    version_axe: str,
    navigateur: str,
    console: list[str],
) -> None:
    """Rien à écrire si le résultat ne change pas : la date seule ne salit rien."""
    resultat = {
        "axe": version_axe,
        "navigateur": navigateur,
        "etiquettes": ETIQUETTES,
        "pages": pages,
    }
    json_ = ACCESSIBILITE / "rapport_axe.json"
    if json_.exists():
        precedent = json.loads(json_.read_text(encoding="utf-8"))
        precedent.pop("date", None)
        if precedent == resultat:
            return
    date = datetime.now(UTC).date().isoformat()
    json_.write_text(
        json.dumps(
            {"date": date, **resultat}, ensure_ascii=False, indent=1, sort_keys=True
        )
        + "\n",
        encoding="utf-8",
    )
    comptes: dict[str, int] = {}
    defauts: dict[str, dict[str, Any]] = {}
    for nom, page in pages.items():
        for violation in page["violations"]:
            gravite = violation["impact"] or "inconnue"
            comptes[gravite] = comptes.get(gravite, 0) + 1
            defaut = defauts.setdefault(
                violation["id"],
                {"gravite": gravite, "aide": violation["help"], "pages": []},
            )
            defaut["pages"].append(nom)
    lignes = [
        "# Audit d'accessibilité de l'application",
        "",
        f"- **Date** : {date}.",
        f"- **Outil** : axe-core {version_axe}, dans Chromium {navigateur}.",
        f"- **Niveau visé** : WCAG 2.1 AA (étiquettes : {', '.join(ETIQUETTES)}).",
        f"- **Pages auditées** : {len(pages)}, chaque écran et chaque cas dégradé,"
        " servis contre la doublure de l'API ; politique de sécurité du contenu active,"
        " `<details>` ouverts, formulaire soumis dans le navigateur.",
        "- **Violations par gravité** : "
        + ", ".join(
            f"{g} {comptes.get(g, 0)}"
            for g in ("critical", "serious", "moderate", "minor")
        )
        + ".",
        f"- **Violations de la politique de sécurité en console** : {len(console)}.",
        "",
        "Un outil automatique ne couvre qu'une partie des critères, de l'ordre d'un"
        " tiers : le reste est relu à la main (README, « Accessibilité »).",
        "",
        "## Défauts restants",
        "",
    ]
    if defauts:
        lignes += ["| Règle | Gravité | Aide | Pages | Sort |", "|---|---|---|---|---|"]
        for regle, defaut in sorted(defauts.items()):
            sort = INSCRITS.get(regle, "à corriger")
            lignes.append(
                f"| `{regle}` | {defaut['gravite']} | {defaut['aide']}"
                f" | {len(defaut['pages'])} | {sort} |"
            )
    else:
        lignes.append("Aucun.")
    a_revoir: dict[str, set[str]] = {}
    for page in pages.values():
        for v in page["incomplete"]:
            cibles = a_revoir.setdefault(v["id"], set())
            cibles.update(str(c) for n in v["nodes"] for c in n["target"])
    lignes += ["", "## À revoir à la main", ""]
    lignes += [
        f"- `{regle}`, sur `{'`, `'.join(sorted(cibles))}` :"
        f" {A_REVOIR.get(regle, 'à relire.')}"
        for regle, cibles in sorted(a_revoir.items())
    ] or ["Rien."]
    lignes += [
        "",
        "## Par page",
        "",
        "| Page | Règles passées | Violées | À revoir | Non applicables |",
        "|---|---:|---:|---:|---:|",
    ]
    for nom, page in sorted(pages.items()):
        lignes.append(
            f"| `{nom}` | {len(page['passes'])} | {len(page['violations'])}"
            f" | {len(page['incomplete'])} | {len(page['inapplicable'])} |"
        )
    (ACCESSIBILITE / "rapport_axe.md").write_text(
        "\n".join(lignes) + "\n", encoding="utf-8"
    )


@pytest.mark.skipif(
    os.environ.get("AUDIT_AXE") != "1",
    reason="audit d'accessibilité : navigateur, poste seulement (make verify-poste)",
)
def test_audit_axe_sur_chaque_ecran_et_chaque_cas(reglages, base_app, base):
    from playwright.sync_api import sync_playwright

    with psycopg.connect(base) as cx:
        for pseudo, role, a_changer in COMPTES_AUDIT:
            cx.execute(
                "INSERT INTO application.comptes"
                " (pseudo, nom_affiche, role, hachage, doit_changer_mdp)"
                " VALUES (%s, %s, %s, %s, %s)",
                (pseudo, f"Nom {pseudo}", role, HACHEUR_RAPIDE.hacher(MDP), a_changer),
            )
        # Les critiques de l'audit (identifiants 1 à 3, base neuve) : la série entière,
        # modifiée ; un tome ; une retirée, d'une autre série.
        for tome, libelle, note, label, statut, serie in CRITIQUES_AUDIT:
            cx.execute(
                "INSERT INTO application.critiques (compte_id, series_id, volume_url,"
                " volume_libelle, texte, note, label, statut, modifie_le) VALUES ("
                " (SELECT id FROM application.comptes WHERE pseudo = 'libraire_audit'),"
                " %s, %s, %s, %s, %s, %s, %s,"
                " now() + CASE WHEN %s THEN interval '1 day' ELSE interval '0' END)",
                (
                    serie,
                    tome,
                    libelle,
                    TEXTE_AUDIT,
                    note,
                    label,
                    statut,
                    label is not None,
                ),
            )
        for serie, etat in LISTES_AUDIT:
            cx.execute(
                "INSERT INTO application.listes (compte_id, series_id, etat) VALUES ("
                " (SELECT id FROM application.comptes WHERE pseudo = 'listes_audit'),"
                " %s, %s)",
                (serie, etat),
            )

    source = (ACCESSIBILITE / "axe.min.js").read_text(encoding="utf-8")
    doublure = Doublure()
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        fermee = f"http://127.0.0.1:{prise.getsockname()[1]}"
    pages: dict[str, dict[str, Any]] = {}
    console: list[str] = []
    sans_focus: list[str] = []
    #: La case cochée dans le navigateur doit produire la section, et elle seule.
    ecarts_de_trace: list[str] = []

    def adresse_de(reglee: str, **delais: float) -> Any:
        reduits = replace(reglages.delais, **delais)
        # Le catalogue (E4, lot 2a) : servi par la même doublure, sous sa clé.
        catalogue = replace(
            reglages.catalogue, adresse=adresse_doublure, cle=CLE_CATALOGUE_ATTENDUE
        )
        return creer_application(
            replace(reglages, adresse_api=reglee, delais=reduits, catalogue=catalogue)
        )

    with (
        ServeurLocal(doublure.app) as adresse_doublure,
        ServeurLocal(adresse_de(adresse_doublure)) as adresse,
        ServeurLocal(adresse_de(fermee)) as adresse_sans_api,
        ServeurLocal(adresse_de(adresse_doublure)) as adresse_neuve,
        ServeurLocal(adresse_de(adresse_doublure, reponse_s=0.2)) as adresse_pressee,
        ServeurLocal(
            creer_application(
                replace(
                    reglages,
                    adresse_api=adresse_doublure,
                    base_dsn=base_app,
                    cle_session="a" * 48,
                    catalogue=replace(
                        reglages.catalogue,
                        adresse=adresse_doublure,
                        cle=CLE_CATALOGUE_ATTENDUE,
                    ),
                ),
                hacheur=HACHEUR_RAPIDE,
            )
        ) as adresse_comptes,
        sync_playwright() as playwright,
    ):
        navigateur = playwright.chromium.launch(headless=True)
        contexte = navigateur.new_context()
        # E4, lot 2b : l'écran du conseiller selon le rôle, sur l'application à base,
        # chaque rôle dans son contexte, connecté par le formulaire.
        par_role: dict[str, Any] = {}
        for role in sorted({e.role for e in ECRANS if e.role is not None}):
            par_role[role] = navigateur.new_context()
            connecter_dans(par_role[role], adresse_comptes, f"conseiller_{role}_audit")
        reglages_comptes = replace(
            reglages,
            adresse_api=adresse_doublure,
            base_dsn=base_app,
            cle_session="a" * 48,
            catalogue=replace(
                reglages.catalogue, adresse=adresse_doublure, cle=CLE_CATALOGUE_ATTENDUE
            ),
        )
        for ecran in ECRANS:
            httpx.post(
                f"{adresse_doublure}/_essai/scenario",
                json={
                    "info": ecran.info,
                    "reponse": ecran.reponse,
                    "catalogue": ecran.catalogue,
                },
            ).raise_for_status()
            pile = ExitStack()
            if ecran.borne is not None:
                # Son application, sa borne serrée, un contexte neuf (les cookies ne
                # séparent pas les ports).
                serree = replace(
                    reglages_comptes,
                    bornes=replace(
                        reglages_comptes.bornes, **BORNES_SERREES[ecran.borne]
                    ),
                )
                adresse_borne = pile.enter_context(
                    ServeurLocal(creer_application(serree, hacheur=HACHEUR_RAPIDE))
                )
                contexte_page = navigateur.new_context()
                pile.callback(contexte_page.close)
                if ecran.role is not None:
                    connecter_dans(
                        contexte_page, adresse_borne, f"conseiller_{ecran.role}_audit"
                    )
            elif ecran.role is not None:
                contexte_page = par_role[ecran.role]
            else:
                contexte_page = contexte
            page = contexte_page.new_page()
            page.on(
                "console",
                lambda m, nom=ecran.nom: (
                    console.append(f"{nom} : {m.text}")
                    if "Content Security Policy" in m.text
                    else None
                ),
            )
            base = adresse
            if ecran.borne is not None:
                base = adresse_borne
            elif ecran.role is not None:
                base = adresse_comptes
            elif ecran.api_fermee:
                base = adresse_sans_api
            elif ecran.neuve:
                base = adresse_neuve
            elif ecran.delai_court:
                base = adresse_pressee
            if ecran.borne is not None:
                ouvrir_borne(page, base, ecran)
            else:
                ouvrir_ecran(page, base, ecran)
            if page.evaluate(FOCUS) is False:
                sans_focus.append(ecran.nom)
            if (page.query_selector("#trace") is not None) != ecran.demonstration:
                ecarts_de_trace.append(ecran.nom)
            page.evaluate(OUVRIR)
            page.evaluate(source)
            pages[ecran.nom] = resume(page.evaluate(LANCER, ETIQUETTES), base)
            page.close()
            pile.close()
        version_axe = page_version(contexte, source)
        version_navigateur = navigateur.version
        navigateur.close()
        des_comptes, console_comptes, sans_focus_comptes = auditer_les_comptes(
            playwright, source, adresse_comptes, adresse, adresse_doublure
        )
        pages |= des_comptes
        console += console_comptes
        sans_focus += sans_focus_comptes

    assert not doublure.ecarts, doublure.ecarts
    ecrire_rapport(pages, version_axe, version_navigateur, console)
    graves = [
        (nom, v["id"], v["impact"])
        for nom, page in pages.items()
        for v in page["violations"]
        if v["impact"] in GRAVES
    ]
    assert not graves, graves
    assert not console, console
    assert not sans_focus, sans_focus
    assert not ecarts_de_trace, ecarts_de_trace
    assert len(pages) == len(ECRANS) + len(ECRANS_COMPTES) + 1


@dataclass(frozen=True)
class EcranCompte:
    """Un écran des comptes : `compte`, qui est connecté avant (None : un visiteur) ;
    `titre`, le titre de niveau 1 attendu — l'écran audité est bien celui qu'on croit ;
    `saisies` et `bouton`, le formulaire rempli puis envoyé ; `action`, l'adresse où
    l'envoyer à la place de la sienne (une page qu'aucun lien n'atteint) ; `catalogue`,
    le scénario du catalogue dans la doublure (E4, lot 3)."""

    nom: str
    compte: str | None
    chemin: str
    titre: str
    saisies: dict[str, str] = field(default_factory=dict)
    bouton: str | None = None
    action: str | None = None
    catalogue: str = "nominal"


MDP = "mot de passe factice de l'audit"
NOUVEAU = "nouveau mot de passe factice de l'audit"
ENVOYER = "main form button[type=submit]"
#: Les comptes de l'audit : (pseudo, rôle, mot de passe à changer) ; et assez de
#: clients pour que la liste de l'administration ait une seconde page.
COMPTES_AUDIT = (
    ("admin_audit", "admin", False),
    ("client_audit", "client", False),
    ("libraire_audit", "libraire", False),
    ("initial_audit", "libraire", True),
    *((f"lecteur_audit_{i:02d}", "client", False) for i in range(26)),
    ("libraire_tiers_audit", "libraire", False),
    ("libraire_vide_audit", "libraire", False),
    # E4, lot 2b : les écrans du conseiller selon le rôle (`Ecran.role`).
    ("conseiller_client_audit", "client", False),
    ("conseiller_libraire_audit", "libraire", False),
    ("conseiller_admin_audit", "admin", False),
    # E4, lot 4 : les listes du client (`LISTES_AUDIT`), une liste vide.
    ("listes_audit", "client", False),
    ("listes_vide_audit", "client", False),
)
#: Les listes de `listes_audit` : (series_id, état) — une série par liste.
LISTES_AUDIT = ((101, "favori"), (102, "a_lire"), (103, "a_commander"))
TEXTE_AUDIT = (
    "Texte factice d'une critique d'audit, assez long pour tenir dans les bornes.\n\n"
    "Un second paragraphe factice, avec <b>du balisage</b> rendu littéralement."
)
#: (volume_url, volume_libelle, note, label, statut, series_id) : les critiques 1 à 3.
CRITIQUES_AUDIT = (
    (None, None, 8, "coup_de_coeur", "publiee", 101),
    (
        "https://catalogue.invalid/serie/101/volume/1",
        "Tome 1 — Premier tome factice (2001)",
        6,
        None,
        "publiee",
        101,
    ),
    (None, None, 4, None, "retiree", 102),
)
ECRANS_COMPTES: tuple[EcranCompte, ...] = (
    EcranCompte("inscription", None, "/inscription", "Créer un compte"),
    EcranCompte(
        "inscription_refusee",
        None,
        "/inscription",
        "Créer un compte",
        {"#pseudo": "ab", "#mot_de_passe": MDP, "#confirmation": MDP},
        ENVOYER,
    ),
    EcranCompte("connexion", None, "/connexion", "Connexion"),
    EcranCompte(
        "connexion_refusee",
        None,
        "/connexion",
        "Connexion",
        {"#pseudo": "inconnu_audit", "#mot_de_passe": MDP},
        ENVOYER,
    ),
    EcranCompte(
        "formulaire_expire",
        None,
        "/connexion",
        "Formulaire expiré",
        {"input[name=jeton]": "faux"},
        ENVOYER,
    ),
    EcranCompte(
        "connexion_requise",
        None,
        "/connexion",
        "Connexion requise",
        {"#pseudo": "inconnu_audit", "#mot_de_passe": MDP},
        ENVOYER,
        action="/compte/mot-de-passe",
    ),
    EcranCompte(
        "mot_de_passe", "admin_audit", "/compte/mot-de-passe", "Changer le mot de passe"
    ),
    EcranCompte(
        "mot_de_passe_refuse",
        "admin_audit",
        "/compte/mot-de-passe",
        "Changer le mot de passe",
        {"#ancien": "faux faux faux", "#nouveau": MDP, "#confirmation": MDP},
        ENVOYER,
    ),
    EcranCompte(
        "mot_de_passe_change",
        "libraire_audit",
        "/compte/mot-de-passe",
        "Changer le mot de passe",
        {"#ancien": MDP, "#nouveau": NOUVEAU, "#confirmation": NOUVEAU},
        ENVOYER,
    ),
    EcranCompte(
        "mot_de_passe_initial",
        "initial_audit",
        "/",
        "Changer le mot de passe",
    ),
    EcranCompte("admin_comptes", "admin_audit", "/admin/comptes", "Comptes"),
    EcranCompte(
        "admin_refuse",
        "admin_audit",
        "/admin/comptes",
        "Comptes",
        {
            "#pseudo": "ab",
            "#nom_affiche": "Nom",
            "#mot_de_passe": MDP,
            "#confirmation": MDP,
        },
        'form[action="/admin/comptes"][method=post] button[type=submit]',
    ),
    EcranCompte(
        "admin_seconde_page_et_avis",
        "admin_audit",
        "/admin/comptes?page=2&etat=desactive",
        "Comptes",
    ),
    # E4, lot 3 : les critiques.
    EcranCompte(
        "critiques_liste", "libraire_audit", "/libraire/critiques", "Mes critiques"
    ),
    EcranCompte(
        "critiques_vide", "libraire_vide_audit", "/libraire/critiques", "Mes critiques"
    ),
    EcranCompte(
        "critiques_catalogue_indisponible",
        "libraire_audit",
        "/libraire/critiques",
        "Mes critiques",
        catalogue="indisponible",
    ),
    EcranCompte(
        "critique_nouvelle",
        "libraire_audit",
        "/libraire/critiques/nouvelle?series_id=102",
        "Écrire une critique",
    ),
    EcranCompte(
        "critique_nouvelle_refusee",
        "libraire_audit",
        "/libraire/critiques/nouvelle?series_id=102",
        "Écrire une critique",
        {"#texte": "court"},
        ENVOYER,
    ),
    EcranCompte(
        "critique_modifier",
        "libraire_audit",
        "/libraire/critiques/1/modifier",
        "Modifier ma critique",
    ),
    EcranCompte(
        "critique_retirer",
        "libraire_audit",
        "/libraire/critiques/1/retirer",
        "Retirer ma critique",
    ),
    EcranCompte(
        "critique_acces_refuse",
        "libraire_tiers_audit",
        "/libraire/critiques/1/modifier",
        "Accès refusé",
    ),
    EcranCompte(
        "serie_avec_critiques",
        "libraire_audit",
        "/series/101?etat=critique_publiee",
        "Série factice A",
    ),
    EcranCompte(
        "admin_critiques", "admin_audit", "/admin/critiques", "Modération des critiques"
    ),
    EcranCompte(
        "admin_critiques_vide",
        "admin_audit",
        "/admin/critiques?libraire=2",
        "Modération des critiques",
    ),
    EcranCompte(
        "admin_critique_retirer",
        "admin_audit",
        "/admin/critiques/2/retirer",
        "Retirer une critique (modération)",
    ),
    # E4, lot 4 : les listes du client.
    EcranCompte(
        "serie_listes_client_etat",
        "listes_audit",
        "/series/101?etat=ajoutee",
        "Série factice A",
    ),
    EcranCompte(
        "mes_listes_vides", "listes_vide_audit", "/compte/listes", "Mes listes"
    ),
    EcranCompte(
        "mes_listes_garnies",
        "listes_audit",
        "/compte/listes?etat=retiree",
        "Mes listes",
    ),
    EcranCompte(
        "mes_listes_catalogue_indisponible",
        "listes_audit",
        "/compte/listes",
        "Mes listes",
        catalogue="indisponible",
    ),
    EcranCompte("acces_refuse", "client_audit", "/admin/comptes", "Accès refusé"),
    EcranCompte(
        "introuvable_connecte", "client_audit", "/nimporte", "Page introuvable"
    ),
)
REMPLIR = """([saisies, action]) => {
  if (action !== null) { document.querySelector("main form").action = action; }
  for (const [selecteur, valeur] of Object.entries(saisies)) {
    const champ = document.querySelector(selecteur);
    champ.removeAttribute("maxlength");
    champ.value = valeur;
  }
  document.querySelectorAll("form").forEach((f) => { f.noValidate = true; });
}"""


def auditer(page: Any, source: str, base: str) -> dict[str, Any]:
    page.evaluate(OUVRIR)
    page.evaluate(source)
    return resume(page.evaluate(LANCER, ETIQUETTES), base)


def connecter_dans(contexte: Any, adresse: str, pseudo: str) -> None:
    """Ouvre la session du compte dans ce contexte, et s'assure qu'elle l'est : sans
    quoi l'audit passerait sur la page de connexion refusée, sous le nom d'un autre."""
    page = contexte.new_page()
    page.goto(adresse + "/connexion")
    page.fill("#pseudo", pseudo)
    page.fill("#mot_de_passe", MDP)
    with page.expect_navigation():
        page.click(ENVOYER)
    menu = page.inner_text("header nav")
    assert "/connexion" not in page.url and "Mon compte" in menu, pseudo
    page.close()


def auditer_les_comptes(
    playwright: Any,
    source: str,
    adresse: str,
    adresse_sans_base: str,
    adresse_doublure: str,
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    """Les écrans des comptes, chacun dans le contexte de son visiteur."""
    pages: dict[str, dict[str, Any]] = {}
    console: list[str] = []
    sans_focus: list[str] = []
    navigateur = playwright.chromium.launch(headless=True)
    contextes: dict[str | None, Any] = {}
    for ecran in ECRANS_COMPTES:
        nom, compte = ecran.nom, ecran.compte
        httpx.post(
            f"{adresse_doublure}/_essai/scenario", json={"catalogue": ecran.catalogue}
        ).raise_for_status()
        if compte not in contextes:
            contextes[compte] = navigateur.new_context()
            if compte is not None:
                connecter_dans(contextes[compte], adresse, compte)
        page = contextes[compte].new_page()
        page.on(
            "console",
            lambda m, nom=nom: (
                console.append(f"{nom} : {m.text}")
                if "Content Security Policy" in m.text
                else None
            ),
        )
        page.goto(adresse + ecran.chemin)
        if ecran.bouton is not None:
            page.evaluate(REMPLIR, [ecran.saisies, ecran.action])
            with page.expect_navigation():
                page.click(ecran.bouton)
        titre = page.inner_text("main h1")
        assert titre == ecran.titre, (nom, titre)
        if page.evaluate(FOCUS) is False:
            sans_focus.append(nom)
        pages[nom] = auditer(page, source, adresse)
        page.close()
    page = navigateur.new_context().new_page()
    page.goto(adresse_sans_base + "/connexion")
    pages["comptes_indisponibles"] = auditer(page, source, adresse_sans_base)
    navigateur.close()
    return pages, console, sans_focus


def page_version(contexte: Any, source: str) -> str:
    page = contexte.new_page()
    page.evaluate(source)
    version = page.evaluate("axe.version")
    page.close()
    return version
