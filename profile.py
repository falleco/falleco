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
SKULL_IMAGE = ROOT / "assets" / "graffiti-skull-pirate.png"
SKULL_BLINK_IMAGE = ROOT / "assets" / "graffiti-skull-pirate-blink.png"
MACOS_ICON = ROOT / "assets" / "os-macos-finder.svg"
IOS_ICON = ROOT / "assets" / "os-ios-apple.svg"
LINUX_ICON = ROOT / "assets" / "os-linux-debian.svg"
LINKEDIN_ICON = ROOT / "assets" / "contact-linkedin.svg"
TWITTER_ICON = ROOT / "assets" / "contact-twitter.svg"
GITHUB_ICON = ROOT / "assets" / "contact-github.svg"
PORTUGAL_ICON = ROOT / "assets" / "location-portugal.svg"
TYPESCRIPT_ICON = ROOT / "assets" / "language-typescript.svg"
JAVASCRIPT_ICON = ROOT / "assets" / "language-javascript.svg"
JAVA_ICON = ROOT / "assets" / "language-java.svg"
PYTHON_ICON = ROOT / "assets" / "language-python.svg"
LUA_ICON = ROOT / "assets" / "language-lua.svg"
SHELL_ICON = ROOT / "assets" / "language-shell.svg"
RUST_ICON = ROOT / "assets" / "language-rust.svg"
CARD_WIDTH = 1130
CARD_HEIGHT = 540
DETAIL_X = 440
DETAIL_CHARS = 68
SKULL_X = 25
SKULL_Y = 75
SKULL_WIDTH = 390
SKULL_HEIGHT = 390
OS_ICON_Y = 34
OS_ICON_SIZE = 14
MACOS_ICON_X = 860
IOS_ICON_X = 952
LINUX_ICON_X = 1025
MACOS_LABEL_X = 882
IOS_LABEL_X = 974
LINUX_LABEL_X = 1047
MACOS_LABEL_WIDTH = 58
IOS_LABEL_WIDTH = 39
LINUX_LABEL_WIDTH = 48
OS_ROW_Y = 47
CONTACT_ICON_SIZE = 14
PORTUGAL_ICON_X = 996
PORTUGAL_VALUE_X = 1018
PORTUGAL_VALUE_WIDTH = 77
PORTUGAL_ROW_Y = 104
LANGUAGE_ICON_SIZE = 14
JAVA_ICON_SIZE = 18
TYPESCRIPT_ICON_X = 754
JAVASCRIPT_ICON_X = 894
JAVA_ICON_X = 1032
TYPESCRIPT_LABEL_X = 776
JAVASCRIPT_LABEL_X = 916
JAVA_LABEL_X = 1056
TYPESCRIPT_LABEL_WIDTH = 106
JAVASCRIPT_LABEL_WIDTH = 106
JAVA_LABEL_WIDTH = 39
PROGRAMMING_ROW_Y = 180
PYTHON_ICON_X = 768
LUA_ICON_X = 868
SHELL_ICON_X = 942
RUST_ICON_X = 1032
LUA_ICON_SIZE = 16
RUST_ICON_SIZE = 18
PYTHON_LABEL_X = 790
LUA_LABEL_X = 891
SHELL_LABEL_X = 964
RUST_LABEL_X = 1056
PYTHON_LABEL_WIDTH = 67
LUA_LABEL_WIDTH = 39
SHELL_LABEL_WIDTH = 58
RUST_LABEL_WIDTH = 39
SYSTEMS_ROW_Y = 199
LINKEDIN_ICON_X = 842
TWITTER_ICON_X = 929
GITHUB_ICON_X = 900
LINKEDIN_VALUE_X = 864
TWITTER_VALUE_X = 951
GITHUB_VALUE_X = 922
LINKEDIN_VALUE_WIDTH = 231
TWITTER_VALUE_WIDTH = 144
GITHUB_VALUE_WIDTH = 173
LINKEDIN_ROW_Y = 294
TWITTER_ROW_Y = 313
GITHUB_ROW_Y = 332

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


def graphql(
    query: str,
    variables: dict[str, Any],
    *,
    allow_partial_forbidden: bool = False,
) -> dict[str, Any]:
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

    errors = result.get("errors", [])
    can_use_partial_data = (
        allow_partial_forbidden
        and result.get("data") is not None
        and all(error.get("type") == "FORBIDDEN" for error in errors)
    )
    if errors and not can_use_partial_data:
        raise RuntimeError(f"GitHub GraphQL error: {errors}")
    if errors:
        print(
            f"warning: skipped {len(errors)} inaccessible repository result(s)",
            file=sys.stderr,
        )
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
            allow_partial_forbidden=True,
        )["user"]["repositories"]
        total = data["totalCount"]
        repositories.extend(node for node in data["nodes"] if node is not None)
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


def image_data_uri(
    path: Path, replacements: dict[str, str] | None = None
) -> str:
    if path.suffix.lower() == ".svg":
        source = path.read_text(encoding="utf-8")
        for original, replacement in (replacements or {}).items():
            source = source.replace(original, replacement)
        payload = source.encode("utf-8")
        media_type = "image/svg+xml"
    else:
        payload = path.read_bytes()
        media_type = "image/png"

    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


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

    def add_os_info() -> None:
        nonlocal y
        rows.append(
            f'<tspan x="{DETAIL_X}" y="{y}" class="cc">. </tspan>'
            f'<tspan class="key">OS</tspan><tspan>:</tspan>'
            f'<tspan class="cc"> {"." * 35} </tspan>'
        )
        y += step

    def add_icon_info(label: str, icon_x: int, dot_count: int) -> None:
        nonlocal y
        connector_width = icon_x - 8 - DETAIL_X
        rows.append(
            f'<tspan x="{DETAIL_X}" y="{y}" '
            f'textLength="{connector_width}" lengthAdjust="spacing">'
            f'<tspan class="cc">. </tspan>'
            f'<tspan class="key">{escape(label)}</tspan><tspan>:</tspan>'
            f'<tspan class="cc"> {"." * dot_count}</tspan></tspan>'
        )
        y += step

    def add_section(title: str) -> None:
        nonlocal y
        separator = "-" * max(1, DETAIL_CHARS - len(title) - 4)
        rows.append(
            f'<tspan x="{DETAIL_X}" y="{y}">- {escape(title)} </tspan>'
            f'<tspan class="cc">{separator}</tspan>'
        )
        y += step

    add_section("General")
    add_os_info()
    add_info("Role", "Software Developer")
    add_info("Company", "Rebelde Incógnito")
    add_icon_info("Location", PORTUGAL_ICON_X, 45)
    add_info("Website", "israelcrisanto.com")
    add_info("Motto", "Code great, live better.")
    add_blank()
    add_icon_info("Languages.Programming", TYPESCRIPT_ICON_X, 8)
    add_icon_info("Languages.Systems", PYTHON_ICON_X, 12)
    add_info("Focus", "Backend, AI, Mobile, Developer Experience")
    add_info("Hobbies", "building things, 3d printing, dogs")
    add_blank()
    add_section("Contacts")
    add_icon_info("LinkedIn", LINKEDIN_ICON_X, 29)
    add_icon_info("X", TWITTER_ICON_X, 45)
    add_icon_info("GitHub", GITHUB_ICON_X, 37)
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

    skull_image = image_data_uri(SKULL_IMAGE)
    skull_blink_image = image_data_uri(SKULL_BLINK_IMAGE)
    macos_icon = image_data_uri(MACOS_ICON)
    ios_icon = image_data_uri(IOS_ICON, {"#000000": theme["value"]})
    linux_icon = image_data_uri(LINUX_ICON)
    linkedin_icon = image_data_uri(LINKEDIN_ICON)
    twitter_icon = image_data_uri(TWITTER_ICON)
    github_icon = image_data_uri(GITHUB_ICON, {"#161514": theme["value"]})
    portugal_icon = image_data_uri(PORTUGAL_ICON)
    typescript_icon = image_data_uri(TYPESCRIPT_ICON)
    javascript_icon = image_data_uri(JAVASCRIPT_ICON)
    java_icon = image_data_uri(JAVA_ICON)
    python_icon = image_data_uri(PYTHON_ICON)
    lua_icon = image_data_uri(LUA_ICON, {"#000000": theme["value"]})
    shell_icon = image_data_uri(SHELL_ICON, {"#000000": theme["value"]})
    rust_icon = image_data_uri(RUST_ICON)

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
    .skull-blink {{
      opacity: 0;
      animation: skull-blink 10s steps(1, end) infinite;
      animation-delay: -7s;
    }}
    @keyframes skull-blink {{
      0%, 88%, 92%, 97%, 100% {{ opacity: 0; }}
      89%, 91%, 94%, 96% {{ opacity: 1; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      .skull-blink {{ animation: none; opacity: 0; }}
    }}
    text, tspan {{ white-space: pre; }}
  </style>
  <rect width="{CARD_WIDTH}" height="{CARD_HEIGHT}" rx="15" fill="{theme['background']}"/>
  <image href="{skull_image}" x="{SKULL_X}" y="{SKULL_Y}" width="{SKULL_WIDTH}" height="{SKULL_HEIGHT}" preserveAspectRatio="xMidYMid meet"/>
  <image class="skull-blink" href="{skull_blink_image}" x="{SKULL_X}" y="{SKULL_Y}" width="{SKULL_WIDTH}" height="{SKULL_HEIGHT}" preserveAspectRatio="xMidYMid meet"/>
  <image href="{macos_icon}" x="{MACOS_ICON_X}" y="{OS_ICON_Y}" width="{OS_ICON_SIZE}" height="{OS_ICON_SIZE}" preserveAspectRatio="xMidYMid meet"/>
  <image href="{ios_icon}" x="{IOS_ICON_X}" y="{OS_ICON_Y}" width="{OS_ICON_SIZE}" height="{OS_ICON_SIZE}" preserveAspectRatio="xMidYMid meet"/>
  <image href="{linux_icon}" x="{LINUX_ICON_X}" y="{OS_ICON_Y}" width="{OS_ICON_SIZE}" height="{OS_ICON_SIZE}" preserveAspectRatio="xMidYMid meet"/>
  <image href="{portugal_icon}" x="{PORTUGAL_ICON_X}" y="{PORTUGAL_ROW_Y - 13}" width="{CONTACT_ICON_SIZE}" height="{CONTACT_ICON_SIZE}" preserveAspectRatio="xMidYMid meet"/>
  <image href="{typescript_icon}" x="{TYPESCRIPT_ICON_X}" y="{PROGRAMMING_ROW_Y - 13}" width="{LANGUAGE_ICON_SIZE}" height="{LANGUAGE_ICON_SIZE}" preserveAspectRatio="xMidYMid meet"/>
  <image href="{javascript_icon}" x="{JAVASCRIPT_ICON_X}" y="{PROGRAMMING_ROW_Y - 13}" width="{LANGUAGE_ICON_SIZE}" height="{LANGUAGE_ICON_SIZE}" preserveAspectRatio="xMidYMid meet"/>
  <image href="{java_icon}" x="{JAVA_ICON_X}" y="{PROGRAMMING_ROW_Y - 15}" width="{JAVA_ICON_SIZE}" height="{JAVA_ICON_SIZE}" preserveAspectRatio="xMidYMid meet"/>
  <image href="{python_icon}" x="{PYTHON_ICON_X}" y="{SYSTEMS_ROW_Y - 13}" width="{LANGUAGE_ICON_SIZE}" height="{LANGUAGE_ICON_SIZE}" preserveAspectRatio="xMidYMid meet"/>
  <image href="{lua_icon}" x="{LUA_ICON_X}" y="{SYSTEMS_ROW_Y - 14}" width="{LUA_ICON_SIZE}" height="{LUA_ICON_SIZE}" preserveAspectRatio="xMidYMid meet"/>
  <image href="{shell_icon}" x="{SHELL_ICON_X}" y="{SYSTEMS_ROW_Y - 13}" width="{LANGUAGE_ICON_SIZE}" height="{LANGUAGE_ICON_SIZE}" preserveAspectRatio="xMidYMid meet"/>
  <image href="{rust_icon}" x="{RUST_ICON_X}" y="{SYSTEMS_ROW_Y - 15}" width="{RUST_ICON_SIZE}" height="{RUST_ICON_SIZE}" preserveAspectRatio="xMidYMid meet"/>
  <image href="{linkedin_icon}" x="{LINKEDIN_ICON_X}" y="{LINKEDIN_ROW_Y - 13}" width="{CONTACT_ICON_SIZE}" height="{CONTACT_ICON_SIZE}" preserveAspectRatio="xMidYMid meet"/>
  <image href="{twitter_icon}" x="{TWITTER_ICON_X}" y="{TWITTER_ROW_Y - 13}" width="{CONTACT_ICON_SIZE}" height="{CONTACT_ICON_SIZE}" preserveAspectRatio="xMidYMid meet"/>
  <image href="{github_icon}" x="{GITHUB_ICON_X}" y="{GITHUB_ROW_Y - 13}" width="{CONTACT_ICON_SIZE}" height="{CONTACT_ICON_SIZE}" preserveAspectRatio="xMidYMid meet"/>
  <text font-family="ConsolasFallback, Consolas, monospace" font-size="16" fill="{theme['value']}">
    <tspan x="{MACOS_LABEL_X}" y="{OS_ROW_Y}" textLength="{MACOS_LABEL_WIDTH}" lengthAdjust="spacingAndGlyphs">macOS,</tspan>
    <tspan x="{IOS_LABEL_X}" y="{OS_ROW_Y}" textLength="{IOS_LABEL_WIDTH}" lengthAdjust="spacingAndGlyphs">iOS,</tspan>
    <tspan x="{LINUX_LABEL_X}" y="{OS_ROW_Y}" textLength="{LINUX_LABEL_WIDTH}" lengthAdjust="spacingAndGlyphs">Linux</tspan>
    <tspan x="{PORTUGAL_VALUE_X}" y="{PORTUGAL_ROW_Y}" textLength="{PORTUGAL_VALUE_WIDTH}" lengthAdjust="spacingAndGlyphs">Portugal</tspan>
    <tspan x="{TYPESCRIPT_LABEL_X}" y="{PROGRAMMING_ROW_Y}" textLength="{TYPESCRIPT_LABEL_WIDTH}" lengthAdjust="spacingAndGlyphs">TypeScript,</tspan>
    <tspan x="{JAVASCRIPT_LABEL_X}" y="{PROGRAMMING_ROW_Y}" textLength="{JAVASCRIPT_LABEL_WIDTH}" lengthAdjust="spacingAndGlyphs">JavaScript,</tspan>
    <tspan x="{JAVA_LABEL_X}" y="{PROGRAMMING_ROW_Y}" textLength="{JAVA_LABEL_WIDTH}" lengthAdjust="spacingAndGlyphs">Java</tspan>
    <tspan x="{PYTHON_LABEL_X}" y="{SYSTEMS_ROW_Y}" textLength="{PYTHON_LABEL_WIDTH}" lengthAdjust="spacingAndGlyphs">Python,</tspan>
    <tspan x="{LUA_LABEL_X}" y="{SYSTEMS_ROW_Y}" textLength="{LUA_LABEL_WIDTH}" lengthAdjust="spacingAndGlyphs">Lua,</tspan>
    <tspan x="{SHELL_LABEL_X}" y="{SYSTEMS_ROW_Y}" textLength="{SHELL_LABEL_WIDTH}" lengthAdjust="spacingAndGlyphs">Shell,</tspan>
    <tspan x="{RUST_LABEL_X}" y="{SYSTEMS_ROW_Y}" textLength="{RUST_LABEL_WIDTH}" lengthAdjust="spacingAndGlyphs">Rust</tspan>
    <tspan x="{LINKEDIN_VALUE_X}" y="{LINKEDIN_ROW_Y}" textLength="{LINKEDIN_VALUE_WIDTH}" lengthAdjust="spacingAndGlyphs">linkedin.com/in/crisanto</tspan>
    <tspan x="{TWITTER_VALUE_X}" y="{TWITTER_ROW_Y}" textLength="{TWITTER_VALUE_WIDTH}" lengthAdjust="spacingAndGlyphs">x.com/icrisanto</tspan>
    <tspan x="{GITHUB_VALUE_X}" y="{GITHUB_ROW_Y}" textLength="{GITHUB_VALUE_WIDTH}" lengthAdjust="spacingAndGlyphs">github.com/falleco</tspan>
  </text>
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
