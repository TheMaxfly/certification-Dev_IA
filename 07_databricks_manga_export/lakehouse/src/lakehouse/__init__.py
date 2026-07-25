"""Module 07 v2 — lakehouse Spark+Delta.

Couche COMPLÉMENTAIRE CONSULTATIVE : historise le raw multi-snapshots en
médaillon bronze→silver→gold et produit des métriques qualité comparées
entre snapshots. Hors chemin critique — n'écrit jamais PostgreSQL, ne bloque
jamais une promotion. Les planchers bloquants restent au module 04.
"""

__version__ = "0.2.0"
