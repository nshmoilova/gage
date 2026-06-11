# GAGE — Generic Artifact Governance & Evaluation

**Adversarial review across a matrix of frontier models × chartered personas** — for any
text artifact: RFCs, PRDs, threat models, briefs, or types that don't exist yet. Inspired by
[karpathy/llm-council](https://github.com/karpathy/llm-council), generalized into governance
machinery: **one invariant pipeline, one finding schema, one defect taxonomy — all variation
lives in YAML registries.**

The two axes of the matrix:

1. **Models** — native adapters for Anthropic, OpenAI, Gemini (Google), and xAI (Grok), plus
   OpenRouter (every vendor through one key). Seats carry their own provider, so one council
   spans all four families. Model ids pin a vendor: `anthropic:…`, `openai:…`,
   `gemini:…`, `xai:…`.
2. **Charters, not one critic** — each persona is a charter: mandate + stance + mechanically
   checked **obligations** (the contrarian *must* object and *must* attack a gating dimension;
   auditors *must* score every dimension and check every acceptance criterion). A seat that
   fails to discharge its charter is itself reported: `charter_breach`.

Three structural guarantees make the matrix adversarial rather than merely plural:

- **Model-blind cross-exam** — with more than one family on the bench, a seat never
  adjudicates findings raised by its own model family; same-family self-confirmation is the
  correlated error being hunted, not a vote.
- **Cross-vendor consensus** — every consolidated finding records which families raised and
  confirmed it; the `adversarial-matrix-v1` policy only *rejects* on blocking findings backed
  by ≥2 families, and high-consensus single-family findings trip a `single_model_consensus`
  anomaly instead of being trusted.
- **Coverage validation** — the frozen plan carries coverage notes: a single-family bench, a
  charter argued by one family only, or a council with no contrarian are all flagged before
  anyone deliberates.

The UI takes a document as input, checks its YAML frontmatter for a `manifest:` block,
**drafts one when absent** (heuristic or LLM), lets a human edit and confirm it, injects it
into the frontmatter, then runs a five-stage council review ending in a human-authorized,
hash-sealed evidence record.

```
Intake ──► Independent critique ──► Anonymized cross-exam ──► Synthesis ──► Human authorize + Seal
(bind        (seats in isolation,      (confirm/reject/         (mechanical     (dismissing a blocking
 manifest,    one finding schema)       cannot-verify,           disposition     finding requires a
 compose                                rigor ranking)           via policy;     recorded reason;
 criteria)                                                       chairman may    sha-256 over the
                                                                 dissent, not    full deliberation)
                                                                 override)
```

## Quick start (no API keys needed)

```bash
pip install -r requirements.txt
python run.py
# open http://127.0.0.1:8000
```

Click **Load sample RFC** — it's deliberately flawed (missing rollback/security sections, an
unsupported "guarantees zero downtime" claim, a TODO) — then **Inspect frontmatter**. GAGE
detects there's no manifest, drafts one, and walks you through edit → inject → council →
report → authorization → seal.

Two visuals explain the system as you use it:

- **How it works** (masthead button) — an annotated process diagram: the manifest branch
  (present? draft → human confirms → inject) feeding the five stages, with the load-bearing
  invariants spelled out (independence before deliberation; anonymization kills
  self-preference; judgment is distributed, decision is mechanical, authority is human).
- **Chamber — the matrix IS the assignment** (appears once the manifest is bound). Rows are
  charters, columns are the **models on the bench** — added right in the chamber, one id at a
  time or comma-separated — and every cell is a seat. Pre-run, the grid is the configuration
  surface: in round-robin, click a cell to move a charter to a different model (one seat per
  row); in matrix, click cells to seat or vacate charter × model pairs (full cross product by
  default; a row can never be emptied). Every charter row also carries an explicit **model
  input** (synchronized with the grid — type any model id, even one not on the bench, or
  click the cell, same result), and the judge gets its own input on the chairman bar. A ✕ on
  a column header removes that model from the bench and reverts the seats that held it.
  During the run, **each cell reports live** — critiques stream into the run state the
  moment a model produces them, so a cell shows its findings payload (`4f · 1B 2M`) as soon
  as that seat lands, with per-seat truth (not first-N guessing) even when live models finish
  out of order. **Click any cell, at any time, to open the seat inspector**: the charter
  mandate, narrative, dimension scores, instance checks, every finding it produced with the
  peer verdicts it received, and every adjudication it gave under the model-blind rule. The
  chairman bar keeps the standing note: it synthesizes and may dissent, but the disposition
  is computed by policy and cannot be overridden.

**Mock mode** runs deterministic, content-aware heuristic reviewers (seeded by the artifact
hash), so the full pipeline works offline. It doubles as the regression harness for
seeded-defect calibration.

### Live councils

Keys are read from the **server environment only** — never from the browser:

```bash
export ANTHROPIC_API_KEY=...    # native vendor adapters — set any subset
export OPENAI_API_KEY=...
export GEMINI_API_KEY=...       # or GOOGLE_API_KEY
export XAI_API_KEY=...          # or GROK_API_KEY
export OPENROUTER_API_KEY=...   # alternative: every vendor through one key
python run.py
```

Keys can come from the environment **or a `.env` file** (repo root or working directory —
copy `.env.example` to `.env`). Real environment variables always win; `.env` fills gaps and
never overrides. At startup the server logs exactly which key *names* it loaded and from
where, and `GET /api/registry` attributes each provider's key to its source (`environment` or
`.env`) — so "why is only mock available?" is answerable from the badge or the log, never a
mystery. Key values are never logged or sent to the browser.

The server validates **every provider the matrix touches** before convening and lists exactly
which keys are missing. The mock engine accepts vendor-prefixed labels, so the full
adversarial matrix — model-blind cross-exam, cross-vendor consensus, charter enforcement —
is demonstrable completely offline.

Then pick the provider in the UI and **assign each seat its model in the chamber**. The bench
arrives pre-seeded — one current frontier model per provider you have configured — so every
seat (and the judge) starts with a default assignment; change any of it by editing the bench,
typing on a seat's row, or clicking cells, so judgment diversity comes from both persona *and*
model, as in llm-council. The API accepts the explicit chart too: `POST /api/evaluate` with
`seats: [{persona, model}, …]` and `chairman_model` for the judge; a seat with an empty model
falls back to round-robin over the `models` list. Model names are configuration, not code —
the default bench is `GAGE_DEFAULT_MODELS` (comma-separated refs) and for current Anthropic
models and API details see https://docs.claude.com/en/api/overview.

### Internal deployments (no public model APIs)

Every vendor adapter's base URL is configuration, so GAGE runs entirely against
internally-hosted models — no code changes:

```bash
# point any vendor's protocol at an internal gateway (vLLM, LiteLLM, enterprise proxy, …)
export OPENAI_BASE_URL=https://llm-gateway.corp.internal        # OpenAI-compatible gateways
export ANTHROPIC_BASE_URL=https://claude-proxy.corp.internal    # Anthropic Messages protocol
# name the models your gateway serves — these pre-assign every seat in the UI
export GAGE_DEFAULT_MODELS="openai:corp-llama-70b, openai:corp-qwen-72b, anthropic:claude-opus-4-8"
python run.py
```

A provider with a base URL override is usable **without an API key** (internal gateways often
authenticate at the network layer); set the key too only if your gateway expects a bearer
token. Most internal gateways speak the OpenAI chat-completions protocol, so `OPENAI_BASE_URL`
plus `openai:<internal-model-name>` refs covers them — and because every seat's model ref pins
its provider, one council can mix internal and vendor-hosted models. The masthead badge marks
providers running through a custom endpoint (`internal endpoint`), and `GET /api/registry`
reports the configured endpoint per provider.

### CLI (CI gating)

```bash
python -m gage.cli evaluate path/to/doc.md --auto-manifest          # exit 0 only on approve*
python -m gage.cli evaluate doc.md --policy strict-v1 --seal        # write an evidence seal
```

## The manifest convention

A document is evaluable when its frontmatter carries a `manifest:` key. Other frontmatter
keys are preserved on injection:

```yaml
---
manifest:
  id: rfc-2026-041
  type: rfc                      # binds a genre profile; "unknown" is legal
  purpose: >
    Decide and document the migration of tenant routing from NGINX to Istio.
  audience: [platform-eng, security-review-board]
  acceptance_criteria:           # instance-level musts — every seat checks each one
    - Names a rollback path executable in under 30 minutes
  constraints: [sox]             # absent from the body ⇒ policy_conflict finding
---
```

`POST /api/inspect` reports whether a manifest exists; when absent it returns an **inferred
draft** (type via profile signal keywords, purpose from the document's own opening, criteria
from the genre's structural expectations). The draft is always confirmed by a human in the UI
before `POST /api/manifest/apply` injects it.

## How the code maps to the framework

| Framework concept (see the two proposal docs) | Where it lives |
|---|---|
| Universal dimensions (7) + defect taxonomy (8 kinds) | `gage/taxonomy.py` |
| One finding/critique/report schema for every type | `gage/schemas.py` |
| Manifest detect / inject (frontmatter) | `gage/frontmatter.py` |
| Three-layer criteria stack + profile inheritance (`extends`) | `gage/registry.py` + `registry/profiles/*.yaml` |
| Personas (4 core + specialists by profile reference) | `registry/personas/*.yaml` |
| Mechanical disposition policy + cross-vendor rules + anomaly flags | `gage/policy.py` + `registry/policies/*.yaml` |
| Matrix layout, model-blind cross-exam, vendor consensus, charter checks | `gage/pipeline.py` |
| Native Anthropic/OpenAI/Gemini/xAI/OpenRouter adapters + vendor attribution | `gage/providers.py` |
| Rubricator front half (manifest inference, human-confirmed) | `gage/inference.py` |
| Invariant 5-stage pipeline, consensus math, dedupe | `gage/pipeline.py` |
| Mock council = offline calibration harness | `gage/mock.py` |
| OpenRouter / Anthropic providers, strict-JSON contracts | `gage/providers.py` |
| Evidence seal (canonical JSON, sha-256, verify) | `gage/evidence.py` |
| API + Stage-4 authorization rules | `gage/server.py` |
| Single-file UI (pipeline rail, chamber, process diagram, report, authorize, seal) | `ui/index.html` |

Load-bearing invariants enforced in code, not prose:

- **No citation, no finding** — findings without a `location` are dropped at parse time.
- **Disposition is mechanical** — `PolicyEngine` computes it from declarative rules; the
  Chairman's narrative explicitly cannot override (mock and LLM prompts both say so).
- **Consensus accrues to defects, not reviewers** — dedupe key is `kind|dimension|location`,
  consensus = (raisers + confirmers) / seats.
- **Anomaly flags watch the council itself** — `low_dispersion_unanimity` (rubber-stamp
  signal) and `silent_contrarian` (failed control).
- **Acceptance over blocking findings requires per-finding dismissal reasons** — the server
  returns 422 otherwise; reasons are recorded verbatim in the seal.
- **The seal hash covers everything** including the human decision; `verify_seal()` detects
  any later edit.

## Adding a new artifact type (no code)

Create `registry/profiles/runbook.yaml`:

```yaml
name: runbook
version: "1"
extends: _default
signals: [runbook, procedure, on-call, escalation]
dimensions:
  - name: step_executability
    question: Can each step be executed exactly as written, by the declared audience, under stress?
    anchors: {1: Steps assume tribal knowledge., 3: Mostly executable; some steps hand-wave., 5: Every step is copy-paste executable with stated preconditions.}
gating_dimensions: [step_executability]
structure_expectations: [Preconditions, Steps, Verification, Escalation]
personas: [operability-reviewer]
```

Restart; the type appears in the manifest form, its dimensions join the universal seven, its
structure expectations feed both the completeness auditor and manifest inference.

## Tests

```bash
python3 -m gage.cli evaluate examples/sample-rfc-no-manifest.md --auto-manifest   # pipeline, headless
npm install jsdom && node tests/test-ui.js                                        # UI boots in a real DOM
```

The DOM test executes the page like a browser would — `init()`, registry application, chamber
render, seat-override statefulness — and asserts the error funnel: every failure path logs to
the console with its real cause (registry fetch errors and UI errors are separate channels,
so a UI bug can never masquerade as "registry unavailable" again).

## Repo layout

```
gage/                 framework package (pipeline, schemas, policy, providers, mock, evidence)
registry/profiles/    genre profiles (decision-document ← rfc, prd, threat-model, product-brief, _default)
registry/personas/    completeness-auditor, consistency-checker, audience-advocate, contrarian, + specialists
registry/policies/    default-v2, strict-v1 (declarative disposition rules + anomaly config)
ui/index.html         the whole UI — vanilla JS, no build step
examples/             sample-rfc-no-manifest.md (seeded with real defects)
data/seals/           content-addressed evidence records
run.py                uvicorn launcher (127.0.0.1:8000)
```

## Honest limitations

- Finding dedupe uses normalized-location heuristics (deterministic and auditable; a
  semantic-dedupe Chairman pass is the obvious LLM-mode upgrade).
- Mock reviewers are shallow by design — they exist to exercise the machinery and calibrate
  it, not to replace model judgment.
- Run state is in-memory (`RUNS` dict); seals persist to disk. Production would back runs
  with a store and sign seals (the `Guardian`-style receipt is a natural fit).
- The Rubricator here covers manifest inference; auto-drafting *profiles* for unknown types
  (with council review of the rubric itself) is specified in the framework doc as the next
  increment.
