"""Manifest inference — the front half of the Rubricator.

When a document arrives without a manifest in its frontmatter, GAGE drafts
one: type (from the profile registry's signal keywords or an LLM), purpose
(from the document's own opening), audience and acceptance criteria
(suggested, never silently committed). The draft is ALWAYS presented to a
human for edit/confirmation before injection — a manifest is a declaration of
intent, and intent is authorized by people.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

from .frontmatter import first_heading
from .providers import LLMProvider, extract_json, infer_manifest_user_prompt, INFER_MANIFEST_SYSTEM
from .registry import Registry


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:48] or "artifact"


def _first_paragraph(body: str) -> str:
    in_para: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#") or not s:
            if in_para:
                break
            continue
        in_para.append(s)
    return " ".join(in_para)


def heuristic_manifest_draft(body: str, registry: Registry) -> dict:
    title = first_heading(body) or "Untitled artifact"
    guessed = registry.guess_type(body)
    para = _first_paragraph(body)
    purpose = (para[:300] + "…") if len(para) > 300 else para
    if not purpose:
        purpose = f"Document titled '{title}' — purpose not stated in the text; please declare it."
    prof = registry.resolve_profile(guessed if guessed != "unknown" else "_default")
    criteria = []
    for sec in prof.structure_expectations[:3]:
        criteria.append(f"Addresses '{sec}' explicitly")
    return {
        "id": f"{_slug(title)}-{date.today().isoformat()}",
        "type": guessed,
        "purpose": purpose,
        "audience": [],
        "acceptance_criteria": criteria,
        "constraints": [],
        "confidence": "low" if guessed == "unknown" else "medium",
        "type_rationale": (f"Keyword signals matched profile '{guessed}'."
                           if guessed != "unknown" else
                           "No profile signals matched; declare a type or proceed with universal dimensions only."),
        "inference_method": "heuristic",
    }


async def infer_manifest_draft(body: str, registry: Registry,
                               provider: Optional[LLMProvider] = None) -> dict:
    """LLM draft when a provider is available; deterministic heuristic
    otherwise or on any failure. Either way the result is a DRAFT for human
    confirmation, never a silently-applied manifest."""
    if provider is None:
        return heuristic_manifest_draft(body, registry)
    try:
        raw = await provider.complete(INFER_MANIFEST_SYSTEM,
                                      infer_manifest_user_prompt(body, registry.known_types()),
                                      max_tokens=1200)
        data = extract_json(raw)
        if data.get("type") not in registry.known_types() + ["unknown"]:
            data["type"] = registry.guess_type(body)
        data.setdefault("id", heuristic_manifest_draft(body, registry)["id"])
        data["inference_method"] = f"llm:{provider.name}"
        return data
    except Exception as e:
        fallback = heuristic_manifest_draft(body, registry)
        fallback["type_rationale"] += f" (LLM inference failed: {e})"
        return fallback
