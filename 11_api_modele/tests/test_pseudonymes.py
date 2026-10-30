"""La liste des pseudonymes en mémoire et la règle des homonymes (§ 7 bis).

Noms factices seulement : ceux de la base de test (`conftest.AUTEURS`) et ceux écrits
ici ; la liste réelle des homonymes admis est lue pour sa forme, jamais affichée.
"""

from __future__ import annotations

import logging

import psycopg
import pytest
from conftest import AUTEURS, BaseApi
from fastapi.testclient import TestClient

from api_modele import pseudonymes
from api_modele.application import creer_application
from api_modele.demarrage import DemarrageRefuse
from api_modele.journal import FormatJson
from api_modele.pseudonymes import Pseudonymes, empreinte

NOM = "Lecteurfactice"
AUTRE = "Membrefictif"


def liste(*admis: tuple[str, str]) -> Pseudonymes:
    return Pseudonymes([NOM, AUTRE], admis)


# --------------------------------------------------------------------------- #
#  La méthode du corpus v2, réutilisée
# --------------------------------------------------------------------------- #
def test_la_methode_est_celle_du_module_05_chargee_par_son_chemin():
    m = pseudonymes.methode()

    assert pseudonymes.METHODE.is_file()
    assert m.__file__ == str(pseudonymes.METHODE)
    assert empreinte(NOM) == m.empreinte(NOM)
    assert len(empreinte(NOM)) == 16


@pytest.mark.parametrize(
    ("texte", "fautifs"),
    [
        ("Rien à signaler dans cette réponse.", 0),
        (f"Selon {NOM}, c'est drôle.", 1),
        (f"{NOM.upper()} et {AUTRE.lower()} l'ont aimé.", 2),  # casse ignorée
        (f"Les {NOM}s et les pré{NOM} ne comptent pas.", 0),  # mot entier
    ],
)
def test_un_nom_compte_en_mot_entier_casse_ignoree(texte, fautifs):
    assert liste().fautifs(texte, []) == fautifs


# --------------------------------------------------------------------------- #
#  La règle des homonymes : admis seulement s'il vient d'un passage de la requête
# --------------------------------------------------------------------------- #
PASSAGE = ("kitsu:7", f"{NOM} est le héros de cette série.")


def test_un_homonyme_admis_present_dans_un_passage_envoye_est_accepte():
    admis = liste(("kitsu:7", empreinte(NOM)))

    assert admis.fautifs(f"Le héros, {NOM}, grandit.", [PASSAGE]) == 0


def test_le_meme_homonyme_absent_des_passages_de_la_requete_est_refuse():
    admis = liste(("kitsu:7", empreinte(NOM)))

    assert admis.fautifs(f"Le héros, {NOM}, grandit.", []) == 1
    autre_passage = ("kitsu:7", "Un passage sans le nom.")
    assert admis.fautifs(f"Le héros, {NOM}, grandit.", [autre_passage]) == 1


def test_un_nom_non_admis_present_dans_un_passage_est_refuse():
    # Le passage porte le nom, mais le couple (document, empreinte) n'est pas admis.
    assert liste().fautifs(f"Le héros, {NOM}, grandit.", [PASSAGE]) == 1
    ailleurs = liste(("kitsu:8", empreinte(NOM)))
    assert ailleurs.fautifs(f"Le héros, {NOM}, grandit.", [PASSAGE]) == 1


def test_les_empreintes_trouvees_pour_le_banc():
    # Le diagnostic du banc dit quels noms, par leur empreinte, jamais par leur nom ;
    # il compte ce que compte le contrôle de la route.
    noms = liste(("kitsu:7", empreinte(NOM)))
    texte = f"Selon {NOM} et {AUTRE.lower()}, c'est drôle."

    assert noms.empreintes_trouvees(texte) == [empreinte(NOM), empreinte(AUTRE)]
    assert noms.empreintes_trouvees("Rien à signaler.") == []
    assert noms.homonyme_admis(empreinte(NOM)) is True
    assert noms.homonyme_admis(empreinte(AUTRE)) is False
    assert noms.fautifs(texte, []) == len(noms.empreintes_trouvees(texte))


def test_seuls_des_nombres_sortent():
    noms = liste(("kitsu:7", empreinte(NOM)))

    assert repr(noms) == "Pseudonymes(noms=2, homonymes_admis=1)"
    assert (noms.nb_noms, noms.nb_admis) == (2, 1)
    assert NOM not in repr(noms) and AUTRE not in repr(noms)


def test_la_liste_versionnee_des_homonymes_se_lit():
    admis = pseudonymes.lire_homonymes()

    assert len(admis) == 155  # rapport de l'étape 1 : 155 occurrences admises
    assert all(len(h) == 16 for _, h in admis)


# --------------------------------------------------------------------------- #
#  Au démarrage
# --------------------------------------------------------------------------- #
def test_l_api_garde_la_liste_en_memoire_et_n_en_dit_que_des_nombres(
    reglages, service_doublure, caplog
):
    caplog.set_level(logging.INFO, logger="api_modele")
    application = creer_application(reglages, instance=service_doublure.instance)

    with TestClient(application) as client:
        etat = client.app.state.etat  # type: ignore[attr-defined]
        assert etat.pseudonymes.nb_noms == len(AUTEURS)
        assert etat.pseudonymes.nb_admis == 155

    lignes = "\n".join(FormatJson().format(r) for r in caplog.records)
    assert f'"pseudonymes": {len(AUTEURS)}' in lignes
    for auteur in AUTEURS:
        assert auteur not in lignes


def demarrer(reglages, instance, **options) -> None:
    with TestClient(creer_application(reglages, instance=instance, **options)):
        pass


def test_une_vue_vide_refuse_le_demarrage(
    base_api: BaseApi, reglages, service_doublure
):
    with psycopg.connect(base_api.dsn_admin) as admin:
        admin.execute("DELETE FROM manga.ms_reviews_all")

    with pytest.raises(DemarrageRefuse, match=r"manga\.v_pseudonymes est vide"):
        demarrer(reglages, service_doublure.instance)


def test_une_vue_illisible_refuse_le_demarrage_sans_contenu(
    base_api: BaseApi, reglages, service_doublure
):
    with psycopg.connect(base_api.dsn_admin) as admin:
        admin.execute("REVOKE SELECT ON manga.v_pseudonymes FROM modele_ro")

    with pytest.raises(DemarrageRefuse) as exc:
        demarrer(reglages, service_doublure.instance)

    assert str(exc.value) == (
        "manga.v_pseudonymes illisible sous le rôle de l'API (InsufficientPrivilege)"
    )


def test_une_liste_d_homonymes_illisible_refuse_le_demarrage(
    reglages, service_doublure, tmp_path
):
    fichier = tmp_path / "corpus_homonymes_admis.csv"
    fichier.write_text("colonne,inattendue\n", encoding="utf-8")

    with pytest.raises(DemarrageRefuse, match="homonymes admis illisible"):
        demarrer(reglages, service_doublure.instance, homonymes=fichier)

    with pytest.raises(DemarrageRefuse, match="homonymes admis illisible"):
        demarrer(reglages, service_doublure.instance, homonymes=tmp_path / "absente")
