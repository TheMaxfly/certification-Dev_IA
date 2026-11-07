"""Le rapport de livraison : `livraisons/<horodatage UTC>_<candidat>.md` (pour un
humain) et `.json` (pour la commande de promotion) — décisions de l'étape, § 8.

Ni secret, ni pseudonyme, ni texte de passage. La sortie d'une étape arrêtée n'entre
qu'en extrait (dix dernières lignes, 200 caractères chacune), après masquage : les
DSN à mot de passe, les formes de clés connues, les secrets connus de la commande, et
les pseudonymes des membres — lus en préproduction sous `verif_lecture`, seulement
quand un extrait est à écrire, jamais écrits.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

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


def markdown(d: dict[str, Any], suite: str) -> str:
    """Le rapport pour un humain : en-tête, tableau des étapes, chiffres, suite."""

    def ligne_etape(e: dict[str, Any]) -> str:
        details = e.get("motif") or ", ".join(
            f"{k} : {v}"
            for k, v in e["details"].items()
            if k not in ("info", "service", "empreintes", "regle")
        )
        return (
            f"| {e['numero']} | {e['nom']} | {e['statut']} | {e['duree_s']}"
            f" | {details or '—'} |"
        )

    m = d["mesure"]
    corps = [
        f"# Rapport de livraison — {d['candidat']['nom']} — {d['livraison']}",
        "",
        f"**Code de sortie : {d['code']} ({d['code_nom']})** — {d['sens']}.",
        "",
        "## Ce qui a été jugé",
        "",
        "| Objet | Valeur |",
        "|---|---|",
        f"| Environnement | {d['environnement']}, base `{d['base']}` |",
        f"| Dossier des échantillons | `{d['dossier_echantillons']}` |",
        f"| Candidat | `{d['candidat']['fichier']}` — {d['candidat']['motif']} |",
        f"| Commit | `{d['commit']}` |",
        f"| Corpus | {d['candidat']['corpus']} |",
        f"| Encodage | {d['encodage']} |",
        f"| Mesure | n° {d['candidat']['mesure']} ; run `{d['run']}`"
        f" ({d['experience']}) |",
        f"| Résultat | global {m['global']} / {m['questions']} ;"
        f" reconnaissance {m['reconnaissance']} ; proposition {m['proposition']} ;"
        f" nDCG@10 {m['ndcg_10']} |",
        f"| Règle | `{d['regle'].get('fichier')}`,"
        f" commit `{d['regle'].get('commit')}` du {d['regle'].get('date')} |",
        f"| Verdict | {d['verdict'] or '—'} |",
        f"| Conditions manquantes | {', '.join(d['conditions_manquantes']) or '—'} |",
        f"| Image de l'API | `{d['image'] or '—'}` |",
        "",
        "## Les étapes",
        "",
        "| N° | Étape | Statut | Durée (s) | Détail |",
        "|---|---|---|---|---|",
        *[ligne_etape(e) for e in d["etapes"]],
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
    md.write_text(markdown(d, suite), encoding="utf-8")
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
