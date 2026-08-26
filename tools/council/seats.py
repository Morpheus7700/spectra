"""Council seats — one uniform interface over three different vendors.

Every seat answers `ask(prompt) -> str | None`. A seat that fails returns None and is
dropped from the round; the council must never block on a dead provider.

Transports differ deliberately:
  - OpenAI  : `openai` SDK, Responses API (not Chat Completions).
  - Gemini  : `google-genai` SDK (NOT the deprecated `google-generativeai`).
  - Claude  : the `claude -p` CLI, which reuses the existing subscription login.
              This is why no ANTHROPIC_API_KEY is needed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass

TIMEOUT_S = 180


@dataclass(frozen=True)
class Answer:
    seat: str
    text: str


class Seat:
    name: str

    async def ask(self, prompt: str) -> Answer | None:
        raise NotImplementedError


class OpenAISeat(Seat):
    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("COUNCIL_OPENAI_MODEL", "gpt-5.6-sol")
        self.name = f"openai:{self.model}"

    async def ask(self, prompt: str) -> Answer | None:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            print(f"[{self.name}] openai SDK not installed — seat skipped")
            return None
        if not os.environ.get("OPENAI_API_KEY"):
            print(f"[{self.name}] OPENAI_API_KEY unset — seat skipped")
            return None
        try:
            client = AsyncOpenAI()
            resp = await asyncio.wait_for(
                client.responses.create(model=self.model, input=prompt),
                timeout=TIMEOUT_S,
            )
            text = (resp.output_text or "").strip()
            return Answer(self.name, text) if text else None
        except Exception as exc:
            print(f"[{self.name}] failed: {type(exc).__name__}: {exc}")
            return None


class GeminiSeat(Seat):
    """NOTE: the free tier trains on submitted content.

    Never route proprietary positioning IP through this seat. The council caller is
    responsible for that judgement; this class only reports which tier it is on.
    """

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("COUNCIL_GEMINI_MODEL", "gemini-3.7-flash")
        self.name = f"gemini:{self.model}"

    async def ask(self, prompt: str) -> Answer | None:
        try:
            from google import genai
        except ImportError:
            print(f"[{self.name}] google-genai not installed — seat skipped")
            return None
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            print(f"[{self.name}] GEMINI_API_KEY unset — seat skipped")
            return None
        try:
            client = genai.Client(api_key=key)
            result = await asyncio.wait_for(
                asyncio.to_thread(client.interactions.create, model=self.model, input=prompt),
                timeout=TIMEOUT_S,
            )
            text = (getattr(result, "output_text", "") or "").strip()
            return Answer(self.name, text) if text else None
        except Exception as exc:
            print(f"[{self.name}] failed: {type(exc).__name__}: {exc}")
            return None


# Tools the council seat must never be able to reach. A council answers in prose; a
# council with filesystem and shell access is a prompt-injection target, because stage 2
# feeds it the *other vendors' raw output* as part of its prompt.
_DENIED_TOOLS = [
    "Bash",
    "Write",
    "Edit",
    "NotebookEdit",
    "Read",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Task",
    "Agent",
]

# Environment variables never passed to the child. It authenticates by subscription login,
# so it has no need of these, and an injected instruction cannot exfiltrate what is absent.
_STRIPPED_ENV = ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY")


class ClaudeCliSeat(Seat):
    """Claude via the `claude -p` CLI, reusing the existing subscription login.

    Hardened deliberately. Stage 2 hands this seat text written by other vendors' models,
    which makes any tool access an injection surface: text arriving from a third party
    would be reaching an agent holding this machine's credentials. So the child gets no
    tools, no API keys, and receives its prompt on stdin rather than argv (argv is visible
    in the process table to every local process).
    """

    name = "claude:cli"

    async def ask(self, prompt: str) -> Answer | None:
        exe = shutil.which("claude")
        if not exe:
            print(f"[{self.name}] `claude` not on PATH — seat skipped")
            return None
        env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
        try:
            proc = await asyncio.create_subprocess_exec(
                exe,
                "-p",
                "--permission-mode",
                "plan",
                "--disallowed-tools",
                *_DENIED_TOOLS,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            out, err = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")), timeout=TIMEOUT_S
            )
            if proc.returncode != 0:
                print(f"[{self.name}] exit {proc.returncode}: {err.decode(errors='replace')[:300]}")
                return None
            text = out.decode(errors="replace").strip()
            return Answer(self.name, text) if text else None
        except Exception as exc:
            print(f"[{self.name}] failed: {type(exc).__name__}: {exc}")
            return None


def default_seats() -> list[Seat]:
    return [ClaudeCliSeat(), GeminiSeat(), OpenAISeat()]
