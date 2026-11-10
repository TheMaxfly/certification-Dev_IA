"""Le paquet du module : il s'importe et dit sa version."""

from __future__ import annotations

import application


def test_le_paquet_dit_sa_version():
    assert application.__version__ == "0.9.1"
