"""Registries: genre profiles (with inheritance), personas, disposition policies.

All variation in the framework lives in these YAML registries. Adding a new
artifact type means writing a profile, never touching pipeline code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

from .schemas import CriteriaStack, Dimension, Manifest
from .taxonomy import UNIVERSAL_DIMENSIONS


class Profile(BaseModel):
    name: str
    version: str = "1"
    summary: str = ""
    extends: Optional[str] = None
    signals: list[str] = Field(default_factory=list)        # keywords for type inference
    dimensions: list[dict] = Field(default_factory=list)    # {name, question, anchors}
    gating_dimensions: list[str] = Field(default_factory=list)
    structure_expectations: list[str] = Field(default_factory=list)
    personas: list[str] = Field(default_factory=list)        # specialist seats to add
    defect_probes: list[str] = Field(default_factory=list)   # calibration hints


class Persona(BaseModel):
    """A charter, not a vibe: mandate + stance + mechanically checkable
    obligations the seat must discharge every review."""
    name: str
    title: str
    mandate: str
    stance: str = ""
    core: bool = False           # core personas sit on every council
    focus_kinds: list[str] = Field(default_factory=list)
    focus_dimensions: list[str] = Field(default_factory=list)
    obligations: list[dict] = Field(default_factory=list)
    # obligation checks: scored_all_dimensions | checked_all_instance_criteria
    #                    | min_findings {n} | min_findings_on_gating {n}
    #                    | min_findings_of_kind {kind, n}


class Registry:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.profiles: dict[str, Profile] = {}
        self.personas: dict[str, Persona] = {}
        self.policies: dict[str, dict] = {}
        self._load()

    def _load_dir(self, sub: str) -> list[dict]:
        out = []
        d = self.root / sub
        if d.exists():
            for p in sorted(d.glob("*.yaml")):
                with open(p) as f:
                    data = yaml.safe_load(f)
                if data:
                    out.append(data)
        return out

    def _load(self):
        for data in self._load_dir("profiles"):
            prof = Profile(**data)
            self.profiles[prof.name] = prof
        for data in self._load_dir("personas"):
            per = Persona(**data)
            self.personas[per.name] = per
        for data in self._load_dir("policies"):
            self.policies[data["id"]] = data

    # -- profile inheritance -------------------------------------------------

    def resolve_profile(self, name: str) -> Profile:
        """Resolve `extends` chains: child dimensions override parent
        dimensions by name; gates, structure, personas are unioned."""
        if name not in self.profiles:
            name = "_default"
        chain: list[Profile] = []
        cur: Optional[str] = name
        seen = set()
        while cur and cur in self.profiles and cur not in seen:
            seen.add(cur)
            chain.append(self.profiles[cur])
            cur = self.profiles[cur].extends
        chain.reverse()  # base first

        dims: dict[str, dict] = {}
        gates: list[str] = []
        structure: list[str] = []
        personas: list[str] = []
        probes: list[str] = []
        for prof in chain:
            for d in prof.dimensions:
                dims[d["name"]] = d
            for g in prof.gating_dimensions:
                if g not in gates:
                    gates.append(g)
            for s in prof.structure_expectations:
                if s not in structure:
                    structure.append(s)
            for p in prof.personas:
                if p not in personas:
                    personas.append(p)
            for pr in prof.defect_probes:
                if pr not in probes:
                    probes.append(pr)
        leaf = chain[-1]
        return Profile(
            name=leaf.name, version=leaf.version, summary=leaf.summary,
            signals=leaf.signals, dimensions=list(dims.values()),
            gating_dimensions=gates, structure_expectations=structure,
            personas=personas, defect_probes=probes,
        )

    # -- criteria composition (Stage 0) ---------------------------------------

    def compose_criteria(self, manifest: Manifest) -> CriteriaStack:
        prof = self.resolve_profile(manifest.type)
        gates = set(prof.gating_dimensions)
        dims: list[Dimension] = []
        for d in UNIVERSAL_DIMENSIONS:
            dims.append(Dimension(name=d["name"], question=d["question"],
                                  anchors=d["anchors"], source="universal",
                                  gating=d["name"] in gates))
        for d in prof.dimensions:
            dims.append(Dimension(name=d["name"], question=d.get("question", ""),
                                  anchors={int(k): v for k, v in (d.get("anchors") or {}).items()},
                                  source="profile", gating=d["name"] in gates))
        return CriteriaStack(
            profile=prof.name, profile_version=prof.version, dimensions=dims,
            instance_criteria=list(manifest.acceptance_criteria),
        )

    # -- council composition ---------------------------------------------------

    def council_personas(self, manifest: Manifest, requested: Optional[list[str]] = None) -> list[Persona]:
        if requested:
            return [self.personas[p] for p in requested if p in self.personas]
        prof = self.resolve_profile(manifest.type)
        names = [p.name for p in self.personas.values() if p.core]
        for p in prof.personas:
            if p in self.personas and p not in names:
                names.append(p)
        return [self.personas[n] for n in names]

    # -- type inference support -------------------------------------------------

    def known_types(self) -> list[str]:
        return [n for n in self.profiles if not n.startswith("_")]

    def guess_type(self, body: str) -> str:
        text = body.lower()
        best, best_score = "_default", 0
        for name, prof in self.profiles.items():
            if name.startswith("_"):
                continue
            score = sum(text.count(sig.lower()) for sig in prof.signals)
            if score > best_score:
                best, best_score = name, score
        return best if best_score > 0 else "unknown"
