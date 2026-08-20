# Goodlane Freight Carrier Agent

An AI-assisted workspace for freight brokers: it ingests carrier emails and call
recordings, extracts structured inquiries, runs deterministic compliance and rate
checks, and gives the broker an evidence-linked view of every load, carrier, and
conversation.

Built as a take-home assignment. The full product requirements live in
[docs/prd.md](docs/prd.md); the implementation is specified in
[docs/specs/](docs/specs/), and every deliberate demo-vs-production shortcut is
recorded in [docs/production-deltas.md](docs/production-deltas.md).

## Stack

Django 5.2 + HTMX (server-rendered, no SPA) · PostgreSQL · Celery + Redis ·
OpenAI (extraction, assistant, drafting) · Deepgram (transcription) ·
Langfuse (tracing, prompts, evaluation) · uv · Railway (deployment)

## Local development

Prerequisites: Docker. Everything runs inside containers through `make` — no host
Python needed (`uv` on the host is only useful for editing the lockfile / IDE support).

```bash
cp .env.example .env        # fill in provider keys
make build                  # build the application image
make migrate                # apply database migrations
make up                     # start web + PostgreSQL 16 + Redis 7
```

`make help` lists every target.

## Tests

```bash
make test                   # deterministic suite (default: excludes live tests)
make test-live              # live provider-contract tests (real API calls, costs money)
make lint                   # ruff check + format check
```

The deterministic suite stubs providers only at the socket boundary and tests all
application logic against a real PostgreSQL. The `live` suite makes one small real
call per provider and asserts on actual response schemas — it runs in CI as the
deploy gate so provider API or schema drift is caught before a deploy.
