"""Le rapport de livraison : `livraisons/<horodatage UTC>_<candidat>.md` (pour un
humain) et `.json` (pour la commande de promotion) — décisions de l'étape, § 8.

Ni secret, ni pseudonyme, ni texte de passage. La sortie d'une étape arrêtée n'entre
qu'en extrait (dix dernières lignes, 200 caractères chacune), après masquage : les
DSN à mot de passe, les formes de clés connues, les secrets connus de la commande, et
les pseudonymes des membres — lus en préproduction sous `verif_lecture`, seulement
quand un extrait est à écrire, jamais écrits.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from chaine import etapes
from chaine.codes import SENS, Code
from chaine.environnement import Lanceur, chemin_lisible, lignes, psql
from chaine.executer import Outils, Passage, suite

MASQUE = "[masqué]"
FORMES = (
    re.compile(r"(postgres(?:ql)?://[^:/@\s]+):[^@\s]+@"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    re.compile(r"(?i)(x-api-key\s*[:=]\s*)\S+"),
)


def masquer(
    texte: str, secrets: Sequence[str] = (), pseudonymes: Sequence[str] = ()
) -> str:
    texte = FORMES[0].sub(rf"\1:{MASQUE}@", texte)
    for forme in FORMES[1:3]:
        texte = forme.sub(MASQUE, texte)
    texte = FORMES[3].sub(rf"\1{MASQUE}", texte)
    for secret in secrets:
        if secret:
            texte = texte.replace(secret, MASQUE)
    for nom in sorted({n for n in pseudonymes if n.strip()}, key=len, reverse=True):
        texte = re.sub(
            rf"(?<!\w){re.escape(nom)}(?!\w)", MASQUE, texte, flags=re.IGNORECASE
        )
    return texte


def extrait(sortie: str, secrets: Sequence[str], pseudonymes: Sequence[str]) -> str:
    dernieres = [ligne[:200] for ligne in sortie.strip().splitlines()[-10:]]
    return masquer("\n".join(dernieres), secrets, pseudonymes)


def pseudonymes_de(lanceur: Lanceur, dsn_lecture: str) -> Callable[[], list[str]]:
    """Les pseudonymes, lus à la demande (une fois), pour masquer un extrait."""
    memoire: list[list[str]] = []

    def lire() -> list[str]:
        if not memoire:
            s = lanceur(
                psql(
                    "psql.pseudonymes",
                    dsn_lecture,
                    sql="SELECT DISTINCT review_author FROM manga.ms_reviews_all"
                    " WHERE review_author IS NOT NULL",
                )
            )
            if s.code != 0:
                raise RuntimeError("pseudonymes illisibles")
            memoire.append(lignes(s))
        return memoire[0]

    return lire


def contenu(
    p: Passage, pseudonymes: Callable[[], list[str]], secrets: Sequence[str] = ()
) -> dict[str, Any]:
    """Le rapport, en données : ce que lit la commande de promotion."""
    c = p.candidat
    etapes_rapport: list[dict[str, Any]] = []
    for r in p.resultats:
        e: dict[str, Any] = {
            "numero": r.numero,
            "nom": r.nom,
            "statut": r.statut,
            "duree_s": r.duree_s,
            "details": r.details,
        }
        if r.code is not None:
            e["code"] = int(r.code)
            e["motif"] = masquer(r.motif, secrets)
        if r.sortie:
            try:
                e["sortie"] = extrait(r.sortie, secrets, pseudonymes())
            except RuntimeError:
                e["sortie"] = (
                    "(omise : les pseudonymes n'ont pas pu être lus pour la masquer)"
                )
        etapes_rapport.append(e)
    verdict: dict[str, Any] = p.verdict or {}
    nouveau: dict[str, Any] = verdict.get("nouveau", {})
    return {
        "livraison": p.nom_livraison,
        "environnement": "préproduction",
        "base": p.cible.base if p.cible else None,
        "dossier_echantillons": chemin_lisible(p.cible.dossier_export)
        if p.cible
        else None,
        "candidat": {
            "fichier": chemin_lisible(p.chemin_candidat),
            "nom": p.nom,
            "motif": c.motif if c else None,
            "corpus": c.corpus if c else None,
            "mesure": c.mesure if c else None,
            "reference_run": c.reference_run if c else None,
        },
        "commit": p.commit,
        "encodage": p.encodage,
        "run": p.run,
        "experience": etapes.EXPERIENCE if p.run else None,
        "mesure": {
            k: nouveau.get(k)
            for k in ("questions", "global", "reconnaissance", "proposition", "ndcg_10")
        },
        "regle": p.regle,
        "verdict": verdict.get("verdict"),
        "conditions_manquantes": p.conditions,
        "image": p.image,
        "etapes": etapes_rapport,
        "code": int(p.code),
        "code_nom": p.code.name,
        "sens": SENS[p.code],
    }


# --------------------------------------------------------------------------- #
#  L'affichage du rapport pour un humain (le `.json` garde les valeurs brutes)
# --------------------------------------------------------------------------- #

FINE = "\u202f"  # espace fine insécable : séparateur des milliers
RIEN = "—"
LIBELLES = {
    "encodage_en_service": "encodage en service",
    "reconstruction_a_blanc": "reconstruction à blanc",
    "duree_encodage_s": "durée de l'encodage",
    "memoire_video_pic": "mémoire vidéo de la carte au plus haut",
    "memoire_vive_pic": "mémoire vive au plus haut (cache de fichiers compris)",
    "echantillon": "échantillon",
    "cos_min": "cosinus minimal",
    "n": "fragments comparés",
    "sous_0999": "sous 0,999",
    "experience": "expérience",
    "ndcg_10": "nDCG@10",
    "conditions_manquantes": "conditions manquantes",
}
OCTETS = ("memoire_video_pic", "memoire_vive_pic")
SECONDES = ("duree_encodage_s",)
CHEMINS = ("dossier", "echantillon")
# Les détails déjà dits ailleurs, ou trop longs pour une cellule (ils sont au `.json`).
CACHES = ("info", "service", "empreintes", "regle")
EMPREINTE = re.compile(r"^(sha256:)?[0-9a-f]{32,}$")
# Dans un motif (texte libre des étapes) : un réel à rallonge, un dictionnaire Python.
REEL_LONG = re.compile(r"(?<![\w.])-?\d+\.\d{5,}(?![\w.])")


def nombre(x: float, decimales: int = 0) -> str:
    """À la française : virgule décimale, espace fine pour les milliers."""
    texte = f"{x:,.{decimales}f}"
    return texte.replace(",", " ").replace(".", ",").replace(" ", FINE)


def ou_rien(v: Any) -> str:
    return RIEN if v is None or v == "" else str(v)


def en_code(v: Any) -> str:
    return RIEN if v is None or v == "" else f"`{v}`"


def litteral(v: object) -> str:
    """Une valeur lue dans un motif, en mots, sans rien tronquer ni réordonner."""
    if isinstance(v, dict):
        d = cast(dict[object, object], v)
        return "(" + ", ".join(f"{k} {litteral(x)}" for k, x in d.items()) + ")"
    if isinstance(v, tuple | list | set | frozenset):
        return "(" + ", ".join(litteral(x) for x in cast(list[object], v)) + ")"
    if v is None:
        return RIEN
    if isinstance(v, bool):
        return "oui" if v else "non"
    if isinstance(v, int):
        return nombre(v)
    if isinstance(v, float):
        decimales = repr(v).partition(".")[2]
        return nombre(v, 8) if len(decimales) >= 5 else repr(v).replace(".", ",")
    return str(v)


def dictionnaires_du_texte(texte: str) -> str:
    """Les dictionnaires Python d'un motif, réécrits en mots ; un morceau qui n'en est
    pas un reste tel quel."""
    sortie: list[str] = []
    i = 0
    while i < len(texte):
        if texte[i] != "{":
            sortie.append(texte[i])
            i += 1
            continue
        profondeur, j = 0, i
        while j < len(texte):
            profondeur += {"{": 1, "}": -1}.get(texte[j], 0)
            if profondeur == 0:
                break
            j += 1
        morceau = texte[i : j + 1]
        try:
            lu: object = ast.literal_eval(morceau)
        except Exception:  # noqa: BLE001 — un texte libre : tout refus le laisse tel quel
            lu = None
        if isinstance(lu, dict) and lu:
            sortie.append(litteral(cast(dict[object, object], lu)))
            i = j + 1
        else:
            sortie.append(texte[i])
            i += 1
    return "".join(sortie)


def motif_lisible(texte: str) -> str:
    """Un motif d'arrêt, ses nombres à la française (huit décimales) et ses
    dictionnaires en mots."""
    texte = dictionnaires_du_texte(texte)
    return REEL_LONG.sub(lambda m: nombre(float(m.group(0)), 8), texte)


def valeur_lisible(cle: str, v: Any) -> str:
    if v is None or v == "":
        return RIEN
    if isinstance(v, bool):
        return "oui" if v else "non"
    if isinstance(v, int | float) and cle in OCTETS:
        # Le module 09 écrit -1 (ou 0) quand le relevé n'a pas pu se faire.
        return f"{nombre(v / 1e9, 2)} Go" if v > 0 else "non mesurée"
    if isinstance(v, int | float) and cle in SECONDES:
        return f"{nombre(v, 1)} s"
    if isinstance(v, int):
        return nombre(v)
    if isinstance(v, float):
        return nombre(v, 8 if cle == "cos_min" else 4)
    if isinstance(v, dict):
        return sous_liste(cast(dict[str, Any], v)) or RIEN
    if isinstance(v, list | tuple):
        return ", ".join(valeur_lisible(cle, x) for x in cast(list[Any], v)) or RIEN
    texte = str(v)
    if EMPREINTE.match(texte):
        prefixe = "sha256:" if texte.startswith("sha256:") else ""
        return f"`{prefixe}{texte.removeprefix('sha256:')[:8]}…`"
    if cle in CHEMINS:
        return f"`{texte}`"
    return texte


def libelle(cle: str) -> str:
    """Le nom d'un champ en mots ; une clé inconnue (un type de document…) telle
    quelle."""
    return LIBELLES.get(cle, cle)


def sous_liste(d: dict[str, Any]) -> str:
    """Un dictionnaire imbriqué, en mots ; un niveau de plus entre parenthèses."""
    return ", ".join(
        f"{libelle(k)} ({sous_liste(cast(dict[str, Any], v))})"
        if isinstance(v, dict)
        else f"{libelle(k)} {valeur_lisible(k, v)}"
        for k, v in d.items()
    )


def par_perimetre(cle: str, v: Any) -> list[str] | None:
    """Des métriques `nom/périmètre` (sortie du banc), une ligne par périmètre."""
    if not isinstance(v, dict) or not v:
        return None
    d = cast(dict[str, Any], v)
    if not all("/" in k for k in d):
        return None
    groupes: dict[str, list[str]] = {}
    for k, x in d.items():
        nom, perimetre = k.split("/", 1)
        groupes.setdefault(perimetre, []).append(f"{nom} {valeur_lisible(nom, x)}")
    return [f"{libelle(cle)} ({p}) : {', '.join(m)}" for p, m in groupes.items()]


def sur(n: Any, total: Any) -> str:
    if n is None:
        return RIEN
    return f"{n} / {total}" if total is not None else f"{n}"


def resultat(m: dict[str, Any], totaux: dict[str, Any]) -> str:
    if m.get("global") is None:
        return RIEN
    reconnaissance = sur(m.get("reconnaissance"), totaux.get("reconnaissance"))
    return (
        f"global {sur(m['global'], m.get('questions'))} ;"
        f" reconnaissance {reconnaissance} ;"
        f" proposition {sur(m.get('proposition'), totaux.get('proposition'))} ;"
        f" nDCG@10 {valeur_lisible('ndcg_10', m.get('ndcg_10'))}"
    )


def detail_etape(e: dict[str, Any], d: dict[str, Any], totaux: dict[str, Any]) -> str:
    if e.get("motif"):
        try:
            return motif_lisible(str(e["motif"]))
        except Exception:  # noqa: BLE001 — le rapport s'écrit toujours : le motif brut
            return str(e["motif"])
    details: dict[str, Any] = dict(e["details"])
    if e["statut"] == "non lancée" and "condition" in details:
        return f"condition non remplie : {details['condition']}"
    morceaux: list[str] = []
    if e["numero"] == 6 and "verdict" in details:
        morceaux.append(f"{details['verdict']} : {resultat(d['mesure'], totaux)}")
        details = {}
    rejeu = details.pop("rejeu", None)
    for k, v in details.items():
        if k in CACHES:
            continue
        groupes = par_perimetre(k, v)
        morceaux += groupes or [f"{libelle(k)} : {valeur_lisible(k, v)}"]
    if isinstance(rejeu, dict):
        r = cast(dict[str, Any], rejeu)
        identique = "identique" if r.get("identiques") else "différent"
        morceaux.append(f"rejeu contre `{str(r.get('reference'))[:8]}` : {identique}")
    return " ; ".join(morceaux) or RIEN


def lignes_non_lancees(d: dict[str, Any]) -> list[dict[str, Any]]:
    """Les étapes 0 à 7 que le passage n'a pas atteintes (un arrêt, un refus) : dites
    « non lancées », avec la raison."""
    vues = {e["numero"] for e in d["etapes"]}
    dernier = d["etapes"][-1] if d["etapes"] else None
    raison = (
        f"arrêt de la chaîne à l'étape {dernier['numero']} ({dernier['statut']})"
        if dernier
        else "la chaîne n'a pas démarré"
    )
    return [
        {
            "numero": e.numero,
            "nom": e.nom,
            "statut": "non lancée",
            "duree_s": None,
            "details": {},
            "motif": f"{raison} ; condition : {e.condition}"
            if e.condition != "toujours"
            else raison,
        }
        for e in etapes.ETAPES
        if e.numero < 8 and e.numero not in vues
    ]


def cellule(texte: str) -> str:
    return texte.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def image(d: dict[str, Any]) -> str:
    if d["image"]:
        return f"`{d['image']}`"
    sept = [e for e in d["etapes"] if e["numero"] == 7]
    if sept and sept[0]["statut"] != "non lancée":
        return f"{RIEN} (empreinte non relevée : voir l'étape 7)"
    return f"{RIEN} (non construite)"


def markdown(
    d: dict[str, Any], suite: str, totaux: dict[str, Any] | None = None
) -> str:
    """Le rapport pour un humain : en-tête, tableau des étapes, chiffres, suite.
    `totaux` : les dénominateurs de la reconnaissance et de la proposition (sortie
    de la règle) ; le `.json` ne les porte pas."""
    totaux = totaux or {}

    def ligne_etape(e: dict[str, Any]) -> str:
        duree = (
            RIEN
            if e["duree_s"] is None or e["statut"] == "non lancée"
            else nombre(e["duree_s"], 1)
        )
        return (
            f"| {e['numero']} | {e['nom']} | {e['statut']} | {duree}"
            f" | {cellule(detail_etape(e, d, totaux))} |"
        )

    regle: dict[str, Any] = d["regle"] or {}
    c: dict[str, Any] = d["candidat"]
    numero = f"n° {c['mesure']}" if c.get("mesure") is not None else RIEN
    mesure = (
        f"{numero} ; run `{d['run']}` ({d['experience']})"
        if d["run"]
        else f"{numero} ; aucun run"
    )
    motif = f" — {cellule(str(c['motif']))}" if c.get("motif") else ""
    corps = [
        f"# Rapport de livraison — {d['candidat']['nom']} — {d['livraison']}",
        "",
        f"**Code de sortie : {d['code']} ({d['code_nom']})** — {d['sens']}.",
        "",
        "## Ce qui a été jugé",
        "",
        "| Objet | Valeur |",
        "|---|---|",
        f"| Environnement | {d['environnement']}, base {en_code(d['base'])} |",
        f"| Dossier des échantillons | {en_code(d['dossier_echantillons'])} |",
        f"| Candidat | {en_code(c['fichier'])}{motif} |",
        f"| Commit | {en_code(d['commit'])} |",
        f"| Corpus | {ou_rien(c['corpus'])} |",
        f"| Encodage | {d['encodage'] if d['encodage'] is not None else RIEN} |",
        f"| Mesure | {mesure} |",
        f"| Résultat | {resultat(d['mesure'], totaux)} |",
        f"| Règle | `{regle['fichier']}`, commit `{regle.get('commit')}`"
        f" du {regle.get('date')} |"
        if regle.get("fichier")
        else f"| Règle | {RIEN} |",
        f"| Verdict | {d['verdict'] or RIEN} |",
        f"| Conditions manquantes | {', '.join(d['conditions_manquantes']) or RIEN} |",
        f"| Image de l'API | {image(d)} |",
        "",
        "## Les étapes",
        "",
        "| N° | Étape | Statut | Durée (s) | Détail |",
        "|---|---|---|---|---|",
        *[ligne_etape(e) for e in [*d["etapes"], *lignes_non_lancees(d)]],
        "",
    ]
    for e in d["etapes"]:
        if "sortie" in e:
            corps += [
                f"### Sortie de l'étape {e['numero']} (extrait masqué)",
                "",
                "```text",
                e["sortie"],
                "```",
                "",
            ]
    corps += ["## Ensuite", "", suite, ""]
    return "\n".join(corps)


def ecrire(
    p: Passage,
    dossier: Path,
    pseudonymes: Callable[[], list[str]],
    suite: str,
    secrets: Sequence[str] = (),
) -> tuple[Path, Path]:
    dossier.mkdir(parents=True, exist_ok=True)
    d = contenu(p, pseudonymes, secrets)
    md = dossier / f"{p.nom_livraison}.md"
    js = dossier / f"{p.nom_livraison}.json"
    js.write_text(
        json.dumps(d, ensure_ascii=False, indent=1, default=str) + "\n",
        encoding="utf-8",
    )
    nouveau: dict[str, Any] = (p.verdict or {}).get("nouveau", {})
    totaux = {
        "reconnaissance": nouveau.get("reconnaissance_sur"),
        "proposition": nouveau.get("proposition_sur"),
    }
    md.write_text(markdown(d, suite, totaux), encoding="utf-8")
    return md, js


def noter_piece(md: Path, js: Path, attachee: bool, raison: str = "") -> None:
    """Le sort de la pièce MLflow, dit dans les deux fichiers."""
    d = json.loads(js.read_text(encoding="utf-8"))
    d["piece_mlflow"] = "attachée" if attachee else f"non attachée : {raison}"
    js.write_text(
        json.dumps(d, ensure_ascii=False, indent=1, default=str) + "\n",
        encoding="utf-8",
    )
    if not attachee:
        md.write_text(
            md.read_text(encoding="utf-8")
            + f"Pièce MLflow : non attachée ({raison}).\n",
            encoding="utf-8",
        )


def code_de(d: dict[str, Any]) -> Code:
    return Code(int(d["code"]))


def aucun() -> list[str]:
    return []


def ecrire_et_attacher(p: Passage, o: Outils) -> tuple[Path, Path]:
    """L'étape 8 : le rapport écrit, puis attaché au run MLflow de la mesure (par la
    commande de MLflow, dans l'environnement du module 10)."""
    lecture = p.cible.dsn("verif_lecture") if p.cible else ""
    pseudonymes = pseudonymes_de(o.lanceur, lecture) if p.cible else aucun
    md, js = ecrire(p, o.livraisons, pseudonymes, suite(p))
    if p.run:
        s = o.lanceur(etapes.attacher(md, p.run))
        noter_piece(md, js, s.code == 0, f"code {s.code}")
    else:
        noter_piece(md, js, False, "aucun run")
    return md, js
