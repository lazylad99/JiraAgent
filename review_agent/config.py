import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name} (see .env.example)")
    return value


@dataclass(frozen=True)
class JiraConfig:
    base_url: str
    email: str
    api_token: str
    project_key: str
    issue_type: str
    label: str

    @classmethod
    def from_env(cls) -> "JiraConfig":
        return cls(
            base_url=_require("JIRA_BASE_URL").rstrip("/"),
            email=_require("JIRA_EMAIL"),
            api_token=_require("JIRA_API_TOKEN"),
            project_key=_require("JIRA_PROJECT_KEY"),
            issue_type=os.environ.get("JIRA_ISSUE_TYPE", "Story"),
            label=os.environ.get("JIRA_LABEL", "play-store-feedback"),
        )
