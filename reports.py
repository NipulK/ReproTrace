"""Immutable report snapshots and editable human review notes."""
import hashlib
import json
import re
from typing import Literal, Optional

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

REPOSITORIES = ("pallets/click", "pallets/werkzeug")


class ReportInput(BaseModel):
    source: Literal["paste", "github"]
    issue_url: Optional[str] = Field(default=None, max_length=500)
    repository: Optional[Literal["pallets/click", "pallets/werkzeug"]] = None
    title: Optional[str] = Field(default=None, max_length=500)
    body: Optional[str] = Field(default=None, max_length=60000)

    @model_validator(mode="after")
    def check_source(self):
        if self.source == "paste" and not (
                self.title and self.title.strip() and self.body and self.body.strip() and self.repository):
            raise ValueError("Pasted reports require a repository, title, and body")
        if self.source == "github":
            if not self.issue_url or not re.fullmatch(
                    r"https://github\.com/pallets/(click|werkzeug)/issues/[1-9][0-9]{0,9}/?",
                    self.issue_url):
                raise ValueError("Use a GitHub issue URL from pallets/click or pallets/werkzeug")
            if any(value is not None for value in (self.repository, self.title, self.body)):
                raise ValueError("GitHub imports accept only source and issue_url")
        return self


class ReviewInput(BaseModel):
    expected_behavior: str = Field(default="", max_length=10000)
    actual_behavior: str = Field(default="", max_length=10000)
    reproduction_steps: str = Field(default="", max_length=10000)
    environment: str = Field(default="", max_length=10000)
    missing_information: str = Field(default="", max_length=10000)
    notes: str = Field(default="", max_length=10000)


def import_issue(url):
    # The URL is validated before this function; construct a fixed-host API URL.
    parts = url.rstrip("/").split("/")
    repository, number = "/".join(parts[3:5]), int(parts[6])
    api_url = f"https://api.github.com/repos/{repository}/issues/{number}"
    try:
        response = httpx.get(api_url, timeout=15, follow_redirects=False,
                             headers={"Accept": "application/vnd.github+json",
                                      "X-GitHub-Api-Version": "2022-11-28",
                                      "User-Agent": "ReproTrace-local-MVP"})
    except httpx.RequestError:
        raise HTTPException(502, "GitHub could not be reached; retry or paste the report")
    if response.status_code == 404:
        raise HTTPException(404, "Public GitHub issue not found")
    if response.status_code in (403, 429):
        raise HTTPException(429, "GitHub access or rate limit reached; retry later or paste the report")
    if response.status_code != 200:
        raise HTTPException(502, "GitHub did not return an issue")
    try:
        issue = response.json()
        if not isinstance(issue, dict):
            raise ValueError()
        if "pull_request" in issue:
            raise HTTPException(422, "This URL identifies a pull request; use a bug issue")
        title, body = issue["title"], issue.get("body") or ""
        if not isinstance(title, str) or not isinstance(body, str):
            raise ValueError()
        if len(title) > 500 or len(body) > 60000:
            raise HTTPException(422, "Issue is too large for this MVP")
    except (KeyError, ValueError, TypeError):
        raise HTTPException(502, "GitHub returned an invalid issue response")
    return {"source": "github", "repository": repository, "issue_url": url.rstrip("/"),
            "title": title, "body": body, "issue_number": number,
            "upstream_created_at": issue.get("created_at"),
            "upstream_updated_at": issue.get("updated_at")}


def make_snapshot(submission):
    if submission.source == "github":
        return import_issue(submission.issue_url)
    return {"source": "paste", "repository": submission.repository,
            "issue_url": None, "title": submission.title.strip(), "body": submission.body,
            "issue_number": None, "upstream_created_at": None, "upstream_updated_at": None}


def snapshot_hash(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
