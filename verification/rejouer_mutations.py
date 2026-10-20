"""Rejouer les mutations versionnées : chacune doit faire rougir son test.

    make verify-mutations                 (toutes ; poste seulement, avant une
                                           demande vers `main`)
    uv run --no-project python -I verification/rejouer_mutations.py [ID …]
    uv run --no-project python -I verification/rejouer_mutations.py --controler
                                          (tâche statique : chaque mutation se lit,
                                           et vise un texte présent une fois)

Une mutation = un fichier de `verification/mutations/` (TOML) : le fichier visé, le
texte exact à remplacer (présent une fois, et une seule), son remplaçant, et la
commande qui doit échouer quand la mutation est posée.

Le rejeu ne touche jamais l'arbre de travail : il se fait dans un arbre jetable
(`git worktree`, au commit courant), retiré à la fin. L'arbre doit donc être propre
— on commite d'abord, on rejoue ensuite.

Une mutation ne prouve rien si son test ne tourne pas : chaque commande est d'abord
jouée SANS mutation, et doit passer ; pour pytest, sans aucun test sauté. Ensuite,
mutation posée, elle doit échouer (un code 5 de pytest — aucun test collecté — est
une erreur d'essai, pas une détection). Le fichier est rendu à l'identique après
chaque mutation, et chaque passage a son propre cache de bytecode.

Code 0 si toutes les mutations sont détectées ; sinon 1, avec la liste.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import tomllib

RACINE = Path(__file__).resolve().parents[1]
DOSSIER = RACINE / "verification" / "mutations"
CLES = {"id", "origine", "fichier", "avant", "apres", "dossier", "commande"}


def charger(ids: list[str]) -> list[dict]:
    mutations = []
    for chemin in sorted(DOSSIER.glob("*.toml")):
        m = tomllib.loads(chemin.read_text(encoding="utf-8"))
        if set(m) != CLES:
            raise SystemExit(f"{chemin.name} : clés {sorted(set(m) ^ CLES)} en écart")
        if m["id"] != chemin.stem:
            raise SystemExit(f"{chemin.name} : id {m['id']!r} ≠ nom du fichier")
        if m["avant"] == m["apres"]:
            raise SystemExit(f"{chemin.name} : la mutation ne change rien")
        mutations.append(m)
    if ids:
        inconnus = set(ids) - {m["id"] for m in mutations}
        if inconnus:
            raise SystemExit(f"mutations inconnues : {sorted(inconnus)}")
        mutations = [m for m in mutations if m["id"] in ids]
    if not mutations:
        raise SystemExit(f"aucune mutation dans {DOSSIER}")
    return mutations


def environnement() -> dict[str, str]:
    """Le même environnement vidé que `verifier.sh` : ni VIRTUAL_ENV, ni DSN."""
    garder = "HOME USER TMPDIR JAVA_HOME DOCKER_HOST DOCKER_CONFIG XDG_RUNTIME_DIR CI"
    env = {k: os.environ[k] for k in garder.split() if k in os.environ}
    env["LANG"] = "C.UTF-8"
    env["PATH"] = ":".join(
        p for p in os.environ.get("PATH", "").split(":") if "/.venv/" not in p
    )
    env |= {k: v for k, v in os.environ.items() if k.startswith("UV_")}
    return env


def lancer(arbre: Path, m: dict) -> tuple[int, str]:
    # Un cache de bytecode neuf à chaque passage : un .pyc d'un passage précédent,
    # de même date et de même taille, ferait tourner l'ancien code (faux verts vus
    # en E3, bloc C).
    with tempfile.TemporaryDirectory(prefix="pyc-") as pyc:
        r = subprocess.run(  # nosec B603 — commande lue dans un fichier versionné
            m["commande"],
            cwd=arbre / m["dossier"],
            env=environnement() | {"PYTHONPYCACHEPREFIX": pyc},
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    return r.returncode, r.stdout + r.stderr


def est_pytest(m: dict) -> bool:
    return "pytest" in m["commande"]


def temoin(arbre: Path, m: dict) -> str | None:
    """Sans mutation, la commande doit passer, et ses tests tourner : sinon, la
    raison."""
    rc, sortie = lancer(arbre, m)
    if rc != 0:
        return f"échoue sans mutation (code {rc})"
    if est_pytest(m) and re.search(r"\b\d+ skipped\b", sortie):
        return "des tests sautent sans mutation : elle ne prouverait rien"
    if est_pytest(m) and not re.search(r"\b\d+ passed\b", sortie):
        return "aucun test passé sans mutation"
    return None


def controler() -> int:
    """Sans rien lancer : chaque mutation se lit, et son texte visé est présent une
    fois, et une seule, dans l'arbre de travail."""
    mutations = charger([])
    ecarts = []
    for m in mutations:
        cible = RACINE / m["fichier"]
        texte = cible.read_text(encoding="utf-8") if cible.is_file() else ""
        if (n := texte.count(m["avant"])) != 1:
            ecarts.append(f"{m['id']} : texte visé trouvé {n} fois dans {m['fichier']}")
        if not (RACINE / m["dossier"]).is_dir():
            ecarts.append(f"{m['id']} : dossier {m['dossier']} absent")
    for e in ecarts:
        print(f"  {e}", file=sys.stderr)
    print(f"mutations : {len(mutations)} lues, {len(ecarts)} écart(s)")
    return 1 if ecarts else 0


def git(*args: str) -> str:
    return subprocess.run(  # nosec B603 B607 — commande fixe, sans shell
        ["git", *args], cwd=RACINE, capture_output=True, text=True, check=True
    ).stdout.strip()


def rejouer(arbre: Path, mutations: list[dict]) -> list[tuple[dict, str, str]]:
    temoins: dict[tuple, str | None] = {}
    resultats = []
    for m in mutations:
        fichier = arbre / m["fichier"]
        texte = fichier.read_text(encoding="utf-8")
        if (n := texte.count(m["avant"])) != 1:
            resultats.append((m, "invalide", f"texte visé trouvé {n} fois"))
        else:
            cle = (m["dossier"], tuple(m["commande"]))
            if cle not in temoins:
                temoins[cle] = temoin(arbre, m)
            if temoins[cle]:
                resultats.append((m, "invalide", temoins[cle]))
            else:
                fichier.write_text(
                    texte.replace(m["avant"], m["apres"]), encoding="utf-8"
                )
                try:
                    rc, _ = lancer(arbre, m)
                finally:
                    fichier.write_text(texte, encoding="utf-8")
                if est_pytest(m) and rc == 5:
                    resultats.append((m, "invalide", "aucun test collecté"))
                elif rc != 0:
                    resultats.append((m, "détectée", f"code {rc}"))
                else:
                    resultats.append(
                        (m, "MANQUÉE", "la commande passe, mutation posée")
                    )
        _, etat, raison = resultats[-1]
        print(f"  {etat:9} {m['id']:8} {raison}", flush=True)
    return resultats


def main(argv: list[str]) -> int:
    if argv == ["--controler"]:
        return controler()
    mutations = charger(argv)
    if git("status", "--porcelain"):
        raise SystemExit(
            "arbre de travail non propre : commiter d'abord, rejouer ensuite"
        )
    commit = git("rev-parse", "--short=12", "HEAD")
    arbre = Path(tempfile.mkdtemp(prefix="mutations-")) / "depot"
    git("worktree", "add", "--detach", "-q", str(arbre), "HEAD")
    print(f"{len(mutations)} mutation(s), au commit {commit}, dans un arbre jetable")
    debut = time.monotonic()
    try:
        resultats = rejouer(arbre, mutations)
    finally:
        git("worktree", "remove", "--force", str(arbre))
        shutil.rmtree(arbre.parent, ignore_errors=True)
    detectees = sum(1 for _, etat, _ in resultats if etat == "détectée")
    duree = round(time.monotonic() - debut)
    print(f"\n{detectees} / {len(resultats)} mutation(s) détectée(s), {duree} s")
    ecarts = [(m, e, r) for m, e, r in resultats if e != "détectée"]
    for m, etat, raison in ecarts:
        print(f"  {etat} {m['id']} ({m['fichier']}) : {raison}", file=sys.stderr)
    return 1 if ecarts else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
