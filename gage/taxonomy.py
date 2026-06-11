"""GAGE universal taxonomy.

The invariant core of the framework: seven universal quality dimensions that
apply to every text artifact, eight defect kinds that exhaust what can go
wrong in text, four severities, and four dispositions. Genre profiles ADD
dimensions; nothing ever removes these.
"""

from enum import Enum


class Severity(str, Enum):
    blocking = "blocking"
    major = "major"
    minor = "minor"
    suggestion = "suggestion"


SEVERITY_ORDER = {
    Severity.blocking: 3,
    Severity.major: 2,
    Severity.minor: 1,
    Severity.suggestion: 0,
}


class DefectKind(str, Enum):
    omission = "omission"
    contradiction = "contradiction"
    unsupported_claim = "unsupported_claim"
    ambiguity = "ambiguity"
    scope_violation = "scope_violation"
    stale_reference = "stale_reference"
    infeasibility = "infeasibility"
    policy_conflict = "policy_conflict"


DEFECT_KIND_DESCRIPTIONS = {
    DefectKind.omission: "Something the purpose, genre, or instance criteria demand is absent.",
    DefectKind.contradiction: "Two statements in the artifact cannot both be true.",
    DefectKind.unsupported_claim: "An assertion presented as fact with no evidence, reference, or marked assumption.",
    DefectKind.ambiguity: "A statement the declared audience cannot resolve to a single meaning.",
    DefectKind.scope_violation: "Content exceeds or contradicts the declared purpose/scope of the artifact.",
    DefectKind.stale_reference: "A reference to a document, system, or fact that is outdated or unresolvable.",
    DefectKind.infeasibility: "A commitment the artifact makes that cannot plausibly be met as written.",
    DefectKind.policy_conflict: "Content that violates a constraint the manifest declares the artifact must satisfy.",
}


class Disposition(str, Enum):
    approve = "approve"
    approve_with_conditions = "approve_with_conditions"
    revise = "revise"
    reject = "reject"


# ---------------------------------------------------------------------------
# Layer 1: universal dimensions. Anchors are deliberately concrete so that a
# score is a claim about the text, not a vibe. Scored 1-5.
# ---------------------------------------------------------------------------

UNIVERSAL_DIMENSIONS = [
    {
        "name": "purpose_fitness",
        "question": "Does the body accomplish what the manifest declares as its purpose?",
        "anchors": {
            1: "The document, read alone, does not address the declared purpose.",
            3: "The purpose is addressed but key parts of it are handled superficially.",
            5: "A reader can confirm every element of the declared purpose is delivered, with nothing extraneous.",
        },
    },
    {
        "name": "completeness",
        "question": "Is everything the purpose and genre demand actually present?",
        "anchors": {
            1: "Multiple sections or topics the purpose obviously requires are missing entirely.",
            3: "All major topics appear, but at least one is a placeholder or materially thin.",
            5: "No reviewer persona can name a required topic that is absent or thin.",
        },
    },
    {
        "name": "internal_consistency",
        "question": "Do any two statements in the artifact contradict each other?",
        "anchors": {
            1: "Load-bearing statements contradict each other (a promise one section forecloses in another).",
            3: "Minor tensions in terminology or numbers that a careful reader can reconcile.",
            5: "Terminology, numbers, and commitments are consistent end to end.",
        },
    },
    {
        "name": "groundedness",
        "question": "Are claims supported by evidence, references, or explicitly marked as assumptions?",
        "anchors": {
            1: "Load-bearing claims are asserted with confidence and no provenance.",
            3: "Most claims are supported; a few assertions float without source or assumption marker.",
            5: "Every non-obvious claim cites evidence, a context_ref, or is explicitly flagged as an assumption.",
        },
    },
    {
        "name": "audience_clarity",
        "question": "Can the declared audience act on this without the author present?",
        "anchors": {
            1: "The declared audience would need the author in the room to use this document.",
            3: "Usable, but with undefined terms or missing context that forces guesswork.",
            5: "A member of the declared audience can act on it unaided; terms are defined on first use.",
        },
    },
    {
        "name": "commitment_verifiability",
        "question": "Can the promises and metrics in this artifact be checked later?",
        "anchors": {
            1: "Success criteria and commitments are unfalsifiable as written.",
            3: "Some commitments are measurable; others are aspirational language.",
            5: "Every commitment has a check that a third party could run after the fact.",
        },
    },
    {
        "name": "risk_transparency",
        "question": "Are limits, assumptions, and uncertainties surfaced rather than buried?",
        "anchors": {
            1: "Material assumptions and dependencies are silent; the document reads riskier than it admits.",
            3: "Risks are mentioned but not connected to consequences or owners.",
            5: "Assumptions, open questions, and residual risks are explicit, owned, and proportionate.",
        },
    },
]

UNIVERSAL_DIMENSION_NAMES = [d["name"] for d in UNIVERSAL_DIMENSIONS]
