"""Le fichier de candidat : schéma fermé (21) ; suivi par git, identique à HEAD (20)."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import CANDIDATS, Factice

from chaine import candidat, environnement
from chaine.codes import Arret, Code
from chaine.environnement import Sortie

REFUS = CANDIDATS / "refus_bgem3_v2.toml"
REQUALIFICATION = CANDIDATS / "requalification_v2.toml"


def test_les_deux_candidats_reels_se_lisent():
    r = candidat.lire(REFUS)
    assert (r.nom, r.corpus, r.nouvel_encodage, r.encodage, r.mesure) == (
        "refus_bgem3_v2",
        "v2",
        True,
        None,
        7,
    )
    assert (r.modele, r.instance, r.encodage_en_service) == ("BAAI/bge-m3", "bge-m3", 3)
    q = candidat.lire(REQUALIFICATION)
    assert (q.nouvel_encodage, q.encodage, q.mesure, q.encodage_en_service) == (
        False,
        3,
        8,
        2,
    )
    assert q.reference_run == "a3b7f0e025e4476ea4b60e7d0498876b"
    assert r.regle == q.regle == "10_mesures_recherche/config/promotion_corpus_v2.toml"


def ecrire(tmp_path: Path, texte: str, nom: str = "refus_bgem3_v2") -> Path:
    chemin = tmp_path / f"{nom}.toml"
    chemin.write_text(texte, encoding="utf-8")
    return chemin


BASE = REFUS.read_text(encoding="utf-8")
EXISTANT = REQUALIFICATION.read_text(encoding="utf-8")

INVALIDES = {
    "clé inconnue à la racine": BASE + 'en_trop = "x"\n',
    "table inconnue": BASE + "[ailleurs]\ncle = 1\n",
    "clé inconnue dans une table": BASE.replace("numero = 7", "numero = 7\nk = 10"),
    "clé manquante": BASE.replace('motif = "', 'autre = "'),
    "table manquante": BASE.replace("[preproduction]\n", "[x]\n").replace(
        "encodage_en_service = 3", ""
    ),
    "type faux, nombre en texte": BASE.replace("numero = 7", 'numero = "7"'),
    "type faux, booléen pour un nombre": BASE.replace("numero = 7", "numero = true"),
    "type faux, nombre pour un booléen": BASE.replace("nouveau = true", "nouveau = 1"),
    "nouveau sans modèle": BASE.replace('modele = "BAAI/bge-m3"\n', ""),
    "nouveau avec un numéro": BASE.replace(
        "nouveau = true", "nouveau = true\nnumero = 4"
    ),
    "modèle et instance discordants": BASE.replace(
        'instance = "bge-m3"', 'instance = "embeddinggemma"'
    ),
    "révision mal formée": BASE.replace("5617a9f6", "zz17a9f6"),
    "existant avec un modèle": EXISTANT.replace(
        "numero = 3\n", 'numero = 3\nmodele = "BAAI/bge-m3"\n'
    ),
    "existant sans numéro": EXISTANT.replace("numero = 3\n", ""),
    "instance inconnue": EXISTANT.replace('"embeddinggemma"', '"autre"'),
    "run de référence mal formé": EXISTANT.replace("a3b7f0e025e4", "a3b7"),
    "corpus v1": BASE.replace('version = "v2"', 'version = "v1"'),
    "autre règle": BASE.replace("promotion_corpus_v2.toml", "autre.toml"),
    "TOML illisible": BASE + "[[[",
}


@pytest.mark.parametrize("texte", INVALIDES.values(), ids=INVALIDES.keys())
def test_un_candidat_hors_schema_rend_21(tmp_path: Path, texte: str):
    nom = "requalification_v2" if texte.startswith(EXISTANT[:40]) else "refus_bgem3_v2"
    with pytest.raises(Arret) as e:
        candidat.lire(ecrire(tmp_path, texte, nom))
    assert e.value.code == Code.CANDIDAT_INVALIDE


def test_le_nom_doit_etre_celui_du_fichier(tmp_path: Path):
    with pytest.raises(Arret) as e:
        candidat.lire(ecrire(tmp_path, BASE, "autre_nom"))
    assert e.value.code == Code.CANDIDAT_INVALIDE and "nom" in e.value.motif


def test_un_candidat_absent_rend_20(tmp_path: Path):
    with pytest.raises(Arret) as e:
        candidat.lire(tmp_path / "absent.toml")
    assert e.value.code == Code.PREREQUIS


def test_un_chemin_relatif_se_lit_depuis_la_racine_du_depot():
    assert candidat.resoudre("14_chaine/candidats/refus_bgem3_v2.toml") == REFUS
    assert candidat.resoudre(str(REFUS)) == REFUS


@pytest.mark.parametrize(
    "reponses, motif",
    [
        ({"git.suivi": Sortie(1), "git.inchange": Sortie(0)}, "suivi"),
        ({"git.suivi": Sortie(0), "git.inchange": Sortie(1)}, "diffère de HEAD"),
    ],
)
def test_un_candidat_non_suivi_ou_modifie_rend_20(reponses, motif):
    with pytest.raises(Arret) as e:
        environnement.suivi_et_inchange(Factice(dict(reponses)), REFUS)
    assert e.value.code == Code.PREREQUIS and motif in e.value.motif
