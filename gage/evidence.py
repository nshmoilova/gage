"""Stage 4 evidence: the immutable seal.

The complete deliberation — manifest, plan, every critique, every
adjudication, the report, and the human decision — is serialized
canonically, hashed, and written to disk. The seal hash covers everything,
so any later edit to the record is detectable. In production this maps
naturally onto an annotated git tag or a signed receipt; here it is a
content-addressed JSON file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from .schemas import Adjudication, Critique, EvaluationPlan, Report, RigorRanking, Seal


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_seal(artifact_text: str, plan: EvaluationPlan, critiques: list[Critique],
               adjudications: list[Adjudication], rankings: list[RigorRanking],
               report: Report, human_decision: Optional[dict] = None) -> Seal:
    seal = Seal(run_id=plan.run_id, artifact_sha256=sha256_text(artifact_text),
                plan=plan, critiques=critiques, adjudications=adjudications,
                rankings=rankings, report=report, human_decision=human_decision)
    payload = seal.model_dump(exclude={"seal_sha256"})
    seal.seal_sha256 = sha256_text(canonical(payload))
    return seal


def write_seal(seal: Seal, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{seal.run_id}.json"
    path.write_text(json.dumps(seal.model_dump(), indent=2, ensure_ascii=False))
    return path


def verify_seal(path: Path) -> bool:
    data = json.loads(Path(path).read_text())
    claimed = data.pop("seal_sha256", "")
    return sha256_text(canonical(data)) == claimed
