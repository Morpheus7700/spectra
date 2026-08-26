"""CLI: python -m tools.council "question" [--adr slug]

Reads .env from the repo root so the seats find their keys.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
import sys
from datetime import date
from pathlib import Path

from .council import CouncilResult

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude" / "hooks"))

# Windows consoles default to cp1252 and mangle em-dashes in model output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def next_adr_number() -> int:
    adr_dir = ROOT / "docs" / "adr"
    adr_dir.mkdir(parents=True, exist_ok=True)
    used = [int(m.group(1)) for p in adr_dir.glob("*.md") if (m := re.match(r"(\d{4})-", p.name))]
    return max(used, default=0) + 1


def write_adr(slug: str, result: CouncilResult) -> Path:
    number = next_adr_number()
    path = ROOT / "docs" / "adr" / f"{number:04d}-{slug}.md"
    seats = "\n\n".join(f"### {a.seat}\n\n{a.text}" for a in result.answers)
    agg = "\n".join(f"- {seat}: mean rank {rank}" for seat, rank in result.aggregate)
    parts = [
        f"# {number:04d}. {slug.replace('-', ' ').capitalize()}",
        "",
        f"Date: {date.today().isoformat()}  ",
        f"Chair: {result.chair}  ",
        f"Seats: {', '.join(a.seat for a in result.answers)}",
        "",
        "## Question",
        "",
        result.question,
        "",
        "## Decision",
        "",
        result.synthesis,
        "",
        "## Aggregate ranking",
        "",
        agg or "_unavailable_",
        "",
        "## Individual positions",
        "",
        seats,
    ]
    if result.debate:
        parts += [
            "",
            "## Adversarial challenge",
            "",
            "_Consensus was strong enough to trigger a forced debate round._",
            "",
            result.debate,
        ]
    if result.parse_failures:
        parts += ["", "## Ranking parse failures", ""]
        parts += [f"- {f}" for f in result.parse_failures]
    body = "\n".join(parts) + "\n"

    # A council seat can return anything, including a credential it was shown or invented.
    # docs/adr/ is tracked, and the PreToolUse hook architecturally cannot see a write made
    # by this process, so the scan happens here or nowhere. Imported inside the function
    # because sys.path only gains .claude/hooks at module load, above.
    from guard_secrets import find_secret

    hit = find_secret(body)
    if hit is not None:
        raise SystemExit(
            f"REFUSED to write {path.name}: council output contains what looks like a "
            f"{hit[1]}. Nothing was written. Inspect the transcript before retrying."
        )

    path.write_text(body, encoding="utf-8")
    return path


async def amain() -> int:
    ap = argparse.ArgumentParser(prog="council")
    ap.add_argument("question", nargs="+")
    ap.add_argument("--adr", help="slug; writes docs/adr/NNNN-<slug>.md")
    ap.add_argument("--chair", type=int, default=None, help="pin chair index (default: rotate)")
    args = ap.parse_args()

    load_dotenv()
    from .council import run_council
    from .seats import default_seats

    question = " ".join(args.question)
    seats = default_seats()
    rotate = os.environ.get("COUNCIL_ROTATE_CHAIR", "true").lower() != "false"
    chair = (
        args.chair if args.chair is not None else (random.randrange(len(seats)) if rotate else 0)
    )
    threshold = float(os.environ.get("COUNCIL_DEBATE_THRESHOLD", "0.8"))

    result = await run_council(question, seats, chair_index=chair, debate_threshold=threshold)

    print(f"\n{'=' * 70}\nCHAIR: {result.chair}")
    print(f"SEATS: {', '.join(a.seat for a in result.answers)}")
    if result.parse_failures:
        print(f"PARSE FAILURES: {'; '.join(result.parse_failures)}")
    if result.debate:
        print("DEBATE: forced (consensus exceeded threshold)")
    print(f"{'=' * 70}\n")
    print(result.synthesis)

    if args.adr:
        path = write_adr(args.adr, result)
        print(f"\nDecision record written to {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
