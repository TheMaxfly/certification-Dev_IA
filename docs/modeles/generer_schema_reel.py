"""Ecrit `schema_reel.md` : l'inventaire du schema REEL, table par table.

C'est la PREUVE DE FIDELITE des planches : tout ce qui est dessine doit se
retrouver ici, et ce fichier ne contient que ce que la base a repondu. Il porte
aussi la confrontation base <-> migrations `000` a `011`.

    uv run python docs/modeles/generer_schema_reel.py
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

ICI = Path(__file__).resolve().parent
RACINE = ICI.parents[1]
MIGRATIONS = RACINE / "database" / "migrations"

MOTIF_CREATE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_]+\.[a-z_0-9]+)", re.I
)


def tables_des_migrations() -> tuple[dict[str, set[str]], set[str]]:
    """Tables declarees par les migrations, en ignorant les lignes commentees."""
    par_fichier: dict[str, set[str]] = {}
    toutes: set[str] = set()
    for fichier in sorted(MIGRATIONS.glob("*.sql")):
        trouvees: set[str] = set()
        for ligne in fichier.read_text(encoding="utf-8").splitlines():
            if ligne.strip().startswith("--"):
                continue
            trouvees |= {m.lower() for m in MOTIF_CREATE.findall(ligne)}
        par_fichier[fichier.name] = trouvees
        toutes |= trouvees
    return par_fichier, toutes


def main() -> int:
    brut = json.loads((ICI / "schema.json").read_text(encoding="utf-8"))

    colonnes: dict[str, list[dict]] = {}
    for c in brut["colonnes"]:
        colonnes.setdefault(f"{c['table_schema']}.{c['table_name']}", []).append(c)
    index: dict[str, list[dict]] = {}
    for i in brut["index"]:
        index.setdefault(f"{i['schema']}.{i['table']}", []).append(i)
    contraintes: dict[str, list[dict]] = {}
    for k in brut["contraintes"]:
        contraintes.setdefault(f"{k['schema']}.{k['table']}", []).append(k)
    fk_par_table: dict[str, list[dict]] = {}
    for f in brut["fk"]:
        fk_par_table.setdefault(f"{f['schema_source']}.{f['table_source']}", []).append(
            f
        )

    base_tables = sorted(
        f"{t['table_schema']}.{t['table_name']}"
        for t in brut["tables"]
        if t["table_type"] == "BASE TABLE"
    )
    vues = sorted(
        f"{t['table_schema']}.{t['table_name']}"
        for t in brut["tables"]
        if t["table_type"] == "VIEW"
    )
    comptes = brut["comptes"]

    par_fichier, declarees = tables_des_migrations()
    pertinentes = {t for t in declarees if t.split(".")[0] in ("manga", "staging")}
    en_trop = sorted(set(base_tables) - pertinentes)
    manquantes = sorted(pertinentes - set(base_tables))

    out: list[str] = []
    a = out.append
    a("# Schema reel — inventaire extrait de la base")
    a("")
    a(
        f"> Extrait de `apimanga` le {date.today().isoformat()} par "
        "`extraire_schema.py` (lecture seule : uniquement des SELECT sur "
        "`information_schema` et `pg_catalog`)."
    )
    a("")
    a(
        "Ce fichier est la **source** des planches de `modele_donnees.drawio` "
        "et la **preuve de fidelite** : rien n'y est saisi a la main, tout vient "
        "de la base. Une table dessinee qui ne figurerait pas ici serait une "
        "invention."
    )
    a("")
    a("## Chiffres")
    a("")
    a("| Element | Nombre |")
    a("| --- | --- |")
    a(f"| Tables `manga` | {sum(1 for t in base_tables if t.startswith('manga.'))} |")
    a(
        f"| Tables `staging` | "
        f"{sum(1 for t in base_tables if t.startswith('staging.'))} |"
    )
    a(f"| Vues (`manga`) | {len(vues)} |")
    a(f"| Colonnes (total) | {len(brut['colonnes'])} |")
    a(f"| Cles etrangeres | {len(brut['fk'])} |")
    a(f"| Index (dont UNIQUE) | {len(brut['index'])} |")
    a(f"| Contraintes declarees | {len(brut['contraintes'])} |")
    a("")

    a("## Confrontation base <-> migrations `000` a `011`")
    a("")
    a(
        f"- tables `manga` + `staging` **declarees par les migrations** : "
        f"**{len(pertinentes)}**"
    )
    a(f"- tables `manga` + `staging` **presentes en base** : **{len(base_tables)}**")
    a("")
    if not en_trop and not manquantes:
        a(
            "**Aucun ecart.** Chaque table de la base est creee par une migration "
            "du depot, et chaque table declaree existe en base. Le depot sait "
            "reconstruire ce qu'il decrit."
        )
    else:
        a("### ⚠️ Ecarts constates (signales, non corriges)")
        a("")
        if en_trop:
            a("**En base mais absentes des migrations :**")
            a("")
            for t in en_trop:
                a(f"- `{t}`")
            a("")
        if manquantes:
            a("**Declarees par une migration mais absentes en base :**")
            a("")
            for t in manquantes:
                a(f"- `{t}`")
            a("")
    a("")
    a("### Origine de chaque table")
    a("")
    a("| Migration | Tables creees |")
    a("| --- | --- |")
    for nom, cles in par_fichier.items():
        retenues = sorted(c for c in cles if c.split(".")[0] in ("manga", "staging"))
        if retenues:
            a(f"| `{nom}` | {', '.join(f'`{c}`' for c in retenues)} |")
    a("")

    a("## Vues")
    a("")
    a("| Vue | Definie sur |")
    a("| --- | --- |")
    for v in brut["vues"]:
        cible = ", ".join(
            sorted(
                set(re.findall(r"\b(manga\.[a-z_0-9]+)", v["definition"], re.I))
            )
        )
        a(f"| `{v['schema']}.{v['nom']}` | {cible or '—'} |")
    a("")

    a("## Cles etrangeres (toutes)")
    a("")
    a("| Source | Colonnes | Cible | Colonnes cible |")
    a("| --- | --- | --- | --- |")
    for f in brut["fk"]:
        a(
            f"| `{f['schema_source']}.{f['table_source']}` "
            f"| {', '.join(f['colonnes_source'])} "
            f"| `{f['schema_cible']}.{f['table_cible']}` "
            f"| {', '.join(f['colonnes_cible'])} |"
        )
    a("")

    a("## Detail par table")
    a("")
    for cle in base_tables:
        a(f"### `{cle}`")
        a("")
        a(f"{comptes.get(cle, '?')} lignes.")
        a("")
        a("| Colonne | Type | Null | Defaut |")
        a("| --- | --- | --- | --- |")
        for c in colonnes.get(cle, []):
            defaut = (c["column_default"] or "").replace("|", "\\|")
            if len(defaut) > 40:
                defaut = defaut[:37] + "..."
            a(
                f"| `{c['column_name']}` | {c['udt_name']} "
                f"| {'oui' if c['is_nullable'] == 'YES' else 'NON'} | {defaut} |"
            )
        a("")
        uniques = [i for i in index.get(cle, []) if i["unique"] and not i["primaire"]]
        primaires = [i for i in index.get(cle, []) if i["primaire"]]
        if primaires:
            a(f"- **PK** : `{primaires[0]['definition']}`")
        for i in uniques:
            partiel = " *(partiel)*" if " WHERE " in i["definition"] else ""
            a(f"- **UNIQUE**{partiel} : `{i['definition']}`")
        for f in fk_par_table.get(cle, []):
            a(
                f"- **FK** : ({', '.join(f['colonnes_source'])}) -> "
                f"`{f['schema_cible']}.{f['table_cible']}`"
                f"({', '.join(f['colonnes_cible'])})"
            )
        for k in contraintes.get(cle, []):
            if k["type"] == "c":
                a(f"- **CHECK** `{k['nom']}` : `{k['definition']}`")
        a("")

    (ICI / "schema_reel.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(
        f"schema_reel.md ecrit : {len(base_tables)} tables, {len(vues)} vues, "
        f"{len(brut['fk'])} FK ; ecarts migrations = "
        f"{len(en_trop) + len(manquantes)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
