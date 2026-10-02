"""Turn raw app reviews into Jira-ready user stories using Claude."""

import json
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .scraper import Review

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """\
You are a senior product manager triaging Google Play reviews for an Android app.
Your job is to turn what users are saying into a small set of actionable Jira user stories.

Guidelines:
- Group reviews that describe the same underlying problem or request into one story.
  Only create a story when it is backed by at least {min_reviews} distinct reviews,
  unless a single review describes a severe issue (crash, data loss, payment, security, privacy).
- Ignore reviews with no actionable content ("great app", "bad", spam, rants about pricing alone).
- Write stories from the user's perspective: "As a <user>, I want <goal> so that <benefit>."
- Acceptance criteria must be concrete and testable.
- Use type "Bug" for broken behavior and "Story" for feature requests and UX improvements.
- Priority reflects user impact and how many reviews mention it (weight by thumbs-up count).
- You will be given the existing open issues for this app. If a theme is already covered
  by one of them, set `existing_issue_key` to that key instead of creating a duplicate;
  the new reviews will be attached to it as additional evidence.
- Cite supporting reviews only by the ids you were given. Never invent ids.
"""


class StoryRecommendation(BaseModel):
    title: str = Field(description="Short Jira summary, under 100 characters")
    issue_type: Literal["Story", "Bug"]
    user_story: str = Field(description='"As a ..., I want ..., so that ..."')
    description: str = Field(description="Problem context synthesized from the reviews")
    acceptance_criteria: list[str]
    priority: Literal["Highest", "High", "Medium", "Low", "Lowest"]
    labels: list[str] = Field(description="A few lowercase, hyphenated topic labels, e.g. 'login', 'performance'")
    supporting_review_ids: list[str]
    representative_quotes: list[str] = Field(description="1-3 short verbatim quotes from the reviews")
    existing_issue_key: str | None = Field(
        description="Key of an existing open issue that already covers this theme, otherwise null"
    )


class AnalysisResult(BaseModel):
    summary: str = Field(description="Two or three sentences on overall user sentiment and top themes")
    stories: list[StoryRecommendation]


class AnalysisError(RuntimeError):
    pass


def _format_reviews(reviews: list[Review]) -> str:
    rows = [
        {
            "id": r.id,
            "rating": r.rating,
            "thumbs_up": r.thumbs_up,
            "app_version": r.app_version,
            "date": r.posted_at.date().isoformat(),
            "text": r.text,
        }
        for r in reviews
    ]
    return json.dumps(rows, ensure_ascii=False, indent=1)


def analyze_reviews(
    app_id: str,
    reviews: list[Review],
    existing_issues: list[dict[str, str]],
    min_reviews: int = 2,
    client: anthropic.Anthropic | None = None,
) -> AnalysisResult:
    """Ask Claude to cluster the reviews into user stories.

    `existing_issues` is a list of {"key": ..., "summary": ...} for open issues,
    used so Claude can map recurring themes onto them instead of duplicating.
    """
    client = client or anthropic.Anthropic()

    user_content = (
        f"App package: {app_id}\n\n"
        f"<existing_open_issues>\n{json.dumps(existing_issues, ensure_ascii=False, indent=1)}\n"
        f"</existing_open_issues>\n\n"
        f"<reviews>\n{_format_reviews(reviews)}\n</reviews>"
    )

    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=64000,
        system=SYSTEM_PROMPT.format(min_reviews=min_reviews),
        messages=[{"role": "user", "content": user_content}],
        thinking={"type": "adaptive"},
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(AnalysisResult)},
        },
        # Re-run on Anthropic's recommended model if a safety classifier declines.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise AnalysisError(f"Claude declined the request: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise AnalysisError("Response hit max_tokens; try fewer reviews per run (--count)")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise AnalysisError("No text block in Claude's response")
    result = AnalysisResult.model_validate_json(text)

    # Drop any review ids Claude didn't actually receive.
    known_ids = {r.id for r in reviews}
    known_keys = {i["key"] for i in existing_issues}
    for story in result.stories:
        story.supporting_review_ids = [i for i in story.supporting_review_ids if i in known_ids]
        if story.existing_issue_key not in known_keys:
            story.existing_issue_key = None
    return result
