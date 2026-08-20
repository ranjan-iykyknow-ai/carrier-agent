# Pending Decisions

Updated 2026-08-20 after the review-feedback build session (PRs #8–#11).

## Resolved

1. **GitHub Actions secrets** — resolved: the `live-contract` CI job now runs and
   passes on every push, so the provider keys are present as repo secrets.
2. **createsuperuser** — not needed. `make manage ARGS="ensure_broker"` idempotently
   creates/promotes the demo broker login with staff + superuser access (the seeded
   account already had it). Same login works for the app and `/admin`.
3. **Langfuse** — tracing is live: pipeline, drafting, and assistant operations
   trace to Langfuse Cloud with deterministic per-execution trace ids; the
   extraction prompts are published there (`production` label, resolved at runtime
   with the bundled fallback as emergency). `make manage ARGS="publish_prompts"`
   re-aligns after prompt edits.

## Still open (answer when convenient)

1. **PR policy** — I continued the overnight default: open PR → wait for CI green
   (including live-contract) → merge. Say the word if you want review-first.
2. **App layout vs spec 3J** — `comms/aiops/candidates/workspace` names vs the
   spec's `ingestion/assistant/evaluation`. Boundaries match; names differ. Still
   my lean: conform before deploy, or record the deviation and keep.
3. **Railway** — when you want to deploy: create the Railway project (PostgreSQL,
   Redis, storage/volume) or hand me an API token and I drive it via CLI. The app
   itself is deployable now; the remaining local work (below) doesn't block it.

## Remaining build items (agreed order)

1. **Railway deploy** — next up. Needs the Railway project (PostgreSQL, Redis,
   volume/storage) created by you, or an API token so I drive it via CLI.
2. **Prompt-improvement cycle** — after deployment, per your call: sharpen the
   bundled prompt against the eval baseline (evaluations/reports/), publish a
   Langfuse candidate, re-run `make eval`, compare, promote.
3. Optional polish: htmx-swap assistant panel (currently full-page PRG),
   dashboard cost breakdown by category.

Local build is complete: all P0 pages, pipeline, review workflow, lab,
assistant/drafting, Langfuse tracing + prompts, offline + runtime evals, and
the read-only admin inspection surface (15 PRs, 360 deterministic + 4 live
tests green).

## Session provider spend (2026-08-20 daytime)

Three manual live emails, two assistant turns, one draft, plus live-contract CI
runs: well under $0.05 total. Cumulative project spend remains ≈ $1.
