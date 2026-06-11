"""Disposition policy engine.

Judgment lives in findings; disposition is mechanical. Rules are structured
conditions (never eval'd code), applied in order; the first match wins. The
Chairman may dissent in prose but cannot change the computed result.
"""

from __future__ import annotations

import statistics

from .schemas import ConsolidatedFinding, DimensionSummary, InstanceCheckResult, Critique
from .taxonomy import Disposition, Severity


class PolicyEngine:
    def __init__(self, policy: dict):
        self.policy = policy
        self.id = policy["id"]

    # -- condition evaluators --------------------------------------------------

    def _instance_check_failed(self, rule, findings, dims, checks, **_):
        return any(c.failed for c in checks)

    def _blocking_finding(self, rule, findings, dims, checks, **_):
        mc = float(rule.get("min_consensus", 0.0))
        xv = bool(rule.get("require_cross_vendor", False))
        return any(f.severity == Severity.blocking and f.consensus >= mc
                   and (f.cross_vendor or not xv) for f in findings)

    def _gating_dimension_low(self, rule, findings, dims, checks, **_):
        mx = float(rule.get("max_median", 2))
        return any(d.gating and d.median <= mx for d in dims)

    def _severity_count(self, rule, findings, dims, checks, **_):
        sev = Severity(rule["severity"])
        mc = float(rule.get("min_consensus", 0.0))
        gt = int(rule.get("greater_than", 0))
        xv = bool(rule.get("require_cross_vendor", False))
        n = sum(1 for f in findings if f.severity == sev and f.consensus >= mc
                and (f.cross_vendor or not xv))
        return n > gt

    def _finding_kind_present(self, rule, findings, dims, checks, **_):
        mc = float(rule.get("min_consensus", 0.0))
        return any(f.kind.value == rule["kind"] and f.consensus >= mc for f in findings)

    CONDITIONS = {
        "instance_check_failed": _instance_check_failed,
        "blocking_finding": _blocking_finding,
        "gating_dimension_low": _gating_dimension_low,
        "severity_count": _severity_count,
        "finding_kind_present": _finding_kind_present,
    }

    # -- main entry --------------------------------------------------------------

    def decide(
        self,
        findings: list[ConsolidatedFinding],
        dims: list[DimensionSummary],
        checks: list[InstanceCheckResult],
    ) -> tuple[Disposition, str]:
        for rule in self.policy.get("rules", []):
            cond = self.CONDITIONS.get(rule.get("when"))
            if cond is None:
                continue  # unknown condition types are inert, never permissive
            if cond(self, rule, findings, dims, checks):
                return Disposition(rule["then"]), rule.get("label", rule["when"])
        return Disposition(self.policy.get("default", "approve")), "default"

    # -- anomaly detection: the council reviewing the council ----------------------

    def anomalies(
        self,
        findings: list[ConsolidatedFinding],
        dims: list[DimensionSummary],
        critiques: list[Critique],
        contrarian_seats: list[str],
        n_vendors: int = 1,
        charter_breaches: list[str] | None = None,
    ) -> list[str]:
        flags: list[str] = []
        cfg = self.policy.get("anomalies", {}) or {}

        if "single_model_consensus" in cfg and n_vendors > 1:
            mc = float(cfg["single_model_consensus"].get("min_consensus", 0.6))
            for f in findings:
                if f.consensus >= mc and not f.cross_vendor:
                    flags.append(
                        f"single_model_consensus: '{f.statement[:80]}' reached consensus {f.consensus} "
                        f"from one model family ({', '.join(f.vendors_raising) or '?'}) despite "
                        f"{n_vendors} families on the bench — possible family-correlated error; "
                        f"verify with a human before acting on it")

        if "charter_breach" in cfg:
            for b in (charter_breaches or []):
                flags.append(f"charter_breach: {b}")

        if "low_dispersion_unanimity" in cfg:
            max_disp = float(cfg["low_dispersion_unanimity"].get("max_mean_dispersion", 0.3))
            dispersions = [d.dispersion for d in dims if d.scores]
            mean_disp = statistics.fmean(dispersions) if dispersions else 0.0
            all_high = all(d.median >= 4 for d in dims) if dims else False
            no_blocking = not any(f.severity == Severity.blocking for f in findings)
            if all_high and no_blocking and mean_disp <= max_disp:
                flags.append(
                    f"low_dispersion_unanimity: every dimension median >= 4 with mean dispersion "
                    f"{mean_disp:.2f} <= {max_disp} and zero blocking findings — possible rubber-stamp; "
                    f"treat as a signal for human scrutiny, not a green light"
                )

        if "silent_contrarian" in cfg and contrarian_seats:
            for c in critiques:
                if c.seat_id in contrarian_seats and len(c.findings) == 0:
                    flags.append(
                        f"silent_contrarian: seat {c.seat_id} ({c.persona}) raised zero findings — "
                        f"a contrarian who raises nothing is a failed control"
                    )
        return flags
