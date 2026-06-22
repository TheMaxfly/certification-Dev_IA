#!/usr/bin/env bash
set -euo pipefail

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -U pip
python3 -m pip install -e ".[dev]"
python3 -m preparation_bdd --help >/dev/null
echo "OK: environnement prêt. Active: source .venv/bin/activate"
