from __future__ import annotations

from scrapy.exceptions import CloseSpider


class MangaSanctuaryAccessGuard:
    """Arrête clairement le crawl quand Manga Sanctuary ne renvoie pas son contenu.

    Le crawl complet représente ~89 k pages : un blocage doit interrompre la
    collecte immédiatement et sans le moindre contournement, plutôt que de
    remplir l'export de pages d'erreur. `handle_httpstatus_list` neutralise le
    filtre par défaut de Scrapy pour que ces réponses parviennent au callback —
    sans quoi un 403 serait silencieusement ignoré.
    """

    handle_httpstatus_list = [403, 429, 503]

    _CHALLENGE_MARKERS = (
        "<title>just a moment...</title>",
        "cf-chl-",
        "challenge-platform",
        "cloudflare ray id",
    )

    def ensure_access(self, response) -> None:
        body_start = response.text[:20_000].lower()
        challenge_detected = any(
            marker in body_start for marker in self._CHALLENGE_MARKERS
        )

        if (
            response.status not in self.handle_httpstatus_list
            and not challenge_detected
        ):
            return

        crawler = getattr(self, "crawler", None)
        if crawler is not None:
            crawler.stats.inc_value("manga_sanctuary/access_blocked_count")
            crawler.stats.set_value(
                "manga_sanctuary/access_blocked_status", response.status
            )
            crawler.stats.set_value("manga_sanctuary/access_blocked_url", response.url)

        self.logger.error(
            "Accès Manga Sanctuary indisponible (HTTP %s, challenge=%s) sur %s. "
            "Le crawl est arrêté sans tenter de contourner la protection du site.",
            response.status,
            challenge_detected,
            response.url,
        )
        raise CloseSpider(
            reason=f"manga_sanctuary_access_blocked_http_{response.status}"
        )
