"""Le README : un format accessible (critère 8) ; ses tableaux disent vrai (1 et 2).

Le tableau des déclencheurs est confronté aux déclencheurs configurés : la clé `on:`
de `verify.yml` et d'`indicatif.yml`, les cibles du `Makefile`, les hooks. Le tableau
des étapes est confronté aux étapes du code.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import MODULE, RACINE

from chaine import etapes

README = MODULE / "README.md"
TEXTE = README.read_text(encoding="utf-8")
WORKFLOWS = RACINE / ".github" / "workflows"


def section(titre: str) -> str:
    debut = TEXTE.index(f"\n## {titre}\n")
    fin = TEXTE.find("\n## ", debut + 1)
    return TEXTE[debut : fin if fin != -1 else len(TEXTE)]


def lignes_du_tableau(texte: str) -> list[list[str]]:
    lignes = [ligne for ligne in texte.splitlines() if ligne.startswith("|")]
    return [[c.strip() for c in ligne.strip("|").split("|")] for ligne in lignes[2:]]


def sans_code(texte: str) -> str:
    texte = re.sub(r"```.*?```", "", texte, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", texte)


# --------------------------------------------------------------------------- #
#  Critère 8 : un format accessible
# --------------------------------------------------------------------------- #


def test_les_titres_se_suivent_sans_saut_de_niveau():
    niveaux = [len(m.group(1)) for m in re.finditer(r"^(#+) ", sans_code(TEXTE), re.M)]
    assert niveaux[0] == 1 and niveaux.count(1) == 1
    for avant, apres in zip(niveaux, niveaux[1:], strict=False):
        assert apres <= avant + 1, (avant, apres)


def test_chaque_tableau_a_sa_ligne_d_en_tete():
    blocs = re.findall(r"(?:^\|.*\n)+", sans_code(TEXTE), re.M)
    assert len(blocs) >= 6
    for bloc in blocs:
        lignes = bloc.splitlines()
        assert len(lignes) >= 3, lignes[0]
        assert re.fullmatch(r"\|(\s*:?-{3,}:?\s*\|)+", lignes[1]), lignes[1]
        colonnes = lignes[0].count("|")
        assert all(ligne.count("|") >= colonnes for ligne in lignes), lignes[0]


def test_chaque_image_a_un_texte_alternatif():
    for alt in re.findall(r"!\[([^\]]*)\]\(", TEXTE):
        assert alt.strip(), "image sans texte alternatif"


def test_aucun_lien_nu_et_des_liens_qui_disent_ou_ils_menent():
    texte = sans_code(TEXTE)
    liens = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", texte)
    for libelle, _ in liens:
        assert libelle.strip().lower() not in {"ici", "lien", "cliquez ici", "voir"}
    reste = re.sub(r"\[[^\]]+\]\([^)]+\)", "", texte)
    assert not re.search(r"https?://", reste), "lien nu"


def test_aucune_information_par_la_seule_couleur():
    assert not re.search(r"[\U0001F534-\U0001F7EB⭕✅❌]", TEXTE)


# --------------------------------------------------------------------------- #
#  Critère 1 : toutes les étapes
# --------------------------------------------------------------------------- #


def test_le_tableau_des_etapes_est_celui_du_code():
    lignes = lignes_du_tableau(section("Les étapes"))
    assert [ligne[0] for ligne in lignes] == [str(n) for n in range(11)] + ["R"]
    for ligne, etape in zip(lignes, etapes.ETAPES, strict=False):
        assert ligne[1] == etape.nom, (ligne[1], etape.nom)
        codes = {int(c) for c in re.findall(r"\d+", ligne[5])}
        assert codes == {int(c) for c in etape.arret}, etape.nom


def test_chaque_code_de_sortie_a_sa_ligne_et_son_geste():
    from chaine.codes import Code

    lignes = lignes_du_tableau(section("Les codes de sortie, et que faire"))
    assert {(int(ligne[0]), ligne[1].strip("`")) for ligne in lignes} == {
        (int(c), c.name) for c in Code
    }
    assert all(ligne[3] for ligne in lignes), "chaque code dit que faire"


# --------------------------------------------------------------------------- #
#  Critère 2 : les déclencheurs, tels que configurés
# --------------------------------------------------------------------------- #


def declencheurs_du_readme() -> list[list[str]]:
    lignes = lignes_du_tableau(section("Les déclencheurs"))
    assert [ligne[0] for ligne in lignes] == [str(n) for n in range(1, 12)]
    return lignes


def on_de(fichier: str) -> dict[str, Any]:
    d = yaml.safe_load((WORKFLOWS / fichier).read_text(encoding="utf-8"))
    on: dict[str, Any] = d.get("on", d.get(True))  # YAML 1.1 : « on » est un booléen
    return on


def evenements_du_readme(fichier: str) -> set[str]:
    trouves: set[str] = set()
    for ligne in declencheurs_du_readme():
        for parenthese in re.findall(rf"`{re.escape(fichier)}` \(([^)]*)\)", ligne[2]):
            trouves |= set(re.findall(r"`([^`]+)`", parenthese))
    return trouves


@pytest.mark.parametrize("fichier", ["verify.yml", "indicatif.yml"])
def test_les_evenements_des_workflows_sont_ceux_du_tableau(fichier: str):
    on = on_de(fichier)
    lus = evenements_du_readme(fichier)
    assert set(on) == lus - {"develop", "0 6 * * 1"}, (sorted(on), sorted(lus))


def test_le_push_vise_develop_et_l_horaire_est_le_lundi():
    assert on_de("verify.yml")["push"]["branches"] == ["develop"]
    assert "develop" in evenements_du_readme("verify.yml")
    assert [s["cron"] for s in on_de("indicatif.yml")["schedule"]] == ["0 6 * * 1"]
    assert "0 6 * * 1" in evenements_du_readme("indicatif.yml")
    types = on_de("verify.yml")["pull_request"]["types"]
    assert types == ["opened", "synchronize", "reopened", "edited"]
    assert "(ouverte, mise à jour, rouverte, modifiée)" in TEXTE


def cibles_du_makefile() -> set[str]:
    texte = (RACINE / "Makefile").read_text(encoding="utf-8")
    return set(re.findall(r"^([a-z][a-z-]*):", texte, re.M))


def test_les_cibles_du_tableau_existent_et_celles_de_la_chaine_y_sont():
    cibles: set[str] = set()
    for ligne in declencheurs_du_readme():
        for parenthese in re.findall(r"`Makefile` \(([^)]*)\)", ligne[2]):
            cibles |= set(re.findall(r"`([^`]+)`", parenthese))
    assert cibles <= cibles_du_makefile(), cibles - cibles_du_makefile()
    de_la_chaine = {
        c for c in cibles_du_makefile() if c.startswith(("chaine", "preprod"))
    } - {"chaine-etat"}
    assert de_la_chaine <= cibles, de_la_chaine - cibles
    assert {"preprod-creer", "chaine", "chaine-promouvoir", "chaine-revenir"} <= cibles
    assert {"verify-poste", "verify-mutations"} <= cibles


def test_les_hooks_du_tableau_existent():
    hooks = set(re.findall(r"`\.githooks/([a-z-]+)`", section("Les déclencheurs")))
    assert hooks == {"pre-commit", "pre-push"}
    for hook in hooks:
        assert (RACINE / ".githooks" / hook).is_file()


def test_les_candidats_du_readme_sont_ceux_du_dossier():
    texte = section("Configuration : le fichier de candidat")
    lignes = lignes_du_tableau(texte[texte.index("**Les deux candidats") :])
    noms = {m for ligne in lignes for m in re.findall(r"^`([a-z0-9_]+)`$", ligne[0])}
    fichiers = {p.stem for p in (MODULE / "candidats").glob("*.toml")}
    assert noms == fichiers


def test_le_readme_ne_porte_aucun_chemin_personnel():
    assert str(Path.home()) not in TEXTE and "/home/" not in TEXTE
