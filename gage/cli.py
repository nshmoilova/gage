"""Minimal CLI: gage evaluate <file> [--policy default-v2] [--provider mock]

Runs the full pipeline headlessly and prints the report. Mock mode by default
so it works with no keys — useful for CI gating and calibration scripts.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from .evidence import build_seal, write_seal
from .frontmatter import extract_manifest, inject_manifest
from .inference import heuristic_manifest_draft
from .pipeline import build_plan, run_evaluation
from .registry import Registry
from .schemas import Manifest

ROOT = Path(__file__).resolve().parent.parent


def main():
    from .env import load_env
    load_env()
    ap = argparse.ArgumentParser(prog="gage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("evaluate")
    ev.add_argument("file")
    ev.add_argument("--policy", default="default-v2")
    ev.add_argument("--provider", default="mock")
    ev.add_argument("--model", default="")
    ev.add_argument("--auto-manifest", action="store_true",
                    help="if no manifest, inject a heuristic draft (CI mode)")
    ev.add_argument("--seal", action="store_true", help="write an evidence seal (decision recorded as 'cli-advisory')")
    args = ap.parse_args()

    registry = Registry(ROOT / "registry")
    text = Path(args.file).read_text()
    manifest, _, body, err = extract_manifest(text)
    if manifest is None:
        if not args.auto_manifest:
            print(f"No manifest in frontmatter ({err or 'absent'}). "
                  f"Re-run with --auto-manifest or add one via the UI.", file=sys.stderr)
            sys.exit(2)
        draft = heuristic_manifest_draft(body, registry)
        manifest = Manifest(**{k: v for k, v in draft.items() if k in Manifest.model_fields})
        text = inject_manifest(text, manifest)
        print(f"[gage] injected heuristic manifest (type={manifest.type}) — review it.", file=sys.stderr)

    plan = build_plan(manifest, registry, provider_mode=args.provider, model=args.model,
                      chairman_model=args.model, policy_id=args.policy)
    critiques, adjs, ranks, report = asyncio.run(
        run_evaluation(body, plan, registry, registry.policies[args.policy]))

    print(json.dumps(report.model_dump(), indent=2))
    if args.seal:
        seal = build_seal(text, plan, critiques, adjs, ranks, report,
                          {"decision": "cli-advisory", "approver": "cli", "rationale": ""})
        p = write_seal(seal, ROOT / "data" / "seals")
        print(f"[gage] seal written: {p} sha256={seal.seal_sha256}", file=sys.stderr)
    sys.exit(0 if report.disposition.value.startswith("approve") else 1)


if __name__ == "__main__":
    main()
