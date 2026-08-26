#!/usr/bin/env python3
"""Stop hook: if the positioning engine changed this session, prove it still works.

The accuracy gate is the one check that cannot be replaced by reading the diff. A change
to the solver can leave every unit test green while moving p50 by 18% — that exact defect
survived 166 tests on this project and was found only by measurement.

So: when `packages/engine` has uncommitted changes, run the gate before the session is
allowed to end quietly. Runs only when the engine actually moved, so an ordinary session
pays nothing.

Exit 0 always — this reports, it does not trap the session. A hook that can strand someone
is a hook they will remove.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WATCHED = "packages/engine/spectra_engine"


def engine_is_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", WATCHED],
            capture_output=True,
            text=True,
            cwd=REPO,
            timeout=15,
        )
    except Exception:
        return False
    return bool(result.stdout.strip())


def main() -> int:
    if not engine_is_dirty():
        return 0

    try:
        result = subprocess.run(
            ["uv", "run", "pytest", "-q", "packages/engine", "-x", "--no-header"],
            capture_output=True,
            text=True,
            cwd=REPO,
            timeout=600,
        )
    except Exception as exc:
        sys.stderr.write(f"engine gate could not run: {type(exc).__name__}: {exc}\n")
        return 0

    tail = (result.stdout or "").strip().splitlines()[-6:]
    if result.returncode != 0:
        sys.stderr.write(
            "ENGINE GATE FAILED — packages/engine changed and its tests do not pass.\n"
            + "\n".join(tail)
            + "\nDo not report this work as complete (R5).\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
