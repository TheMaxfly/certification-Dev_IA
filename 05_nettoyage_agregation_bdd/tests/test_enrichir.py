"""Recalcul des colonnes dérivées, sur base JETABLE.

Le harnais fabrique un catalogue MINUSCULE mais qui porte tous les cas que 4a
et 4b ont mis au jour :

  - une série vue par MS seul (le cas qui manquait : la dérivée d'avant 4c
    était du Kitsu à 100 %) ;
  - une série vue par les deux, avec des genres qui ne se recouvrent pas —
    c'est l'UNION qu'on vérifie, pas un COALESCE ;
  - une série portant un code fin, pour la dérivation hiérarchique ;
  - une série dont le seul libellé est `inconnu` : elle doit rester non
    couverte, sans faire échouer le recalcul ;
  - un libellé ABSENT du référentiel : lui doit faire échouer le recalcul.

Le référentiel chargé est le VRAI CSV du dépôt. Une fixture de genres inventés
testerait un vocabulaire qui n'existe pas.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from conftest import lire

RACINE = Path(__file__).resolve().parents[2]
DONNEES = RACINE / "database/donnees"
MODULE = RACINE / "05_nettoyage_agregation_bdd"

sys.path.insert(0, str(MODULE / "src"))

from identity.enrichir import ErreurEnrichissement, libelles_non_couverts  # noqa: E402

# series_id, genres MS, genres Kitsu, synopsis MS, synopsis Kitsu
CATALOGUE = [
    # MS seul — le cas que la dérivée d'avant 4c ne savait pas produire.
    (1, ["romance", "comédie"], None, "Synopsis MS", None),
    # Les deux sources, sans recouvrement : l'union doit donner 4 codes.
    (2, ["action"], ["Romance", "School"], None, "Synopsis Kitsu"),
    # Code fin -> ancêtre dérivé (yaoi -> lgbt).
    (3, None, ["Yaoi"], None, None),
    # Deux codes fins sous le même parent : `lgbt` ne doit apparaître qu'une fois.
    (4, ["lesbiennes"], ["Yaoi"], None, None),
    # Seulement de l'inconnu : reste non couverte, sans bloquer.
    (5, ["chaos"], None, None, None),
    # Seulement de l'exclu.
    (6, ["Manga"], None, "  ", "Kitsu prend la main"),
    # Axe érotique : ecchi -> adulte, mais PAS de chaîne vers erotique/hentai.
    (7, ["Ecchi"], None, None, None),
]


def peupler(dsn: str, catalogue=CATALOGUE) -> None:
    with psycopg.connect(dsn) as connexion:
        connexion.execute("TRUNCATE manga.ms_series_enriched CASCADE")
        for series_id, ms, kitsu, syn_ms, syn_kitsu in catalogue:
            connexion.execute(
                "INSERT INTO manga.ms_series_enriched "
                "  (series_id, series_genres, kitsu_genres_json, "
                "   series_synopsis, kitsu_synopsis_clean) "
                "VALUES (%s, %s::jsonb, %s::jsonb, %s, %s)",
                (
                    series_id,
                    __import__("json").dumps(ms) if ms is not None else None,
                    __import__("json").dumps(kitsu) if kitsu is not None else None,
                    syn_ms,
                    syn_kitsu,
                ),
            )
        connexion.commit()


def executer(dsn: str, module: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=MODULE,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(MODULE / "src"),
            "DATABASE_URL": dsn,
        },
    )


@pytest.fixture
def prepare(base: str) -> str:
    """Base migrée + référentiel réel chargé + catalogue de test."""
    sortie = executer(base, "identity.charger_genres", "--donnees", str(DONNEES))
    assert sortie.returncode == 0, sortie.stderr
    peupler(base)
    return base


def codes(dsn: str, series_id: int) -> list[str]:
    ligne = lire(
        dsn,
        "SELECT series_genres_enriched FROM manga.ms_series_enriched "
        "WHERE series_id = %s",
        (series_id,),
    )[0][0]
    return list(ligne or [])


# ---------------------------------------------------------------------------
# Le calcul
# ---------------------------------------------------------------------------
def test_ms_seul_produit_des_codes(prepare: str):
    """Le témoin de la correction : avant 4c, une série sans Kitsu n'avait
    AUCUN code — la dérivée était du Kitsu à 100 %."""
    assert executer(prepare, "identity.enrichir").returncode == 0
    assert sorted(codes(prepare, 1)) == ["comedie", "romance"]


def test_union_des_deux_sources(prepare: str):
    """UNION, pas COALESCE : MS n'efface pas Kitsu et réciproquement."""
    executer(prepare, "identity.enrichir")
    assert sorted(codes(prepare, 2)) == ["action", "ecole", "romance"]


def test_ancetre_derive(prepare: str):
    executer(prepare, "identity.enrichir")
    assert sorted(codes(prepare, 3)) == ["lgbt", "yaoi"]


def test_ancetre_commun_non_duplique(prepare: str):
    """Deux enfants du même parent : l'ancêtre n'apparaît qu'une fois."""
    executer(prepare, "identity.enrichir")
    obtenus = codes(prepare, 4)
    assert sorted(obtenus) == ["lgbt", "yaoi", "yuri"]
    assert obtenus.count("lgbt") == 1


def test_axe_erotique_sans_chaine(prepare: str):
    """L'arbitrage 4c a REFUSÉ la chaîne hentai -> erotique -> ecchi : une
    série `ecchi` reçoit `adulte`, jamais `erotique` ni `hentai`."""
    executer(prepare, "identity.enrichir")
    assert sorted(codes(prepare, 7)) == ["adulte", "ecchi"]


def test_inconnu_ne_bloque_pas_et_ne_produit_rien(prepare: str):
    sortie = executer(prepare, "identity.enrichir")
    assert sortie.returncode == 0, sortie.stderr
    assert codes(prepare, 5) == []
    assert "inconnu" in sortie.stdout


def test_ordre_stable(prepare: str):
    """Les codes sont triés par `ordre` : deux runs produisent le même tableau,
    sinon l'idempotence serait fausse pour une raison purement cosmétique."""
    executer(prepare, "identity.enrichir")
    premier = codes(prepare, 2)
    executer(prepare, "identity.enrichir")
    assert codes(prepare, 2) == premier
    ordres = [
        lire(prepare, "SELECT ordre FROM manga.genre_ref WHERE code = %s", (c,))[0][0]
        for c in premier
    ]
    assert ordres == sorted(ordres)


# ---------------------------------------------------------------------------
# Le garde-fou
# ---------------------------------------------------------------------------
def test_libelle_absent_fait_echouer(prepare: str):
    """La règle centrale : un libellé inconnu du référentiel ABANDONNE le
    recalcul. Sans elle, il disparaîtrait de la dérivée sans que rien ne le
    signale — c'est ce qui s'est produit en juin."""
    peupler(prepare, [(9, ["libellé jamais vu"], None, None, None)])
    sortie = executer(prepare, "identity.enrichir")
    assert sortie.returncode == 1
    assert "libellé jamais vu" in sortie.stderr
    assert "ABANDONNÉ" in sortie.stderr


def test_libelle_absent_n_ecrit_rien(prepare: str):
    peupler(prepare, [*CATALOGUE, (9, ["libellé jamais vu"], None, None, None)])
    executer(prepare, "identity.enrichir")
    assert codes(prepare, 1) == []


def test_detection_des_libelles_absents(prepare: str):
    peupler(prepare, [(9, ["absent A"], ["absent B"], None, None)])
    with psycopg.connect(prepare) as connexion, connexion.cursor() as curseur:
        fautifs = libelles_non_couverts(curseur)
    assert {(source, libelle) for source, libelle, _ in fautifs} == {
        ("ms", "absent A"),
        ("kitsu", "absent B"),
    }


# ---------------------------------------------------------------------------
# Écriture, idempotence, périmètre
# ---------------------------------------------------------------------------
def test_rejeu_zero_modification(prepare: str):
    premier = executer(prepare, "identity.enrichir")
    assert "0 lignes modifiées" not in premier.stdout
    second = executer(prepare, "identity.enrichir")
    assert second.stdout.count("0 lignes modifiées") == 2


def test_dry_run_n_ecrit_rien(prepare: str):
    sortie = executer(prepare, "identity.enrichir", "--dry-run")
    assert sortie.returncode == 0, sortie.stderr
    assert "DRY-RUN" in sortie.stdout
    assert codes(prepare, 1) == []


def test_synopsis_ms_prioritaire_puis_kitsu(prepare: str):
    executer(prepare, "identity.enrichir")
    lignes = dict(
        lire(
            prepare,
            "SELECT series_id, series_synopsis_enriched "
            "FROM manga.ms_series_enriched ORDER BY series_id",
        )
    )
    assert lignes[1] == "Synopsis MS"
    assert lignes[2] == "Synopsis Kitsu"
    # Un synopsis MS réduit à des espaces n'est pas un synopsis.
    assert lignes[6] == "Kitsu prend la main"
    assert lignes[3] is None


def test_colonne_cible_une_seule(prepare: str):
    executer(prepare, "identity.enrichir", "--colonne", "synopsis")
    assert codes(prepare, 1) == []
    assert (
        lire(
            prepare,
            "SELECT series_synopsis_enriched FROM manga.ms_series_enriched "
            "WHERE series_id = 1",
        )[0][0]
        == "Synopsis MS"
    )


def test_colonne_inconnue_refusee(prepare: str):
    sortie = executer(prepare, "identity.enrichir", "--colonne", "tags")
    assert sortie.returncode == 1
    assert "inconnue" in sortie.stderr


EMPREINTE_DECISIONS = (
    "SELECT md5(string_agg(coalesce(match_method,'') "
    "|| coalesce(kitsu_id::text,'') || coalesce(needs_review::text,''), "
    "'|' ORDER BY series_id)) FROM manga.ms_series_enriched"
)


def test_ne_touche_pas_aux_colonnes_de_decision(prepare: str):
    """Le périmètre, vérifié plutôt que promis."""
    with psycopg.connect(prepare) as connexion:
        connexion.execute(
            "UPDATE manga.ms_series_enriched "
            "SET match_method = 'exact_title_norm_scored', "
            "    needs_review = TRUE, kitsu_id = 42 WHERE series_id = 1"
        )
        connexion.commit()
    avant = lire(
        prepare,
        EMPREINTE_DECISIONS,
    )
    executer(prepare, "identity.enrichir")
    apres = lire(
        prepare,
        EMPREINTE_DECISIONS,
    )
    assert avant == apres


def test_ne_touche_pas_aux_tags(prepare: str):
    """`series_tags_enriched` est hors périmètre — décision explicite de 4c."""
    with psycopg.connect(prepare) as connexion:
        connexion.execute(
            "UPDATE manga.ms_series_enriched "
            "SET series_tags_enriched = '[\"témoin\"]'::jsonb WHERE series_id = 1"
        )
        connexion.commit()
    executer(prepare, "identity.enrichir")
    assert lire(
        prepare,
        "SELECT series_tags_enriched FROM manga.ms_series_enriched WHERE series_id = 1",
    )[0][0] == ["témoin"]


def test_aucune_suppression_en_cascade(prepare: str):
    """Un UPDATE, jamais un DELETE : une ligne fille survit au recalcul."""
    with psycopg.connect(prepare) as connexion:
        connexion.execute(
            "INSERT INTO manga.ms_kitsu_map (series_id, kitsu_id) VALUES (1, 999)"
        )
        connexion.commit()
    executer(prepare, "identity.enrichir")
    assert lire(prepare, "SELECT count(*) FROM manga.ms_kitsu_map")[0][0] == 1


def test_migration_014_refuse_le_cycle_de_longueur_1(prepare: str):
    with psycopg.connect(prepare) as connexion:
        with pytest.raises(psycopg.errors.CheckViolation):
            connexion.execute(
                "UPDATE manga.genre_ref SET parent = 'lgbt' WHERE code = 'lgbt'"
            )


def test_arbre_sans_cycle_et_peu_profond(prepare: str):
    """Ce qu'aucune contrainte ne sait dire : la profondeur et les cycles longs."""
    profondeurs = lire(
        prepare,
        """
        WITH RECURSIVE chaine AS (
            SELECT code, parent, 1 AS profondeur, ARRAY[code] AS vus
              FROM manga.genre_ref
             UNION ALL
            SELECT c.code, r.parent, c.profondeur + 1, c.vus || r.code
              FROM chaine c
              JOIN manga.genre_ref r ON r.code = c.parent
             WHERE NOT r.code = ANY(c.vus) AND c.profondeur < 10
        )
        SELECT max(profondeur) FROM chaine
        """,
    )
    assert profondeurs[0][0] <= 3


def test_erreur_si_referentiel_absent(base: str):
    """Sans référentiel chargé, le message doit dire quoi faire."""
    peupler(base)
    sortie = executer(base, "identity.enrichir")
    assert sortie.returncode == 1
    assert "charger_genres" in sortie.stderr


def test_erreur_sans_database_url():
    sortie = subprocess.run(
        [sys.executable, "-m", "identity.enrichir"],
        cwd=MODULE,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(MODULE / "src")},
    )
    assert sortie.returncode == 1
    assert "DATABASE_URL" in sortie.stderr


def test_erreur_enrichissement_est_exportee():
    assert issubclass(ErreurEnrichissement, Exception)
