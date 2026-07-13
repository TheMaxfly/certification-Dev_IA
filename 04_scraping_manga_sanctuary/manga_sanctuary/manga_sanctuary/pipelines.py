# manga_sanctuary/pipelines.py

import re

from itemadapter import ItemAdapter

from .items import ReviewItem, VolumeItem


class CleanAndTypePipeline:
    def process_item(self, item, spider):
        adapter = ItemAdapter(item)

        # ---------------------------------------------------
        # 1) Nettoyage générique : strip sur toutes les chaînes
        # ---------------------------------------------------
        for field_name, value in list(adapter.items()):
            if isinstance(value, str):
                adapter[field_name] = value.strip()

        # ---------------------------------------------------
        # 2) Traitement spécifique pour les VolumeItem
        # ---------------------------------------------------
        if isinstance(item, VolumeItem):
            # a) Champs devant être des entiers
            int_fields = [
                "series_popularity_rank",
                "series_members_votes",
                "series_experts_votes",
                "volume_number",
                "volume_pages",
                "volume_tomes_published",
                "volume_tomes_total",
                "volume_members_votes",
                "volume_experts_votes",
            ]

            for field in int_fields:
                value = adapter.get(field)
                if value is None:
                    continue

                # Si c'est déjà un int, on laisse
                if isinstance(value, int):
                    continue

                # Si c'est une chaîne, on essaie d'en extraire un entier
                if isinstance(value, str):
                    m = re.search(r"\d+", value)
                    adapter[field] = int(m.group()) if m else None

            # b) Champs devant être des floats
            float_fields = [
                "series_members_rating",
                "series_experts_rating",
                "volume_members_rating",
                "volume_experts_rating",
            ]

            for field in float_fields:
                value = adapter.get(field)
                if value is None:
                    continue

                # Si c'est déjà un float ou un int, on ne touche pas
                if isinstance(value, (int, float)):
                    continue

                if isinstance(value, str):
                    cleaned = value.replace(",", ".")
                    try:
                        adapter[field] = float(cleaned)
                    except ValueError:
                        adapter[field] = None

            return item

        # ---------------------------------------------------
        # 3) Traitement spécifique pour les ReviewItem
        # ---------------------------------------------------
        if isinstance(item, ReviewItem):
            # review_score doit être un float
            value = adapter.get("review_score")
            if value is not None:
                if isinstance(value, (int, float)):
                    adapter["review_score"] = float(value)
                elif isinstance(value, str):
                    cleaned = value.replace(",", ".")
                    try:
                        adapter["review_score"] = float(cleaned)
                    except ValueError:
                        adapter["review_score"] = None

            # volume_number peut aussi être normalisé en int si besoin
            vnum = adapter.get("volume_number")
            if vnum is not None and not isinstance(vnum, int):
                if isinstance(vnum, str):
                    m = re.search(r"\d+", vnum)
                    adapter["volume_number"] = int(m.group()) if m else None

            return item

        # ---------------------------------------------------
        # 4) Fallback : autre type d'item éventuel
        # ---------------------------------------------------
        return item
