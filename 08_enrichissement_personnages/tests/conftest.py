"""Racine du module sur le chemin d'import, et une base PostgreSQL JETABLE.

Les chargeurs sont éprouvés sur une base copiée du gabarit migré du harnais
partagé (`database/harnais_tests/`), à partir de raws FACTICES écrits ici : jamais
`apimanga`. Le DSN est fabriqué par le harnais ; sans Docker, ces tests sautent,
sans repli sur une base réelle.

Restent sur le poste seulement, dans l'étape « état d'apimanga » de
`verify-poste` : l'égalité au raw réel et la volumétrie de la base réelle
(`APIMANGA_DSN`).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))


def _charger_harnais():
    """Le harnais partagé des bases jetables (`database/harnais_tests/`), par chemin."""
    spec = importlib.util.spec_from_file_location(
        "harnais_tests", RACINE.parent / "database" / "harnais_tests" / "__init__.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["harnais_tests"] = module
    spec.loader.exec_module(module)
    return module


harnais = _charger_harnais()

#: Le nom que porte un personnage dans les deux sources : rien ne doit les fondre.
NOM_COMMUN = "Monkey D. Luffy"

#: Deux pages de relations Kitsu : quatre personnages, cinq liens (Luffy est lié
#: aux deux œuvres, ses formes ne sont versées qu'une fois).
PAGES_KITSU = [
    {
        "manga_id": 38,
        "data": [
            {
                "attributes": {"role": "main"},
                "relationships": {"character": {"data": {"id": "1"}}},
            },
            {
                "attributes": {"role": "supporting"},
                "relationships": {"character": {"data": {"id": "2"}}},
            },
            {
                "attributes": {"role": "figurant"},
                "relationships": {"character": {"data": {"id": "3"}}},
            },
        ],
        "included": [
            {
                "type": "characters",
                "id": "1",
                "attributes": {
                    "slug": "monkey-d-luffy",
                    "canonicalName": NOM_COMMUN,
                    "malId": 40,
                    "createdAt": "2013-02-20T16:00:00.000Z",
                    "updatedAt": "2024-01-02T03:04:05.000Z",
                    "names": {"en": NOM_COMMUN, "ja_jp": "モンキー・D・ルフィ"},
                    "otherNames": ["Mugiwara", ""],
                    "description": "Luffy is the captain of the crew. "
                    "(Source: One Piece Wikia)",
                },
            },
            {
                "type": "characters",
                "id": "2",
                "attributes": {
                    "canonicalName": "Roronoa Zoro",
                    "names": {"ja_jp": "ロロノア・ゾロ"},
                    "description": "Zoro is a swordsman. (Source: ANN)",
                },
            },
            {
                "type": "characters",
                "id": "3",
                "attributes": {"canonicalName": "Figurant", "description": ""},
            },
            {"type": "mediaCharacters", "id": "99", "attributes": {}},
        ],
    },
    {
        "manga_id": 41,
        "data": [
            {
                "attributes": {"role": "cameo"},
                "relationships": {"character": {"data": {"id": "1"}}},
            },
            {"attributes": {"role": "main"}, "relationships": {}},
        ],
        "included": [
            {
                "type": "characters",
                "id": "1",
                "attributes": {"canonicalName": NOM_COMMUN},
            },
        ],
    },
]


def _article(
    series_id: int,
    titre: str,
    personnages: list[dict],
    renvoi: tuple[str, int] | None = None,
) -> dict:
    """Une enveloppe du collecteur ; `renvoi` : (titre, revid) de l'article dédié."""
    return {
        "series_id": series_id,
        "charge": {
            "forme_retenue": "dedie" if renvoi else "in_situ",
            "titre_servi": titre,
            "revid": 42,
            "renvoi_titre_servi": renvoi[0] if renvoi else None,
            "renvoi_revid": renvoi[1] if renvoi else None,
            "personnages": personnages,
        },
    }


EREN = {
    "nom": "Eren Jäger",
    "forme_japonaise": "エレン・イェーガー",
    "description": "Personnage principal de la série.",
}

#: Trois séries résolvent vers le même article : UN personnage, TROIS liens.
ARTICLES_WIKIPEDIA = [
    _article(101, "L'Attaque des Titans", [EREN]),
    _article(102, "L'Attaque des Titans", [EREN]),
    _article(103, "L'Attaque des Titans", [EREN]),
    _article(
        104,
        "One Piece",
        [
            {"nom": NOM_COMMUN, "forme_japonaise": "", "description": "Capitaine."},
            {"nom": "  ", "forme_japonaise": "", "description": "écarté"},
            {"nom": "Nami", "forme_japonaise": "ナミ", "description": ""},
        ],
        renvoi=("Liste des personnages de One Piece", 99),
    ),
]


def ecrire_ndjson(chemin: Path, lignes: list[dict]) -> Path:
    chemin.write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lignes),
        encoding="utf-8",
    )
    return chemin


@pytest.fixture(scope="session")
def conteneur() -> str:
    """Un PostgreSQL jetable pour toute la session."""
    raison = harnais.raison_de_sauter()
    if raison:
        pytest.skip(raison)
    with harnais.conteneur("personnages-test") as dsn:
        yield dsn


@pytest.fixture(scope="session")
def gabarit(conteneur) -> str:
    """La base gabarit, migrée une fois par session par le runner du dépôt."""
    return harnais.creer_gabarit(conteneur)


@pytest.fixture
def base(conteneur, gabarit) -> str:
    """Une base neuve par test, copiée du gabarit migré."""
    with harnais.base_depuis_gabarit(conteneur, gabarit) as dsn:
        yield dsn


@pytest.fixture(scope="session")
def raws(tmp_path_factory) -> dict[str, Path]:
    """Les raws factices des deux sources, au format de leurs collecteurs."""
    dossier = tmp_path_factory.mktemp("raws")
    return {
        "kitsu": ecrire_ndjson(dossier / "characters.ndjson", PAGES_KITSU),
        "wikipedia_fr": ecrire_ndjson(
            dossier / "wikipedia_fr_personnages.ndjson", ARTICLES_WIKIPEDIA
        ),
    }


@pytest.fixture(scope="session")
def base_chargee(conteneur, gabarit, raws) -> str:
    """Les deux sources chargées par leurs commandes, raw → staging → tables.

    Une base pour la session : les tests qui la lisent n'écrivent rien (le rejeu
    de la promotion est annulé).
    """
    import chargeur_kitsu
    import chargeur_wikipedia_fr

    with harnais.base_depuis_gabarit(conteneur, gabarit) as dsn:
        for chargeur, raw in (
            (chargeur_kitsu, raws["kitsu"]),
            (chargeur_wikipedia_fr, raws["wikipedia_fr"]),
        ):
            assert chargeur.main(["--dsn", dsn, "--raw", str(raw)]) == 0
        yield dsn
