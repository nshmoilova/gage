"""The invariant pipeline. Five stages, identical for every artifact type;
all type-awareness enters as data composed at Stage 0.

Stage 0  intake & binding      -> EvaluationPlan
Stage 1  independent critique  -> list[Critique]          (seats in isolation)
Stage 2  cross-examination     -> adjudications, rankings  (anonymized)
Stage 3  synthesis             -> Report                   (mechanical disposition)
Stage 4  human authorization   -> Seal                     (server layer / evidence.py)
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections import defaultdict
from typing import Awaitable, Callable, Optional

from pydantic import ValidationError

from .mock import MockEngine
from .policy import PolicyEngine
from .providers import (ADJUDICATE_SYSTEM, CHAIRMAN_SYSTEM, CRITIQUE_SYSTEM,
                        LLMProvider, adjudicate_user_prompt, chairman_user_prompt,
                        critique_user_prompt, extract_json, make_provider,
                        parse_model_ref, vendor_of)
from .registry import Persona, Registry
from .schemas import (Adjudication, CharterCompliance, ConsolidatedFinding, Critique,
                      CriteriaStack, DimensionScore, DimensionSummary, Disagreement,
                      EvaluationPlan, Finding, InstanceCheck, InstanceCheckResult,
                      Manifest, ObligationResult, Report, RigorRanking, Seat)
from .taxonomy import SEVERITY_ORDER, DefectKind, Severity

ProgressFn = Callable[[str, dict], None]


def _noop(stage: str, info: dict) -> None:  # pragma: no cover
    pass


# ---------------------------------------------------------------------------
# Stage 0
# ---------------------------------------------------------------------------

def build_plan(manifest: Manifest, registry: Registry, *, provider_mode: str,
               model: str, chairman_model: str, policy_id: str,
               personas: Optional[list[str]] = None,
               models: Optional[list[str]] = None,
               seat_specs: Optional[list[dict]] = None,
               layout: str = "round_robin") -> EvaluationPlan:
    """Seat assignment, most explicit wins:
    - layout="matrix": the adversarial matrix — every (charter × model) pair
      becomes a seat, so each charter is argued by every model family.
    - seat_specs: [{persona, model}] — exact seating chart from the UI chamber.
    - models: roster; round_robin layout takes models round-robin.
    Model refs may carry a provider prefix ('openai:gpt-x'); bare refs use the
    run-level provider. Mock keeps the labels (for offline matrix demos) but
    routes every call to the deterministic engine."""
    criteria = registry.compose_criteria(manifest)
    specs = [s for s in (seat_specs or []) if s.get("persona") in registry.personas] or None
    pool = [m for m in (models or []) if m] or ([model] if model else [])

    def resolve(ref: str) -> tuple[str, str]:
        prov, mod = parse_model_ref(ref, provider_mode)
        if provider_mode == "mock":
            return "mock", (ref.strip() or "mock")   # keep the label, route to mock
        return prov, mod

    seats: list[Seat] = []
    if layout == "matrix" and not specs:
        chosen = registry.council_personas(manifest, personas)
        refs = pool or ["mock"]
        i = 0
        for p in chosen:                       # persona-major: charter × every model
            for ref in refs:
                prov, mod = resolve(ref)
                i += 1
                seats.append(Seat(id=f"seat-{i}", persona=p.name, model=mod, provider_mode=prov))
    else:
        if specs:
            chosen = [registry.personas[s["persona"]] for s in specs]
            # layout label passes through: a pruned matrix is still a matrix
        else:
            chosen = registry.council_personas(manifest, personas)
        for i, p in enumerate(chosen):
            ref = (specs[i].get("model") if specs and specs[i].get("model")
                   else (pool[i % len(pool)] if pool else model))
            prov, mod = resolve(str(ref or ""))
            seats.append(Seat(id=f"seat-{i+1}", persona=p.name, model=mod, provider_mode=prov))

    # ----- coverage notes: is the matrix actually adversarial? -----
    notes: list[str] = []
    vendors = {vendor_of(s.provider_mode, s.model) for s in seats}
    if len(vendors) == 1:
        notes.append(f"single model family on the bench ({next(iter(vendors))}) — "
                     "cross-vendor consensus is undefined; family-correlated errors undetectable")
    by_persona: dict[str, set] = {}
    for s in seats:
        by_persona.setdefault(s.persona, set()).add(vendor_of(s.provider_mode, s.model))
    for persona, vset in by_persona.items():
        if len(vendors) > 1 and len(vset) < 2:
            notes.append(f"charter '{persona}' argued by a single family ({next(iter(vset))}) — "
                         "its findings cannot earn cross-vendor confirmation at raise time")
    if not any(s.persona == "contrarian" for s in seats):
        notes.append("no contrarian seated — the council has no designated adversarial charter")

    c_prov, c_mod = resolve(chairman_model) if chairman_model else (provider_mode if provider_mode == "mock" else provider_mode, chairman_model)
    if provider_mode == "mock":
        c_prov = "mock"
        c_mod = chairman_model or "mock"
    return EvaluationPlan(run_id=uuid.uuid4().hex[:12], artifact_id=manifest.id,
                          manifest=manifest, criteria=criteria, seats=seats,
                          layout=layout, coverage_notes=notes,
                          chairman_model=c_mod, chairman_provider=c_prov,
                          policy_id=policy_id)


# ---------------------------------------------------------------------------
# Stage 1 helpers (LLM path)
# ---------------------------------------------------------------------------

def _parse_critique(seat: Seat, raw: str, criteria: CriteriaStack) -> Critique:
    data = extract_json(raw)
    findings = []
    for i, f in enumerate(data.get("findings", [])):
        try:
            if not f.get("location"):
                continue  # no citation, no finding — enforced, not requested
            findings.append(Finding(id=f"{seat.id}-f{i+1}",
                                    dimension=f.get("dimension", "purpose_fitness"),
                                    severity=Severity(f.get("severity", "minor")),
                                    location=str(f["location"])[:200],
                                    kind=DefectKind(f.get("kind", "ambiguity")),
                                    statement=str(f.get("statement", ""))[:500],
                                    evidence=str(f.get("evidence", ""))[:500],
                                    recommendation=str(f.get("recommendation", ""))[:500]))
        except (ValidationError, ValueError):
            continue
    valid_dims = set(criteria.dimension_names())
    scores = []
    for s in data.get("scores", []):
        try:
            if s.get("dimension") in valid_dims:
                scores.append(DimensionScore(dimension=s["dimension"],
                                             score=int(s["score"]),
                                             anchor_cited=str(s.get("anchor_cited", ""))[:300]))
        except (ValidationError, ValueError, TypeError):
            continue
    checks = []
    for c in data.get("instance_checks", []):
        try:
            checks.append(InstanceCheck(criterion=str(c["criterion"])[:300],
                                        passed=bool(c["passed"]),
                                        citation=str(c.get("citation", ""))[:300]))
        except (KeyError, ValidationError):
            continue
    return Critique(seat_id=seat.id, persona=seat.persona, model=seat.model,
                    scores=scores, findings=findings, instance_checks=checks,
                    narrative=str(data.get("narrative", ""))[:1200])


# ---------------------------------------------------------------------------
# Stage 3 helpers — consolidation & consensus math (deterministic & auditable)
# ---------------------------------------------------------------------------

def _norm_location(loc: str) -> str:
    s = loc.lower()
    digits = "".join(re.findall(r"\d+", s))
    words = re.findall(r"[a-z]{4,}", s)
    return f"{digits}|{words[0] if words else ''}"


def consolidate(critiques: list[Critique], adjudications: list[Adjudication],
                n_seats: int, seat_vendor: Optional[dict[str, str]] = None
                ) -> tuple[list[ConsolidatedFinding], list[Disagreement]]:
    seat_vendor = seat_vendor or {}
    by_finding: dict[str, list[Adjudication]] = defaultdict(list)
    for a in adjudications:
        by_finding[f"{a.target_seat}:{a.finding_id}"].append(a)

    groups: dict[str, dict] = {}
    for c in critiques:
        for f in c.findings:
            key = f"{f.kind.value}|{f.dimension}|{_norm_location(f.location)}"
            g = groups.setdefault(key, {"findings": [], "raisers": set(),
                                        "confirm": 0, "reject": 0, "cv": 0})
            g["findings"].append((c.seat_id, f))
            g["raisers"].add(c.seat_id)
            for a in by_finding.get(f"{c.seat_id}:{f.id}", []):
                if a.verdict == "confirm":
                    g["confirm"] += 1
                elif a.verdict == "reject":
                    g["reject"] += 1
                else:
                    g["cv"] += 1

    consolidated: list[ConsolidatedFinding] = []
    disagreements: list[Disagreement] = []
    for key, g in groups.items():
        # representative = most severe instance
        seat_id, rep = max(g["findings"], key=lambda t: SEVERITY_ORDER[t[1].severity])
        # confirming seats: distinct adjudicators outside raisers who confirmed ANY instance
        confirmers = set()
        rejecters = set()
        for sid, f in g["findings"]:
            for a in by_finding.get(f"{sid}:{f.id}", []):
                if a.adjudicator_seat in g["raisers"]:
                    continue
                if a.verdict == "confirm":
                    confirmers.add(a.adjudicator_seat)
                elif a.verdict == "reject":
                    rejecters.add(a.adjudicator_seat)
        consensus = min(1.0, (len(g["raisers"]) + len(confirmers)) / max(n_seats, 1))
        v_raise = sorted({seat_vendor.get(s, "?") for s in g["raisers"]})
        v_conf = sorted({seat_vendor.get(s, "?") for s in confirmers})
        cf = ConsolidatedFinding(key=key, dimension=rep.dimension, severity=rep.severity,
                                 kind=rep.kind, location=rep.location, statement=rep.statement,
                                 evidence=rep.evidence, recommendation=rep.recommendation,
                                 raised_by=sorted(g["raisers"]),
                                 confirms=len(confirmers), rejects=len(rejecters),
                                 cannot_verify=g["cv"],
                                 consensus=round(consensus, 2),
                                 vendors_raising=v_raise, vendors_confirming=v_conf,
                                 cross_vendor=len(set(v_raise) | set(v_conf)) >= 2)
        consolidated.append(cf)
        if confirmers and rejecters:
            disagreements.append(Disagreement(
                finding_key=key, statement=rep.statement,
                confirms=len(confirmers), rejects=len(rejecters),
                note="Council split on verification — reported verbatim, not smoothed."))

    consolidated.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], -f.consensus))
    return consolidated, disagreements


def summarize_dimensions(criteria: CriteriaStack, critiques: list[Critique]) -> list[DimensionSummary]:
    out = []
    for d in criteria.dimensions:
        scores = [s.score for c in critiques for s in c.scores if s.dimension == d.name]
        if scores:
            out.append(DimensionSummary.from_scores(d, scores))
    return out


def summarize_checks(criteria: CriteriaStack, critiques: list[Critique]) -> list[InstanceCheckResult]:
    out = []
    for crit in criteria.instance_criteria:
        passes = fails = 0
        citations = []
        for c in critiques:
            for ic in c.instance_checks:
                if ic.criterion == crit:
                    if ic.passed:
                        passes += 1
                        if ic.citation:
                            citations.append(ic.citation)
                    else:
                        fails += 1
        out.append(InstanceCheckResult(criterion=crit, passes=passes, fails=fails,
                                       failed=fails > 0, citations=citations[:5]))
    return out


def check_charters(critiques: list[Critique], criteria: CriteriaStack,
                   personas: dict) -> list[CharterCompliance]:
    """Charters are obligations, mechanically checked — a seat that did not
    discharge its charter is itself a finding about the council."""
    gating = set(criteria.gating_names())
    dim_names = set(criteria.dimension_names())
    out: list[CharterCompliance] = []
    for c in critiques:
        persona = personas.get(c.persona)
        results: list[ObligationResult] = []
        for ob in (persona.obligations if persona else []):
            check, met, detail = ob.get("check", ""), True, ""
            if check == "scored_all_dimensions":
                scored = {s.dimension for s in c.scores}
                missing = dim_names - scored
                met, detail = not missing, ("missing: " + ", ".join(sorted(missing)) if missing else
                                            f"all {len(dim_names)} scored")
            elif check == "checked_all_instance_criteria":
                checked = {ic.criterion for ic in c.instance_checks}
                missing_n = len([x for x in criteria.instance_criteria if x not in checked])
                met, detail = missing_n == 0, (f"{missing_n} criteria unchecked" if missing_n else
                                               f"all {len(criteria.instance_criteria)} checked")
            elif check == "min_findings":
                n = int(ob.get("n", 1))
                met, detail = len(c.findings) >= n, f"{len(c.findings)} raised (need {n})"
            elif check == "min_findings_on_gating":
                n = int(ob.get("n", 1))
                hits = sum(1 for f in c.findings if f.dimension in gating)
                met, detail = hits >= n, f"{hits} on gating dims {sorted(gating)} (need {n})"
            elif check == "min_findings_of_kind":
                n = int(ob.get("n", 1))
                hits = sum(1 for f in c.findings if f.kind.value == ob.get("kind"))
                met, detail = hits >= n, f"{hits} of kind {ob.get('kind')} (need {n})"
            results.append(ObligationResult(id=ob.get("id", check), met=met, detail=detail,
                                            description=ob.get("description", "")))
        out.append(CharterCompliance(seat_id=c.seat_id, persona=c.persona, model=c.model,
                                     met=all(r.met for r in results), obligations=results))
    return out


# ---------------------------------------------------------------------------
# Full run
# ---------------------------------------------------------------------------

async def run_evaluation(body: str, plan: EvaluationPlan, registry: Registry,
                         policy: dict, progress: ProgressFn = _noop
                         ) -> tuple[list[Critique], list[Adjudication], list[RigorRanking], Report]:
    manifest, criteria, seats = plan.manifest, plan.criteria, plan.seats
    profile = registry.resolve_profile(manifest.type)
    personas = {p: registry.personas[p] for p in {s.persona for s in seats} if p in registry.personas}
    mock = MockEngine(body, manifest, criteria, profile)
    is_mock = all(s.provider_mode == "mock" for s in seats)

    # ---------------- Stage 1: independent critique --------------------------
    progress("critique", {"done": 0, "total": len(seats)})
    critiques: list[Critique] = []
    failed_seats: list[str] = []
    if is_mock:
        for i, seat in enumerate(seats):
            c = mock.critique(seat, personas[seat.persona])
            critiques.append(c)
            progress("seat_critique", {"seat_id": seat.id, "critique": c})
            progress("critique", {"done": i + 1, "total": len(seats)})
            await asyncio.sleep(0.12 if len(seats) > 8 else 0.25)
    else:
        async def one(seat: Seat) -> tuple[Seat, Optional[Critique]]:
            try:
                provider = make_provider(seat.provider_mode, seat.model)
                raw = await provider.complete(
                    CRITIQUE_SYSTEM,
                    critique_user_prompt(personas[seat.persona], manifest, criteria, body))
                return seat, _parse_critique(seat, raw, criteria)
            except Exception as e:
                import traceback; traceback.print_exc()
                progress("seat_error", {"seat_id": seat.id, "error": f"{type(e).__name__}: {e}"})
                return seat, None
        done = 0
        for coro in asyncio.as_completed([one(s) for s in seats]):
            seat, c = await coro
            done += 1
            if c is not None:
                critiques.append(c)
                progress("seat_critique", {"seat_id": c.seat_id, "critique": c})
            else:
                failed_seats.append(seat.id)
            progress("critique", {"done": done, "total": len(seats)})
        critiques.sort(key=lambda c: c.seat_id)
    if not critiques:
        raise RuntimeError("all seats failed — no critiques produced; check model names and API keys")

    # ---------------- Stage 2: anonymized cross-examination -------------------
    # Adversarial rule: with >1 model family on the bench, a seat never
    # adjudicates findings raised by its own family — self-confirmation across
    # seats of the same model is exactly the correlated error we are hunting.
    # Only seats that produced critiques participate in cross-exam.
    surviving_seats = [s for s in seats if s.id not in failed_seats]
    progress("crossexam", {"done": 0, "total": len(surviving_seats)})
    adjudications: list[Adjudication] = []
    rankings: list[RigorRanking] = []
    labels = [f"Reviewer {chr(65+i)}" for i in range(len(critiques))]
    seat_by_id = {s.id: s for s in seats}
    seat_vendor = {s.id: vendor_of(s.provider_mode, s.model) for s in seats}
    model_blind = len(set(seat_vendor.get(s.id, "unknown") for s in surviving_seats)) > 1

    def may_adjudicate(adj_id: str, target_id: str) -> bool:
        if adj_id == target_id:
            return False
        if model_blind and seat_vendor.get(adj_id) == seat_vendor.get(target_id):
            return False
        return True

    if is_mock:
        for i, seat in enumerate(surviving_seats):
            mine: list[Adjudication] = []
            others = [c for c in critiques if may_adjudicate(seat.id, c.seat_id)]
            for target in others:
                mine.extend(mock.adjudicate(seat, personas[seat.persona], target))
            adjudications.extend(mine)
            labeled = {labels[j]: c for j, c in enumerate(critiques)
                       if may_adjudicate(seat.id, c.seat_id)}
            rank = mock.rank(seat, labeled)
            rankings.append(rank)
            progress("seat_adjudications", {"seat_id": seat.id, "adjudications": mine, "ranking": rank})
            progress("crossexam", {"done": i + 1, "total": len(surviving_seats)})
            await asyncio.sleep(0.05 if len(surviving_seats) > 8 else 0.2)
    else:
        async def cross(seat: Seat) -> tuple[Seat, Optional[list[Adjudication]], Optional[RigorRanking]]:
            try:
                provider = make_provider(seat.provider_mode, seat.model)
                others = [(labels[j], c) for j, c in enumerate(critiques)
                          if may_adjudicate(seat.id, c.seat_id)]
                label_of = {c.seat_id: lbl for lbl, c in others}
                blob, idmap = [], {}
                for lbl, c in others:
                    for f in c.findings:
                        idmap[f.id] = c.seat_id
                        blob.append(f"[{lbl}] {f.id} | {f.severity.value} {f.kind.value} @ {f.location}: "
                                    f"{f.statement} Evidence: {f.evidence}")
                raw = await provider.complete(
                    ADJUDICATE_SYSTEM,
                    adjudicate_user_prompt(personas[seat.persona], body,
                                           "\n".join(blob) or "(no findings raised by peers)",
                                           [lbl for lbl, _ in others]))
                data = extract_json(raw)
                adjs = []
                for a in data.get("adjudications", []):
                    fid = a.get("finding_id", "")
                    if fid in idmap and a.get("verdict") in ("confirm", "reject", "cannot_verify"):
                        adjs.append(Adjudication(adjudicator_seat=seat.id, target_seat=idmap[fid],
                                                 finding_id=fid, verdict=a["verdict"],
                                                 reasoning=str(a.get("reasoning", ""))[:400]))
                rigor = {lbl: max(1, min(5, int(v))) for lbl, v in (data.get("rigor") or {}).items()
                         if isinstance(v, (int, float)) and lbl in label_of.values()}
                return seat, adjs, RigorRanking(adjudicator_seat=seat.id, scores=rigor)
            except Exception as e:
                import traceback; traceback.print_exc()
                progress("seat_error", {"seat_id": seat.id, "error": f"{type(e).__name__}: {e}"})
                return seat, None, None
        done = 0
        for coro in asyncio.as_completed([cross(s) for s in surviving_seats]):
            seat, adjs, rank = await coro
            done += 1
            if adjs is not None and rank is not None:
                adjudications.extend(adjs)
                rankings.append(rank)
                progress("seat_adjudications", {"seat_id": rank.adjudicator_seat,
                                                "adjudications": adjs, "ranking": rank})
            progress("crossexam", {"done": done, "total": len(surviving_seats)})

    # ---------------- Stage 3: synthesis ---------------------------------------
    progress("synthesis", {})
    n_total = len(seats)
    n_responded = len(critiques)
    findings, disagreements = consolidate(critiques, adjudications, n_responded, seat_vendor)
    dims = summarize_dimensions(criteria, critiques)
    checks = summarize_checks(criteria, critiques)
    charters = check_charters(critiques, criteria, personas)
    breaches = [f"{cc.seat_id} ({cc.persona} on {cc.model}) breached: " +
                ", ".join(o.id for o in cc.obligations if not o.met)
                for cc in charters if not cc.met]

    engine = PolicyEngine(policy)
    disposition, fired = engine.decide(findings, dims, checks)
    contrarian_seats = [s.id for s in surviving_seats if s.persona == "contrarian"]
    anomalies = engine.anomalies(findings, dims, critiques, contrarian_seats,
                                 n_vendors=len(set(seat_vendor.get(s.id, "unknown")
                                                   for s in surviving_seats)),
                                 charter_breaches=breaches)
    if failed_seats:
        anomalies.append(f"partial_council: {len(failed_seats)}/{n_total} seat(s) failed "
                         f"({', '.join(failed_seats)}); synthesis is based on {n_responded} responses")

    n_block = sum(1 for f in findings if f.severity == Severity.blocking)
    n_major = sum(1 for f in findings if f.severity == Severity.major)
    top = findings[0] if findings else None
    ctx = {
        "artifact_id": manifest.id, "profile": criteria.profile,
        "n_seats": n_total, "n_responded": n_responded,
        "n_failed": len(failed_seats),
        "n_dims": len(criteria.dimensions),
        "n_criteria": len(criteria.instance_criteria),
        "n_findings": len(findings), "n_blocking": n_block, "n_major": n_major,
        "top_finding": (f"[{top.severity.value}/{top.kind.value} consensus {top.consensus}"
                        f"{', cross-vendor: ' + '+'.join(sorted(set(top.vendors_raising) | set(top.vendors_confirming))) if top.cross_vendor else ', single-family'}] "
                        f"{top.statement}" if top else "none."),
        "disposition": disposition.value, "policy_id": engine.id,
        "fired_rule": fired, "anomalies": anomalies,
    }

    narrative, dissent = "", ""
    if is_mock:
        narrative = mock.chairman_narrative(ctx)
    else:
        try:
            provider = make_provider(plan.chairman_provider, plan.chairman_model)
            record = f"ARTIFACT UNDER REVIEW\n"
            record += f"  id: {manifest.id}\n  type: {manifest.type}\n"
            record += f"  purpose: {manifest.purpose}\n"
            if manifest.audience:
                record += f"  audience: {', '.join(manifest.audience)}\n"
            if manifest.acceptance_criteria:
                record += f"  acceptance criteria: {'; '.join(manifest.acceptance_criteria)}\n"
            record += f"\nCOUNCIL SUMMARY\n"
            record += "\n".join(f"  {k}: {v}" for k, v in ctx.items())
            record += "\n\nDimension scores:\n" + "\n".join(
                f"- {d.dimension}{' [GATING]' if d.gating else ''}: "
                f"mean={d.mean:.1f}, median={d.median:.1f}, spread={d.dispersion:.1f} "
                f"(scores: {d.scores})"
                for d in dims)
            record += "\n\nTop findings:\n" + "\n".join(
                f"- [{f.severity.value}/{f.kind.value} c={f.consensus}] @ {f.location}: {f.statement}"
                for f in findings[:10])
            if disagreements:
                record += "\n\nKey disagreements:\n" + "\n".join(
                    f"- {d.finding_key}: {d.confirms} confirm, {d.rejects} reject"
                    + (f" — {d.note}" if d.note else "")
                    for d in disagreements[:5])
            if failed_seats:
                record += f"\n\nNote: {len(failed_seats)} seat(s) failed and did not contribute."
            data = extract_json(await provider.complete(CHAIRMAN_SYSTEM, chairman_user_prompt(record)))
            narrative = str(data.get("narrative", ""))[:2000]
            dissent = str(data.get("dissent", ""))[:1000]
        except Exception as e:
            narrative = f"(Chairman narrative unavailable: {e}) Disposition stands as computed by policy."

    report = Report(run_id=plan.run_id, artifact_id=manifest.id, profile=criteria.profile,
                    policy_id=engine.id, charter_compliance=charters,
                    dimension_summaries=dims, findings=findings,
                    instance_check_results=checks, disagreements=disagreements,
                    disposition=disposition, fired_rule=fired, anomaly_flags=anomalies,
                    chairman_narrative=narrative, chairman_dissent=dissent)
    progress("synthesis", {"done": True})
    return critiques, adjudications, rankings, report
