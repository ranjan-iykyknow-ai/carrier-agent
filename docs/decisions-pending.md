# Pending Decisions

Questions queued for you while you were away. Answer inline or in chat; none of them
block the work recorded below — where a default was needed I picked one and marked it.

## 1. GitHub Actions secrets for the live-contract CI job

The `live-contract` job needs `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, and the Langfuse
keys as **GitHub Actions secrets** on `ranjan-iykyknow-ai/carrier-agent` (encrypted by
GitHub, visible only to CI). Until they exist the job skips itself gracefully.

**Decision needed:** may I run `gh secret set` for those four values from the local
`.env`? (Alternative: you add them yourself in repo Settings → Secrets → Actions.)

## 2. Overnight PR policy

The dev→main PR ceremony exists so the interviewer sees a real workflow. While you
were asleep I proceeded as: open PR → wait for CI green → merge, since no second
reviewer exists.

**Decision needed:** keep that, or do you want to review each PR before merge from
now on?

## 3. Langfuse reviewer invite

The Langfuse Hobby plan allows 2 users. Plan of record: when you know the Goodlane
reviewer's email, invite them as **Viewer** to the carrier-agent project.
Nothing to do until you have the email — just flagging it lives here now.

## 4. Rethemed mockups

The mockup artifact now uses the real Goodlane brand (slate ink, orange accent,
Plus Jakarta Sans / Inter). Same link as before. Any visual notes before I build the
actual templates in the pages phase?

## 5. Railway (needed only at the deploy phase, several phases away)

When we get there you'll need to create the Railway project with PostgreSQL, Redis,
and a Storage Bucket — or hand me a Railway API token and I drive it via CLI.
No action needed yet.

## 6. App layout vs spec 3J (needs your call)

I implemented the domain apps as `freight / comms / aiops / inquiries / candidates /
workspace` before reading 3J, which prescribes `common / accounts / dashboard /
freight / ingestion / inquiries / assistant / evaluation` (with `candidates` folded
into `inquiries` and per-file model packages). Domain **boundaries** match; names
and file layout differ. New apps (accounts, dashboard, …) follow the spec names.

**Decision needed:** conform the existing app names/layout to 3J with a mechanical
rename (safe now — nothing is deployed; migrations regenerate cleanly), or accept
the current names and record the deviation? I lean toward conforming before the
deploy phase.

## 7. Full dataset processed live overnight (twice)

After the pipeline passed its live provider-contract tests and a 2-job real E2E,
I processed all 329 seeded jobs with real OpenAI + Deepgram calls. The first full
run (232 completed / 95 needs_review / 2 failed, $0.45) surfaced three real
issues — envelope-sender identity matching, a NUL character in one model output,
and no-load-reference availability emails flooding review — all fixed with tests.
I then wiped and reseeded for a clean demo state. Final run: **268 completed /
61 needs_review / 0 failed**, ≈ $0.45. Total provider spend ≈ $0.90, within the
disclosed ~$1 estimate. The local app at http://localhost:8000 is fully populated
(`var/dev-password.txt` holds your local login for broker@goodlanelogistics.com).

## Defaults I chose overnight (flag if you disagree)

- `Load.status` choices = the dataset vocabulary: `open`, `covered`, `delivered`,
  `cancelled` (the spec never enumerated them).
- `Carrier.authority_status` / `safety_rating` / `payment_terms_preference` are stored
  as raw nullable text, **not** constrained choices — the compliance policy interprets
  vocabulary (unrecognized → unknown) per the spec, and the dataset contains values
  like the literal string `"unknown"` that must be preserved verbatim.
- Django apps layout: `apps/freight`, `apps/comms`, `apps/inquiries`,
  `apps/candidates`, `apps/assistant`, `apps/aiops` (spec defines models, not app
  boundaries).
