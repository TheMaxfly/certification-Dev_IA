"""Extensions Scrapy utilisées par le lanceur sécurisé."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from scrapy import signals


class RunStatusExtension:
    """Écrit la raison de fermeture pour distinguer un crawl fini d'un arrêt."""

    def __init__(self, status_path: str | None):
        self.status_path = Path(status_path).resolve() if status_path else None

    @classmethod
    def from_crawler(cls, crawler):
        extension = cls(crawler.settings.get("RUN_STATUS_PATH"))
        crawler.signals.connect(extension.spider_closed, signal=signals.spider_closed)
        return extension

    def spider_closed(self, spider, reason):
        if self.status_path is None:
            return

        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "spider": spider.name,
            "reason": str(reason),
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "stats": spider.crawler.stats.get_stats(),
        }
        temporary = self.status_path.with_suffix(self.status_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.status_path)
