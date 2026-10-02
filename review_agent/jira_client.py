"""Minimal Jira Cloud REST v3 client for creating and updating feedback stories."""

import re

import requests

from .analyzer import StoryRecommendation
from .config import JiraConfig
from .scraper import Review


# --- Atlassian Document Format helpers -------------------------------------

def _text(s: str) -> dict:
    return {"type": "text", "text": s}


def _paragraph(s: str) -> dict:
    return {"type": "paragraph", "content": [_text(s)]} if s else {"type": "paragraph"}


def _heading(s: str, level: int = 3) -> dict:
    return {"type": "heading", "attrs": {"level": level}, "content": [_text(s)]}


def _bullets(items: list[str]) -> dict:
    return {
        "type": "bulletList",
        "content": [{"type": "listItem", "content": [_paragraph(i)]} for i in items],
    }


def _quote(items: list[str]) -> dict:
    return {"type": "blockquote", "content": [_paragraph(i) for i in items]}


def _doc(blocks: list[dict]) -> dict:
    return {"type": "doc", "version": 1, "content": blocks}


def _evidence_lines(reviews: list[Review]) -> list[str]:
    return [
        f"{'★' * r.rating}{'☆' * (5 - r.rating)} (v{r.app_version or '?'}, "
        f"{r.posted_at.date().isoformat()}, {r.thumbs_up} 👍): {r.text[:300]}"
        for r in reviews
    ]


def story_description(story: StoryRecommendation, app_id: str, evidence: list[Review]) -> dict:
    blocks = [
        _paragraph(story.user_story),
        _heading("Context"),
        _paragraph(story.description),
        _heading("Acceptance criteria"),
        _bullets(story.acceptance_criteria),
    ]
    if story.representative_quotes:
        blocks += [_heading("What users are saying"), _quote(story.representative_quotes)]
    if evidence:
        blocks += [
            _heading(f"Supporting reviews ({len(evidence)})"),
            _bullets(_evidence_lines(evidence)),
        ]
    blocks.append(_paragraph(f"Generated from Google Play reviews of {app_id} by the review agent."))
    return _doc(blocks)


def _sanitize_label(label: str) -> str:
    return re.sub(r"\s+", "-", label.strip().lower())


# --- Client -----------------------------------------------------------------

class JiraClient:
    def __init__(self, config: JiraConfig):
        self.config = config
        self.session = requests.Session()
        self.session.auth = (config.email, config.api_token)
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}/rest/api/3/{path}"

    def _raise_for_status(self, resp: requests.Response) -> None:
        if not resp.ok:
            raise RuntimeError(f"Jira API {resp.request.method} {resp.url} -> {resp.status_code}: {resp.text}")

    def open_feedback_issues(self, app_id: str) -> list[dict[str, str]]:
        """Open issues this agent created previously for the app (used for dedup)."""
        jql = (
            f'project = "{self.config.project_key}" AND labels = "{self.config.label}" '
            f'AND labels = "{_sanitize_label(app_id)}" AND statusCategory != Done ORDER BY created DESC'
        )
        issues: list[dict[str, str]] = []
        next_token = None
        while True:
            body = {"jql": jql, "fields": ["summary"], "maxResults": 100}
            if next_token:
                body["nextPageToken"] = next_token
            resp = self.session.post(self._url("search/jql"), json=body, timeout=30)
            self._raise_for_status(resp)
            data = resp.json()
            issues += [{"key": i["key"], "summary": i["fields"]["summary"]} for i in data.get("issues", [])]
            next_token = data.get("nextPageToken")
            if not next_token:
                return issues

    def create_story(self, story: StoryRecommendation, app_id: str, evidence: list[Review]) -> str:
        labels = {self.config.label, _sanitize_label(app_id), *map(_sanitize_label, story.labels)}
        fields = {
            "project": {"key": self.config.project_key},
            "issuetype": {"name": story.issue_type if story.issue_type == "Bug" else self.config.issue_type},
            "summary": story.title[:250],
            "description": story_description(story, app_id, evidence),
            "labels": sorted(l for l in labels if l),
            "priority": {"name": story.priority},
        }
        resp = self.session.post(self._url("issue"), json={"fields": fields}, timeout=30)
        if resp.status_code == 400 and "priority" in resp.text:
            # Some projects don't expose the priority field on the create screen.
            fields.pop("priority")
            resp = self.session.post(self._url("issue"), json={"fields": fields}, timeout=30)
        self._raise_for_status(resp)
        return resp.json()["key"]

    def add_evidence_comment(self, issue_key: str, story: StoryRecommendation, evidence: list[Review]) -> None:
        blocks = [_paragraph(f"{len(evidence)} new Google Play review(s) mention this issue.")]
        if story.representative_quotes:
            blocks.append(_quote(story.representative_quotes))
        if evidence:
            blocks.append(_bullets(_evidence_lines(evidence)))
        resp = self.session.post(
            self._url(f"issue/{issue_key}/comment"), json={"body": _doc(blocks)}, timeout=30
        )
        self._raise_for_status(resp)
