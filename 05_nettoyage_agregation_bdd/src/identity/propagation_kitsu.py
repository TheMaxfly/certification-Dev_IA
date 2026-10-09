"""Propagation du kitsu_id par identifiant — l'étage qui manquait au bloc 1.

    uv run python -m identity.propagation_kitsu --dry-run   # tout, puis ROLLBACK
    uv run python -m identity.propagation_kitsu             # exécute et commit

LE DÉFAUT (établi le 2026-09-30). L'étage 1 identifie une série par Wikidata
et en remplit wikidata_qid, mal_id, anilist_id — jamais kitsu_id ; l'étage 2
saute les séries déjà décidées. One Piece, Naruto, Death Note, Monster étaient
identifiées, avec leurs identifiants externes, et sans kitsu_id. La cascade
avait prouvé sa précision, jamais son rappel là où l'échec est le plus visible.

LA RÈGLE — déterministe, pures jointures d'identifiants, décrite dans
`sql/propagation_kitsu.sql`. L'égalité de titre n'y entre pas : ce module la
mesure (définition de `evaluation.catalogue`) et la rapporte comme
confirmation, jamais pour décider.

POINTS D'ARRÊT — le rapport est toujours écrit, la transaction annulée :
  A  un identifiant mène à plusieurs entrées Kitsu : la série est EXCLUE et
     LISTÉE, on ne choisit pas (décision du 2026-09-30, Chronos Ruler) ;
  B  une entrée déduite est déjà rattachée à une autre série, ou déduite par
     deux séries : conflit d'identité → ARRÊT ;
  C  un canari ne se rattache pas à son entrée → ARRÊT avant toute écriture.
Après écriture, en transaction : unicité dans les deux sens, aucun kitsu_id
déjà renseigné modifié, canaris en place — sinon ARRÊT.

Journal append-only (méthode `kitsu_propagation`, migration 018). Idempotent :
une série rattachée n'est plus candidate, le rejeu n'écrit rien.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

from evaluation.atteignabilite import atteignables, corpus_lu
from evaluation.catalogue import Catalogue

SQL = Path(__file__).resolve().parent / "sql"
CLASSEMENT = SQL / "propagation_kitsu.sql"
ECRITURE = SQL / "propagation_kitsu_ecriture.sql"
MODULE = Path(__file__).resolve().parents[2]
RAPPORTS = MODULE / "rapports"

#: Les cas les plus faciles : s'ils ne se rattachent pas, la règle a un trou.
#: series_id → kitsu_id attendu.
CANARIS = {736: 38, 745: 35, 2027: 57, 746: 4}
NOMS_CANARIS = {736: "One Piece", 745: "Naruto", 2027: "Death Note", 746: "Monster"}

CAS = (
    "rattachable",
    "sans_chemin",
    "conflit_plusieurs_entrees",
    "hors_type",
    "conflit_deja_rattachee",
    "conflit_entree_partagee",
)
CAS_ARRET = ("conflit_deja_rattachee", "conflit_entree_partagee")

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurPropagation(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurPropagation(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'"
        )
    return url


def dsn_affichable(url: str) -> str:
    infos = psycopg.conninfo.conninfo_to_dict(url)
    infos.pop("password", None)
    return " ".join(f"{k}={v}" for k, v in sorted(infos.items()))


def verifier_prerequis(cur) -> None:
    """Le schéma doit préexister, lu dans `pg_catalog` : cet étage ne crée
    aucune structure, et sa méthode doit être au contrat (018)."""
    manquants = []
    for objet in (
        "manga.work_identity",
        "manga.match_decision",
        "manga.v_match_current",
        "manga.kitsu_mappings",
        "manga.kitsu_meta",
        "manga.kitsu_formes",
        "manga.ms_series_enriched",
        "manga.ms_formes",
        "bench.corpus_docs",
        "bench.corpus_chunks",
    ):
        cur.execute("SELECT to_regclass(%s)", (objet,))
        if cur.fetchone()[0] is None:
            manquants.append(objet)
    if manquants:
        raise ErreurPropagation(
            "Schéma incomplet — STOP. Absents : " + ", ".join(manquants)
        )
    cur.execute(
        "SELECT pg_catalog.pg_get_constraintdef(c.oid) "
        "FROM pg_catalog.pg_constraint c "
        "WHERE c.conrelid = 'manga.match_decision'::regclass "
        "AND c.conname = 'match_decision_method_check'"
    )
    ligne = cur.fetchone()
    if ligne is None or "'kitsu_propagation'" not in ligne[0]:
        raise ErreurPropagation(
            "La méthode 'kitsu_propagation' n'est pas au CHECK de match_decision "
            "— migration 018 non appliquée. STOP, aucune migration n'est faite ici."
        )
    cur.execute(
        "SELECT 1 FROM pg_catalog.pg_attribute "
        "WHERE attrelid = 'manga.match_decision'::regclass "
        "AND attname = 'details' AND NOT attisdropped"
    )
    if cur.fetchone() is None:
        raise ErreurPropagation("match_decision.details est absente (009). STOP.")


# --------------------------------------------------------------------------- #
#  Mesures
# --------------------------------------------------------------------------- #


def empreinte_moyeu(cur) -> tuple[str, int, int]:
    """(md5 de l'identité, décisions au journal, dernier decision_id) : le rejeu
    se prouve par l'égalité de ces trois valeurs avant et après."""
    cur.execute(
        "SELECT md5(coalesce(string_agg(concat_ws('|', series_id, wikidata_qid, "
        "kitsu_id, mal_id, anilist_id), ',' ORDER BY work_uid), '')) "
        "FROM manga.work_identity"
    )
    md5 = cur.fetchone()[0]
    cur.execute(
        "SELECT count(*), coalesce(max(decision_id), 0) FROM manga.match_decision"
    )
    n, dernier = cur.fetchone()
    return md5, n, dernier


def compter_cas(cur) -> dict[str, int]:
    cur.execute("SELECT cas, count(*) FROM propagation_serie GROUP BY cas")
    comptes = dict.fromkeys(CAS, 0)
    comptes.update(dict(cur.fetchall()))
    return comptes


def repartition(cur, cas: str) -> dict[str, int]:
    cur.execute(
        "SELECT methode_source, count(*) FROM propagation_serie WHERE cas = %s "
        "GROUP BY 1 ORDER BY 1",
        (cas,),
    )
    return dict(cur.fetchall())


def lister_conflits(cur, cas: tuple[str, ...]) -> list[tuple]:
    """Une ligne par (série, entrée atteinte), avec ce qu'il faut pour juger."""
    cur.execute(
        "SELECT p.cas, p.series_id, s.series_title, p.methode_source, p.mal_id, "
        "       p.anilist_id, e.kitsu_id, m.subtype, "
        "       (SELECT min(f.forme) FROM manga.kitsu_formes f "
        "        WHERE f.kitsu_id = e.kitsu_id AND f.forme_type = 'canonical'), "
        "       (SELECT min(w.series_id) FROM manga.work_identity w "
        "        WHERE w.kitsu_id = e.kitsu_id::text) "
        "FROM propagation_serie p "
        "CROSS JOIN LATERAL unnest(p.entrees) AS e(kitsu_id) "
        "JOIN manga.ms_series_enriched s ON s.series_id = p.series_id "
        "LEFT JOIN manga.kitsu_meta m ON m.kitsu_id = e.kitsu_id "
        "WHERE p.cas = ANY(%s) ORDER BY p.cas, p.series_id, e.kitsu_id",
        (list(cas),),
    )
    return cur.fetchall()


def verifier_canaris(cur, canaris: dict[int, int]) -> list[str]:
    """Point C : chaque canari a déjà son entrée, ou va la recevoir."""
    echecs = []
    for series_id, attendu in canaris.items():
        cur.execute(
            "SELECT kitsu_id FROM manga.work_identity WHERE series_id = %s",
            (series_id,),
        )
        ligne = cur.fetchone()
        if ligne and ligne[0] == str(attendu):
            continue
        cur.execute(
            "SELECT cas, kitsu_id FROM propagation_serie WHERE series_id = %s",
            (series_id,),
        )
        classe = cur.fetchone()
        if classe != ("rattachable", attendu):
            echecs.append(
                f"{NOMS_CANARIS.get(series_id, 'série')} ({series_id}) → attendu "
                f"{attendu}, "
                f"kitsu_id actuel {ligne[0] if ligne else 'série absente'}, "
                f"classement {classe}"
            )
    return echecs


def controles_apres(cur) -> dict[str, int]:
    """Après écriture, dans la transaction : tout doit valoir zéro."""

    def scalaire(sql: str) -> int:
        cur.execute(sql)
        return cur.fetchone()[0]

    return {
        "kitsu_id sur deux séries": scalaire(
            "SELECT count(*) FROM (SELECT kitsu_id FROM manga.work_identity "
            "WHERE kitsu_id IS NOT NULL GROUP BY kitsu_id HAVING count(*) > 1) d"
        ),
        "série sur deux lignes d'identité": scalaire(
            "SELECT count(*) FROM (SELECT series_id FROM manga.work_identity "
            "WHERE series_id IS NOT NULL GROUP BY series_id HAVING count(*) > 1) d"
        ),
        "kitsu_id déjà renseignés modifiés": scalaire(
            "SELECT count(*) FROM propagation_avant a "
            "JOIN manga.work_identity w ON w.series_id = a.series_id "
            "WHERE w.kitsu_id IS DISTINCT FROM a.kitsu_id"
        ),
        "rattachements sans décision": scalaire(
            "SELECT count(*) FROM propagation_serie p WHERE p.cas = 'rattachable' "
            "AND NOT EXISTS (SELECT 1 FROM manga.v_match_current v "
            "  WHERE v.series_id = p.series_id AND v.method = 'kitsu_propagation' "
            "  AND v.wikidata_qid IS NOT DISTINCT FROM p.wikidata_qid)"
        ),
    }


@dataclass
class Titres:
    """Ce que dit l'égalité de titre — mesurée, jamais utilisée pour décider.

    Définition : un titre Kitsu de l'entrée (canonique, variantes, abrégés)
    égale, après `normaliser()`, le titre ou un alias d'une série du catalogue
    — la définition de `evaluation.catalogue`, celle de `confirmer`.
    """

    non_rattachees: int = 0
    a_titre_egal: int = 0
    doublons: int = 0
    manques: int = 0
    series_manquees: int = 0
    manquees_rattachees: int = 0
    manquees_conflit: list[int] = field(default_factory=list)
    hors_perimetre: list[tuple[int, str, list[int]]] = field(default_factory=list)
    confirmes: int = 0
    confirmes_base_corpus: int = 0
    conflits_vers_l_entree: int = 0
    doublons_touches: int = 0


def mesurer_titres(cx: psycopg.Connection, cur) -> Titres:
    """Sur l'état d'AVANT écriture. La base « entrées non rattachées » est celle
    du §63.5 : les synopsis Kitsu du corpus `bench` sans série rattachée."""
    catalogue = Catalogue.charger(cx)
    formes: dict[int, set[str]] = {}
    for kitsu_id, forme_norm in cx.execute(
        "SELECT kitsu_id, forme_norm FROM manga.kitsu_formes"
    ):
        formes.setdefault(kitsu_id, set()).add(forme_norm)

    def series_a_titre_egal(kitsu_id: int) -> set[int]:
        trouvees: set[int] = set()
        for forme in formes.get(kitsu_id, ()):
            trouvees |= set(catalogue._formes.get(forme, {}))
        return trouvees

    vides = {
        s
        for (s,) in cx.execute(
            "SELECT series_id FROM manga.work_identity "
            "WHERE series_id IS NOT NULL AND kitsu_id IS NULL"
        )
    }
    cur.execute("SELECT series_id, cas, kitsu_id, entrees FROM propagation_serie")
    lignes = cur.fetchall()
    classement = {s: (cas, k) for s, cas, k, _ in lignes}
    entrees = {s: e for s, _, _, e in lignes}
    rattachables = {s: k for s, (cas, k) in classement.items() if cas == "rattachable"}

    non_rattachees = [
        k
        for (k,) in cx.execute(
            "SELECT DISTINCT d.kitsu_id::bigint FROM bench.corpus_docs d "
            "WHERE d.corpus_id = %(corpus)s AND d.source = 'kitsu_synopsis' "
            "AND NOT EXISTS (SELECT 1 FROM manga.work_identity w "
            "WHERE w.kitsu_id = d.kitsu_id::text)",
            {"corpus": corpus_lu(cx)},
        )
    ]
    egal = {k: series_a_titre_egal(k) for k in non_rattachees}
    egal = {k: s for k, s in egal.items() if s}
    doublons = {k for k, s in egal.items() if not s & vides}
    manques = {k: s & vides for k, s in egal.items() if s & vides}
    manquees = set().union(*manques.values()) if manques else set()

    t = Titres(
        non_rattachees=len(non_rattachees),
        a_titre_egal=len(egal),
        doublons=len(doublons),
        manques=len(manques),
        series_manquees=len(manquees),
    )
    t.manquees_rattachees = len(manquees & rattachables.keys())
    t.manquees_conflit = sorted(
        s
        for s in manquees - rattachables.keys()
        if classement.get(s, ("",))[0].startswith("conflit")
    )
    titres = {s: serie.titre for s, serie in catalogue.series.items()}
    entrees_par_serie: dict[int, list[int]] = {}
    for k, ss in manques.items():
        for s in ss:
            entrees_par_serie.setdefault(s, []).append(k)
    t.hors_perimetre = sorted(
        (s, titres.get(s, "?"), sorted(entrees_par_serie[s]))
        for s in manquees - rattachables.keys() - set(t.manquees_conflit)
    )
    t.confirmes = sum(s in series_a_titre_egal(k) for s, k in rattachables.items())
    t.confirmes_base_corpus = sum(
        rattachables.get(s) == k for k, ss in manques.items() for s in ss
    )
    t.conflits_vers_l_entree = sum(
        any(s in manques.get(k, ()) for k in entrees.get(s, ()))
        for s in t.manquees_conflit
    )
    t.doublons_touches = len(doublons & set(rattachables.values()))
    return t


def environnement(cur, url: str) -> dict[str, str]:
    cur.execute("SELECT version(), current_database(), current_user")
    version, base, utilisateur = cur.fetchone()
    cur.execute("SELECT count(*), max(version) FROM public.schema_migrations")
    n_migrations, derniere = cur.fetchone()
    try:
        tete = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=MODULE,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        sale = subprocess.run(
            ["git", "status", "--porcelain", "--", "src", "../database"],
            cwd=MODULE,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        git = tete + (" + modifications non commitées" if sale else "")
    except (OSError, subprocess.CalledProcessError):
        git = "inconnu"
    return {
        "Serveur": version.split(" on ")[0],
        "Connexion": f"`{dsn_affichable(url)}`",
        "Base / rôle": f"`{base}` / `{utilisateur}`",
        "Migrations appliquées": f"{n_migrations} (dernière : {derniere})",
        "Python / psycopg": f"{platform.python_version()} / {psycopg.__version__}",
        "Code": f"`{git}`",
    }


# --------------------------------------------------------------------------- #
#  Rapport
# --------------------------------------------------------------------------- #


def n(x: int) -> str:
    return f"{x:_}".replace("_", " ")


def ecrire_rapport(chemin: Path, r: dict) -> None:
    t: Titres = r["titres"]
    cas = r["cas"]
    L = [
        f"# Propagation du kitsu_id par identifiant — {r['mode']}",
        "",
        f"Horodatage : `{r['horodatage']}` · durée {r['duree']:.1f} s · "
        f"**{r['issue']}**",
        "",
        "## Environnement",
        "",
        *[f"- {k} : {v}" for k, v in r["environnement"].items()],
        "",
        "## La règle",
        "",
        "Pures jointures d'identifiants (`src/identity/sql/propagation_kitsu.sql`). "
        "Une série est rattachée si, cumulativement : elle porte un MAL ou un "
        "AniList issu d'une décision antérieure de la cascade, automatique ou "
        "arbitrée ; les correspondances Kitsu mènent de ces identifiants à "
        "**exactement une** entrée ; cette entrée est manga, manhwa ou manhua ; "
        "elle n'est rattachée à aucune autre série ; le `kitsu_id` de la série "
        "est vide. **L'égalité de titre n'est pas une condition** : elle est "
        "mesurée ci-dessous comme confirmation.",
        "",
        "## Chiffres de contrôle",
        "",
        "| # | Contrôle | Attendu | Mesuré |",
        "|---|---|---|---:|",
        f"| 1 | séries candidates (kitsu_id vide, identifiant externe présent) "
        f"| ≥ 1 168 au premier run | {n(r['candidates'])} |",
        f"| 2 | rattachements {'effectués' if r['commit'] else 'simulés'} "
        f"| à produire | **{n(r['ecrites'])}** |",
        f"| 3 | dont confirmés par égalité de titre | ≈ 1 073, mesuré "
        f"| {n(t.confirmes)} |",
        "| 4 | canaris One Piece → 38, Naruto → 35, Death Note → 57, Monster → 4 "
        f"| rattachés | {'✅ les quatre' if not r['canaris'] else '❌'} |",
        f"| 5 | doublons légitimes touchés | 0 | {t.doublons_touches} |",
        "| 6 | `kitsu_id` déjà renseignés modifiés | 0 | "
        f"{r['apres']['kitsu_id déjà renseignés modifiés']} |",
        f"| 7 | atteignabilité | ~8 718 au premier run | "
        f"{n(r['atteignables_avant'])} → **{n(r['atteignables_apres'])}** "
        f"({r['atteignables_apres'] / r['catalogue'] * 100:.1f} %)".replace(".", ",")
        + " |",
        "",
        "Répartition des rattachements par décision source : "
        + (", ".join(f"`{m}` {n(c)}" for m, c in r["par_source"].items()) or "aucun")
        + ". Types d'entrée : "
        + (", ".join(f"{k} {n(c)}" for k, c in r["par_type"].items()) or "—")
        + ".",
        "",
        "**N° 3, la définition écrite.** Une entrée déduite est confirmée si l'un "
        "de ses titres Kitsu (canonique, variantes, abrégés) égale, après "
        "`normaliser()`, le titre ou un alias de la série — la définition de "
        "`evaluation.catalogue`, celle de `confirmer`. Le « 1 073 » du §63.5 "
        "comptait la même égalité sur une base plus étroite, les seules entrées "
        "du corpus non rattachées : sur cette base, "
        f"{n(t.confirmes_base_corpus)} rattachements, plus "
        f"{t.conflits_vers_l_entree} série(s) en conflit dont l'identifiant mène "
        "aussi à l'entrée, sans y mener seul. Même mesure, base plus large ; pas "
        "un écart de règle.",
        "",
        "## Classement des candidates",
        "",
        "| Cas | Séries |",
        "|---|---:|",
        *[f"| `{c}` | {n(cas[c])} |" for c in CAS],
        f"| **total** | **{n(sum(cas.values()))}** |",
        "",
        "`sans_chemin` par décision source : "
        + (", ".join(f"`{m}` {n(c)}" for m, c in r["sans_chemin"].items()) or "aucune")
        + ".",
        "",
        "## Conflits",
        "",
        "**Point A — un identifiant mène à plusieurs entrées Kitsu.** Exclues et "
        "listées : on ne choisit pas (décision du 2026-09-30).",
        "",
        *tableau_conflits(
            [c for c in r["conflits"] if c[0] == "conflit_plusieurs_entrees"]
        ),
        "",
        "**Point B — entrée déjà rattachée à une autre série, ou déduite par deux "
        "séries.** Arrêt s'il y en a.",
        "",
        *tableau_conflits([c for c in r["conflits"] if c[0] in CAS_ARRET]),
        "",
        "## Contrôles après écriture (dans la transaction)",
        "",
        "| Contrôle | Valeur |",
        "|---|---:|",
        *[f"| {k} | {v} |" for k, v in r["apres"].items()],
        "",
        "| Moyeu | Avant | Après |",
        "|---|---|---|",
        f"| empreinte md5 de `work_identity` | `{r['empreinte_avant'][0]}` | "
        f"`{r['empreinte_apres'][0]}` |",
        f"| décisions au journal | {n(r['empreinte_avant'][1])} | "
        f"{n(r['empreinte_apres'][1])} |",
        f"| dernier `decision_id` | {r['empreinte_avant'][2]} | "
        f"{r['empreinte_apres'][2]} |",
        "",
        "## Ce que dit l'égalité de titre (§63.5, sur l'état d'avant)",
        "",
        "| | |",
        "|---|---:|",
        f"| entrées Kitsu du corpus non rattachées | {n(t.non_rattachees)} |",
        f"| dont à titre égal à une série du catalogue | {n(t.a_titre_egal)} |",
        f"| — doublons (toutes les séries à titre égal ont déjà un kitsu_id) "
        f"| {n(t.doublons)} |",
        f"| — rattachements manqués | {n(t.manques)} |",
        f"| séries concernées par un rattachement manqué | {n(t.series_manquees)} |",
        f"| — rattachées par cet étage | {n(t.manquees_rattachees)} |",
        f"| — en conflit (points A, B) | {len(t.manquees_conflit)} |",
        f"| — **hors périmètre : aucun chemin par identifiant** "
        f"| **{n(len(t.hors_perimetre))}** |",
        "",
        "## Hors périmètre — titre égal, aucun chemin par identifiant",
        "",
        f"**{n(len(t.hors_perimetre))} séries.** Cet étage ne rattache jamais par "
        "le titre : ces séries restent à instruire (étage 2). Entrées Kitsu non "
        "rattachées dont un titre égale celui de la série :",
        "",
        "| series_id | Titre | Entrées Kitsu à titre égal |",
        "|---:|---|---|",
        *[
            f"| {s} | {titre.replace('|', '/')} | {', '.join(map(str, ks))} |"
            for s, titre, ks in t.hors_perimetre
        ],
        "",
    ]
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("\n".join(L), encoding="utf-8")


def tableau_conflits(lignes: list[tuple]) -> list[str]:
    if not lignes:
        return ["Aucun."]
    return [
        "| series_id | Titre | Décision source | MAL | AniList | Entrée Kitsu "
        "| Type | Titre Kitsu | Rattachée à |",
        "|---:|---|---|---|---|---:|---|---|---|",
        *[
            f"| {s} | {titre} | `{m}` | {mal or '—'} | {ani or '—'} | {k} "
            f"| {st or '?'} | {canon or '—'} | {autre or '—'} |"
            for _, s, titre, m, mal, ani, k, st, canon, autre in lignes
        ],
    ]


# --------------------------------------------------------------------------- #
#  Pilote
# --------------------------------------------------------------------------- #


def executer(
    url: str,
    commit: bool,
    chemin: Path | None = None,
    canaris: dict[int, int] = CANARIS,
) -> tuple[dict, Path]:
    debut = time.monotonic()
    horodatage = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    mode = "exécution" if commit else "à blanc (ROLLBACK)"
    chemin = chemin or RAPPORTS / (
        f"propagation_kitsu_{'execution' if commit else 'dryrun'}_{horodatage}.md"
    )
    r: dict = {"mode": mode, "horodatage": horodatage, "commit": commit}
    arret: str | None = None

    with psycopg.connect(url) as cx, cx.cursor() as cur:
        verifier_prerequis(cur)
        r["environnement"] = environnement(cur, url)
        r["catalogue"] = cur.execute(
            "SELECT count(*) FROM manga.ms_series_enriched"
        ).fetchone()[0]
        r["empreinte_avant"] = empreinte_moyeu(cur)
        r["atteignables_avant"] = len(atteignables(cx))

        cur.execute(CLASSEMENT.read_text(encoding="utf-8"))
        r["cas"] = compter_cas(cur)
        r["candidates"] = sum(r["cas"].values())
        r["par_source"] = repartition(cur, "rattachable")
        r["sans_chemin"] = repartition(cur, "sans_chemin")
        cur.execute(
            "SELECT subtype, count(*) FROM propagation_serie "
            "WHERE cas = 'rattachable' GROUP BY 1 ORDER BY 2 DESC"
        )
        r["par_type"] = dict(cur.fetchall())
        r["conflits"] = lister_conflits(cur, ("conflit_plusieurs_entrees", *CAS_ARRET))
        r["titres"] = mesurer_titres(cx, cur)
        r["canaris"] = verifier_canaris(cur, canaris)

        if any(r["cas"][c] for c in CAS_ARRET):
            arret = "point B — conflit d'identité, voir la liste des conflits"
        elif r["canaris"]:
            arret = "point C — canari non rattaché : " + " ; ".join(r["canaris"])

        r["ecrites"] = 0
        if arret is None:
            cur.execute(ECRITURE.read_text(encoding="utf-8"))
            r["ecrites"] = r["cas"]["rattachable"]
        r["apres"] = controles_apres(cur)
        r["empreinte_apres"] = empreinte_moyeu(cur)
        r["atteignables_apres"] = len(atteignables(cx))
        if arret is None and any(r["apres"].values()):
            arret = "contrôle après écriture non nul : " + str(r["apres"])
        if arret is None and verifier_canaris(cur, canaris):
            arret = "point C — canari absent après écriture"

        if arret is None and commit:
            cx.commit()
        else:
            cx.rollback()

    r["duree"] = time.monotonic() - debut
    if arret:
        r["issue"] = f"ARRÊT — {arret}. Transaction annulée, rien n'est écrit"
    elif commit and r["ecrites"]:
        r["issue"] = "écrit et commité"
    elif commit:
        r["issue"] = "rien à écrire — moyeu inchangé (rejeu)"
    else:
        r["issue"] = "à blanc : tout a été joué puis annulé"
    ecrire_rapport(chemin, r)
    r["arret"] = arret
    return r, chemin


@app.command()
def principal(
    dry_run: bool = typer.Option(  # noqa: B008
        False, help="Joue tout, contrôles compris, puis ROLLBACK."
    ),
    rapport: Path | None = typer.Option(  # noqa: B008
        None, help="Chemin du rapport (défaut : rapports/propagation_kitsu_*.md)."
    ),
) -> None:
    """Propage le kitsu_id des séries identifiées, par identifiant."""
    r, chemin = executer(dsn(), commit=not dry_run, chemin=rapport)
    typer.echo(
        f"Candidates : {r['candidates']} · "
        + ", ".join(f"{c} {v}" for c, v in r["cas"].items())
    )
    typer.echo(
        f"Rattachements : {r['ecrites']} · atteignables "
        f"{r['atteignables_avant']} → {r['atteignables_apres']}"
    )
    typer.echo(f"Rapport : {chemin}")
    typer.echo(r["issue"])
    if r["arret"]:
        raise ErreurPropagation(r["arret"])


def main() -> int:
    try:
        app()
    except ErreurPropagation as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
