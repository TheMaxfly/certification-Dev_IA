import argparse
import sys
from pathlib import Path

import pandas as pd
import great_expectations as gx


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True, help="Chemin vers un .jsonl (ex: data/enriched/manganews_series.jsonl)")
    p.add_argument("--required-cols", default="", help="Colonnes obligatoires, séparées par des virgules")
    p.add_argument("--min-rows", type=int, default=1, help="Nombre minimum de lignes")
    args = p.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Fichier introuvable: {path}", file=sys.stderr)
        return 2

    df = pd.read_json(path, lines=True)

    # Validator runtime (pas besoin de DataContext)
    v = gx.from_pandas(df)

    # Tests de base (tu peux en ajouter autant que tu veux)
    v.expect_table_row_count_to_be_between(min_value=args.min_rows, max_value=None)

    required = [c.strip() for c in args.required_cols.split(",") if c.strip()]
    for col in required:
        v.expect_column_to_exist(col)
        v.expect_column_values_to_not_be_null(col)

    result = v.validate()

    success = bool(result.get("success", False))
    print("success =", success)

    # Affiche un petit résumé lisible si échec
    if not success:
        stats = result.get("statistics", {})
        print("statistics =", stats)
        for r in result.get("results", []):
            if not r.get("success", True):
                exp = r.get("expectation_config", {}).get("expectation_type")
                kwargs = r.get("expectation_config", {}).get("kwargs")
                print("FAILED:", exp, kwargs)

    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
