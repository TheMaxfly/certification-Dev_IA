"""Reconstruction du corpus RAG dans `bench` — règle validée le 2026-09-28.

    uv run python -m corpus.construire --dry-run   # tout, puis ROLLBACK
    uv run python -m corpus.construire             # applique
    uv run python -m corpus.construire --a-blanc   # rechargement complet, ROLLBACK

LA RÈGLE — validée le 2026-09-28 (rapports/corpus_decisions_20260928.md)

  S1  source `manga.ms_reviews_all`, jamais `ms_reviews` ;
  S2  appartenance au snapshot 2026-07, lue dans le RAW MANIFESTÉ (D2) et non
      dans `staging.ms_reviews`, que le prochain cycle écrasera ;
  S3  corps non vide, sans seuil (aucun corps entre 1 et 108 caractères) ;
  S5  `series_id` non nul — une garde qui ARRÊTE, pas un filtre ;
  S6  un document par (série, corps identique), clé = plus petit `site_id`.

  C1  une critique = un document `ms_review:<site_id>` ;
  C2  `kitsu_synopsis` construits depuis le raw Kitsu de JUILLET (même
      snapshot pour toutes les sources, règle du 2026-09-29) : cf. `kitsu.py` ;
  C3  `ms_hybrid` non reconstruit (D1) — les 5 608 sont retirés.

  A1–A4  aucun auteur dans `bench` ; références à un membre masquées ;
         non-fuite contrôlée, seuls les homonymes qualifiés sont tolérés.

UN DIFF, PAS UNE PURGE. Le chargeur compare la cible à `bench` et n'écrit que
l'écart : une ligne identique n'est pas réécrite, un fragment identique garde
son `chunk_id`. Le rejeu annonce donc zéro écriture, et c'est ce qui rend
l'idempotence vérifiable (§7.2). Le rechargement complet (§7.3) se prouve à
part, par `--a-blanc` : tout est vidé puis reconstruit DANS une transaction
annulée, et les empreintes avant/après sont comparées.

UNE SEULE TRANSACTION. Gardes, écriture, puis contrôles du §7 : si un contrôle
échoue, rien n'est validé. Un corpus à moitié reconstruit est un état que
personne ne sait interpréter.

CE QUE LE CHARGEUR NE FAIT PAS : il ne touche ni `manga`, ni `queries`, ni
aucun embedding. Il retire les documents hors règle, et les FK en `ON DELETE
CASCADE` emportent qrels et résultats qui les visaient — état archivé AVANT
(`data/archives/bench_2025-12/`, D6).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

from corpus import decoupage, kitsu, pseudonymes

RACINE = Path(__file__).resolve().parents[3]
RAW_DEFAUT = (
    RACINE
    / "04_scraping_manga_sanctuary/data/raw/2026-07/manga_sanctuary_reviews.jsonl"
)
DONNEES_DEFAUT = RACINE / "database/donnees"
RAPPORTS_DEFAUT = RACINE / "05_nettoyage_agregation_bdd/rapports"

SOURCES_REGLE = ("kitsu_synopsis", "ms_review")

app = typer.Typer(add_completion=False, help=__doc__)


class ErreurChargement(Exception):
    """Erreur attendue : message lisible, pas de trace."""


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ErreurChargement(
            "DATABASE_URL n'est pas définie. Exemple :\n"
            "  export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'"
        )
    return url


# --------------------------------------------------------------------------- #
#  S2 — le snapshot, lu dans le raw manifesté
# --------------------------------------------------------------------------- #

LIGNE_MANIFESTE = re.compile(
    r"^\|\s*`(?P<nom>[^`]+)`\s*\|\s*(?P<lignes>[\d\s ]+)\|\s*(?P<octets>[\d\s ]+)"
    r"\|\s*`(?P<sha>[0-9a-f]{64})`"
)


def lire_manifeste(raw: Path) -> tuple[int, str]:
    """(lignes, sha256) déclarés pour `raw` dans le MANIFEST.md de son dossier."""
    manifeste = raw.parent / "MANIFEST.md"
    if not manifeste.is_file():
        raise ErreurChargement(f"Manifeste introuvable : {manifeste}")
    for ligne in manifeste.read_text(encoding="utf-8").splitlines():
        m = LIGNE_MANIFESTE.match(ligne)
        if m and m["nom"] == raw.name:
            return int(re.sub(r"\D", "", m["lignes"])), m["sha"]
    raise ErreurChargement(f"{raw.name} n'est pas décrit dans {manifeste}")


def lire_snapshot(raw: Path) -> tuple[list[str], str]:
    """Les `review_url` du snapshot, après vérification de l'empreinte (G5)."""
    if not raw.is_file():
        raise ErreurChargement(f"Raw introuvable : {raw}")
    lignes_attendues, sha_attendu = lire_manifeste(raw)
    condense = hashlib.sha256()
    urls: list[str] = []
    with raw.open("rb") as f:
        for numero, brute in enumerate(f, start=1):
            condense.update(brute)
            url = json.loads(brute).get("review_url")
            if not url:
                raise ErreurChargement(f"{raw.name}:{numero} sans review_url")
            urls.append(url)
    if condense.hexdigest() != sha_attendu:
        raise ErreurChargement(
            f"G5 — {raw.name} : sha256 {condense.hexdigest()} ≠ manifeste {sha_attendu}"
        )
    if len(urls) != lignes_attendues or len(set(urls)) != len(urls):
        raise ErreurChargement(
            f"G5 — {raw.name} : {len(urls)} lignes, {len(set(urls))} URL distinctes, "
            f"manifeste {lignes_attendues}"
        )
    return sorted(urls), sha_attendu


# --------------------------------------------------------------------------- #
#  La règle en SQL — mêmes requêtes que le document de règle, le snapshot
#  venant de la table temporaire `snapshot_urls`
# --------------------------------------------------------------------------- #

SQL_GARDES = """
SELECT
  (SELECT count(*) FROM manga.ms_reviews_all
    WHERE review_url IS NULL
       OR review_url !~ 'fiche_serie_critique\\.php\\?id=[0-9]+$') AS g1_motif,
  (SELECT count(*) - count(DISTINCT substring(review_url FROM 'id=([0-9]+)$'))
     FROM manga.ms_reviews_all) AS g1_doublons,
  (SELECT count(*) FROM snapshot_urls s WHERE NOT EXISTS
     (SELECT 1 FROM manga.ms_reviews_all a WHERE a.review_url = s.review_url))
    AS g5_absentes,
  (SELECT count(*) FROM manga.ms_reviews_all a
     JOIN snapshot_urls s USING (review_url)
    WHERE length(btrim(coalesce(a.review_body, ''))) > 0
      AND a.series_id IS NULL) AS g3_sans_serie
"""

SQL_COMPTAGE = """
WITH a AS (
  SELECT a.series_id, btrim(coalesce(a.review_body, '')) AS corps,
         EXISTS (SELECT 1 FROM snapshot_urls s WHERE s.review_url = a.review_url)
           AS dans_snap
  FROM manga.ms_reviews_all a)
SELECT 'S1 source' AS clause, count(*) AS restent FROM a
UNION ALL SELECT 'S2 snapshot', count(*) FROM a WHERE dans_snap
UNION ALL SELECT 'S3 corps', count(*) FROM a WHERE dans_snap AND corps <> ''
UNION ALL SELECT 'S5 série', count(*) FROM a
  WHERE dans_snap AND corps <> '' AND series_id IS NOT NULL
UNION ALL SELECT 'S6 doublons', count(*) FROM (SELECT DISTINCT series_id, corps FROM a
  WHERE dans_snap AND corps <> '' AND series_id IS NOT NULL) d
"""

CTE_RETENUES = """
candidats AS (
  SELECT substring(a.review_url FROM 'id=([0-9]+)$')::bigint AS site_id,
         a.series_id, a.volume_number, a.review_grain,
         btrim(a.review_body) AS corps
  FROM manga.ms_reviews_all a                                   -- S1
  JOIN snapshot_urls s USING (review_url)                       -- S2
  WHERE length(btrim(coalesce(a.review_body, ''))) > 0          -- S3
    AND a.series_id IS NOT NULL                                 -- S5 (garde)
),
retenues AS (                                                   -- S6
  SELECT series_id, corps,
         min(site_id)                                             AS site_id,
         array_agg(site_id ORDER BY site_id)                      AS site_ids,
         array_agg(DISTINCT volume_number ORDER BY volume_number) AS volumes,
         min(review_grain)                                        AS review_grain
  FROM candidats
  GROUP BY series_id, corps
)"""

SQL_MS_REVIEW = f"""
WITH {CTE_RETENUES}
SELECT 'ms_review:' || r.site_id                                  AS doc_key,
       'ms_review'                                                AS source,
       r.series_id,
       NULL::bigint                                               AS kitsu_id,
       NULL::text                                                 AS boost_score,
       'Critique Manga ' || se.series_title
         || coalesce(' #' || r.volumes[1], '') || E'\\n' || r.corps AS doc_text,
       jsonb_build_object('site_id', r.site_id, 'site_ids', r.site_ids,
                          'series_id', r.series_id, 'volumes', r.volumes,
                          'review_grain', r.review_grain,
                          'title', se.series_title,
                          'corps_len', length(r.corps))::text     AS metadata_json,
       se.series_title                                            AS title
FROM retenues r
JOIN manga.ms_series_enriched se USING (series_id)
ORDER BY r.site_id
"""

#: Les œuvres Kitsu que la cascade rattache au catalogue — seul motif
#: d'admission d'un roman (K1).
SQL_KITSU_RATTACHEES = """
SELECT kitsu_id::bigint FROM manga.work_identity
WHERE kitsu_id IS NOT NULL AND series_id IS NOT NULL
"""

COLONNES_DOC = (
    "doc_key",
    "source",
    "series_id",
    "kitsu_id",
    "boost_score",
    "doc_text",
    "metadata_json",
    "title",
)

SQL_TEMPORAIRES = """
CREATE TEMP TABLE snapshot_urls (review_url text PRIMARY KEY) ON COMMIT DROP;
CREATE TEMP TABLE cible_docs (LIKE bench.corpus_docs) ON COMMIT DROP;
CREATE TEMP TABLE cible_chunks (
  doc_key text, chunk_index integer, chunk_text text,
  char_start integer, char_end integer, chunk_hash text) ON COMMIT DROP;
CREATE TEMP TABLE admis (doc_key text, empreinte text) ON COMMIT DROP;
"""

# --- le diff --------------------------------------------------------------- #

SQL_RETIRER_DOCS = """
DELETE FROM bench.corpus_docs d
WHERE NOT EXISTS (SELECT 1 FROM cible_docs c WHERE c.doc_key = d.doc_key)
RETURNING d.source
"""

SQL_MODIFIER_DOCS = """
UPDATE bench.corpus_docs d
SET source = c.source, series_id = c.series_id, kitsu_id = c.kitsu_id,
    boost_score = c.boost_score, doc_text = c.doc_text,
    metadata_json = c.metadata_json, title = c.title
FROM cible_docs c
WHERE c.doc_key = d.doc_key
  AND (d.source, d.series_id, d.kitsu_id, d.boost_score, d.doc_text,
       d.metadata_json, d.title)
      IS DISTINCT FROM
      (c.source, c.series_id, c.kitsu_id, c.boost_score, c.doc_text,
       c.metadata_json, c.title)
RETURNING d.source
"""

SQL_AJOUTER_DOCS = """
INSERT INTO bench.corpus_docs
  (doc_key, source, series_id, kitsu_id, boost_score, doc_text, metadata_json, title)
SELECT c.doc_key, c.source, c.series_id, c.kitsu_id, c.boost_score, c.doc_text,
       c.metadata_json, c.title
FROM cible_docs c
WHERE NOT EXISTS (SELECT 1 FROM bench.corpus_docs d WHERE d.doc_key = c.doc_key)
ORDER BY c.doc_key
RETURNING source
"""

SQL_RETIRER_CHUNKS = """
DELETE FROM bench.corpus_chunks k
WHERE NOT EXISTS (
  SELECT 1 FROM cible_chunks c
  WHERE c.doc_key = k.doc_key AND c.chunk_index = k.chunk_index
    AND c.chunk_text = k.chunk_text
    AND c.char_start IS NOT DISTINCT FROM k.char_start
    AND c.char_end IS NOT DISTINCT FROM k.char_end
    AND c.chunk_hash IS NOT DISTINCT FROM k.chunk_hash
    AND k.token_count IS NULL)
RETURNING split_part(k.doc_key, ':', 1)
"""

SQL_AJOUTER_CHUNKS = """
INSERT INTO bench.corpus_chunks
  (doc_key, chunk_index, chunk_text, char_start, char_end, token_count, chunk_hash)
SELECT c.doc_key, c.chunk_index, c.chunk_text, c.char_start, c.char_end, NULL,
       c.chunk_hash
FROM cible_chunks c
WHERE NOT EXISTS (SELECT 1 FROM bench.corpus_chunks k
                  WHERE k.doc_key = c.doc_key AND k.chunk_index = c.chunk_index)
ORDER BY c.doc_key, c.chunk_index
RETURNING split_part(doc_key, ':', 1)
"""

# --- les contrôles du §7 --------------------------------------------------- #

SQL_ETAT = """
SELECT 'docs:' || source, count(*) FROM bench.corpus_docs GROUP BY source
UNION ALL
SELECT 'fragments:' || split_part(doc_key, ':', 1), count(*)
  FROM bench.corpus_chunks GROUP BY split_part(doc_key, ':', 1)
UNION ALL SELECT 'qrels', count(*) FROM bench.qrels
UNION ALL SELECT 'retrieval_results', count(*) FROM bench.retrieval_results
UNION ALL SELECT 'retrieval_results sans fragment', count(*)
  FROM bench.retrieval_results WHERE chunk_id IS NULL
ORDER BY 1
"""

SQL_7_1 = f"""
WITH {CTE_RETENUES},
attendus AS (SELECT 'ms_review:' || site_id AS doc_key FROM retenues),
presents AS (SELECT doc_key FROM bench.corpus_docs WHERE source = 'ms_review')
SELECT (SELECT count(*) FROM attendus a
         WHERE NOT EXISTS (SELECT 1 FROM presents p WHERE p.doc_key = a.doc_key)),
       (SELECT count(*) FROM presents p
         WHERE NOT EXISTS (SELECT 1 FROM attendus a WHERE a.doc_key = p.doc_key))
"""

SQL_7_5 = """
SELECT
  count(*) FILTER (WHERE source NOT IN ('ms_review', 'kitsu_synopsis')),
  count(*) FILTER (WHERE source = 'ms_review' AND (series_id IS NULL
                   OR doc_key <> 'ms_review:' || (metadata_json ->> 'site_id'))),
  count(*) FILTER (WHERE source = 'kitsu_synopsis' AND (kitsu_id IS NULL
                   OR doc_key <> 'kitsu:' || kitsu_id))
FROM bench.corpus_docs
"""

#: A4 / §7.4 — la requête du document de règle, étendue aux fragments.
#:
#: Précision d'application du 2026-09-28. Un fragment est une tranche : la
#: coupure à 1 200 caractères peut trancher un mot plus long, et laisser en bord
#: de fragment un début de mot égal à un pseudonyme (constaté : un mot de 6
#: lettres coupé à 4). Une occurrence de fragment est donc classée :
#:   - `document` — le couple existe au niveau du document : admis ou fuite ;
#:   - `fragment` — absente du document mais À L'INTÉRIEUR du fragment : fuite
#:     (impossible en théorie, un intérieur de tranche est un intérieur de texte) ;
#:   - `coupure`  — absente du document et EN BORD de fragment : un mot tranché,
#:     compté et rapporté, non bloquant.
SQL_7_4 = """
WITH pseudos AS (
  SELECT DISTINCT
    left(encode(sha256(convert_to(review_author, 'UTF8')), 'hex'), 16) AS h,
    regexp_replace(review_author, '([.^$*+?()\\[\\]{}|\\\\-])', '\\\\\\1', 'g') AS esc
  FROM manga.ms_reviews_all WHERE review_author IS NOT NULL),
docs_occ AS (
  SELECT DISTINCT d.doc_key, x.h FROM bench.corpus_docs d JOIN pseudos x
    ON d.doc_text ~* ('\\m' || x.esc || '\\M') OR d.title ~* ('\\m' || x.esc || '\\M')
    OR d.metadata_json::text ~* ('\\m' || x.esc || '\\M')),
chunks_occ AS (
  SELECT k.doc_key, x.h,
         regexp_count(k.chunk_text, '\\m' || x.esc || '\\M', 1, 'i') AS n,
         (k.chunk_text ~* ('^' || x.esc || '\\M'))::int
           + (k.chunk_text ~* ('\\m' || x.esc || '$'))::int AS n_bord
  FROM bench.corpus_chunks k JOIN pseudos x
    ON k.chunk_text ~* ('\\m' || x.esc || '\\M'))
SELECT o.doc_key, o.h, 'document' AS niveau,
       EXISTS (SELECT 1 FROM admis a WHERE a.doc_key = o.doc_key AND a.empreinte = o.h)
FROM docs_occ o
UNION ALL
SELECT c.doc_key, c.h, CASE WHEN c.n > c.n_bord THEN 'fragment' ELSE 'coupure' END,
       false
FROM chunks_occ c
WHERE NOT EXISTS (SELECT 1 FROM docs_occ o WHERE o.doc_key = c.doc_key AND o.h = c.h)
ORDER BY 1, 2, 3
"""

#: Empreinte de contenu du corpus — sans `chunk_id`, qu'un rechargement complet
#: renumérote légitimement (séquence). Aucune colonne horodatée : l'empreinte ne
#: dépend pas du fuseau de session.
SQL_EMPREINTE = """
SELECT
  (SELECT md5(string_agg(ROW(doc_key, source, series_id, kitsu_id, boost_score,
                             doc_text, metadata_json, title)::text,
                         E'\\n' ORDER BY doc_key))
     FROM bench.corpus_docs),
  (SELECT md5(string_agg(ROW(doc_key, chunk_index, chunk_text, char_start, char_end,
                             token_count, chunk_hash)::text,
                         E'\\n' ORDER BY doc_key, chunk_index))
     FROM bench.corpus_chunks)
"""


# --------------------------------------------------------------------------- #
#  Construction de la cible
# --------------------------------------------------------------------------- #


def construire_cible(
    curseur: psycopg.Cursor, donnees: Path, run_kitsu: Path
) -> tuple[dict[str, dict], dict]:
    """Documents cibles (masqués), et le bilan de leur construction."""
    docs: dict[str, dict] = {}
    for ligne in curseur.execute(SQL_MS_REVIEW):
        doc = dict(zip(COLONNES_DOC, ligne, strict=True))
        docs[doc["doc_key"]] = doc
    rattachees = {k for (k,) in curseur.execute(SQL_KITSU_RATTACHEES)}
    try:
        docs_kitsu, bilan_kitsu = kitsu.documents(run_kitsu, rattachees)
    except kitsu.ErreurKitsu as erreur:
        raise ErreurChargement(f"Kitsu — {erreur}") from erreur
    docs.update((d["doc_key"], d) for d in docs_kitsu)

    pseudos = {
        pseudonymes.empreinte(p): p
        for (p,) in curseur.execute(
            "SELECT DISTINCT review_author FROM manga.ms_reviews_all"
            " WHERE review_author IS NOT NULL"
        )
    }
    masquage, homonymes = pseudonymes.lire_listes(donnees)
    try:
        pseudonymes.verifier_homonymes(docs, homonymes, pseudos)
        remplacements = pseudonymes.appliquer_masquage(docs, masquage, pseudos)
    except pseudonymes.ListeInvalide as erreur:
        raise ErreurChargement(f"D5 — liste versionnée : {erreur}") from erreur

    with curseur.copy("COPY admis (doc_key, empreinte) FROM STDIN") as copie:
        for e in homonymes:
            copie.write_row((e.doc_key, e.empreinte))

    bilan = {
        "par_source": Counter(d["source"] for d in docs.values()),
        "masquage_entrees": len(masquage),
        "masquage_documents": len({e.doc_key for e in masquage}),
        "masquage_remplacements": remplacements,
        "homonymes_admis": len(homonymes),
        "pseudonymes": len(pseudos),
        "kitsu": bilan_kitsu,
    }
    return docs, bilan


def decouper_cible(docs: dict[str, dict]) -> tuple[list[tuple], dict]:
    """Fragments de la cible, et ce que le plancher de décembre laisse de côté."""
    fragments: list[tuple] = []
    sans_fragment: Counter[str] = Counter()
    longueurs_sans: list[int] = []
    for doc_key in sorted(docs):
        doc = docs[doc_key]
        if not decoupage.est_decoupe(doc["doc_text"]):
            sans_fragment[doc["source"]] += 1
            longueurs_sans.append(len(doc["doc_text"] or ""))
            continue
        for index, (debut, fin, texte) in enumerate(
            decoupage.decouper(doc["doc_text"])
        ):
            fragments.append(
                (doc_key, index, texte, debut, fin, decoupage.empreinte_fragment(texte))
            )
    bilan = {
        "sans_fragment": sans_fragment,
        "sans_fragment_longueurs": (
            (min(longueurs_sans), max(longueurs_sans)) if longueurs_sans else None
        ),
        "fragment_max": max((len(f[2]) for f in fragments), default=0),
    }
    return fragments, bilan


def verser_cible(
    curseur: psycopg.Cursor, docs: dict[str, dict], fragments: list[tuple]
) -> None:
    with curseur.copy(
        f"COPY cible_docs ({', '.join(COLONNES_DOC)}) FROM STDIN"
    ) as copie:
        for doc_key in sorted(docs):
            copie.write_row(tuple(docs[doc_key][c] for c in COLONNES_DOC))
    with curseur.copy(
        "COPY cible_chunks (doc_key, chunk_index, chunk_text, char_start, char_end,"
        " chunk_hash) FROM STDIN"
    ) as copie:
        for fragment in fragments:
            copie.write_row(fragment)


def appliquer_diff(curseur: psycopg.Cursor, a_blanc: bool) -> dict:
    """Écrit l'écart cible → bench ; `a_blanc` vide d'abord tout le corpus."""
    ecritures: dict[str, Counter] = {}
    if a_blanc:
        vidage = curseur.execute("DELETE FROM bench.corpus_docs RETURNING source")
        ecritures["vidage"] = Counter(s for (s,) in vidage)
    for nom, requete in (
        ("docs retirés", SQL_RETIRER_DOCS),
        ("docs modifiés", SQL_MODIFIER_DOCS),
        ("docs ajoutés", SQL_AJOUTER_DOCS),
        ("fragments retirés", SQL_RETIRER_CHUNKS),
        ("fragments ajoutés", SQL_AJOUTER_CHUNKS),
    ):
        ecritures[nom] = Counter(s for (s,) in curseur.execute(requete))
    return ecritures


def controler(curseur: psycopg.Cursor) -> dict:
    """§7.1, §7.4, §7.5 — en base, après écriture, avant validation."""
    manquants, en_trop = curseur.execute(SQL_7_1).fetchone()
    hors_type, ms_mal, kitsu_mal = curseur.execute(SQL_7_5).fetchone()
    occurrences = curseur.execute(SQL_7_4).fetchall()
    fuites = [
        (d, h, niveau)
        for (d, h, niveau, admis) in occurrences
        if niveau != "coupure" and not admis
    ]
    return {
        "7.1 manquants": manquants,
        "7.1 en trop": en_trop,
        "7.4 fuites": fuites,
        "7.4 homonymes admis rencontrés": sum(1 for o in occurrences if o[3]),
        "7.4 coupures de fragment": [
            (d, h) for (d, h, niveau, _) in occurrences if niveau == "coupure"
        ],
        "7.5 type hors règle": hors_type,
        "7.5 ms_review mal rattachés": ms_mal,
        "7.5 kitsu mal rattachés": kitsu_mal,
    }


#: Rapportés, jamais bloquants : ce ne sont pas des défauts du corpus.
INFORMATIFS = ("7.4 homonymes admis rencontrés", "7.4 coupures de fragment")


def echecs(controles: dict) -> list[str]:
    return [
        f"{nom} = {valeur if not isinstance(valeur, list) else len(valeur)}"
        for nom, valeur in controles.items()
        if nom not in INFORMATIFS and valeur
    ]


# --------------------------------------------------------------------------- #
#  Rapport
# --------------------------------------------------------------------------- #


def dsn_affichable(url: str) -> str:
    infos = psycopg.conninfo.conninfo_to_dict(url)
    infos.pop("password", None)
    return " ".join(f"{k}={v}" for k, v in sorted(infos.items()))


def section_kitsu(b: kitsu.Bilan) -> list[str]:
    """La part Kitsu : d'où elle vient, et ce que chaque clause de K1 écarte."""

    def n(x: int) -> str:
        return f"{x:_}".replace("_", " ")

    exclues = sum(b.exclues_sous_type.values())
    return [
        "## Part Kitsu — raw de juillet (règle du 2026-09-29)",
        "",
        *[
            f"- `{nom}` sha256 `{sha}` (conforme au manifeste)"
            for nom, sha in b.fichiers.items()
        ],
        "",
        "| Clause | Œuvres |",
        "|---|---:|",
        f"| œuvres du raw | {n(b.oeuvres)} |",
        f"| écartées par le type (hors manga/manhwa/manhua) | {n(exclues)} |",
        *[f"| — dont {st} | {n(c)} |" for st, c in b.exclues_sous_type.most_common()],
        f"| romans réadmis (rattachés au catalogue par la cascade) | "
        f"{n(sum(b.sur_rattachement_admises.values()))} |",
        f"| écartées faute de synopsis | {n(b.exclues_sans_synopsis)} |",
        f"| — dont rattachées au catalogue par la cascade | "
        f"{n(b.rattachees_sans_synopsis)} |",
        f"| **retenues** | **{n(b.retenues)}** |",
        "",
        "Œuvres rattachées au catalogue par la cascade, par type : "
        + ", ".join(
            f"{st} {n(c)}" for st, c in b.rattachees_par_sous_type.most_common()
        )
        + ". Le référentiel Kitsu de la cascade ne charge que manga, manhwa et "
        "manhua : un roman rattaché est impossible par construction, et le catalogue "
        "ne connaît aucun type roman.",
        "",
        f"- Ligne `Auteurs:` (K2) : {n(b.avec_auteurs)} documents sur {n(b.retenues)}. "
        "Conséquence : une question du type « les mangas de Naoki Urasawa » devient "
        "trouvable par le texte Kitsu — F1 mesure aussi du lexical sur métadonnée.",
        "- Positions de tendance et `boost_score` : retirés (K3).",
        f"- `source_citee` (K4) : {n(sum(b.sources_citees.values()))} mentions ; "
        "les plus fréquentes : "
        + ", ".join(f"{s} {n(c)}" for s, c in b.sources_citees.most_common(8)),
        "",
    ]


def ecrire_rapport(chemin: Path, r: dict) -> None:
    def tableau(compteur: dict, entete=("", "")) -> list[str]:
        lignes = [f"| {entete[0]} | {entete[1]} |", "|---|---:|"]
        lignes += [f"| {k} | {v:,} |".replace(",", " ") for k, v in compteur.items()]
        return lignes

    L = [
        f"# Reconstruction du corpus — {r['mode']}",
        "",
        f"Horodatage : `{r['horodatage']}` · durée {r['duree']:.1f} s · "
        f"**{r['issue']}**",
        "",
        "## Environnement",
        "",
        f"- Serveur : {r['serveur']}",
        f"- Connexion : `{r['dsn']}`",
        f"- Raw du snapshot : `{r['raw']}` — sha256 `{r['raw_sha']}` "
        f"(conforme au manifeste), {r['snapshot']:_} URL".replace("_", " "),
        f"- Listes D5 : `{r['donnees']}`",
        "",
        "## Sélection — lignes restantes après chaque clause",
        "",
        *tableau(r["clauses"], ("Clause", "Restent")),
        "",
        "## Cible",
        "",
        *tableau(r["cible"]["par_source"], ("Source", "Documents")),
        "",
        f"- Masquage (D5) : {r['cible']['masquage_entrees']} entrées sur "
        f"{r['cible']['masquage_documents']} documents, "
        f"{r['cible']['masquage_remplacements']} remplacements par "
        f"`{pseudonymes.JETON}`",
        f"- Homonymes admis, tous retrouvés : {r['cible']['homonymes_admis']}",
        f"- Fragments : {r['fragments']:,} ; plus long : "
        f"{r['decoupe']['fragment_max']} caractères — aucune troncature".replace(
            ",", " "
        ),
        f"- **Sans fragment** (plancher de décembre, {decoupage.PLANCHER} car.) : "
        f"{dict(r['decoupe']['sans_fragment'])} — longueurs "
        f"{r['decoupe']['sans_fragment_longueurs']}. Introuvables au retrieval.",
        "",
        *section_kitsu(r["cible"]["kitsu"]),
        "## Écritures",
        "",
        "| Opération | Par source |",
        "|---|---|",
        *[f"| {k} | {dict(v) or '—'} |" for k, v in r["ecritures"].items()],
        "",
        "## État de `bench` avant / après",
        "",
        "| | Avant | Après |",
        "|---|---:|---:|",
        *[
            f"| {k} | {r['avant'].get(k, 0)} | {r['apres'].get(k, 0)} |"
            for k in sorted(set(r["avant"]) | set(r["apres"]))
        ],
        "",
        "## Contrôles du §7",
        "",
        "| Contrôle | Valeur |",
        "|---|---:|",
        *[
            f"| {k} | {len(v) if isinstance(v, list) else v} |"
            for k, v in r["controles"].items()
        ],
        "",
        "Fuites et coupures, par `doc_key` et empreinte du pseudonyme :",
        "",
        *[
            f"- fuite : `{d}` · `{h}` · {n}"
            for (d, h, n) in r["controles"]["7.4 fuites"]
        ],
        *[
            f"- coupure de fragment : `{d}` · `{h}`"
            for (d, h) in r["controles"]["7.4 coupures de fragment"]
        ],
        "",
        "## Empreintes de contenu (sans `chunk_id`)",
        "",
        "| | Documents | Fragments |",
        "|---|---|---|",
        f"| avant | `{r['empreinte_avant'][0]}` | `{r['empreinte_avant'][1]}` |",
        f"| après | `{r['empreinte_apres'][0]}` | `{r['empreinte_apres'][1]}` |",
        "",
    ]
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("\n".join(L), encoding="utf-8")


# --------------------------------------------------------------------------- #
#  Commande
# --------------------------------------------------------------------------- #


def executer(
    url: str,
    raw: Path = RAW_DEFAUT,
    donnees: Path = DONNEES_DEFAUT,
    dry_run: bool = False,
    a_blanc: bool = False,
    run_kitsu: Path = kitsu.RUN_DEFAUT,
) -> dict:
    """Le chargement complet ; rend le rapport. Lève si une garde ou un
    contrôle échoue — la transaction est alors annulée."""
    debut = time.monotonic()
    urls, raw_sha = lire_snapshot(raw)
    mode = "dry-run" if dry_run else "chargement"
    r: dict = {
        "mode": "à blanc (§7.3)" if a_blanc else mode,
        "horodatage": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "dsn": dsn_affichable(url),
        "raw": str(raw.relative_to(RACINE)) if raw.is_relative_to(RACINE) else str(raw),
        "raw_sha": raw_sha,
        "snapshot": len(urls),
        "donnees": (
            str(donnees.relative_to(RACINE))
            if donnees.is_relative_to(RACINE)
            else str(donnees)
        ),
    }
    with psycopg.connect(url) as connexion, connexion.cursor() as cur:
        r["serveur"] = cur.execute("SELECT version()").fetchone()[0].split(" on ")[0]
        cur.execute(
            "LOCK TABLE bench.corpus_docs, bench.corpus_chunks"
            " IN SHARE ROW EXCLUSIVE MODE"
        )
        cur.execute(SQL_TEMPORAIRES)
        with cur.copy("COPY snapshot_urls (review_url) FROM STDIN") as copie:
            for u in urls:
                copie.write_row((u,))

        g1_motif, g1_doublons, g5_absentes, g3 = cur.execute(SQL_GARDES).fetchone()
        gardes = {
            "G1 URL hors motif": g1_motif,
            "G1 site_id en double": g1_doublons,
            "G5 URL du snapshot absentes du référentiel": g5_absentes,
            "G3 critiques du snapshot sans série": g3,
        }
        if any(gardes.values()):
            raise ErreurChargement(f"garde en échec : {gardes}")

        r["clauses"] = dict(cur.execute(SQL_COMPTAGE).fetchall())
        docs, r["cible"] = construire_cible(cur, donnees, run_kitsu)
        fragments, r["decoupe"] = decouper_cible(docs)
        r["fragments"] = len(fragments)
        verser_cible(cur, docs, fragments)

        r["avant"] = dict(cur.execute(SQL_ETAT).fetchall())
        r["empreinte_avant"] = cur.execute(SQL_EMPREINTE).fetchone()
        r["ecritures"] = appliquer_diff(cur, a_blanc)
        r["apres"] = dict(cur.execute(SQL_ETAT).fetchall())
        r["empreinte_apres"] = cur.execute(SQL_EMPREINTE).fetchone()
        r["controles"] = controler(cur)

        en_echec = echecs(r["controles"])
        if en_echec:
            connexion.rollback()
            r["issue"] = f"ÉCHEC — ANNULÉ ({'; '.join(en_echec)})"
        elif dry_run or a_blanc:
            connexion.rollback()
            r["issue"] = "contrôles verts — ANNULÉ (aucune écriture validée)"
        else:
            connexion.commit()
            r["issue"] = "contrôles verts — VALIDÉ"
    r["duree"] = time.monotonic() - debut
    r["echecs"] = en_echec
    return r


@app.command()
def construire(
    # noqa: B008 — `typer.Option()` en défaut est l'API de Typer, pas un oubli.
    dry_run: bool = typer.Option(  # noqa: B008
        False, "--dry-run", help="Tout exécuter, contrôles compris, puis annuler."
    ),
    a_blanc: bool = typer.Option(  # noqa: B008
        False,
        "--a-blanc",
        help="Vider puis reconstruire tout le corpus, comparer, annuler (§7.3).",
    ),
    raw: Path = typer.Option(  # noqa: B008
        RAW_DEFAUT, help="JSONL des critiques du snapshot."
    ),
    donnees: Path = typer.Option(  # noqa: B008
        DONNEES_DEFAUT, help="Dossier des listes D5."
    ),
    rapports: Path = typer.Option(  # noqa: B008
        RAPPORTS_DEFAUT, help="Dossier des rapports."
    ),
    run_kitsu: Path = typer.Option(  # noqa: B008
        kitsu.RUN_DEFAUT, "--kitsu", help="Run Kitsu (manga.ndjson, staff, manifeste)."
    ),
) -> None:
    """Reconstruit le corpus RAG de `bench` selon la règle validée."""
    r = executer(
        dsn(),
        raw=raw,
        donnees=donnees,
        dry_run=dry_run,
        a_blanc=a_blanc,
        run_kitsu=run_kitsu,
    )
    suffixe = {"dry-run": "dryrun", "chargement": "execution"}.get(r["mode"], "a_blanc")
    chemin = rapports / f"corpus_{suffixe}_{r['horodatage']}.md"
    ecrire_rapport(chemin, r)
    typer.echo(f"{r['issue']} — rapport : {chemin}")
    for k, v in r["ecritures"].items():
        typer.echo(f"  {k:18} {dict(v) or '—'}")
    if a_blanc:
        identiques = r["empreinte_avant"] == r["empreinte_apres"]
        typer.echo(f"  empreintes avant = après : {identiques}")
        if not identiques:
            raise ErreurChargement("§7.3 — le rechargement complet change le corpus")
    if r["echecs"]:
        raise ErreurChargement(f"contrôles en échec : {r['echecs']}")


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
