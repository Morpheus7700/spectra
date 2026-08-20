#!/usr/bin/env python3
"""PreToolUse guard: refuse to write credential-shaped strings into tracked files.

Exit 0 = allow, exit 2 = block (stderr is shown to Claude).

Scope, stated honestly so nobody over-trusts it. This hook sees **tool calls only**. It
cannot stop `cat .env >> notes.md` through Bash, it cannot stop `git add -f .env`, and it
cannot see files written by a subprocess (which is why `tools/council/__main__.py` runs
these same patterns itself before writing an ADR). It is one layer, not the control.

The real control is `git check-ignore`. The filename check is only a fast path, and it is
deliberately *exact*: an earlier version used `name.startswith(".env")`, which waved
through `.envrc` — a file direnv fills with `export OPENAI_API_KEY=...` and which
`.gitignore` did not match. Prefix matching on security-relevant filenames is a trap.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-proj-[A-Za-z0-9_\-]{20,}"), "OpenAI project key"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "Anthropic key"),
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b"), "Google API key"),
    (re.compile(r"\bAQ\.[A-Za-z0-9_\-]{20,}"), "Google OAuth token"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"), "GitHub token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "Slack token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
]

# Placeholders in .env.example and docs must not trip the guard.
ALLOW_IF_MATCHES = re.compile(r"(YOUR_|EXAMPLE|PLACEHOLDER|xxx+|\.\.\.|<[a-z_]+>)", re.I)

# Tools that never write to disk, so their payloads need no scanning.
READ_ONLY_TOOLS = frozenset(
    {"Read", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite", "ExitPlanMode"}
)


def find_secret(text: str) -> tuple[str, str] | None:
    """Return (matched_text, label) for the first real-looking credential found."""
    for pattern, label in SECRET_PATTERNS:
        m = pattern.search(text)
        if m and not ALLOW_IF_MATCHES.search(m.group(0)):
            return m.group(0), label
    return None


def is_env_file(name: str) -> bool:
    """True for `.env` and `.env.<suffix>`, but NOT `.envrc` or `.env.example`.

    Exact rather than prefix matching — see the module docstring for why.
    """
    lowered = name.lower()
    if lowered == ".env.example":
        return False
    return lowered == ".env" or lowered.startswith(".env.")


def is_git_ignored(path: str) -> bool:
    try:
        return (
            subprocess.run(
                ["git", "check-ignore", "-q", path], capture_output=True, timeout=5
            ).returncode
            == 0
        )
    except Exception:
        return False  # fail closed: unknown status is treated as tracked


def iter_strings(value: object, depth: int = 0) -> list[str]:
    """Every string anywhere in the tool payload.

    A denylist of known content keys ("content", "new_string", ...) silently fails open
    the moment a new write tool appears with a different field name, or an MCP filesystem
    server is added. Walking the whole structure fails closed instead.
    """
    if depth > 8:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in iter_strings(v, depth + 1)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in iter_strings(v, depth + 1)]
    return []


def target_path(tool_input: dict[str, object]) -> str:
    for key in ("file_path", "notebook_path", "path", "filePath"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # malformed hook input must never wedge the session

    tool = payload.get("tool_name", "")
    if tool in READ_ONLY_TOOLS:
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    if not isinstance(tool_input, dict):
        return 0

    target = target_path(tool_input)
    if target:
        name = Path(target).name
        if is_env_file(name) or is_git_ignored(target):
            return 0

    for text in iter_strings(tool_input):
        hit = find_secret(text)
        if hit is None:
            continue
        _, label = hit
        where = f"'{target}'" if target else f"a {tool} payload"
        sys.stderr.write(
            f"BLOCKED: a {label} would be written into {where}, which git tracks.\n"
            f"Put the value in .env (gitignored) and read it via os.environ instead.\n"
            f"If this is a placeholder, make it obviously fake (e.g. sk-proj-EXAMPLE).\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
