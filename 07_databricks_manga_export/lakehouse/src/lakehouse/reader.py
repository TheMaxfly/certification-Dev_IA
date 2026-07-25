"""Livrable 6 — le lecteur HORS Spark (la boucle C1 fermée).

Extrait les métriques du lakehouse SANS démarrer le conteneur ni la JVM :
DuckDB lit les tables gold Delta via son extension `delta` (`delta_scan`), qui
respecte le journal de transactions (pas de lecture parquet naïve qui
compterait des fichiers tombstoned). C'est ce lecteur que la checklist du cycle
mensuel consulte — chaque moteur à sa place : Spark écrit le médaillon,
DuckDB sort la synthèse en ~1 s.
"""

from __future__ import annotations

import time

import duckdb

from .config import chemin_table, lakehouse_root


def connexion() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    # L'extension delta est chargée à la demande ; installée une fois puis mise
    # en cache (bundlée dans le conteneur pour éviter tout réseau à l'usage).
    con.execute("INSTALL delta; LOAD delta;")
    return con


def scan(con: duckdb.DuckDBPyConnection, table: str):
    """`SELECT * FROM delta_scan(<chemin de la table>)`."""
    chemin = str(chemin_table(table))
    return con.sql(f"SELECT * FROM delta_scan('{chemin}')")


def synthese(con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """Tableau de synthèse consommable par le cycle mensuel (métriques clés)."""
    fermer = con is None
    con = con or connexion()
    try:
        # Colonnes explicites : on évite `computed_at` (timestamp), dont la
        # conversion DuckDB→Python tirerait une dépendance pytz inutile ici.
        vol_cols = [
            "snapshot_date",
            "grain",
            "n",
            "n_precedent",
            "delta_abs",
            "delta_pct",
        ]
        volumetrie = con.sql(
            f"SELECT {', '.join(vol_cols)} "
            f"FROM delta_scan('{chemin_table('gold.volumetrie')}') "
            "ORDER BY grain, snapshot_date"
        ).fetchall()
        deficits = con.sql(
            "SELECT prefixe, snapshot_date, delta_abs, disparues, nouvelles "
            f"FROM delta_scan('{chemin_table('gold.completude_par_prefixe')}') "
            "WHERE disparues >= 5 ORDER BY disparues DESC"
        ).fetchall()
        recouvrement = con.sql(
            "SELECT cle_type, n_precedent, n_courant, disparues "
            f"FROM delta_scan('{chemin_table('gold.recouvrement_snapshots')}') "
            "ORDER BY cle_type"
        ).fetchall()
        ean = con.sql(
            "SELECT snapshot_date, total_volumes, renseignes, cle_valide "
            f"FROM delta_scan('{chemin_table('gold.qualite_ean')}') "
            "ORDER BY snapshot_date"
        ).fetchall()
        return {
            "volumetrie": [dict(zip(vol_cols, r, strict=False)) for r in volumetrie],
            "deficits_prefixe": deficits,
            "recouvrement": recouvrement,
            "qualite_ean": ean,
        }
    finally:
        if fermer:
            con.close()


def afficher_synthese() -> None:
    """Sort la synthèse et le temps de lecture (démonstration hors Spark)."""
    debut = time.perf_counter()
    con = connexion()
    s = synthese(con)
    duree = time.perf_counter() - debut

    print("# Synthèse qualité (lecteur DuckDB, hors Spark)")
    print(f"# lakehouse : {lakehouse_root()}")
    print()
    print("## Déficits de préfixe (disparues ≥ 5)")
    for prefixe, snap, delta, disp, nouv in s["deficits_prefixe"]:
        marque = " ⚠️" if prefixe == "di" else ""
        print(
            f"  {prefixe:>4}  {snap}  Δnet={delta:+}  disparues={disp}  "
            f"nouvelles={nouv}{marque}"
        )
    print()
    print("## Recouvrement (clés non revues)")
    for cle_type, n_prec, n_cur, disp in s["recouvrement"]:
        print(f"  {cle_type:>12}  {n_prec} → {n_cur}  disparues={disp}")
    print()
    print("## Qualité EAN")
    for snap, total, renseignes, valide in s["qualite_ean"]:
        print(
            f"  {snap}  volumes={total}  renseignés={renseignes}  clé_valide={valide}"
        )
    print()
    print(f"— synthèse produite en {duree * 1000:.0f} ms (sans JVM).")


def main() -> int:
    afficher_synthese()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
