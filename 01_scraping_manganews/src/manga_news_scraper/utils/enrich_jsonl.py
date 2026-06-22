import json, re, hashlib, unicodedata
from urllib.parse import urlparse
from datetime import datetime, timezone

SCHEMA_VERSION = "manganews.series.v1"
ENRICH_VERSION = "enrich_jsonl.v1"


# -------------------------
# Helpers de normalisation
# -------------------------
def norm_str(s: str) -> str | None:
    """Sans accents, uppercase, espaces normalisés. Retourne None si vide."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    # retire accents
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    # espaces et ponctuation un peu tolérante
    s = re.sub(r"\s+", " ", s).strip()
    return s.upper()

def clean_text(s: str) -> str | None:
    """Nettoyage simple texte (trim + espaces)."""
    if s is None:
        return None
    s = str(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None

def sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def extract_series_slug(url: str) -> str | None:
    """
    Ex:
    https://www.manga-news.com/index.php/serie/12345-et-Roku -> 12345-et-Roku
    """
    if not url:
        return None
    try:
        path = urlparse(url).path  # /index.php/serie/12345-et-Roku
        m = re.search(r"/serie/([^/?#]+)", path)
        return m.group(1) if m else None
    except Exception:
        return None

def parse_origine(origine: str):
    """
    Ex:
      "Japon - 2014" -> ("Japon", 2014)
      "France - 2016" -> ("France", 2016)
      "Japon" -> ("Japon", None)
      "Japon (2014)" -> ("Japon", 2014)
    """
    if not origine:
        return (None, None)

    o = clean_text(origine)
    if not o:
        return (None, None)

    # normalise les tirets typographiques
    o = o.replace("–", "-").replace("—", "-")

    # 1) pays = tout avant le premier "-" (si présent)
    parts = re.split(r"\s*-\s*", o, maxsplit=1)
    country = clean_text(parts[0]) if parts else None

    # 2) année = premier 4 chiffres trouvés (si présent)
    year_int = None
    m = re.search(r"\b(19|20)\d{2}\b", o)
    if m:
        year_int = int(m.group(0))

    return (country, year_int)


def align_parallel_lists(a, b):
    """
    Force genres / genres_urls à être cohérents.
    - Si mismatch: on zippe au min et on garde seulement les paires valides.
    """
    a = a or []
    b = b or []
    if not isinstance(a, list):
        a = [a]
    if not isinstance(b, list):
        b = [b]

    pairs = [(x, y) for x, y in zip(a, b) if x and y]
    a2 = [x for x, _ in pairs]
    b2 = [y for _, y in pairs]
    return a2, b2

def build_rag_text(row: dict) -> str:
    parts = []
    def add(label, value):
        if value is None:
            return
        if isinstance(value, list):
            value = " | ".join(v for v in value if v)
        value = clean_text(value)
        if value:
            parts.append(f"[{label}] {value}")

    add("TITRE", row.get("title_page"))
    add("TYPE", row.get("type"))
    add("GENRES", row.get("genres"))
    # origine affichage + champs parsés
    add("ORIGINE", row.get("origine"))
    if row.get("origin_country"):
        add("ORIGINE_PAYS", row.get("origin_country"))
    if row.get("origin_year"):
        add("ORIGINE_ANNEE", str(row.get("origin_year")))
    add("RESUME", row.get("resume"))
    # si plus tard tu ajoutes auteurs/éditeurs :
    # add("AUTEURS", row.get("authors"))
    # add("EDITEUR", row.get("publisher"))

    return "\n".join(parts)

def enrich_row(row: dict) -> tuple[dict, list[str]]:
    """
    Retourne (row_enrichi, erreurs)
    """
    errors = []


    # --- provenance / versions ---
    row["schema_version"] = row.get("schema_version") or SCHEMA_VERSION
    row["enrich_version"] = row.get("enrich_version") or ENRICH_VERSION

    # timestamp ISO en UTC (stable, comparable)
    if not row.get("scraped_at"):
        row["scraped_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")


    url = row.get("url")
    if not url:
        errors.append("missing:url")

    # 1) Clés stables
    row["series_slug"] = row.get("series_slug") or extract_series_slug(url)
    if url:
        row["source_id"] = row.get("source_id") or sha1_hex(url)

    # 2) Listes parallèles
    genres, genres_urls = align_parallel_lists(row.get("genres"), row.get("genres_urls"))
    if (row.get("genres") or []) and (row.get("genres_urls") or []) and len((row.get("genres") or [])) != len((row.get("genres_urls") or [])):
        errors.append("mismatch:genres_vs_genres_urls")
    row["genres"] = genres
    row["genres_urls"] = genres_urls

    # 3) Origine parsée
    origin_country, origin_year = parse_origine(row.get("origine"))
    row["origin_country"] = origin_country
    row["origin_year"] = origin_year

    row["origin_has_year"] = row["origin_year"] is not None

    # 4) Normalisations (ex: utile pour matching MS/Kitsu)
    row["title_page_norm"] = norm_str(row.get("title_page"))
    row["type_norm"] = norm_str(row.get("type"))
    row["origin_country_norm"] = norm_str(origin_country)
    row["genres_norm"] = [norm_str(g) for g in genres if norm_str(g)]

    # 5) Résumé clean + flags
    resume = clean_text(row.get("resume"))
    row["resume"] = resume
    row["has_resume"] = bool(resume)

    # 6) rag_text “sectionné” robuste
    row["rag_text"] = build_rag_text(row)
    row["rag_char_len"] = len(row["rag_text"])
    row["indexable_rag"] = row["rag_char_len"] >= 200  # seuil ajustable

    # 7) validation basique
    if not row.get("title_page"):
        errors.append("missing:title_page")
    if not row.get("rag_text"):
        errors.append("missing:rag_text")

    return row, errors


def enrich_item(row: dict) -> dict:
    enriched, _errors = enrich_row(row)
    return enriched

# -------------------------
# Run sur un JSONL
# -------------------------
def _run_jsonl(in_path: str, out_path: str) -> None:
    total = 0
    err_counter = {}
    missing_resume = 0
    not_indexable = 0

    with open(in_path, "r", encoding="utf-8") as f_in, open(out_path, "w", encoding="utf-8") as f_out:
        for line in f_in:
            total += 1
            row = json.loads(line)

            row2, errors = enrich_row(row)
            if not row2.get("has_resume"):
                missing_resume += 1
            if not row2.get("indexable_rag"):
                not_indexable += 1

            for e in errors:
                err_counter[e] = err_counter.get(e, 0) + 1

            f_out.write(json.dumps(row2, ensure_ascii=False) + "\n")

    print("OK enrich:", total, "rows ->", out_path)
    print("missing resume:", missing_resume)
    print("not indexable (rag_char_len<200):", not_indexable)
    print("errors:", err_counter)


if __name__ == "__main__":
    _run_jsonl("test.jsonl", "test.enriched.jsonl")
