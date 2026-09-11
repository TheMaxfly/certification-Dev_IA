"""Controle 2 du paragraphe 10 : la fusion preserve les entrees anterieures."""

from pathlib import Path

import pytest

from commun import manifeste


def _entree(nom: str, run: str, source: str = "wikipedia_fr") -> manifeste.Entree:
    return manifeste.Entree(
        chemin=nom,
        octets=10,
        sha256="a" * 64,
        lignes=1,
        date_run=run,
        source=source,
        perimetre_couvert="1368 series",
    )


def test_fusion_de_deux_runs_partiels_rend_l_union(tmp_path: Path) -> None:
    etat = manifeste.Etat(source="wikipedia_fr", partition="2026-09")
    etat = manifeste.fusionner(etat, [_entree("a.ndjson", "r1")], date_run="r1")
    manifeste.enregistrer(tmp_path, etat)

    relu = manifeste.charger_etat(tmp_path, source="wikipedia_fr", partition="2026-09")
    fusionne = manifeste.fusionner(relu, [_entree("b.ndjson", "r2")], date_run="r2")

    assert set(fusionne.entrees) == {"a.ndjson", "b.ndjson"}, (
        "un run partiel a efface l'entree du run precedent — c'est exactement "
        "le defaut que le paragraphe 4 interdit de reproduire"
    )
    assert fusionne.runs == ["r1", "r2"]


def test_le_markdown_liste_toutes_les_entrees(tmp_path: Path) -> None:
    etat = manifeste.Etat(source="anilist", partition="2026-09")
    etat = manifeste.fusionner(
        etat,
        [_entree("a.ndjson", "r1", "anilist"), _entree("b.ndjson", "r1", "anilist")],
        date_run="r1",
    )
    rendu = manifeste.rendre_markdown(etat)
    assert "`a.ndjson`" in rendu and "`b.ndjson`" in rendu


def test_enregistrer_est_idempotent(tmp_path: Path) -> None:
    """Controle 1 du paragraphe 9 : relancer ne modifie ni le raw ni le manifeste."""
    etat = manifeste.Etat(source="anilist", partition="2026-09")
    etat = manifeste.fusionner(
        etat, [_entree("a.ndjson", "r1", "anilist")], date_run="r1"
    )
    manifeste.enregistrer(tmp_path, etat)
    premier = (tmp_path / manifeste.NOM_MANIFESTE).read_bytes()
    etat_relu = manifeste.charger_etat(tmp_path, source="anilist", partition="2026-09")
    etat_relu = manifeste.fusionner(etat_relu, [], date_run="r1")
    manifeste.enregistrer(tmp_path, etat_relu)
    assert (tmp_path / manifeste.NOM_MANIFESTE).read_bytes() == premier


def test_empreinte_compte_lignes_et_octets(tmp_path: Path) -> None:
    fichier = tmp_path / "x.ndjson"
    fichier.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
    e = manifeste.empreinte(fichier, source="anilist", date_run="r1", perimetre="test")
    assert e.lignes == 2
    assert e.octets == fichier.stat().st_size
    assert len(e.sha256) == 64


def test_fusion_refuse_une_entree_d_une_autre_source() -> None:
    etat = manifeste.Etat(source="anilist", partition="2026-09")
    with pytest.raises(manifeste.FusionImpossible):
        manifeste.fusionner(etat, [_entree("a.ndjson", "r1")], date_run="r1")
