"""The engine must stay pure. Enforced by the build, not by discipline.

`packages/engine` takes data and returns data: no file access, no network, no clock, no
randomness, no logging side effects. Everything else in this project depends on that.

It is what makes the accuracy gate meaningful (the same input must always produce the same
error figure), what makes property-based testing possible, and what stops the solver from
quietly acquiring a dependency on a vendor API. Purity is easy to lose one convenient
import at a time, which is exactly why it is checked mechanically.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1] / "spectra_engine"

FORBIDDEN_MODULES = {
    "os": "filesystem and environment access",
    "io": "file I/O",
    "pathlib": "filesystem access",
    "socket": "network access",
    "http": "network access",
    "urllib": "network access",
    "requests": "network access",
    "httpx": "network access",
    "sqlite3": "database access",
    "logging": "logging side effects",
    "random": "non-determinism",
    "subprocess": "process execution",
    "threading": "concurrency side effects",
    "asyncio": "concurrency side effects",
    "tempfile": "filesystem access",
    "pickle": "serialisation side effects",
}

FORBIDDEN_CALLS = {
    ("datetime", "now"): "reads the clock; pass the timestamp in instead",
    ("datetime", "utcnow"): "reads the clock; pass the timestamp in instead",
    ("time", "time"): "reads the clock; pass the timestamp in instead",
    ("time", "sleep"): "blocks",
    ("np", "random"): "non-determinism; seed and inject instead",
}

FORBIDDEN_BUILTINS = {"open", "input", "print", "eval", "exec", "breakpoint"}


def engine_modules() -> list[Path]:
    return sorted(p for p in ENGINE.rglob("*.py") if p.name != "__init__.py")


def test_engine_has_modules_to_check():
    assert engine_modules(), "purity check found no engine modules -- wrong path?"


@pytest.mark.parametrize("path", engine_modules(), ids=lambda p: p.name)
def test_module_imports_nothing_impure(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offences: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_MODULES:
                    offences.append(
                        f"line {node.lineno}: imports {alias.name} ({FORBIDDEN_MODULES[root]})"
                    )
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in FORBIDDEN_MODULES:
                offences.append(
                    f"line {node.lineno}: imports from {node.module} ({FORBIDDEN_MODULES[root]})"
                )

    assert not offences, f"{path.name} is no longer pure:\n  " + "\n  ".join(offences)


@pytest.mark.parametrize("path", engine_modules(), ids=lambda p: p.name)
def test_module_makes_no_impure_calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offences: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            key = (func.value.id, func.attr)
            if key in FORBIDDEN_CALLS:
                offences.append(
                    f"line {node.lineno}: {key[0]}.{key[1]}() -- {FORBIDDEN_CALLS[key]}"
                )
        elif isinstance(func, ast.Name) and func.id in FORBIDDEN_BUILTINS:
            offences.append(f"line {node.lineno}: {func.id}() is not allowed in the engine")

    assert not offences, f"{path.name} performs impure operations:\n  " + "\n  ".join(offences)


def test_engine_does_not_depend_on_the_simulator():
    """A solver that knows about the simulator cannot be honestly measured by it."""
    offenders = []
    for path in engine_modules():
        text = path.read_text(encoding="utf-8")
        if "spectra_sim" in text:
            offenders.append(path.name)
    assert not offenders, (
        f"engine modules reference the simulator: {offenders}. The engine must not be able "
        "to tell simulated data from a real controller."
    )


def test_engine_does_not_depend_on_any_vendor_adapter():
    """Adapters know vendors; the engine knows geometry. Neither knows the other."""
    vendors = ("unifi", "meraki", "aruba", "openwrt", "esp32", "cisco", "mist")
    offenders = []
    for path in engine_modules():
        lowered = path.read_text(encoding="utf-8").lower()
        for vendor in vendors:
            # Prose mentions in docstrings are fine; imports and attribute access are not.
            if f"import {vendor}" in lowered or f"adapters.{vendor}" in lowered:
                offenders.append(f"{path.name} -> {vendor}")
    assert not offenders, f"engine leaked a vendor dependency: {offenders}"
