"""La cible, les mots de passe, la copie de la préproduction, les commandes réelles."""

from __future__ import annotations

import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import Factice, empreintes_de

from chaine import environnement
from chaine.codes import Arret, Code
from chaine.environnement import (
    BASE_PREPROD,
    DOSSIER_PRODUCTION,
    VARIABLE_DOSSIER,
    Cible,
    Commande,
    Sortie,
)

PREPROD = Cible(BASE_PREPROD, Path("/tmp/preprod"))
PROD = "échantillons de la production"


# --------------------------------------------------------------------------- #
#  La cible
# --------------------------------------------------------------------------- #


def test_la_cible_par_defaut_est_la_preproduction(tmp_path: Path):
    c = environnement.cible({VARIABLE_DOSSIER: str(tmp_path / "preprod")})
    assert c.base == BASE_PREPROD and c.dossier_export == (tmp_path / "preprod")
    assert c.dsn("verif_lecture") == (
        "postgresql://verif_lecture@localhost:5432/apimanga_preprod"
    )
    assert c.env_ecriture()["DATABASE_URL"].endswith("/apimanga_preprod")


@pytest.mark.parametrize(
    "environ, motif",
    [
        ({"CHAINE_BASE": "apimanga"}, "la base apimanga est la production"),
        ({"CHAINE_BASE": "apimanga_essai"}, "ne vise que la préproduction"),
        ({}, "n'est pas posée"),
        ({VARIABLE_DOSSIER: str(DOSSIER_PRODUCTION)}, PROD),
        ({VARIABLE_DOSSIER: str(DOSSIER_PRODUCTION / "x")}, PROD),
        ({VARIABLE_DOSSIER: f"{DOSSIER_PRODUCTION}/../equivalence"}, PROD),
    ],
)
def test_la_production_et_son_dossier_sont_refuses(environ, motif):
    if "CHAINE_BASE" in environ:
        environ = environ | {VARIABLE_DOSSIER: "/tmp/preprod"}
    with pytest.raises(Arret) as e:
        environnement.cible(environ)
    assert e.value.code == Code.PREREQUIS and motif in e.value.motif


def test_un_chemin_s_affiche_relatif_au_depot():
    assert environnement.chemin_lisible(DOSSIER_PRODUCTION) == (
        "09_service_embedding/mesures/equivalence"
    )
    assert (
        environnement.chemin_lisible(Path("/ailleurs/fichier.json")) == "fichier.json"
    )


# --------------------------------------------------------------------------- #
#  Les mots de passe — vérifiés par une connexion, jamais en ouvrant ~/.pgpass
# --------------------------------------------------------------------------- #

# Les messages réels, relevés le 7 novembre (01 bis), en anglais et dans l'enveloppe
# française de psql.
PAS_DE_MOT_DE_PASSE = (
    'psql: error: connection to server at "localhost" (127.0.0.1), port 5432 failed:'
    " fe_sendauth: no password supplied"
)
PAS_DE_MOT_DE_PASSE_FR = (
    "psql: error: la connexion au serveur sur « localhost » (127.0.0.1), port 5432 a"
    " échoué : fe_sendauth: no password supplied"
)
BASE_ABSENTE = (
    "psql: error: la connexion au serveur sur « localhost » (127.0.0.1), port 5432 a"
    ' échoué : FATAL:  database "apimanga_preprod" does not exist'
)


@pytest.mark.parametrize(
    "sortie, etat",
    [
        (Sortie(0, "1\n"), "ok"),
        (Sortie(2, "", PAS_DE_MOT_DE_PASSE), "mot_de_passe_absent"),
        (Sortie(2, "", PAS_DE_MOT_DE_PASSE_FR), "mot_de_passe_absent"),
        (Sortie(2, "", BASE_ABSENTE), "base_absente"),
        (Sortie(2, "", "could not connect to server: Connection refused"), "autre"),
    ],
)
def test_la_lecture_des_messages_de_connexion(sortie, etat):
    assert environnement.connexion(sortie) == etat


def connexions(**etats: Sortie) -> dict[str, Sortie]:
    base = Sortie(2, "", BASE_ABSENTE)
    r = {
        "psql.connexion.postgres.postgres": Sortie(0, "1\n"),
        f"psql.connexion.postgres.{BASE_PREPROD}": base,
        f"psql.connexion.verif_lecture.{BASE_PREPROD}": base,
        f"psql.connexion.modele_api.{BASE_PREPROD}": base,
    }
    return r | {k.replace("__", "."): v for k, v in etats.items()}


def test_les_quatre_mots_de_passe_trouves_ne_rendent_aucune_ligne():
    assert environnement.verifier_mots_de_passe(Factice(connexions()), PREPROD) == []


def test_un_mot_de_passe_absent_rend_la_forme_de_la_ligne_sans_secret():
    lanceur = Factice(
        connexions(
            **{
                "psql.connexion.verif_lecture__apimanga_preprod": Sortie(
                    2, "", PAS_DE_MOT_DE_PASSE
                )
            }
        )
    )
    manquent = environnement.verifier_mots_de_passe(lanceur, PREPROD)
    assert manquent == [
        "localhost:5432:apimanga_preprod:verif_lecture:"
        "<mot de passe du rôle verif_lecture>"
    ]
    for c in lanceur.lancees:
        assert c.env["PGOPTIONS"] == environnement.LECTURE_SEULE
        assert "-w" in c.argv and ".pgpass" not in " ".join(c.argv)


def test_une_connexion_impossible_rend_20():
    lanceur = Factice(
        connexions(**{"psql.connexion.postgres__postgres": Sortie(2, "", "timeout")})
    )
    with pytest.raises(Arret) as e:
        environnement.verifier_mots_de_passe(lanceur, PREPROD)
    assert e.value.code == Code.PREREQUIS


# --------------------------------------------------------------------------- #
#  La copie
# --------------------------------------------------------------------------- #


def etat_conforme() -> dict[str, Sortie]:
    ref = {c[1]: c[2] for c in environnement.reference() if c[0] == "-"}
    return {
        "psql.empreintes.v1": empreintes_de("v1"),
        "psql.empreintes.v2": empreintes_de("v2"),
        "psql.encodage_en_service": Sortie(0, ref["encodage_en_service"] + "\n"),
        "psql.promotions": Sortie(0, ref["promotions"] + "\n"),
        "psql.eval_runs": Sortie(0, ref["eval_runs"] + "\n"),
        "psql.migrations": Sortie(0, ref["migrations"] + "\n"),
        "psql.taille": Sortie(0, "2325 MB\n"),
        "pg_dump": Sortie(0),
        "dropdb": Sortie(0),
        "createdb": Sortie(0),
        "pg_restore": Sortie(0),
    }


class Temporaire:
    def __init__(self, racine: Path) -> None:
        self.dossier = tempfile.TemporaryDirectory(dir=racine)
        self.chemin = Path(self.dossier.name)

    def __call__(self) -> tempfile.TemporaryDirectory[str]:
        return self.dossier


def test_la_copie_lit_la_production_et_ne_recree_que_la_preproduction(tmp_path: Path):
    lanceur = Factice(connexions() | etat_conforme())
    tmp = Temporaire(tmp_path)
    d = environnement.creer_preproduction(lanceur, PREPROD, dossier_temporaire=tmp)
    assert d["base"] == BASE_PREPROD and d["taille"] == "2325 MB"
    noms = lanceur.noms()
    copie = [n for n in noms if n in ("pg_dump", "dropdb", "createdb", "pg_restore")]
    assert copie == ["pg_dump", "dropdb", "createdb", "pg_restore"]
    assert noms.index("pg_restore") < noms.index("psql.empreintes.v1")
    dump = lanceur.une("pg_dump").argv
    assert dump[dump.index("-d") + 1] == "apimanga", "la copie lit la production"
    for nom in ("dropdb", "createdb"):
        assert lanceur.une(nom).argv[-1] == BASE_PREPROD
    restore = lanceur.une("pg_restore").argv
    assert restore[restore.index("-d") + 1] == BASE_PREPROD
    assert "--exit-on-error" in restore
    for c in lanceur.lancees:
        if c.nom in ("dropdb", "createdb", "pg_restore"):
            assert "apimanga" not in [a for a in c.argv if a != BASE_PREPROD], c.nom
    lectures = [
        c.argv[1] for c in lanceur.lancees if c.nom.startswith("psql.empreintes")
    ]
    assert sum(a.endswith("/apimanga_preprod") for a in lectures) == 2
    assert sum(a.endswith("/apimanga") for a in lectures) == 2, "production relue"
    assert all("verif_lecture@" in a for a in lectures)
    assert not tmp.chemin.exists(), "le fichier temporaire est supprimé"


def test_la_copie_refuse_sans_les_mots_de_passe():
    lanceur = Factice(
        connexions(
            **{"psql.connexion.postgres__postgres": Sortie(2, "", PAS_DE_MOT_DE_PASSE)}
        )
    )
    with pytest.raises(Arret) as e:
        environnement.creer_preproduction(lanceur, PREPROD)
    assert e.value.code == Code.PREREQUIS
    assert "localhost:5432:postgres:postgres:<mot de passe" in e.value.motif
    assert "pg_dump" not in lanceur.noms()


def test_la_copie_refuse_une_autre_base():
    with pytest.raises(Arret):
        environnement.creer_preproduction(
            Factice({}), Cible("apimanga", Path("/tmp/x"))
        )


def test_une_commande_de_la_copie_en_echec_rend_20(tmp_path: Path):
    lanceur = Factice(
        connexions() | etat_conforme() | {"pg_restore": Sortie(1, "", "e")}
    )
    with pytest.raises(Arret) as e:
        environnement.creer_preproduction(
            lanceur, PREPROD, dossier_temporaire=Temporaire(tmp_path)
        )
    assert e.value.code == Code.PREREQUIS and "pg_restore" in e.value.motif


def test_une_copie_qui_differe_de_la_reference_rend_20(tmp_path: Path):
    def selon_la_base(commande: Commande) -> Sortie:
        return Sortie(0, "33\n" if commande.argv[1].endswith("_preprod") else "32\n")

    lanceur = Factice(
        connexions() | etat_conforme() | {"psql.eval_runs": selon_la_base}
    )
    with pytest.raises(Arret) as e:
        environnement.creer_preproduction(
            lanceur, PREPROD, dossier_temporaire=Temporaire(tmp_path)
        )
    assert "copie diffère" in e.value.motif and "eval_runs" in e.value.sortie


def test_une_production_qui_a_bouge_arrete_tout(tmp_path: Path):
    def selon_la_base(commande: Commande) -> Sortie:
        return Sortie(0, "3\n" if commande.argv[1].endswith("_preprod") else "2\n")

    lanceur = Factice(
        connexions() | etat_conforme() | {"psql.encodage_en_service": selon_la_base}
    )
    with pytest.raises(Arret) as e:
        environnement.creer_preproduction(
            lanceur, PREPROD, dossier_temporaire=Temporaire(tmp_path)
        )
    assert "PRODUCTION" in e.value.motif


def test_les_ecarts_a_la_reference_reelle():
    lanceur = Factice(etat_conforme())
    constate = environnement.etat(lanceur, PREPROD.dsn("verif_lecture"))
    assert (
        environnement.ecarts_a_la_reference(constate, environnement.reference()) == []
    )
    v2 = list(constate["v2"])
    v2[0] = ["docs", "1", "x"]
    constate["v2"] = v2
    assert environnement.ecarts_a_la_reference(constate, environnement.reference())


def test_des_empreintes_illisibles_rendent_30():
    with pytest.raises(Arret) as e:
        environnement.empreintes(
            Factice({"psql.empreintes.v2": Sortie(3)}), "dsn", "v2"
        )
    assert e.value.code == Code.DONNEES


def test_une_lecture_impossible_rend_20():
    with pytest.raises(Arret) as e:
        environnement.valeur(
            Factice({"psql.x": Sortie(2)}), "psql.x", "dsn", "SELECT 1"
        )
    assert e.value.code == Code.PREREQUIS


# --------------------------------------------------------------------------- #
#  Le dépôt et la pile
# --------------------------------------------------------------------------- #


def test_un_fichier_pousse_rend_son_commit():
    lanceur = Factice(
        {
            "git.suivi": Sortie(0),
            "git.inchange": Sortie(0),
            "git.commit-fichier": Sortie(0, "abc123\n"),
            "git.fichier-pousse": Sortie(0, "  origin/x\n"),
        }
    )
    fichier = environnement.RACINE_MODULE / "candidats" / "refus_bgem3_v2.toml"
    assert environnement.pousse(lanceur, fichier) == "abc123"
    lanceur.reponses["git.fichier-pousse"] = Sortie(0, "")
    with pytest.raises(Arret):
        environnement.pousse(lanceur, fichier)


def test_docker_qui_ne_repond_pas_rend_20():
    with pytest.raises(Arret):
        environnement.pile_en_marche(Factice({"docker.pile": Sortie(1, "", "daemon")}))


# --------------------------------------------------------------------------- #
#  Les commandes réelles, sans service
# --------------------------------------------------------------------------- #


def test_le_vrai_lanceur_lance_sans_shell_dans_un_environnement_vide():
    s = environnement.lancer_reel(
        Commande(
            "essai",
            (sys.executable, "-c", "import os; print(os.environ.get('ESSAI', '-'))"),
            env={"ESSAI": "transmise"},
        )
    )
    assert s == Sortie(0, "transmise\n", "")


class Reponse(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — l'API de la bibliothèque standard
        statut = 200 if self.path == "/info" and self.headers.get("X-Essai") else 404
        self.send_response(statut)
        self.end_headers()
        self.wfile.write(b'{"ok": true}' if statut == 200 else b"")

    def log_message(self, *args: object) -> None:
        pass


def test_le_vrai_bouclage_http():
    serveur = ThreadingHTTPServer(("127.0.0.1", 0), Reponse)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    try:
        url = f"http://127.0.0.1:{serveur.server_address[1]}"
        assert environnement.http_reel(f"{url}/info", {"X-Essai": "1"}) == (
            200,
            '{"ok": true}',
        )
        assert environnement.http_reel(f"{url}/autre", {})[0] == 404
    finally:
        serveur.shutdown()
        serveur.server_close()
    statut, raison = environnement.http_reel(f"{url}/info", {})
    assert statut == 0 and raison
    with pytest.raises(Arret):
        environnement.http_reel("http://exemple.org/", {})
