"""Le journal de déploiement, versionné : `journal/<horodatage>_<cible>_<étiquette>.md`.

C'EST LA PREUVE que la grille demande (décision 6 du cadre) : qui (l'utilisateur du
poste), quand, l'étiquette et son commit, les images et leurs empreintes, les migrations
avant et après, le verdict de santé, la durée, les appels facturés relevés, et pour un
retour arrière le motif et ce qui a été quitté. `index.md` reçoit une ligne par journal.

AUCUN SECRET, AUCUNE ADRESSE autre que `127.0.0.1` : avant d'écrire, le texte est relu
par les motifs **du hook lui-même** (`.githooks/cles-fournisseurs` : clés du poste et
chaînes d'aspect ; `.githooks/controler-texte` : mentions) — le journal n'est pas écrit
s'il en porte un. Les motifs ne sont pas recopiés : ils ne peuvent pas diverger.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from livraison.codes import Arret, Code
from livraison.images import Construite
from livraison.outils import Commande, Outils, lignes
from livraison.sante import Controle

INDEX = "index.md"
EN_TETE_INDEX = (
    "# Journal des déploiements\n\n"
    "Une ligne par déploiement, retour arrière ou arrêt, écrite par `livrer`\n"
    "(`15_livraison/README.md`, « Le journal »).\n\n"
    "| Horodatage (UTC) | Cible | Geste | Étiquette | Verdict | Journal |\n"
    "|---|---|---|---|---|---|\n"
)
ADRESSE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
#: La relecture par les motifs du hook : le fragment sourcé, comme au pre-commit, sur
#: les lignes « journal<TAB>ligne » ; puis les mentions. Rend les refus, un par ligne.
RELECTURE = r"""
set -uo pipefail
. .githooks/cles-fournisseurs
echecs=()
controler_cles "" < <(sed 's/^/journal\t/')
printf '%s\n' "${echecs[@]+"${echecs[@]}"}"
"""

Relecture = Callable[[str], list[str]]


@dataclass
class Entree:
    cible: str
    geste: str  # « déploiement », « retour arrière »
    etiquette: str
    commit: str
    debut: datetime
    utilisateur: str
    duree_s: float = 0.0
    images: list[Construite] = field(default_factory=list[Construite])
    migrations_avant: str = "—"
    migrations_apres: str = "—"
    controles: list[Controle] = field(default_factory=list[Controle])
    verdict: str = "—"
    compteur_avant: str = "non relevé (pile arrêtée)"
    compteur_apres: str = "non relevé"
    motif: str | None = None
    quittee: str | None = None
    notes: list[str] = field(default_factory=list[str])

    @property
    def horodatage(self) -> str:
        return self.debut.strftime("%Y%m%dT%H%M%SZ")

    @property
    def nom_fichier(self) -> str:
        return f"{self.horodatage}_{self.cible}_{self.etiquette}.md"


def rendre(e: Entree) -> str:
    t = [
        f"# {e.geste.capitalize()} — cible {e.cible}, étiquette `{e.etiquette}`",
        "",
        "| Champ | Valeur |",
        "|---|---|",
        f"| Cible | `{e.cible}` |",
        f"| Geste | {e.geste} |",
        f"| Étiquette | `{e.etiquette}` (commit `{e.commit[:12]}`) |",
        f"| Début (UTC) | {e.debut.strftime('%Y-%m-%d %H:%M:%S')} |",
        f"| Durée | {e.duree_s:.0f} s |",
        f"| Utilisateur du poste | `{e.utilisateur}` |",
        f"| Verdict | **{e.verdict}** |",
    ]
    if e.motif is not None:
        t.append(f"| Motif | {e.motif} |")
    if e.quittee is not None:
        t.append(f"| Quitté | {e.quittee} |")
    t += ["", "## Images", "", "| Image | Empreinte | Statut |", "|---|---|---|"]
    t += [f"| `{c.image.ref}` | `{c.empreinte}` | {c.statut} |" for c in e.images]
    t += [
        "",
        "## Migrations de la base",
        "",
        f"- avant : {e.migrations_avant}",
        f"- après : {e.migrations_apres}",
        "",
        "## Santé",
        "",
        "| Service | Contrôle | Attendu | Constaté | Verdict |",
        "|---|---|---|---|---|",
    ]
    t += [
        f"| {c.service} | {c.objet} | {c.attendu} | {c.constate} |"
        f" {'tenu' if c.tenu else '**manque**'} |"
        for c in e.controles
    ]
    t += [
        "",
        "## Appels facturés (compteurs de l'API, depuis son démarrage)",
        "",
        f"- avant : {e.compteur_avant}",
        f"- après : {e.compteur_apres}",
    ]
    if e.notes:
        t += ["", "## Notes", "", *[f"- {n}" for n in e.notes]]
    return "\n".join(t) + "\n"


def relecture_du_hook(o: Outils) -> Relecture:
    """La relecture réelle : le fragment du hook, lancé à la racine du dépôt."""

    def relire(texte: str) -> list[str]:
        s = o.lanceur(
            Commande(
                "hook.cles", ("bash", "-c", RELECTURE), dossier=o.racine, entree=texte
            )
        )
        refus = lignes(s)
        if s.code != 0 and not refus:
            refus = ["relecture des clés impossible"]
        m = o.lanceur(
            Commande(
                "hook.mentions",
                ("bash", ".githooks/controler-texte", "journal de déploiement"),
                dossier=o.racine,
                entree=texte,
            )
        )
        if m.code != 0:
            refus.append("mention refusée par controler-texte")
        return refus

    return relire


def adresses_hors_bouclage(texte: str) -> list[str]:
    return sorted({a for a in ADRESSE.findall(texte) if a != "127.0.0.1"})


def exiger_propre(texte: str, relire: Relecture) -> None:
    refus = relire(texte)
    hors = adresses_hors_bouclage(texte)
    if hors:
        refus.append(f"adresses hors du bouclage : {', '.join(hors)}")
    if refus:
        raise Arret(
            Code.INTERNE,
            "journal non écrit : il porterait ce que le hook refuse — "
            + " ; ".join(refus),
        )


def ligne_index(e: Entree) -> str:
    return (
        f"| {e.debut.strftime('%Y-%m-%d %H:%M:%S')} | {e.cible} | {e.geste} |"
        f" `{e.etiquette}` | {e.verdict} | [{e.nom_fichier}]({e.nom_fichier}) |\n"
    )


def ecrire(o: Outils, e: Entree, *, relire: Relecture | None = None) -> Path:
    """Relit, écrit le journal, ajoute sa ligne à l'index ; rend son chemin."""
    dossier = o.journal
    texte = rendre(e)
    relecture = relire or relecture_du_hook(o)
    exiger_propre(texte, relecture)
    ligne = ligne_index(e)
    exiger_propre(ligne, relecture)
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / e.nom_fichier
    chemin.write_text(texte, encoding="utf-8")
    index = dossier / INDEX
    actuel = index.read_text(encoding="utf-8") if index.is_file() else EN_TETE_INDEX
    index.write_text(actuel + ligne, encoding="utf-8")
    return chemin


def dernier(o: Outils, cible: str) -> Path | None:
    """Le journal le plus récent de la cible."""
    trouves = sorted(o.journal.glob(f"*_{cible}_*.md"))
    return trouves[-1] if trouves else None


def completer(
    o: Outils,
    chemin: Path,
    titre: str,
    puces: list[str],
    *,
    relire: Relecture | None = None,
) -> None:
    """Ajoute une section au journal (l'arrêt d'une cible), relue elle aussi."""
    ajout = "\n" + "\n".join([f"## {titre}", "", *[f"- {p}" for p in puces]]) + "\n"
    exiger_propre(ajout, relire or relecture_du_hook(o))
    with chemin.open("a", encoding="utf-8") as f:
        f.write(ajout)
