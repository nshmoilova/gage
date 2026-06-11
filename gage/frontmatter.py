"""Frontmatter handling: detect, parse, and inject a GAGE manifest.

Convention: the manifest lives under the `manifest:` key of standard YAML
frontmatter (delimited by `---` lines at the top of the file). Other
frontmatter keys are preserved on injection, so GAGE coexists with whatever
static-site or docs tooling already owns the frontmatter.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

import yaml

from .schemas import Manifest

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def split_frontmatter(text: str) -> Tuple[Optional[dict], str]:
    """Return (frontmatter_dict_or_None, body). Malformed YAML is treated as
    no frontmatter rather than an error — the document is still evaluable,
    it just lacks a manifest."""
    m = _FM_RE.match(text)
    if not m:
        return None, text
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None, text
    if not isinstance(data, dict):
        return None, text
    return data, text[m.end():]


def extract_manifest(text: str) -> Tuple[Optional[Manifest], Optional[dict], str, Optional[str]]:
    """Return (manifest, frontmatter, body, error).

    manifest is None when frontmatter is absent or has no `manifest` key.
    error is set when a `manifest` key exists but fails validation — that is
    surfaced to the user instead of silently re-inferring.
    """
    fm, body = split_frontmatter(text)
    if fm is None or "manifest" not in fm:
        return None, fm, body, None
    raw = fm["manifest"]
    if not isinstance(raw, dict):
        return None, fm, body, "frontmatter `manifest` key is not a mapping"
    try:
        return Manifest(**raw), fm, body, None
    except Exception as e:  # pydantic ValidationError
        return None, fm, body, f"manifest present but invalid: {e}"


def inject_manifest(text: str, manifest: Manifest) -> str:
    """Insert (or replace) the manifest in the document's frontmatter,
    preserving any other frontmatter keys."""
    fm, body = split_frontmatter(text)
    fm = dict(fm or {})
    fm["manifest"] = manifest.model_dump(exclude_none=True)
    block = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=88).rstrip()
    return f"---\n{block}\n---\n\n{body.lstrip()}"


def first_heading(body: str) -> Optional[str]:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()
    return None


def headings(body: str) -> list[str]:
    out = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            out.append(s.lstrip("#").strip())
    return out
