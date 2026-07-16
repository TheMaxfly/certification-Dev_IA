"""Chargement + promotion du snapshot Manga Sanctuary, de bout en bout.

La base jetable et le garde-fou anti-apimanga viennent de conftest.py.

Le snapshot de test est minuscule mais porte tous les cas qui comptent : une
série à alias, un EAN valide, un EAN à clé fausse, une date tronquée, un corps
vide, deux séries dont les titres se normalisent pareil.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import lire


def volume(series_id, volume_url, **extra):
    """Un volume complet ; les 39 clés doivent exister, même vides."""
    ligne = {
        "series_id": str(series_id),
        "series_url": f"https://x/serie/{series_id}",
        "series_title": f"Serie {series_id}",
        "series_type": "Manga",
        "series_category": "Shonen",
        "series_year": "2006",
        "series_other_titles": [],
        "series_dessinateur": "D",
        "series_scenariste": "S",
        "series_genres": ["action"],
        "series_tags": [],
        "series_mag_prepub": "",
        "series_statuses": [],
        "series_popularity_rank": 1,
        "series_members_rating": 7.0,
        "series_members_votes": 42,
        "series_experts_rating": None,
        "series_experts_votes": 0,
        "series_synopsis": "Un synopsis.",
        "series_related_works": [],
        "volume_url": volume_url,
        "volume_title": "Tome",
        "volume_number": 1,
        "volume_publication_date": "mar. 27 nov. 2012",
        "volume_dessinateur": "D",
        "volume_scenariste": "S",
        "volume_editeur": "E",
        "volume_ean": None,
        "volume_format": "",
        "volume_pages": 200,
        "volume_country": "France",
        "volume_status": "Complète",
        "volume_tomes_published": 1,
        "volume_tomes_total": 1,
        "volume_members_rating": None,
        "volume_members_votes": 0,
        "volume_experts_rating": None,
        "volume_experts_votes": 0,
        "volume_synopsis": "",
    }
    ligne.update(extra)
    return ligne


def critique(review_url, series_id, **extra):
    ligne = {
        "series_id": str(series_id),
        "series_title": f"Serie {series_id}",
        "series_url": f"https://x/serie/{series_id}",
        "volume_number": 1,
        "volume_url": f"https://x/vol/{series_id}-1",
        "review_url": review_url,
        "review_title": "Critique",
        "review_score": 7.0,
        "review_author": "A",
        "review_date": "jeu. 10 oct. 2024",
        "review_type": "Staff",
        "review_body": "Un corps de critique.",
    }
    ligne.update(extra)
    return ligne


@pytest.fixture
def snapshot(tmp_path) -> Path:
    """Snapshot minuscule, mais tous les cas qui comptent."""
    volumes = [
        # EAN valide, alias, date correcte.
        volume(
            1,
            "https://x/vol/1-1",
            volume_ean="9782355929489",
            series_other_titles=["妖逆門", "BakeGyamon"],
        ),
        # Deuxième volume de la MÊME série : attributs répétés (dénormalisé).
        volume(
            1,
            "https://x/vol/1-2",
            volume_number=2,
            volume_ean="9782344029244",
            series_other_titles=["妖逆門", "BakeGyamon"],
        ),
        # EAN à clé de contrôle fausse.
        volume(2, "https://x/vol/2-1", volume_ean="9782355929488"),
        # Pas d'EAN, date sentinelle.
        volume(
            3,
            "https://x/vol/3-1",
            volume_ean=None,
            volume_publication_date="Date inconnue",
        ),
        # Titre qui se normalise comme celui de la série 1 -> collision
        # INTER-séries, légitime : deux séries homonymes.
        volume(4, "https://x/vol/4-1", series_title="Serie 1"),
        # Alias qui se normalise comme le titre principal -> collision
        # INTRA-série : le title doit gagner.
        volume(
            5,
            "https://x/vol/5-1",
            series_title="Élégante",
            series_other_titles=["elegante", "Autre Titre"],
        ),
    ]
    reviews = [
        critique("https://x/rev/1", 1),
        critique("https://x/rev/2", 1, review_date="jeu."),  # date tronquée
        critique("https://x/rev/3", 2, review_body=""),  # corps vide
    ]
    dossier = tmp_path / "2026-07"
    dossier.mkdir()
    for nom, lignes in [
        ("manga_sanctuary_volumes.jsonl", volumes),
        ("manga_sanctuary_reviews.jsonl", reviews),
    ]:
        (dossier / nom).write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in lignes) + "\n",
            encoding="utf-8",
        )
    return dossier


def executer(snapshot: Path, dsn: str):
    from identity import charger_ms

    charger_ms.charger(snapshot=snapshot, promouvoir=True)


class TestChargement:
    def test_staging_recoit_toutes_les_lignes(self, base, snapshot):
        executer(snapshot, base)
        assert lire(base, "SELECT count(*) FROM staging.ms_volumes")[0][0] == 6
        assert lire(base, "SELECT count(*) FROM staging.ms_reviews")[0][0] == 3

    def test_staging_est_tronque_a_chaque_cycle(self, base, snapshot):
        executer(snapshot, base)
        executer(snapshot, base)
        assert lire(base, "SELECT count(*) FROM staging.ms_volumes")[0][0] == 6

    def test_ligne_illisible_arrete_le_chargement(self, base, snapshot):
        from identity.charger_ms import ErreurChargement

        fichier = snapshot / "manga_sanctuary_volumes.jsonl"
        fichier.write_text(fichier.read_text() + "{ceci n'est pas du json\n")
        with pytest.raises(ErreurChargement, match="JSON illisible"):
            executer(snapshot, base)


class TestPromotion:
    def test_les_trois_grains_sont_promus(self, base, snapshot):
        executer(snapshot, base)
        assert lire(base, "SELECT count(*) FROM manga.ms_series_enriched")[0][0] == 5
        assert lire(base, "SELECT count(*) FROM manga.ms_volumes_enriched")[0][0] == 6
        assert lire(base, "SELECT count(*) FROM manga.ms_reviews_all")[0][0] == 3

    def test_serie_derivee_du_fichier_volumes(self, base, snapshot):
        """Deux volumes, une seule série : DISTINCT ON (series_id)."""
        executer(snapshot, base)
        lignes = lire(
            base,
            "SELECT series_title, series_genres FROM manga.ms_series_enriched "
            "WHERE series_id = 1",
        )
        assert len(lignes) == 1
        assert lignes[0][0] == "Serie 1"
        assert lignes[0][1] == ["action"], "les listes JSON sont castées jsonb"

    def test_dates_parsees_et_sentinelles_a_null(self, base, snapshot):
        executer(snapshot, base)
        (parsee,) = lire(
            base,
            "SELECT volume_publication_date FROM manga.ms_volumes_enriched "
            "WHERE volume_url = 'https://x/vol/1-1'",
        )[0]
        assert str(parsee) == "2012-11-27"
        (sentinelle,) = lire(
            base,
            "SELECT volume_publication_date FROM manga.ms_volumes_enriched "
            "WHERE volume_url = 'https://x/vol/3-1'",
        )[0]
        assert sentinelle is None, "« Date inconnue » n'est pas une date"

    def test_date_tronquee_est_signalee_pas_masquee(self, base, snapshot):
        executer(snapshot, base)
        brut, iso, ok = lire(
            base,
            "SELECT review_date_raw, review_date_iso, review_date_parse_ok "
            "FROM manga.ms_reviews_all WHERE review_url = 'https://x/rev/2'",
        )[0]
        assert brut == "jeu.", "le texte de la source est conservé"
        assert iso is None
        assert ok is False, "l'échec de parsing doit être mesurable"

    def test_critique_au_corps_vide_est_promue(self, base, snapshot):
        executer(snapshot, base)
        lignes = lire(
            base,
            "SELECT review_body FROM manga.ms_reviews_all "
            "WHERE review_url = 'https://x/rev/3'",
        )
        assert len(lignes) == 1, "une critique sans texte reste une critique"

    def test_review_grain_vaut_volume(self, base, snapshot):
        executer(snapshot, base)
        grains = lire(base, "SELECT DISTINCT review_grain FROM manga.ms_reviews_all")
        assert grains == [("volume",)]


class TestUpsert:
    def test_rejeu_ne_duplique_rien(self, base, snapshot):
        executer(snapshot, base)
        executer(snapshot, base)
        assert lire(base, "SELECT count(*) FROM manga.ms_series_enriched")[0][0] == 5
        assert lire(base, "SELECT count(*) FROM manga.ms_volumes_enriched")[0][0] == 6
        assert lire(base, "SELECT count(*) FROM manga.ms_reviews_all")[0][0] == 3
        assert lire(base, "SELECT count(*) FROM manga.ms_formes")[0][0] == 8
        assert lire(base, "SELECT count(*) FROM manga.volume_identity")[0][0] == 6

    def test_upsert_met_a_jour_sans_supprimer(self, base, snapshot):
        """Le cœur du cycle mensuel : mettre à jour, ajouter, ne rien perdre."""
        executer(snapshot, base)
        # Le mois suivant : une série change de titre, une autre disparaît du
        # snapshot, une nouvelle apparaît.
        fichier = snapshot / "manga_sanctuary_volumes.jsonl"
        lignes = [
            volume(
                1,
                "https://x/vol/1-1",
                series_title="Serie 1 renommee",
                volume_ean="9782355929489",
            ),
            volume(9, "https://x/vol/9-1"),
        ]
        fichier.write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in lignes) + "\n",
            encoding="utf-8",
        )
        executer(snapshot, base)

        (titre,) = lire(
            base,
            "SELECT series_title FROM manga.ms_series_enriched WHERE series_id = 1",
        )[0]
        assert titre == "Serie 1 renommee", "l'existant est mis à jour"
        assert (
            lire(
                base,
                "SELECT count(*) FROM manga.ms_series_enriched WHERE series_id = 9",
            )[0][0]
            == 1
        ), "le nouveau est ajouté"
        assert (
            lire(
                base,
                "SELECT count(*) FROM manga.ms_series_enriched WHERE series_id = 3",
            )[0][0]
            == 1
        ), "l'absent du snapshot RESTE en base"
        assert (
            lire(
                base,
                "SELECT count(*) FROM manga.ms_volumes_enriched "
                "WHERE volume_url = 'https://x/vol/3-1'",
            )[0][0]
            == 1
        ), "son volume aussi"

    def test_upsert_n_ecrase_pas_l_enrichissement_kitsu(self, base, snapshot):
        """Le risque majeur du rechargement : effacer 5 608 matchs Kitsu."""
        import psycopg

        executer(snapshot, base)
        with psycopg.connect(base) as connexion:
            connexion.execute(
                "UPDATE manga.ms_series_enriched SET kitsu_id = 4242, "
                "match_method = 'exact', match_score = 0.99, "
                "series_synopsis_enriched = 'synopsis Kitsu', "
                "series_review_count = 7 WHERE series_id = 1"
            )
            connexion.commit()

        executer(snapshot, base)  # rechargement du mois suivant

        kitsu_id, methode, score, synopsis, agregat = lire(
            base,
            "SELECT kitsu_id, match_method, match_score, "
            "series_synopsis_enriched, series_review_count "
            "FROM manga.ms_series_enriched WHERE series_id = 1",
        )[0]
        assert kitsu_id == 4242, "le rapprochement Kitsu doit survivre"
        assert methode == "exact"
        assert score == 0.99
        assert synopsis == "synopsis Kitsu"
        assert agregat == 7, "les agrégats calculés doivent survivre"

    def test_upsert_n_ecrase_pas_work_uid(self, base, snapshot):
        """work_uid n'appartient qu'à la cascade (étape C)."""
        import psycopg

        executer(snapshot, base)
        with psycopg.connect(base) as connexion:
            work_uid = connexion.execute(
                "INSERT INTO manga.work_identity (series_id) VALUES (1) "
                "RETURNING work_uid"
            ).fetchone()[0]
            connexion.execute(
                "UPDATE manga.ms_series_enriched SET work_uid = %s WHERE series_id = 1",
                (work_uid,),
            )
            connexion.commit()

        executer(snapshot, base)

        (apres,) = lire(
            base, "SELECT work_uid FROM manga.ms_series_enriched WHERE series_id = 1"
        )[0]
        assert apres == work_uid, "un rechargement ne touche pas au moyeu"


class TestFormes:
    def test_un_title_par_serie(self, base, snapshot):
        executer(snapshot, base)
        assert (
            lire(
                base, "SELECT count(*) FROM manga.ms_formes WHERE forme_type = 'title'"
            )[0][0]
            == 5
        )

    def test_alias_normalises_par_la_fonction_python(self, base, snapshot):
        executer(snapshot, base)
        formes = lire(
            base,
            "SELECT forme, forme_norm FROM manga.ms_formes "
            "WHERE series_id = 1 AND forme_type = 'alias' ORDER BY forme_norm",
        )
        assert ("BakeGyamon", "bakegyamon") in formes
        assert ("妖逆門", "妖逆門") in formes

    def test_langue_toujours_nulle(self, base, snapshot):
        executer(snapshot, base)
        assert (
            lire(base, "SELECT count(*) FROM manga.ms_formes WHERE langue IS NOT NULL")[
                0
            ][0]
            == 0
        )

    def test_collision_intra_serie_le_title_gagne(self, base, snapshot):
        """« Élégante » et l'alias « elegante » se normalisent pareil : une
        seule forme, et c'est le titre principal qui reste."""
        executer(snapshot, base)
        formes = lire(
            base,
            "SELECT forme, forme_type FROM manga.ms_formes "
            "WHERE series_id = 5 AND forme_norm = 'elegante'",
        )
        assert formes == [("Élégante", "title")]

    def test_serie_renommee_garde_un_seul_titre_principal(self, base, snapshot):
        """33 séries ont été renommées entre 2025-12 et 2026-07. Sans
        rétrogradation, l'ancien titre resterait 'title' à côté du nouveau et la
        cascade aurait deux titres principaux pour une même série."""
        executer(snapshot, base)
        fichier = snapshot / "manga_sanctuary_volumes.jsonl"
        fichier.write_text(
            json.dumps(
                volume(1, "https://x/vol/1-1", series_title="Serie 1 renommee"),
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        executer(snapshot, base)

        titles = lire(
            base,
            "SELECT forme FROM manga.ms_formes "
            "WHERE series_id = 1 AND forme_type = 'title'",
        )
        assert titles == [("Serie 1 renommee",)], "un seul titre principal"

    def test_ancien_titre_devient_alias_pas_un_trou(self, base, snapshot):
        """Un nom que l'œuvre a porté reste une cible de rapprochement."""
        executer(snapshot, base)
        fichier = snapshot / "manga_sanctuary_volumes.jsonl"
        fichier.write_text(
            json.dumps(
                volume(1, "https://x/vol/1-1", series_title="Serie 1 renommee"),
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        executer(snapshot, base)

        formes = lire(
            base,
            "SELECT forme, forme_type FROM manga.ms_formes "
            "WHERE series_id = 1 AND forme_norm = 'serie 1'",
        )
        assert formes == [("Serie 1", "alias")], "l'ex-titre survit, en alias"

    def test_titre_deja_present_en_alias_est_promu(self, base, snapshot):
        """Si le nouveau titre était déjà un alias, il est promu — pas dupliqué
        (l'UNIQUE (series_id, forme_norm, source) l'interdirait)."""
        executer(snapshot, base)
        fichier = snapshot / "manga_sanctuary_volumes.jsonl"
        fichier.write_text(
            json.dumps(
                volume(
                    1,
                    "https://x/vol/1-1",
                    series_title="BakeGyamon",
                    series_other_titles=["Serie 1"],
                ),
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        executer(snapshot, base)

        formes = lire(
            base,
            "SELECT forme_type FROM manga.ms_formes "
            "WHERE series_id = 1 AND forme_norm = 'bakegyamon'",
        )
        assert formes == [("title",)], "l'ancien alias est promu titre"

    def test_collision_inter_series_conservee(self, base, snapshot):
        """Deux séries homonymes : c'est le problème que la cascade doit
        trancher, pas une erreur à écraser."""
        executer(snapshot, base)
        series = lire(
            base,
            "SELECT series_id FROM manga.ms_formes "
            "WHERE forme_norm = 'serie 1' ORDER BY series_id",
        )
        assert series == [(1,), (4,)]


class TestVolumeIdentity:
    def test_une_ligne_par_volume(self, base, snapshot):
        executer(snapshot, base)
        assert lire(base, "SELECT count(*) FROM manga.volume_identity")[0][0] == 6

    def test_ean_valide_devient_isbn13(self, base, snapshot):
        executer(snapshot, base)
        isbn, valide = lire(
            base,
            "SELECT isbn13, isbn13_valide FROM manga.volume_identity "
            "WHERE volume_url = 'https://x/vol/1-1'",
        )[0]
        assert isbn == "9782355929489"
        assert valide is True

    def test_ean_a_cle_fausse_reste_null_mais_signale(self, base, snapshot):
        executer(snapshot, base)
        isbn, valide = lire(
            base,
            "SELECT isbn13, isbn13_valide FROM manga.volume_identity "
            "WHERE volume_url = 'https://x/vol/2-1'",
        )[0]
        assert isbn is None, "un EAN faux n'entre pas dans isbn13"
        assert valide is False, "mais l'échec est enregistré, pas effacé"

    def test_absence_d_ean_se_distingue_d_un_ean_faux(self, base, snapshot):
        executer(snapshot, base)
        isbn, valide = lire(
            base,
            "SELECT isbn13, isbn13_valide FROM manga.volume_identity "
            "WHERE volume_url = 'https://x/vol/3-1'",
        )[0]
        assert isbn is None
        assert valide is None, "pas d'EAN du tout : ni valide, ni invalide"

    def test_ean_brut_reste_sur_le_volume(self, base, snapshot):
        """La traçabilité : ms_volumes garde ce que la source a affiché."""
        executer(snapshot, base)
        (brut,) = lire(
            base,
            "SELECT volume_ean FROM manga.ms_volumes_enriched "
            "WHERE volume_url = 'https://x/vol/2-1'",
        )[0]
        assert brut == "9782355929488", "l'EAN faux reste visible côté source"

    def test_work_uid_reste_null(self, base, snapshot):
        executer(snapshot, base)
        assert (
            lire(
                base,
                "SELECT count(*) FROM manga.volume_identity WHERE work_uid IS NOT NULL",
            )[0][0]
            == 0
        )
