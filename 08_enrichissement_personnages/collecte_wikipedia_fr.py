#!/usr/bin/env python3
"""Collecte des personnages depuis Wikipedia francais.

Acces par l'API MediaWiki (`https://fr.wikipedia.org/w/api.php`). Jamais de
collecte HTML, jamais de rendu de page.

Le perimetre se derive des sitelinks `frwiki` deja stockes : aucune recherche
par titre. Elargir aux series jamais appariees a un QID reintroduirait le
matching flou que le bloc 1 a elimine — et ici une erreur d'appariement
rattacherait les personnages d'une oeuvre a une autre, sans signal.

Trois etapes, deux verrous :

    reconnaissance --etape inventaire   observe les sections, sans les compter
    (verrou 4.3 : rediger et valider la definition a la main)
    reconnaissance --etape mesures      compte selon la definition validee
    (verrou 4.5 : valider le taux d'extraction a la main)
    collecte                            parcours complet du perimetre
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

import extraction_wikipedia_fr as xwf
from commun import echantillon as ech
from commun import rapport
from commun.collecteur import Collecteur, collecte
from commun.limiteur import Limiteur
from commun.mediawiki import (
    MAXLAG,
    POINT_ACCES,
    ClientMediaWiki,
    ReponseInattendue,
)
from commun.perimetre import DERIVATIONS, Serie, deriver

RACINE = Path(__file__).resolve().parent

#: Cadence. Requetes serielles, jamais paralleles, `maxlag` positionne : en cas
#: de depassement on attend et on reessaie plutot que forcer. Une seconde entre
#: requetes est la valeur prudente pour un acces anonyme a l'API MediaWiki.
CADENCE_S = 1.0


def _user_agent() -> str:
    """En-tete descriptif avec moyen de contact — exige par l'etiquette Wikimedia.

    Le contact vient de l'environnement : le depot est public, aucune adresse
    n'y est ecrite en dur.
    """
    contact = os.environ.get("CONTACT_COLLECTE", "").strip()
    if not contact:
        raise SystemExit(
            "CONTACT_COLLECTE non defini. L'etiquette Wikimedia exige un "
            "User-Agent descriptif portant un moyen de contact ; un agent "
            "generique ou absent est un motif de blocage legitime.\n"
            "  export CONTACT_COLLECTE="
            "'https://github.com/TheMaxfly/certification-Dev_IA'"
        )
    return f"certification-DevIA-enrichissement-personnages/0.1 ({contact})"


# ---------------------------------------------------------------------------
# Phase B — collecte complete.
#
# Trois decisions figees s'y lisent directement : chaque enregistrement porte
# l'identifiant catalogue (4), son `revid` (12), et les deux titres — celui du
# sitelink et celui effectivement servi (13).
# ---------------------------------------------------------------------------

#: Resolution des titres, faite une fois pour tout le perimetre.
_RESOLUS: dict[str, object] = {}
_CLIENT: ClientMediaWiki | None = None


def _client(limiteur: Limiteur) -> ClientMediaWiki:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = ClientMediaWiki(_user_agent(), limiteur=limiteur)
    return _CLIENT


def preparer(series: list[Serie], limiteur: Limiteur) -> None:
    """Resout tout le perimetre par lots de 50 — 28 requetes, pas 1 368."""
    client = _client(limiteur)
    _RESOLUS.clear()
    _RESOLUS.update(client.resoudre([s.cle_source for s in series]))


def _personnage_en_dict(p, forme: str) -> dict:
    return {
        "nom": p.nom,
        "description": p.description,
        "forme_japonaise": p.forme_japonaise,
        "position": p.position,
        "decrit": p.decrit,
        "provenance": forme,
    }


def recuperer(serie: Serie, limiteur: Limiteur) -> dict:
    """Collecte un article, et suit son renvoi quand il en porte un.

    Regle du paragraphe 54.6 : on retient **la plus riche des deux formes**,
    jamais l'une par principe. Les deux comptes sont conserves pour que
    l'arbitrage reste verifiable apres coup.
    """
    client = _client(limiteur)
    article = _RESOLUS.get(serie.cle_source)
    if article is None:
        article = client.resoudre([serie.cle_source])[serie.cle_source]
    if not article.existe:
        raise ReponseInattendue(article.motif_absence or "page absente")

    titre = article.titre_servi or serie.cle_source
    in_situ = xwf.analyser(client.wikitexte(titre))

    dedie = None
    revid_dedie = None
    titre_dedie = None
    if in_situ.article_dedie:
        titre_dedie = in_situ.article_dedie
        resolu = client.resoudre([titre_dedie])[titre_dedie]
        if resolu.existe:
            revid_dedie = resolu.revid
            titre_dedie = resolu.titre_servi or titre_dedie
            dedie = xwf.analyser_dedie(client.wikitexte(titre_dedie))

    retenue = "in_situ"
    analyse = in_situ
    if dedie is not None and len(dedie.decrits) > len(in_situ.decrits):
        retenue, analyse = "dedie", dedie

    personnages = [_personnage_en_dict(p, retenue) for p in analyse.personnages]
    return {
        "titre_sitelink": serie.cle_source,
        "titre_servi": titre,
        "redirige": article.redirige,
        "revid": article.revid,
        "sections_c1": in_situ.sections_c1,
        "renvoi": in_situ.article_dedie,
        "renvoi_titre_servi": titre_dedie,
        "renvoi_revid": revid_dedie,
        "renvoi_suivi": dedie is not None,
        "forme_retenue": retenue,
        "decrits_in_situ": len(in_situ.decrits),
        "decrits_dedie": len(dedie.decrits) if dedie is not None else None,
        "exploitable": analyse.exploitable,
        "prose": in_situ.prose,
        "motif_rejet": analyse.motif_rejet,
        "marqueurs_denouement": analyse.marqueurs_denouement,
        "personnages": personnages,
    }


#: Projection de la phase A, a confronter au taux reel (spec 8.5).
PROJECTION_PHASE_A = 45.0
MARGE_PHASE_A = 15.4


def mesurer(fichier: Path) -> dict[str, str]:
    """Les deux mesures exigees au rapport de collecte.

    La premiere est prevue par la spec : le taux reel remplace l'intervalle de
    la phase A. La seconde ne l'est pas — la part d'articles porteurs d'un
    renvoi sur tout le perimetre. Elle est gratuite, le wikitexte etant deja
    telecharge, et c'est elle qui rend le +26 % projetable ou non.
    """
    lignes = [
        json.loads(ligne)
        for ligne in fichier.read_text(encoding="utf-8").splitlines()
        if ligne.strip()
    ]
    if not lignes:
        return {}
    charges = [ligne["charge"] for ligne in lignes]
    n = len(charges)

    exploitables = [c for c in charges if c["exploitable"]]
    taux = 100 * len(exploitables) / n
    ecart = taux - PROJECTION_PHASE_A
    dans_marge = abs(ecart) <= MARGE_PHASE_A

    renvois = [c for c in charges if c["renvoi"]]
    suivis = [c for c in renvois if c["renvoi_suivi"]]
    retenus = [c for c in suivis if c["forme_retenue"] == "dedie"]
    gain = sum((c["decrits_dedie"] or 0) - c["decrits_in_situ"] for c in retenus)
    decrits_sans_renvoi = sum(c["decrits_in_situ"] for c in charges)
    decrits = sum(len([p for p in c["personnages"] if p["decrit"]]) for c in charges)
    cjk = sum(1 for c in charges for p in c["personnages"] if p["forme_japonaise"])
    isoles = sum(len(c["personnages"]) for c in charges)
    longueurs = sorted(
        len(p["description"]) for c in charges for p in c["personnages"] if p["decrit"]
    )
    proses = [c for c in charges if c["prose"] and not c["exploitable"]]
    denouement = [c for c in charges if c["marqueurs_denouement"]]

    return {
        "— TAUX REEL (exploitables)": f"{len(exploitables)}/{n} ({taux:.1f} %)",
        "— projection phase A": f"{PROJECTION_PHASE_A} % ± {MARGE_PHASE_A} pts",
        "— ecart a la projection": (
            f"{ecart:+.1f} pts — "
            f"{'DANS' if dans_marge else 'HORS'} l'intervalle de la phase A"
        ),
        "— prose continue": f"{len(proses)}/{n} ({100 * len(proses) / n:.1f} %)",
        "— PART D'ARTICLES A RENVOI": (
            f"{len(renvois)}/{n} ({100 * len(renvois) / n:.1f} %)"
        ),
        "— renvois effectivement suivis": f"{len(suivis)}",
        "— renvois ou la forme dediee l'emporte": f"{len(retenus)}",
        "— gain net du suivi des renvois": (
            f"{gain:+d} personnages decrits, soit "
            f"{100 * gain / decrits_sans_renvoi:+.1f} % du total in situ"
            if decrits_sans_renvoi
            else "—"
        ),
        "— personnages isoles / decrits": f"{isoles} / {decrits}",
        "— personnages par article exploitable": (
            f"{decrits / len(exploitables):.1f}" if exploitables else "—"
        ),
        "— graphie CJK": (
            f"{cjk}/{isoles} ({100 * cjk / isoles:.1f} %)" if isoles else "—"
        ),
        "— longueur min / mediane / max": (
            f"{longueurs[0]} / {longueurs[len(longueurs) // 2]} / {longueurs[-1]}"
            if longueurs
            else "—"
        ),
        "— articles a marqueur de denouement": (
            f"{len(denouement)}/{n} ({100 * len(denouement) / n:.1f} %)"
        ),
    }


COLLECTEUR = Collecteur(
    nom="wikipedia_fr",
    intervalle_minimal_s=CADENCE_S,
    motif_cadence=(
        f"acces anonyme a l'API MediaWiki, requetes serielles, maxlag={MAXLAG}"
    ),
    user_agent="(defini au lancement depuis CONTACT_COLLECTE)",
    recuperer=recuperer,
    preparer=preparer,
    mesurer=mesurer,
)


# ---------------------------------------------------------------------------
# Phase A, etape 1 : inventaire — observer avant de definir.
#
# Le paragraphe 4.3 demande de trancher « quels titres de section comptent —
# Personnages, Personnages principaux, Univers, autre chose ? ». Cette etape
# rend la distribution reelle des titres de section sur un echantillon
# stratifie, **sans compter une seule section exploitable**. Definir sans elle
# reviendrait a deviner.
# ---------------------------------------------------------------------------


def inventaire(dsn: str, *, par_strate: int = 20) -> Path:
    """Tire l'echantillon, resout les articles, releve leurs sections."""
    series = deriver(dsn, "wikipedia_fr")
    tirage = ech.tirer(series, par_strate=par_strate)
    client = ClientMediaWiki(_user_agent(), limiteur=Limiteur(CADENCE_S))

    articles = client.resoudre([s.cle_source for s in tirage.tout])
    strate_de = {s.cle_source: "tete" for s in tirage.tete}
    strate_de.update({s.cle_source: "queue" for s in tirage.queue})

    releves: list[dict] = []
    titres_de_section: Counter[str] = Counter()
    for serie in tirage.tout:
        article = articles[serie.cle_source]
        releve = {
            "series_id": serie.series_id,
            "rang_popularite": serie.rang_popularite,
            "strate": strate_de[serie.cle_source],
            "titre_sitelink": serie.cle_source,
            "titre_servi": article.titre_servi,
            "redirige": article.redirige,
            "existe": article.existe,
            "revid": article.revid,
            "motif_absence": article.motif_absence,
            "sections": [],
        }
        if article.existe:
            sections = client.sections(article.titre_servi or serie.cle_source)
            releve["sections"] = [
                {
                    "niveau": s.get("level"),
                    "titre": (s.get("line") or "").strip(),
                    "index": s.get("index"),
                }
                for s in sections
            ]
            for s in releve["sections"]:
                titres_de_section[s["titre"]] += 1
        releves.append(releve)

    quand = rapport.horodatage()
    brut = RACINE / "rapports" / f"wikipedia_fr_inventaire_{quand}.json"
    brut.parent.mkdir(parents=True, exist_ok=True)
    brut.write_text(
        json.dumps(
            {
                "graine": tirage.graine,
                "part_strate": tirage.part_strate,
                "taille_strate_tete": tirage.taille_strate_tete,
                "taille_strate_queue": tirage.taille_strate_queue,
                "releves": releves,
                "titres_de_section": titres_de_section.most_common(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    absents = [r for r in releves if not r["existe"]]
    rediriges = [r for r in releves if r["redirige"]]
    sans_revid = [r for r in releves if r["existe"] and not r["revid"]]
    cite_liste = {
        r["series_id"]
        for r in releves
        for s in r["sections"]
        if "liste des personnages" in s["titre"].lower()
    }

    return rapport.ecrire(
        RACINE / "rapports",
        source="wikipedia_fr",
        phase="inventaire",
        environnement={
            "point d'acces": POINT_ACCES,
            "user-agent": _user_agent(),
            "cadence": f"{CADENCE_S} s, requetes serielles, maxlag={MAXLAG}",
            "requetes emises": f"{client.requetes_emises}",
            "attentes maxlag": f"{client.attentes_maxlag}",
            "releve brut": brut.name,
        },
        perimetre={
            "colonne source": DERIVATIONS["wikipedia_fr"].colonne_source,
            "series du perimetre": f"{len(series)}",
            "graine du tirage": f"{tirage.graine}",
            "strates": (
                f"premier et dernier decile — {tirage.taille_strate_tete} series "
                f"chacune ; {par_strate} tirees dans chaque"
            ),
        },
        resultats={
            "articles sondes": f"{len(releves)}",
            "articles existants": f"{len(releves) - len(absents)}",
            "articles absents": f"{len(absents)}",
            "redirections resolues": f"{len(rediriges)}",
            "articles sans revid": f"{len(sans_revid)}",
            "titres de section distincts": f"{len(titres_de_section)}",
            "articles citant une liste dediee": f"{len(cite_liste)}",
        },
        ecarts=[f"{r['titre_sitelink']!r} : {r['motif_absence']}" for r in absents]
        + [
            f"{r['titre_sitelink']!r} redirige vers {r['titre_servi']!r}"
            for r in rediriges
        ],
        non_etabli=[
            "Proportion d'articles portant une section personnages EXPLOITABLE : "
            "non comptee, la definition n'etant pas validee (spec 4.3).",
            "Nombre de personnages par article, longueur des textes, presence "
            "d'une graphie CJK : mesures de l'etape suivante.",
            "Part des sections revelant le denouement : appreciation a porter "
            "sur le texte, non sur l'inventaire des titres.",
        ],
        quand=quand,
    )


def mesures(dsn: str) -> Path:
    """Phase A, etape 2 — verrouillee par la definition du paragraphe 4.3.

    Rejoue le meme echantillon que l'inventaire : meme graine, memes strates.
    Les taux sont donc comparables d'une etape a l'autre.
    """
    definition = rapport.exiger_definition_validee(RACINE / "rapports", "wikipedia_fr")
    series = deriver(dsn, "wikipedia_fr")
    tirage = ech.tirer(series)
    client = ClientMediaWiki(_user_agent(), limiteur=Limiteur(CADENCE_S))
    articles = client.resoudre([s.cle_source for s in tirage.tout])
    strate_de = {s.cle_source: "tete" for s in tirage.tete}
    strate_de.update({s.cle_source: "queue" for s in tirage.queue})

    releves: list[dict] = []
    for serie in tirage.tout:
        article = articles[serie.cle_source]
        titre = article.titre_servi or serie.cle_source
        analyse = xwf.analyser(client.wikitexte(titre))
        longueurs = [len(p.description) for p in analyse.decrits]
        releves.append(
            {
                "series_id": serie.series_id,
                "rang_popularite": serie.rang_popularite,
                "strate": strate_de[serie.cle_source],
                "titre_servi": titre,
                "revid": article.revid,
                "sections_c1": analyse.sections_c1,
                "article_dedie": analyse.article_dedie,
                "exploitable": analyse.exploitable,
                "prose": analyse.prose,
                "motif_rejet": analyse.motif_rejet,
                "personnages_isoles": len(analyse.personnages),
                "personnages_decrits": len(analyse.decrits),
                "personnages_avec_japonais": len(analyse.avec_japonais),
                "longueurs_description": sorted(longueurs),
                "marqueurs_denouement": analyse.marqueurs_denouement,
                "noms": [p.nom for p in analyse.personnages[:5]],
            }
        )

    quand = rapport.horodatage()
    brut = RACINE / "rapports" / f"wikipedia_fr_mesures_{quand}.json"
    brut.write_text(
        json.dumps({"releves": releves}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return _rapporter_mesures(releves, client, definition, brut, len(series), quand)


def _taux(part: int, total: int) -> str:
    return f"{part}/{total} ({100 * part / total:.1f} %)" if total else "—"


def _rapporter_mesures(
    releves: list[dict],
    client: ClientMediaWiki,
    definition: Path,
    brut: Path,
    taille_perimetre: int,
    quand: str,
) -> Path:
    """Trois taux, et une projection qui porte sa marge."""
    par_strate = {
        s: [r for r in releves if r["strate"] == s] for s in ("tete", "queue")
    }
    exploitables = [r for r in releves if r["exploitable"]]
    proses = [r for r in releves if r["prose"] and not r["exploitable"]]
    denouement = [r for r in releves if r["marqueurs_denouement"]]
    dedies = [r for r in releves if r["article_dedie"]]
    decrits = sum(r["personnages_decrits"] for r in releves)
    japonais = sum(r["personnages_avec_japonais"] for r in releves)
    isoles = sum(r["personnages_isoles"] for r in releves)
    toutes = sorted(x for r in releves for x in r["longueurs_description"])

    n = len(releves)
    p = len(exploitables) / n
    # Intervalle de Wald a 95 % — grossier a n=40, et c'est pourquoi il est dit.
    marge = 1.96 * (p * (1 - p) / n) ** 0.5

    resultats = {
        "articles mesures": f"{n}",
        "EXPLOITABLES (C1+C2+C3)": _taux(len(exploitables), n),
        "— en tete": _taux(
            sum(1 for r in par_strate["tete"] if r["exploitable"]),
            len(par_strate["tete"]),
        ),
        "— en queue": _taux(
            sum(1 for r in par_strate["queue"] if r["exploitable"]),
            len(par_strate["queue"]),
        ),
        "PROSE continue (parametre 4)": _taux(len(proses), n),
        "AUCUN des deux": _taux(n - len(exploitables) - len(proses), n),
        "articles renvoyant a un article dedie": _taux(len(dedies), n),
        "personnages isoles": f"{isoles}",
        "personnages decrits (>= 80 car.)": f"{decrits}",
        "personnages par article exploitable": (
            f"{decrits / len(exploitables):.1f}" if exploitables else "—"
        ),
        "personnages portant une graphie CJK": _taux(japonais, isoles),
        "longueur description min / mediane / max": (
            f"{toutes[0]} / {toutes[len(toutes) // 2]} / {toutes[-1]}"
            if toutes
            else "—"
        ),
        "articles a marqueur de denouement": _taux(len(denouement), n),
        "PROJECTION sur le perimetre": (
            f"{p * taille_perimetre:.0f} series exploitables sur "
            f"{taille_perimetre} — {100 * p:.1f} % ± {100 * marge:.1f} pts"
        ),
    }
    return rapport.ecrire(
        RACINE / "rapports",
        source="wikipedia_fr",
        phase="mesures",
        environnement={
            "definition appliquee": definition.name,
            "point d'acces": POINT_ACCES,
            "cadence": f"{CADENCE_S} s, maxlag={MAXLAG}",
            "requetes emises": f"{client.requetes_emises}",
            "attentes maxlag": f"{client.attentes_maxlag}",
            "releve brut": brut.name,
        },
        perimetre={
            "series du perimetre": f"{taille_perimetre}",
            "echantillon": f"{n}, stratifie, graine {ech.GRAINE}",
        },
        resultats=resultats,
        ecarts=[
            f"{r['titre_servi']} : {r['motif_rejet']}"
            for r in releves
            if r["motif_rejet"]
        ],
        non_etabli=[
            "La marge est un intervalle de Wald a 95 % sur n=40 : grossier, il "
            "indique un ordre de grandeur, pas une precision.",
            "Le taux de denouement repose sur des marqueurs lexicaux explicites : "
            "c'est une appreciation, elle sous-detecte une revelation formulee "
            "autrement et sur-detecte un emploi anodin de « trahit ».",
            "La qualite d'isolation des personnages n'est pas verifiee article "
            "par article : le parseur reconnait quatre formes frwiki courantes, "
            "une cinquieme lui echapperait sans signal.",
        ],
        quand=quand,
    )


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument(
        "--dsn", default=os.environ.get("APIMANGA_DSN"), help="DSN PostgreSQL"
    )
    sous = analyseur.add_subparsers(dest="mode", required=True)
    p_reco = sous.add_parser("reconnaissance", help="phase A")
    p_reco.add_argument(
        "--etape", choices=("inventaire", "mesures"), default="inventaire"
    )
    p_reco.add_argument("--par-strate", type=int, default=20)
    p_collecte = sous.add_parser("collecte", help="phase B (verrouillee)")
    p_collecte.add_argument("--partition", required=True, help="ex. 2026-09")
    p_collecte.add_argument("--limite-series", type=int, default=None)
    args = analyseur.parse_args(argv)

    if not args.dsn:
        print("APIMANGA_DSN non defini (ou --dsn absent).", file=sys.stderr)
        return 2

    if args.mode == "reconnaissance":
        if args.etape == "inventaire":
            chemin = inventaire(args.dsn, par_strate=args.par_strate)
            print(f"rapport ecrit : {chemin}")
            print(
                "Etape suivante verrouillee : rediger la definition de "
                "« section personnages exploitable », puis la valider."
            )
            return 0
        chemin = mesures(args.dsn)
        print(f"rapport ecrit : {chemin}")
        return 0

    chemin = collecte(
        COLLECTEUR,
        dsn=args.dsn,
        racine=RACINE,
        partition=args.partition,
        quand=rapport.horodatage(),
        limite_series=args.limite_series,
    )
    print(f"rapport ecrit : {chemin}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as erreur:  # sortie non nulle, et affichee
        print(f"ECHEC : {type(erreur).__name__}: {erreur}", file=sys.stderr)
        sys.exit(1)
