"""`livrer deployer --cible <nom>`, `livrer arreter --cible <nom>`, `livrer etat`.

L'ORDRE D'UN DÉPLOIEMENT : préalables (aucune écriture) → relevé avant → `up -d
--no-build` (les images sont celles de `construire`) → attente de `/sante` → santé →
journal. **Une santé rouge n'arrête pas la pile** : on la regarde ; le code de sortie
est 1, le journal dit le verdict, et la suite est un retour arrière proposé à Max.

UNE CIBLE À LA FOIS SUR LE POSTE (décision 3 du cadre) : staging et production partagent
la carte graphique, le conteneur `tei-api-modele` (nom fixe) et les sous-réseaux fixes
admis par `pg_hba.conf`. `deployer` refuse si l'autre cible tourne. Sur un serveur, deux
hôtes : la même commande, un autre `DOCKER_HOST`.

LA COMMANDE NE MIGRE JAMAIS : des migrations en attente sur la base de la cible sont un
refus (geste de Max, après sa sauvegarde).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from livraison import cibles, depot, images, journal, sante
from livraison.cibles import Cible
from livraison.codes import Arret, Code
from livraison.depot import Etiquette
from livraison.images import Image
from livraison.journal import Entree, Relecture
from livraison.outils import (
    Commande,
    Outils,
    commande_affichee,
    json_objet,
    lignes,
    liste,
    objet,
)

DELAI_S = 180.0
PAUSE_S = 3.0
PHRASE_UNE_CIBLE = (
    "une cible à la fois sur le poste : staging et production partagent la carte"
    " graphique, le conteneur tei-api-modele et les sous-réseaux de la base — la cible"
    " {autre} tourne ({n} conteneurs) ; arrêtez-la d'abord (livrer arreter --cible"
    " {autre})"
)
PHRASE_RETOUR = "proposer un retour arrière à Max (livrer retour), jamais sans lui"
PROGRAMME_METRIQUES = """
import json, urllib.request as u
try:
    r = u.urlopen("http://127.0.0.1:8010/metrics", timeout=5)
    statut, texte = r.status, r.read().decode()
except Exception as e:
    statut, texte = getattr(e, "code", 0), ""
somme = lambda nom: sum(float(l.rsplit(" ", 1)[1]) for l in texte.splitlines()
                        if l.startswith(nom + "{") or l.startswith(nom + " "))
print(json.dumps({"statut": statut, "appels": somme("api_modele_llm_appels_total"),
                  "cout": somme("api_modele_llm_cout_usd_total")}))
"""


@dataclass(frozen=True)
class Plan:
    """Ce qu'un déploiement lève : la cible, l'étiquette, ses images, ses fichiers."""

    cible: Cible
    etiquette: Etiquette
    images: list[Image]


# --------------------------------------------------------------------------- #
#  Les préalables
# --------------------------------------------------------------------------- #


def en_marche(o: Outils, projet: str) -> list[str]:
    s = o.lanceur(
        Commande(
            f"docker.ps.{projet}",
            (
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={projet}",
                "--format",
                "{{.Names}}",
            ),
            dossier=o.racine,
        )
    )
    if s.code != 0:
        raise Arret(Code.PREREQUIS, "docker ne répond pas", s.stderr)
    return lignes(s)


def exiger_autre_arretee(o: Outils, cible: Cible) -> None:
    autre = cibles.lire(cible.autre, cible.fichier_env.parent)
    tournent = en_marche(o, autre.projet)
    if tournent:
        raise Arret(
            Code.PREREQUIS, PHRASE_UNE_CIBLE.format(autre=autre.nom, n=len(tournent))
        )


def exiger_fichiers(o: Outils, cible: Cible) -> None:
    """Les fichiers d'environnement existent (jamais ouverts) ; pour le staging, sa clé
    de session et le fichier de mots de passe de la préproduction."""
    manquent = [f for f in cibles.ENV_MODULES if not (o.racine / f).is_file()]
    if cible.nom == "staging" and not (o.racine / cible.env_propre).is_file():
        manquent.append(
            f"{cible.env_propre} (la clé de session du staging, une ligne"
            " APPLICATION_CLE_SESSION=… : geste de Max)"
        )
    if manquent:
        raise Arret(Code.PREREQUIS, f"fichiers absents : {', '.join(manquent)}")
    if cible.nom == "staging":
        chemin = o.environ.get(cibles.VARIABLE_PGPASS_PREPROD, "")
        if not chemin or not (o.racine / chemin).is_file():
            raise Arret(
                Code.PREREQUIS,
                f"{cibles.VARIABLE_PGPASS_PREPROD} : le chemin du fichier de mots de"
                " passe de l'API pour la préproduction (celui de la chaîne) — absente,"
                " ou le fichier n'existe pas",
            )


def exiger_migrations_appliquees(o: Outils, cible: Cible) -> tuple[int, int]:
    appliquees, attente = sante.migrations(o, cible.base)
    if attente:
        raise Arret(
            Code.PREREQUIS,
            f"migrations en attente sur {cible.base} : {attente} ; appliquez-les"
            " d'abord (geste de Max, après sa sauvegarde) — livrer ne migre jamais",
        )
    return appliquees, attente


def texte_migrations(o: Outils, base: str) -> str:
    try:
        a, e = sante.migrations(o, base)
    except Arret as arret:
        return arret.motif
    return f"{a} appliquées, {e} en attente ({base})"


# --------------------------------------------------------------------------- #
#  Le compteur des appels facturés, l'attente
# --------------------------------------------------------------------------- #


def valeur_prometheus(o: Outils, requete: str) -> float | None:
    statut, corps = sante.lire_json(
        o, f"http://127.0.0.1:{sante.PORT_PROMETHEUS}/api/v1/query?query={requete}"
    )
    if statut != 200:
        return None
    resultat = liste(objet(corps.get("data")).get("result"))
    if not resultat:
        return 0.0
    valeur = liste(objet(resultat[0]).get("value"))
    return float(str(valeur[1])) if len(valeur) == 2 else None


def compteur(o: Outils, cible: Cible) -> str:
    """Les appels au modèle de langage et leur coût, comptés par l'API depuis son
    démarrage : `/metrics` lu dans son conteneur ; en production (le 13 restreint
    `/metrics` au réseau de collecte), par Prometheus."""
    if not en_marche(o, cible.projet):
        return "non relevé (pile arrêtée)"
    s = o.lanceur(
        Commande(
            "docker.exec.metriques",
            (
                "docker",
                "exec",
                f"{cible.projet}-api-1",
                "python",
                "-c",
                PROGRAMME_METRIQUES,
            ),
            dossier=o.racine,
        )
    )
    lu = json_objet(s.stdout) if s.code == 0 else {}
    if lu.get("statut") == 200:
        appels, cout = float(str(lu.get("appels"))), float(str(lu.get("cout")))
    elif cible.avec_monitorage:
        a = valeur_prometheus(o, "sum(api_modele_llm_appels_total)")
        c = valeur_prometheus(o, "sum(api_modele_llm_cout_usd_total)")
        if a is None or c is None:
            return "non relevé (Prometheus ne répond pas)"
        appels, cout = a, c
    else:
        return "non relevé (/metrics de l'API illisible)"
    return f"{appels:.0f} appels, {cout:.6f} $"


def attendre(o: Outils, url: str, delai: float = DELAI_S) -> None:
    debut = o.horloge()
    while True:
        statut, _ = o.http(url)
        if statut == 200:
            return
        if o.horloge() - debut > delai:
            raise Arret(
                Code.DEPLOIEMENT,
                f"{url.split('/', 3)[-1]} ne répond pas après {int(delai)} s",
            )
        o.dormir(PAUSE_S)


# --------------------------------------------------------------------------- #
#  Lever, vérifier, journaliser
# --------------------------------------------------------------------------- #


def lever_et_verifier(
    o: Outils,
    entree: Entree,
    cible: Cible,
    at: sante.Attendus,
    leves: list[Commande],
    relire: Relecture | None,
) -> Code:
    """Lance les commandes Compose, attend, vérifie, journalise — dans tous les cas.

    Deux durées au journal : « Pile prête », du lancement des commandes Compose à
    `/sante` 200 ; « Durée », le geste entier, santé et attente de Prometheus comprises.
    L'instant de référence de la santé est celui où `/sante` a répondu."""
    t0 = o.horloge()
    code = Code.OK
    try:
        for commande in leves:
            s = o.lanceur(commande)
            if s.code != 0:
                raise Arret(
                    Code.DEPLOIEMENT,
                    f"{commande.nom} : la pile ne se lève pas",
                    s.stderr,
                )
        attendre(o, f"http://127.0.0.1:{cible.port_application}/sante")
        entree.pile_prete_s = o.horloge() - t0
        depuis = o.maintenant()
        entree.controles = sante.verifier(
            o, cible, at, depuis=depuis, notes=entree.notes
        )
        if sante.verte(entree.controles):
            entree.verdict = "santé verte"
        else:
            entree.verdict = "santé rouge"
            entree.notes.append(f"La pile n'est pas arrêtée : {PHRASE_RETOUR}.")
            code = Code.SANTE_ROUGE
    except Arret as arret:
        entree.verdict = f"échec : {arret.motif}"
        entree.notes.append(f"La pile est laissée telle quelle : {PHRASE_RETOUR}.")
        code = arret.code
    entree.migrations_apres = texte_migrations(o, cible.base)
    entree.compteur_apres = compteur(o, cible)
    entree.duree_s = o.horloge() - t0
    chemin = journal.ecrire(o, entree, relire=relire)
    for ligne in sante.lignes_verdict(entree.controles):
        o.afficher(f"  {ligne}")
    o.afficher(f"{entree.verdict} — journal : 15_livraison/journal/{chemin.name}")
    if code != Code.OK:
        o.afficher(PHRASE_RETOUR)
    return code


def nouvelle_entree(
    o: Outils, cible: Cible, etiquette: Etiquette, geste: str
) -> Entree:
    return Entree(
        cible=cible.nom,
        geste=geste,
        etiquette=etiquette.nom,
        commit=etiquette.commit,
        debut=o.maintenant(),
        utilisateur=o.utilisateur(),
    )


def deployer(
    o: Outils,
    nom: str,
    *,
    a_blanc: bool = False,
    relire: Relecture | None = None,
) -> Code:
    cible = cibles.lire(nom)
    if a_blanc:
        return a_blanc_deployer(o, cible)
    etiquette = depot.exiger_etiquette(o)
    liste = images.images_de(o, etiquette.nom)
    exiger_autre_arretee(o, cible)
    exiger_fichiers(o, cible)
    presentes = images.presentes(o, liste)
    exiger_migrations_appliquees(o, cible)
    at = sante.attendus(o, etiquette.nom, cible)
    entree = nouvelle_entree(o, cible, etiquette, "déploiement")
    entree.images = images.avec_construction(o, etiquette.nom, presentes)
    entree.migrations_avant = texte_migrations(o, cible.base)
    entree.compteur_avant = compteur(o, cible)
    tournaient = en_marche(o, cible.projet)
    if tournaient:
        entree.notes.append(
            f"La cible tournait ({len(tournaient)} conteneurs) : servies avant —"
            f" {servies_texte(o, cible)}."
        )
    up = cibles.commande_compose(cible, o, "up", "-d", "--no-build", "--remove-orphans")
    o.afficher(f"Déploiement de {etiquette.nom} sur {cible.nom} ({cible.projet}).")
    return lever_et_verifier(o, entree, cible, at, [up], relire)


def servies_texte(o: Outils, cible: Cible) -> str:
    lues = sante.servies(o, cible.projet)
    return ", ".join(f"{s} {i}" for s, i in sorted(lues.items())) or "aucune"


def a_blanc_deployer(o: Outils, cible: Cible) -> Code:
    """Ce que `deployer` ferait : chaque préalable, son verdict ; les commandes, dans
    l'ordre. Rien n'est lancé que des lectures."""
    o.afficher(f"À blanc — déploiement sur {cible.nom} ({cible.projet}, {cible.base}).")
    reference = depot.etiquette_de_head(o) or "HEAD"
    verifs: list[tuple[str, Callable[[], object]]] = [
        ("étiquette", lambda: depot.exiger_etiquette(o)),
        ("autre cible arrêtée", lambda: exiger_autre_arretee(o, cible)),
        ("fichiers d'environnement", lambda: exiger_fichiers(o, cible)),
        (
            "images présentes",
            lambda: images.presentes(o, images.images_de(o, reference)),
        ),
        ("migrations appliquées", lambda: exiger_migrations_appliquees(o, cible)),
    ]
    for libelle, verif in verifs:
        try:
            verif()
            o.afficher(f"  préalable tenu : {libelle}")
        except Arret as arret:
            o.afficher(f"  refuserait : {libelle} — {arret.motif}")
    up = cibles.commande_compose(cible, o, "up", "-d", "--no-build", "--remove-orphans")
    o.afficher("Commandes :")
    o.afficher(f"  {commande_affichee(up, o.racine)}")
    o.afficher(
        f"  attente de http://127.0.0.1:{cible.port_application}/sante (≤ 180 s)"
    )
    o.afficher(f"  livrer sante --cible {cible.nom} ; journal 15_livraison/journal/")
    return Code.OK


def verifier_sante(o: Outils, nom: str) -> Code:
    """`livrer sante` : les contrôles, aux versions de l'étiquette de HEAD."""
    cible = cibles.lire(nom)
    reference = depot.etiquette_de_head(o) or "HEAD"
    controles = sante.verifier(o, cible, sante.attendus(o, reference, cible))
    for ligne in sante.lignes_verdict(controles):
        o.afficher(ligne)
    verte = sante.verte(controles)
    o.afficher(f"Santé {cible.nom} ({reference}) : {'verte' if verte else 'ROUGE'}.")
    return Code.OK if verte else Code.SANTE_ROUGE


def arreter(o: Outils, nom: str, *, relire: Relecture | None = None) -> Code:
    """`docker compose down` de la cible ; l'arrêt est ajouté à son dernier journal."""
    cible = cibles.lire(nom)
    avant = en_marche(o, cible.projet)
    t0 = o.horloge()
    s = o.lanceur(cibles.commande_compose(cible, o, "down", "--remove-orphans"))
    if s.code != 0:
        raise Arret(Code.DEPLOIEMENT, f"la cible {cible.nom} ne s'arrête pas", s.stderr)
    restent = en_marche(o, cible.projet)
    o.afficher(
        f"Cible {cible.nom} arrêtée : {len(avant)} conteneurs avant, {len(restent)}"
        " après."
    )
    dernier = journal.dernier(o, cible.nom)
    if dernier is not None:
        journal.completer(
            o,
            dernier,
            f"Arrêt — {o.maintenant().strftime('%Y-%m-%d %H:%M:%S')} UTC",
            [
                f"`livrer arreter --cible {cible.nom}`, par `{o.utilisateur()}`",
                f"{len(avant)} conteneurs avant, {len(restent)} après ;"
                f" {o.horloge() - t0:.0f} s",
            ],
            relire=relire,
        )
    return Code.OK if not restent else Code.DEPLOIEMENT


def etat(o: Outils) -> Code:
    """Les deux cibles : en marche ou non, les images servies, le dernier journal."""
    for nom in cibles.NOMS:
        cible = cibles.lire(nom)
        tournent = en_marche(o, cible.projet)
        dernier = journal.dernier(o, nom)
        o.afficher(
            f"{nom} ({cible.projet}) : "
            + (f"en marche, {len(tournent)} conteneurs" if tournent else "arrêtée")
        )
        if tournent:
            o.afficher(f"  servies : {servies_texte(o, cible)}")
        o.afficher(f"  dernier journal : {dernier.name if dernier else '—'}")
    return Code.OK
