"""Chargeurs Wikidata et Kitsu, de bout en bout, sur base JETABLE.

Réutilise le harnais de test_charger_ms (conteneur + base migrée 000->006).
`apimanga` n'est jamais atteignable.

Les fixtures sont minuscules mais portent les cas qui décident : une forme dont
la normalisation du CSV a vieilli, un qid sans entité, un subtype exclu, un site
externe hors cible, un titre canonique identique à son titre en_jp.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import lire

CIBLE = "manga"


def ecrire_csv(dossier: Path, nom: str, entete: str, lignes: list[str]) -> None:
    (dossier / nom).write_text(
        entete + "\n" + "\n".join(lignes) + "\n", encoding="utf-8"
    )


@pytest.fixture
def source_wd(tmp_path) -> Path:
    dossier = tmp_path / "staging"
    dossier.mkdir()
    ecrire_csv(
        dossier,
        "wd_pivot.csv",
        "qid,mal_id,anilist_id",
        ["Q1,101,201", "Q2,102,", "Q3,,203", "Q4,,"],
    )
    ecrire_csv(
        dossier,
        "wd_entities.csv",
        "qid,label_principal,annee,mal_id,anilist_id,ann_id,wiki_fr,wiki_en",
        [
            "Q1,Dragon Ball,1984,101,201,A1,fr/Dragon_Ball,en/Dragon_Ball",
            "Q2,Élégante,1990,102,,,,",
            "Q3,Naruto,1999,,203,,,",
            "Q4,Sans identifiant,,,,,,",
        ],
    )
    ecrire_csv(
        dossier,
        "wd_formes.csv",
        "qid,forme_normalisee,forme_originale,langue,type",
        [
            "Q1,dragon ball,Dragon Ball,en,label",
            "Q1,dragon ball,DRAGON BALL,ja,alias",  # collision normalisée
            "Q1,VALEUR-PERIMEE,Dragonball,en,alias",  # normalisation vieillie
            "Q2,elegante,Élégante,fr,label",
            "Q3,naruto,Naruto,en,label",
            "Q9,orpheline,Orpheline,en,label",  # qid sans entité
        ],
    )
    ecrire_csv(
        dossier,
        "wd_auteurs.csv",
        "qid,auteur_qid",
        ["Q1,Q1000", "Q1,Q1001", "Q3,Q1002", "Q9,Q1003"],
    )
    return dossier


def entree_kitsu(kitsu_id, subtype=CIBLE, **attrs):
    attributs = {"subtype": subtype, "canonicalTitle": f"Titre {kitsu_id}"}
    attributs.update(attrs)
    return {"data": {"id": str(kitsu_id), "type": "manga", "attributes": attributs}}


def page_mappings(kitsu_id, mappings):
    return {
        "manga_id": str(kitsu_id),
        "http_status": 200,
        "data": [
            {
                "id": str(1000 + i),
                "type": "mappings",
                "attributes": {"externalSite": site, "externalId": ext},
            }
            for i, (site, ext) in enumerate(mappings)
        ],
    }


@pytest.fixture
def run_kitsu(tmp_path) -> Path:
    catalogue = tmp_path / "full_catalog"
    dossier = catalogue / "20260714T152202Z"
    (dossier / "relations").mkdir(parents=True)
    (catalogue / "LATEST").write_text("20260714T152202Z\n", encoding="utf-8")

    entrees = [
        # Canonique == en_jp : une seule forme après dédup normalisée.
        entree_kitsu(
            1,
            titles={"en_jp": "Titre 1", "ja_jp": "タイトル"},
            abbreviatedTitles=["T1"],
        ),
        # Un manhwa : le coréen ne doit pas disparaître.
        entree_kitsu(2, subtype="manhwa", titles={"ko_kr": "한국어", "en": "Korean"}),
        entree_kitsu(3, subtype="manhua", titles={"zh_cn": "中文"}),
        # HORS CIBLE : ni formes, ni mappings.
        entree_kitsu(4, subtype="novel", titles={"en": "Un roman"}),
        entree_kitsu(5, subtype="oneshot"),
    ]
    (dossier / "manga.ndjson").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entrees) + "\n",
        encoding="utf-8",
    )
    pages = [
        page_mappings(
            1,
            [
                ("myanimelist/manga", "101"),
                ("anilist/manga", "201"),
                ("myanimelist/anime", "999"),
            ],
        ),  # site exclu
        page_mappings(2, [("mangaupdates", "301")]),
        page_mappings(3, []),  # page sans mapping
        page_mappings(4, [("myanimelist/manga", "444")]),  # subtype exclu
    ]
    (dossier / "relations/mappings.ndjson").write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in pages) + "\n",
        encoding="utf-8",
    )
    return catalogue


def charger_wd(source: Path):
    from identity import charger_wikidata

    charger_wikidata.charger(source=source)


def charger_kitsu(catalogue: Path, run=None):
    from identity import charger_kitsu as module

    module.CATALOGUE = catalogue
    module.charger(run=run)


class TestWikidata:
    def test_pivot_fusionne_les_deux_csv(self, base, source_wd):
        charger_wd(source_wd)
        lignes = lire(
            base,
            "SELECT qid, label_principal, annee, mal_id, anilist_id, wiki_fr "
            "FROM manga.wd_pivot WHERE qid = 'Q1'",
        )
        assert lignes == [("Q1", "Dragon Ball", 1984, "101", "201", "fr/Dragon_Ball")]

    def test_pivot_charge_toutes_les_entites(self, base, source_wd):
        charger_wd(source_wd)
        assert lire(base, "SELECT count(*) FROM manga.wd_pivot")[0][0] == 4

    def test_identifiants_manquants_restent_null(self, base, source_wd):
        charger_wd(source_wd)
        mal, anilist = lire(
            base, "SELECT mal_id, anilist_id FROM manga.wd_pivot WHERE qid = 'Q4'"
        )[0]
        assert mal is None and anilist is None

    def test_forme_norm_est_recalculee_pas_lue_du_csv(self, base, source_wd):
        """Le CSV dit « VALEUR-PERIMEE » ; normaliser() dit « dragonball »."""
        charger_wd(source_wd)
        (norm,) = lire(
            base,
            "SELECT forme_norm FROM manga.wd_formes WHERE forme = 'Dragonball'",
        )[0]
        assert norm == "dragonball", "la normalisation du fichier ne fait pas foi"

    def test_langue_est_conservee(self, base, source_wd):
        """Côté Wikidata la langue est DÉCLARÉE : elle doit survivre."""
        charger_wd(source_wd)
        langues = dict(
            lire(
                base,
                "SELECT forme, langue FROM manga.wd_formes WHERE qid = 'Q1' "
                "AND langue IS NOT NULL",
            )
        )
        assert langues["Dragon Ball"] == "en"

    def test_collision_normalisee_dedoublonnee_par_qid(self, base, source_wd):
        """« Dragon Ball » et « DRAGON BALL » : une seule forme pour Q1."""
        charger_wd(source_wd)
        formes = lire(
            base,
            "SELECT count(*) FROM manga.wd_formes "
            "WHERE qid = 'Q1' AND forme_norm = 'dragon ball'",
        )
        assert formes[0][0] == 1

    def test_forme_sans_entite_est_ecartee(self, base, source_wd):
        """Q9 n'a pas d'entité : la FK la refuserait, on la compte et on passe."""
        charger_wd(source_wd)
        assert (
            lire(base, "SELECT count(*) FROM manga.wd_formes WHERE qid = 'Q9'")[0][0]
            == 0
        )

    def test_auteurs_charges_et_orphelins_ecartes(self, base, source_wd):
        charger_wd(source_wd)
        assert lire(base, "SELECT count(*) FROM manga.wd_auteurs")[0][0] == 3
        assert (
            lire(base, "SELECT count(*) FROM manga.wd_auteurs WHERE qid = 'Q9'")[0][0]
            == 0
        )

    def test_auteur_reste_null_faute_de_nom(self, base, source_wd):
        """La source ne donne qu'un Q-id : inventer un nom serait pire que NULL."""
        charger_wd(source_wd)
        assert (
            lire(
                base, "SELECT count(*) FROM manga.wd_auteurs WHERE auteur IS NOT NULL"
            )[0][0]
            == 0
        )

    def test_rejeu_ne_duplique_rien(self, base, source_wd):
        charger_wd(source_wd)
        charger_wd(source_wd)
        assert lire(base, "SELECT count(*) FROM manga.wd_pivot")[0][0] == 4
        assert lire(base, "SELECT count(*) FROM manga.wd_auteurs")[0][0] == 3


class TestKitsu:
    def test_seule_la_cible_produit_des_formes(self, base, run_kitsu):
        charger_kitsu(run_kitsu)
        subtypes = lire(
            base, "SELECT DISTINCT subtype FROM manga.kitsu_formes ORDER BY 1"
        )
        assert subtypes == [("manga",), ("manhua",), ("manhwa",)]
        assert (
            lire(
                base, "SELECT count(*) FROM manga.kitsu_formes WHERE kitsu_id IN (4, 5)"
            )[0][0]
            == 0
        )

    def test_langues_non_latines_survivent(self, base, run_kitsu):
        """Le coréen et le chinois sont les manhwa et manhua de la cible :
        écraser la langue dans le forme_type les perdrait."""
        charger_kitsu(run_kitsu)
        langues = dict(
            lire(
                base,
                "SELECT langue, forme FROM manga.kitsu_formes "
                "WHERE forme_type = 'title' AND langue IN ('ko_kr', 'zh_cn')",
            )
        )
        assert langues == {"ko_kr": "한국어", "zh_cn": "中文"}

    def test_canonique_egal_en_jp_ne_fait_qu_une_forme(self, base, run_kitsu):
        """Cas ordinaire d'une œuvre sans titre traduit : l'UNIQUE dédoublonne
        et le canonique gagne, car il est rencontré en premier."""
        charger_kitsu(run_kitsu)
        formes = lire(
            base,
            "SELECT forme_type FROM manga.kitsu_formes "
            "WHERE kitsu_id = 1 AND forme_norm = 'titre 1'",
        )
        assert formes == [("canonical",)]

    def test_forme_type_suit_les_cles_reelles(self, base, run_kitsu):
        charger_kitsu(run_kitsu)
        types = {
            t
            for (t,) in lire(base, "SELECT DISTINCT forme_type FROM manga.kitsu_formes")
        }
        assert types == {"canonical", "title", "abbreviated"}

    def test_staging_recoit_tous_les_mappings_meme_exclus(self, base, run_kitsu):
        """Le staging n'arbitre pas : c'est ce qui rend le filtre mesurable."""
        charger_kitsu(run_kitsu)
        assert lire(base, "SELECT count(*) FROM staging.kitsu_mappings")[0][0] == 5
        assert (
            lire(
                base,
                "SELECT count(*) FROM staging.kitsu_mappings "
                "WHERE external_site = 'myanimelist/anime'",
            )[0][0]
            == 1
        )

    def test_promotion_ecarte_les_sites_hors_manga(self, base, run_kitsu):
        charger_kitsu(run_kitsu)
        sites = {
            s
            for (s,) in lire(
                base, "SELECT DISTINCT external_site FROM manga.kitsu_mappings"
            )
        }
        assert sites == {"myanimelist/manga", "anilist/manga", "mangaupdates"}

    def test_promotion_ecarte_les_mappings_hors_subtype(self, base, run_kitsu):
        """kitsu_id 4 est un novel : son mapping ne doit pas entrer."""
        charger_kitsu(run_kitsu)
        assert (
            lire(base, "SELECT count(*) FROM manga.kitsu_mappings WHERE kitsu_id = 4")[
                0
            ][0]
            == 0
        )
        assert lire(base, "SELECT count(*) FROM manga.kitsu_mappings")[0][0] == 3

    def test_run_resolu_par_latest(self, base, run_kitsu):
        charger_kitsu(run_kitsu, run=None)
        assert lire(base, "SELECT count(*) FROM manga.kitsu_formes")[0][0] > 0

    def test_run_inconnu_refuse(self, base, run_kitsu):
        from identity.charger_kitsu import ErreurChargement

        with pytest.raises(ErreurChargement, match="introuvable"):
            charger_kitsu(run_kitsu, run="20991231T000000Z")

    def test_rejeu_ne_duplique_rien(self, base, run_kitsu):
        charger_kitsu(run_kitsu)
        formes = lire(base, "SELECT count(*) FROM manga.kitsu_formes")[0][0]
        mappings = lire(base, "SELECT count(*) FROM manga.kitsu_mappings")[0][0]
        charger_kitsu(run_kitsu)
        assert lire(base, "SELECT count(*) FROM manga.kitsu_formes")[0][0] == formes
        assert lire(base, "SELECT count(*) FROM manga.kitsu_mappings")[0][0] == mappings


class TestPontDeLaCascade:
    def test_wikidata_rejoint_kitsu_par_les_mappings(self, base, source_wd, run_kitsu):
        """La raison d'être de 006 : sans identifiant commun, le pivot Wikidata
        (mal_id) rejoint Kitsu (kitsu_id) par la table de mappings."""
        charger_wd(source_wd)
        charger_kitsu(run_kitsu)
        ponts = lire(
            base,
            "SELECT p.qid, m.kitsu_id FROM manga.wd_pivot p "
            "JOIN manga.kitsu_mappings m "
            "  ON m.external_site = 'myanimelist/manga' AND m.external_id = p.mal_id "
            "ORDER BY p.qid",
        )
        assert ponts == [("Q1", 1)], "Q1 (mal_id 101) doit rejoindre kitsu_id 1"
