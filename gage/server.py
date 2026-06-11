"""GAGE server.

Endpoints:
  GET  /                       — the UI
  GET  /api/registry           — profiles, personas, policies (for the UI)
  POST /api/inspect            — frontmatter check + manifest draft when absent
  POST /api/manifest/apply     — inject a confirmed manifest into the document
  POST /api/evaluate           — start a council run (background task)
  GET  /api/runs/{id}          — poll run state
  POST /api/runs/{id}/authorize— Stage 4: record the human decision, write the seal
  GET  /api/runs/{id}/seal     — download the evidence seal

Security posture: provider API keys are read from the server environment only
(OPENROUTER_API_KEY / ANTHROPIC_API_KEY) and are never accepted from the
browser. Mock mode needs no keys at all.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .env import load_env
from .evidence import build_seal, write_seal
from .frontmatter import extract_manifest, inject_manifest
from .inference import infer_manifest_draft
from .pipeline import build_plan, run_evaluation
from .providers import (PROVIDER_ENV, ProviderError, default_models,
                        make_provider, model_catalog, parse_model_ref,
                        provider_available, provider_status)
from .registry import Registry
from .schemas import Manifest
from .taxonomy import DEFECT_KIND_DESCRIPTIONS, UNIVERSAL_DIMENSIONS

ROOT = Path(__file__).resolve().parent.parent

# ----- configuration: .env support with loud, value-free diagnostics -----
_ENV_LOADED = load_env()
for _path, _keys in _ENV_LOADED.items():
    if _keys:
        print(f"[gage] loaded {len(_keys)} key(s) from {_path}: {', '.join(_keys)}", file=sys.stderr)
if not any(_ENV_LOADED.values()):
    _looked = " and ".join(str(p) for p in [ROOT / '.env', Path.cwd() / '.env'])
    print(f"[gage] no .env keys loaded (looked for {_looked})", file=sys.stderr)
_live = [m for m in ("anthropic", "openai", "gemini", "xai", "openrouter") if provider_available(m)]
print(f"[gage] live providers available: {', '.join(_live) if _live else 'none — mock only'}", file=sys.stderr)

registry = Registry(ROOT / "registry")
SEALS_DIR = ROOT / "data" / "seals"

app = FastAPI(title="GAGE — Generic Artifact Governance & Evaluation")

RUNS: dict[str, dict] = {}


# ---------------------------------------------------------------------------

class InspectIn(BaseModel):
    text: str
    use_llm_inference: bool = False
    provider_mode: str = "mock"
    model: str = ""


class ApplyIn(BaseModel):
    text: str
    manifest: dict


class SeatSpec(BaseModel):
    persona: str
    model: str = ""   # may carry a provider prefix: 'openai:gpt-x'; empty -> roster round-robin


class EvaluateIn(BaseModel):
    text: str
    provider_mode: str = "mock"          # mock | openrouter | anthropic
    model: str = ""                       # single model (back-compat)
    models: Optional[list[str]] = None    # roster: seats round-robin when no explicit seat model
    seats: Optional[list[SeatSpec]] = None  # explicit seating chart: which model holds which seat
    layout: str = "round_robin"           # round_robin | matrix (charter x model cross product)
    chairman_model: str = ""              # the judge seat; may carry a provider prefix
    policy_id: str = "default-v2"
    personas: Optional[list[str]] = None  # ignored when seats is provided


class AuthorizeIn(BaseModel):
    decision: str                         # accept | accept_with_conditions | send_back | reject
    approver: str = "anonymous"
    rationale: str = ""
    dismissed_findings: list[dict] = Field(default_factory=list)  # [{key, reason}]


# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(ROOT / "ui" / "index.html")


@app.get("/api/registry")
def get_registry():
    return {
        "types": [{"name": n, "summary": registry.profiles[n].summary,
                   "gating": registry.resolve_profile(n).gating_dimensions,
                   "personas": registry.resolve_profile(n).personas}
                  for n in registry.known_types()],
        "personas": [{"name": p.name, "title": p.title, "core": p.core,
                      "mandate": p.mandate.strip()}
                     for p in registry.personas.values()],
        "policies": [{"id": pid, "summary": pol.get("summary", "")}
                     for pid, pol in registry.policies.items()],
        "universal_dimensions": UNIVERSAL_DIMENSIONS,
        "defect_kinds": {k.value: v for k, v in DEFECT_KIND_DESCRIPTIONS.items()},
        "providers_available": {p: provider_available(p)
                                for p in ["mock", "anthropic", "openai", "gemini", "xai", "openrouter"]},
        "provider_status": provider_status(),
        "default_models": default_models(),
        "model_catalog": model_catalog(),
    }


@app.post("/api/inspect")
async def inspect(inp: InspectIn):
    manifest, fm, body, err = extract_manifest(inp.text)
    out = {
        "has_frontmatter": fm is not None,
        "has_manifest": manifest is not None,
        "manifest": manifest.model_dump() if manifest else None,
        "manifest_error": err,
        "body_words": len(body.split()),
        "inferred": None,
    }
    if manifest is None:
        provider = None
        if inp.use_llm_inference and inp.provider_mode != "mock" and inp.model:
            try:
                provider = make_provider(inp.provider_mode, inp.model)
            except ProviderError as e:
                out["inference_note"] = str(e)
        out["inferred"] = await infer_manifest_draft(body, registry, provider)
    return out


@app.post("/api/manifest/apply")
def apply_manifest(inp: ApplyIn):
    raw = {k: v for k, v in inp.manifest.items()
           if k in Manifest.model_fields}
    try:
        manifest = Manifest(**raw)
    except Exception as e:
        raise HTTPException(422, f"manifest invalid: {e}")
    return {"text": inject_manifest(inp.text, manifest),
            "manifest": manifest.model_dump()}


@app.post("/api/evaluate")
async def evaluate(inp: EvaluateIn):
    manifest, _, body, err = extract_manifest(inp.text)
    if manifest is None:
        raise HTTPException(400, err or "No manifest in frontmatter — inspect the document and apply one first.")
    if inp.policy_id not in registry.policies:
        raise HTTPException(400, f"unknown policy '{inp.policy_id}'")
    pool = [m.strip() for m in (inp.models or []) if m and m.strip()]
    if not pool and inp.model:
        pool = [inp.model.strip()]
    specs = [{"persona": s.persona, "model": s.model.strip()} for s in (inp.seats or [])] or None
    seat_models = [s["model"] for s in (specs or []) if s["model"]]
    judge = inp.chairman_model.strip() or (pool[0] if pool else (seat_models[0] if seat_models else ""))
    if inp.provider_mode != "mock":
        if not pool and (specs is None or len(seat_models) < len(specs)):
            raise HTTPException(400, "every seat needs a model — assign per-seat models or provide a roster")
        # validate EVERY provider the matrix will touch, not just the default
        needed = {parse_model_ref(r, inp.provider_mode)[0] for r in (pool + seat_models + [judge]) if r}
        missing = [p for p in sorted(needed) if p != "mock" and not provider_available(p)]
        if missing:
            envs = "; ".join(f"{p}: set {' or '.join(PROVIDER_ENV[p])}" for p in missing)
            raise HTTPException(400, f"missing API keys for provider(s) {missing} — {envs}")

    plan = build_plan(manifest, registry, provider_mode=inp.provider_mode,
                      model=pool[0] if pool else "",
                      chairman_model=judge,
                      policy_id=inp.policy_id, personas=inp.personas, models=pool,
                      seat_specs=specs, layout=inp.layout)
    state = {"status": "running", "stage": "critique",
             "stages": {"intake": {"done": True, "at": plan.created_at},
                        "critique": {"done": 0, "total": len(plan.seats)},
                        "crossexam": {"done": 0, "total": len(plan.seats)},
                        "synthesis": {"done": False},
                        "seal": {"done": False}},
             "stage_seats": {"critique": [], "crossexam": []},
             "plan": plan.model_dump(), "critiques": [], "adjudications": [],
             "rankings": [], "report": None, "error": None,
             "seat_errors": [],
             "_plan": plan, "_body": body, "_text": inp.text,
             "_critiques": [], "_adjudications": [], "_rankings": []}
    RUNS[plan.run_id] = state

    def progress(stage: str, info: dict):
        if stage == "seat_critique":
            state["critiques"].append(info["critique"].model_dump())
            state["stage_seats"]["critique"].append(info["seat_id"])
            return
        if stage == "seat_adjudications":
            state["adjudications"].extend(a.model_dump() for a in info["adjudications"])
            state["rankings"].append(info["ranking"].model_dump())
            state["stage_seats"]["crossexam"].append(info["seat_id"])
            return
        if stage == "seat_error":
            state["seat_errors"].append(info)
            return
        state["stage"] = stage
        if stage in state["stages"] and info:
            state["stages"][stage].update(info)

    async def task():
        try:
            critiques, adjs, ranks, report = await run_evaluation(
                body, plan, registry, registry.policies[inp.policy_id], progress)
            state.update({
                "critiques": [c.model_dump() for c in critiques],
                "adjudications": [a.model_dump() for a in adjs],
                "rankings": [r.model_dump() for r in ranks],
                "report": report.model_dump(),
                "_critiques": critiques, "_adjudications": adjs,
                "_rankings": ranks, "_report": report,
                "status": "awaiting_authorization", "stage": "authorize",
            })
            state["stages"]["synthesis"]["done"] = True
        except Exception as e:  # surfaced to the client AND printed to the server console
            traceback.print_exc()
            state.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    asyncio.create_task(task())
    return {"run_id": plan.run_id}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    state = RUNS.get(run_id)
    if not state:
        raise HTTPException(404, "unknown run")
    return JSONResponse({k: v for k, v in state.items() if not k.startswith("_")})


@app.post("/api/runs/{run_id}/authorize")
def authorize(run_id: str, inp: AuthorizeIn):
    state = RUNS.get(run_id)
    if not state:
        raise HTTPException(404, "unknown run")
    if state["status"] != "awaiting_authorization":
        raise HTTPException(409, f"run is '{state['status']}', not awaiting authorization")
    report = state["_report"]
    blocking_keys = {f.key for f in report.findings if f.severity.value == "blocking"}
    dismissed = {d.get("key"): d.get("reason", "") for d in inp.dismissed_findings}
    if inp.decision in ("accept", "accept_with_conditions"):
        undismissed = [k for k in blocking_keys if k not in dismissed or not dismissed[k].strip()]
        if undismissed:
            raise HTTPException(422,
                f"{len(undismissed)} blocking finding(s) must each carry a recorded dismissal "
                f"reason before acceptance: {sorted(undismissed)[:3]}")
    decision = {"decision": inp.decision, "approver": inp.approver,
                "rationale": inp.rationale, "dismissed_findings": inp.dismissed_findings}
    seal = build_seal(state["_text"], state["_plan"], state["_critiques"],
                      state["_adjudications"], state["_rankings"], report, decision)
    path = write_seal(seal, SEALS_DIR)
    state.update({"status": "sealed", "stage": "seal",
                  "seal": {"path": str(path), "sha256": seal.seal_sha256,
                           "human_decision": decision}})
    state["stages"]["seal"] = {"done": True, "sha256": seal.seal_sha256}
    return {"sealed": True, "seal_sha256": seal.seal_sha256}


@app.get("/api/runs/{run_id}/seal")
def get_seal(run_id: str):
    path = SEALS_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, "no seal for this run (authorize it first)")
    return FileResponse(path, media_type="application/json",
                        filename=f"gage-seal-{run_id}.json")
