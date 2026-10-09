"""Construction du corpus v2 dans `bench`, à côté du v1 — E3, étape 1, bloc D.

    uv run python -m corpus.construire_v2 --dry-run   # tout, contrôles, ROLLBACK
    uv run python -m corpus.construire_v2             # construit, contrôle, clôt le v2

LA RÈGLE — celle du 2026-09-28 (`corpus.construire`), avec TROIS différences, et
seulement trois (`config/corpus_v2.toml`) :

  1. les RÉSUMÉS DE SÉRIE du raw de juillet s'ajoutent, un document par série
     (`ms_synopsis:<series_id>`), texte `Résumé Manga <titre>` + saut de ligne + le
     résumé, langue notée en métadonnée. Exclus, dans cet ordre : moins de 50
     caractères ; faux résumé (le texte contient l'amorce d'un lien vers une
     chronique, ou une critique entière de la même série) ; pseudonyme dans le
     texte. Les séries absentes du raw de juillet n'y ont pas de résumé ;
  2. le DÉCOUPAGE est celui de `corpus.phrases`, pour tous les types ;
  3. le texte des critiques n'est PLUS MASQUÉ (D2) : seul le nom de l'auteur reste
     anonymisé.

Tout le reste est le code du v1 : sélection des critiques (S1–S6), gabarit et
part Kitsu (`corpus.kitsu`), gardes.

LES PSEUDONYMES — contrôlés AVANT d'écrire, et bloquants (décision de Max) :
  - critiques : exactement les occurrences connues — les références masquées en
    v1 (`corpus_references_masquees.csv`) et les homonymes admis ; aucune de plus ;
  - Kitsu : exactement les homonymes admis par provenance ;
  - résumés : aucune, ni dans le texte ni dans le titre ;
  - hors du texte (titre, métadonnées) : seulement des homonymes admis.

UNE SEULE TRANSACTION, une seule porte d'écriture (`corpus.ecriture`), corpus
nommé `v2`. Le v1 n'est ni lu ni touché. Le v2 est CLOS à la fin : un rejeu sans
écart n'écrit rien ; un écart sur un v2 clos est refusé par la base.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import tomllib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

from corpus import ecriture, kitsu, phrases, pseudonymes
from corpus.construire import (
    COLONNES_DOC,
    DONNEES_DEFAUT,
    RACINE,
    RAW_DEFAUT,
    SQL_COMPTAGE,
    SQL_EMPREINTE,
    SQL_GARDES,
    SQL_KITSU_RATTACHEES,
    SQL_MS_REVIEW,
    SQL_TEMPORAIRES,
    ErreurChargement,
    dsn,
    dsn_affichable,
    lire_manifeste,
    lire_snapshot,
    verser_cible,
)

MODULE = Path(__file__).resolve().parents[2]
CONFIG_DEFAUT = MODULE / "config/corpus_v2.toml"
RAPPORTS_DEFAUT = MODULE / "data/rapports"
SOURCES = ("ms_review", "kitsu_synopsis", "ms_synopsis")

app = typer.Typer(add_completion=False, help=__doc__)


def charger_config(chemin: Path = CONFIG_DEFAUT) -> dict:
    brut = chemin.read_bytes()
    config = tomllib.loads(brut.decode("utf-8"))
    config["empreinte"] = hashlib.sha256(brut).hexdigest()
    return config


# --------------------------------------------------------------------------- #
#  Les résumés de série
# --------------------------------------------------------------------------- #


def langue(texte: str, c: dict) -> str:
    """La règle de langue (information annexe) : mots-outils, seuils de la config."""
    mots = [
        m.lower().strip("'") for m in re.findall(r"[a-zàâäéèêëîïôöùûüç']+", texte, re.I)
    ]
    fr = sum(m in set(c["mots_fr"]) for m in mots)
    en = sum(m in set(c["mots_en"]) for m in mots)
    if fr >= c["minimum"] and fr >= c["rapport"] * en:
        return "fr"
    if en >= c["minimum"] and en >= c["rapport"] * fr:
        return "en"
    return "indeterminee"


def lire_resumes(raw: Path) -> tuple[dict[int, str | None], str]:
    """series_id → résumé du raw (ou None), après contrôle du manifeste."""
    lignes_attendues, sha_attendu = lire_manifeste(raw)
    condense, n = hashlib.sha256(), 0
    resumes: dict[int, set] = {}
    with raw.open("rb") as f:
        for brute in f:
            condense.update(brute)
            n += 1
            item = json.loads(brute)
            s = item.get("series_synopsis")
            resumes.setdefault(int(item["series_id"]), set()).add(
                s if s not in (None, "None") else None
            )
    if condense.hexdigest() != sha_attendu or n != lignes_attendues:
        raise ErreurChargement(f"{raw.name} : non conforme à son manifeste")
    divergents = [k for k, v in resumes.items() if len({x for x in v if x}) > 1]
    if divergents:
        raise ErreurChargement(
            f"{raw.name} : {len(divergents)} séries à résumés divergents"
        )
    return {k: next((x for x in v if x), None) for k, v in resumes.items()}, sha_attendu


def documents_resumes(
    cur: psycopg.Cursor, config: dict, pseudos: dict[str, str]
) -> tuple[list[dict], dict]:
    r = config["resumes"]
    raw = RACINE / r["raw"]
    resumes, sha = lire_resumes(raw)
    table = {
        sid: (titre, syn)
        for sid, titre, syn in cur.execute(
            "SELECT series_id, series_title, series_synopsis"
            " FROM manga.ms_series_enriched"
        )
    }
    critiques: dict[int, list[str]] = {}
    for sid, corps in cur.execute(
        "SELECT series_id, btrim(review_body) FROM manga.ms_reviews_all"
        " WHERE series_id IS NOT NULL AND length(btrim(review_body)) >= %s",
        (r["critique_incluse_min"],),
    ):
        critiques.setdefault(sid, []).append(corps)
    remplissage = re.compile(r["remplissage"], re.IGNORECASE)
    amorce = re.compile(r["amorce"], re.IGNORECASE)
    motifs = [pseudonymes.motif(p) for p in pseudos.values()]

    def reel(texte: str | None) -> bool:
        return bool(texte and texte.strip() and not remplissage.match(texte.strip()))

    exclus: Counter = Counter()
    exclus["1_absent_du_raw_de_juillet"] = sum(
        1 for sid, (_, syn) in table.items() if sid not in resumes and reel(syn)
    )
    docs = []
    for sid in sorted(resumes):
        texte = resumes[sid]
        if not reel(texte):
            continue
        if sid not in table:
            raise ErreurChargement(f"série {sid} du raw absente du catalogue")
        if table[sid][1] != texte:
            raise ErreurChargement(f"série {sid} : résumé du raw ≠ résumé du catalogue")
        texte = texte.strip()
        if len(texte) < r["plancher"]:
            exclus["2_moins_de_50"] += 1
            continue
        if amorce.search(texte) or any(c in texte for c in critiques.get(sid, [])):
            exclus["3_faux_resume"] += 1
            continue
        if any(m.search(texte) for m in motifs):
            exclus["4_pseudonyme"] += 1
            continue
        titre = table[sid][0]
        docs.append(
            {
                "doc_key": f"ms_synopsis:{sid}",
                "source": "ms_synopsis",
                "series_id": sid,
                "kitsu_id": None,
                "boost_score": None,
                "doc_text": r["entete"].format(titre=titre) + "\n" + texte,
                "metadata_json": json.dumps(
                    {
                        "series_id": sid,
                        "title": titre,
                        "langue": langue(texte, config["langue"]),
                        "resume_len": len(texte),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "title": titre,
            }
        )
    bilan = {
        "raw": str(raw.relative_to(RACINE)) if raw.is_relative_to(RACINE) else str(raw),
        "raw_sha": sha,
        "exclus": dict(sorted(exclus.items())),
        "retenus": len(docs),
        "langues": dict(
            Counter(json.loads(d["metadata_json"])["langue"] for d in docs)
        ),
    }
    return docs, bilan


# --------------------------------------------------------------------------- #
#  Les pseudonymes — bloquant, avant toute écriture
# --------------------------------------------------------------------------- #


def controler_pseudonymes(
    docs: dict[str, dict], pseudos: dict[str, str], donnees: Path
) -> dict:
    """Les occurrences de pseudonymes du corpus v2, contre ce qui est connu."""
    masquage, homonymes = pseudonymes.lire_listes(donnees)
    masques = {(e.doc_key, e.empreinte) for e in masquage}
    admis = {(e.doc_key, e.empreinte) for e in homonymes}
    motifs = {h: pseudonymes.motif(p) for h, p in pseudos.items()}
    texte, hors_texte = set(), set()
    for k, d in docs.items():
        for h, m in motifs.items():
            if m.search(d["doc_text"]):
                texte.add((k, h))
            if m.search(d["title"] or "") or m.search(d["metadata_json"] or ""):
                hors_texte.add((k, h))
    trouves = texte | hors_texte

    def de(source: str, couples: set) -> set:
        return {c for c in couples if docs[c[0]]["source"] == source}

    def connus(prefixe: str, couples: set) -> set:
        return {c for c in couples if c[0].startswith(prefixe)}

    attendus = {
        "ms_review": connus("ms_review:", masques | admis),
        "kitsu_synopsis": connus("kitsu:", admis),
        "ms_synopsis": set(),
    }
    ecarts = {}
    for source, attendu in attendus.items():
        vus = de(source, trouves)
        if vus != attendu:
            ecarts[source] = {
                "en_trop": sorted(vus - attendu),
                "manquants": sorted(attendu - vus),
            }
    hors_admis = sorted(hors_texte - admis)
    if hors_admis:
        ecarts["hors_texte"] = {"non_admis": hors_admis}
    return {
        "ecarts": ecarts,
        "critiques": len(de("ms_review", trouves)),
        "dont_references_masquees_en_v1": len(de("ms_review", trouves) & masques),
        "kitsu": len(de("kitsu_synopsis", trouves)),
        "resumes": len(de("ms_synopsis", trouves)),
        "hors_texte": {s: len(de(s, hors_texte)) for s in SOURCES},
    }


# --------------------------------------------------------------------------- #
#  Contrôles en base, après écriture, avant validation
# --------------------------------------------------------------------------- #

SQL_ETAT = """
SELECT 'docs:' || source, count(*) FROM bench.corpus_docs
  WHERE corpus_id = %(corpus)s GROUP BY source
UNION ALL
SELECT 'fragments:' || d.source, count(*)
  FROM bench.corpus_chunks k
  JOIN bench.corpus_docs d ON d.corpus_id = k.corpus_id AND d.doc_key = k.doc_key
  WHERE k.corpus_id = %(corpus)s GROUP BY d.source
ORDER BY 1
"""

SQL_CONTROLES = """
SELECT
  (SELECT count(*) FROM bench.corpus_docs WHERE corpus_id = %(corpus)s
     AND source NOT IN ('ms_review', 'kitsu_synopsis', 'ms_synopsis')),
  (SELECT count(*) FROM bench.corpus_docs WHERE corpus_id = %(corpus)s
     AND source = 'ms_review' AND (series_id IS NULL
       OR doc_key <> 'ms_review:' || (metadata_json ->> 'site_id'))),
  (SELECT count(*) FROM bench.corpus_docs WHERE corpus_id = %(corpus)s
     AND source = 'kitsu_synopsis'
     AND (kitsu_id IS NULL OR doc_key <> 'kitsu:' || kitsu_id)),
  (SELECT count(*) FROM bench.corpus_docs WHERE corpus_id = %(corpus)s
     AND source = 'ms_synopsis' AND (series_id IS NULL
       OR doc_key <> 'ms_synopsis:' || series_id)),
  (SELECT count(*) FROM bench.corpus_docs d WHERE corpus_id = %(corpus)s
     AND NOT EXISTS (SELECT 1 FROM bench.corpus_chunks k
                     WHERE k.corpus_id = d.corpus_id AND k.doc_key = d.doc_key)),
  (SELECT coalesce(max(length(chunk_text)), 0) FROM bench.corpus_chunks
     WHERE corpus_id = %(corpus)s)
"""


# --------------------------------------------------------------------------- #
#  La construction
# --------------------------------------------------------------------------- #


def executer(
    url: str,
    raw: Path = RAW_DEFAUT,
    donnees: Path = DONNEES_DEFAUT,
    run_kitsu: Path = kitsu.RUN_DEFAUT,
    config: dict | None = None,
    reglages: phrases.Reglages | None = None,
    dry_run: bool = False,
) -> dict:
    """La construction complète ; rend le rapport. Lève si une garde ou un
    contrôle échoue — la transaction est alors annulée."""
    debut = time.monotonic()
    config = config or charger_config()
    reglages = reglages or phrases.charger_reglages()
    corpus_id = config["corpus"]["corpus_id"]
    urls, raw_sha = lire_snapshot(raw)
    r: dict = {
        "mode": "dry-run" if dry_run else "construction",
        "horodatage": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "dsn": dsn_affichable(url),
        "corpus_id": corpus_id,
        "config_empreinte": config["empreinte"],
        "decoupage": {"nom": reglages.nom, "empreinte": reglages.empreinte},
        "raw_critiques_sha": raw_sha,
        "snapshot": len(urls),
    }
    p = {"corpus": corpus_id}
    with psycopg.connect(url) as connexion, connexion.cursor() as cur:
        cur.execute(
            "LOCK TABLE bench.corpus_docs, bench.corpus_chunks"
            " IN SHARE ROW EXCLUSIVE MODE"
        )
        cur.execute(SQL_TEMPORAIRES)
        with cur.copy("COPY snapshot_urls (review_url) FROM STDIN") as copie:
            for u in urls:
                copie.write_row((u,))
        gardes = dict(
            zip(
                ("G1 motif", "G1 doublons", "G5 absentes", "G3 sans série"),
                cur.execute(SQL_GARDES).fetchone(),
                strict=True,
            )
        )
        if any(gardes.values()):
            raise ErreurChargement(f"garde en échec : {gardes}")
        r["clauses"] = dict(cur.execute(SQL_COMPTAGE).fetchall())

        # Les documents : critiques (non masquées), Kitsu, résumés de série.
        docs: dict[str, dict] = {}
        for ligne in cur.execute(SQL_MS_REVIEW):
            d = dict(zip(COLONNES_DOC, ligne, strict=True))
            docs[d["doc_key"]] = d
        rattachees = {k for (k,) in cur.execute(SQL_KITSU_RATTACHEES)}
        try:
            docs_kitsu, _ = kitsu.documents(run_kitsu, rattachees)
        except kitsu.ErreurKitsu as erreur:
            raise ErreurChargement(f"Kitsu — {erreur}") from erreur
        docs.update((d["doc_key"], d) for d in docs_kitsu)
        pseudos = {
            pseudonymes.empreinte(a): a
            for (a,) in cur.execute(
                "SELECT DISTINCT review_author FROM manga.ms_reviews_all"
                " WHERE review_author IS NOT NULL"
            )
        }
        docs_resumes, r["resumes"] = documents_resumes(cur, config, pseudos)
        docs.update((d["doc_key"], d) for d in docs_resumes)
        r["documents"] = dict(Counter(d["source"] for d in docs.values()))

        r["pseudonymes"] = controler_pseudonymes(docs, pseudos, donnees)
        ecarts = r["pseudonymes"]["ecarts"]
        if ecarts:
            # Des nombres et des empreintes, jamais un pseudonyme en clair.
            resume = {s: {x: len(y) for x, y in v.items()} for s, v in ecarts.items()}
            raise ErreurChargement(f"pseudonymes hors de ce qui est connu : {resume}")

        # Le découpage par phrases, pour tous les types.
        fragments, coupes = [], Counter()
        for doc_key in sorted(docs):
            fr, b = phrases.decouper(docs[doc_key]["doc_text"], reglages)
            coupes.update(b)
            for i, f in enumerate(fr):
                fragments.append(
                    (
                        doc_key,
                        i,
                        f.texte,
                        f.debut,
                        f.fin,
                        hashlib.sha1(f.texte.encode("utf-8")).hexdigest(),
                    )  # nosec B324 — empreinte de contenu, comme le v1
                )
        r["coupes"] = dict(coupes)
        r["fragments"] = len(fragments)
        verser_cible(cur, docs, fragments)

        # Le corpus v2 : inscrit s'il manque, avec sa stratégie de découpage.
        chunking_id = phrases.enregistrer_strategie(cur, reglages)
        ligne = cur.execute(
            "SELECT chunking_id, clos_le FROM bench.corpus WHERE corpus_id = %s",
            (corpus_id,),
        ).fetchone()
        if ligne is None:
            cur.execute(
                "INSERT INTO bench.corpus (corpus_id, regle, chunking_id)"
                " VALUES (%s, %s, %s)",
                (corpus_id, config["corpus"]["regle"], chunking_id),
            )
        elif ligne[0] != chunking_id:
            raise ErreurChargement(f"corpus {corpus_id} : autre découpage inscrit")

        r["empreinte_avant"] = cur.execute(SQL_EMPREINTE, p).fetchone()
        r["ecritures"] = ecriture.ecrire_corpus(cur, corpus_id=corpus_id)
        r["etat"] = dict(cur.execute(SQL_ETAT, p).fetchall())
        r["empreinte_apres"] = cur.execute(SQL_EMPREINTE, p).fetchone()
        hors_type, ms_mal, kitsu_mal, resume_mal, sans_fragment, plus_long = (
            cur.execute(SQL_CONTROLES, p).fetchone()
        )
        r["controles"] = {
            "type hors règle": hors_type,
            "critiques mal rattachées": ms_mal,
            "Kitsu mal rattachés": kitsu_mal,
            "résumés mal rattachés": resume_mal,
            "documents sans fragment": sans_fragment,
            "fragment au-delà de la taille": int(plus_long > reglages.taille),
        }
        en_echec = [f"{k} = {v}" for k, v in r["controles"].items() if v]
        if en_echec:
            connexion.rollback()
            raise ErreurChargement(f"contrôles en échec : {en_echec}")

        # La clôture : un corpus construit et compté ne bouge plus.
        cur.execute(
            "UPDATE bench.corpus SET clos_le = now(),"
            " nb_documents = (SELECT count(*) FROM bench.corpus_docs"
            "                 WHERE corpus_id = %(corpus)s),"
            " nb_fragments = (SELECT count(*) FROM bench.corpus_chunks"
            "                 WHERE corpus_id = %(corpus)s)"
            " WHERE corpus_id = %(corpus)s AND clos_le IS NULL",
            p,
        )
        r["clos_maintenant"] = cur.rowcount == 1
        if dry_run:
            connexion.rollback()
            r["issue"] = "contrôles verts — ANNULÉ (aucune écriture validée)"
        else:
            connexion.commit()
            r["issue"] = "contrôles verts — VALIDÉ"
    r["duree"] = round(time.monotonic() - debut, 1)
    return r


def ecrire_rapport(chemin: Path, r: dict) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(
        f"# Construction du corpus {r['corpus_id']} — {r['mode']}\n\n"
        f"Horodatage `{r['horodatage']}` · {r['duree']} s · **{r['issue']}**\n\n"
        "```json\n"
        + json.dumps(r, ensure_ascii=False, indent=1, default=str)
        + "\n```\n",
        encoding="utf-8",
    )


@app.command()
def construire(
    # noqa: B008 — `typer.Option()` en défaut est l'API de Typer, pas un oubli.
    dry_run: bool = typer.Option(  # noqa: B008
        False, "--dry-run", help="Tout exécuter, contrôles compris, puis annuler."
    ),
    rapports: Path = typer.Option(  # noqa: B008
        RAPPORTS_DEFAUT, help="Dossier des rapports (hors dépôt)."
    ),
) -> None:
    """Construit le corpus v2 à côté du v1, le contrôle et le clôt."""
    r = executer(dsn(), dry_run=dry_run)
    suffixe = "dryrun" if dry_run else "execution"
    chemin = rapports / f"corpus_v2_{suffixe}_{r['horodatage']}.md"
    ecrire_rapport(chemin, r)
    typer.echo(f"{r['issue']} — rapport : {chemin}")
    typer.echo(
        json.dumps(
            {
                k: r[k]
                for k in (
                    "documents",
                    "fragments",
                    "etat",
                    "ecritures",
                    "clos_maintenant",
                )
            },
            ensure_ascii=False,
            default=str,
        )
    )


def main() -> int:
    try:
        app()
    except ErreurChargement as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
