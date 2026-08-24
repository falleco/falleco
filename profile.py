#!/usr/bin/env python3
"""Update the terminal-style profile block in README.md with GitHub stats."""

from __future__ import annotations

import calendar
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


USERNAME = os.getenv("PROFILE_USERNAME", "falleco")
TOKEN = os.getenv("PROFILE_TOKEN") or os.getenv("GITHUB_TOKEN")
ROOT = Path(__file__).resolve().parent
README = ROOT / "README.md"
CACHE_FILE = ROOT / "cache" / "stats.json"
START_MARKER = "<!-- profile:start -->"
END_MARKER = "<!-- profile:end -->"
DETAILS_COLUMN = 20
BRAILLE_VISUAL_OFFSET = 2
DETAILS_WIDTH = 66

# Compact skull adapted for the side-by-side layout.
ASCII_ART = [
    "⠀⠀⠀⠀⢀⣀⣤⣤⣤⣤⣄⡀⠀⠀⠀⠀",
    "⠀⢀⣤⣾⣿⣾⣿⣿⣿⣿⣿⣿⣷⣄⠀⠀",
    "⢠⣾⣿⢛⣼⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⡀",
    "⣾⣯⣷⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣧",
    "⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿",
    "⣿⡿⠻⢿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠻⢿⡵",
    "⢸⡇⠀⠀⠉⠛⠛⣿⣿⠛⠛⠉⠀⠀⣿⡇",
    "⢸⣿⣀⠀⢀⣠⣴⡇⠹⣦⣄⡀⠀⣠⣿⡇",
    "⠈⠻⠿⠿⣟⣿⣿⣦⣤⣼⣿⣿⠿⠿⠟⠀",
    "⠀⠀⠀⠀⠸⡿⣿⣿⢿⡿⢿⠇⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠈⠁⠈⠁⠀⠀⠀⠀⠀⠀",
]


def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    if not TOKEN:
        raise RuntimeError("Set PROFILE_TOKEN or GITHUB_TOKEN before running profile.py")

    payload = json.dumps({"query": query, "variables": variables}).encode()
    request = Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": f"{USERNAME}-profile-readme",
        },
    )
    try:
        with urlopen(request, timeout=60) as response:
            result = json.load(response)
    except HTTPError as error:
        details = error.read().decode(errors="replace")
        raise RuntimeError(f"GitHub API returned {error.code}: {details}") from error

    if result.get("errors"):
        raise RuntimeError(f"GitHub GraphQL error: {result['errors']}")
    return result["data"]


def fetch_user() -> dict[str, Any]:
    query = """
    query($login: String!) {
      user(login: $login) {
        id
        createdAt
        followers { totalCount }
      }
    }
    """
    user = graphql(query, {"login": USERNAME})["user"]
    if user is None:
        raise RuntimeError(f"GitHub user {USERNAME!r} was not found")
    return user


def fetch_repositories(affiliations: list[str]) -> tuple[int, list[dict[str, Any]]]:
    query = """
    query($login: String!, $affiliations: [RepositoryAffiliation!], $cursor: String) {
      user(login: $login) {
        repositories(
          first: 100
          after: $cursor
          ownerAffiliations: $affiliations
          orderBy: {field: UPDATED_AT, direction: DESC}
        ) {
          totalCount
          nodes {
            nameWithOwner
            stargazerCount
            defaultBranchRef { target { ... on Commit { oid } } }
          }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
    """
    cursor = None
    repositories: list[dict[str, Any]] = []
    total = 0
    while True:
        data = graphql(
            query,
            {"login": USERNAME, "affiliations": affiliations, "cursor": cursor},
        )["user"]["repositories"]
        total = data["totalCount"]
        repositories.extend(data["nodes"])
        if not data["pageInfo"]["hasNextPage"]:
            return total, repositories
        cursor = data["pageInfo"]["endCursor"]


def fetch_repository_contributions(
    repository: str, author_id: str
) -> dict[str, int]:
    owner, name = repository.split("/", 1)
    query = """
    query(
      $owner: String!
      $name: String!
      $author: ID!
      $cursor: String
    ) {
      repository(owner: $owner, name: $name) {
        defaultBranchRef {
          target {
            ... on Commit {
              history(first: 100, after: $cursor, author: {id: $author}) {
                totalCount
                nodes { additions deletions }
                pageInfo { hasNextPage endCursor }
              }
            }
          }
        }
      }
    }
    """
    cursor = None
    additions = 0
    deletions = 0
    commits = 0
    while True:
        data = graphql(
            query,
            {
                "owner": owner,
                "name": name,
                "author": author_id,
                "cursor": cursor,
            },
        )["repository"]
        branch = data and data["defaultBranchRef"]
        target = branch and branch["target"]
        history = target and target.get("history")
        if not history:
            return {"commits": 0, "additions": 0, "deletions": 0}
        commits = history["totalCount"]
        additions += sum(node["additions"] for node in history["nodes"])
        deletions += sum(node["deletions"] for node in history["nodes"])
        if not history["pageInfo"]["hasNextPage"]:
            return {
                "commits": commits,
                "additions": additions,
                "deletions": deletions,
            }
        cursor = history["pageInfo"]["endCursor"]


def load_cache() -> dict[str, Any]:
    try:
        return json.loads(CACHE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"repositories": {}}


def collect_contributions(
    repositories: list[dict[str, Any]], author_id: str
) -> dict[str, int]:
    cache = load_cache()
    previous = cache.get("repositories", {})
    current: dict[str, Any] = {}

    for repository in repositories:
        name = repository["nameWithOwner"]
        branch = repository.get("defaultBranchRef")
        target = branch and branch.get("target")
        head = target and target.get("oid")
        cached = previous.get(name)
        if cached and cached.get("head") == head:
            current[name] = cached
            continue

        try:
            contribution = fetch_repository_contributions(name, author_id)
            current[name] = {"head": head, **contribution}
            print(f"updated {name}", file=sys.stderr)
        except RuntimeError as error:
            if cached:
                current[name] = cached
                print(f"warning: using stale cache for {name}: {error}", file=sys.stderr)
            else:
                print(f"warning: skipping {name}: {error}", file=sys.stderr)

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps({"repositories": current}, indent=2, sort_keys=True) + "\n"
    )
    return {
        key: sum(item.get(key, 0) for item in current.values())
        for key in ("commits", "additions", "deletions")
    }


def account_age(created_at: str) -> str:
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    today = datetime.now(timezone.utc)
    years = today.year - created.year
    months = today.month - created.month
    if today.day < created.day:
        months -= 1
        previous_month = today.month - 1 or 12
        previous_year = today.year if today.month > 1 else today.year - 1
        days = today.day + calendar.monthrange(previous_year, previous_month)[1] - created.day
    else:
        days = today.day - created.day
    if months < 0:
        years -= 1
        months += 12

    def unit(value: int, singular: str) -> str:
        return f"{value} {singular}{'' if value == 1 else 's'}"

    return f"{unit(years, 'year')}, {unit(months, 'month')}, {unit(days, 'day')}"


def render_profile(stats: dict[str, Any]) -> str:
    def format_number(value: Any) -> str:
        return f"{value:,}" if isinstance(value, int) else str(value)

    additions = stats["additions"]
    deletions = stats["deletions"]
    if isinstance(additions, int) and isinstance(deletions, int):
        lines_of_code = (
            f"{additions - deletions:,} "
            f"(+{additions:,}, -{deletions:,})"
        )
    else:
        lines_of_code = "pending"

    def detail_line(label: str, value: Any) -> str:
        prefix = f"{label}:"
        rendered_value = str(value)
        available = DETAILS_WIDTH - len(prefix) - len(rendered_value)
        if available < 3:
            return f"{prefix} {rendered_value}"
        return f"{prefix} {'.' * (available - 2)} {rendered_value}"

    details = [
        "crisanto@israel",
        "----------------",
        detail_line("OS", "macOS, iOS, Linux"),
        detail_line("Role", "Software Developer"),
        detail_line("Company", "Rebelde Incógnito"),
        detail_line("Location", "Portugal"),
        detail_line("Website", "https://israelcrisanto.com"),
        detail_line("Motto", "Code great, live better."),
        "",
        detail_line("Languages.Programming", "TypeScript, JavaScript, Java, Python"),
        detail_line("Languages.Systems", "Python, Lua, Shell, C++"),
        detail_line("Focus", "AI, mobile, developer tooling"),
        detail_line("Hobbies", "building things, 3d printing, dogs"),
        "",
        "Contact",
        detail_line("LinkedIn", "https://linkedin.com/in/crisanto"),
        detail_line("X", "https://x.com/icrisanto"),
        detail_line("GitHub", "https://github.com/falleco"),
        "",
        "GitHub Stats",
        detail_line("Account age", stats["age"]),
        detail_line(
            "Repos",
            f"{format_number(stats['repos'])} "
            f"{{Contributed to: {format_number(stats['contributed'])}}} | "
            f"Stars: {format_number(stats['stars'])}",
        ),
        detail_line(
            "Commits",
            f"{format_number(stats['commits'])} | "
            f"Followers: {format_number(stats['followers'])}",
        ),
        detail_line("Lines of code", lines_of_code),
    ]
    compact_art = [line.rstrip("⠀ ") for line in ASCII_ART]
    height = max(len(compact_art), len(details))
    art_top = (height - len(compact_art)) // 2
    centered_art = (
        [""] * art_top
        + compact_art
        + [""] * (height - art_top - len(compact_art))
    )
    padded_details = details + [""] * (height - len(details))
    lines = []
    for art, detail in zip(centered_art, padded_details):
        # GitHub renders Braille through a fallback font that is visually wider
        # than its monospace font. Compensate where art and text share a line.
        column = DETAILS_COLUMN - BRAILLE_VISUAL_OFFSET if art and detail else DETAILS_COLUMN
        lines.append(f"{art:<{column}}{detail}".rstrip())
    return f"{START_MARKER}\n```text\n" + "\n".join(lines) + f"\n```\n{END_MARKER}"


def update_readme(profile: str) -> None:
    contents = README.read_text()
    start = contents.find(START_MARKER)
    end = contents.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        raise RuntimeError("README.md does not contain valid profile markers")
    end += len(END_MARKER)
    README.write_text(contents[:start] + profile + contents[end:])


def main() -> None:
    user = fetch_user()
    repo_count, owned = fetch_repositories(["OWNER"])
    contributed_count, contributed = fetch_repositories(
        ["OWNER", "COLLABORATOR", "ORGANIZATION_MEMBER"]
    )
    contribution_stats = collect_contributions(contributed, user["id"])
    stats = {
        "age": account_age(user["createdAt"]),
        "repos": repo_count,
        "contributed": contributed_count,
        "stars": sum(repository["stargazerCount"] for repository in owned),
        "followers": user["followers"]["totalCount"],
        **contribution_stats,
    }
    update_readme(render_profile(stats))
    print(json.dumps(stats, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()
