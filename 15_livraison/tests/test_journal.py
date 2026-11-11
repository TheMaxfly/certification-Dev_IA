"""Le journal : sa forme, ses empreintes, et aucun secret (motifs du hook rejoués)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import E4, Poste
from outils_essai import relecture_reelle

from livraison import images, journal, sante
from livraison.codes import Arret, Code
from livraison.outils import Outils

#: Une chaîne d'aspect de clé (préfixe et longueur), factice : jamais une vraie clé.
ASPECT = "sk-" + "a1B2" * 12


def entree(o: Outils) -> journal.Entree:
    liste = images.images_de(o, E4)
    e = journal.Entree(
        cible="production",
        geste="déploiement",
        etiquette=E4,
        commit="a" * 40,
        debut=datetime(2026, 11, 11, 10, 0, 0, tzinfo=UTC),
        utilisateur="poste",
        duree_s=42.4,
        images=images.presentes(o, liste),
        migrations_avant="27 appliquées, 3 en attente (apimanga)",
        migrations_apres="30 appliquées, 0 en attente (apimanga)",
        controles=[sante.Controle("api", "version_api", "1.5.0", "1.5.0", True)],
        verdict="santé verte",
    )
    return e


def test_la_forme_du_journal(o: Outils):
    texte = journal.rendre(entree(o))
    for attendu in (
        "# Déploiement — cible production, étiquette `e4-livraison`",
        "| Étiquette | `e4-livraison` (commit `aaaaaaaaaaaa`) |",
        "| Durée | 42 s |",
        "| Utilisateur du poste | `poste` |",
        "| `api-modele:1.5.0` | `sha256:" + "1" * 64 + "` | présente |",
        "- avant : 27 appliquées, 3 en attente (apimanga)",
        "- après : 30 appliquées, 0 en attente (apimanga)",
        "| api | version_api | 1.5.0 | 1.5.0 | tenu |",
        "## Appels facturés",
    ):
        assert attendu in texte, attendu
    assert "Motif" not in texte


def test_un_journal_porte_toujours_ses_empreintes(o: Outils):
    texte = journal.rendre(entree(o))
    for c in entree(o).images:
        assert c.empreinte in texte


def test_ecrit_puis_indexe(o: Outils):
    chemin = journal.ecrire(o, entree(o))
    assert chemin.name == "20261111T100000Z_production_e4-livraison.md"
    index = (o.journal / journal.INDEX).read_text(encoding="utf-8")
    assert index.startswith(journal.EN_TETE_INDEX)
    assert f"[{chemin.name}]({chemin.name})" in index
    second = entree(o)
    second.debut = datetime(2026, 11, 11, 11, 0, 0, tzinfo=UTC)
    journal.ecrire(o, second)
    lignes = (o.journal / journal.INDEX).read_text(encoding="utf-8").splitlines()
    assert len([ligne for ligne in lignes if ligne.startswith("| 2026")]) == 2
    assert journal.dernier(o, "production") == o.journal / second.nom_fichier
    assert journal.dernier(o, "staging") is None


@pytest.mark.parametrize(
    "reglage",
    [{"refus_cles": ["chaîne d'aspect clé dans journal"]}, {"refus_mention": True}],
)
def test_un_refus_du_hook_n_ecrit_rien(o: Outils, poste: Poste, reglage):
    for cle, valeur in reglage.items():
        setattr(poste, cle, valeur)
    with pytest.raises(Arret, match="journal non écrit") as e:
        journal.ecrire(o, entree(o))
    assert e.value.code == Code.INTERNE
    assert not o.journal.exists()


def test_une_adresse_hors_bouclage_n_est_pas_ecrite(o: Outils):
    e = entree(o)
    e.notes.append("joint 192.168.1.20")
    with pytest.raises(Arret, match="192.168.1.20"):
        journal.ecrire(o, e)
    e.notes = ["joint 127.0.0.1"]
    journal.ecrire(o, e)


def test_le_hook_reel_refuse_un_journal_qui_porte_une_cle(o: Outils, tmp_path):
    relire = relecture_reelle(tmp_path)
    e = entree(o)
    assert relire(journal.rendre(e)) == []
    e.notes.append(f"OPENAI {ASPECT}")
    with pytest.raises(Arret, match="journal non écrit"):
        journal.ecrire(o, e, relire=relire)
    assert not o.journal.exists()


def test_la_relecture_impossible_est_un_refus(o: Outils):
    from livraison.outils import Sortie

    o.lanceur = lambda c: Sortie(2, "", "")
    assert journal.relecture_du_hook(o)("x") == [
        "relecture des clés impossible",
        "mention refusée par controler-texte",
    ]


def test_completer_ajoute_une_section_relue(o: Outils, poste: Poste):
    chemin = journal.ecrire(o, entree(o))
    journal.completer(o, chemin, "Arrêt — 2026-11-11", ["3 conteneurs avant, 0 après"])
    texte = chemin.read_text(encoding="utf-8")
    assert texte.endswith("## Arrêt — 2026-11-11\n\n- 3 conteneurs avant, 0 après\n")
    poste.refus_mention = True
    with pytest.raises(Arret):
        journal.completer(o, chemin, "x", ["y"])


def test_un_retour_porte_son_motif(o: Outils):
    e = entree(o)
    e.geste, e.motif, e.quittee = (
        "retour arrière",
        "répétition",
        "arbre à `e4-livraison`",
    )
    e.notes.append("Les migrations de la base ne sont pas revenues.")
    texte = journal.rendre(e)
    assert "| Motif | répétition |" in texte and "| Quitté |" in texte
    assert "## Notes" in texte
