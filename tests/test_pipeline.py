from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import anthropic

from review_agent import analyzer, main as cli
from review_agent.analyzer import AnalysisResult, StoryRecommendation
from review_agent.config import JiraConfig
from review_agent.jira_client import JiraClient, story_description
from review_agent.scraper import Review

REVIEWS = [
    Review("r1", 1, "App crashes every time I open the camera", 12, "5.2.0", datetime(2026, 9, 1)),
    Review("r2", 2, "Camera screen crashes on my Pixel", 3, "5.2.0", datetime(2026, 9, 2)),
    Review("r3", 3, "Please add dark mode", 7, "5.2.0", datetime(2026, 9, 3)),
]

STORY = StoryRecommendation(
    title="Fix crash when opening the camera",
    issue_type="Bug",
    user_story="As a user, I want the camera to open without crashing so that I can scan documents.",
    description="Multiple users on 5.2.0 report a crash on the camera screen.",
    acceptance_criteria=["Camera opens on Pixel devices", "No crash reported in Crashlytics"],
    priority="High",
    labels=["Camera", "crash"],
    supporting_review_ids=["r1", "r2", "made-up"],
    representative_quotes=["App crashes every time I open the camera"],
    existing_issue_key=None,
)


def test_schema_is_accepted_by_structured_outputs_transform():
    schema = anthropic.transform_schema(AnalysisResult)
    assert schema["type"] == "object"


def _fake_client(result: AnalysisResult):
    message = SimpleNamespace(
        stop_reason="end_turn",
        stop_details=None,
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=result.model_dump_json())],
    )
    stream = mock.MagicMock()
    stream.__enter__.return_value.get_final_message.return_value = message
    client = mock.MagicMock()
    client.beta.messages.stream.return_value = stream
    return client


def test_analyze_drops_unknown_review_ids_and_issue_keys():
    bad_key = STORY.model_copy(update={"existing_issue_key": "APP-999"})
    client = _fake_client(AnalysisResult(summary="s", stories=[bad_key]))
    result = analyzer.analyze_reviews("com.x", REVIEWS, [{"key": "APP-1", "summary": "x"}], client=client)
    assert result.stories[0].supporting_review_ids == ["r1", "r2"]
    assert result.stories[0].existing_issue_key is None
    kwargs = client.beta.messages.stream.call_args.kwargs
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["output_config"]["format"]["type"] == "json_schema"


def test_description_is_valid_adf():
    doc = story_description(STORY, "com.x", REVIEWS[:2])
    assert doc["type"] == "doc" and doc["version"] == 1
    assert any(b["type"] == "bulletList" for b in doc["content"])


def _jira():
    return JiraClient(JiraConfig("https://x.atlassian.net", "e", "t", "APP", "Story", "play-store-feedback"))


def test_create_story_retries_without_priority():
    jira = _jira()
    bad = SimpleNamespace(ok=False, status_code=400, text='{"errors":{"priority":"not on screen"}}')
    good = SimpleNamespace(ok=True, status_code=201, json=lambda: {"key": "APP-7"})
    with mock.patch.object(jira.session, "post", side_effect=[bad, good]) as post:
        assert jira.create_story(STORY, "com.x", REVIEWS[:2]) == "APP-7"
    fields = post.call_args.kwargs["json"]["fields"]
    assert "priority" not in fields
    assert fields["issuetype"]["name"] == "Bug"
    assert fields["labels"] == ["camera", "com.x", "crash", "play-store-feedback"]


def test_cli_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    result = AnalysisResult(summary="Users want a stable camera.", stories=[STORY])
    with mock.patch.object(cli, "fetch_reviews", return_value=REVIEWS), \
         mock.patch.object(cli, "analyze_reviews", return_value=result):
        assert cli.main(["com.x", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Fix crash when opening the camera" in out
    assert not (tmp_path / "state").exists()


def test_cli_creates_and_comments_then_skips_processed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for k, v in {"JIRA_BASE_URL": "https://x", "JIRA_EMAIL": "e", "JIRA_API_TOKEN": "t", "JIRA_PROJECT_KEY": "APP"}.items():
        monkeypatch.setenv(k, v)
    dup = STORY.model_copy(update={"existing_issue_key": "APP-1", "title": "Dark mode"})
    result = AnalysisResult(summary="s", stories=[STORY, dup])
    jira = mock.MagicMock()
    jira.open_feedback_issues.return_value = [{"key": "APP-1", "summary": "Dark mode"}]
    jira.create_story.return_value = "APP-2"
    with mock.patch.object(cli, "fetch_reviews", return_value=REVIEWS), \
         mock.patch.object(cli, "analyze_reviews", return_value=result) as analyze, \
         mock.patch.object(cli, "JiraClient", return_value=jira):
        cli.main(["com.x"])
        jira.create_story.assert_called_once()
        jira.add_evidence_comment.assert_called_once()
        # Second run: all reviews already processed, so Claude isn't called again.
        cli.main(["com.x"])
        assert analyze.call_count == 1
