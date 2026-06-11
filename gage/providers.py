"""LLM providers and prompt contracts.

Native adapters for the four frontier vendors — Anthropic, OpenAI, Gemini
(Google), and xAI (Grok) — plus OpenRouter (one key, every vendor) and
strict-JSON prompt builders. Seats carry their own provider, so a single
council can span all four vendors natively: the adversarial matrix.
API keys come from server environment variables only; they are never accepted
from the browser.

For current Anthropic model identifiers and API details see
https://docs.claude.com/en/api/overview — model names are configuration, not
code, in this framework.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

import httpx
import yaml

from .registry import Persona
from .schemas import Critique, CriteriaStack, Manifest


class ProviderError(RuntimeError):
    pass


class LLMProvider:
    name = "abstract"

    async def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        raise NotImplementedError


# ----- endpoint configuration: every base URL is overridable -----
# For deployments that cannot reach public model APIs, point each vendor's
# protocol at an internal gateway (vLLM, LiteLLM, an enterprise proxy, …) via
# environment variable. A provider with an overridden base URL is usable even
# without an API key — internal endpoints often authenticate at the network
# layer instead.
DEFAULT_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "xai": "https://api.x.ai",
    "openrouter": "https://openrouter.ai",
}
BASE_URL_ENV = {
    "anthropic": "ANTHROPIC_BASE_URL",
    "openai": "OPENAI_BASE_URL",
    "gemini": "GEMINI_BASE_URL",
    "xai": "XAI_BASE_URL",
    "openrouter": "OPENROUTER_BASE_URL",
}


def base_url_override(mode: str) -> Optional[str]:
    v = os.environ.get(BASE_URL_ENV.get(mode, ""), "").strip()
    return v.rstrip("/") or None


def base_url(mode: str) -> str:
    return base_url_override(mode) or DEFAULT_BASE_URLS[mode]


def _require_key_or_endpoint(mode: str, api_key: Optional[str], env_hint: str) -> None:
    if not api_key and not base_url_override(mode):
        raise ProviderError(
            f"{env_hint} is not set in the server environment "
            f"(for a keyless internal endpoint, set {BASE_URL_ENV[mode]} instead)")


def _bearer(api_key: Optional[str]) -> dict:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _check(r, provider: str, model: str):
    if r.is_success:
        return
    try:
        detail = r.json()
    except Exception:
        detail = r.text[:500]
    raise ProviderError(
        f"{provider} returned {r.status_code} for model '{model}': {detail}"
    )


# ----- per-model endpoint registry (registry/models.yaml) -----

_MODEL_ENDPOINTS: dict[str, dict] = {}  # ref -> {endpoint, api_key_env}


def _load_model_endpoints():
    root = Path(__file__).resolve().parent.parent
    config_path = os.environ.get("GAGE_MODELS_CONFIG", str(root / "registry" / "models.yaml"))
    p = Path(config_path)
    if not p.exists():
        return
    try:
        entries = yaml.safe_load(p.read_text()) or []
        if not isinstance(entries, list):
            return
        for entry in entries:
            ref = (entry.get("ref") or "").strip()
            endpoint = (entry.get("endpoint") or "").strip().rstrip("/")
            if ref and endpoint:
                _MODEL_ENDPOINTS[ref] = {
                    "endpoint": endpoint,
                    "api_key_env": (entry.get("api_key_env") or "").strip(),
                }
        if _MODEL_ENDPOINTS:
            print(f"[gage] loaded {len(_MODEL_ENDPOINTS)} model endpoint(s) from {p}: "
                  f"{', '.join(_MODEL_ENDPOINTS)}", file=sys.stderr)
    except Exception as e:
        print(f"[gage] warning: failed to load {p}: {e}", file=sys.stderr)


_load_model_endpoints()


def model_endpoint(ref: str) -> Optional[str]:
    entry = _MODEL_ENDPOINTS.get(ref)
    return entry["endpoint"] if entry else None


def model_api_key(ref: str) -> Optional[str]:
    entry = _MODEL_ENDPOINTS.get(ref)
    if entry and entry.get("api_key_env"):
        return os.environ.get(entry["api_key_env"])
    return None


class OpenRouterProvider(LLMProvider):
    name = "openrouter"

    def __init__(self, model: str, api_key: Optional[str] = None, endpoint: Optional[str] = None):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.url = (endpoint or base_url("openrouter")) + "/api/v1/chat/completions"
        if not endpoint:
            _require_key_or_endpoint("openrouter", self.api_key, "OPENROUTER_API_KEY")

    async def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(self.url, headers=_bearer(self.api_key),
                                  json={"model": self.model, "max_tokens": max_tokens,
                                        "messages": [{"role": "system", "content": system},
                                                     {"role": "user", "content": user}]})
            _check(r, "openrouter", self.model)
            data = r.json()
            return data["choices"][0]["message"]["content"]


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key: Optional[str] = None, endpoint: Optional[str] = None):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.url = (endpoint or base_url("anthropic")) + "/v1/messages"
        if not endpoint:
            _require_key_or_endpoint("anthropic", self.api_key, "ANTHROPIC_API_KEY")

    async def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        headers = {"anthropic-version": "2023-06-01"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(self.url, headers=headers,
                                  json={"model": self.model, "max_tokens": max_tokens,
                                        "system": system,
                                        "messages": [{"role": "user", "content": user}]})
            _check(r, "anthropic", self.model)
            data = r.json()
            return "".join(b.get("text", "") for b in data.get("content", []))


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str, api_key: Optional[str] = None, endpoint: Optional[str] = None):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.url = (endpoint or base_url("openai")) + "/v1/chat/completions"
        if not endpoint:
            _require_key_or_endpoint("openai", self.api_key, "OPENAI_API_KEY")

    async def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        async with httpx.AsyncClient(timeout=180) as client:
            body = {"model": self.model,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                    "max_completion_tokens": max_tokens}
            r = await client.post(self.url, headers=_bearer(self.api_key), json=body)
            if r.status_code == 400 and "max_completion_tokens" in r.text:
                body.pop("max_completion_tokens")
                body["max_tokens"] = max_tokens
                r = await client.post(self.url, headers=_bearer(self.api_key), json=body)
            _check(r, "openai", self.model)
            return r.json()["choices"][0]["message"]["content"]


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, model: str, api_key: Optional[str] = None, endpoint: Optional[str] = None):
        self.model = model
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.url = (endpoint or base_url("gemini")) + "/v1beta/models/{model}:generateContent"
        if not endpoint:
            _require_key_or_endpoint("gemini", self.api_key, "GEMINI_API_KEY (or GOOGLE_API_KEY)")

    async def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(self.url.format(model=self.model),
                                  params={"key": self.api_key} if self.api_key else None,
                                  json={"system_instruction": {"parts": [{"text": system}]},
                                        "contents": [{"role": "user", "parts": [{"text": user}]}],
                                        "generationConfig": {"maxOutputTokens": max_tokens}})
            _check(r, "gemini", self.model)
            data = r.json()
            parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts)


class XAIProvider(LLMProvider):
    name = "xai"

    def __init__(self, model: str, api_key: Optional[str] = None, endpoint: Optional[str] = None):
        self.model = model
        self.api_key = api_key or os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")
        self.url = (endpoint or base_url("xai")) + "/v1/chat/completions"
        if not endpoint:
            _require_key_or_endpoint("xai", self.api_key, "XAI_API_KEY (or GROK_API_KEY)")

    async def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(self.url, headers=_bearer(self.api_key),
                                  json={"model": self.model, "max_tokens": max_tokens,
                                        "messages": [{"role": "system", "content": system},
                                                     {"role": "user", "content": user}]})
            _check(r, "xai", self.model)
            return r.json()["choices"][0]["message"]["content"]


PROVIDERS = {
    "openrouter": OpenRouterProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "xai": XAIProvider,
}
PROVIDER_ENV = {
    "openrouter": ["OPENROUTER_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "xai": ["XAI_API_KEY", "GROK_API_KEY"],
}

# Default bench: one current frontier model per vendor (verified 2026-06).
# Model names are configuration, not code — override with GAGE_DEFAULT_MODELS
# (comma-separated refs) when these age out, no code change needed.
DEFAULT_MODELS = [
    "anthropic:claude-opus-4-8",
    "openai:gpt-5.5",
    "gemini:gemini-3.1-pro-preview",
]


def default_models() -> list[str]:
    env = os.environ.get("GAGE_DEFAULT_MODELS", "")
    pool = [m.strip() for m in env.split(",") if m.strip()]
    return pool or list(DEFAULT_MODELS)


MODEL_CATALOG: dict[str, list[str]] = {
    "anthropic": [
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    "openai": [
        "gpt-5.5",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "o3",
        "o4-mini",
    ],
    "gemini": [
        "gemini-3.1-pro-preview",
        "gemini-2.5-flash",
    ],
}


def model_catalog() -> list[dict]:
    seen = set()
    out = []
    for ref, entry in _MODEL_ENDPOINTS.items():
        if ":" in ref:
            v, m = ref.split(":", 1)
            out.append({"ref": ref, "vendor": v, "model": m,
                        "endpoint": entry["endpoint"]})
            seen.add(ref)
    for v, models in MODEL_CATALOG.items():
        for m in models:
            ref = f"{v}:{m}"
            if ref not in seen:
                out.append({"ref": ref, "vendor": v, "model": m})
    return out


def provider_available(mode: str) -> bool:
    if mode == "mock":
        return True
    if any(os.environ.get(k) for k in PROVIDER_ENV.get(mode, [])):
        return True
    if base_url_override(mode) is not None:
        return True
    return any(ref.startswith(f"{mode}:") for ref in _MODEL_ENDPOINTS)


def provider_status() -> dict:
    """Per-provider availability with the satisfying variable and its source
    (environment vs .env) — names only, never values. `endpoint` reports a
    custom base URL when one is configured."""
    from .env import env_file_keys
    file_keys = env_file_keys()
    out = {"mock": {"available": True, "via": None, "source": "builtin", "endpoint": None}}
    for mode, env_vars in PROVIDER_ENV.items():
        var = next((v for v in env_vars if os.environ.get(v)), None)
        endpoint = base_url_override(mode)
        has_model_endpoints = any(ref.startswith(f"{mode}:") for ref in _MODEL_ENDPOINTS)
        via = var or (BASE_URL_ENV[mode] if endpoint else None) or ("models.yaml" if has_model_endpoints else None)
        out[mode] = {"available": bool(via), "via": via,
                     "source": (".env" if via in file_keys else "environment") if via and via != "models.yaml" else via,
                     "endpoint": endpoint}
    return out


def configured_model_endpoints() -> list[dict]:
    return [{"ref": ref, "endpoint": entry["endpoint"],
             "api_key_env": entry.get("api_key_env") or None}
            for ref, entry in _MODEL_ENDPOINTS.items()]


def make_provider(mode: str, model: str) -> LLMProvider:
    cls = PROVIDERS.get(mode)
    if cls is None:
        raise ProviderError(f"unknown provider mode: {mode}")
    ref = f"{mode}:{model}"
    endpoint = model_endpoint(ref)
    api_key = model_api_key(ref)
    return cls(model, api_key=api_key, endpoint=endpoint)


def parse_model_ref(ref: str, default_provider: str) -> tuple[str, str]:
    """'openai:gpt-x' -> ('openai', 'gpt-x'); bare refs use the default
    provider. OpenRouter slash-paths pass through untouched."""
    ref = (ref or "").strip()
    if ":" in ref:
        p, m = ref.split(":", 1)
        if p in PROVIDERS or p == "mock":
            return p, m.strip()
    return default_provider, ref


def vendor_of(provider_mode: str, model: str) -> str:
    """Model-family attribution for diversity math. OpenRouter models carry
    their vendor in the path; mock labels may embed one for offline demos."""
    if provider_mode == "openrouter" and "/" in model:
        return model.split("/", 1)[0]
    if provider_mode == "mock":
        if ":" in model:
            return model.split(":", 1)[0]
        if "/" in model:
            return model.split("/", 1)[0]
        return "mock"
    return provider_mode


# ---------------------------------------------------------------------------
# JSON extraction with one repair pass — LLM output is hostile input.
# ---------------------------------------------------------------------------

def extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------------------
# Prompt contracts
# ---------------------------------------------------------------------------

def _criteria_block(criteria: CriteriaStack) -> str:
    lines = []
    for d in criteria.dimensions:
        gate = " [GATING]" if d.gating else ""
        anchors = "; ".join(f"{k}={v}" for k, v in sorted(d.anchors.items()))
        lines.append(f"- {d.name}{gate} ({d.source}): {d.question} Anchors: {anchors}")
    return "\n".join(lines)


CRITIQUE_SYSTEM = """You are one seat on an artifact review council. You review text artifacts
against an explicit criteria stack and emit findings with citations. Rules that are not negotiable:
1. Every finding MUST cite a location in the artifact (section heading or quoted phrase). No citation, no finding.
2. Findings classify into exactly these kinds: omission, contradiction, unsupported_claim, ambiguity,
   scope_violation, stale_reference, infeasibility, policy_conflict.
3. Severities: blocking, major, minor, suggestion. Blocking means: do not accept until fixed.
4. Score every dimension 1-5 against its anchors; cite the anchor your score matches.
5. Judge what is ABSENT as much as what is present.
6. Output ONLY a single JSON object. No prose before or after, no markdown fences."""

def critique_user_prompt(persona: Persona, manifest: Manifest, criteria: CriteriaStack, body: str) -> str:
    checks = "\n".join(f"- {c}" for c in criteria.instance_criteria) or "(none)"
    return f"""YOUR PERSONA — {persona.title}
Mandate: {persona.mandate}
Stance: {persona.stance}
Focus defect kinds: {persona.focus_kinds or 'any'}; focus dimensions: {persona.focus_dimensions or 'any'}.

ARTIFACT MANIFEST
id: {manifest.id} | type: {manifest.type}
purpose: {manifest.purpose}
audience: {manifest.audience}
constraints: {manifest.constraints}

CRITERIA STACK (score ALL dimensions)
{_criteria_block(criteria)}

INSTANCE ACCEPTANCE CRITERIA (binary pass/fail each, with citation)
{checks}

ARTIFACT BODY
<<<
{body}
>>>

Respond with ONLY this JSON shape:
{{
 "scores": [{{"dimension": "...", "score": 1-5, "anchor_cited": "..."}}],
 "findings": [{{"dimension": "...", "severity": "blocking|major|minor|suggestion",
   "location": "...", "kind": "...", "statement": "...", "evidence": "...", "recommendation": "..."}}],
 "instance_checks": [{{"criterion": "...", "passed": true, "citation": "..."}}],
 "narrative": "3 sentences max"
}}"""


ADJUDICATE_SYSTEM = """You are cross-examining anonymized peer reviews of a text artifact. For each
finding, attempt to independently verify it against the artifact. confirm = you can re-derive it from
the text; reject = your re-check contradicts it (say where); cannot_verify = outside what the text can
settle. One sentence of reasoning each. Also rate each anonymous critique's rigor 1-5 (citations,
specificity, severity calibration). Output ONLY JSON."""

def adjudicate_user_prompt(persona: Persona, body: str, labeled_findings: str, labels: list[str]) -> str:
    return f"""You are reviewing as: {persona.title} ({persona.stance})

ARTIFACT BODY
<<<
{body}
>>>

ANONYMIZED PEER FINDINGS
{labeled_findings}

Respond with ONLY this JSON shape:
{{
 "adjudications": [{{"finding_id": "...", "verdict": "confirm|reject|cannot_verify", "reasoning": "..."}}],
 "rigor": {{{", ".join(f'"{l}": 1-5' for l in labels)}}}
}}"""


CHAIRMAN_SYSTEM = """You are the Chairman of an artifact review council. Your role is to synthesize
the full evaluation into a coherent judgment that a human decision-maker can act on. The mechanical
disposition has already been computed by policy and you CANNOT change it, but your synthesis IS the
evaluation — it must stand on its own.

Write a structured synthesis covering:
1. SUBJECT — what artifact is being evaluated: its id, type, stated purpose, and intended audience.
2. OVERALL ASSESSMENT — one sentence: is this artifact ready, and at what confidence level?
3. CONSENSUS FINDINGS — what the council agreed on (highest-consensus, cross-vendor findings first).
4. CONTESTED AREAS — where reviewers disagreed and what that uncertainty means for the decision.
5. CRITICAL GAPS — what the artifact must address before it can be approved (blocking findings).
6. RECOMMENDATIONS — concrete next steps for the author, ordered by priority.

If seats failed during the review, note the reduced coverage and its implications.
If you believe the computed disposition is wrong given the evidence, add a dissent note explaining
why — the dissent is recorded, the disposition stands.

Keep the synthesis under 500 words. Be direct and specific — cite finding locations and dimensions.
Output ONLY JSON: {"narrative": "...", "dissent": ""}"""

def chairman_user_prompt(ctx: str) -> str:
    return f"COUNCIL RECORD\n{ctx}\n\nRespond with ONLY the JSON object."


INFER_MANIFEST_SYSTEM = """You draft GAGE manifests for bare documents so they become evaluable.
A manifest declares: what the document must accomplish (purpose), for whom (audience), and the
non-negotiables (acceptance_criteria). Be faithful to the document — infer, do not invent ambition
the text does not show. Output ONLY JSON."""

def infer_manifest_user_prompt(body: str, known_types: list[str]) -> str:
    return f"""KNOWN ARTIFACT TYPES (pick the best fit, or "unknown"): {known_types}

DOCUMENT
<<<
{body[:12000]}
>>>

Respond with ONLY this JSON shape:
{{
 "id": "kebab-case-slug-from-title",
 "type": "one of the known types or unknown",
 "purpose": "one paragraph: what this document must accomplish to succeed",
 "audience": ["..."],
 "acceptance_criteria": ["2-4 concrete, checkable musts implied by the document itself"],
 "constraints": [],
 "confidence": "high|medium|low",
 "type_rationale": "one sentence"
}}"""
