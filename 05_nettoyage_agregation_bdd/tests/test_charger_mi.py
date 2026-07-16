"""Chargeur Manga Insight, de bout en bout, sur base JETABLE.

Le parquet de test est fabriqué ici : minuscule, mais il porte les cas qui ont
motivé la révision de décision de 007 — un EAN absent, un EAN faux, deux
sorties partageant un EAN (erreur source), une réédition partageant l'EAN de
son original, et les deux formats de date que la source mélange.

Il reproduit aussi les noms de colonnes RÉELS, espace parasite de
« Prépublication » comprise : une fixture aux noms propres passerait au vert
pendant que le vrai fichier fait échouer le chargeur — c'est arrivé.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import lire

COLONNES = [
    "Original Url",
    "Adresse",
    "Code HTTP",
    "Title",
    "Titre VO",
    "Adresse.1",
    "Titre traduit",
    "Éditeur VF",
    "Éditeur VO",
    "Type",
    "Genre 1",
    "Genre 2",
    "Prépublication ",  # espace finale : le fichier réel la porte
    "Nombre tomes VF",
    "Nombre tomes VO",
    "Statut VF",
    "Statut VO",
    "Pays",
    "Année pays d'origine",
    "Date sortie France",
    "Année",
    "_catégorie",
    "_fichier",
    "_année_fichier",
    "_mois_fichier",
    "Date sortie France - année",
    "Date sortie France - mois",
    "Tomes VF",
    "Tomes VO",
    "Unnamed: 19",
    "Unnamed: 0",
    "Titre",
    "Ean",
    "_nouveauté",
    "_nouvelle_édition",
    "_coffret",
    "_collector",
    "_type_titre",
    "_type_source",
    "_doublon_éditeur",
    "_éditeurs_doublons",
    "Dessin",
    "Scénario",
]


def ligne(**valeurs):
    """Une ligne des 43 colonnes ; seules celles qui comptent sont passées."""
    base = dict.fromkeys(COLONNES)
    base.update(
        {
            "_nouveauté": False,
            "_nouvelle_édition": False,
            "_coffret": False,
            "_collector": False,
            "_doublon_éditeur": False,
        }
    )
    base.update(valeurs)
    return base


def sortie(**valeurs):
    """Population A : « Original Url » vide."""
    return ligne(**{"Original Url": None, **valeurs})


def serie(url, **valeurs):
    """Population B : « Original Url » rempli."""
    return ligne(**{"Original Url": url, **valeurs})


def ecrire_parquet(dossier: Path, lignes: list[dict]) -> Path:
    dossier.mkdir(parents=True, exist_ok=True)
    colonnes = {
        nom: pa.array(
            [ligne[nom] for ligne in lignes],
            type=pa.bool_()
            if nom
            in {
                "_nouveauté",
                "_nouvelle_édition",
                "_coffret",
                "_collector",
                "_doublon_éditeur",
            }
            else pa.float64()
            if nom in {"Code HTTP", "Unnamed: 19"}
            else pa.int64()
            if nom
            in {
                "Année pays d'origine",
                "Année",
                "Date sortie France - année",
                "Date sortie France - mois",
                "Tomes VF",
                "Tomes VO",
            }
            else pa.large_string(),
        )
        for nom in COLONNES
    }
    chemin = dossier / "data.parquet"
    pq.write_table(pa.table(colonnes), chemin)
    (dossier / "MANIFEST.md").write_text("# raw de test\n", encoding="utf-8")
    return dossier


@pytest.fixture
def raw(tmp_path) -> Path:
    lignes = [
        # EAN valide, date ISO.
        sortie(
            **{
                "Titre": "Berserk Vol.1",
                "Ean": "9782355929489",
                "Éditeur VF": "Glénat",
                "Date sortie France": "1978-01-04 00:00:00",
            }
        ),
        # EAN à clé fausse, date française à mois capitalisé.
        sortie(
            **{
                "Titre": "Faux EAN",
                "Ean": "9782355929488",
                "Éditeur VF": "Kana",
                "Date sortie France": "01 Octobre 2025",
            }
        ),
        # AUCUN EAN : l'upsert clé EAN l'aurait perdue.
        sortie(**{"Titre": "Sans EAN", "Ean": None, "Éditeur VF": "Noeve"}),
        # Deux œuvres DIFFÉRENTES, même EAN : erreur de la source.
        sortie(
            **{
                "Titre": "Berserk of Gluttony Vol.12",
                "Ean": "9782487369641",
                "Éditeur VF": "Mahô Editions",
            }
        ),
        sortie(
            **{
                "Titre": "Martial Universe Vol.10",
                "Ean": "9782487369641",
                "Éditeur VF": "Mahô Editions",
            }
        ),
        # Réédition partageant l'EAN de son original : légitime.
        sortie(
            **{
                "Titre": "NonNonBa - Edition 2011",
                "Ean": "9782360810284",
                "Éditeur VF": "Cornelius",
            }
        ),
        sortie(
            **{
                "Titre": "NonNonBa - Edition 2024",
                "Ean": "9782360810284",
                "Éditeur VF": "Cornelius",
                "_nouvelle_édition": True,
            }
        ),
        # Population B.
        serie(
            "https://www.manga-news.com/serie/Berserk",
            **{
                "Title": "Berserk",
                "Code HTTP": 200.0,
                "Éditeur VF": "Glénat",
                "Année": 1989,
                "Nombre tomes VF": "41",
            },
        ),
        serie(
            "https://www.manga-news.com/serie/Monster",
            **{
                "Title": "Monster",
                "Code HTTP": 200.0,
                "Éditeur VF": "Kana",
            },
        ),
    ]
    return ecrire_parquet(tmp_path / "mi" / "2026-07", lignes)


def executer(raw: Path):
    from identity import charger_mi

    charger_mi.charger(raw=raw)


class TestPartition:
    def test_les_deux_populations_sont_separees(self, base, raw):
        executer(raw)
        assert lire(base, "SELECT count(*) FROM manga.mi_sorties")[0][0] == 7
        assert lire(base, "SELECT count(*) FROM manga.mi_series")[0][0] == 2

    def test_le_critere_est_original_url(self, base, raw):
        """Vide -> A (aucune colonne original_url), rempli -> B."""
        executer(raw)
        urls = lire(base, "SELECT original_url FROM manga.mi_series ORDER BY 1")
        assert [u for (u,) in urls] == [
            "https://www.manga-news.com/serie/Berserk",
            "https://www.manga-news.com/serie/Monster",
        ]

    def test_colonnes_snake_case(self, base, raw):
        executer(raw)
        colonnes = {
            c
            for (c,) in lire(
                base,
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'manga' AND table_name = 'mi_sorties'",
            )
        }
        for attendue in ("editeur_vf", "meta_nouvelle_edition", "annee_pays_d_origine"):
            assert attendue in colonnes


class TestRienNEstPerdu:
    """Le cœur de la révision de 007 : l'upsert clé EAN aurait perdu ces lignes."""

    def test_la_sortie_sans_ean_entre(self, base, raw):
        executer(raw)
        assert (
            lire(
                base, "SELECT count(*) FROM manga.mi_sorties WHERE titre = 'Sans EAN'"
            )[0][0]
            == 1
        )

    def test_les_deux_oeuvres_au_meme_ean_entrent_toutes_les_deux(self, base, raw):
        executer(raw)
        titres = lire(
            base,
            "SELECT titre FROM manga.mi_sorties WHERE ean = '9782487369641' "
            "ORDER BY titre",
        )
        assert [t for (t,) in titres] == [
            "Berserk of Gluttony Vol.12",
            "Martial Universe Vol.10",
        ]

    def test_les_trois_editions_au_meme_ean_entrent(self, base, raw):
        executer(raw)
        assert (
            lire(
                base,
                "SELECT count(*) FROM manga.mi_sorties WHERE ean = '9782360810284'",
            )[0][0]
            == 2
        )


class TestEan:
    def test_ean_valide_signale(self, base, raw):
        executer(raw)
        assert (
            lire(
                base,
                "SELECT ean_valide FROM manga.mi_sorties WHERE titre = 'Berserk Vol.1'",
            )[0][0]
            is True
        )

    def test_ean_faux_signale_pas_efface(self, base, raw):
        executer(raw)
        ean, valide = lire(
            base,
            "SELECT ean, ean_valide FROM manga.mi_sorties WHERE titre = 'Faux EAN'",
        )[0]
        assert ean == "9782355929488", "l'EAN faux reste visible"
        assert valide is False, "et son échec est enregistré"

    def test_absence_d_ean_se_distingue_d_un_ean_faux(self, base, raw):
        executer(raw)
        ean, valide = lire(
            base,
            "SELECT ean, ean_valide FROM manga.mi_sorties WHERE titre = 'Sans EAN'",
        )[0]
        assert ean is None
        assert valide is None, "ni valide, ni invalide : absent"


class TestVueEanMultiples:
    def test_la_vue_liste_les_ean_partages(self, base, raw):
        executer(raw)
        eans = lire(
            base, "SELECT ean, nb_sorties FROM manga.v_mi_ean_multiples ORDER BY ean"
        )
        assert eans == [("9782360810284", 2), ("9782487369641", 2)]

    def test_la_vue_distingue_erreur_source_et_reedition(self, base, raw):
        """Deux titres différents sur un EAN = erreur ; deux éditions du même
        titre = réédition légitime."""
        executer(raw)
        divergents = dict(
            lire(base, "SELECT ean, titres_divergents FROM manga.v_mi_ean_multiples")
        )
        assert divergents["9782487369641"] is True, "deux œuvres : erreur source"
        assert divergents["9782360810284"] is True, "deux éditions : titres distincts"


class TestDates:
    def test_les_deux_formats_sont_lus(self, base, raw):
        executer(raw)
        iso = lire(
            base,
            "SELECT date_sortie_france FROM manga.mi_sorties "
            "WHERE titre = 'Berserk Vol.1'",
        )[0][0]
        francais = lire(
            base,
            "SELECT date_sortie_france FROM manga.mi_sorties WHERE titre = 'Faux EAN'",
        )[0][0]
        assert str(iso) == "1978-01-04"
        assert str(francais) == "2025-10-01"

    def test_la_valeur_brute_est_conservee(self, base, raw):
        executer(raw)
        brute = lire(
            base,
            "SELECT date_sortie_france_raw FROM manga.mi_sorties "
            "WHERE titre = 'Berserk Vol.1'",
        )[0][0]
        assert brute == "1978-01-04 00:00:00"


class TestRechargement:
    def test_rejeu_ne_change_aucun_compte(self, base, raw):
        executer(raw)
        executer(raw)
        assert lire(base, "SELECT count(*) FROM manga.mi_sorties")[0][0] == 7
        assert lire(base, "SELECT count(*) FROM manga.mi_series")[0][0] == 2

    def test_la_table_est_le_snapshot(self, base, raw, tmp_path):
        """Une sortie retirée de la source disparaît de la table : c'est la
        sémantique choisie, et elle est explicite."""
        executer(raw)
        ecrire_parquet(
            tmp_path / "mi" / "2026-08",
            [
                sortie(**{"Titre": "Berserk Vol.1", "Ean": "9782355929489"}),
                sortie(**{"Titre": "Nouveau", "Ean": "9782344029244"}),
                sortie(**{"Titre": "Rembourrage 1"}),
                sortie(**{"Titre": "Rembourrage 2"}),
                sortie(**{"Titre": "Rembourrage 3"}),
                sortie(**{"Titre": "Rembourrage 4"}),
                sortie(**{"Titre": "Rembourrage 5"}),
                serie(
                    "https://www.manga-news.com/serie/Berserk", **{"Title": "Berserk"}
                ),
                serie(
                    "https://www.manga-news.com/serie/Monster", **{"Title": "Monster"}
                ),
            ],
        )
        executer(tmp_path / "mi" / "2026-08")
        titres = {t for (t,) in lire(base, "SELECT titre FROM manga.mi_sorties")}
        assert "Nouveau" in titres
        assert "Sans EAN" not in titres, "la table reflète le snapshot du mois"

    def test_le_plancher_annule_un_fichier_tronque(self, base, raw, tmp_path):
        """MUTATION : sans le plancher, un fichier tronqué remplacerait une
        table saine et la perte passerait inaperçue."""
        from identity.charger_mi import ErreurChargement

        executer(raw)
        avant = lire(base, "SELECT count(*) FROM manga.mi_sorties")[0][0]
        ecrire_parquet(
            tmp_path / "mi" / "tronque",
            [
                sortie(**{"Titre": "Seule rescapée", "Ean": "9782355929489"}),
                serie(
                    "https://www.manga-news.com/serie/Berserk", **{"Title": "Berserk"}
                ),
            ],
        )
        with pytest.raises(ErreurChargement, match="PLANCHER DE VOLUMÉTRIE"):
            executer(tmp_path / "mi" / "tronque")

        assert lire(base, "SELECT count(*) FROM manga.mi_sorties")[0][0] == avant, (
            "la table doit être intacte : le DELETE est annulé avec le reste"
        )

    def test_le_plancher_laisse_passer_une_baisse_normale(self, base, raw, tmp_path):
        """Le seuil est à 90 % : un snapshot de volumétrie comparable passe.
        Les DEUX populations doivent tenir le plancher — mi_series aussi."""
        executer(raw)
        lignes = [sortie(**{"Titre": f"Sortie {i}", "Ean": None}) for i in range(7)] + [
            serie("https://www.manga-news.com/serie/Berserk", **{"Title": "Berserk"}),
            serie("https://www.manga-news.com/serie/Monster", **{"Title": "Monster"}),
        ]
        ecrire_parquet(tmp_path / "mi" / "normal", lignes)
        executer(tmp_path / "mi" / "normal")
        assert lire(base, "SELECT count(*) FROM manga.mi_sorties")[0][0] == 7
        assert lire(base, "SELECT count(*) FROM manga.mi_series")[0][0] == 2


class TestGardeFous:
    def test_raw_sans_parquet_refuse(self, base, tmp_path):
        from identity.charger_mi import ErreurChargement

        (tmp_path / "vide").mkdir()
        with pytest.raises(ErreurChargement, match="introuvable"):
            executer(tmp_path / "vide")

    def test_raw_sans_manifest_refuse(self, base, raw, tmp_path):
        """Un raw sans provenance ne se charge pas : on doit toujours pouvoir
        dire d'où viennent les données en base."""
        from identity.charger_mi import ErreurChargement

        (raw / "MANIFEST.md").unlink()
        with pytest.raises(ErreurChargement, match="MANIFEST"):
            executer(raw)


class TestColonnesReelles:
    """Le fichier réel porte « Prépublication » avec une espace finale."""

    def test_l_espace_parasite_est_toleree(self, base, raw):
        executer(raw)
        assert lire(base, "SELECT count(*) FROM manga.mi_series")[0][0] == 2

    def test_colonne_attendue_absente_arrete_tout(self, base, tmp_path):
        """MUTATION : sans le contrôle, une colonne disparue chargerait des
        NULL en silence et le changement de schéma passerait inaperçu."""
        from identity.charger_mi import ErreurChargement

        dossier = tmp_path / "mi" / "ampute"
        dossier.mkdir(parents=True)
        colonnes = [c for c in COLONNES if c != "Éditeur VF"]
        table = pa.table(
            {c: pa.array([None], type=pa.large_string()) for c in colonnes}
        )
        pq.write_table(table, dossier / "data.parquet")
        (dossier / "MANIFEST.md").write_text("# test\n", encoding="utf-8")

        with pytest.raises(ErreurChargement, match="Colonnes absentes"):
            executer(dossier)
