"""CLI: scrape Play Store reviews -> Claude analysis -> Jira stories.

Usage:
    python -m review_agent.main com.example.app --count 300 --dry-run
"""

import argparse
import json
import sys

from .analyzer import analyze_reviews
from .config import JiraConfig
from .jira_client import JiraClient
from .scraper import fetch_reviews
from .state import ProcessedReviews


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("app_id", help="Android package name, e.g. com.spotify.music")
    p.add_argument("--count", type=int, default=200, help="Number of newest reviews to fetch (default 200)")
    p.add_argument("--lang", default="en")
    p.add_argument("--country", default="us")
    p.add_argument("--max-rating", type=int, default=4, choices=range(1, 6),
                   help="Only analyze reviews at or below this star rating (default 4)")
    p.add_argument("--min-reviews", type=int, default=2,
                   help="Minimum reviews backing a theme before it becomes a story (default 2)")
    p.add_argument("--dry-run", action="store_true", help="Print the proposed stories without touching Jira")
    p.add_argument("--reprocess", action="store_true", help="Ignore the record of already-processed reviews")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    print(f"Fetching up to {args.count} reviews for {args.app_id}...")
    reviews = fetch_reviews(args.app_id, args.count, args.lang, args.country, args.max_rating)

    processed = ProcessedReviews(args.app_id)
    if not args.reprocess:
        reviews = [r for r in reviews if r.id not in processed]
    if not reviews:
        print("No new reviews to analyze.")
        return 0
    print(f"Analyzing {len(reviews)} reviews with Claude...")

    jira = None if args.dry_run else JiraClient(JiraConfig.from_env())
    existing = jira.open_feedback_issues(args.app_id) if jira else []

    result = analyze_reviews(args.app_id, reviews, existing, min_reviews=args.min_reviews)
    print(f"\n{result.summary}\n")

    by_id = {r.id: r for r in reviews}
    for story in result.stories:
        evidence = [by_id[i] for i in story.supporting_review_ids if i in by_id]
        if args.dry_run:
            print(json.dumps(story.model_dump(), indent=2, ensure_ascii=False))
            continue
        if story.existing_issue_key:
            jira.add_evidence_comment(story.existing_issue_key, story, evidence)
            print(f"  + {story.existing_issue_key}: added {len(evidence)} reviews as evidence ({story.title})")
        else:
            key = jira.create_story(story, args.app_id, evidence)
            print(f"  * {key}: created [{story.issue_type}/{story.priority}] {story.title}")

    if not args.dry_run:
        processed.add_all(r.id for r in reviews)
        processed.save()
    print(f"\nDone: {len(result.stories)} recommendation(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
