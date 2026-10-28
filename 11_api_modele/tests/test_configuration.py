"""La configuration : un seul lecteur, tout validé, un écart et l'API ne démarre pas."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

import pytest
from conftest import (
    CLE_APPLICATION,
    CLE_DEMONSTRATION,
    CLE_FOURNISSEUR,
    RACINE_MODULE,
)

from api_modele.configuration import (
    FICHIER_REGLAGES,
    ConfigurationInvalide,
    charger,
    lire_reglages_reponse,
)

DSN = "postgresql://modele_api@localhost:5432/apimanga"
VALIDE = {
    "API_MODELE_CLES": (
        f"application:{CLE_APPLICATION},demonstration:{CLE_DEMONSTRATION}"
    ),
    "API_MODELE_DSN": DSN,
    "MISTRAL_API_KEY": CLE_FOURNISSEUR,
}


def ecarts(environ: dict[str, str], fichier: Path = FICHIER_REGLAGES) -> list[str]:
    with pytest.raises(ConfigurationInvalide) as exc:
        charger(environ, fichier)
    return exc.value.ecarts


def test_le_fichier_versionne_donne_les_valeurs_du_contrat():
    r = charger(VALIDE)

    assert [c.nom for c in r.cles] == ["application", "demonstration"]
    assert (r.nombre_series.defaut, r.nombre_series.min, r.nombre_series.max) == (
        10,
        1,
        50,
    )
    assert (
        r.passages_par_serie.defaut,
        r.passages_par_serie.min,
        r.passages_par_serie.max,
    ) == (1, 1, 5)
    assert r.catalogue_seul is True
    assert r.longueur_question_max == 500
    assert r.corps_octets_max == 16384
    assert r.debit_par_minute == 60
    assert r.version == "v1"
    assert r.instance == "embeddinggemma"
    assert r.client_hote is None
    assert (r.hote, r.port) == ("127.0.0.1", 8010)
    assert r.journal_question is False
    assert r.documentation is False
    assert ipaddress.ip_address("127.0.0.1") in r.metriques_reseaux[0]
    assert r.pool_max <= 10, "sous la borne du rôle modele_api (CONNECTION LIMIT 10)"


def test_chaque_table_du_fichier_porte_son_motif():
    texte = FICHIER_REGLAGES.read_text(encoding="utf-8")
    tables = re.findall(r"^\[([a-z0-9_.-]+)\]$", texte, flags=re.MULTILINE)

    assert len(tables) >= 26  # dont les quatre profils, chacun sa table
    blocs = re.split(r"^\[[a-z0-9_.-]+\]$", texte, flags=re.MULTILINE)[1:]
    assert all(re.search(r'^motif = ".+"$', b, flags=re.MULTILINE) for b in blocs)


def test_les_secrets_ne_s_affichent_pas():
    r = charger(VALIDE)

    assert CLE_APPLICATION not in repr(r)
    assert DSN not in repr(r)
    assert CLE_FOURNISSEUR not in repr(r)


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [
        (None, "absente ou vide"),
        ("", "absente ou vide"),
        ("application", "sans « nom:secret »"),
        (f"Application:{CLE_APPLICATION}", "nom invalide"),
        ("application:court", "moins de 32 caractères"),
        (f"application:{CLE_APPLICATION},application:{CLE_DEMONSTRATION}", "en double"),
        (
            f"application:{CLE_APPLICATION},demonstration:{CLE_APPLICATION}",
            "même secret",
        ),
    ],
)
def test_le_trousseau_est_a_defaillance_fermee(valeur, attendu):
    environ = dict(VALIDE)
    if valeur is None:
        del environ["API_MODELE_CLES"]
    else:
        environ["API_MODELE_CLES"] = valeur

    assert any(attendu in e for e in ecarts(environ))


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [
        (None, "absente"),
        ("postgresql://modele_api:secret@localhost/apimanga", "~/.pgpass"),
        ("host=localhost port", "illisible"),
    ],
)
def test_le_dsn_est_exige_et_sans_mot_de_passe(valeur, attendu):
    environ = dict(VALIDE)
    if valeur is None:
        del environ["API_MODELE_DSN"]
    else:
        environ["API_MODELE_DSN"] = valeur

    assert any(attendu in e for e in ecarts(environ))


@pytest.mark.parametrize(
    ("nom", "valeur", "attendu"),
    [
        ("API_MODELE_VERSION", "v2", "n'est pas servie"),
        ("API_MODELE_DEBIT_PAR_MINUTE", "soixante", "entier attendu"),
        ("API_MODELE_DEBIT_PAR_MINUTE", "0", "entre 1"),
        ("API_MODELE_METRIQUES_RESEAUX", "10.0.0.0/33", "réseau illisible"),
        ("API_MODELE_JOURNAL_QUESTION", "peut-être", "vrai ou faux"),
        ("API_MODELE_DOCUMENTATION", "oui-non", "vrai ou faux"),
        ("CLIENT_HOTE", "hote;rm", "hôte invalide"),
        ("API_MODELE_PORT", "70000", "entre 1 et 65535"),
        ("API_MODELE_PROFIL_ACTIF", "inconnu", "pas un profil déclaré"),
        ("COUT_PLAFOND_RUN", "un demi", "nombre attendu"),
        ("COUT_PLAFOND_RUN", "0", "nombre > 0"),
        ("API_MODELE_VERIFIER_MODELES", "parfois", "vrai ou faux"),
    ],
)
def test_une_surcharge_fautive_est_refusee(nom, valeur, attendu):
    assert any(attendu in e for e in ecarts(VALIDE | {nom: valeur}))


def test_les_surcharges_d_exploitation_sont_lues():
    r = charger(
        VALIDE
        | {
            "API_MODELE_DEBIT_PAR_MINUTE": "120",
            "API_MODELE_METRIQUES_RESEAUX": "10.0.0.0/8, 127.0.0.1/32",
            "API_MODELE_JOURNAL_QUESTION": "vrai",
            "API_MODELE_DOCUMENTATION": "vrai",
            "CLIENT_HOTE": "tei.interne",
            "API_MODELE_HOTE": "0.0.0.0",
            "API_MODELE_PORT": "9010",
        }
    )

    assert r.debit_par_minute == 120
    assert len(r.metriques_reseaux) == 2
    assert r.journal_question is True
    assert r.documentation is True
    assert r.client_hote == "tei.interne"
    assert (r.hote, r.port) == ("0.0.0.0", 9010)


def test_tous_les_ecarts_sont_nommes_ensemble():
    trouves = ecarts({"API_MODELE_VERSION": "v9"})

    assert len(trouves) == 4  # clés, DSN, version, clé du profil actif


def ecrire(tmp_path: Path, remplacement: tuple[str, str]) -> Path:
    texte = FICHIER_REGLAGES.read_text(encoding="utf-8")
    assert texte.count(remplacement[0]) == 1
    fichier = tmp_path / "reglages.toml"
    fichier.write_text(texte.replace(*remplacement), encoding="utf-8")
    return fichier


@pytest.mark.parametrize(
    ("remplacement", "attendu"),
    [
        (("defaut = 10\n", "defaut = 51\n"), "hors de [1, 50]"),
        (("defaut = 10\n", "defaut = true\n"), "entier ≥ 1"),
        (
            (
                "[question]\nlongueur_max = 500\nmotif",
                "[question]\nlongueur_max = 500\nnote",
            ),
            "sans motif",
        ),
        (("[corps]\n", "[corps_absent]\n"), "table [corps] absente"),
        (("min = 1\nmax = 5\nattente_s", "min = 6\nmax = 5\nattente_s"), "min > max"),
        (("service_s = 10.0", "service_s = 0"), "nombre > 0"),
        (
            (
                "[recherche.catalogue_seul]\ndefaut = true\n",
                '[recherche.catalogue_seul]\ndefaut = "oui"\n',
            ),
            "vrai ou faux",
        ),
        (('instance = "embeddinggemma"', 'instance = ""'), "texte non vide"),
        (('reseaux = ["127.0.0.1/32", "::1/128"]', "reseaux = []"), "aucun réseau"),
        (
            (
                "defaut = 0.0\nmin = 0.0\nmax = 1.0",
                "defaut = 2.0\nmin = 0.0\nmax = 1.0",
            ),
            "défaut 2.0 hors de",
        ),
        (
            (
                'defaut = "strict"\nvaleurs = ["strict"]',
                'defaut = "enrichi"\nvaleurs = ["strict"]',
            ),
            "défaut hors des valeurs",
        ),
        (
            ("nombre_series = 5\n", "nombre_series = 60\n"),
            "nombre_series 60 hors de [1, 50]",
        ),
        (
            ('profil_actif = "mistral-large-2512"', 'profil_actif = "absent"'),
            "pas un profil déclaré",
        ),
    ],
)
def test_un_fichier_de_reglages_fautif_est_refuse(tmp_path, remplacement, attendu):
    assert any(attendu in e for e in ecarts(VALIDE, ecrire(tmp_path, remplacement)))


def test_un_fichier_illisible_est_refuse(tmp_path):
    fichier = tmp_path / "reglages.toml"
    fichier.write_text("[recherche\n", encoding="utf-8")

    assert "illisible" in ecarts(VALIDE, fichier)[0]


def test_le_fichier_lu_est_celui_du_module():
    assert FICHIER_REGLAGES == RACINE_MODULE / "config" / "reglages.toml"


# --------------------------------------------------------------------------- #
#  Le modèle de langage : profils, disponibilité, profil actif
# --------------------------------------------------------------------------- #
def test_le_fichier_versionne_declare_les_quatre_profils_et_la_route():
    ml = charger(VALIDE).modele_langage

    assert ml.profil_actif == "mistral-large-2512"
    assert [p.nom for p in ml.profils] == [
        "mistral-large-2512",
        "mistral-large-4",
        "gpt-6-luna",
        "gpt-6-sol",
    ]
    actif = ml.profil("mistral-large-2512")
    assert actif is not None and actif.disponible
    assert (actif.fournisseur, actif.variable_cle, actif.statut) == (
        "mistral",
        "MISTRAL_API_KEY",
        "stable",
    )
    assert (actif.top_p, actif.reasoning_effort, actif.reglages_absents) == (
        1.0,
        None,
        (),
    )
    assert actif.prix is not None and (actif.prix.entree, actif.prix.sortie) == (
        0.5,
        1.5,
    )
    sol = ml.profil("gpt-6-sol")
    assert sol is not None
    assert (sol.reasoning_effort, sol.delai_s, sol.disponible) == ("none", 45.0, False)
    apercu = ml.profil("mistral-large-4")
    assert apercu is not None and apercu.statut == "apercu" and apercu.disponible
    r = ml.reponse
    assert (r.temperature.defaut, r.temperature.min, r.temperature.max) == (0, 0, 1)
    assert (r.longueur_max.defaut, r.longueur_max.min, r.longueur_max.max) == (
        400,
        50,
        1000,
    )
    assert (r.mode_defaut, r.modes) == ("strict", ("strict",))
    assert (
        r.historique_tours_max,
        r.historique_caracteres_max,
        r.historique_tour_caracteres_max,
    ) == (10, 4000, 2000)
    assert r.reformuler is True
    assert (r.nombre_series.defaut, r.nombre_series.min, r.nombre_series.max) == (
        5,
        1,
        50,
    )
    assert (r.passages_par_serie.defaut, r.passages_par_serie.max) == (2, 5)
    assert r.catalogue_seul is True
    assert (ml.caracteres_par_jeton, ml.plafond_run_usd, ml.connexion_s) == (
        3.8,
        0.5,
        5.0,
    )
    assert ml.verifier_modeles is False


def test_un_profil_sans_cle_est_indisponible_sans_etre_fatal():
    ml = charger(VALIDE).modele_langage

    assert [p.nom for p in ml.profils if not p.disponible] == [
        "gpt-6-luna",
        "gpt-6-sol",
    ]
    avec = charger(VALIDE | {"OPENAI_API_KEY": "factice-openai"}).modele_langage
    assert all(p.disponible for p in avec.profils)


@pytest.mark.parametrize("valeur", [None, "", "   "])
def test_le_profil_actif_sans_cle_refuse_le_demarrage_en_nommant_la_variable(valeur):
    environ = dict(VALIDE)
    if valeur is None:
        del environ["MISTRAL_API_KEY"]
    else:
        environ["MISTRAL_API_KEY"] = valeur

    assert ecarts(environ) == [
        "profil actif « mistral-large-2512 » : variable MISTRAL_API_KEY absente ou"
        " vide — la clé du profil actif est exigée"
    ]


def test_le_profil_actif_se_change_par_la_seule_configuration():
    environ = {k: v for k, v in VALIDE.items() if k != "MISTRAL_API_KEY"}
    ml = charger(
        environ
        | {"API_MODELE_PROFIL_ACTIF": "gpt-6-luna", "OPENAI_API_KEY": "factice-openai"}
    ).modele_langage

    assert ml.profil_actif == "gpt-6-luna"
    assert [p.nom for p in ml.profils if not p.disponible] == [
        "mistral-large-2512",
        "mistral-large-4",
    ]


def test_les_surcharges_du_modele_de_langage_sont_lues():
    ml = charger(
        VALIDE | {"COUT_PLAFOND_RUN": "0.8", "API_MODELE_VERIFIER_MODELES": "1"}
    ).modele_langage

    assert (ml.plafond_run_usd, ml.verifier_modeles) == (0.8, True)


def test_un_prix_absent_est_permis_et_un_delai_absent_prend_celui_par_defaut(
    tmp_path,
):
    texte = FICHIER_REGLAGES.read_text(encoding="utf-8")
    bloc = "prix_usd_par_million = { entree = 2.00, sortie = 10.00 }\ndelai_s = 45.0\n"
    assert texte.count(bloc) == 1
    fichier = tmp_path / "reglages.toml"
    fichier.write_text(texte.replace(bloc, ""), encoding="utf-8")

    sol = charger(VALIDE, fichier).modele_langage.profil("gpt-6-sol")

    assert sol is not None
    assert (sol.prix, sol.delai_s) == (None, 30.0)


def test_aucun_profil_declare_est_refuse(tmp_path):
    texte = FICHIER_REGLAGES.read_text(encoding="utf-8")
    debut = texte.index("[modele_langage.profils.")
    fichier = tmp_path / "reglages.toml"
    fichier.write_text(texte[:debut], encoding="utf-8")

    trouves = ecarts(VALIDE, fichier)

    assert any("aucun profil déclaré" in e for e in trouves)
    assert any("pas un profil déclaré" in e for e in trouves)


def test_un_profil_qui_n_est_pas_une_table_est_refuse(tmp_path):
    texte = FICHIER_REGLAGES.read_text(encoding="utf-8")
    debut = texte.index("[modele_langage.profils.")
    fichier = tmp_path / "reglages.toml"
    fichier.write_text(
        texte[:debut] + '[modele_langage.profils]\nmotif = "x"\n' + texte[debut:],
        encoding="utf-8",
    )

    assert any("motif : une table" in e for e in ecarts(VALIDE, fichier))


def ecrire_profil(tmp_path: Path, profil: str, avant: str, apres: str) -> Path:
    """Le fichier versionné, `avant` remplacé par `apres` dans la table d'un profil."""
    texte = FICHIER_REGLAGES.read_text(encoding="utf-8")
    entete = f"[modele_langage.profils.{profil}]\n"
    debut = texte.index(entete)
    suite = texte.find("\n[", debut + len(entete))
    fin = len(texte) if suite < 0 else suite
    bloc = texte[debut:fin]
    assert bloc.count(avant) == 1, (profil, avant)
    fichier = tmp_path / "reglages.toml"
    fichier.write_text(
        texte[:debut] + bloc.replace(avant, apres) + texte[fin:], encoding="utf-8"
    )
    return fichier


@pytest.mark.parametrize(
    ("profil", "avant", "apres", "attendu"),
    [
        (
            "gpt-6-luna",
            'fournisseur = "openai"',
            'fournisseur = "autre"',
            "fournisseur : l'un de mistral, openai",
        ),
        ("gpt-6-luna", "https://api.openai", "http://api.openai", "https exigé"),
        (
            "mistral-large-4",
            "/v1/chat/completions",
            "/v1/completions",
            "/v1/chat/completions attendu",
        ),
        (
            "mistral-large-4",
            'variable_cle = "MISTRAL_API_KEY"',
            'variable_cle = "mistral key"',
            "variable_cle : un nom de variable",
        ),
        (
            "gpt-6-sol",
            "reglages_absents = []",
            'reglages_absents = ["top_p"]',
            "top_p n'est pas un réglage exposé",
        ),
        (
            "gpt-6-sol",
            "reglages_absents = []",
            'reglages_absents = "temperature"',
            "reglages_absents : liste de textes",
        ),
        (
            "mistral-large-4",
            'statut = "apercu"',
            'statut = "beta"',
            "statut : l'un de stable, apercu",
        ),
        ("gpt-6-sol", "delai_s = 45.0", "delai = 45.0", "champs inconnus : delai"),
        (
            "gpt-6-sol",
            'reasoning_effort = "none"',
            'reasoning_effort = "extreme"',
            "reasoning_effort : l'un de",
        ),
        (
            "mistral-large-2512",
            "top_p = 1.0",
            "top_p = 1.5",
            "top_p : entre 0 et 1",
        ),
        (
            "mistral-large-2512",
            "top_p = 1.0",
            "top_k = 1",
            "reglages_fixes : inconnus top_k",
        ),
        (
            "mistral-large-2512",
            "reglages_fixes = { top_p = 1.0 }",
            'reglages_fixes = "aucun"',
            "reglages_fixes : une table",
        ),
        (
            "mistral-large-2512",
            "prix_usd_par_million = { entree = 0.50, sortie = 1.50 }",
            'prix_usd_par_million = "gratuit"',
            "prix_usd_par_million : une table",
        ),
        (
            "mistral-large-2512",
            "entree = 0.50",
            "entree = -1",
            "entree : nombre ≥ 0",
        ),
        (
            "gpt-6-sol",
            "[modele_langage.profils.gpt-6-sol]",
            "[modele_langage.profils.GPT_6]",
            "nom de profil invalide",
        ),
    ],
)
def test_un_profil_fautif_est_refuse(tmp_path, profil, avant, apres, attendu):
    fichier = ecrire_profil(tmp_path, profil, avant, apres)

    assert any(attendu in e for e in ecarts(VALIDE, fichier))


def test_un_profil_avec_temperature_absente_est_admis(tmp_path):
    fichier = ecrire_profil(
        tmp_path,
        "gpt-6-luna",
        "reglages_absents = []",
        'reglages_absents = ["temperature"]',
    )

    luna = charger(VALIDE, fichier).modele_langage.profil("gpt-6-luna")

    assert luna is not None and luna.reglages_absents == ("temperature",)


def test_les_reglages_de_la_reponse_se_lisent_sans_l_environnement(tmp_path):
    """Les formes de `/v1/reponse` en dérivent à l'import : un fichier fautif les
    refuse, comme la configuration complète."""
    assert lire_reglages_reponse().nombre_series.defaut == 5
    fichier = ecrire(tmp_path, ("nombre_series = 5\n", "nombre_series = 60\n"))

    with pytest.raises(ConfigurationInvalide, match="nombre_series 60"):
        lire_reglages_reponse(fichier)
