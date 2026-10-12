"""Mesures du corpus reconstruit — §8 de la spec, et A3 (auto-référence).

    uv run python -m corpus.mesurer

LECTURE SEULE, imposée à la connexion : `default_transaction_read_only=on`
couvre toutes les transactions, la première comprise. Mesurer n'écrit rien, pas
même par accident. (Corrigé le 2026-09-29 : un `SET SESSION CHARACTERISTICS`
exécuté après connexion laissait la transaction courante en écriture.)

A3 — MESURÉ, JAMAIS FILTRÉ (§5.3). Les marqueurs M1–M6 ont été validés avec la
règle le 2026-09-28. Leur taux sert l'arbitrage RGPD ; aucune critique n'est
écartée ici. Les marqueurs portent sur le CORPS SOURCE des critiques retenues
(avant masquage), puisque c'est ce texte-là que l'arbitrage doit juger.

DEUX SORTIES, ET LA FRONTIÈRE ENTRE ELLES
  - le rapport (taux, comptes, distributions) : versionné, dépôt public ;
  - les exemples d'auto-référence : un extrait de texte qui dit « j'ai 34 ans »
    ou « j'habite à Lyon » est précisément ce que l'anonymisation doit juger.
    Il ne va PAS dans le dépôt public : fichier local, sous `data/`, exclu du
    dépôt. Les pseudonymes y sont masqués.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

from corpus import pseudonymes
from corpus.construire import RACINE, RAPPORTS_DEFAUT, dsn_affichable
from evaluation.atteignabilite import corpus_lu

EXEMPLES_DEFAUT = RACINE / "05_nettoyage_agregation_bdd/data/corpus_exemples"
DIMENSIONS = (384, 768, 1024)
PAR_MARQUEUR = 5

#: Motifs validés (ARE PostgreSQL, casse ignorée). M1 est calculé à part : il
#: dépend de la liste des pseudonymes.
MARQUEURS = {
    "M2 âge": r"\mj['’]ai\s+[0-9]{1,2}\s+ans\M",
    "M3 lieu de vie": (
        r"\m(j['’]habite|je vis|je vivais|j['’]ai grandi)\s+(à|a|en|au|dans)\M"
    ),
    "M4 prénom": r"\m(je m['’]appelle|mon prénom)\M",
    "M5 entourage / métier": (
        r"\m(mon|ma|mes)\s+(fils|fille|femme|mari|copine|copain|enfants?|neveu"
        r"|nièce|métier|boulot)\M"
    ),
    "M6a collègue": r"\m(mon|ma)\s+collègue\M",
    "M6b merci à": r"\mmerci à\M",
    "M6c chroniqueur": r"\mchroniqueu(r|se)\M",
    "M6d signature finale": r"\(?par\s+\S+\s*\)?\s*$",
}

app = typer.Typer(add_completion=False, help=__doc__)

CTE_CORPS = """
corps AS (
  SELECT d.doc_key, btrim(a.review_body) AS corps
  FROM bench.corpus_docs d
  JOIN manga.ms_reviews_all a
    ON substring(a.review_url FROM 'id=([0-9]+)$')::bigint
       = (d.metadata_json ->> 'site_id')::bigint
  WHERE d.corpus_id = %(corpus)s AND d.source = 'ms_review')"""

SQL_LONGUEURS = f"""
WITH {CTE_CORPS}
SELECT count(*), min(length(corps)),
       percentile_disc(0.5) WITHIN GROUP (ORDER BY length(corps)),
       percentile_disc(0.95) WITHIN GROUP (ORDER BY length(corps)),
       max(length(corps)),
       count(*) FILTER (WHERE length(corps) < 80),
       count(*) FILTER (WHERE length(corps) < 200)
FROM corps
"""

SQL_FRAGMENTS = """
SELECT d.source, count(DISTINCT d.doc_key), count(k.chunk_id),
       count(DISTINCT d.doc_key) FILTER (WHERE k.chunk_id IS NULL)
FROM bench.corpus_docs d
LEFT JOIN bench.corpus_chunks k ON k.corpus_id = d.corpus_id AND k.doc_key = d.doc_key
WHERE d.corpus_id = %(corpus)s
GROUP BY d.source ORDER BY d.source
"""

SQL_M1 = f"""
WITH {CTE_CORPS},
pseudos AS (
  SELECT DISTINCT review_author AS p,
    '\\m'
      || regexp_replace(review_author, '([.^$*+?()\\[\\]{{}}|\\\\-])', '\\\\\\1', 'g')
      || '\\M' AS motif
  FROM manga.ms_reviews_all WHERE review_author IS NOT NULL)
SELECT count(DISTINCT c.doc_key) FROM corps c JOIN pseudos x ON c.corps ~* x.motif
"""

SQL_MARQUEUR = f"""
WITH {CTE_CORPS}
SELECT count(*) FILTER (WHERE corps ~* %(motif)s) FROM corps
"""

SQL_AU_MOINS_UN = f"""
WITH {CTE_CORPS}
SELECT count(*) FILTER (WHERE corps ~* ANY (%(motifs)s)) FROM corps
"""

SQL_EXEMPLES = f"""
WITH {CTE_CORPS}
SELECT doc_key,
       substring(corps
                 FROM greatest(regexp_instr(corps, %(motif)s, 1, 1, 0, 'i') - 70, 1)
                 FOR 180)
FROM corps WHERE corps ~* %(motif)s
ORDER BY doc_key LIMIT %(n)s
"""


def pourcent(n: int, total: int) -> str:
    return f"{100 * n / total:.2f} %".replace(".", ",") if total else "—"


def nombre(n: int | float) -> str:
    return f"{n:_}".replace("_", " ")


def mesurer(url: str) -> tuple[dict, dict]:
    m: dict = {"horodatage": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")}
    exemples: dict = {}
    # Lecture seule dès la PREMIÈRE requête : `SET SESSION CHARACTERISTICS` ne
    # vaudrait que pour les transactions suivantes, pas pour celle qu'il ouvre.
    with psycopg.connect(url, options="-c default_transaction_read_only=on") as cx:
        m["serveur"] = cx.execute("SELECT version()").fetchone()[0].split(" on ")[0]
        m["dsn"] = dsn_affichable(url)
        # Le corpus mesuré : celui de l'encodage en service (migration 021).
        c = {"corpus": corpus_lu(cx)}
        m["corpus"] = c["corpus"]
        m["longueurs"] = cx.execute(SQL_LONGUEURS, c).fetchone()
        m["fragments"] = cx.execute(SQL_FRAGMENTS, c).fetchall()
        m["M1"] = cx.execute(SQL_M1, c).fetchone()[0]
        m["marqueurs"] = {
            nom: cx.execute(SQL_MARQUEUR, c | {"motif": motif}).fetchone()[0]
            for nom, motif in MARQUEURS.items()
        }
        m["au_moins_un_M2_M6"] = cx.execute(
            SQL_AU_MOINS_UN, c | {"motifs": list(MARQUEURS.values())}
        ).fetchone()[0]
        pseudos = [
            p
            for (p,) in cx.execute(
                "SELECT DISTINCT review_author FROM manga.ms_reviews_all"
                " WHERE review_author IS NOT NULL"
            )
        ]
        for nom, motif in MARQUEURS.items():
            lignes = cx.execute(
                SQL_EXEMPLES, c | {"motif": motif, "n": PAR_MARQUEUR}
            ).fetchall()
            masques = []
            for doc_key, extrait in lignes:
                for p in pseudos:
                    extrait = pseudonymes.motif(p).sub("[PSEUDO]", extrait)
                masques.append((doc_key, " ".join(extrait.split())))
            exemples[nom] = masques
    return m, exemples


def ecrire_rapport(chemin: Path, m: dict) -> None:
    n, lmin, lmed, lp95, lmax, lt80, lt200 = m["longueurs"]
    total_fragments = sum(f[2] for f in m["fragments"])
    L = [
        "# Corpus reconstruit — mesures (§8) et auto-référence (A3)",
        "",
        f"Horodatage : `{m['horodatage']}` · {m['serveur']} · `{m['dsn']}` · "
        "session en lecture seule",
        "",
        "## Longueur des critiques retenues (corps, sans le préfixe de titre)",
        "",
        "| Critiques | Min | Médiane | p95 | Max | < 80 car. | < 200 car. |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        f"| {nombre(n)} | {lmin} | {nombre(lmed)} | {nombre(lp95)} | {nombre(lmax)} "
        f"| {lt80} ({pourcent(lt80, n)}) | {lt200} ({pourcent(lt200, n)}) |",
        "",
        "## Fragments par type de document",
        "",
        "| Type | Documents | Fragments | Documents sans fragment |",
        "|---|---:|---:|---:|",
        *[
            f"| {s} | {nombre(d)} | {nombre(f)} | {nombre(sans)} |"
            for s, d, f, sans in m["fragments"]
        ],
        f"| **total** | {nombre(sum(f[1] for f in m['fragments']))} "
        f"| **{nombre(total_fragments)}** "
        f"| {nombre(sum(f[3] for f in m['fragments']))} |",
        "",
        "## Taille estimée de l'index — flottants 32 bits, vecteurs seuls",
        "",
        "| Dimensions | Octets | Mo |",
        "|---:|---:|---:|",
        *[
            f"| {d} | {nombre(total_fragments * d * 4)} "
            f"| {total_fragments * d * 4 / 1_000_000:.1f} |".replace(".", ",")
            for d in DIMENSIONS
        ],
        "",
        "Hors métadonnées et hors structure d'index (HNSW, IVF…) : un plancher, "
        "pas un devis.",
        "",
        "## A3 — marqueurs d'auto-référence (mesurés, aucun filtrage)",
        "",
        f"Sur les {nombre(n)} critiques retenues, corps SOURCE (avant masquage).",
        "",
        "| Marqueur | Critiques | Taux |",
        "|---|---:|---:|",
        f"| M1 pseudonyme d'un membre (brut, homonymes compris) | {m['M1']} "
        f"| {pourcent(m['M1'], n)} |",
        *[f"| {nom} | {v} | {pourcent(v, n)} |" for nom, v in m["marqueurs"].items()],
        f"| **au moins un de M2–M6** | **{m['au_moins_un_M2_M6']}** "
        f"| **{pourcent(m['au_moins_un_M2_M6'], n)}** |",
        "",
        "M1 compte des homonymes (personnages, mangakas) : les vraies références "
        "à un membre sont celles de la liste de masquage (D5). Les motifs M2–M6 "
        "sont des marqueurs, pas des preuves : leur taux de faux positifs n'est "
        "pas mesuré ici. Les exemples, qui contiennent du texte à juger, restent "
        "hors dépôt.",
        "",
    ]
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("\n".join(L), encoding="utf-8")


def ecrire_exemples(chemin: Path, m: dict, exemples: dict) -> None:
    L = [
        "# Auto-référence — exemples (LOCAL, hors dépôt)",
        "",
        f"Mesure `{m['horodatage']}`. {PAR_MARQUEUR} premiers par marqueur, "
        "ordre des `doc_key`, pseudonymes masqués. Pour l'arbitrage RGPD.",
        "",
    ]
    for nom, lignes in exemples.items():
        L += [f"## {nom} — {m['marqueurs'][nom]} critiques", ""]
        L += [f"- `{d}` — « {e} »" for d, e in lignes] or ["- (aucune)"]
        L.append("")
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("\n".join(L), encoding="utf-8")


@app.command()
def principal(
    # noqa: B008 — `typer.Option()` en défaut est l'API de Typer, pas un oubli.
    rapports: Path = typer.Option(  # noqa: B008
        RAPPORTS_DEFAUT, help="Dossier du rapport versionné."
    ),
    exemples: Path = typer.Option(  # noqa: B008
        EXEMPLES_DEFAUT, help="Dossier LOCAL des exemples (hors dépôt)."
    ),
) -> None:
    """Mesure le corpus de `bench` sans rien écrire."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise typer.BadParameter("DATABASE_URL n'est pas définie.")
    m, ex = mesurer(url)
    rapport = rapports / f"corpus_mesures_{m['horodatage']}.md"
    ecrire_rapport(rapport, m)
    fichier_exemples = exemples / f"corpus_autoreference_exemples_{m['horodatage']}.md"
    ecrire_exemples(fichier_exemples, m, ex)
    typer.echo(f"rapport : {rapport}\nexemples (local) : {fichier_exemples}")


def main() -> int:
    try:
        app()
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
