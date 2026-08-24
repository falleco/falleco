#!/usr/bin/env python3
"""Calculate GitHub stats and generate light/dark profile SVG cards."""

from __future__ import annotations

import base64
import calendar
import json
import os
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


USERNAME = os.getenv("PROFILE_USERNAME", "falleco")
TOKEN = os.getenv("PROFILE_TOKEN") or os.getenv("GITHUB_TOKEN")
ROOT = Path(__file__).resolve().parent
CACHE_FILE = ROOT / "cache" / "stats.json"
SKULL_IMAGE = ROOT / "assets" / "graffiti-skull.png"
CARD_WIDTH = 1130
CARD_HEIGHT = 540
DETAIL_X = 440
DETAIL_CHARS = 68
SKULL_X = 25
SKULL_Y = 75
SKULL_WIDTH = 390
SKULL_HEIGHT = 390

THEMES = {
    "dark_mode.svg": {
        "background": "#161b22",
        "text": "#c9d1d9",
        "key": "#ffa657",
        "value": "#a5d6ff",
        "add": "#3fb950",
        "delete": "#f85149",
        "connector": "#616e7f",
    },
    "light_mode.svg": {
        "background": "#f6f8fa",
        "text": "#24292f",
        "key": "#953800",
        "value": "#0a3069",
        "add": "#1a7f37",
        "delete": "#cf222e",
        "connector": "#c2cfde",
    },
}


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
    query($owner: String!, $name: String!, $author: ID!, $cursor: String) {
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


def format_number(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) else str(value)


def svg_info_line(label: str, value: Any, x: int, y: int) -> str:
    rendered_value = format_number(value)
    prefix_length = len(label) + 3
    dot_count = max(1, DETAIL_CHARS - prefix_length - len(rendered_value) - 2)
    dots = " " + "." * dot_count + " "
    return (
        f'<tspan x="{x}" y="{y}" class="cc">. </tspan>'
        f'<tspan class="key">{escape(label)}</tspan>'
        f'<tspan>:</tspan>'
        f'<tspan class="cc">{dots}</tspan>'
        f'<tspan class="value">{escape(rendered_value)}</tspan>'
    )


def skull_data_uri() -> str:
    encoded = base64.b64encode(SKULL_IMAGE.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def render_svg(stats: dict[str, Any], theme: dict[str, str]) -> str:
    additions = stats["additions"]
    deletions = stats["deletions"]
    if isinstance(additions, int) and isinstance(deletions, int):
        net_lines = format_number(additions - deletions)
        loc_value = (
            f'<tspan class="value">{net_lines}</tspan> '
            f'(<tspan class="addColor">+{format_number(additions)}</tspan>, '
            f'<tspan class="delColor">-{format_number(deletions)}</tspan>)'
        )
    else:
        loc_value = '<tspan class="value">pending</tspan>'

    rows: list[str] = []
    y = 28
    step = 19

    def add_info(label: str, value: Any) -> None:
        nonlocal y
        rows.append(svg_info_line(label, value, DETAIL_X, y))
        y += step

    def add_blank() -> None:
        nonlocal y
        y += step

    def add_section(title: str) -> None:
        nonlocal y
        separator = "-" * max(1, DETAIL_CHARS - len(title) - 4)
        rows.append(
            f'<tspan x="{DETAIL_X}" y="{y}">- {escape(title)} </tspan>'
            f'<tspan class="cc">{separator}</tspan>'
        )
        y += step

    rows.append(
        f'<tspan x="{DETAIL_X}" y="{y}">crisanto@israel </tspan>'
        f'<tspan class="cc">{"-" * 50}</tspan>'
    )
    y += step
    add_info("OS", "macOS, iOS, Linux")
    add_info("Role", "Software Developer")
    add_info("Company", "Rebelde Incógnito")
    add_info("Location", "Portugal")
    add_info("Website", "israelcrisanto.com")
    add_info("Motto", "Code great, live better.")
    add_blank()
    add_info("Languages.Programming", "TypeScript, JavaScript, Java, Python")
    add_info("Languages.Systems", "Python, Lua, Shell, C++")
    add_info("Focus", "AI, mobile, developer tooling")
    add_info("Hobbies", "building things, 3d printing, dogs")
    add_blank()
    add_section("Contact")
    add_info("LinkedIn", "linkedin.com/in/crisanto")
    add_info("X", "x.com/icrisanto")
    add_info("GitHub", "github.com/falleco")
    add_blank()
    add_section("GitHub Stats")
    add_info("Account age", stats["age"])
    add_info("Repos", stats["repos"])
    add_info("Contributed", stats["contributed"])
    add_info("Stars", stats["stars"])
    add_info("Commits", stats["commits"])
    add_info("Followers", stats["followers"])

    loc_label = "Lines of code"
    loc_plain = (
        f"{format_number(additions - deletions)} "
        f"(+{format_number(additions)}, -{format_number(deletions)})"
        if isinstance(additions, int) and isinstance(deletions, int)
        else "pending"
    )
    loc_dots = " " + "." * max(
        1, DETAIL_CHARS - len(loc_label) - len(loc_plain) - 5
    ) + " "
    rows.append(
        f'<tspan x="{DETAIL_X}" y="{y}" class="cc">. </tspan>'
        f'<tspan class="key">{loc_label}</tspan><tspan>:</tspan>'
        f'<tspan class="cc">{loc_dots}</tspan>{loc_value}'
    )

    skull_image = skull_data_uri()

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{CARD_WIDTH}" height="{CARD_HEIGHT}" viewBox="0 0 {CARD_WIDTH} {CARD_HEIGHT}" role="img" aria-labelledby="title desc">
  <title id="title">Israel Crisanto GitHub profile</title>
  <desc id="desc">Terminal-style profile card with a graffiti skull and GitHub statistics</desc>
  <style>
    @font-face {{
      src: local("Consolas"), local("Consolas Bold");
      font-family: "ConsolasFallback";
      font-display: swap;
      size-adjust: 109%;
    }}
    .key {{ fill: {theme['key']}; }}
    .value {{ fill: {theme['value']}; }}
    .addColor {{ fill: {theme['add']}; }}
    .delColor {{ fill: {theme['delete']}; }}
    .cc {{ fill: {theme['connector']}; }}
    text, tspan {{ white-space: pre; }}
  </style>
  <rect width="{CARD_WIDTH}" height="{CARD_HEIGHT}" rx="15" fill="{theme['background']}"/>
  <image href="{skull_image}" x="{SKULL_X}" y="{SKULL_Y}" width="{SKULL_WIDTH}" height="{SKULL_HEIGHT}" preserveAspectRatio="xMidYMid meet"/>
  <text font-family="ConsolasFallback, Consolas, monospace" font-size="16" fill="{theme['text']}">
    {''.join(rows)}
  </text>
</svg>
'''


def write_svgs(stats: dict[str, Any]) -> None:
    for filename, theme in THEMES.items():
        (ROOT / filename).write_text(render_svg(stats, theme))


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
    write_svgs(stats)
    print(json.dumps(stats, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()
