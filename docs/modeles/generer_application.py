"""Ecrit `application.md` : le modele de donnees du schema `application`.

Le schema de l'application du libraire (module 12 ; migrations 027 et 029) :
tables, colonnes, cles, contraintes, cascades, index, droits par role, et le
diagramme Mermaid (`erDiagram`) dans le meme fichier. Tout vient de
`schema.json` (l'extraction en lecture seule de `extraire_schema.py`), les
commentaires compris (`COMMENT ON` des migrations) ; rien n'est saisi a la
main, sauf les deux liens applicatifs vers le catalogue, que la base ne peut
pas porter (pas de cle etrangere entre les schemas : voir leur commentaire).

    uv run python docs/modeles/generer_application.py
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ICI = Path(__file__).resolve().parent
SCHEMA = "application"

#: Ce que fait la suppression de la ligne visee, selon `confdeltype`.
SUPPRESSION = {
    "a": "NO ACTION (refusee)",
    "r": "RESTRICT (refusee)",
    "c": "CASCADE (suivie)",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}

#: La regle, telle que l'ecrit `pg_get_constraintdef`, pour les liens du diagramme.
REGLE = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}

#: Les liens sans cle etrangere vers le catalogue : (table, colonne) -> cible. Ils
#: sont dits par les commentaires de la base (`critiques.series_id`) ; un lien dont
#: la colonne n'existe plus fait echouer la generation.
LIENS_APPLICATIFS = {
    ("critiques", "series_id"): "manga.ms_series_enriched",
    ("listes", "series_id"): "manga.ms_series_enriched",
}


def echapper(texte: str | None) -> str:
    return (texte or "").replace("|", "\\|").replace("\n", " ")


def nom_mermaid(qualifie: str) -> str:
    schema, table = qualifie.split(".", 1)
    return table if schema == SCHEMA else f"{schema}_{table}"


def main() -> int:  # noqa: C901 — une page, section par section
    brut = json.loads((ICI / "schema.json").read_text(encoding="utf-8"))
    source = brut["source"]

    tables = [
        t
        for t in brut["tables"]
        if t["table_schema"] == SCHEMA and t["table_type"] == "BASE TABLE"
    ]
    noms = [t["table_name"] for t in tables]
    colonnes: dict[str, list[dict]] = {n: [] for n in noms}
    for c in brut["colonnes"]:
        if c["table_schema"] == SCHEMA:
            colonnes[c["table_name"]].append(c)
    contraintes: dict[str, list[dict]] = {n: [] for n in noms}
    for k in brut["contraintes"]:
        if k["schema"] == SCHEMA:
            contraintes[k["table"]].append(k)
    index: dict[str, list[dict]] = {n: [] for n in noms}
    for i in brut["index"]:
        if i["schema"] == SCHEMA:
            index[i["table"]].append(i)
    fk = [f for f in brut["fk"] if f["schema_source"] == SCHEMA]
    droits = [d for d in brut.get("droits", []) if d["schema"] == SCHEMA]

    for (table, colonne), _cible in LIENS_APPLICATIFS.items():
        if colonne not in {c["column_name"] for c in colonnes.get(table, [])}:
            raise SystemExit(f"lien applicatif inconnu : {SCHEMA}.{table}.{colonne}")

    cles_primaires = {
        n: {
            col.strip()
            for k in contraintes[n]
            if k["type"] == "p"
            for col in k["definition"].removeprefix("PRIMARY KEY (")[:-1].split(",")
        }
        for n in noms
    }
    cles_etrangeres = {
        (f["table_source"], col) for f in fk for col in f["colonnes_source"]
    }
    uniques = {
        (k["table"], k["definition"].removeprefix("UNIQUE (")[:-1])
        for k in brut["contraintes"]
        if k["schema"] == SCHEMA and k["type"] == "u"
    }

    out: list[str] = []
    a = out.append
    a("# Modele de donnees — schema `application`")
    a("")
    a(
        f"> Genere le {date.today().isoformat()} par `generer_application.py` depuis "
        f"`schema.json`, extrait de `{source['base']}` sous le role "
        f"`{source['role']}` en lecture seule. Rien n'est saisi a la main : tables, "
        "colonnes, contraintes, droits et commentaires sont ceux que la base a "
        "rendus."
    )
    a("")
    ecrivains = sorted(
        {
            d["role"]
            for d in droits
            if {"INSERT", "UPDATE", "DELETE"} & set(d["privileges"])
        }
    )
    a(
        "Le schema des donnees de l'application du libraire (module "
        "`12_application`) : cree par la migration `027`, complete par la `029` "
        "(`questions_jour`, le quota des comptes). Il est separe du catalogue "
        "(`manga`) et du corpus (`bench`) : aucune cle etrangere ne les relie, et "
        + (
            f"un seul role y ecrit, `{ecrivains[0]}`."
            if len(ecrivains) == 1
            else "les roles qui y ecrivent : "
            + ", ".join(f"`{r}`" for r in ecrivains)
            + "."
        )
    )
    a("")
    a("## Chiffres")
    a("")
    a("| Element | Nombre |")
    a("| --- | --- |")
    a(f"| Tables | {len(tables)} |")
    a(f"| Colonnes | {sum(len(v) for v in colonnes.values())} |")
    a(f"| Cles etrangeres (internes au schema) | {len(fk)} |")
    a(
        "| Contraintes CHECK | "
        f"{sum(1 for v in contraintes.values() for k in v if k['type'] == 'c')} |"
    )
    a(f"| Index (cles comprises) | {sum(len(v) for v in index.values())} |")
    a(f"| Liens applicatifs vers le catalogue (sans cle) | {len(LIENS_APPLICATIFS)} |")
    a("")

    a("## Diagramme")
    a("")
    a(
        "Trait plein : cle etrangere reelle ; pointille : lien applicatif, sans "
        "cle (l'identifiant du catalogue est valide par l'API de donnees a "
        "l'ecriture). `PK` cle primaire, `FK` cle etrangere, `UK` unique."
    )
    a("")
    a("```mermaid")
    a("erDiagram")
    for n in noms:
        a(f"    {n} {{")
        for c in colonnes[n]:
            marques = []
            if c["column_name"] in cles_primaires[n]:
                marques.append("PK")
            if (n, c["column_name"]) in cles_etrangeres:
                marques.append("FK")
            if (n, c["column_name"]) in uniques:
                marques.append("UK")
            ligne = f"        {c['udt_name']} {c['column_name']}"
            if marques:
                ligne += " " + ", ".join(marques)
            if c["is_nullable"] == "NO" and "PK" not in marques:
                ligne += ' "non nul"'
            a(ligne)
        a("    }")
    cibles_externes = sorted(set(LIENS_APPLICATIFS.values()))
    for cible in cibles_externes:
        schema_c, table_c = cible.split(".", 1)
        a(f"    {nom_mermaid(cible)} {{")
        for (_table, colonne), visee in LIENS_APPLICATIFS.items():
            if visee != cible:
                continue
            type_c = next(
                c["udt_name"]
                for c in brut["colonnes"]
                if (c["table_schema"], c["table_name"], c["column_name"])
                == (schema_c, table_c, colonne)
            )
            a(f"        {type_c} {colonne}")
            break
        a("    }")
    for f in fk:
        source_nullable = any(
            c["is_nullable"] == "YES"
            for c in colonnes[f["table_source"]]
            if c["column_name"] in f["colonnes_source"]
        )
        gauche = "|o" if source_nullable else "||"
        regle = REGLE[f["on_delete"]]
        a(
            f"    {nom_mermaid(f['schema_cible'] + '.' + f['table_cible'])} "
            f'{gauche}--o{{ {f["table_source"]} : "{", ".join(f["colonnes_source"])}'
            f' ({regle})"'
        )
    for (table, colonne), cible in LIENS_APPLICATIFS.items():
        a(f'    {nom_mermaid(cible)} ||..o{{ {table} : "{colonne} (sans cle)"')
    a("```")
    a("")

    visees = sorted({f"{f['schema_cible']}.{f['table_cible']}" for f in fk})
    if len(visees) == 1:
        a(f"## Cascades — ce que fait la suppression d'une ligne de `{visees[0]}`")
    else:
        a("## Cascades — ce que fait la suppression de la ligne visee")
    a("")
    a("| Table | Colonne | Vers | A la suppression de la ligne visee |")
    a("| --- | --- | --- | --- |")
    for f in fk:
        a(
            f"| `{f['table_source']}` | {', '.join(f['colonnes_source'])} "
            f"| `{f['schema_cible']}.{f['table_cible']}`"
            f"({', '.join(f['colonnes_cible'])}) | {SUPPRESSION[f['on_delete']]} |"
        )
    a("")

    a("## Droits par role")
    a("")
    a("| Objet | Type | Proprietaire | Role | Droits |")
    a("| --- | --- | --- | --- | --- |")
    for d in droits:
        a(
            f"| `{d['objet']}` | {d['type']} | `{d['proprietaire']}` "
            f"| `{d['role']}` | {', '.join(d['privileges'])} |"
        )
    a("")
    a(
        "Aucun autre role n'a de droit sur le schema : ni `PUBLIC`, ni les roles "
        "de lecture du catalogue et du corpus. Un droit absent est un refus."
    )
    a("")

    a("## Tables")
    a("")
    for t in tables:
        n = t["table_name"]
        a(f"### `{SCHEMA}.{n}`")
        a("")
        if t.get("commentaire"):
            a(f"> {t['commentaire']}")
            a("")
        a("| Colonne | Type | Null | Defaut | Commentaire |")
        a("| --- | --- | --- | --- | --- |")
        for c in colonnes[n]:
            a(
                f"| `{c['column_name']}` | {c['udt_name']} "
                f"| {'oui' if c['is_nullable'] == 'YES' else 'NON'} "
                f"| {echapper(c['column_default'])} "
                f"| {echapper(c.get('commentaire'))} |"
            )
        a("")
        ordre = sorted(
            contraintes[n], key=lambda k: ("pufc".index(k["type"]), k["nom"])
        )
        for k in ordre:
            etiquette = {"p": "PK", "u": "UNIQUE", "f": "FK", "c": "CHECK"}[k["type"]]
            a(f"- **{etiquette}** `{k['nom']}` : `{k['definition']}`")
        for i in index[n]:
            if i["primaire"] or any(i["index"] == k["nom"] for k in contraintes[n]):
                continue
            genre = "UNIQUE" if i["unique"] else "index"
            partiel = " *(partiel)*" if " WHERE " in i["definition"] else ""
            a(f"- **{genre}**{partiel} `{i['index']}` : `{i['definition']}`")
        a("")

    (ICI / "application.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(
        f"application.md ecrit : {len(tables)} tables, {len(fk)} FK, "
        f"{len(droits)} droits"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
