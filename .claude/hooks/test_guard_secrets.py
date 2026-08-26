"""Tests for the PreToolUse secret guard.

This hook is the only mechanical control stopping a credential reaching a tracked file,
and until now it had no tests — which is exactly how the `.envrc` hole survived: the
guard allowed it (prefix match on `.env`) while `.gitignore` did not cover it, so a
direnv file full of exported keys was writable and committable. Every bypass found in
review is pinned below so it cannot come back.

Note the fixtures are ASSEMBLED AT RUNTIME rather than written literally. The first draft
of this file was blocked by the very hook it tests, because a literal private-key header
is indistinguishable from the real thing — which is the correct behaviour and must not be
softened with a test-file exemption. Building the strings from parts keeps the guard
strict while letting its tests exist.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent / "guard_secrets.py"
REPO = HOOK.parents[2]

# Shaped like real credentials so the patterns fire; none are live.
FAKE_OPENAI = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0" * 2
FAKE_AWS = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
FAKE_GITHUB = "gh" + "p_" + "a1b2c3d4e5" * 4
FAKE_PEM = "-----" + "BEGIN RSA PRIVATE KEY" + "-----"

ALLOW, BLOCK = 0, 2


def run(payload: dict) -> int:
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=30,
    )
    return proc.returncode


def write(path: str, content: str, tool: str = "Write") -> dict:
    return {"tool_name": tool, "tool_input": {"file_path": path, "content": content}}


# --------------------------------------------------------------------------- blocks


def test_blocks_key_in_tracked_file():
    assert run(write("docs/notes.md", f"key is {FAKE_OPENAI}")) == BLOCK


@pytest.mark.parametrize("secret", [FAKE_OPENAI, FAKE_AWS, FAKE_GITHUB, FAKE_PEM])
def test_blocks_every_credential_shape(secret: str):
    assert run(write("docs/notes.md", secret)) == BLOCK


def test_blocks_secret_nested_in_an_edits_array():
    """A denylist of content field names fails open on any new tool shape."""
    payload = {
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": "docs/notes.md",
            "edits": [{"old_string": "x", "new_string": FAKE_OPENAI}],
        },
    }
    assert run(payload) == BLOCK


def test_blocks_secret_from_an_unknown_future_tool():
    """An MCP filesystem server must not silently defeat the guard."""
    payload = {
        "tool_name": "McpFilesystemWrite",
        "tool_input": {"path": "docs/notes.md", "data": FAKE_OPENAI},
    }
    assert run(payload) == BLOCK


def test_blocks_deeply_nested_secret():
    payload = {
        "tool_name": "SomeTool",
        "tool_input": {"a": {"b": [{"c": {"d": FAKE_OPENAI}}]}},
    }
    assert run(payload) == BLOCK


def test_blocks_real_key_in_env_example():
    """.env.example is committed, so a real value there is the worst case."""
    assert run(write(".env.example", f"OPENAI_API_KEY={FAKE_OPENAI}")) == BLOCK


# --------------------------------------------------------------------------- allows


def test_allows_key_in_gitignored_env():
    assert run(write(".env", f"OPENAI_API_KEY={FAKE_OPENAI}")) == ALLOW


def test_allows_envrc_now_that_gitignore_covers_it():
    """Regression: `.envrc` was allowed by a `.env` prefix match while untracked by
    .gitignore. The prefix match is gone; this now passes via the ignore check, which
    means the .gitignore entry is load-bearing and must not be removed."""
    assert run(write(".envrc", f"export OPENAI_API_KEY={FAKE_OPENAI}")) == ALLOW


def test_allows_obvious_placeholders():
    assert run(write(".env.example", "OPENAI_API_KEY=sk-proj-EXAMPLE")) == ALLOW
    assert run(write("README.md", "set it to sk-proj-YOUR_KEY_HERE")) == ALLOW


def test_allows_ordinary_content():
    assert run(write("packages/core/x.py", "def f() -> int:\n    return 42\n")) == ALLOW


def test_allows_read_only_tools():
    assert run({"tool_name": "Read", "tool_input": {"file_path": FAKE_OPENAI}}) == ALLOW
    assert run({"tool_name": "Grep", "tool_input": {"pattern": FAKE_OPENAI}}) == ALLOW


# --------------------------------------------------------------------------- robustness


def test_malformed_input_never_wedges_the_session():
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not json",
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=30,
    )
    assert proc.returncode == ALLOW


def test_empty_payload_is_allowed():
    assert run({}) == ALLOW


def test_non_dict_tool_input_is_survived():
    assert run({"tool_name": "Write", "tool_input": "oops"}) == ALLOW


def test_recursion_is_bounded():
    """A pathologically nested payload must not blow the stack."""
    nested: object = FAKE_OPENAI
    for _ in range(200):
        nested = {"k": nested}
    assert run({"tool_name": "Write", "tool_input": {"deep": nested}}) == ALLOW
