"""Remembers which reviews were already processed so reruns only look at new ones."""

import json
from pathlib import Path

STATE_DIR = Path("state")


class ProcessedReviews:
    def __init__(self, app_id: str, state_dir: Path = STATE_DIR):
        self.path = state_dir / f"{app_id}.json"
        self.ids: set[str] = set()
        if self.path.exists():
            self.ids = set(json.loads(self.path.read_text()))

    def __contains__(self, review_id: str) -> bool:
        return review_id in self.ids

    def add_all(self, review_ids) -> None:
        self.ids.update(review_ids)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(self.ids)))
