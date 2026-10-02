# JiraAgent — Play Store reviews → Jira stories

Scrapes Google Play reviews for an Android app, uses Claude to cluster them into
actionable user stories / bugs, and files them in Jira.

```
Google Play ──► scraper.py ──► analyzer.py (Claude) ──► jira_client.py ──► Jira
                  newest N        groups themes,          creates Story/Bug,
                  reviews         writes stories,         or comments on an
                                  maps to existing        existing open issue
                                  open issues
```

## How it works

1. **Scrape** (`review_agent/scraper.py`) — pulls the newest reviews with
   [`google-play-scraper`](https://pypi.org/project/google-play-scraper/), filtered by star rating.
2. **Skip already-seen reviews** (`review_agent/state.py`) — processed review IDs are saved in
   `state/<app_id>.json`, so a rerun only analyzes new reviews.
3. **Analyze** (`review_agent/analyzer.py`) — one Claude call with structured outputs returns a
   validated list of stories: title, Story/Bug, user story, acceptance criteria, priority,
   labels, quotes, and the IDs of the supporting reviews. Claude also gets the open issues
   the agent filed before, so a recurring theme is mapped onto its existing ticket instead of
   creating a duplicate.
4. **File in Jira** (`review_agent/jira_client.py`) — new themes become issues (description in
   Atlassian Document Format, with the supporting reviews listed); known themes get a comment
   with the new reviews.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill it in
```

- `ANTHROPIC_API_KEY` — from the [Claude Console](https://platform.claude.com/).
- `JIRA_API_TOKEN` — create at <https://id.atlassian.com/manage-profile/security/api-tokens>.
- `JIRA_PROJECT_KEY` — the project the stories go into; it needs the `Story` and `Bug` issue types
  (or set `JIRA_ISSUE_TYPE` to another type for non-bug items).

## Usage

```bash
# See what it would create, without touching Jira
python -m review_agent.main com.example.app --count 200 --dry-run

# Create the issues
python -m review_agent.main com.example.app --count 200
```

| Option | Default | Meaning |
|---|---|---|
| `--count` | 200 | Newest reviews to fetch |
| `--max-rating` | 4 | Only analyze reviews at or below this many stars (5-star reviews rarely contain requests) |
| `--min-reviews` | 2 | Reviews needed before a theme becomes a story (severe single reports still qualify) |
| `--lang` / `--country` | `en` / `us` | Play Store locale |
| `--dry-run` | off | Print stories as JSON instead of filing them |
| `--reprocess` | off | Ignore the saved list of processed reviews |

Run it on a schedule (cron, GitHub Actions) to keep the backlog fed with fresh feedback.
For a scheduled job, persist the `state/` directory between runs.

## Notes

- Model: `claude-opus-5-5` with adaptive thinking. Refusal fallback (`fallbacks: "default"`) is
  enabled so a safety-classifier decline is retried on Anthropic's recommended fallback model.
- Google Play has no official public reviews API for apps you don't own; `google-play-scraper`
  reads the public web pages. For your own app, the
  [Play Developer Reporting / Reply to Reviews API](https://developers.google.com/android-publisher/reply-to-reviews)
  is the official route and could replace `scraper.py`.
- Very large batches can exceed the output limit; lower `--count` if you see a `max_tokens` error.

## Tests

```bash
pip install pytest && python -m pytest -q
```
Tests mock Google Play, Claude, and Jira, so they run offline.
