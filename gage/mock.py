"""Mock council engine.

Deterministic, content-aware reviewers that run the full pipeline with no API
keys. This is not a toy: it is the framework's offline test harness and the
substrate for seeded-defect calibration. Each mock persona implements real
(if shallow) heuristics over the text, seeded by the artifact hash so runs
are reproducible.

Verification model for Stage 2: a mock adjudicator CONFIRMS a finding when it
can independently re-derive it from the text with its own heuristics, REJECTS
when its re-check contradicts it, and otherwise answers CANNOT_VERIFY — which
mirrors how cross-examination should behave with real models.
"""

from __future__ import annotations

import hashlib
import random
import re

from .frontmatter import headings
from .registry import Persona, Profile
from .schemas import (Adjudication, Critique, CriteriaStack, DimensionScore,
                      Finding, InstanceCheck, Manifest, RigorRanking, Seat)
from .taxonomy import DefectKind, Severity

_STOP = set("the a an and or of to in for with on by is are was were be this that it as at from".split())
_ASSERTIVE = re.compile(r"\b(will always|guarantees?|never fail|cannot fail|zero risk|fully secure|100%)\b", re.I)
_TODO = re.compile(r"\b(TODO|TBD|FIXME|\?\?\?)\b")
_REF = re.compile(r"(\[\d+\]|\bhttps?://|\bsee\s+§|\bper\s+[A-Z]|\(ref|\bRFC-\d|\bPRD-\d)", re.I)
_NUMBERED_CLAIM = re.compile(r"\b(\d+(?:\.\d+)?)\s*(%|ms|seconds?|minutes?|hours|days|x)\b", re.I)


def _content_words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", s.lower()) if w not in _STOP and len(w) > 2}


def _sentences(body: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if len(s.strip()) > 20]


def _nearest_heading(body: str, idx: int) -> str:
    best = "document start"
    for m in re.finditer(r"^#{1,6}\s+(.+)$", body, re.M):
        if m.start() <= idx:
            best = m.group(1).strip()
        else:
            break
    return f"§ {best}"


class MockEngine:
    def __init__(self, body: str, manifest: Manifest, criteria: CriteriaStack, profile: Profile):
        self.body = body
        self.manifest = manifest
        self.criteria = criteria
        self.profile = profile
        self.heads = [h.lower() for h in headings(body)]
        self.head_text = " ".join(self.heads)
        seed = int(hashlib.sha256(body.encode()).hexdigest()[:8], 16)
        self.rng = random.Random(seed)
        self.words = len(body.split())

    # ---------------------------------------------------------------- helpers

    def _missing_sections(self) -> list[str]:
        out = []
        for expect in self.profile.structure_expectations:
            ew = _content_words(expect)
            if not ew:
                continue
            if not any(ew & _content_words(h) for h in self.heads):
                # allow body-level mention to soften, but heading absence still flags
                out.append(expect)
        return out

    def _unsupported(self) -> list[tuple[str, str]]:
        out = []
        for s in _sentences(self.body):
            if _ASSERTIVE.search(s) and not _REF.search(s):
                idx = self.body.find(s)
                out.append((s[:160], _nearest_heading(self.body, max(idx, 0))))
        # confident numeric claims with no reference nearby
        for m in _NUMBERED_CLAIM.finditer(self.body):
            window = self.body[max(0, m.start() - 120): m.end() + 120]
            if not _REF.search(window) and "assum" not in window.lower():
                out.append((self.body[max(0, m.start() - 60): m.end() + 40].strip().replace("\n", " "),
                            _nearest_heading(self.body, m.start())))
        # dedupe by location+snippet head
        seen, uniq = set(), []
        for snip, loc in out:
            k = (loc, snip[:40])
            if k not in seen:
                seen.add(k)
                uniq.append((snip, loc))
        return uniq[:6]

    def _ambiguities(self) -> list[tuple[str, str]]:
        out = []
        for m in _TODO.finditer(self.body):
            out.append((self.body[max(0, m.start() - 50): m.end() + 50].strip().replace("\n", " "),
                        _nearest_heading(self.body, m.start())))
        # undefined acronyms: ALLCAPS token appearing without a parenthetical definition
        seen_def = set(re.findall(r"\(([A-Z]{2,8})\)", self.body))
        for token in sorted(set(re.findall(r"\b[A-Z]{3,8}\b", self.body))):
            if token in seen_def or token in {"GAGE", "TODO", "TBD", "FIXME", "API", "URL", "JSON", "YAML", "HTTP", "HTTPS", "DNS", "RFC", "PRD", "SLO", "SLA", "AWS", "GCP", "SRE", "NGINX", "REST", "GRPC", "SDK", "CLI"}:
                continue
            if self.body.count(token) >= 2:
                idx = self.body.find(token)
                out.append((f"Acronym '{token}' is used {self.body.count(token)}x but never defined for the declared audience.",
                            _nearest_heading(self.body, idx)))
        return out[:5]

    _META = set("addresses address explicitly explicit names name states state includes include "
                "contains contain section must should clearly".split())

    def _criterion_pass(self, criterion: str) -> tuple[bool, str]:
        # quoted phrases are the strongest signal: 'Context' -> the word itself
        quoted = re.findall(r"['\u2018\u2019\"\u201c\u201d]([^'\u2018\u2019\"\u201c\u201d]{2,60})['\u2018\u2019\"\u201c\u201d]", criterion)
        haystack = (self.body + " " + self.head_text).lower()
        if quoted:
            hits = [q for q in quoted if all(w in haystack for w in _content_words(q))]
            if len(hits) == len(quoted):
                idx = self.body.lower().find(next(iter(_content_words(hits[0])), ""))
                return True, _nearest_heading(self.body, max(idx, 0))
            missing = [q for q in quoted if q not in hits]
            return False, f"quoted term(s) {missing} not found in body or headings"
        cw = _content_words(criterion) - self._META
        if not cw:
            return True, "criterion has no checkable terms"
        bw = _content_words(self.body) | _content_words(self.head_text)
        overlap = len(cw & bw) / len(cw)
        if overlap >= 0.5:
            hit = next(iter(cw & bw), "")
            idx = self.body.lower().find(hit)
            return True, _nearest_heading(self.body, max(idx, 0))
        return False, f"only {overlap:.0%} of criterion terms appear anywhere in the body"

    # ------------------------------------------------------------- Stage 1

    def critique(self, seat: Seat, persona: Persona) -> Critique:
        rng = random.Random(f"{seat.id}|{self.rng.random()}")
        findings: list[Finding] = []
        fid = 0

        def add(dim, sev, loc, kind, stmt, ev="", rec=""):
            nonlocal fid
            fid += 1
            findings.append(Finding(id=f"{seat.id}-f{fid}", dimension=dim, severity=sev,
                                    location=loc, kind=kind, statement=stmt,
                                    evidence=ev, recommendation=rec))

        missing = self._missing_sections()
        unsupported = self._unsupported()
        ambigs = self._ambiguities()
        gates = self.criteria.gating_names()

        if persona.name == "completeness-auditor":
            for sec in missing:
                gate_hit = any(g in ("completeness", "rollback_plan", "threat_mitigation_mapping") for g in gates)
                add("completeness",
                    Severity.blocking if (gate_hit and len(missing) > 1) else Severity.major,
                    "document structure", DefectKind.omission,
                    f"Genre '{self.profile.name}' expects a '{sec}' section; none found.",
                    f"No heading overlaps with '{sec}'. Purpose declares: {self.manifest.purpose[:120]}",
                    f"Add a '{sec}' section or state explicitly why it does not apply.")
            for crit in self.criteria.instance_criteria:
                ok, why = self._criterion_pass(crit)
                if not ok:
                    add("purpose_fitness", Severity.blocking, "whole document", DefectKind.omission,
                        f"Acceptance criterion appears unaddressed: '{crit[:100]}'", why,
                        "Address the criterion explicitly and cite where.")

        elif persona.name == "consistency-checker":
            for snip, loc in unsupported:
                add("groundedness", Severity.major, loc, DefectKind.unsupported_claim,
                    "Confident claim with no evidence, reference, or assumption marker.",
                    f"\u201c{snip}\u201d", "Cite a source, link a context_ref, or mark as assumption.")
            # crude contradiction probe: 'no downtime' vs 'maintenance window' style pairs
            low = self.body.lower()
            pairs = [("zero downtime", "maintenance window"), ("no new dependencies", "introduce"),
                     ("backwards compatible", "breaking change")]
            for a, b in pairs:
                if a in low and b in low:
                    add("internal_consistency", Severity.major, "cross-section", DefectKind.contradiction,
                        f"Document asserts '{a}' yet elsewhere discusses '{b}'.",
                        "Both phrases occur; a careful reader cannot hold both.",
                        "Reconcile the two statements explicitly.")

        elif persona.name == "audience-advocate":
            for snip, loc in ambigs:
                add("audience_clarity", Severity.minor, loc, DefectKind.ambiguity,
                    "Audience cannot resolve this to a single meaning unaided.", snip,
                    "Define on first use or replace with concrete language.")
            if self.manifest.audience and self.words > 1500:
                add("audience_clarity", Severity.suggestion, "whole document", DefectKind.ambiguity,
                    f"At {self.words} words with audience {self.manifest.audience}, an executive summary would aid first-pass comprehension.",
                    "", "Add a 5-sentence summary up top.")

        elif persona.name == "contrarian":
            if missing:
                # prefer a missing section that maps onto a gating dimension: that
                # silence IS the gate attack the charter demands
                target, gate_dim = missing[0], None
                for sec in missing:
                    sw = _content_words(sec)
                    for g in gates:
                        if sw & set(g.split("_")):
                            target, gate_dim = sec, g
                            break
                    if gate_dim:
                        break
                add(gate_dim or "risk_transparency", Severity.blocking, "document structure",
                    DefectKind.omission,
                    f"The document is silent on '{target}' — the silence reads as unexamined risk, not confidence."
                    + (f" This silence fails the gating dimension '{gate_dim}'." if gate_dim else ""),
                    "Required by genre structure; absent from headings.",
                    "Either the section exists in someone's head (write it down) or it does not (that is the finding).")
            if unsupported:
                snip, loc = unsupported[0]
                add("risk_transparency", Severity.major, loc, DefectKind.infeasibility,
                    "Strongest stated commitment is not demonstrably achievable as written.",
                    f"\u201c{snip}\u201d", "Downgrade to a measured commitment or attach the evidence.")
            if self.words < 250:
                add("purpose_fitness", Severity.major, "whole document", DefectKind.infeasibility,
                    f"At {self.words} words the artifact cannot plausibly discharge its declared purpose.",
                    f"Purpose: {self.manifest.purpose[:120]}", "Expand or narrow the declared purpose.")
            for c in self.manifest.constraints:
                if c.lower() not in self.body.lower():
                    add("risk_transparency", Severity.major, "whole document", DefectKind.policy_conflict,
                        f"Manifest declares constraint '{c}' but the body never addresses it.",
                        "Constraint absent from text.", f"Add a section addressing '{c}' compliance.")
            # charter obligation: attack the gates — challenge any gating dimension
            # whose subject matter the text never even mentions
            low_body = self.body.lower()
            covered = {f.dimension for f in findings}
            for gate in gates:
                if gate in covered:
                    continue
                tokens = [t for t in gate.split("_") if len(t) > 3]
                if tokens and tokens[0] not in low_body:
                    add(gate, Severity.major, "whole document", DefectKind.omission,
                        f"Gating dimension '{gate}' is unargued — the text never engages its subject at all.",
                        f"None of {tokens} appear anywhere in the body.",
                        f"Address '{gate}' explicitly; a gate cannot pass on silence.")
                    break

        elif persona.name == "security-specialist":
            low = self.body.lower()
            if any(k in low for k in ("api", "endpoint", "service", "data", "user")):
                for need, kind_msg in [("auth", "authentication/authorization"), ("encrypt", "encryption of data"), ("secret", "secret handling")]:
                    if need not in low:
                        add("risk_transparency", Severity.major, "whole document", DefectKind.omission,
                            f"No discussion of {kind_msg} despite the artifact touching systems/data.",
                            "Term family absent from body.", f"State the {kind_msg} posture or mark explicitly out of scope.")

        elif persona.name == "operability-reviewer":
            low = self.body.lower()
            for need, label in [("monitor", "monitoring"), ("alert", "alerting"), ("rollback", "rollback executability")]:
                if need not in low:
                    add("commitment_verifiability", Severity.major, "whole document", DefectKind.omission,
                        f"No {label} discussion — the on-call engineer inherits this blind.",
                        "Term family absent from body.", f"Specify {label} before this ships.")

        # ----- scores: heuristic base, persona-weighted, seeded jitter -----
        scores: list[DimensionScore] = []
        sec_total = max(len(self.profile.structure_expectations), 1)
        completeness_base = 5 - round(4 * len(missing) / sec_total)
        grounded_base = max(1, 5 - len(unsupported))
        clarity_base = max(2, 5 - len(ambigs))
        for d in self.criteria.dimensions:
            base = {"completeness": completeness_base, "groundedness": grounded_base,
                    "audience_clarity": clarity_base}.get(d.name, 4)
            if d.name in [f.dimension for f in findings]:
                base = min(base, 3 if persona.name != "contrarian" else 2)
            if persona.name == "contrarian":
                base -= 1
            jitter = rng.choice([-1, 0, 0, 1])
            score = max(1, min(5, base + jitter))
            anchor = d.anchors.get(score) or d.anchors.get(3, "")
            scores.append(DimensionScore(dimension=d.name, score=score, anchor_cited=anchor))

        checks = [InstanceCheck(criterion=c, passed=ok, citation=why)
                  for c in self.criteria.instance_criteria
                  for ok, why in [self._criterion_pass(c)]]

        narrative = (f"{persona.title} review ({len(findings)} findings). Mandate: {persona.mandate.strip()[:140]}…"
                     if findings else
                     f"{persona.title} review: nothing rose to a finding under this persona's mandate.")
        return Critique(seat_id=seat.id, persona=persona.name, model=seat.model,
                        scores=scores, findings=findings, instance_checks=checks, narrative=narrative)

    # ------------------------------------------------------------- Stage 2

    def adjudicate(self, adjudicator: Seat, persona: Persona, target: Critique) -> list[Adjudication]:
        out = []
        missing = {m.lower() for m in self._missing_sections()}
        unsupported_locs = {loc for _, loc in self._unsupported()}
        ambig_locs = {loc for _, loc in self._ambiguities()}
        rng = random.Random(f"adj|{adjudicator.id}|{target.seat_id}|{self.rng.random()}")
        for f in target.findings:
            verdict, why = "cannot_verify", "Outside this seat's independent verification reach."
            if f.kind == DefectKind.omission and any(m in f.statement.lower() for m in missing):
                verdict = "confirm"
                why = "Independent structure scan confirms the section is absent."
            elif f.kind == DefectKind.omission and "section" in f.statement.lower():
                verdict = "reject"
                why = "Independent structure scan finds the section present — finding does not reproduce."
            elif f.kind == DefectKind.unsupported_claim:
                verdict = "confirm" if f.location in unsupported_locs else "cannot_verify"
                why = "Re-derived the same unreferenced claim at that location." if verdict == "confirm" else why
            elif f.kind == DefectKind.ambiguity:
                verdict = "confirm" if f.location in ambig_locs or "acronym" in f.evidence.lower() else "cannot_verify"
                why = "Same ambiguity reproduces on independent read." if verdict == "confirm" else why
            elif f.kind == DefectKind.policy_conflict:
                c = next((c for c in self.manifest.constraints if c.lower() in f.statement.lower()), None)
                if c is not None:
                    present = c.lower() in self.body.lower()
                    verdict = "reject" if present else "confirm"
                    why = "Constraint term is addressed in body." if present else "Constraint term verified absent."
            elif f.severity == Severity.suggestion and rng.random() < 0.4:
                verdict, why = "reject", "Stylistic preference, not a defect against the criteria stack."
            out.append(Adjudication(adjudicator_seat=adjudicator.id, target_seat=target.seat_id,
                                    finding_id=f.id, verdict=verdict, reasoning=why))
        return out

    def rank(self, adjudicator: Seat, labeled: dict[str, Critique]) -> RigorRanking:
        scores = {}
        for label, c in labeled.items():
            cited = sum(1 for f in c.findings if f.evidence)
            scores[label] = max(1, min(5, 2 + min(len(c.findings), 2) + (1 if cited >= len(c.findings) and c.findings else 0)))
        return RigorRanking(adjudicator_seat=adjudicator.id, scores=scores)

    # ------------------------------------------------------------- Chairman narrative

    def chairman_narrative(self, report_ctx: dict) -> str:
        d = report_ctx
        return (
            f"Council of {d['n_seats']} seats reviewed '{d['artifact_id']}' against profile "
            f"'{d['profile']}' ({d['n_dims']} dimensions, {d['n_criteria']} instance criteria). "
            f"{d['n_findings']} consolidated findings: {d['n_blocking']} blocking, {d['n_major']} major. "
            f"Highest-consensus finding: {d['top_finding']} "
            f"Disposition '{d['disposition']}' computed mechanically by policy '{d['policy_id']}' "
            f"(rule: {d['fired_rule']}); this narrative summarizes but cannot override it. "
            f"{'Anomaly flags raised: ' + '; '.join(d['anomalies']) + '. ' if d['anomalies'] else ''}"
            f"Human authorization required before any disposition takes effect."
        )
