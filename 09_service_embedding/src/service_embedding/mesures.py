"""Mémoire de l'hôte, d'une instance, et surveillance pendant une charge.

Sources, toutes lues sans privilège :
- mémoire vive disponible et swap : `/proc/meminfo` (ce qu'affiche `free`) ;
- swap écrit : compteur `pswpout` de `/proc/vmstat`, la source de la colonne
  `so` de `vmstat` (pages de 4 Kio) ;
- mémoire vive d'un conteneur : son cgroup v2 (`memory.current`, `memory.peak`) ;
- mémoire vidéo : `nvidia-smi`, pour la carte et pour les processus du
  conteneur (pids de l'hôte donnés par `docker top`).
"""

from __future__ import annotations

import subprocess  # nosec B404 — commandes fixes (docker, nvidia-smi), sans shell
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

MIO = 1024 * 1024
PAGE = 4096
# Spec E2 §6. La mémoire vive est passée de 6 à 5 Go le 2026-10-07 (décision de
# Max) : le service charge le modèle en mémoire vidéo, et une seule instance
# tourne à la fois. La mémoire vidéo reste à 4 Go libres.
SEUIL_RAM_DISPONIBLE = 5 * 10**9
SEUIL_VRAM_LIBRE = 4 * 10**9


def _executer(commande: list[str]) -> str:
    return subprocess.run(  # nosec B603 — commande fixe, sans shell
        commande, capture_output=True, text=True, check=True
    ).stdout


def meminfo() -> dict[str, int]:
    """Valeurs de /proc/meminfo, en octets."""
    valeurs = {}
    for ligne in Path("/proc/meminfo").read_text().splitlines():
        cle, reste = ligne.split(":", 1)
        nombre, *unite = reste.split()
        valeurs[cle] = int(nombre) * (1024 if unite == ["kB"] else 1)
    return valeurs


def pages_swap_ecrites() -> int:
    for ligne in Path("/proc/vmstat").read_text().splitlines():
        cle, valeur = ligne.split()
        if cle == "pswpout":
            return int(valeur)
    raise RuntimeError("pswpout absent de /proc/vmstat")


def gpu() -> dict[str, int]:
    """Mémoire vidéo de la carte, en octets."""
    ligne = _executer(
        [
            "nvidia-smi",
            "--query-gpu=memory.total,memory.used,memory.free",
            "--format=csv,noheader,nounits",
        ]
    ).splitlines()[0]
    total, utilisee, libre = (int(x) * MIO for x in ligne.split(","))
    return {"total": total, "utilisee": utilisee, "libre": libre}


def etat_hote() -> dict[str, int]:
    """Ce qu'on relève avant chaque lancement (spec §6)."""
    m = meminfo()
    g = gpu()
    return {
        "ram_disponible": m["MemAvailable"],
        "swap_occupe": m["SwapTotal"] - m["SwapFree"],
        "vram_libre": g["libre"],
        "vram_utilisee": g["utilisee"],
    }


def seuils_tenus(etat: dict[str, int]) -> list[str]:
    """Les seuils non tenus, en clair ; liste vide si tout est tenu."""
    ecarts = []
    if etat["ram_disponible"] < SEUIL_RAM_DISPONIBLE:
        ecarts.append(
            f"mémoire vive disponible {etat['ram_disponible'] / 1e9:.2f} Go "
            f"< {SEUIL_RAM_DISPONIBLE / 1e9:.0f} Go"
        )
    if etat["vram_libre"] < SEUIL_VRAM_LIBRE:
        ecarts.append(
            f"mémoire vidéo libre {etat['vram_libre'] / 1e9:.2f} Go "
            f"< {SEUIL_VRAM_LIBRE / 1e9:.0f} Go"
        )
    return ecarts


def cgroup_conteneur(nom: str) -> Path:
    identifiant = _executer(["docker", "inspect", "-f", "{{.Id}}", nom]).strip()
    chemin = Path(f"/sys/fs/cgroup/system.slice/docker-{identifiant}.scope")
    if not chemin.is_dir():
        raise RuntimeError(f"cgroup introuvable pour {nom} : {chemin}")
    return chemin


def vram_conteneur(nom: str) -> int:
    """Mémoire vidéo des processus du conteneur, en octets."""
    pids = set(_executer(["docker", "top", nom, "-eo", "pid"]).split()[1:])
    occupee = 0
    for ligne in _executer(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,used_memory",
            "--format=csv,noheader,nounits",
        ]
    ).splitlines():
        pid, memoire = (x.strip() for x in ligne.split(","))
        if pid in pids:
            occupee += int(memoire) * MIO
    return occupee


def stat_cgroup(cgroup: Path) -> dict[str, int]:
    return {
        cle: int(valeur)
        for cle, valeur in (
            ligne.split() for ligne in (cgroup / "memory.stat").read_text().splitlines()
        )
    }


def memoire_conteneur(nom: str) -> dict[str, int]:
    """RAM et VRAM d'une instance, en octets.

    `ram` et `ram_pic` (cgroup) comptent aussi le cache de fichiers : les poids
    lus au chargement y restent, récupérables. La mémoire réellement consommée
    est `ram_anonyme` (`anon` de memory.stat) ; `ram_cache_fichiers` est le reste.
    """
    cgroup = cgroup_conteneur(nom)
    stat = stat_cgroup(cgroup)
    return {
        "ram": int((cgroup / "memory.current").read_text()),
        "ram_pic": int((cgroup / "memory.peak").read_text()),
        "ram_anonyme": stat["anon"],
        "ram_cache_fichiers": stat["file"],
        "vram": vram_conteneur(nom),
    }


@dataclass
class Surveillance:
    """Échantillonne, pendant une charge, la mémoire d'une instance et le swap.

    `swap_continu` passe à vrai quand du swap s'écrit `secondes_continues`
    secondes d'affilée : c'est le signal d'arrêt de la spec (§6). Une écriture
    ponctuelle ne le déclenche pas.
    """

    conteneur: str
    periode: float = 1.0
    secondes_continues: int = 10
    vram_pic: int = 0
    vram_carte_pic: int = 0
    ram_anonyme_pic: int = 0
    swap_ko_ecrits: float = 0.0
    secondes_avec_swap: int = 0
    serie_swap: int = 0
    swap_continu: bool = False
    echantillons: list[dict] = field(default_factory=list)
    _arret: threading.Event = field(default_factory=threading.Event)
    _fil: threading.Thread | None = None
    _cgroup: Path | None = None

    def _boucle(self) -> None:
        precedent = pages_swap_ecrites()
        while not self._arret.wait(self.periode):
            courant = pages_swap_ecrites()
            so_ko = (courant - precedent) * PAGE / 1024
            precedent = courant
            try:
                vram = vram_conteneur(self.conteneur)
                carte = gpu()["utilisee"]
                anon = stat_cgroup(self._cgroup)["anon"] if self._cgroup else -1
            except (subprocess.CalledProcessError, OSError):
                vram, carte, anon = -1, -1, -1
            self.ram_anonyme_pic = max(self.ram_anonyme_pic, anon)
            self.vram_pic = max(self.vram_pic, vram)
            self.vram_carte_pic = max(self.vram_carte_pic, carte)
            self.swap_ko_ecrits += so_ko
            if so_ko > 0:
                self.secondes_avec_swap += 1
                self.serie_swap += 1
            else:
                self.serie_swap = 0
            if self.serie_swap >= self.secondes_continues:
                self.swap_continu = True
            self.echantillons.append(
                {
                    "t": time.time(),
                    "so_ko": so_ko,
                    "vram": vram,
                    "vram_carte": carte,
                    "ram_anonyme": anon,
                }
            )

    def demarrer(self) -> Surveillance:
        self._cgroup = cgroup_conteneur(self.conteneur)
        self._fil = threading.Thread(target=self._boucle, daemon=True)
        self._fil.start()
        return self

    def arreter(self) -> dict:
        self._arret.set()
        if self._fil is not None:
            self._fil.join()
        bilan = {
            "vram_pic": self.vram_pic,
            "vram_carte_pic": self.vram_carte_pic,
            "ram_anonyme_pic": self.ram_anonyme_pic,
            "swap_ko_ecrits": round(self.swap_ko_ecrits),
            "secondes_avec_swap": self.secondes_avec_swap,
            "swap_continu": self.swap_continu,
            "echantillons": len(self.echantillons),
        }
        try:
            bilan |= {"ram_pic": memoire_conteneur(self.conteneur)["ram_pic"]}
        except (subprocess.CalledProcessError, RuntimeError, OSError):
            bilan |= {"ram_pic": -1}
        return bilan
