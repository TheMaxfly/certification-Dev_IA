"""L'audit d'accessibilité : axe-core dans Chromium, sur chaque écran et chaque cas.

Sauté hors de `make verify-poste` (`AUDIT_AXE=1`), et compté : il demande un
navigateur, installé une fois (`uv run --extra audit playwright install chromium`). La
politique de sécurité du contenu reste active : axe entre par le protocole du
navigateur, toute violation lue en console est un échec. Les `<details>` sont ouverts,
sinon leur contenu n'est pas audité ; le formulaire est soumis, sinon une page issue
d'un `POST` n'existe pas.

Verdict : aucune violation `serious` ou `critical`. Le test réécrit `rapport_axe.json`
et `rapport_axe.md` ; un diff après un passage signale un changement à commiter.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import ServeurLocal
from doublure_api import Doublure
from ecrans import ECRANS, Ecran

from application.app import creer_application

ACCESSIBILITE = Path(__file__).resolve().parents[1] / "accessibilite"
ETIQUETTES = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]
GRAVES = ("serious", "critical")
#: Les défauts modérés ou mineurs laissés, avec leur motif : écrits au rapport.
INSCRITS: dict[str, str] = {}
#: Ce qu'axe ne sait pas juger, avec ce qui le règle : écrit au rapport.
A_REVOIR = {
    "color-contrast": "le chevron de la liste déroulante est une image de fond : axe ne"
    " déduit pas la couleur du fond. Le couple de la liste est fixé par `theme.css`, et"
    " son contraste calculé par un test (`test_wcag_1_4_3_…`).",
}
PREPARER = """(valeurs) => {
  const form = document.querySelector("form");
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
        },
    )
    with page.expect_navigation():
        page.click("button[type=submit]")


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
    date = datetime.now(UTC).date().isoformat()
    (ACCESSIBILITE / "rapport_axe.json").write_text(
        json.dumps(
            {
                "date": date,
                "axe": version_axe,
                "navigateur": navigateur,
                "etiquettes": ETIQUETTES,
                "pages": pages,
            },
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
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
    a_revoir = sorted({v["id"] for p in pages.values() for v in p["incomplete"]})
    lignes += ["", "## À revoir à la main", ""]
    lignes += [f"- `{r}` : {A_REVOIR.get(r, 'à relire.')}" for r in a_revoir] or [
        "Rien."
    ]
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
def test_audit_axe_sur_chaque_ecran_et_chaque_cas(reglages):
    from playwright.sync_api import sync_playwright

    source = (ACCESSIBILITE / "axe.min.js").read_text(encoding="utf-8")
    doublure = Doublure()
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        fermee = f"http://127.0.0.1:{prise.getsockname()[1]}"
    pages: dict[str, dict[str, Any]] = {}
    console: list[str] = []

    def adresse_de(reglee: str) -> Any:
        return creer_application(replace(reglages, adresse_api=reglee))

    with (
        ServeurLocal(doublure.app) as adresse_doublure,
        ServeurLocal(adresse_de(adresse_doublure)) as adresse,
        ServeurLocal(adresse_de(fermee)) as adresse_sans_api,
        sync_playwright() as playwright,
    ):
        navigateur = playwright.chromium.launch(headless=True)
        contexte = navigateur.new_context()
        for ecran in ECRANS:
            httpx.post(
                f"{adresse_doublure}/_essai/scenario",
                json={"info": ecran.info, "reponse": ecran.reponse},
            ).raise_for_status()
            page = contexte.new_page()
            page.on(
                "console",
                lambda m, nom=ecran.nom: (
                    console.append(f"{nom} : {m.text}")
                    if "Content Security Policy" in m.text
                    else None
                ),
            )
            base = adresse_sans_api if ecran.api_fermee else adresse
            ouvrir_ecran(page, base, ecran)
            page.evaluate(OUVRIR)
            page.evaluate(source)
            pages[ecran.nom] = resume(page.evaluate(LANCER, ETIQUETTES), base)
            page.close()
        version_axe = page_version(contexte, source)
        version_navigateur = navigateur.version
        navigateur.close()

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
    assert len(pages) == len(ECRANS)


def page_version(contexte: Any, source: str) -> str:
    page = contexte.new_page()
    page.evaluate(source)
    version = page.evaluate("axe.version")
    page.close()
    return version
