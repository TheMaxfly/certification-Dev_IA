"""Le verrou entre reconnaissance et collecte, et la structure des rapports."""

from pathlib import Path

import pytest

from commun import rapport
from commun.collecteur import Collecteur, _enveloppe_ndjson, collecte
from commun.perimetre import Serie

_MINIMAL = {
    "environnement": {"cadence appliquee": "1.0 s"},
    "perimetre": {"series du perimetre": "1368"},
    "resultats": {"articles_traites": "0"},
    "ecarts": [],
}


def test_la_section_ce_que_je_n_ai_pas_pu_etablir_ne_peut_pas_etre_vide(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError):
        rapport.ecrire(
            tmp_path,
            source="wikipedia_fr",
            phase="reconnaissance",
            non_etabli=[],
            **_MINIMAL,
        )


def test_le_rapport_nait_en_attente_de_validation(tmp_path: Path) -> None:
    """L'outil ecrit le statut ; il ne se valide jamais lui-meme."""
    chemin = rapport.ecrire(
        tmp_path,
        source="wikipedia_fr",
        phase="reconnaissance",
        non_etabli=["taux d'extraction inconnu"],
        **_MINIMAL,
    )
    assert rapport.statut(chemin) == rapport.STATUT_ATTENTE
    assert "## 5. Ce que je n'ai pas pu etablir" in chemin.read_text(encoding="utf-8")


def test_l_horodatage_evite_l_ecrasement_de_deux_runs_du_meme_jour(
    tmp_path: Path,
) -> None:
    a = rapport.chemin_rapport(tmp_path, "anilist", "collecte", "20260911T120000Z")
    b = rapport.chemin_rapport(tmp_path, "anilist", "collecte", "20260911T173000Z")
    assert a != b


def test_sans_rapport_la_collecte_est_verrouillee(tmp_path: Path) -> None:
    with pytest.raises(rapport.ReconnaissanceNonValidee, match="aucun rapport"):
        rapport.exiger_reconnaissance_validee(tmp_path, "wikipedia_fr")


def test_un_rapport_non_valide_ne_deverrouille_pas(tmp_path: Path) -> None:
    rapport.ecrire(
        tmp_path,
        source="wikipedia_fr",
        phase="mesures",
        non_etabli=["inconnu"],
        **_MINIMAL,
    )
    with pytest.raises(rapport.ReconnaissanceNonValidee, match="non valide"):
        rapport.exiger_reconnaissance_validee(tmp_path, "wikipedia_fr")


def test_la_validation_manuelle_deverrouille(tmp_path: Path) -> None:
    chemin = rapport.ecrire(
        tmp_path,
        source="wikipedia_fr",
        phase="mesures",
        non_etabli=["inconnu"],
        **_MINIMAL,
    )
    chemin.write_text(
        chemin.read_text(encoding="utf-8").replace(
            f"Statut : {rapport.STATUT_ATTENTE}", f"Statut : {rapport.STATUT_VALIDE}"
        ),
        encoding="utf-8",
    )
    assert rapport.exiger_reconnaissance_validee(tmp_path, "wikipedia_fr") == chemin


def test_l_arret_entre_phases_est_structurel_pas_conventionnel(tmp_path: Path) -> None:
    """La collecte refuse de demarrer avant meme de toucher a la base."""
    faux = Collecteur(
        nom="wikipedia_fr",
        intervalle_minimal_s=1.0,
        motif_cadence="test",
        user_agent="test",
        recuperer=lambda serie, limiteur: {},
    )
    with pytest.raises(rapport.ReconnaissanceNonValidee):
        collecte(
            faux,
            dsn="host=/inexistant dbname=inexistant",
            racine=tmp_path,
            partition="2026-09",
            quand="20260911T120000Z",
        )


def test_chaque_enregistrement_porte_son_identifiant_catalogue() -> None:
    """Controle 3 du paragraphe 9 : rattachement complet, zero exception."""
    serie = Serie(series_id=4242, cle_source="Berserk", rang_popularite=7)
    ligne = _enveloppe_ndjson(serie, "wikipedia_fr", "20260911T120000Z", {"x": 1})
    assert ligne["series_id"] == 4242
    assert ligne["cle_source"] == "Berserk"
    assert ligne["rang_popularite"] == 7


def test_la_definition_verrouille_l_etape_de_mesures(tmp_path: Path) -> None:
    """Verrou du paragraphe 4.3 : definir avant de compter."""
    with pytest.raises(rapport.ReconnaissanceNonValidee, match="definition"):
        rapport.exiger_definition_validee(tmp_path, "wikipedia_fr")
