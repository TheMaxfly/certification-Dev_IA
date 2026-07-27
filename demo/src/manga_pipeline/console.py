"""Console `manga-pipeline` — écran d'état + menu d'actions.

Mode par défaut : LECTURE SEULE. Les actions qui écrivent en base ne sont pas
seulement grisées, elles sont absentes du menu — on ne déclenche pas par erreur
ce qui n'est pas proposé. `--ecriture` les rend disponibles, chacune derrière
une confirmation tapée en toutes lettres.
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from . import etat
from .actions import Action, Impact, actions_visibles, par_phase
from .executeur import (
    RefusEcriture,
    confirmation_valide,
    executer,
    texte_confirmation,
)

COULEUR = {"ok": "green", "attention": "yellow", "erreur": "red"}
COULEUR_IMPACT = {
    Impact.DOCUMENTE: "dim",
    Impact.LECTURE: "green",
    Impact.FICHIERS: "cyan",
    Impact.BASE: "red",
}


def afficher_etat(console: Console) -> None:
    """L'écran qu'on laisse affiché en ouverture de soutenance."""
    console.print()
    console.print(
        Panel(
            Text(
                "Pipeline ELT manga — état réel du système",
                justify="center",
                style="bold",
            ),
            subtitle=f"racine : {etat.chemin_racine()}",
        )
    )
    for bloc in etat.collecter():
        table = Table(show_header=False, box=None, padding=(0, 2), title_justify="left")
        table.add_column(style="bold")
        table.add_column()
        for libelle, valeur, niveau in bloc.lignes:
            table.add_row(libelle, Text(valeur, style=COULEUR.get(niveau, "white")))
        if bloc.note:
            table.add_row("", Text(bloc.note, style="dim italic"))
        console.print(Panel(table, title=bloc.titre, title_align="left"))


def afficher_menu(console: Console, actions: list[Action], lecture_seule: bool) -> None:
    console.print(Rule("Actions"))
    mode = (
        Text("LECTURE SEULE — écritures base masquées", style="bold green")
        if lecture_seule
        else Text(
            "ÉCRITURE ACTIVÉE — les actions base sont proposées", style="bold red"
        )
    )
    console.print(mode)
    console.print()
    numero = 1
    for phase, membres in par_phase(actions).items():
        console.print(Text(f"[{phase.value}]", style="bold magenta"))
        for action in membres:
            etiquette = Text(f"  {numero:>2}. ", style="bold")
            etiquette.append(action.libelle)
            etiquette.append(
                f"   ({action.impact.value})",
                style=COULEUR_IMPACT.get(action.impact, "white"),
            )
            if action.duree_typique:
                # La donnée porte déjà son « ~ » quand la durée est approchée.
                etiquette.append(f"  {action.duree_typique}", style="dim")
            console.print(etiquette)
            numero += 1
        console.print()
    console.print(Text("   e. Réafficher l'état    q. Quitter", style="dim"))


def presenter_action(console: Console, action: Action) -> None:
    """La commande EXACTE + la phrase à dire — avant toute exécution."""
    console.print()
    console.print(
        Panel(
            Text(action.commande, style="bold white on grey23"),
            title=f"commande réelle · cwd={action.cwd.name}",
            title_align="left",
            border_style=COULEUR_IMPACT.get(action.impact, "white"),
        )
    )
    console.print(Text("À dire : ", style="bold") + Text(action.explication))
    console.print(Text("Vérification : ", style="bold") + Text(action.verification))


def jouer(console: Console, action: Action, lecture_seule: bool) -> None:
    presenter_action(console, action)

    if action.impact is Impact.DOCUMENTE:
        console.print(
            Text(
                "Action documentée : la console ne l'exécute pas "
                "(sortie réseau / hors périmètre démo).",
                style="yellow",
            )
        )
        return

    saisie = console.input(f"\n{texte_confirmation(action)} > ")
    if not confirmation_valide(action, saisie):
        console.print(Text("Annulé.", style="yellow"))
        return

    console.print(Rule("sortie de la commande"))
    try:
        resultat = executer(
            action, lecture_seule=lecture_seule, ecrire_ligne=console.out
        )
    except RefusEcriture as refus:
        console.print(Text(str(refus), style="red bold"))
        return
    console.print(Rule())

    style = "green bold" if resultat.ok else "red bold"
    verdict = "SUCCÈS" if resultat.ok else "ÉCHEC"
    if resultat.interrompu:
        verdict = "INTERROMPU"
    console.print(
        Text(
            f"{verdict} · code retour {resultat.code} · {resultat.duree_s:.1f} s",
            style=style,
        )
    )


def boucle(console: Console, lecture_seule: bool) -> int:
    afficher_etat(console)
    while True:
        actions = actions_visibles(lecture_seule)
        afficher_menu(console, actions, lecture_seule)
        try:
            choix = console.input("\nChoix > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 0
        if choix in {"q", "quit", "quitter"}:
            return 0
        if choix in {"e", "etat", "état"}:
            afficher_etat(console)
            continue
        if not choix.isdigit() or not 1 <= int(choix) <= len(actions):
            console.print(Text("Choix invalide.", style="yellow"))
            continue
        jouer(console, actions[int(choix) - 1], lecture_seule)


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        prog="manga-pipeline",
        description=(
            "Console de démonstration du pipeline ELT. Elle orchestre les CLI "
            "existantes du dépôt — elle n'implémente aucune logique métier."
        ),
    )
    parseur.add_argument(
        "--ecriture",
        action="store_true",
        help=(
            "Rend disponibles les actions qui écrivent en base (masquées par "
            "défaut). Chacune exige une confirmation tapée en toutes lettres."
        ),
    )
    parseur.add_argument(
        "--lecture-seule",
        action="store_true",
        help="Mode par défaut, explicite : aucune action d'écriture base au menu.",
    )
    parseur.add_argument(
        "--etat",
        action="store_true",
        help="Affiche l'écran d'état et sort (aucun menu, aucune exécution).",
    )
    return parseur


def main(argv: list[str] | None = None) -> int:
    arguments = construire_parseur().parse_args(argv)
    if arguments.ecriture and arguments.lecture_seule:
        print("--ecriture et --lecture-seule s'excluent.", file=sys.stderr)
        return 2
    lecture_seule = not arguments.ecriture
    console = Console()
    if arguments.etat:
        afficher_etat(console)
        return 0
    return boucle(console, lecture_seule)


if __name__ == "__main__":
    raise SystemExit(main())
