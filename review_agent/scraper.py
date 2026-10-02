"""Fetch Google Play reviews for an Android app."""

from dataclasses import dataclass
from datetime import datetime

from google_play_scraper import Sort, reviews


@dataclass
class Review:
    id: str
    rating: int
    text: str
    thumbs_up: int
    app_version: str | None
    posted_at: datetime


def fetch_reviews(
    app_id: str,
    count: int = 200,
    lang: str = "en",
    country: str = "us",
    max_rating: int = 5,
) -> list[Review]:
    """Return up to `count` newest reviews with rating <= `max_rating`.

    Paginates with the scraper's continuation token since Google Play caps
    a single page at a few hundred reviews.
    """
    collected: list[Review] = []
    token = None
    while len(collected) < count:
        batch, token = reviews(
            app_id,
            lang=lang,
            country=country,
            sort=Sort.NEWEST,
            count=min(200, count - len(collected)),
            continuation_token=token,
        )
        if not batch:
            break
        for r in batch:
            text = (r.get("content") or "").strip()
            if not text or r["score"] > max_rating:
                continue
            collected.append(
                Review(
                    id=r["reviewId"],
                    rating=r["score"],
                    text=text,
                    thumbs_up=r.get("thumbsUpCount", 0),
                    app_version=r.get("appVersion") or r.get("reviewCreatedVersion"),
                    posted_at=r["at"],
                )
            )
        if token is None or getattr(token, "token", None) is None:
            break
    return collected[:count]
