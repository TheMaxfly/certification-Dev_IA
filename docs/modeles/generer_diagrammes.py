"""Genere les planches (draw.io + PNG + Mermaid) a partir du schema REEL.

Entree : `schema.json`, produit par `extraire_schema.py` (lecture seule sur
apimanga). Aucune table, aucune colonne, aucune FK n'est ecrite a la main ici :
tout vient de l'extraction. Les seules decisions humaines sont la SELECTION des
tables a montrer et leur PLACEMENT.

    uv run --with cairosvg python docs/modeles/generer_diagrammes.py
"""

from __future__ import annotations

import hashlib
import html
import json
import sys
from pathlib import Path

from layout import (
    FAMILLES,
    HAUTEUR_ENTETE,
    HAUTEUR_LIGNE,
    PAGE_H,
    PAGE_L,
    Boite,
    Colonne,
    Lien,
    ancres,
    route,
    verifier_disposition,
)

ICI = Path(__file__).resolve().parent

# ------------------------------------------------------------- perimetre ---
# Planche 1 : le coeur d'identite et les tables TYPEES des 4 sources.
# Volontairement absentes (note portee sur la planche) : le schema `bench.*`
# (module 06, experimental), les tables et vues RAG heritage
# (`rag_*`, `manga.ms_reviews`) et les tables `*_stg` / `*_stage` heritage,
# qui sont du staging deguise en table applicative.
PLANCHE1 = {
    "coeur": ["manga.work_identity", "manga.volume_identity"],
    "journal": ["manga.match_decision", "manga.v_match_current", "manga.llm_avis"],
    "ms": [
        "manga.ms_formes",
        "manga.ms_reviews_all",
        "manga.ms_series_enriched",
        "manga.ms_kitsu_map",
        "manga.ms_kitsu_ambiguous",
        "manga.ms_volumes_enriched",
    ],
    "wd": [
        "manga.wd_pivot",
        "manga.wd_formes",
        "manga.wd_auteurs",
        "manga.wd_auteurs_formes",
    ],
    "kitsu": [
        "manga.kitsu_series_core",
        "manga.kitsu_formes",
        "manga.kitsu_mappings",
        "manga.kitsu_staff",
        "manga.kitsu_meta",
        "manga.kitsu_series_authors",
        "manga.kitsu_weekly_snapshot",
    ],
    "mi": ["manga.mi_series", "manga.mi_sorties"],
}

# Placement : (x, y) pour le coeur et le journal (corridor central) ;
# grilles de famille pour les couronnes. Tout est multiple de 20.
# Placement EXPLICITE (page 2800 x 1980, grille 20). Disposition en moyeu :
# work_identity au centre, familles en couronnes, corridors libres entre elles.
#   MS haut/bas-gauche | Wikidata haut-droite | Kitsu bas-droite | MI bas-gauche
POSITIONS = {
    # --- corridor central : coeur puis journal, de haut en bas ---
    "manga.work_identity": (1240, 380),
    "manga.volume_identity": (1240, 700),
    "manga.match_decision": (1240, 940),
    "manga.v_match_current": (1240, 1220),
    "manga.llm_avis": (1240, 1500),
    # --- Manga Sanctuary : pivot en colonne interne, dependants en externe ---
    "manga.ms_series_enriched": (680, 820),
    "manga.ms_formes": (60, 340),
    "manga.ms_kitsu_map": (60, 580),
    "manga.ms_kitsu_ambiguous": (60, 820),
    "manga.ms_reviews_all": (60, 1060),
    "manga.ms_volumes_enriched": (60, 1300),
    # --- Manga Insight : aucune FK, cluster compact en bas a gauche ---
    "manga.mi_series": (60, 1540),
    "manga.mi_sorties": (680, 1540),
    # --- Wikidata : pivot interne, dependants en colonne mediane ---
    "manga.wd_pivot": (1800, 420),
    "manga.wd_formes": (2140, 340),
    "manga.wd_auteurs": (2140, 580),
    "manga.wd_auteurs_formes": (2480, 340),
    # --- Kitsu : pivot interne, dependants medians, referentiels externes ---
    "manga.kitsu_series_core": (1800, 1240),
    "manga.kitsu_series_authors": (2140, 1020),
    "manga.kitsu_weekly_snapshot": (2140, 1260),
    "manga.kitsu_meta": (2140, 1500),
    "manga.kitsu_formes": (2480, 1020),
    "manga.kitsu_mappings": (2480, 1260),
    "manga.kitsu_staff": (2480, 1500),
}

LARGEUR_BOITE = 260

# Corridors imposes pour les liens longs : sans eux, le routage automatique
# ferait passer un lien a travers le corridor central (verifie par
# `verifier_disposition`, qui refuse toute traversee de boite).
GAUCHE, DROITE = (0.0, 0.5), (1.0, 0.5)
ROUTAGES = {
    ("manga.ms_formes", "manga.ms_series_enriched"): (None, None, -120),
    ("manga.ms_kitsu_map", "manga.ms_series_enriched"): (None, None, -60),
    ("manga.ms_kitsu_ambiguous", "manga.ms_series_enriched"): (None, None, 0),
    ("manga.ms_reviews_all", "manga.ms_series_enriched"): (None, None, 60),
    ("manga.ms_volumes_enriched", "manga.ms_series_enriched"): (None, None, 120),
    ("manga.match_decision", "manga.ms_series_enriched"): (GAUCHE, DROITE, -60),
    ("manga.llm_avis", "manga.ms_series_enriched"): (GAUCHE, DROITE, -110),
    ("manga.match_decision", "manga.wd_pivot"): (DROITE, GAUCHE, 0),
}

MAX_COLONNES_CENTRE = 12
MAX_COLONNES_PERIPHERIE = 6
# Les pivots de famille meritent plus de detail : ce sont eux qu'on lit.
PIVOTS = {
    "manga.ms_series_enriched",
    "manga.wd_pivot",
    "manga.kitsu_series_core",
}
MAX_COLONNES_PIVOT = 10

TYPES_COURTS = {
    "int2": "smallint",
    "int4": "int",
    "int8": "bigint",
    "float4": "real",
    "float8": "double",
    "bool": "bool",
    "text": "text",
    "varchar": "varchar",
    "bpchar": "char",
    "timestamptz": "timestamptz",
    "timestamp": "timestamp",
    "date": "date",
    "jsonb": "jsonb",
    "json": "json",
    "numeric": "numeric",
    "uuid": "uuid",
    "_text": "text[]",
}


def type_court(udt: str) -> str:
    return TYPES_COURTS.get(udt, udt.lstrip("_"))


# ----------------------------------------------------------- chargement ----
class Schema:
    def __init__(self, chemin: Path):
        brut = json.loads(chemin.read_text(encoding="utf-8"))
        self.comptes: dict[str, int] = brut["comptes"]
        self.colonnes: dict[str, list[dict]] = {}
        for c in brut["colonnes"]:
            self.colonnes.setdefault(
                f"{c['table_schema']}.{c['table_name']}", []
            ).append(c)
        self.tables = {
            f"{t['table_schema']}.{t['table_name']}": t["table_type"]
            for t in brut["tables"]
        }
        self.fk = brut["fk"]
        self.index = brut["index"]
        self.contraintes = brut["contraintes"]
        self.vues = brut["vues"]

        self.pk: dict[str, set[str]] = {}
        self.uniques: dict[str, set[str]] = {}
        for i in self.index:
            cle = f"{i['schema']}.{i['table']}"
            colonnes = _colonnes_d_index(i["definition"])
            if i["primaire"]:
                self.pk.setdefault(cle, set()).update(colonnes)
            elif i["unique"]:
                self.uniques.setdefault(cle, set()).update(colonnes)

        self.fk_colonnes: dict[str, set[str]] = {}
        for f in self.fk:
            cle = f"{f['schema_source']}.{f['table_source']}"
            self.fk_colonnes.setdefault(cle, set()).update(f["colonnes_source"])


def _colonnes_d_index(definition: str) -> list[str]:
    """Extrait les colonnes d'un `CREATE INDEX … USING btree (a, b) WHERE …`."""
    debut = definition.find("(")
    fin = definition.find(")", debut)
    if debut < 0 or fin < 0:
        return []
    return [c.strip().strip('"') for c in definition[debut + 1 : fin].split(",")]


def construire_colonnes(schema: Schema, cle: str, maximum: int) -> tuple[list, str]:
    """Colonnes affichees : les cles d'abord, puis les structurantes.

    La lisibilite prime sur l'exhaustivite : au-dela de `maximum`, on coupe et
    on annonce le reste. Le detail complet vit dans `schema_reel.md`.
    """
    toutes = schema.colonnes.get(cle, [])
    pk = schema.pk.get(cle, set())
    uniq = schema.uniques.get(cle, set())
    fks = schema.fk_colonnes.get(cle, set())

    def rang(c: dict) -> tuple:
        nom = c["column_name"]
        return (
            0 if nom in pk else 1 if nom in fks else 2 if nom in uniq else 3,
            c["ordinal_position"],
        )

    ordonnees = sorted(toutes, key=rang)
    retenues = ordonnees[:maximum]
    # On reprend l'ordre physique pour la lecture, une fois la selection faite.
    retenues.sort(key=lambda c: c["ordinal_position"])
    colonnes = [
        Colonne(
            nom=c["column_name"],
            type_court=type_court(c["udt_name"]),
            pk=c["column_name"] in pk,
            fk=c["column_name"] in fks,
            unique=c["column_name"] in uniq,
            non_nul=c["is_nullable"] == "NO",
        )
        for c in retenues
    ]
    reste = len(toutes) - len(retenues)
    note = f"… ({reste} autres colonnes)" if reste > 0 else ""
    return colonnes, note


# -------------------------------------------------------------- planche 1 ---
def batir_planche1(schema: Schema) -> tuple[dict[str, Boite], list[Lien]]:
    boites: dict[str, Boite] = {}
    for famille, cles in PLANCHE1.items():
        au_centre = famille in ("coeur", "journal")
        for cle in cles:
            if au_centre:
                maximum = MAX_COLONNES_CENTRE
            elif cle in PIVOTS:
                maximum = MAX_COLONNES_PIVOT
            else:
                maximum = MAX_COLONNES_PERIPHERIE
            x, y = POSITIONS[cle]
            cols, note = construire_colonnes(schema, cle, maximum)
            boites[cle] = Boite(
                cle=cle,
                titre=cle,
                famille=famille,
                x=x,
                y=y,
                largeur=LARGEUR_BOITE,
                colonnes=cols,
                note=note,
                est_vue=schema.tables.get(cle) == "VIEW",
            )

    liens = liens_reels(schema, set(boites)) + LIENS_APPLICATIFS
    for lien in liens:
        surcharge = ROUTAGES.get((lien.source, lien.cible))
        if surcharge:
            lien.ancre_source, lien.ancre_cible, lien.decalage = surcharge
    return boites, liens


# Liens SANS contrainte FK, traces en pointille : ils existent dans le code,
# pas dans le catalogue. Les inventer en trait plein serait un mensonge.
LIENS_APPLICATIFS = [
    Lien(
        "manga.match_decision",
        "manga.ms_series_enriched",
        "series_id",
        "0,n",
        "1,1",
        applicatif=True,
    ),
    Lien(
        "manga.match_decision",
        "manga.wd_pivot",
        "wikidata_qid",
        "0,n",
        "0,1",
        applicatif=True,
    ),
    Lien(
        "manga.v_match_current",
        "manga.match_decision",
        "vue sur",
        "",
        "",
        applicatif=True,
        vue=True,
    ),
]


def liens_reels(schema: Schema, presentes: set[str]) -> list[Lien]:
    """Une FK dessinee = une FK du catalogue. Rien d'autre."""
    liens = []
    for f in schema.fk:
        source = f"{f['schema_source']}.{f['table_source']}"
        cible = f"{f['schema_cible']}.{f['table_cible']}"
        if source in presentes and cible in presentes:
            liens.append(
                Lien(
                    source=source,
                    cible=cible,
                    etiquette=", ".join(f["colonnes_source"]),
                    cardinalite_source="0,n",
                    cardinalite_cible="1,1",
                )
            )
    return liens


# -------------------------------------------------------------- planche 2 ---
def batir_planche2(schema: Schema) -> tuple[dict[str, Boite], list[Lien]]:
    """Staging : representation SIMPLIFIEE. Ces tables sont jetables ; lister
    leurs 39 colonnes n'apprendrait rien. On montre le nom, la nature
    tout-TEXT et les colonnes techniques."""
    cles = sorted(k for k in schema.tables if k.startswith("staging."))
    boites: dict[str, Boite] = {}
    x0, y0, ncols, largeur, gouttiere = 120, 320, 4, 300, 100
    colonne, y, hmax = 0, y0, 0
    for cle in cles:
        toutes = schema.colonnes.get(cle, [])
        techniques = [
            c
            for c in toutes
            if c["column_name"] in ("loaded_at", "source_file", "chargement_id")
        ]
        cols = [Colonne("(toutes colonnes en text)", "text")]
        cols += [
            Colonne(c["column_name"], type_court(c["udt_name"])) for c in techniques
        ]
        boite = Boite(
            cle=cle,
            titre=cle,
            famille="ms",
            x=x0 + colonne * (largeur + gouttiere),
            y=y,
            largeur=largeur,
            colonnes=cols,
            note=f"{len(toutes)} colonnes au total",
        )
        boites[cle] = boite
        hmax = max(hmax, boite.hauteur)
        colonne += 1
        if colonne >= ncols:
            colonne, y, hmax = 0, y + ((hmax + gouttiere) // 20 + 1) * 20, 0
    return boites, []


# -------------------------------------------------------------- planche 3 ---
# MCD Merise : entites METIER et associations. Ni type, ni cle technique — un
# MCD decrit ce que le domaine contient, pas comment PostgreSQL le stocke.
ENTITES_MCD = [
    ("FORME", 1240, 340, ["libelle normalise", "type de forme", "langue"]),
    ("SERIE-SOURCE", 1240, 800, ["titre", "annee", "plateforme d'origine"]),
    ("OEUVRE", 1240, 1240, ["identite consolidee", "identifiants externes"]),
    ("VOLUME", 1240, 1620, ["numero", "ISBN-13", "date de parution"]),
    ("AUTEUR", 500, 800, ["nom", "role"]),
    ("DECISION-DE-RAPPROCHEMENT", 2140, 340, ["methode", "score", "statut", "date"]),
    ("AVIS-LLM", 2140, 700, ["verdict", "confiance", "justification", "modele"]),
    ("CRITIQUE", 2140, 1240, ["texte", "note", "date"]),
]

ASSOCIATIONS_MCD = [
    ("SERIE-SOURCE", "OEUVRE", "designe", "0,n", "0,1"),
    ("OEUVRE", "VOLUME", "regroupe", "1,1", "0,n"),
    ("SERIE-SOURCE", "FORME", "se nomme", "1,1", "0,n"),
    ("SERIE-SOURCE", "AUTEUR", "est signee par", "0,n", "0,n"),
    ("SERIE-SOURCE", "CRITIQUE", "recoit", "1,1", "0,n"),
    ("DECISION-DE-RAPPROCHEMENT", "SERIE-SOURCE", "tranche", "0,n", "1,1"),
    ("AVIS-LLM", "SERIE-SOURCE", "porte sur", "0,n", "1,1"),
    ("AVIS-LLM", "DECISION-DE-RAPPROCHEMENT", "eclaire", "0,n", "0,1"),
]


def batir_planche3() -> tuple[dict[str, Boite], list[Lien]]:
    boites = {}
    for nom, x, y, attributs in ENTITES_MCD:
        boites[nom] = Boite(
            cle=nom,
            titre=nom,
            famille=(
                "coeur"
                if nom in ("OEUVRE", "VOLUME")
                else "journal"
                if nom in ("DECISION-DE-RAPPROCHEMENT", "AVIS-LLM")
                else "ms"
            ),
            x=x,
            y=y,
            largeur=300,
            colonnes=[Colonne(a, "") for a in attributs],
        )
    liens = [Lien(a, b, libelle, cs, cc) for a, b, libelle, cs, cc in ASSOCIATIONS_MCD]
    # Deux associations arrivent sur la meme face de SERIE-SOURCE : on decale
    # leurs corridors pour que les etiquettes ne se superposent pas.
    decalages = {
        ("DECISION-DE-RAPPROCHEMENT", "SERIE-SOURCE"): -120,
        ("AVIS-LLM", "SERIE-SOURCE"): 0,
        ("SERIE-SOURCE", "CRITIQUE"): 120,
    }
    for lien in liens:
        lien.decalage = decalages.get((lien.source, lien.cible), 0)
    return boites, liens


# ------------------------------------------------------------- rendu XML ----
def style_boite(boite: Boite) -> str:
    famille = FAMILLES[boite.famille]
    trait = "dashed=1;" if boite.est_vue else ""
    return (
        "swimlane;html=1;startSize=30;horizontal=1;childLayout=stackLayout;"
        "resizeParent=0;resizeLast=0;collapsible=0;marginBottom=0;"
        f"fillColor={famille.entete};swimlaneFillColor={famille.fond};"
        f"strokeColor=#37474F;fontStyle=1;fontSize=12;fontColor=#000000;{trait}"
    )


def style_lien(lien: Lien) -> str:
    base = (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;jettySize=auto;"
        "orthogonalLoop=1;fontSize=11;fontColor=#000000;labelBackgroundColor=#FFFFFF;"
    )
    if lien.applicatif:
        return base + "dashed=1;strokeColor=#78909C;endArrow=open;endFill=0;"
    return base + "strokeColor=#37474F;endArrow=block;endFill=1;"


def _identifiant(nom: str) -> str:
    """Identifiant d'onglet deterministe, derive du nom de la planche."""
    return hashlib.md5(nom.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def xml_planche(
    nom: str, boites: dict[str, Boite], liens: list[Lien], legende: str, notes: str
) -> str:
    parties = [
        # id STABLE : `hash()` est randomise par processus (PYTHONHASHSEED),
        # ce qui ferait varier le fichier a chaque regeneration.
        f'<diagram name="{html.escape(nom)}" id="{_identifiant(nom)}">',
        f'<mxGraphModel dx="1400" dy="900" grid="1" gridSize="20" guides="1" '
        f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        f'pageWidth="{PAGE_L}" pageHeight="{PAGE_H}" math="0" shadow="0">',
        "<root>",
        '<mxCell id="0" />',
        '<mxCell id="1" parent="0" />',
    ]
    ident = {cle: f"n{i}" for i, cle in enumerate(boites)}

    style_encadre = (
        "text;html=1;whiteSpace=wrap;align=left;verticalAlign=top;"
        "spacingLeft=10;spacingTop=8;fontSize=12;"
    )
    parties.append(
        f'<mxCell id="legende" value="{legende}" '
        f'style="{style_encadre}fillColor=#FFFFFF;strokeColor=#37474F;" '
        f'vertex="1" parent="1">'
        f'<mxGeometry x="60" y="60" width="460" height="200" as="geometry" />'
        f"</mxCell>"
    )
    parties.append(
        f'<mxCell id="titre" value="{html.escape(nom)}" '
        f'style="text;html=1;align=center;verticalAlign=middle;fontSize=22;'
        f'fontStyle=1;" vertex="1" parent="1">'
        f'<mxGeometry x="950" y="80" width="900" height="50" as="geometry" />'
        f"</mxCell>"
    )
    if notes:
        parties.append(
            f'<mxCell id="notes" value="{notes}" '
            f'style="{style_encadre}fillColor=#FFFDE7;strokeColor=#F9A825;" '
            f'vertex="1" parent="1">'
            f'<mxGeometry x="2180" y="60" width="560" height="200" as="geometry" />'
            f"</mxCell>"
        )

    for cle, boite in boites.items():
        bid = ident[cle]
        parties.append(
            f'<mxCell id="{bid}" value="{html.escape(boite.titre)}" '
            f'style="{style_boite(boite)}" vertex="1" parent="1">'
            f'<mxGeometry x="{boite.x}" y="{boite.y}" width="{boite.largeur}" '
            f'height="{boite.hauteur}" as="geometry" /></mxCell>'
        )
        y = HAUTEUR_ENTETE
        for j, colonne in enumerate(boite.colonnes):
            parties.append(
                f'<mxCell id="{bid}c{j}" value="{html.escape(colonne.html())}" '
                f'style="text;html=1;strokeColor=none;fillColor=none;align=left;'
                f"verticalAlign=middle;spacingLeft=8;overflow=hidden;fontSize=11;"
                f'fontColor=#000000;" vertex="1" parent="{bid}">'
                f'<mxGeometry y="{y}" width="{boite.largeur}" '
                f'height="{HAUTEUR_LIGNE}" as="geometry" /></mxCell>'
            )
            y += HAUTEUR_LIGNE
        if boite.note:
            parties.append(
                f'<mxCell id="{bid}note" value="{html.escape(boite.note)}" '
                f'style="text;html=1;strokeColor=none;fillColor=none;align=left;'
                f"verticalAlign=middle;spacingLeft=8;fontSize=10;fontStyle=2;"
                f'fontColor=#455A64;" vertex="1" parent="{bid}">'
                f'<mxGeometry y="{y}" width="{boite.largeur}" '
                f'height="{HAUTEUR_LIGNE}" as="geometry" /></mxCell>'
            )

    for k, lien in enumerate(liens):
        if lien.source not in ident or lien.cible not in ident:
            continue
        a, b = boites[lien.source], boites[lien.cible]
        (sx, sy), (tx, ty) = ancres(a, b)
        etiquette = lien.etiquette
        if lien.cardinalite_source or lien.cardinalite_cible:
            etiquette = (
                f"{lien.etiquette}\n({lien.cardinalite_source} → "
                f"{lien.cardinalite_cible})"
                if lien.etiquette
                else f"{lien.cardinalite_source} → {lien.cardinalite_cible}"
            )
        parties.append(
            f'<mxCell id="e{k}" value="{html.escape(etiquette)}" '
            f'style="{style_lien(lien)}exitX={sx};exitY={sy};exitDx=0;exitDy=0;'
            f'entryX={tx};entryY={ty};entryDx=0;entryDy=0;" edge="1" parent="1" '
            f'source="{ident[lien.source]}" target="{ident[lien.cible]}">'
            f'<mxGeometry relative="1" as="geometry" /></mxCell>'
        )

    parties += ["</root>", "</mxGraphModel>", "</diagram>"]
    return "\n".join(parties)


LEGENDE_MPD = html.escape(
    "LEGENDE\n"
    "PK nom : type   cle primaire (gras + souligne)\n"
    "FK nom : type   cle etrangere (italique)\n"
    "U  nom : type   index UNIQUE (partiel si conditionnel)\n"
    "nom : type *    colonne NOT NULL\n"
    "--- trait plein : contrainte FK reelle du catalogue\n"
    "--- pointille  : lien applicatif, sans contrainte FK\n"
    "Couleurs : coeur / journal / Manga Sanctuary / Wikidata / Kitsu / "
    "Manga Insight"
).replace("\n", "&#10;")

NOTES_MPD = html.escape(
    "HORS PERIMETRE de cette planche\n"
    "- schema bench.* (module 06, experimental) ;\n"
    "- vues et tables RAG heritage (rag_*, manga.ms_reviews : corpus RAG de "
    "3 187 documents, a ne pas confondre avec ms_reviews_all, le referentiel) ;\n"
    "- tables *_stg / *_stage heritage (staging deguise).\n"
    "Le detail complet des colonnes vit dans schema_reel.md."
).replace("\n", "&#10;")

LEGENDE_STAGING = html.escape(
    "LEGENDE\n"
    "Les tables staging.* sont JETABLES : elles recoivent le fichier brut\n"
    "en TOUT-TEXT, sans typage et sans rejet. Le typage a lieu ensuite,\n"
    "en SQL, vers les tables manga.*.\n"
    "On ne detaille donc pas leurs colonnes : seules comptent leur\n"
    "existence et leurs colonnes techniques de tracabilite."
).replace("\n", "&#10;")

LEGENDE_MCD = html.escape(
    "LEGENDE — MCD (Merise)\n"
    "Entites METIER et associations, avec cardinalites (min,max).\n"
    "Ni type, ni cle technique : un MCD dit ce que le domaine contient,\n"
    "pas comment PostgreSQL le stocke.\n"
    "Lecture : SERIE-SOURCE (0,n) --designe--> (0,1) OEUVRE se lit\n"
    "« une serie designe au plus une oeuvre ; une oeuvre est designee\n"
    "par zero a n series de sources differentes »."
).replace("\n", "&#10;")


def ecrire_drawio(chemin: Path, planches: list[str]) -> None:
    contenu = (
        '<mxfile host="app.diagrams.net" agent="generer_diagrammes.py" '
        'version="24.0.0">\n' + "\n".join(planches) + "\n</mxfile>\n"
    )
    chemin.write_text(contenu, encoding="utf-8")


# ------------------------------------------------------------- rendu SVG ----
def svg_planche(
    nom: str, boites: dict[str, Boite], liens: list[Lien], legende: str, notes: str
) -> str:
    """Meme geometrie que le .drawio — c'est la garantie que le PNG montre
    exactement la planche editable."""
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{PAGE_L}" '
        f'height="{PAGE_H}" viewBox="0 0 {PAGE_L} {PAGE_H}">',
        f'<rect width="{PAGE_L}" height="{PAGE_H}" fill="#FFFFFF"/>',
        "<style>text{font-family:Helvetica,Arial,sans-serif;fill:#000}</style>",
        f'<text x="{PAGE_L / 2}" y="110" font-size="30" font-weight="bold" '
        f'text-anchor="middle">{html.escape(nom)}</text>',
    ]

    def bloc_texte(x, y, w, h, contenu, fond, bord, taille=13):
        out.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fond}" '
            f'stroke="{bord}" stroke-width="1.5"/>'
        )
        for i, ligne in enumerate(contenu.split("&#10;")):
            out.append(
                f'<text x="{x + 12}" y="{y + 24 + i * (taille + 5)}" '
                f'font-size="{taille}">{ligne}</text>'
            )

    bloc_texte(60, 60, 460, 200, legende, "#FFFFFF", "#37474F", 12)
    if notes:
        bloc_texte(1880, 60, 440, 200, notes, "#FFFDE7", "#F9A825", 11)

    for lien in liens:
        if lien.source not in boites or lien.cible not in boites:
            continue
        points = route(boites[lien.source], boites[lien.cible])
        chemin = " ".join(f"{x:.0f},{y:.0f}" for x, y in points)
        style = (
            'stroke="#78909C" stroke-dasharray="8,5"'
            if lien.applicatif
            else 'stroke="#37474F"'
        )
        out.append(
            f'<polyline points="{chemin}" fill="none" {style} stroke-width="2"/>'
        )
        mx, my = points[len(points) // 2]
        etiquette = lien.etiquette
        if lien.cardinalite_source or lien.cardinalite_cible:
            etiquette = (
                f"{etiquette} ({lien.cardinalite_source}→{lien.cardinalite_cible})"
                if etiquette
                else f"{lien.cardinalite_source}→{lien.cardinalite_cible}"
            )
        if etiquette:
            largeur = 7 * len(etiquette)
            out.append(
                f'<rect x="{mx - largeur / 2:.0f}" y="{my - 11:.0f}" '
                f'width="{largeur}" height="18" fill="#FFFFFF" opacity="0.9"/>'
                f'<text x="{mx:.0f}" y="{my + 3:.0f}" font-size="11" '
                f'text-anchor="middle">{html.escape(etiquette)}</text>'
            )

    for boite in boites.values():
        famille = FAMILLES[boite.famille]
        tirets = ' stroke-dasharray="6,4"' if boite.est_vue else ""
        out.append(
            f'<rect x="{boite.x}" y="{boite.y}" width="{boite.largeur}" '
            f'height="{boite.hauteur}" fill="{famille.fond}" stroke="#37474F" '
            f'stroke-width="1.5"{tirets}/>'
        )
        out.append(
            f'<rect x="{boite.x}" y="{boite.y}" width="{boite.largeur}" '
            f'height="{HAUTEUR_ENTETE}" fill="{famille.entete}" stroke="#37474F" '
            f'stroke-width="1.5"{tirets}/>'
        )
        out.append(
            f'<text x="{boite.x + boite.largeur / 2}" y="{boite.y + 20}" '
            f'font-size="13" font-weight="bold" text-anchor="middle">'
            f"{html.escape(boite.titre)}</text>"
        )
        y = boite.y + HAUTEUR_ENTETE + 13
        for colonne in boite.colonnes:
            gras = (
                ' font-weight="bold" text-decoration="underline"' if colonne.pk else ""
            )
            italique = ' font-style="italic"' if colonne.fk else ""
            out.append(
                f'<text x="{boite.x + 8}" y="{y}" font-size="11"{gras}{italique}>'
                f"{html.escape(colonne.libelle())}</text>"
            )
            y += HAUTEUR_LIGNE
        if boite.note:
            out.append(
                f'<text x="{boite.x + 8}" y="{y}" font-size="10" font-style="italic" '
                f'fill="#455A64">{html.escape(boite.note)}</text>'
            )
    out.append("</svg>")
    return "\n".join(out)


# ----------------------------------------------------------------- mermaid --
def mermaid(schema: Schema, boites: dict[str, Boite], liens: list[Lien]) -> str:
    lignes = [
        "%% MPD coeur — version Mermaid (SECONDAIRE).",
        "%% La mise en page est AUTOMATIQUE ici : Mermaid place les entites",
        "%% lui-meme, sans respecter la disposition en moyeu. La reference",
        "%% opposable reste modele_donnees.drawio (planche 1).",
        "%% Genere par docs/modeles/generer_diagrammes.py depuis schema.json.",
        "erDiagram",
    ]
    for lien in liens:
        if lien.source not in boites or lien.cible not in boites:
            continue
        a = lien.source.split(".")[1]
        b = lien.cible.split(".")[1]
        relation = "|o..o{" if lien.applicatif else "||--o{"
        etiquette = (lien.etiquette or "lien").replace(" ", "_").replace(",", "_")
        # b = cible (parent, cote FK reference), a = source (enfant).
        lignes.append(f"    {b} {relation} {a} : {etiquette}")
    for cle, boite in boites.items():
        nom = cle.split(".")[1]
        lignes.append(f"    {nom} {{")
        for colonne in boite.colonnes:
            marque = "PK" if colonne.pk else "FK" if colonne.fk else ""
            type_m = (colonne.type_court or "text").replace(" ", "_")
            lignes.append(f"        {type_m} {colonne.nom} {marque}".rstrip())
        lignes.append("    }")
    return "\n".join(lignes) + "\n"


# --------------------------------------------------------------------- main --
def main() -> int:
    schema = Schema(ICI / "schema.json")

    b1, l1 = batir_planche1(schema)
    b2, l2 = batir_planche2(schema)
    b3, l3 = batir_planche3()

    planches = [
        ("MPD coeur — referentiel d'identite manga", b1, l1, LEGENDE_MPD, NOTES_MPD),
        ("MPD staging (annexe) — tables jetables", b2, l2, LEGENDE_STAGING, ""),
        ("MCD — modele conceptuel (Merise)", b3, l3, LEGENDE_MCD, ""),
    ]

    ecrire_drawio(
        ICI / "modele_donnees.drawio",
        [xml_planche(n, b, li, le, no) for n, b, li, le, no in planches],
    )

    fichiers_png = {
        "MPD coeur — referentiel d'identite manga": "mpd_coeur",
        "MPD staging (annexe) — tables jetables": "mpd_staging",
        "MCD — modele conceptuel (Merise)": "mcd",
    }
    for nom, b, li, le, no in planches:
        svg = svg_planche(nom, b, li, le, no)
        base = fichiers_png[nom]
        (ICI / f"{base}.svg").write_text(svg, encoding="utf-8")

    (ICI / "modele_donnees.mmd").write_text(mermaid(schema, b1, l1), encoding="utf-8")

    rapport = {
        "planche1": verifier_disposition(b1, l1),
        "planche2": verifier_disposition(b2, l2),
        "planche3": verifier_disposition(b3, l3),
        "tables_planche1": len(b1),
        "tables_planche2": len(b2),
        "entites_planche3": len(b3),
        "liens_reels_planche1": sum(1 for x in l1 if not x.applicatif),
        "liens_applicatifs_planche1": sum(1 for x in l1 if x.applicatif),
    }
    json.dump(rapport, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
