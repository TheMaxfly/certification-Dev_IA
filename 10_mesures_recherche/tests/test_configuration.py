"""La configuration versionnée : les réglages du second tour, tels que la spec
E2 jour 3 les déclare, et les refus du lecteur."""

from __future__ import annotations

import json
import tomllib

import pytest

from mesures_recherche import configuration
from mesures_recherche.configuration import ConfigurationInvalide, charger


def test_tour_1_inchange_perimetre_toutes_par_defaut():
    config = charger()
    for n in range(1, 7):
        m = configuration.mesure(config, n)
        assert "perimetre_entites" not in m and "tour" not in m
        assert configuration.perimetre(m) == "toutes"
        assert configuration.tour(m) == 1
    assert config["tours"] == {"1": "E2 jour 2", "2": "E2 jour 3"}


def test_tour_2_tel_que_declare_par_la_spec():
    config = charger()
    attendu = {
        # numéro : (type, périmètre, sources, k_rrf)
        7: ("semantique", "catalogue", None, None),
        8: ("semantique", "catalogue", None, None),
        9: ("tfidf", "catalogue", None, None),
        10: ("fusion", "toutes", [6, 2], 60),
        11: ("fusion", "catalogue", [6, 2], 60),
        12: ("fusion", "toutes", [6, 2], 10),
        13: ("fusion", "toutes", [6, 2], 100),
    }
    for n, (genre, perimetre, sources, k_rrf) in attendu.items():
        m = configuration.mesure(config, n)
        assert m["tour"] == 2 and m["type"] == genre
        assert m["perimetre_entites"] == perimetre
        assert m.get("sources") == sources and m.get("k_rrf") == k_rrf
        if genre == "fusion":
            assert m["profondeur"] == 100
    assert configuration.mesure(config, 7)["instance"] == "bge-m3"
    assert configuration.mesure(config, 8)["instance"] == "embeddinggemma"
    tfidf_1 = configuration.mesure(config, 6)
    tfidf_2 = configuration.mesure(config, 9)
    for cle in ("lowercase", "strip_accents", "sublinear_tf", "min_df", "norm"):
        assert tfidf_2[cle] == tfidf_1[cle]
    assert config["bootstrap"]["comparaisons"][-2:] == [[10, 2], [11, 8]]
    assert len(config["bootstrap"]["comparaisons"]) == 6


def _ecrire(tmp_path, transformer):
    """La configuration versionnée, modifiée par `transformer`, réécrite en TOML
    (les seules structures du fichier : tables simples et `[[mesures]]`)."""
    brut = tomllib.loads(configuration.FICHIER.read_text(encoding="utf-8"))
    transformer(brut)
    lignes = []
    for cle, valeur in brut.items():
        if cle == "mesures":
            continue
        lignes.append(f"[{cle}]")
        for k, v in valeur.items():
            lignes.append(f'"{k}" = {json.dumps(v)}')
    for m in brut["mesures"]:
        lignes.append("[[mesures]]")
        lignes += [f"{k} = {json.dumps(v)}" for k, v in m.items()]
    chemin = tmp_path / "mesures.toml"
    chemin.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return chemin


def test_relecture_fidele(tmp_path):
    chemin = _ecrire(tmp_path, lambda c: None)
    assert charger(chemin) == charger()


def test_perimetre_inconnu_refuse(tmp_path):
    def casser(c):
        c["mesures"][6]["perimetre_entites"] = "series"

    with pytest.raises(ConfigurationInvalide, match="perimetre_entites"):
        charger(_ecrire(tmp_path, casser))


def test_tour_inconnu_refuse(tmp_path):
    def casser(c):
        c["mesures"][6]["tour"] = 3

    with pytest.raises(ConfigurationInvalide, match="tour inconnu"):
        charger(_ecrire(tmp_path, casser))
