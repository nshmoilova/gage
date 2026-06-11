"""GAGE data contracts.

One finding schema, one critique schema, one report schema — for every
artifact type. This file is the part of the framework that never varies.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .taxonomy import DefectKind, Disposition, Severity


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Manifest — what makes a bare blob of text evaluable.
# ---------------------------------------------------------------------------

class Manifest(BaseModel):
    id: str
    type: str = "unknown"
    purpose: str
    audience: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    claims_of_record: list[str] = Field(default_factory=list)
    context_refs: list[dict] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    generation_provenance: dict = Field(default_factory=dict)

    @field_validator("purpose")
    @classmethod
    def purpose_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("manifest.purpose must be non-empty — evaluation is undefined without it")
        return v.strip()


# ---------------------------------------------------------------------------
# Criteria stack — composed at Stage 0 from universal + profile + instance.
# ---------------------------------------------------------------------------

class Dimension(BaseModel):
    name: str
    question: str
    anchors: dict[int, str] = Field(default_factory=dict)
    source: Literal["universal", "profile"] = "universal"
    gating: bool = False


class CriteriaStack(BaseModel):
    profile: str
    profile_version: str
    dimensions: list[Dimension]
    instance_criteria: list[str] = Field(default_factory=list)

    def dimension_names(self) -> list[str]:
        return [d.name for d in self.dimensions]

    def gating_names(self) -> list[str]:
        return [d.name for d in self.dimensions if d.gating]


class Seat(BaseModel):
    """A council seat = (model, persona). Anonymized at Stage 2."""
    id: str                      # e.g. "seat-1"
    persona: str                 # persona name from registry
    model: str                   # model identifier or "mock"
    provider_mode: str           # mock | openrouter | anthropic


# ---------------------------------------------------------------------------
# Stage 1 outputs
# ---------------------------------------------------------------------------

class Finding(BaseModel):
    id: str
    dimension: str
    severity: Severity
    location: str                # mandatory: no citation, no finding
    kind: DefectKind
    statement: str
    evidence: str = ""
    recommendation: str = ""


class InstanceCheck(BaseModel):
    criterion: str
    passed: bool
    citation: str = ""


class DimensionScore(BaseModel):
    dimension: str
    score: int = Field(ge=1, le=5)
    anchor_cited: str = ""


class Critique(BaseModel):
    seat_id: str
    persona: str
    model: str
    scores: list[DimensionScore]
    findings: list[Finding]
    instance_checks: list[InstanceCheck]
    narrative: str = ""


# ---------------------------------------------------------------------------
# Stage 2 outputs
# ---------------------------------------------------------------------------

class Adjudication(BaseModel):
    adjudicator_seat: str
    target_seat: str             # de-anonymized for the evidence record
    finding_id: str
    verdict: Literal["confirm", "reject", "cannot_verify"]
    reasoning: str = ""


class RigorRanking(BaseModel):
    adjudicator_seat: str
    # anonymized label -> rigor score 1-5; labels resolved back for evidence
    scores: dict[str, int]


# ---------------------------------------------------------------------------
# Stage 3 outputs
# ---------------------------------------------------------------------------

class ConsolidatedFinding(BaseModel):
    key: str
    dimension: str
    severity: Severity
    kind: DefectKind
    location: str
    statement: str
    evidence: str = ""
    recommendation: str = ""
    raised_by: list[str]
    confirms: int = 0
    rejects: int = 0
    cannot_verify: int = 0
    consensus: float = 0.0       # (raisers + confirms) / seats, capped at 1.0
    # adversarial-matrix metadata: model-family diversity behind the finding
    vendors_raising: list[str] = Field(default_factory=list)
    vendors_confirming: list[str] = Field(default_factory=list)
    cross_vendor: bool = False   # >=2 distinct model families stand behind it


class DimensionSummary(BaseModel):
    dimension: str
    source: str
    gating: bool
    scores: list[int]
    median: float
    mean: float
    dispersion: float            # population stdev — disagreement is signal

    @staticmethod
    def from_scores(dim: Dimension, scores: list[int]) -> "DimensionSummary":
        return DimensionSummary(
            dimension=dim.name,
            source=dim.source,
            gating=dim.gating,
            scores=scores,
            median=float(statistics.median(scores)) if scores else 0.0,
            mean=round(statistics.fmean(scores), 2) if scores else 0.0,
            dispersion=round(statistics.pstdev(scores), 2) if len(scores) > 1 else 0.0,
        )


class InstanceCheckResult(BaseModel):
    criterion: str
    passes: int
    fails: int
    failed: bool                 # any seat failed it -> failed (conservative)
    citations: list[str] = Field(default_factory=list)


class Disagreement(BaseModel):
    finding_key: str
    statement: str
    confirms: int
    rejects: int
    note: str = ""


class ObligationResult(BaseModel):
    id: str
    description: str
    met: bool
    detail: str = ""


class CharterCompliance(BaseModel):
    """Did each seat discharge its charter? Charters are obligations, not
    vibes — a contrarian that raised nothing breached its charter."""
    seat_id: str
    persona: str
    model: str
    met: bool
    obligations: list[ObligationResult] = Field(default_factory=list)


class Report(BaseModel):
    run_id: str
    artifact_id: str
    profile: str
    policy_id: str
    charter_compliance: list[CharterCompliance] = Field(default_factory=list)
    dimension_summaries: list[DimensionSummary]
    findings: list[ConsolidatedFinding]
    instance_check_results: list[InstanceCheckResult]
    disagreements: list[Disagreement]
    disposition: Disposition
    fired_rule: str
    anomaly_flags: list[str]
    chairman_narrative: str = ""
    chairman_dissent: str = ""
    created_at: str = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Stage 0 / Stage 4 records
# ---------------------------------------------------------------------------

class EvaluationPlan(BaseModel):
    run_id: str
    artifact_id: str
    manifest: Manifest
    criteria: CriteriaStack
    seats: list[Seat]
    layout: str = "round_robin"          # round_robin | matrix | explicit
    coverage_notes: list[str] = Field(default_factory=list)
    chairman_model: str
    chairman_provider: str = "mock"
    policy_id: str
    created_at: str = Field(default_factory=utcnow)


class Seal(BaseModel):
    """The immutable evidence record. The seal is the product as much as the
    disposition is."""
    run_id: str
    artifact_sha256: str
    plan: EvaluationPlan
    critiques: list[Critique]
    adjudications: list[Adjudication]
    rankings: list[RigorRanking]
    report: Report
    human_decision: Optional[dict] = None   # filled at Stage 4 authorization
    sealed_at: str = Field(default_factory=utcnow)
    seal_sha256: str = ""                   # hash over everything above
