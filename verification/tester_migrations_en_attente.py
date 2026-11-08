"""Essais du contrôle des migrations attendues en attente (`migrations_en_attente.py`).

    uv run --no-project python -I verification/tester_migrations_en_attente.py

Joués par la tâche `statique`, sans base : des migrations inventées, puis la liste
versionnée relue contre les fichiers du dépôt. Code 0 si tout passe.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path

CHEMIN = Path(__file__).resolve().parent / "migrations_en_attente.py"
_spec = importlib.util.spec_from_file_location("migrations_en_attente", CHEMIN)
assert _spec is not None and _spec.loader is not None
mea = importlib.util.module_from_spec(_spec)
sys.modules["migrations_en_attente"] = mea
_spec.loader.exec_module(mea)

MIGRATIONS = CHEMIN.parents[1] / "database" / "migrations"

FICHIERS = {"025": "a5", "026": "a6", "027": "a7"}
EN_PRODUCTION = {"025": "a5", "026": "a6"}
LISTEE = {"027": "schéma application"}


class Ecarts(unittest.TestCase):
    def test_nominal_la_migration_listee_attend(self) -> None:
        self.assertEqual(mea.ecarts(EN_PRODUCTION, FICHIERS, LISTEE), [])

    def test_sans_attente_base_egale_aux_fichiers(self) -> None:
        self.assertEqual(mea.ecarts(FICHIERS, FICHIERS, {}), [])

    def test_echec_une_migration_en_attente_non_listee(self) -> None:
        self.assertEqual(
            mea.ecarts(EN_PRODUCTION, FICHIERS, {}),
            ["027 : absente de la base, sans être listée en attente"],
        )

    def test_echec_une_migration_listee_mais_appliquee(self) -> None:
        self.assertEqual(
            mea.ecarts(FICHIERS, FICHIERS, LISTEE),
            ["027 : listée en attente, mais appliquée (liste périmée)"],
        )

    def test_echec_une_migration_listee_sans_fichier(self) -> None:
        self.assertEqual(
            mea.ecarts(EN_PRODUCTION, {"025": "a5", "026": "a6"}, LISTEE),
            ["027 : listée en attente, sans fichier au dépôt"],
        )

    def test_echec_une_migration_appliquee_sans_fichier(self) -> None:
        self.assertEqual(
            mea.ecarts(FICHIERS, EN_PRODUCTION, {}),
            ["027 : appliquée, sans fichier au dépôt"],
        )

    def test_echec_une_somme_de_controle_differente(self) -> None:
        self.assertEqual(
            mea.ecarts({"025": "a5", "026": "zz"}, FICHIERS, LISTEE),
            ["026 : somme de contrôle différente du fichier"],
        )


class Annonce(unittest.TestCase):
    def test_un_ecart_est_ecrit_et_rend_faux(self) -> None:
        flux = io.StringIO()
        self.assertFalse(mea.annoncer(["027 : un écart"], LISTEE, flux))
        self.assertEqual(flux.getvalue(), "migrations : 027 : un écart\n")

    def test_sans_ecart_les_attentes_sont_dites(self) -> None:
        flux = io.StringIO()
        self.assertTrue(mea.annoncer([], LISTEE, flux))
        self.assertEqual(flux.getvalue(), "migrations attendues en attente : 027\n")

    def test_sans_ecart_ni_attente_rien_n_est_ecrit(self) -> None:
        flux = io.StringIO()
        self.assertTrue(mea.annoncer([], {}, flux))
        self.assertEqual(flux.getvalue(), "")


class Liste(unittest.TestCase):
    def ecrire(self, texte: str) -> Path:
        dossier = tempfile.TemporaryDirectory()
        self.addCleanup(dossier.cleanup)
        chemin = Path(dossier.name) / "liste.tsv"
        chemin.write_text(texte, encoding="utf-8")
        return chemin

    def test_commentaires_et_lignes_vides_ignores(self) -> None:
        chemin = self.ecrire("# titre\n\n027\tmotif\tun lot\n")
        self.assertEqual(mea.lire(chemin), {"027": "motif"})

    def test_ligne_a_deux_champs_refusee(self) -> None:
        with self.assertRaises(mea.ListeInvalide):
            mea.lire(self.ecrire("027\tmotif\n"))

    def test_version_mal_formee_refusee(self) -> None:
        with self.assertRaises(mea.ListeInvalide):
            mea.lire(self.ecrire("27\tmotif\tun lot\n"))

    def test_doublon_refuse(self) -> None:
        with self.assertRaises(mea.ListeInvalide):
            mea.lire(self.ecrire("027\tmotif\tun lot\n027\tautre\tun lot\n"))

    def test_la_liste_versionnee_se_lit_et_vise_des_fichiers_du_depot(self) -> None:
        """Vide après le lot de livraison : elle doit alors seulement se lire."""
        attendues = mea.lire()
        presentes = {p.name[:3] for p in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")}
        self.assertLessEqual(set(attendues), presentes)
        if attendues:
            # Les migrations listées sont les dernières du dépôt : la production
            # n'en a pas reçu une plus récente avant elles.
            self.assertEqual(sorted(presentes)[-len(attendues) :], sorted(attendues))


if __name__ == "__main__":
    unittest.main(verbosity=2)
