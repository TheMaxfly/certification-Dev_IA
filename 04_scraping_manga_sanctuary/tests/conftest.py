"""Rend le package Scrapy importable : il vit sous manga_sanctuary/, aux côtés
de scrapy.cfg, et non à la racine du module."""

from __future__ import annotations

import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR / "manga_sanctuary"))
