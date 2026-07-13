from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .client import KitsuClient
from .exporter import (
    export_most_popular,
    export_top_publishing,
    export_top_rated,
    export_trending_weekly,
)
from .service import MangaService


def main(args: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="api-manga",
        description="Client Kitsu pour récupérer des données manga.",
    )

    parser.add_argument("--slug", help="Slug ou titre simple du manga.")
    parser.add_argument("--tag", help="Catégorie (tag) pour lister plusieurs mangas.")

    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help=(
            "Nombre maximum d'éléments listés "
            "(--tag, --trending, --publishing, --top-rated, --popular)."
        ),
    )

    parser.add_argument(
        "--trending",
        action="store_true",
        help="Affiche le top tendances hebdomadaire (20 éléments par défaut).",
    )

    parser.add_argument(
        "--publishing",
        action="store_true",
        help=(
            "Affiche le top des publications manga "
            "(publishing/current) selon popularité."
        ),
    )

    parser.add_argument(
        "--top-rated",
        action="store_true",
        help="Affiche les mangas les mieux notés (classement par ratingRank).",
    )

    parser.add_argument(
        "--popular",
        action="store_true",
        help="Affiche les mangas les plus populaires (classement par popularityRank).",
    )

    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help=(
            "Offset de pagination (utile pour parcourir le classement "
            "sur --publishing, --top-rated, --popular)."
        ),
    )

    parser.add_argument(
        "--out-dir",
        default="exports",
        help="Dossier de sortie pour les exports JSON (défaut: exports).",
    )

    parsed = parser.parse_args(args=args)

    client = KitsuClient()
    service = MangaService(client)
    out_dir = Path(parsed.out_dir)

    # --- 1) Détail d'un manga par texte (slug/titre) ---
    if parsed.slug:
        summary = service.get_manga_summary(parsed.slug)
        if not summary:
            print(f"Aucun manga trouvé pour '{parsed.slug}'.")
            return
        print("Résumé")
        for key, value in summary.items():
            print(f"- {key}: {value}")
        return

    # --- 2) Liste par tag/catégorie ---
    if parsed.tag:
        catalog = service.list_manga_by_tag(parsed.tag, limit=parsed.limit)
        print(f"Mangas associés au tag '{parsed.tag}':")
        for item in catalog.get("data", []):
            title = (item.get("attributes", {}) or {}).get("canonicalTitle")
            print(f"- {title} (ID: {item.get('id')})")
        return

    # --- 3) Trending hebdo ---
    if parsed.trending:
        out_path = export_trending_weekly(service, out_dir=out_dir, limit=parsed.limit)
        print(f"Export JSON écrit: {out_path}")
        return

    # --- 4) Top publishing (publications) ---
    if parsed.publishing:
        out_path = export_top_publishing(
            service, out_dir=out_dir, limit=parsed.limit, offset=parsed.offset
        )
        print(f"Export JSON écrit: {out_path}")
        return

    # --- 5) Top rated (mieux notés) ---
    if parsed.top_rated:
        out_path = export_top_rated(
            service, out_dir=out_dir, limit=parsed.limit, offset=parsed.offset
        )
        print(f"Export JSON écrit: {out_path}")
        return

    # --- 6) Plus populaires ---
    if parsed.popular:
        out_path = export_most_popular(
            service, out_dir=out_dir, limit=parsed.limit, offset=parsed.offset
        )
        print(f"Export JSON écrit: {out_path}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
