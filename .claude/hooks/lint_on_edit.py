#!/usr/bin/env python3
"""PostToolUse: lint the file that was just edited.

Deliberately narrow. It runs ruff on the single edited file, which takes about 100 ms,
rather than the whole tree — a hook that adds seconds to every edit gets disabled, and a
disabled hook protects nothing.

Type checking is NOT here. mypy needs the whole import graph and takes ~0.7 s; it belongs
in the pre-commit and CI gates where a one-second cost is invisible. Running it per edit
would also produce failures for code that is legitimately half-written.

Exit 0 always. This hook informs; it never blocks. Blocking on style mid-edit fights the
author instead of helping them.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

LINTABLE = {".py"}
REPO = Path(__file__).resolve().parents[2]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return 0

    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    target = tool_input.get("file_path")
    if not isinstance(target, str) or Path(target).suffix not in LINTABLE:
        return 0

    path = Path(target)
    if not path.exists():
        return 0

    try:
        result = subprocess.run(
            ["uv", "run", "ruff", "check", "--output-format", "concise", str(path)],
            capture_output=True,
            text=True,
            cwd=REPO,
            timeout=45,
        )
    except Exception:
        return 0  # a missing toolchain must never wedge editing

    if result.returncode != 0 and result.stdout.strip():
        sys.stderr.write(f"ruff on {path.name}:\n{result.stdout.strip()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
