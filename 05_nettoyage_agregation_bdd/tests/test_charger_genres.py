"""Chargeur du référentiel de genres, de bout en bout, sur base JETABLE.

Deux natures de test, séparées :

  - les CSV RÉELS du dépôt sont chargés tels quels, et les contrôles chiffrés
    de l'étape 4b sont vérifiés en base (99 / 55 libellés, les 5 libellés
    communs sur le même code, l'invariant statut/code, l'idempotence du rejeu).
    Une fixture aux valeurs inventées passerait au vert pendant que le vrai
    fichier serait faux — c'est le mode d'échec qu'on cherche à couvrir ;

  - des CSV fabriqués ici, minuscules, pour les refus : ce que le chargeur doit
    REJETER avant d'écrire quoi que ce soit.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import lire

RACINE = Path(__file__).resolve().parents[2]
DONNEES = RACINE / "database/donnees"

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from identity.charger_genres import (  # noqa: E402
    ErreurChargement,
    charger_mapping,
    charger_ref,
    lire_mapping,
    lire_ref,
)

# Les 5 libellés écrits à l'identique chez MS et chez Kitsu. Ils DOIVENT
# pointer vers le même code : deux codes pour un libellé identique serait la
# preuve que la correspondance a été écrite deux fois sans se relire.
COMMUNS = ("Ecchi", "Fantasy", "Mecha", "Samurai", "Vampire")


def executer(dsn: str, *args: str) -> subprocess.CompletedProcess:
    """Lance le CLI comme un utilisateur le ferait, pas la fonction Python."""
    return subprocess.run(
        [sys.executable, "-m", "identity.charger_genres", *args],
        cwd=RACINE / "05_nettoyage_agregation_bdd",
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(RACINE / "05_nettoyage_agregation_bdd/src"),
            "DATABASE_URL": dsn,
        },
    )


# ---------------------------------------------------------------------------
# Les CSV du dépôt, sans base : ce qui est vérifiable sans Docker.
# ---------------------------------------------------------------------------
def test_csv_du_depot_valides():
    """Les deux CSV versionnés respectent leurs propres contrôles."""
    ref = lire_ref(DONNEES)
    mapping = lire_mapping(DONNEES, {ligne["code"] for ligne in ref})

    par_source = {"ms": 0, "kitsu": 0}
    for ligne in mapping:
        par_source[ligne["source"]] += 1
    assert par_source == {"ms": 99, "kitsu": 55}


def test_aucun_code_orphelin():
    """Un code que rien ne mappe est du vocabulaire mort — SAUF s'il est le
    parent d'un code mappé.

    La règle de 013 (« tout code a une correspondance ») ne tient plus depuis
    014 : `adulte` est le parent commun de ecchi/erotique/hentai et n'est le
    reflet d'aucun libellé observé. La règle qui la remplace est plus juste —
    un code doit être atteignable, directement ou par un de ses enfants.
    """
    ref = lire_ref(DONNEES)
    mapping = lire_mapping(DONNEES, {ligne["code"] for ligne in ref})
    mappes = {ligne["code"] for ligne in mapping if ligne["code"]}
    parents = {ligne["parent"] for ligne in ref if ligne["parent"]}
    orphelins = {ligne["code"] for ligne in ref} - mappes - parents
    assert orphelins == set()


def test_adulte_est_le_seul_code_sans_correspondance():
    """Le contraire de l'assouplissement précédent : la dérogation reste UNE."""
    ref = lire_ref(DONNEES)
    mapping = lire_mapping(DONNEES, {ligne["code"] for ligne in ref})
    mappes = {ligne["code"] for ligne in mapping if ligne["code"]}
    assert {ligne["code"] for ligne in ref} - mappes == {"adulte"}


def test_hierarchie_attendue():
    """Les parentés que l'arbitrage 4c a fixées, nommément."""
    parent = {ligne["code"]: ligne["parent"] or None for ligne in lire_ref(DONNEES)}
    assert parent["yaoi"] == parent["yuri"] == parent["gender_bender"] == "lgbt"
    assert parent["ecchi"] == parent["erotique"] == parent["hentai"] == "adulte"
    # Et ce que l'arbitrage a REFUSÉ : pas de chaîne entre les niveaux de
    # l'axe érotique — un filtre `ecchi` ne doit pas remonter de l'explicite.
    assert parent["adulte"] is None
    assert parent["lgbt"] is None


def test_types_coherents_avec_le_prefixe():
    for ligne in lire_ref(DONNEES):
        assert ligne["code"].startswith("format_") == (ligne["type"] == "format")


def test_libelles_communs_meme_code():
    """Contrôle n°2 de l'étape 4b, vérifié dans le CSV."""
    ref = lire_ref(DONNEES)
    mapping = lire_mapping(DONNEES, {ligne["code"] for ligne in ref})
    par_libelle: dict[str, set[str]] = {}
    for ligne in mapping:
        if ligne["libelle_brut"] in COMMUNS:
            par_libelle.setdefault(ligne["libelle_brut"], set()).add(ligne["code"])
    assert set(par_libelle) == set(COMMUNS)
    for libelle, codes in par_libelle.items():
        assert len(codes) == 1, f"{libelle} : {codes}"


def test_codes_en_snake_case_ascii():
    """Le CHECK de 013 dit la même chose ; le dire ici évite un aller-retour
    en base pour une faute de frappe dans un CSV."""
    import re

    for ligne in lire_ref(DONNEES):
        assert re.fullmatch(r"[a-z][a-z0-9_]*", ligne["code"]), ligne["code"]


# ---------------------------------------------------------------------------
# Refus : ce que le chargeur doit rejeter AVANT la base.
# ---------------------------------------------------------------------------
def _ecrire(dossier: Path, ref: list[tuple], mapping: list[tuple]) -> Path:
    with (dossier / "genre_ref.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(
            ["code", "label_fr", "label_en", "label_ja", "ordre", "type", "parent"]
        )
        w.writerows(ref)
    with (dossier / "genre_mapping.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["source", "libelle_brut", "code", "statut", "note"])
        w.writerows(mapping)
    return dossier


REF_MINIMAL = [("romance", "Romance", "Romance", "恋愛", 10, "genre", "")]


@pytest.mark.parametrize(
    ("mapping", "attendu"),
    [
        # L'invariant central, dans ses deux sens.
        ([("ms", "romance", "", "mappe", "")], "invariant rompu"),
        ([("ms", "Manga", "romance", "exclu", "média")], "invariant rompu"),
        # Un code qui n'existe pas : refusé ici, pas par la clé étrangère.
        ([("ms", "romance", "absent", "mappe", "")], "absent de genre_ref"),
        # Un statut inventé.
        ([("ms", "romance", "", "peut-etre", "")], "statut hors"),
        # Une décision non motivée.
        ([("ms", "chaos", "", "inconnu", "")], "doit dire pourquoi"),
        # Le même libellé deux fois pour une source.
        (
            [
                ("ms", "romance", "romance", "mappe", ""),
                ("ms", "romance", "romance", "mappe", ""),
            ],
            "couple en double",
        ),
    ],
)
def test_refus(tmp_path: Path, mapping: list[tuple], attendu: str):
    _ecrire(tmp_path, REF_MINIMAL, mapping)
    with pytest.raises(ErreurChargement, match=attendu):
        lire_mapping(tmp_path, {"romance"})


def test_refus_entete_renommee(tmp_path: Path):
    (tmp_path / "genre_ref.csv").write_text("code,libelle\nromance,Romance\n")
    with pytest.raises(ErreurChargement, match="en-tête inattendu"):
        lire_ref(tmp_path)


# ---------------------------------------------------------------------------
# Bout en bout, sur base jetable.
# ---------------------------------------------------------------------------
def test_chargement_puis_rejeu(base: str):
    """Le chargement écrit tout ; le rejeu ne change rien — contrôle n°6."""
    premier = executer(base, "--donnees", str(DONNEES))
    assert premier.returncode == 0, premier.stderr
    assert "+73 nouveaux" in premier.stdout
    assert "+154 nouveaux" in premier.stdout

    assert lire(base, "SELECT count(*) FROM manga.genre_ref")[0][0] == 73
    assert lire(base, "SELECT count(*) FROM manga.genre_mapping")[0][0] == 154

    second = executer(base, "--donnees", str(DONNEES))
    assert second.returncode == 0, second.stderr
    assert "+0 nouveaux, 0 modifiés" in second.stdout
    assert second.stdout.count("+0 nouveaux, 0 modifiés") == 2


def test_repartition_par_source_et_statut(base: str):
    executer(base, "--donnees", str(DONNEES))
    lignes = lire(
        base,
        "SELECT source, statut, count(*) FROM manga.genre_mapping "
        "GROUP BY 1, 2 ORDER BY 1, 2",
    )
    par_source: dict[str, int] = {}
    for source, _statut, n in lignes:
        par_source[source] = par_source.get(source, 0) + n
    assert par_source == {"ms": 99, "kitsu": 55}


def test_invariant_tenu_en_base(base: str):
    """Contrôle n°4 : zéro ligne ne viole (statut='mappe') = (code IS NOT NULL)."""
    executer(base, "--donnees", str(DONNEES))
    violations = lire(
        base,
        "SELECT count(*) FROM manga.genre_mapping "
        "WHERE (statut = 'mappe') <> (code IS NOT NULL)",
    )[0][0]
    assert violations == 0


def test_invariant_refuse_par_la_base(base: str):
    """Le CHECK de 013 tient même si le chargeur est contourné."""
    import psycopg

    executer(base, "--donnees", str(DONNEES))
    with psycopg.connect(base) as connexion:
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "INSERT INTO manga.genre_mapping (source, libelle_brut, statut) "
                "VALUES ('ms', 'inexistant', 'mappe')"
            )


def test_dry_run_n_ecrit_rien(base: str):
    sortie = executer(base, "--donnees", str(DONNEES), "--dry-run")
    assert sortie.returncode == 0, sortie.stderr
    assert "DRY-RUN" in sortie.stdout
    assert lire(base, "SELECT count(*) FROM manga.genre_ref")[0][0] == 0


def test_ne_touche_pas_aux_series(base: str):
    """Contrôle n°7 : aucune écriture sur ms_series_enriched.

    La base de test est vide de séries — ce que ce test vérifie est donc
    l'absence d'écriture, pas la préservation d'un contenu. La préservation sur
    `apimanga` se contrôle par un comptage avant/après, hors suite.
    """
    executer(base, "--donnees", str(DONNEES))
    assert lire(base, "SELECT count(*) FROM manga.ms_series_enriched")[0][0] == 0


def test_ordre_range_les_formats_apres_les_genres(base: str):
    executer(base, "--donnees", str(DONNEES))
    borne = lire(
        base,
        "SELECT max(ordre) FILTER (WHERE code NOT LIKE 'format\\_%'), "
        "       min(ordre) FILTER (WHERE code LIKE 'format\\_%') "
        "FROM manga.genre_ref",
    )[0]
    assert borne[0] < borne[1]


def test_lecture_seule_accordee(base: str):
    """013 accorde le SELECT à manga_ro : sans lui, l'API ne verrait pas le
    référentiel alors qu'elle voit toutes les autres tables du schéma."""
    executer(base, "--donnees", str(DONNEES))
    for table in ("genre_ref", "genre_mapping"):
        accorde = lire(
            base,
            "SELECT has_table_privilege('manga_ro', %s, 'SELECT')",
            (f"manga.{table}",),
        )[0][0]
        assert accorde, table


def test_charge_sans_le_cli(base: str):
    """Les fonctions de chargement, appelées directement : le bilan distingue
    bien l'insertion de la modification."""
    import psycopg

    ref = lire_ref(DONNEES)
    mapping = lire_mapping(DONNEES, {ligne["code"] for ligne in ref})
    with psycopg.connect(base) as connexion:
        assert charger_ref(connexion, ref)["inseres"] == 73
        assert charger_mapping(connexion, mapping)["inseres"] == 154
        connexion.commit()
    with psycopg.connect(base) as connexion:
        assert charger_ref(connexion, ref) == {
            "inseres": 0,
            "modifies": 0,
            "total": 73,
        }
