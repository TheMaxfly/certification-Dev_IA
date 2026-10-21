"""Essais du contrôle des pseudonymes des passages (`pseudonymes_passages.py`).

    uv run --no-project python -I verification/tester_pseudonymes_passages.py

Joués par la tâche `statique`, sans base : des passages et des pseudonymes inventés,
la méthode du corpus v2 (module 05) chargée par son chemin, les listes versionnées
relues. Code 0 si tout passe.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

CHEMIN = Path(__file__).resolve().parent / "pseudonymes_passages.py"
_spec = importlib.util.spec_from_file_location("pseudonymes_passages", CHEMIN)
assert _spec is not None and _spec.loader is not None
pp = importlib.util.module_from_spec(_spec)
sys.modules["pseudonymes_passages"] = pp
_spec.loader.exec_module(pp)
methode = pp.charger_methode()

#: Des pseudonymes inventés, et leur empreinte.
PSEUDOS = {methode.empreinte(p): p for p in ("Zorglub", "Plume42")}
ZORGLUB = methode.empreinte("Zorglub")
PLUME = methode.empreinte("Plume42")


def controler(fragments, connus=()):
    return pp.controler(fragments, PSEUDOS, connus, methode.motif)


class Controle(unittest.TestCase):
    def test_une_occurrence_connue_passe(self):
        bilan = controler(
            {1: ("ms_review:7", "Merci à Zorglub pour la relecture.")},
            {("ms_review:7", ZORGLUB)},
        )
        self.assertEqual(bilan["hors_liste"], [])
        self.assertEqual((bilan["couples_trouves"], bilan["couples_connus"]), (1, 1))

    def test_une_occurrence_inconnue_est_rapportee_par_identifiants(self):
        bilan = controler({4: ("ms_review:9", "Comme le disait Plume42, ...")})
        self.assertEqual(bilan["hors_liste"], [[4, "ms_review:9", PLUME]])

    def test_un_couple_admis_ne_vaut_que_pour_son_document(self):
        bilan = controler(
            {
                1: ("ms_review:7", "Zorglub, encore."),
                2: ("ms_review:8", "Zorglub, toujours."),
            },
            {("ms_review:7", ZORGLUB)},
        )
        self.assertEqual(bilan["hors_liste"], [[2, "ms_review:8", ZORGLUB]])

    def test_un_couple_admis_ne_vaut_que_pour_son_pseudonyme(self):
        bilan = controler(
            {1: ("ms_review:7", "Zorglub et Plume42.")},
            {("ms_review:7", ZORGLUB)},
        )
        self.assertEqual(bilan["hors_liste"], [[1, "ms_review:7", PLUME]])

    def test_mot_entier_et_casse_ignoree(self):
        bilan = controler(
            {
                1: ("kitsu:1", "Les Zorglubiens attaquent."),
                2: ("kitsu:2", "ZORGLUB revient."),
            }
        )
        self.assertEqual(bilan["hors_liste"], [[2, "kitsu:2", ZORGLUB]])

    def test_sans_pseudonyme_ou_sans_passage_le_controle_refuse(self):
        with self.assertRaises(pp.ControleImpossible):
            pp.controler({1: ("ms_review:1", "x")}, {}, (), methode.motif)
        with self.assertRaises(pp.ControleImpossible):
            controler({})

    def test_la_sortie_ne_porte_aucun_pseudonyme(self):
        bilan = controler(
            {
                1: ("ms_review:7", "Merci à Zorglub."),
                2: ("ms_review:8", "Plume42 le dit."),
            },
            {("ms_review:7", ZORGLUB)},
        )
        texte = json.dumps(bilan)
        for pseudo in PSEUDOS.values():
            self.assertNotIn(pseudo.lower(), texte.lower())


class Temoin(unittest.TestCase):
    def test_une_reference_retrouvee_n_est_pas_signalee(self):
        fragments = {1: ("ms_review:7", "Merci à Zorglub.")}
        self.assertEqual(
            pp.temoin(fragments, PSEUDOS, {("ms_review:7", ZORGLUB)}, methode.motif),
            [],
        )

    def test_une_reference_absente_est_signalee(self):
        fragments = {1: ("ms_review:7", "Rien ici.")}
        self.assertEqual(
            pp.temoin(fragments, PSEUDOS, {("ms_review:7", ZORGLUB)}, methode.motif),
            [("ms_review:7", ZORGLUB)],
        )


class Bilan(unittest.TestCase):
    def ecrire(self, contenu: object) -> Path:
        dossier = tempfile.TemporaryDirectory()
        self.addCleanup(dossier.cleanup)
        chemin = Path(dossier.name) / "egalite_api.json"
        chemin.write_text(json.dumps(contenu), encoding="utf-8")
        return chemin

    def test_les_fragments_sont_dedoublonnes_et_tries(self):
        chemin = self.ecrire(
            {"passages": {"corpus_id": "v2", "encodage_id": 3, "fragments": [9, 2, 9]}}
        )
        self.assertEqual(pp.lire_bilan(chemin), ("v2", 3, [2, 9]))

    def test_un_bilan_sans_passages_est_refuse(self):
        for contenu in (
            {"questions": 69},
            {"passages": {"corpus_id": "v2", "encodage_id": 3, "fragments": []}},
            {"passages": {"corpus_id": "v2", "encodage_id": 3, "fragments": [True]}},
            {"passages": {"corpus_id": "v2", "fragments": [1]}},
            [],
        ):
            refus = self.assertRaises(pp.ControleImpossible)
            with self.subTest(contenu=contenu), refus:
                pp.lire_bilan(self.ecrire(contenu))

    def test_un_bilan_absent_est_refuse(self):
        with self.assertRaises(pp.ControleImpossible):
            pp.lire_bilan(Path(tempfile.gettempdir()) / "absent" / "egalite_api.json")


class Listes(unittest.TestCase):
    def test_les_listes_versionnees_se_lisent(self):
        masquage, homonymes = methode.lire_listes(pp.DONNEES)
        self.assertTrue(masquage)
        self.assertTrue(homonymes)


if __name__ == "__main__":
    unittest.main(verbosity=1)
