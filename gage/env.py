"""Zero-dependency .env loader.

Looked up at startup from (1) the repo root and (2) the current working
directory. Real environment variables ALWAYS take precedence — a .env file
fills gaps, it never overrides what the shell already set. Key NAMES (never
values) are logged at startup and attributed in /api/registry so "why is only
mock available?" is answerable at a glance.

Supported syntax: KEY=value, export KEY=value, comments (#), blank lines,
single/double-quoted values, inline comments after unquoted values.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")

# global ledger: {path: [keys this loader actually set]} — survives multiple
# load_env() calls in one process so source attribution stays correct
LOADED: dict[str, list[str]] = {}


def parse_env_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        else:
            val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
        out[key] = val
    return out


def default_paths() -> list[Path]:
    root = Path(__file__).resolve().parent.parent   # repo root
    paths = [root / ".env"]
    cwd = Path.cwd() / ".env"
    if cwd not in paths:
        paths.append(cwd)
    return paths


def load_env(paths: list[Path] | None = None, override: bool = False) -> dict[str, list[str]]:
    """Load .env file(s) into os.environ; returns the global ledger of what
    this loader set, per file. Idempotent: re-loading never double-sets."""
    for p in (paths if paths is not None else default_paths()):
        p = Path(p)
        if not p.is_file():
            continue
        try:
            data = parse_env_text(p.read_text())
        except OSError:
            continue
        keys = LOADED.setdefault(str(p), [])
        for k, v in data.items():
            if v == "":
                continue   # placeholder lines in .env.example style files
            if override or not os.environ.get(k):
                os.environ[k] = v
                if k not in keys:
                    keys.append(k)
    return LOADED


def env_file_keys() -> set[str]:
    return {k for keys in LOADED.values() for k in keys}
