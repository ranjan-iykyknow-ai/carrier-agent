# Goodlane Freight Carrier Agent

## Step 3 — ETL Pipeline, Service Boundaries, and State Transitions

**Status:** Complete — Steps 3A.1 through 3J agreed  
**Version:** 2.0  
**Last updated:** August 20, 2026  
**Related documents:** [Product Requirements Document](../prd.md) · [Architecture Foundation](./step-1-foundation.md) · [Domain Model](./step-2-domain-model.md) · [Production Deltas](../production-deltas.md)

---

## 1. Scope and Plan

Step 3 converts the agreed models into an executable processing design before migrations and application services are implemented. It will define:

- The importer, manual-ingestion, and simulation entry points.
- Shared validation and normalization services.
- Django transaction boundaries and Celery task orchestration.
- Email extraction and call transcription/extraction sequences.
- Retry, timeout, idempotency, failure, and broker-review transitions.
- Provider adapter contracts for OpenAI, Deepgram, Langfuse, and storage.
- Domain services for entity resolution, candidate reconciliation, eligibility, drafting, assistant tools, and evaluation.
- The first implementation slices and test-first commit sequence.

Step 3 is designed and reviewed incrementally. The shared ingestion contract and all three P0 entry points are specified below. The scripted simulation entry point remains P1 and is intentionally deferred; orchestration and downstream processing will be designed next.

## 2. Step 3A — Ingestion Entry Points

### 3A.1 Shared ingestion contract

#### Purpose and boundary

The shared ingestion contract is the single controlled application boundary through which every source enters the processing pipeline. Dataset seeding, manual email submission, manual audio upload, and the future P1 simulation prepare their inputs differently, but every accepted submission must produce or identify the same durable core records:

`CommunicationEvent` → channel-specific content → `IngestionJob`

No entry-point adapter calls OpenAI, Deepgram, entity resolution, compliance, ranking, or drafting logic directly. Those operations begin only after the durable ingestion job has been committed and dispatched to the worker pipeline.

The application service exposes channel-specific methods rather than a single loosely typed method:

```text
IngestionSubmissionService
├── submit_email(command) -> SubmissionResult
└── submit_call(command) -> SubmissionResult
```

Email and audio use separate commands because their validation, fingerprinting, persistence, and storage requirements differ. Both methods share the same origin policy, snapshot resolution, duplicate handling, job creation, correlation, and post-commit dispatch rules.

#### Shared submission context

The validated internal commands carry the following common context:

| Attribute | Purpose and rule |
|---|---|
| `origin` | `dataset`, `manual`, or later `simulation`; assigned by the trusted adapter rather than accepted from a manual form |
| `dataset_snapshot_id` | Snapshot supplying the data context and demo clock; the server selects the single active snapshot for manual submissions and rejects the submission when none exists |
| `external_source_id` | Stable dataset/provider identifier when supplied by a trusted adapter; generated internally for manual evidence |
| `import_batch_id` | Present for dataset ingestion and absent for manual submissions |
| `submitted_by` | Authenticated user for manual ingestion or an explicit system actor for trusted internal entry points |
| `source_timestamp_raw` | Exact source timestamp representation when one exists; absent for P0 manual submissions and dataset calls |
| `occurred_at` | Dataset sources use their normalized source time when available; the service derives manual business time from the active snapshot as described below |
| `source_timezone` | Timezone applied while deriving `occurred_at` |
| `source_metadata` | Untrusted annotations preserved only from trusted dataset/import adapters; never accepted as authoritative product facts |
| `correlation_id` | Server-generated identifier propagated through the job, worker calls, structured logs, AI operations, and Langfuse traces |
| `dispatch_mode` | Trusted internal choice of `immediate` or `deferred`; manual web submissions are always immediate and bulk seed ingestion is deferred |

Manual users cannot set `origin`, `import_batch_id`, trusted source metadata, a system actor, an arbitrary snapshot, or dispatch mode. The web adapter derives those values from authenticated server context before constructing the internal command. If no active snapshot exists, it returns a visible `dataset_not_ready` service error and creates no communication, storage object, or job.

The service captures one real `received_at` instant for every accepted origin. For a manual email or WAV, it derives `occurred_at` from the active snapshot's stored `as_of_at` local calendar date plus `received_at`'s real local time of day in the snapshot display timezone, then stores the result as a timezone-aware instant. It never rereads `DEMO_AS_OF_DATE` during submission. Dataset emails use their parsed source timestamp; dataset calls have no reliable source time and leave `occurred_at` and `source_timestamp_raw` null. Filesystem modification time is never business evidence.

#### Channel-specific commands

`SubmitEmailCommand` adds normalized-but-not-destructive input fields for sender address, recipient addresses, sender name, subject, plain-text body, optional HTML body, and the source timing supplied by a trusted adapter. For the P0 manual form it also carries optional `suspected_load_reference` and `suspected_carrier_identity` strings. These are explicitly untrusted user hints: they are stored on the job, may propose weak entity-resolution candidates, and can never verify a match, override extracted evidence, or satisfy a deterministic decision. The original accepted content remains preserved in `EmailContent`; normalization used for duplicate detection does not replace it.

`SubmitCallCommand` adds the accepted upload or prepared storage input, original filename, MIME type, byte size, duration and audio metadata established by validation, and SHA-256 checksum. The original filename is display metadata only and never controls the private storage key.

The commands are internal immutable application objects. Django forms validate manual web input, and dataset-specific parsers validate imported records before constructing them. They are not Django models, AI-output schemas, or user-controlled JSON contracts.

#### Submission result

Both methods return a common `SubmissionResult`:

| Attribute | Purpose |
|---|---|
| `outcome` | `created` or `existing` |
| `job_id` | Durable job the caller may display or poll |
| `communication_event_id` | Raw evidence record that was created or identified |
| `stable_evidence_id` | Citation-safe product identifier |
| `status` | Current ingestion-job status |
| `inquiry_id` | Deterministically selected resulting inquiry when available; required whenever status is `needs_review` so the broker has a review destination |
| `correlation_id` | Safe troubleshooting identifier |
| `retry_available` | Whether the identified failed job can be explicitly retried |

Presentation messages are selected by the view rather than returned by the domain service. The result never contains raw email content, audio, transcripts, prompts, provider responses, or credentials.

If a communication has multiple inquiries, `inquiry_id` selects the lowest `sequence_number` whose review status is `needs_review` when the job itself is `needs_review`; otherwise it selects the lowest `sequence_number` overall. It remains null only while no inquiry has been produced. The `(communication_event, sequence_number)` database constraint and this explicit ordering prevent database-default row order from affecting navigation.

#### Shared submission sequence

Every entry point follows the same boundary sequence:

1. The source adapter authenticates or identifies the actor and performs source-specific structural validation.
2. It constructs the applicable internal email or call command.
3. The submission service enforces origin, actor, snapshot, and metadata invariants again at the application boundary.
4. The service captures the real `received_at`, derives the origin-specific `occurred_at`, and owns all canonicalization used for the single fingerprint formula in Step 2B. Adapters parse structure but cannot implement competing fingerprint normalization.
5. It computes the channel- and algorithm-namespaced fingerprint while retaining the accepted original evidence unchanged.
6. For a new manual email, one database transaction creates the immutable `CommunicationEvent`, its `EmailContent`, and its non-null one-to-one `IngestionJob`, including any raw optional hints. For a new manual call, the same three database records are created in one transaction after private-object preparation; storage compensation is specified in Step 3C.
7. For a duplicate, it identifies the one existing event and job without changing their raw evidence or silently applying newly supplied hints.
8. Immediate dispatch registers the worker task with `transaction.on_commit`; deferred dispatch leaves the valid queued job for the trusted bulk dispatcher. Tasks receive only the stable job UUID.
9. The service assigns immutable manual evidence IDs as `email:manual:<event-uuid>` or `call:manual:<event-uuid>`; dataset evidence keeps its source-derived format.
10. It immediately returns `SubmissionResult`. Provider calls and downstream business processing never extend the web request or seed-command transaction.

The exact transaction and storage-compensation boundaries are specified in Step 3C. Celery claiming, broker unavailability, retries, and stale queued-job recovery are specified in Step 3D.

#### Duplicate and recovery behavior

- A duplicate never creates another communication record.
- If the existing logical job is queued, processing, completed, or `needs_review`, the service returns that job.
- If the existing job failed, the service returns it with `retry_available=true`; a repeated submission does not silently initiate another paid attempt.
- A `needs_review` result always returns its populated `inquiry_id`; if processing cannot create an inquiry card, the job is failed rather than marked reviewable.
- A manual submission that matches dataset evidence links to the existing dataset event, job, and inquiry when available.
- Optional hints submitted with duplicate content do not overwrite the original job or trigger a hidden reprocessing attempt. The broker uses the existing inquiry review flow to correct or add identity context.
- An existing successful result produces no new provider calls or provider cost.
- Email and audio use the one service-owned, namespaced, versioned fingerprint formula defined in Step 2B; adapters may not substitute their own recipe.

#### Validation and failure boundary

Errors that mean the application cannot safely accept the source are returned before any communication or job record is created. Examples include no active dataset snapshot, missing email content, invalid email structure, an unauthenticated manual request, unsupported audio, empty audio, or an upload outside configured size and duration limits. Manual HTTP requests with no active snapshot receive a clear service-unavailable response instructing the operator to complete dataset seeding; they never fall back to the environment clock or an empty reference context.

Failures after an accepted durable submission belong to the ingestion lifecycle. Provider unavailability, transcription failure, extraction failure, unresolved identity, evidence conflict, and deterministic business-review conditions therefore update the persisted job and related records instead of becoming an untracked form error.

Dataset-row validation errors are recorded through `ImportBatch` error reporting. They do not create incomplete communication records merely to make counts appear successful.

#### Scope

P0 implements this contract for the Goodlane dataset importer, manual email paste, and manual WAV upload. The P1 simulator will construct the same trusted internal commands and call the same service; it will not own a separate extraction or processing pipeline.

The boundary creates evidence and work only. It never sends an email, contacts or books a carrier, changes compliance or master data, or treats source metadata as a canonical fact.

#### 3A.1 acceptance criteria

- Every accepted submission creates or identifies one durable communication event and one appropriate logical ingestion job.
- Dataset and manual sources reach the same downstream worker pipeline.
- Duplicate submissions are safe, return the existing result, and do not repeat paid processing.
- Provider APIs are never called inside a manual HTTP request or the database transaction that accepts a source.
- Celery receives only stable record UUIDs and is dispatched only after commit.
- Manual callers cannot spoof trusted origins, snapshots, import batches, or source annotations.
- Manual email and audio submissions fail safely with no durable side effects when no active snapshot exists.
- Manual `received_at` and demo-clock `occurred_at` follow one service-owned rule based on the active snapshot's stored clock.
- An accepted event and its job have a non-null one-to-one relationship from the first migration.
- Optional manual email hints remain weak, auditable inputs and never become proof.
- `needs_review` duplicates return the existing inquiry card.
- Raw emails, audio, transcripts, prompts, and provider responses do not enter application logs or submission results.
- Unit and integration tests cover command invariants, rejection when no active snapshot exists, exact fingerprint parity between dataset and manual adapters, successful email and call registration, demo-clock derivation, hints, every duplicate job status, deterministic multi-inquiry selection, rollback, storage compensation, and immediate/deferred post-commit dispatch.

### 3A.2 Dataset seed entry point

#### Purpose and responsibility

The dataset seed entry point converts the supplied Goodlane package into the active, durable reference snapshot and prepares every supplied communication for the common asynchronous processing pipeline. It is a Django management command, not an application startup hook, migration, fixture load, web endpoint, or provider-specific script.

The seed must be safe to rerun, observable through `DatasetSnapshot` and `ImportBatch`, tolerant of intentionally incomplete or contradictory business data, strict about corrupt source structures, and explicit about when paid provider work may begin.

The supplied package contains:

| Logical source | Supplied records | Primary durable records |
|---|---:|---|
| `loads.csv` | 50 loads | `Load`, `Lane`, and equipment relationships |
| `carrier_profiles.json` | 48 carriers | `Carrier`, `CarrierContact`, `CarrierEquipment`, and `CarrierPreferredLane` |
| `rate_history.csv` | 720 weekly rates | `MarketRateHistory`, `Lane`, and equipment relationships |
| `carrier_emails.json` | 274 emails | `CommunicationEvent`, `EmailContent`, and `IngestionJob` |
| `call_recordings/` | 55 WAV files | Private audio object, `CommunicationEvent`, `CallRecording`, and `IngestionJob` |

A complete first import therefore creates or identifies 329 logical communication jobs: 274 email jobs and 55 call jobs. If all are processed live, it may cause approximately 55 Deepgram transcription calls and 329 OpenAI extraction calls as already disclosed in the deployment section.

#### Command interface

The P0 management command is:

```text
python manage.py seed
```

It supports the following bounded options:

| Option | Default and behavior |
|---|---|
| `--dataset-path PATH` | Defaults to the committed `goodlane-dataset/` package; primarily supports isolated tests and local verification |
| `--validate-only` | Performs discovery, schema checks, WAV inspection, source counts, and checksum calculation without database, storage, queue, or provider changes |
| `--no-enqueue` | Imports all durable records and creates queued jobs but suppresses Celery dispatch and therefore all paid processing |
| `--verbosity` | Uses Django's standard command verbosity behavior; it never enables raw payload output |

The default command imports and dispatches eligible missing work. A later ordinary run can dispatch jobs left queued by `--no-enqueue`, so no separate enqueue-only mode is needed.

P0 provides no `--force`, reset, delete, replace, or bypass-validation option. A seed command never modifies a completed immutable snapshot in place. `DEMO_AS_OF_DATE` and `APP_TIME_ZONE` come from validated application settings rather than CLI arguments, keeping local and deployed business-time semantics consistent.

#### Internal seed result

The command builds a safe `SeedSummary` for terminal output and structured logging:

| Attribute | Purpose |
|---|---|
| `snapshot_id` | Created or resumed snapshot |
| `snapshot_version` | Human-reviewable dataset version |
| `manifest_checksum` | Exact package identity |
| `batch_results` | Status and counts for the five logical sources |
| `records_created`, `records_existing`, `records_failed` | Aggregate import result |
| `audio_copied`, `audio_existing`, `audio_failed` | Private-storage result |
| `jobs_created`, `jobs_existing` | Logical ingestion-job result |
| `jobs_dispatched`, `jobs_left_queued` | Provider-work boundary result |
| `job_status_counts` | Snapshot-wide queued, processing, completed, `needs_review`, and failed counts after the global stale-job sweep |
| `stale_jobs_swept` | Processing jobs moved to failed because their processing start age exceeded the configured threshold |
| `processing_health` | Derived `processing`, `ready`, or `attention_required` summary |
| `safe_errors` | Bounded identifiers and error codes without raw payloads |

The command exit status is zero only when required structural imports, finalization, and requested dispatch succeed. Provider completion is intentionally separate: a successful initial seed may report `processing` while jobs are queued or running. `ready` requires no queued, processing, stale, or failed jobs; `needs_review` is a valid product outcome and is reported separately rather than treated as infrastructure failure. Any failed or stale work produces `attention_required` even when the import itself exits zero.

#### Trusted dispatch policy

Dataset communication commands use `dispatch_mode=deferred`. Manual web submissions always use `dispatch_mode=immediate`.

Deferred dispatch ensures carriers, loads, market history, storage objects, and every communication job are durable before workers begin entity resolution or provider calls. After snapshot activation, the seed hands eligible queued job UUIDs to the shared dispatch coordinator. `--no-enqueue` skips that final handoff while leaving valid queued work recoverable.

The dispatch mode is supplied only by the trusted importer. It is not a form field, public request parameter, or property a manual user can influence.

#### Seed execution sequence

##### 1. Resolve the package and prevent concurrent seeding

The command resolves the configured dataset root to an approved application path and acquires one session-scoped PostgreSQL advisory lock for the Goodlane seed operation before writing. It uses a stable application-owned 64-bit lock key, fails fast with a safe non-zero result when another seed holds the lock, keeps the same dedicated database connection open across all bounded import transactions, and releases the lock in `finally` or automatically when the connection closes. A row lock is insufficient because it would end with each bounded transaction; database uniqueness remains the final race-safety boundary.

The command rejects a missing directory, path outside the permitted dataset root in deployed mode, or package without all required logical sources.

##### 2. Preflight every source before writes

The preflight pass verifies:

- All four required data files and the call directory exist and are readable.
- JSON top-level shapes and required keys are structurally valid.
- CSV headers contain the required columns.
- Dates, decimals, integers, and controlled source values are parseable where the source claims a value exists.
- Every email timestamp includes `Z` or an explicit numeric UTC offset; timezone-naive timestamps are rejected rather than interpreted using server or application timezone.
- Every WAV is non-empty PCM WAV within the configured 25 MB and 600-second limits and exposes readable header metadata.
- External email and load identifiers are present where required and are not duplicated inside their source.
- The shared pure fingerprint implementation is run across all dataset emails and WAVs, and no two distinct dataset source IDs produce the same fingerprint. A conflict reports both stable source IDs and stops before writes instead of failing later at the global database constraint.
- Record counts and SHA-256 checksums can be calculated.
- `DEMO_AS_OF_DATE` and the application/display timezone resolve successfully.

The entire dataset is small enough for deterministic preflight without a staging warehouse. Structural preflight errors stop before database or storage mutation. `--validate-only` returns its summary here and makes no external calls.

Intentional business-data quality problems are not structural preflight failures. Missing MC/DOT values, unknown carrier names, blank weights, qualitative pickup windows, inconsistent email annotations, expired insurance, conditional authority, and garbled information expected inside calls remain valid inputs to be preserved and reviewed.

##### 3. Calculate the manifest and resolve the snapshot

The importer builds a deterministic manifest from the dataset version, sorted relative source paths, and each source's SHA-256 checksum. The call-directory entry incorporates its sorted filenames and per-file checksums. `DatasetSnapshot.manifest_checksum` stores the checksum of this canonical manifest.

Snapshot resolution follows these rules:

- The same version and manifest checksum identifies the same resumable snapshot.
- A completed matching snapshot is reused and verified, never rewritten.
- A partial matching snapshot resumes only its missing or incomplete work.
- Different content presented under an already completed version is rejected with a version/checksum conflict.
- Intentionally changed source content requires an explicit new dataset version and therefore a new snapshot.

`as_of_at` is resolved once from `DEMO_AS_OF_DATE` in the configured timezone and stored on the snapshot. All later manual business-clock calculations read this stored value rather than the environment variable.

##### 4. Create five logical import batches

The importer creates or resumes one `ImportBatch` for each source in the table above. `call_recordings/` is one logical batch containing 55 WAV records; it does not create 55 batch rows.

Each batch moves through queued, processing, and one terminal status while tracking records seen, created, existing, failed, start/completion times, checksum, and a bounded safe error summary. An unchanged record found during rerun is `existing`; immutable snapshot records are never counted as updated. A source whose structure cannot be processed is `failed`. A source with one or more structurally invalid rejected records but other successfully imported records is `partially_failed`. Both terminal failure statuses block activation. Intentional nulls, uncertainty, and contradictory business annotations are accepted records and do not cause `partially_failed`.

##### 5. Bootstrap deterministic reference vocabulary

Before business records, the importer ensures the approved P0 equipment types and source-scoped aliases exist. Directional `Lane` rows are created or reused as loads, carrier preferences, and market records are normalized. `PA → NJ` and `NJ → PA` remain different lanes, while valid intra-state pairs such as `PA → PA` and `NJ → NJ` are retained normally.

Reference bootstrapping is deterministic, contains no AI call, and never guesses an unknown equipment mapping. Unrecognized labels retain their raw value and unresolved status.

##### 6. Import carrier profiles

For each carrier profile, the importer:

1. Preserves raw MC, DOT, company, contact, payment, performance, compliance, and note values.
2. Normalizes identifiers, email, and phone into separate indexed values without replacing the raw display data.
3. Creates or identifies the snapshot-scoped `Carrier`.
4. Creates its contacts, canonical equipment links, and directional preferred-lane links.
5. Preserves missing compliance and performance fields as unknown rather than converting them to passing values or zero.

Because some rows lack an MC number, deterministic source identity uses the first available namespaced value: `mc:<normalized-mc>`, `dot:<normalized-dot>`, `email:<normalized-email>`, or `phone:<normalized-phone>`. If all are absent, it uses `rowhash:<digest>` over the canonical raw row plus its stable ordinal inside the immutable source. The namespace prevents equal-looking values from different identifier types from colliding. This key is import identity only; it is not proof that a later communication belongs to the carrier.

Conditional uniqueness conflicts among otherwise distinct carrier rows are preserved as import errors for review; the importer does not merge them through fuzzy matching.

##### 7. Import loads

For each load, the importer:

1. Uses `load_id` as the snapshot-scoped external load identifier.
2. Preserves origin, destination, raw equipment, weight, pickup window, delivery date, offered rate, status, shipper, and internal notes.
3. Resolves the directional lane and equipment only through deterministic vocabulary.
4. Parses money as decimals and counts/distances/weights as integers without converting blank values to zero.
5. Parses exact pickup ranges or single times when supported by the text and preserves qualitative, missing, or invalid windows without inventing timestamps.
6. Applies database constraints for non-negative values and valid controlled statuses.

The load importer does not derive operational facts from `internal_notes` and does not repair source data with AI.

##### 8. Import market-rate history

For each market row, the importer resolves the directional lane and canonical equipment, parses rates as decimals, and validates that minimum is less than or equal to average and average is less than or equal to maximum. Volume must be non-negative.

The snapshot, week, lane, and equipment combination is the idempotent business key. Invalid rows are reported rather than silently reordered, clamped, or replaced with averages.

##### 9. Import carrier emails through the shared contract

The email adapter converts each structurally valid source row into `SubmitEmailCommand` and calls `submit_email()` with dataset origin and deferred dispatch.

It uses:

- `email_id` as `external_source_id`.
- `email:<email_id>` as `stable_evidence_id`, for example `email:CE0074`.
- The supplied ISO timestamp as `source_timestamp_raw`, normalized UTC `occurred_at`, and the real import instant as `received_at`.
- Sender, recipient, subject, and body as immutable accepted evidence.
- Dataset-provided MC number, load reference, equipment, quoted rate, and intent only as untrusted `source_metadata`.

The shared service, not the adapter, computes the exact email-v1 fingerprint. This guarantees that pasting the same CE0074 sender, subject, and body into the manual form reaches the duplicate path even though origin, timestamps, recipient, metadata, and optional hints differ.

Contradictions between the email body and dataset annotations are successful imports. They become extraction, evidence, or `metadata_content_conflict` review cases later. Dataset annotations never initialize canonical inquiry fields and never become offline-evaluation ground truth.

##### 10. Import call recordings through the shared contract

For every validated WAV, the call adapter:

1. Calculates the audio-v1 byte checksum through the shared fingerprint implementation.
2. Generates a private content-controlled storage key that cannot be influenced by the original filename.
3. Copies the object through Django storage only if the content-addressed object is not already present with matching size/checksum metadata.
4. Uses the filename stem as `external_source_id` and the original filename only as display metadata.
5. Assigns `call:<filename-stem>` as stable evidence, for example `call:call_021_availability_check`.
6. Calls `submit_call()` with dataset origin, the prepared storage reference, validated media metadata, and deferred dispatch.

The filename's suffix (`rate_negotiation`, `availability_check`, `compliance_check`, `load_details`, or `voicemail`) is preserved only as an untrusted source hint. It cannot become extracted intent without transcript evidence.

The package provides no reliable call-occurrence timestamp. `source_timestamp_raw`, `occurred_at`, and `source_timezone` therefore remain null for dataset calls, while `received_at` records the real import instant. The importer never substitutes filesystem modification time. `received_at` may provide a clearly labeled deterministic presentation fallback in the Inbox and timeline, but it never decides quote or availability supersession. Conflicting cross-event positions without comparable source time are routed to broker review as defined in the domain-model specification.

##### 11. Finalize and activate the snapshot

After all logical batches finish, the command verifies:

- Required source counts and checksums match the preflight manifest.
- All five required batches are completed without structural failures.
- Every accepted email and call has exactly one channel detail and one non-null one-to-one ingestion job.
- Every accepted call points to an available private storage object.
- Reference constraints and expected source identifiers are satisfied.

Only when all five required batches are `completed` does the command set `imported_at` and activate the snapshot. Activation and deactivation of any previous snapshot occur atomically so P0 never exposes two active snapshots. Any queued, processing, `partially_failed`, or failed required batch leaves the candidate snapshot inactive. A same-manifest rerun after an importer/code correction may complete the rejected work; changing source bytes requires an explicit new snapshot version.

Intentional business-level missing values and contradictions do not block activation because they are the product's input, not importer defects.

##### 12. Dispatch eligible jobs

Before selection and final health aggregation, the command invokes the same bounded global stale-job sweep used by status polling. This marks abandoned processing jobs — and stale immediate-dispatch queued jobs, per the sweep's foundation definition — as failed even though nobody polls their individual pages.

Unless `--no-enqueue` is set, the command selects queued jobs for the resolved active snapshot after import and activation verification and hands their UUIDs to the shared dispatch coordinator. This covers both first activation and a later ordinary run following `seed --no-enqueue`. It does not redispatch jobs already processing, completed, `needs_review`, or failed. Failed jobs require an explicit retry action so reseeding cannot silently spend money again.

The seed command does not wait for all provider work to finish. It reports the numbers created, existing, dispatched, and left queued plus the complete per-status aggregate. Celery concurrency, dispatch batching, task claiming, provider rate pressure, time limits, and automatic per-attempt retries are specified in Step 3D.

If Redis is unavailable after snapshot activation, the durable jobs remain queued and the command exits non-zero with a safe dispatch error. Rerunning the ordinary seed command later skips completed imports and dispatches those eligible queued jobs.

#### Explicit bulk recovery for failed jobs

Terminal failed jobs are recovered through a separate management command rather than `seed`:

```text
python manage.py retry_failed_jobs \
  --snapshot active \
  --source-type call \
  --error-code provider_unavailable
```

Without `--execute`, the command is read-only and prints the selected count, error-code breakdown, estimated affected provider operations, and job identifiers in bounded form. Repeating the same command with `--execute` is the explicit confirmation that redispatch and possible provider cost are authorized.

The command supports snapshot, source type, repeatable error-code, and maximum-job filters. It selects only jobs still in `failed`, refuses jobs beyond the configured whole-job retry limit, and never accepts raw SQL or unbounded arbitrary filters. The Deepgram-outage recovery path can therefore preview and retry all failed call jobs in one controlled action without clicking 55 inquiry pages.

Execution atomically changes each selected job from failed to queued only if it remains failed, increments its whole-job retry count, preserves earlier `AIOperation`/`AIProviderCall` records, and dispatches the stable job UUID after commit. The management process never calls OpenAI or Deepgram directly. A racing manual retry or second command safely loses the conditional transition and does not double-dispatch the same logical attempt.

The command prints the same final per-status aggregate as `seed`. Permanent validation, unsupported-media, or deterministic policy outcomes are excluded from the default transient-provider selection and require an explicit error-code filter; `needs_review` is never bulk retried.

#### Record-level validation and error policy

The importer distinguishes source corruption from business uncertainty:

- Unreadable files, malformed JSON/CSV structure, missing required columns, duplicate required source IDs, invalid WAV containers, and impossible typed values are import errors.
- Nullable carrier attributes, missing load weight, qualitative pickup windows, conflicting annotations, unknown identities, compliance problems, and unclear call speech are accepted domain conditions.
- An invalid record is identified in the batch summary by a stable source ID or safe ordinal and controlled error code. Its raw body or audio is not copied into logs; one or more rejected records produce `partially_failed` and block activation.
- Independent structurally valid records may continue so the batch can report all safe failures in one pass.
- A fatal dependency or database error stops the current batch, leaves earlier completed batches intact, and permits a later same-manifest run to resume.

The exact per-record/chunk transaction sizes and object-storage compensation mechanics are specified in Step 3C. The importer never wraps all 1,147 supplied records and 55 object writes in one long database transaction.

#### Idempotency and recovery rules

- Snapshot version plus manifest checksum identifies the logical dataset run.
- Snapshot, source type, source name, and content checksum identify an `ImportBatch`.
- Loads use snapshot plus external load ID; market rates use snapshot/week/lane/equipment; emails use snapshot plus email ID; calls use snapshot plus filename-stem source ID.
- Carrier source identity follows the deterministic hierarchy above and remains snapshot-scoped.
- The shared content fingerprint constraint provides a second, global communication duplicate boundary across origins.
- A completed unchanged batch is verified and skipped.
- A `partially_failed` batch resumes only rejected or missing safe work after the cause is corrected; it does not rewrite previously accepted raw evidence and cannot activate until it becomes completed.
- An existing source ID with different content inside the same immutable snapshot is a conflict, not an update.
- Existing content-addressed audio is verified and skipped.
- A matching event already has its one logical job; rerun returns it rather than creating another.
- Completed, reviewable, processing, and failed jobs are not implicitly reprocessed.
- Ordinary reseeding recovers missing import work and eligible queued dispatch only; terminal failure recovery belongs exclusively to the explicitly confirmed bulk-retry command or an individual UI retry.
- Database wipe-and-reseed may yield different nondeterministic AI outputs after dispatch; this accepted P0 limitation remains recorded in the production-delta ledger.

#### Transaction and storage boundary

Preflight is read-only. Snapshot activation is a short final transaction. Import work uses bounded transactions so a failure does not hold locks across file parsing, storage upload, or the whole dataset.

For each accepted email, the event, `EmailContent`, and one-to-one job are committed together. For each accepted call, private storage preparation occurs outside the database transaction; the event, `CallRecording`, and one-to-one job then commit together. A newly written object is deleted if its database transaction fails, subject to safe orphan-cleanup logging. The complete compensation algorithm is finalized in Step 3C.

No Celery message is published before snapshot activation. No provider API is called inside an import transaction.

#### Safe output and observability

Terminal output and structured logs may include snapshot/batch UUIDs, source names, checksums, counts, durations, job statuses, safe error codes, storage keys or hashes where appropriate, and the seed correlation ID.

They never include full email bodies, audio bytes, transcripts, prompt text, credentials, provider responses, or sensitive environment values. Import success measures durable source acceptance; it does not claim that asynchronous transcription, extraction, matching, compliance, or evaluation has completed.

#### Tests

P0 tests the seed entry point without network or paid APIs:

- Parser and schema tests for all five logical sources.
- Tests proving intentional nulls and contradictions remain accepted data.
- Manifest ordering, checksum, snapshot-conflict, and active-snapshot tests.
- PostgreSQL advisory-lock contention and guaranteed-release tests.
- Full miniature-package integration seed using fake/local storage and `--no-enqueue`.
- Same-manifest rerun proving zero duplicate business, evidence, storage, or job records.
- Partial/fatal batch failure, blocked activation, and successful same-manifest resume after correction.
- Dataset-versus-manual CE0074 fingerprint parity.
- Namespaced carrier fallback source-identity and collision tests.
- Email annotation tests proving metadata is preserved but not canonical.
- Explicit-offset email timestamp acceptance and timezone-naive rejection tests.
- In-dataset duplicate-fingerprint preflight tests proving failure occurs before database or storage writes.
- Intra-state lane import tests.
- Call timestamp and ordering tests proving filesystem time is ignored, ingestion time is presentation-only, and unknown chronology cannot supersede business evidence.
- Audio copy, checksum verification, skip, and failed-database compensation tests.
- Deferred dispatch, `--validate-only`, `--no-enqueue`, Redis-failure, and later-dispatch tests.
- Snapshot-wide status aggregation and global stale-processing sweep tests, including seeded jobs whose pages were never polled.
- Bulk failed-job preview, filters, explicit execution, retry-limit, racing retry, and post-commit dispatch tests.
- Safe command-output tests ensuring payloads and secrets are absent.

CI uses representative miniature fixtures, mocked task dispatch, and fake/local storage. It never transcribes all 55 calls, extracts all 329 communications, seeds deployed data, or contacts OpenAI, Deepgram, Langfuse, Railway, or S3.

#### 3A.2 acceptance criteria

- The supplied 50 loads, 48 carriers, 720 market rows, 274 emails, and 55 WAV files are accounted for by the five batches.
- All 329 accepted communications have exactly one immutable event, channel detail, and logical job.
- Repeating an unchanged seed creates no duplicate snapshot, business record, evidence record, audio object, or job.
- The same fingerprint implementation makes dataset/manual duplicate detection deterministic.
- Intentional bad business data is preserved without becoming canonical truth or an import failure.
- Every WAV is privately and idempotently available to both web and worker services before activation.
- No worker or provider call begins before the complete snapshot is active.
- `--validate-only` and `--no-enqueue` allow safe validation and development without provider cost.
- An incomplete snapshot never replaces the active one, and a same-manifest rerun safely resumes recoverable work.
- Every required batch must be completed before activation; `partially_failed` has one unambiguous blocking meaning distinct from accepted business uncertainty.
- One PostgreSQL advisory lock protects the complete multi-transaction seed command.
- A later run after `--no-enqueue` dispatches queued jobs from the already-active resolved snapshot.
- Email source time is never accepted without an explicit offset, carrier import identities are type-namespaced, and duplicate dataset fingerprints fail during preflight.
- Every seed/rerun summary exposes snapshot-wide job counts and distinguishes processing, ready, and attention-required states.
- A provider outage affecting many jobs can be previewed and recovered through one explicitly confirmed, filtered command without making ordinary reseeding spend again.
- Any status poll and the seed health summary sweep all stale processing jobs through one bounded global operation.
- Command output, application logs, and error summaries contain no raw sensitive payloads.

### 3A.3 Manual email entry point

#### Purpose and responsibility

The manual email entry point lets an authenticated broker or interviewer paste a new carrier email and demonstrate that it uses the same extraction, entity-resolution, deterministic-validation, and review pipeline as the preloaded dataset.

P0 supports direct form entry. `.eml` and other email-artifact uploads remain optional later additions and do not delay the direct-entry happy path.

#### Form contract

The Django `ManualEmailForm` contains:

| Field | Required | Validation and storage rule |
|---|---:|---|
| `sender_name` | No | Trim outer whitespace; maximum 255 characters; preserve the accepted display value |
| `sender_email` | Yes | Django email validation; maximum 320 characters; preserve raw and normalized forms |
| `subject` | No | Maximum 998 characters; an intentionally blank subject remains blank rather than being invented |
| `body` | Yes | Must contain non-whitespace content; maximum 50,000 characters; preserve accepted line breaks |
| `suspected_load_reference` | No | Maximum 64 characters; untrusted entity-resolution hint |
| `suspected_carrier_identity` | No | Maximum 255 characters; untrusted entity-resolution hint |

The P0 direct-entry form does not collect recipient addresses, HTML, attachments, source timestamps, origin, snapshot, import batch, source metadata, dispatch mode, or external identifiers. Recipient and HTML values remain null/absent for manual direct entry. Future `.eml` support may populate them through a separately validated trusted parser.

Form limits are code-owned settings/constants and are enforced in both HTML attributes and server-side validation. Browser limits improve usability but never replace server validation.

#### Preview behavior

The form provides a Preview action before processing. Preview:

- Runs the same Django form validation used by final submission.
- Renders escaped sender, subject, body, and optional hints.
- Clearly labels the suspected carrier and load values as untrusted hints.
- Displays the active snapshot's demo date so the user understands the business-time context.
- Creates no communication, email, job, AI operation, trace, or other database record.
- Calls no provider and incurs no provider cost.

The editable form remains the source of truth in the browser; P0 creates no temporary preview model or session payload. Final submission revalidates every field because the user may edit after preview. Preview is a usability step, not authorization or trusted validation state.

#### HTTP and view contract

Recommended named Django routes are:

```text
GET  /manual-ingestion/
POST /manual-ingestion/email/preview/
POST /manual-ingestion/email/submit/
GET  /manual-ingestion/jobs/<job_id>/
GET  /manual-ingestion/jobs/<job_id>/status/
```

All routes require the authenticated demo session. Preview and submission require POST plus Django CSRF validation. HTMX requests receive server-rendered partials or `HX-Redirect`; direct browser requests use normal templates and Post/Redirect/Get behavior. Invalid form input re-renders the form with field-level errors and no durable side effects.

The submit control uses `hx-disabled-elt` (not HTMX's `hx-disable`, which is a security attribute that disables HTMX processing entirely) to disable after activation for double-click usability. Database constraints and the shared submission service remain the actual concurrency protection.

#### Command mapping

After successful final validation, the view adapter creates `SubmitEmailCommand` with:

| Command or stored value | Source and rule |
|---|---|
| `origin` | Server-owned `manual` |
| `dataset_snapshot_id` | The single active snapshot; absence returns `dataset_not_ready` before persistence |
| `submitted_by` | Authenticated demo user |
| `dispatch_mode` | Server-owned `immediate` |
| `external_source_id` | Generated `manual-email:<event-uuid>` |
| `stable_evidence_id` | Generated `email:manual:<event-uuid>` |
| `received_at` | One real submission instant captured by the service |
| `occurred_at` | Active snapshot local date plus the real local time of day, stored timezone-aware |
| `source_timestamp_raw` | Null; P0 direct entry accepts no user-controlled source timestamp |
| `source_timezone` | Active snapshot display timezone used for manual business time |
| `import_batch` | Null |
| `source_metadata` | Empty; the manual user cannot populate trusted source annotations |
| Email content | Validated form sender, optional name/subject, and body |
| Optional hints | Raw bounded values stored on the new `IngestionJob` |

The optional hints are excluded from the OpenAI extraction prompt. They enter only the application-owned entity-resolution stage as weak candidate signals. A hint may propose a carrier or load, but it cannot verify a match, override extracted evidence, satisfy compliance, become a citation, or resolve conflicting evidence. A hint that conflicts with the communication evidence contributes a controlled review reason.

#### Submission sequence

1. Require the authenticated session and valid CSRF token.
2. Resolve the single active dataset snapshot; if none exists, return the visible `dataset_not_ready` response with no durable side effects.
3. Validate the complete `ManualEmailForm` again.
4. Construct the trusted internal command without accepting server-owned attributes from the request.
5. Let `IngestionSubmissionService` capture time, apply the shared email-v1 normalization, and calculate the fingerprint.
6. Resolve the duplicate path through the database uniqueness boundary.
7. For a new source, create `CommunicationEvent`, `EmailContent`, and the non-null one-to-one `IngestionJob` in one transaction.
8. Register immediate Celery dispatch with `transaction.on_commit`; only the stable job UUID is sent.
9. Return or redirect to the durable job page immediately. OpenAI, matching, compliance, and canonical inquiry creation run only in the worker pipeline.

For HTMX, a successful POST uses `HX-Redirect` to the job detail route. A non-HTMX POST returns HTTP 303 to the same route. This prevents browser refresh from resubmitting the email while ensuring the status URL is bookmarkable and refresh-safe.

#### New-submission response and polling

The initial job page safely displays:

- Stable evidence ID.
- Escaped sender and subject preview.
- Queued or processing status.
- Real submission time and demo business time with clear labels.
- Correlation ID for troubleshooting.
- A short explanation that background processing is continuing.

It does not display prompt bodies, hidden reasoning, system instructions, secrets, unrestricted tool payloads, or full provider responses.

The page polls the job-status route every two to three seconds using HTMX. Each poll invokes the bounded global stale-job sweep already defined in the foundation. Polling stops for completed, `needs_review`, or failed. Refreshing the page reads the same durable job instead of restarting processing.

#### Duplicate response

A duplicate is a successful idempotent outcome rather than a validation error:

- Return the one existing event and job.
- Display “This email was already ingested.”
- Create no second event, job, inquiry, AI operation, or trace.
- Do not repeat provider cost.
- Do not apply newly submitted hints to the existing job.
- Link to the deterministic existing Inquiry Review when available.

This explicitly supports the interview demonstration in which manually pasting dataset email CE0074 resolves to its preloaded evidence and result.

If the existing job is queued or processing, the browser opens its status page. If completed, it links to the selected inquiry. If `needs_review`, it links to the lowest-sequence inquiry requiring review. If failed, it displays the existing safe failure and whole-job retry control.

#### Job outcomes and dispatch failure

The page presents only the agreed durable job vocabulary:

- `queued`: accepted and waiting for the worker.
- `processing`: claimed by the worker.
- `completed`: finished with a deterministic selected Inquiry Review destination.
- `needs_review`: finished but requiring broker action, with the selected review inquiry.
- `failed`: terminal safe failure with a whole-job retry action.

When one communication yields multiple inquiries, navigation uses the agreed deterministic rule: lowest sequence requiring review for a `needs_review` job, otherwise lowest sequence overall.

If Redis publication fails after the database commit, the dispatch callback transitions the durable job to failed with the controlled `queue_unavailable` error in a new short transaction. The callback's contract is explicit, because Django propagates exceptions from non-robust `transaction.on_commit` callbacks: it catches every publish failure internally — broker connection and timeout errors, and publish retry-policy exhaustion — writes the failed state, and never re-raises, so an already-committed submission can never surface as a server error. The response still opens the durable job page, which explains the recoverable failure and offers whole-job retry. This contract applies identically to the manual email and manual WAV entry points.

One crash window remains: `on_commit` callbacks are in-memory, so a process killed after the commit but before the callback runs leaves the job queued with no published task and no failure mark. The global sweep closes it: an immediate-dispatch (manual-origin) job still queued beyond the configured threshold is failed with `queue_unavailable` on the next poll or seed health check, so no manual job remains queued forever.

Provider, schema, entity-resolution, or deterministic-processing failures occur after acceptance and preserve the original email. Their safe status/error behavior belongs to the durable job rather than the original form.

#### Completed result presentation

The Manual Ingestion Lab shows a compact safe result and links into the normal product workflow rather than duplicating the complete Inquiry Review UI. When available, it shows:

- Extracted carrier and load.
- Availability, equipment, intent, explicit rate, questions, and conditions.
- Categorical field evidence status, match tier, and review status rather than numeric product confidence.
- Warnings, missing information, conflicts, and deterministic blockers.
- Stable source-evidence link.
- Links to Inquiry Review, matched Load Workspace, and matched Carrier Profile.
- Langfuse trace link only when configured and available after terminal processing.

The original escaped source remains inspectable. Detailed prompt/provider traces remain in Langfuse rather than being copied into the product page.

#### Validation and security rules

- Authentication and CSRF are mandatory.
- No active snapshot produces a clear service-unavailable response and no durable object.
- Sender email uses server-side email validation; body cannot be empty after trimming for the required-value check.
- NUL and unsafe control characters are rejected; legitimate body newlines and tabs are preserved.
- Sender, subject, body, hints, and validation messages are escaped in every template; submitted text is never rendered through Django's `safe` filter.
- Raw email bodies and hints are absent from application logs and safe error summaries.
- The form cannot set trusted source metadata, source time, origin, snapshot, user, import batch, dispatch mode, evidence ID, or correlation ID.
- Preview and final submission never send an email or cause another external business action.

#### Tests

P0 tests cover:

- Required/optional fields, maximum lengths, invalid sender addresses, and whitespace-only bodies.
- NUL/control-character rejection while preserving supported line breaks.
- Preview rendering, escaping, and proof that preview creates no records, traces, task dispatch, or provider calls.
- Authentication, CSRF, and missing-active-snapshot behavior.
- Correct real and demo-clock timestamps.
- Atomic event/content/job creation and rollback.
- Optional hints stored on a new job, excluded from extraction prompts, and treated only as weak resolution signals.
- Conflicting hints producing review rather than a verified override.
- Exact CE0074 dataset/manual fingerprint parity.
- Sequential and racing duplicate submissions with no additional provider work.
- Correct duplicate behavior for queued, processing, completed, `needs_review`, and failed jobs.
- Immediate dispatch only after commit and controlled Redis-publication failure.
- HTMX and non-HTMX redirects, durable status polling, terminal poll stop, and refresh safety.
- Deterministic multi-inquiry navigation.
- Absence of raw email content, hints, secrets, prompts, and provider payloads from logs and safe results.

#### 3A.3 acceptance criteria

- An authenticated user can preview and submit a valid carrier email through the Manual Ingestion Lab.
- Preview is escaped, provider-free, and side-effect-free; final submission always revalidates.
- An accepted new email creates one immutable event, one email detail, and one logical queued job atomically.
- The HTTP request returns a durable status destination without waiting for AI processing.
- Optional carrier/load hints remain auditable weak signals and cannot become evidence or proof.
- Duplicate submission returns the existing workflow without new records, traces, or provider cost.
- Queued, processing, completed, `needs_review`, and failed states remain understandable after refresh.
- Completed or reviewable processing links into the ordinary Inquiry Review and matched product pages.
- Invalid input, missing snapshot, queue failure, and downstream failure remain visible and recoverable without losing accepted evidence.
- No form or result page exposes hidden reasoning, secrets, unsafe raw payloads, or external-action capability.

### 3A.4 Manual WAV entry point

#### Purpose and responsibility

The manual WAV entry point lets an authenticated broker or interviewer upload a carrier-call recording and demonstrate that it uses the same transcription, extraction, entity-resolution, deterministic-validation, and review pipeline as the preloaded dataset calls.

P0 supports uncompressed PCM WAV. The supplied dataset format—16-bit, mono, 16 kHz PCM WAV—must work. Other valid PCM sample rates and mono or stereo recordings are accepted because Deepgram can process them. MP3, M4A, compressed WAV variants, live phone integration, and perceptual audio deduplication remain optional P1 or production additions.

#### Form and preview contract

The Django `ManualAudioForm` contains one required `audio_file`. The page displays the accepted PCM WAV type, maximum 25 MB size, and maximum 600-second duration before selection.

After the user selects a file, browser-side controls may display the filename, reported size, and a local audio preview using an object URL. This preview improves usability but is not trusted validation and creates no storage object, communication, job, trace, or provider call. P0 does not upload the file merely to preview it and therefore needs no temporary-preview model, session payload, or cleanup workflow.

The final action is labelled **Process Call**. It submits the file once for authoritative server validation and durable registration. Browser attributes such as `accept`, reported MIME type, size, duration, and filename remain hints only.

#### Authoritative WAV validation

Before durable acceptance, the server validates:

- A `.wav` extension, case-insensitively.
- A recognized WAV MIME declaration. A missing or generic `application/octet-stream` declaration may proceed only when the extension, signature, and parser all confirm valid WAV content; an explicitly unrelated MIME type is rejected.
- A real RIFF little-endian `WAVE` signature rather than trusting the extension.
- An uncompressed PCM codec and supported PCM sample width.
- A positive sample rate, supported channel count, positive frame count, and non-empty audio payload.
- A complete, parseable file whose declared audio data is not truncated; a plausible header alone is insufficient.
- A configured maximum byte size of 25 MB and calculated duration of no more than 600 seconds.

The application calculates the authoritative byte size, duration, checksum, sample rate, channel information, and audio format. Django's default upload handlers receive the entire request body before the view runs (and `DATA_UPLOAD_MAX_MEMORY_SIZE` deliberately excludes file uploads), so the size limit is enforced in two explicit layers: the view rejects the request early when the `Content-Length` header exceeds `AUDIO_MAX_UPLOAD_BYTES`, and a per-view bounded upload handler (installed via `request.upload_handlers`) aborts receipt with `StopUpload` once the byte budget is exceeded, covering absent or dishonest length headers. Validation and SHA-256 calculation then stream over the accepted upload without loading it fully into application memory. Safe field-level errors identify the violated rule without logging raw audio or unrestricted file metadata.

#### Command mapping and preserved metadata

After successful validation, the view adapter prepares `SubmitCallCommand` with:

| Command or stored value | Source and rule |
|---|---|
| `origin` | Server-owned `manual` |
| `dataset_snapshot_id` | The single active snapshot; absence returns `dataset_not_ready` before storage or persistence |
| `submitted_by` | Authenticated demo user |
| `dispatch_mode` | Server-owned `immediate` |
| `external_source_id` | Generated `manual-call:<event-uuid>` |
| `stable_evidence_id` | Generated `call:manual:<event-uuid>` |
| `received_at` | One real submission instant captured by the service |
| `occurred_at` | Active snapshot local date plus the real local time of day, stored timezone-aware |
| `source_timestamp_raw` | Null; P0 upload accepts no user-controlled source timestamp |
| `source_timezone` | Active snapshot display timezone used for manual business time |
| `import_batch` | Null |
| `source_metadata` | Empty; the manual user cannot populate trusted source annotations |
| Audio metadata | Original display filename, canonical MIME type, authoritative byte size and duration, PCM/WAV format, sample rate, upload time, and raw-byte SHA-256 checksum |

The original filename is bounded, reduced to display metadata, and escaped whenever rendered. It never controls a filesystem path, object key, content type, evidence identity, or provider option. P0 does not collect suspected carrier or load hints for audio; the transcript and extracted evidence drive matching.

#### Submission, storage, and transaction sequence

1. Require the authenticated session and valid CSRF token.
2. Resolve the single active dataset snapshot. If none exists, return the visible `dataset_not_ready` response and create no temporary durable object, communication, or job.
3. Stream and validate the complete upload while calculating its raw checksum, metadata, duration, and namespaced audio fingerprint.
4. Check the fingerprint against the global communication uniqueness boundary. An already committed duplicate returns its existing event and job without writing another object.
5. For a new source, allocate the prospective event UUID and generate a request-owned private key such as `manual-audio/<snapshot-id>/<event-uuid>.wav`. The object key never contains the original filename.
6. Store the validated bytes in private shared object storage before database registration.
7. In one database transaction, create the immutable `CommunicationEvent`, its `CallRecording`, and its non-null one-to-one `IngestionJob`.
8. Register immediate Celery dispatch with `transaction.on_commit`; only the stable job UUID is sent to the worker.
9. Return or redirect immediately to the durable job page. Deepgram, OpenAI, matching, and inquiry creation never extend the upload request.

Object storage and PostgreSQL cannot participate in one atomic transaction. If private storage fails, no database records are created. If database registration fails, the service deletes only the unique object created for that prospective event. If two requests race after the initial duplicate check, the global fingerprint constraint chooses the winner; the losing request deletes its own UUID-keyed object and returns the committed existing job after a bounded reread. It never deletes a shared or winner-owned object.

A process crash between the storage write and the database commit orphans a request-owned object that no database row references. This is accepted P0 debt, recorded in the production-delta ledger: the failure direction is safe (an unreferenced private object, never a committed record pointing at missing audio), the objects are UUID-keyed and private, and production would add a bucket-versus-database reconciliation sweep.

After database commit, the audio is accepted evidence and remains preserved even when Redis publication or downstream provider processing fails.

#### Duplicate behavior

The service applies the single audio-v1 fingerprint from Step 2B: SHA-256 over the algorithm namespace and exact raw WAV bytes. Consequently:

- The same WAV uploaded under another filename is a duplicate.
- A manual upload that exactly matches a dataset recording returns the dataset event and existing workflow.
- A duplicate creates no storage object, communication, job, transcript, AI operation, trace, or provider cost.
- A byte-different re-encoding of the same conversation is new evidence in P0. Perceptual or acoustic duplicate detection remains a production delta.

Queued, processing, completed, `needs_review`, and failed duplicates use the same result and deterministic inquiry-selection behavior defined in the shared contract. A failed duplicate exposes whole-job retry rather than silently restarting paid work.

#### HTTP, status, and playback contract

Recommended named Django routes are:

```text
GET  /manual-ingestion/
POST /manual-ingestion/audio/submit/
GET  /manual-ingestion/jobs/<job_id>/
GET  /manual-ingestion/jobs/<job_id>/status/
GET  /manual-ingestion/audio/<recording_id>/stream/
```

All routes require the authenticated demo session. Submission requires POST and Django CSRF validation. A successful HTMX request uses `HX-Redirect`; a normal browser request uses HTTP 303 to the durable job page, preventing refresh from resubmitting the upload.

The accepted source is played through an authenticated Django streaming endpoint rather than a public object URL. The endpoint resolves the recording by its application UUID, checks access, reads the private object through the storage backend, sets the canonical WAV content type, and returns an escaped inline filename. It supports bounded single-range requests needed by the HTML audio player for seeking and returns a controlled response for invalid ranges. Bucket credentials and private storage keys never enter HTML or client-visible URLs.

The job page follows the same two-to-three-second polling contract as manual email and invokes the global stale-job sweep. It shows only queued, processing, completed, `needs_review`, or failed. It does not expose internal chain-of-thought, prompts, secrets, unrestricted provider responses, or per-stage progress.

#### Background processing and outcomes

The worker receives only the job UUID and opens the persisted recording through the configured storage backend. Downstream orchestration performs Deepgram transcription, persists transcript evidence, runs OpenAI structured extraction, resolves carrier and load candidates, applies deterministic validation, and creates the resulting inquiry workflow.

The completed or reviewable page may show the authenticated audio player, current transcript, uncertain transcript regions, extracted business fields, warnings, categorical evidence status, and links to Inquiry Review, Load Workspace, Carrier Profile, and the Langfuse trace when available. Detailed provider operations remain in Langfuse and the internal observability models.

Once accepted, downstream failures never require the user to upload the WAV again. A Redis publication failure marks the durable job failed with `queue_unavailable`; a Deepgram or OpenAI failure records a safe terminal failure with whole-job retry; uncertain or conflicting business evidence produces `needs_review` when a valid inquiry card exists. The later worker-orchestration section will decide when a retry may safely reuse a successful durable transcript rather than purchase another transcription.

#### Validation and security rules

- Authentication and CSRF are mandatory.
- No active snapshot produces a service-unavailable response and no durable object.
- File content, not the filename or browser MIME declaration, controls acceptance.
- The configured size limit is enforced by the `Content-Length` precheck and the bounded upload handler before private persistence; oversize receipt is aborted rather than buffered to completion.
- Original filenames are never used in object keys or passed unsafely into headers or templates.
- The storage bucket and local production-equivalent objects remain private and outside executable paths.
- Raw audio, storage credentials, private keys, transcripts, and unrestricted provider payloads are absent from application logs and safe errors.
- The upload cannot set origin, snapshot, source timestamp, import batch, dispatch mode, evidence ID, correlation ID, trusted metadata, or provider configuration.
- Upload and processing never place a call, contact a carrier, send an email, or book capacity.

#### Tests

P0 tests cover:

- Authentication, CSRF, missing-active-snapshot behavior, and upload limits displayed on the form.
- Valid dataset-format audio plus supported PCM sample-rate and mono/stereo variations.
- Invalid extension, unrelated MIME declaration, bad signature, unsupported codec, malformed header, truncated data, zero frames, invalid sample rate, unsupported channels or sample width, oversize input, and excessive duration.
- Case-insensitive `.wav` and common WAV MIME variations without trusting MIME alone.
- Streaming validation and hashing without unbounded memory use.
- Exact duplicate detection across manual and dataset origins, the same bytes under another filename, and racing duplicate submissions.
- The documented P0 limitation that a byte-different re-encoding is treated as new evidence.
- Private UUID-based object keys and proof that the original filename cannot influence a path.
- Storage failure with no database side effects, database rollback with request-owned object compensation, and race-safe cleanup.
- Atomic event/recording/job creation and immediate dispatch only after commit.
- Controlled Redis-publication failure while preserving accepted audio.
- Proof that the web request makes no Deepgram or OpenAI call.
- Authenticated playback, access rejection, inline filename escaping, valid range responses, and invalid-range handling.
- Durable polling, terminal-state behavior, whole-job retry, deterministic inquiry navigation, and absence of raw audio, storage keys, prompts, secrets, or unrestricted provider payloads from logs and responses.

#### 3A.4 acceptance criteria

- An authenticated user can select, locally preview, and submit a supported PCM WAV through the Manual Ingestion Lab.
- Authoritative server validation enforces real WAV content, PCM structure, non-empty audio, size, and duration without trusting browser metadata.
- A new accepted WAV is stored privately and creates one immutable event, one call detail, and one logical job with the compensation boundary outlined here and finalized in Step 3C.
- The HTTP request returns a durable status destination without waiting for Deepgram, OpenAI, or business processing.
- Exact duplicates return the existing workflow without another object, record, trace, or provider cost.
- Accepted audio remains playable and recoverable through an authenticated application endpoint after refresh or downstream failure.
- Queued, processing, completed, `needs_review`, and failed outcomes remain understandable and whole-job retry never requires another upload.
- Completed or reviewable processing links into the normal Inquiry Review and matched product pages.
- Raw audio, private object locations, credentials, prompts, and unsafe provider payloads are never exposed.

The scripted simulation entry point is P1 and is intentionally not expanded in Step 3A.

## 3. Step 3B — Shared Validation and Normalization

### Product goal

Email evidence and call transcripts must become the same evidence-grounded structured proposal so downstream matching and review do not need separate business logic for each channel.

```text
Email content ─────┐
                   ├── Normalize → Extract → Validate → ExtractionRun
Call transcript ──┘
```

This section prepares and validates machine proposals. It does not select database carriers or loads, calculate eligibility, rank candidates, create drafts, or take external action.

### Inputs and outputs

| Area | Primary records |
|---|---|
| Email input | `CommunicationEvent`, `EmailContent`, `IngestionJob` |
| Call input | `CommunicationEvent`, `CallRecording`, current `Transcript`, ordered `TranscriptSegment` rows, `IngestionJob` |
| AI execution | `AIOperation`, `AIProviderCall` |
| Primary output | Append-only `ExtractionRun` containing raw and validated output plus schema, prompt, and model versions |
| Later consumers | `Inquiry`, `CarrierQuote`, evidence, and match records created by the reconciliation section |

Only a successful current transcript can enter call extraction. Original email content, audio, transcript text, and previous extraction attempts are never overwritten.

### Shared behavior

The normalized extraction proposal may contain one or more ordered inquiries with:

- Raw carrier names, contact details, and identifiers mentioned in the source.
- Raw load references and equipment mentions.
- Availability and one or more controlled intents.
- Monetary mentions with their semantic roles, such as carrier quote, counteroffer, broker-rate reference, accepted broker rate, or ambiguous amount.
- Carrier questions, conditions, and requirements.
- Source evidence pointers supporting each proposed value.

Missing information stays missing. The model must not invent a carrier, load, rate, equipment type, availability statement, or source evidence.

Application-owned normalization maps identifiers, email and phone values, load references, equipment terminology, currencies, decimals, availability, intents, email offsets, and call timestamps into the controlled domain vocabulary while retaining the original source representation.

### Responsibility and trust rules

- OpenAI proposes structured fields, semantic roles, and evidence locations; it never verifies an entity or product decision.
- Deterministic application code validates the schema, controlled values, normalized representations, and evidence grounding.
- Manual hints are excluded from the extraction prompt and enter only later as weak matching signals.
- Dataset `source_metadata` remains an untrusted annotation and cannot become extraction truth; later comparison may produce a review reason when it conflicts with content.
- A monetary value is not automatically a carrier quote. Multiple values and their different roles must be preserved.
- The product uses categorical evidence states—explicit, inferred, missing, or conflicting—rather than displaying an arbitrary numeric LLM confidence score.
- Prompt resolution follows the agreed Langfuse, last-known-good cache, and bundled emergency-fallback policy without copying prompt bodies into product records or logs.

### Validation and failure behavior

An extraction may continue only when it matches the versioned schema, uses valid controlled values, represents monetary data safely, provides deterministic inquiry ordering, and points evidence references to the actual email or transcript region.

A schema-valid but incomplete, ambiguous, or conflicting proposal continues to reconciliation and may become `needs_review`. A structurally invalid or ungrounded proposal is preserved as a failed `ExtractionRun`, records a safe error, and follows the worker retry policy; it cannot modify canonical inquiry records.

### Tests

P0 tests cover:

- Equivalent email and transcript content producing the same shared structured shape.
- Missing values remaining missing and conflicting values remaining visible.
- Identifier, equipment, intent, availability, currency, decimal, and rate-role normalization.
- Broker-rate references remaining distinct from carrier quotes and counteroffers.
- Valid email offsets and transcript timestamps, plus rejection of fabricated or out-of-range evidence.
- Multiple extracted inquiries receiving deterministic sequence numbers.
- Invalid schemas never producing canonical inquiries.
- Manual hints and source metadata never becoming verified facts.
- Raw evidence and previous extraction attempts remaining unchanged.

### 3B acceptance criteria

- Email and call sources enter one shared versioned extraction contract.
- Every extraction attempt is preserved with its prompt, schema, model, and validation identity.
- Only schema-valid and source-grounded proposals can continue to canonical reconciliation.
- AI proposals remain separate from verified product facts and deterministic decisions.
- Missing, ambiguous, and conflicting information remains visible for later review.
- Downstream services receive a consistent structured proposal regardless of source channel.

## 4. Step 3C — Worker Orchestration and Job Transitions

### Product goal

Every accepted communication must reach a durable result without blocking the web request, creating duplicate inquiries, losing evidence, or silently hanging when Redis, a worker, or an AI provider fails.

The Manual Ingestion Lab continues to show one understandable job status. Detailed task and provider diagnosis belongs to `AIOperation`, `AIProviderCall`, structured logs, and Langfuse.

### Records and services involved

| Area | Primary records or service |
|---|---|
| Product lifecycle | `IngestionJob` and its `CommunicationEvent` |
| Email source | `EmailContent` |
| Call source | `CallRecording`, `Transcript`, `TranscriptSegment` |
| AI work | `ExtractionRun`, `AIOperation`, `AIProviderCall` |
| Business result | One or more `Inquiry` records and their review state |
| Queue coordination | Celery and Redis; PostgreSQL remains the durable source of truth |

Celery tasks receive only a stable job UUID. Redis carries work coordination, not canonical product state or task results.

### High-level processing flow

1. Claim a queued job through an atomic database transition to `processing`, capturing the job's current `retry_count` as this execution's generation and setting `started_at` for the generation.
2. Load the immutable source through its database relationship or private storage key.
3. For a call, use a valid current transcript or create one through Deepgram.
4. Reuse a compatible successful current extraction when one exists under the Step 3D compatibility rules; otherwise run the shared structured extraction from Step 3B.
5. Pass a valid proposal through entity resolution, inquiry reconciliation, and deterministic decision services defined later.
6. Finish the job as `completed`, `needs_review`, or `failed` as the final step of the single finalization transaction defined in Step 3F.

An email skips transcription. Provider work never runs inside the ingestion HTTP request, seed transaction, or database lock.

### State and idempotency rules

The P0 state vocabulary remains:

```text
queued → processing → completed
                    → needs_review
                    → failed

failed → queued     only through an explicit whole-job retry
```

- One communication owns one logical job; retries increment and reuse it.
- A duplicate Celery delivery may claim the job only once. A task finding an already processing or terminal job exits without repeating paid work.
- Each worker execution captures the job's retry generation at claim. Stale or superseded execution may preserve diagnostic provider attempts but cannot become current or overwrite canonical results after a newer retry begins.
- The generation fence is enforced at write time, not only at stage start: **every canonical persistence or terminal transaction locks the job row and proceeds only when the status is `processing` and `retry_count` equals the generation captured at claim**; otherwise the execution persists only its diagnostic `AIOperation`/`AIProviderCall` rows and its `Transcript`/`ExtractionRun` output as non-current, and exits. The duplicate provider spend on this rare path is accepted P0 cost.
- The job outcome is determined by the extraction attempt this execution ran or deliberately reused. A failed new attempt fails the job even when an older run remains current; "prior current stays current" governs only what later reads see, never this execution's outcome.
- The terminal transition recomputes the job aggregate from the persisted `Inquiry.review_status` rows inside the finalization transaction, so `needs_review` can never exist without a persisted reviewable inquiry.
- `completed` means usable inquiries were created with no unresolved review condition. `needs_review` means at least one valid inquiry requires broker attention and the job exposes the deterministic review destination. A technical failure that produces no reviewable inquiry is `failed`.
- When one communication creates multiple inquiries, the job is `needs_review` if any requires review; navigation uses the agreed lowest-sequence rule.

### Retry and recovery behavior

- Provider adapters use explicit timeouts and bounded automatic retries only for safe transient failures such as network errors, timeouts, rate limits, and temporary unavailability.
- A terminal failed job is never silently redispatched. The user uses the whole-job retry control, or an operator uses the confirmed `retry_failed_jobs` command for a bounded set.
- Whole-job retry locks the failed job, checks the configured retry limit, increments `retry_count`, clears only the current safe terminal markers, returns it to queued, and dispatches after commit. Raw evidence and historical AI operations remain unchanged.
- Retrying a call never requires another upload. Step 3E specifies when a compatible successful transcript is reused instead of purchasing another transcription.
- Redis publication failure after commit becomes the durable `queue_unavailable` failure already defined in Step 3A.
- The global stale-job sweep marks expired processing work failed and also catches overdue immediate-dispatch manual jobs. Legitimately deferred dataset jobs remain queued for the seed dispatcher. The sweep predicate is processing start age (no heartbeat column exists in P0), so its threshold must be sized above the worst-case legitimate provider latency; sweeping a slow-but-alive worker causes at most one duplicate provider spend, which the generation fence keeps out of canonical state and the ledger records as accepted P0 cost.

### Failure and observability rules

Safe controlled categories cover queue unavailability, provider timeout/rate limit/unavailability, transcription failure, extraction-schema failure, storage failure, stale processing, validation failure, and unexpected internal failure. User-visible summaries identify the failed operation and recovery action without including raw evidence, prompts, transcripts, credentials, or provider payloads.

Every task propagates the job correlation ID into structured logs, AI operation records, provider calls, and Langfuse. Operational cost, latency, and provider usage are recorded without adding per-stage progress to the P0 product UI.

### Tests

P0 tests cover:

- Atomic claiming and duplicate Celery delivery without duplicate provider calls or inquiries.
- Correct email and call branch selection.
- Only stable UUID task arguments and dispatch only after commit.
- Every valid state transition and rejection of invalid transitions.
- Completed, `needs_review`, and failed outcomes, including multiple-inquiry aggregation.
- Bounded transient provider retry and no silent terminal-job redispatch.
- Explicit whole-job retry reusing evidence and incrementing the retry generation.
- A stale older execution being unable to overwrite a newer retry.
- Queue publication failure, worker timeout, stale-job recovery, storage failure, and safe errors.
- Refresh-safe polling and PostgreSQL remaining authoritative when Redis is unavailable.
- Correlation across job, logs, provider operations, and Langfuse without sensitive payload leakage.

### 3C acceptance criteria

- Accepted evidence is processed asynchronously through one durable logical job.
- At-least-once Celery delivery cannot duplicate paid work or canonical business records.
- Every job reaches a durable completed, reviewable, or recoverable failed outcome.
- Whole-job retries preserve evidence and history while preventing stale work from becoming current.
- Redis and provider failures remain visible and cannot silently lose or indefinitely hang work.
- The P0 product shows one simple job lifecycle while detailed technical diagnosis remains available through observability records and Langfuse.

## 5. Step 3D — Email Extraction Pipeline

### Product goal

Turn an accepted carrier email into a versioned, evidence-grounded extraction proposal that can enter the shared matching and inquiry workflow. Dataset and manually entered emails use the same pipeline, and the original email remains the source of truth.

This section owns email-specific input preparation and OpenAI extraction. Shared normalization and validation remain in Step 3B; entity resolution and canonical inquiry creation remain later steps.

### Inputs and outputs

| Area | Primary records |
|---|---|
| Source | `CommunicationEvent`, `EmailContent`, `IngestionJob` |
| AI execution | `AIOperation`, `AIProviderCall`, Langfuse trace |
| Primary output | Validated or failed append-only `ExtractionRun` |
| Later output | `EvidenceSpan`, `Inquiry`, `CarrierQuote`, field assessments, and match candidates created during reconciliation |

The extraction input may use the sender name and address, recipient headers when present, subject, and plain-text body. Dataset `source_metadata` and manual suspected-identity hints are excluded because they are not message evidence.

### Email preparation and evidence grounding

The application prepares one deterministic extraction document from the accepted email without changing the stored source. Subject and body content are separated into stable source blocks so the model can identify where each proposed fact appeared.

The model returns a source part and short exact excerpt for each proposed fact. Deterministic code locates the excerpt and derives the final character offsets used by `EvidenceSpan` under precise matching rules: the prepared document's blocks are byte-identical to the stored subject and body (no normalization is applied to the matching surface), matching is an exact code-point substring search within the named block only (an excerpt never spans blocks), and when an excerpt legitimately occurs more than once in its block the model supplies an occurrence ordinal — a missing ordinal deterministically selects the first occurrence, because a repeated statement is strong evidence, never a rejection reason. Missing, altered, fabricated, or out-of-range evidence is not silently accepted; the affected field becomes invalid or reviewable according to the Step 3B validation rules.

The email body is untrusted business content. Instructions written inside an email are treated as carrier text, never as application or prompt instructions. The extraction request exposes no tools and gives the model no authority to query records or perform actions.

### Extraction behavior

The provider request uses the configured extraction model and strict JSON-schema Structured Outputs. The schema returns one or more ordered inquiry proposals containing the shared fields defined in Step 3B, including raw identity signals, load references, equipment, availability, intents, rate mentions and roles, questions, conditions, and evidence excerpts.

One email discussing one load with several intents normally produces one inquiry with multiple intent rows. An email clearly discussing different loads may produce multiple ordered inquiries. The application validates and assigns deterministic sequence numbers rather than relying on model or database row order.

No carrier or load database objects are included for the model to select. Entity matching later evaluates the raw extracted identifiers and references against the active snapshot.

### Prompt, schema, and model identity

Prompt retrieval follows the agreed order: labelled Langfuse prompt, process-level last-known-good cached prompt, then the bundled emergency fallback. A request proceeds only when a valid prompt and supported schema are available.

Every `ExtractionRun` records the schema version, resolved prompt name/version/source, configured model, raw response, validated result, and validation outcome. `AIOperation`, `AIProviderCall`, and Langfuse capture correlation, latency, token use, estimated cost, provider request identity, and safe failures.

If the communication already has a successful current extraction produced by the same schema version, prompt version, and model, a downstream retry **reuses** it rather than purchasing another identical OpenAI request — reuse is deterministic, not optional, so retry cost and trace shape are testable. The comparison uses the prompt version resolved by the *current* execution: if Langfuse is unavailable at retry time and the fallback resolves a different version, a new attempt is created under that identity — an explicit, tested outcome rather than an accident. Reuse considers current runs only; a stale execution's successful-but-non-current output is never reused. A changed schema, prompt, or model creates a new append-only extraction attempt and supersedes the prior current result only after validation succeeds; that supersession rule governs what later reads see, while the running execution's outcome is always determined by the attempt it ran or reused.

### Business and failure rules

- Missing values remain null or controlled `not_stated` values; they are never guessed from dataset metadata or directory records.
- Every rate mention retains its semantic role. A broker-posted amount, carrier quote, counteroffer, accepted amount, and ambiguous amount are not interchangeable.
- Conflicting statements are preserved rather than resolved by the model.
- Only a validated extraction can enter reconciliation or become current.
- Timeouts, rate limits, and temporary provider unavailability follow the bounded transient retry policy in Step 3C.
- Provider refusal, incomplete output, unsupported schema, ungrounded evidence, or semantic validation failure creates a safe failed extraction outcome and cannot create canonical inquiry facts.
- Raw email content, prompts, and provider responses remain absent from application logs and user-visible error summaries. Detailed authorized trace inspection remains in Langfuse.

### Tests

P0 tests cover:

- Dataset and manual emails reaching the same email extraction path.
- Sender, subject, and body preparation while excluding manual hints and dataset metadata.
- Email content that resembles instructions remaining untrusted data and receiving no tools.
- Strict schema parsing, explicit missing values, controlled vocabularies, and multiple-inquiry ordering.
- Exact subject/body excerpt validation and deterministic `EvidenceSpan` offsets.
- Rejection of fabricated, altered, out-of-range, and ambiguous evidence references.
- Correct separation of broker rates, carrier quotes, counteroffers, and ambiguous amounts.
- Prompt fallback identity and recording of schema, prompt, model, cost, and correlation metadata.
- Reuse of a compatible successful extraction and creation of a new run after prompt, schema, or model changes.
- Provider timeout, rate limit, refusal, incomplete response, and validation failure without canonical side effects.
- Preservation of raw email and previous extraction attempts without sensitive logging.

### 3D acceptance criteria

- Every accepted dataset or manual email can produce one shared, versioned extraction proposal.
- Extraction uses only the accepted email evidence and never treats hints, annotations, or directory data as source truth.
- Each proposed business value is missing, conflicting, or grounded to a verifiable subject/body excerpt.
- Rate roles and multiple inquiries remain distinct and deterministic.
- Only validated output can continue to matching and inquiry creation.
- Compatible successful work may be reused safely, while changed prompt, schema, or model identity creates an auditable new attempt.
- Provider and validation failures remain durable, recoverable, cost-visible, and free of canonical side effects.

## 6. Step 3E — Call Transcription and Extraction Pipeline

### Product goal

Turn an accepted dataset or manually uploaded carrier-call recording into a durable, reviewable transcript and then into the same structured extraction proposal used by email. The resulting inquiry workflow must preserve what was heard, where it was heard, and where transcription uncertainty may affect a business decision.

This section owns call-specific transcription, transcript evidence, and the handoff to Step 3B extraction. It does not decide carrier/load matches, eligibility, ranking, or outbound action.

### Inputs and outputs

| Area | Primary records or service |
|---|---|
| Source | `CommunicationEvent`, `CallRecording`, `IngestionJob`, private object storage |
| Transcription execution | `AIOperation`, `AIProviderCall`, Deepgram, Langfuse trace |
| Transcript output | Append-only `Transcript` and ordered `TranscriptSegment` rows |
| Extraction output | Append-only validated or failed `ExtractionRun` using the shared Step 3B schema |
| Later output | `EvidenceSpan`, `Inquiry`, `CarrierQuote`, field assessments, and match candidates created during reconciliation |

Dataset and manual calls use this same path. The worker reads the WAV through its private storage key; the browser, Celery message, and logs never carry the audio bytes or storage credentials.

### High-level processing flow

1. Confirm that the current job execution still owns the processing generation and that the private WAV matches the stored recording metadata.
2. Reuse a compatible successful current transcript when one already exists for the same audio checksum and transcription configuration.
3. Otherwise stream the private WAV from the worker to Deepgram's prerecorded transcription API.
4. Validate and persist the provider result as one `Transcript` with ordered `TranscriptSegment` rows.
5. Prepare a normalized speaker-labelled transcript from the stored segments.
6. Run the channel-specific call prompt using the shared structured schema and validation rules from Step 3B.
7. Pass only a validated `ExtractionRun` to entity resolution and inquiry reconciliation.

No transcription or extraction provider call occurs in the upload request or database transaction that accepted the recording.

### Transcription contract

P0 uses the configured Deepgram prerecorded model, initially Nova-3, with smart formatting, utterance segmentation, and `diarize_model=latest`. The application stores the requested configuration and the resolved provider and diarizer metadata, including the returned diarizer model identity, so later behavior remains explainable when a `latest` alias changes.

The persisted transcript contains the raw and normalized text, language, provider request identity, model/configuration identity, safe provider metadata, and current/superseded state. Each `TranscriptSegment` preserves deterministic order, start/end seconds, text, provider speaker label, optional mapped speaker role, and provider confidence when supplied.

Provider speaker numbers are labels, not business identities. The pipeline never assumes speaker 0 is the broker or carrier. A speaker role is mapped only when the conversation provides sufficient consistent evidence; otherwise the role remains unknown while the original label is retained.

Deepgram utterance and word confidence are transcription metadata rather than product confidence. Segments below the application-owned low-confidence threshold are visibly uncertain. If a critical field such as carrier identity, load reference, equipment, availability, or rate depends only on uncertain transcript text, later field assessment cannot treat it as clean explicit evidence and may require broker review.

### Transcript evidence and extraction

The call extraction prompt receives ordered, timestamped transcript segments and no carrier/load directory candidates, manual hints, or trusted answer annotations. Spoken instructions inside the recording are communication content, not application instructions, and the extraction request exposes no tools or external actions.

The model proposes the same inquiry fields and rate roles defined in Step 3B. For evidence, it returns the relevant transcript segment identity and a short exact excerpt. Deterministic code verifies the excerpt and timestamps against stored `TranscriptSegment` rows before later creating timestamped `EvidenceSpan` records.

Relative transcript seconds describe positions inside the WAV. They never manufacture a business `occurred_at` timestamp for dataset calls whose real source time is unknown.

### Versioning, reuse, and failure behavior

- A successful compatible transcript is reused when extraction or later reconciliation is retried, avoiding another paid transcription.
- Compatibility is based on the audio checksum and the **requested** transcription model/options identity, not merely the existence of transcript text. The resolved provider and diarizer metadata is stored for explanation only and never enters the reuse comparison — the resolved identity of a not-yet-made call is unknowable, and a reused transcript may legitimately carry an older resolved diarizer than a fresh call would.
- Call-extraction reuse additionally keys on the identity of the current `Transcript` (its primary key), not merely the audio fingerprint: superseding a transcript invalidates extraction reuse for that communication, because the extraction input is the transcript, not the audio bytes.
- A changed transcription configuration creates a new append-only transcript attempt. A prior current transcript is superseded only after the replacement validates successfully.
- Empty speech, a malformed provider response, invalid segment ordering/timestamps, or missing required transcript data cannot continue to extraction.
- Provider timeouts, rate limits, and temporary unavailability follow the bounded transient retry policy in Step 3C.
- A terminal transcription or extraction failure preserves the WAV and prior attempts and exposes whole-job retry; the user never has to upload the audio again.
- Provider responses and transcripts may be retained in their authorized records and Langfuse trace but remain absent from application logs and safe user-facing errors.

### Tests

P0 tests cover:

- Dataset and manual WAVs reaching the same transcription path.
- Private storage streaming and controlled failure when the object is missing or no longer matches its metadata.
- Nova-3 prerecorded request configuration with smart formatting, utterances, and the configured diarizer model.
- Persistence of provider/model identity, resolved diarizer metadata, transcript text, ordered segments, timestamps, speaker labels, and available confidence.
- No assumption that speaker 0 represents a particular business participant.
- Low-confidence transcript regions remaining distinct from product field-evidence status and causing review when solely supporting a critical fact.
- Empty audio speech, malformed provider output, invalid timestamps, overlapping or unordered segments, and unsupported response shapes.
- Exact transcript evidence validation and rejection of fabricated excerpts or timestamps.
- The call transcript entering the same structured schema and rate-role handling as email.
- Compatible transcript reuse after a downstream failure and a new append-only attempt after configuration changes.
- Deepgram timeout, rate limit, unavailability, and terminal failure while preserving the WAV.
- Stable job ownership preventing an older transcription execution from becoming current after a newer retry.
- Correlated provider cost, audio duration, latency, and trace metadata without audio, transcript, credentials, or provider payload leakage in logs.

### 3E acceptance criteria

- Every accepted dataset or manual WAV can enter one private asynchronous transcription path.
- A successful call produces an auditable transcript with ordered timestamped speaker segments and provider metadata.
- Speaker and confidence metadata remain informative without being misrepresented as verified participant identity or product confidence.
- Only a valid current transcript can enter the shared extraction contract.
- Call evidence can be cited through verified transcript segments and time ranges.
- Compatible successful transcripts are reused safely, while changed configurations produce append-only attempts.
- Transcription and extraction failures preserve the WAV, remain recoverable, and cannot create unsupported canonical facts.

## 7. Step 3F — Carrier/Load Matching and Inquiry Reconciliation

### Product goal

Turn a validated extraction proposal into one or more evidence-backed inquiry cards connected to the most defensible carrier and load candidates. Exact facts should resolve automatically, uncertainty should remain visible, and the broker must be able to correct a match without changing the original email, call, transcript, or machine output.

Reconciliation links each communication-derived inquiry into the shared Load Workspace and Carrier Profile. It does not merge or delete the underlying communications and does not decide carrier eligibility or ranking.

### Inputs and outputs

| Area | Primary records |
|---|---|
| Valid machine proposal | Current validated `ExtractionRun` and owning `CommunicationEvent`/`IngestionJob` |
| Evidence source | `EmailContent` or current `Transcript` and `TranscriptSegment` rows |
| Candidate data | Active-snapshot `Carrier`, `CarrierContact`, equipment/lane data, `Load`, and `Lane` records |
| Canonical inquiry | `Inquiry`, `InquiryIntent`, `InquiryQuestion`, `CarrierQuote` |
| Evidence and assessment | `EvidenceSpan`, `InquiryFieldAssessment`, `InquiryFieldEvidenceLink` |
| Resolution | `InquiryCarrierMatch`, `InquiryLoadMatch`, `InquiryReviewReason` |
| Broker correction | Append-only `InquiryReviewAction` |

All canonical inquiry, evidence, match, quote, assessment, and review-reason changes for one communication — including the Step 3G candidate, compliance, and eligibility assessments and the Step 3C terminal job transition — are persisted in **one finalization transaction**. Everything inside it is local typed data with no provider calls, so a single transaction is defensible at P0 scale. A failure rolls back the complete reconciliation rather than leaving a partially usable card.

### High-level reconciliation flow

1. Verify that the extraction is current, schema-valid, evidence-grounded, and belongs to this job — a compatible extraction deliberately reused from an earlier generation under the Step 3D rules qualifies; generation-level ownership applies to the write fence, not to reuse.
2. Create verified email-offset or transcript-time `EvidenceSpan` records from the validated pointers.
3. Normalize the proposed inquiry fields and create the ordered `Inquiry`, intent, question, quote, and field-assessment records.
4. Generate carrier and load candidates using deterministic snapshot data and controlled matching priorities.
5. Select only a defensible candidate, attach review reasons for uncertainty or conflict, and keep selected inquiry foreign keys consistent with the selected match rows.
6. Compare extracted content with untrusted dataset annotations or manual hints and surface meaningful conflicts without treating those values as truth.
7. Derive the inquiry review state and allow the owning job to finalize through the Step 3C rules.

P0 normally creates one inquiry per communication, but the same transaction supports several inquiries ordered by `sequence_number`. Repeated execution for the same extraction is idempotent and does not duplicate canonical records.

### Carrier matching rules

Carrier candidates follow the Step 2C priority: exact normalized MC number, exact DOT number, exact email, exact phone, multiple consistent company/contact signals, then weak name similarity.

- A unique exact candidate with no contradiction may be selected and verified automatically.
- A strong candidate may be proposed or selected only when multiple independent signals consistently identify one carrier and the controlled policy permits it.
- Weak name similarity creates a review candidate and can never verify identity by itself.
- Conflicting exact signals—for example, an MC number belonging to one carrier and an email belonging to another—remain conflicting and require broker review.
- Manual suspected-carrier hints and untrusted dataset annotations may propose weak candidates or explain a conflict, but they cannot verify or override an evidence-based match.
- Candidate lookup is confined to the communication's dataset snapshot so records from another snapshot cannot leak into the result.

### Load matching rules

Load candidates follow the Step 2C priority: exact valid external load ID; corrected or partially garbled reference with strong supporting evidence; consistent lane, equipment, and pickup-date signals; then weak lane or equipment similarity.

- A unique exact load reference with no contradiction may be selected and verified automatically.
- A corrected or partially garbled reference remains reviewable unless the complete controlled signal set establishes a single defensible candidate.
- Lane or equipment alone is weak and cannot verify a load.
- Conflicting reference, lane, equipment, or date evidence creates candidate rows and review reasons rather than a silent best guess.
- A manual suspected-load hint is only a weak proposal and cannot satisfy load evidence.

The product shows categorical match tiers—exact, strong, weak, or conflicting—together with verified, needs-review, or unmatched status. No arbitrary numeric LLM confidence score is shown.

### Inquiry facts, evidence, and quotes

Each significant field receives an `InquiryFieldAssessment` with explicit, inferred, missing, or conflicting status and links to supporting or contradicting `EvidenceSpan` records. A missing field has no invented evidence, while a conflict preserves every relevant span.

Every monetary mention is persisted with its extracted semantic role. Only explicit current carrier quotes or counteroffers can participate in later best-rate calculations; broker references, ambiguous amounts, inferred values, and missing values remain visible but ineligible.

Within one communication, a clearly later carrier statement may supersede an earlier position—for example, “not $240; our floor is $280.” Across communications, supersession requires reliable business chronology. When one source lacks a real `occurred_at`, as with dataset calls, cross-source ordering is unresolved and the competing observed quotes require review rather than being silently ordered by import time.

### Review state and broker correction

Critical identity ambiguity, unmatched required entities, conflicting evidence, garbled references, uncertain critical transcript evidence (`low_confidence_transcript_evidence`), and unresolved quote chronology (`quote_chronology_unresolved`) produce `review`-severity reasons and therefore `needs_review`. Purely informational annotation divergence (`metadata_content_conflict`) carries `informational` severity and remains a visible warning without blocking an otherwise defensible inquiry — the dataset's 15 planted equipment-annotation conflicts must surface as warnings, not flood the review queue. Conflicting exact identity signals record `conflicting_carrier_identity` or `conflicting_load_reference`.

The review page lets the broker approve or reject the extraction or correct the carrier/load resolution. A correction locks the inquiry, records an append-only `InquiryReviewAction`, creates or selects a broker-verified match, updates the canonical selected relationship, and triggers later deterministic reassessment. It never rewrites raw evidence or `ExtractionRun` output and cannot override a compliance blocker.

P0 does not silently reprocess completed or reviewable inquiries when a prompt changes. Failed jobs may retry before canonical completion, and broker corrections use the explicit review workflow.

### Tests

P0 tests cover:

- Atomic and idempotent creation of inquiry, evidence, assessments, quotes, candidates, and review reasons.
- One and multiple inquiry proposals with deterministic sequence numbers and navigation.
- Exact MC, DOT, email, phone, and load-ID matches.
- Strong multi-signal carrier/load candidates, weak name/lane candidates, and conflicting exact signals.
- Manual hints and dataset annotations never verifying or overriding a match.
- Snapshot isolation during every candidate lookup.
- Selected inquiry carrier/load values remaining consistent with selected candidate rows.
- Explicit, inferred, missing, and conflicting field assessments with valid evidence links.
- Correct carrier quote, counteroffer, broker-reference, accepted-rate, and ambiguous-rate handling.
- Same-communication quote supersession and unresolved cross-source chronology when reliable timestamps are absent.
- Required-review versus informational-warning outcomes and correct job finalization.
- Auditable broker approval, rejection, carrier correction, and load correction without changing source evidence or extraction output.
- Transaction rollback leaving no partial inquiry card.
- The 15 dataset equipment-annotation conflicts producing informational warnings, not `needs_review`.
- Synthetic fixtures for conflicting exact identity signals and rate-annotation divergence, since the real dataset never exercises those branches.

### 3F acceptance criteria

- Every validated extraction can become one or more atomic, evidence-backed inquiry cards.
- Exact carrier/load evidence resolves automatically while weak, unmatched, or conflicting evidence remains reviewable.
- Manual hints and dataset annotations never become verified business facts.
- Every significant inquiry field and quote remains traceable to stable email or call evidence.
- Quote roles and chronology prevent ambiguous or outdated amounts from becoming a false best rate.
- Broker corrections are explicit and auditable without destroying machine output or raw evidence.
- Reconciliation is idempotent, snapshot-isolated, and incapable of leaving partial canonical state.

## 8. Step 3G — Eligibility, Ranking, and Rate Decisions

### Product goal

Help the broker understand which carriers are eligible for a load, which are blocked or incomplete, who has the lowest defensible rate, and which eligible candidate is strongest overall. These are deterministic and explainable product decisions; AI may extract evidence but never determines compliance, eligibility, best rate, or carrier ordering.

Rate intelligence is embedded in the Load Workspace for P0. It uses the supplied historical data as context and does not claim to be a live market-pricing or predictive-rate service.

### Inputs and outputs

| Area | Primary records |
|---|---|
| Candidate history | `CarrierLoadCandidate`, `CandidateInquiry`, resolved `Inquiry` and current `CarrierQuote` records |
| Load requirements | `Load`, `Lane`, required equipment, pickup date/window, distance, offered rate |
| Carrier facts | `Carrier`, equipment relationships, authority, safety, insurance, onboarding, reliability and history |
| Market context | `MarketRateHistory` for the load's lane, equipment, and pickup date |
| Decisions | Immutable `ComplianceAssessment`, `EligibilityAssessment`, `CandidateAssessmentReason` records and current assessment pointers |

An inquiry without both a resolved carrier and load remains visible as unmatched and does not create a `CarrierLoadCandidate`. All inquiries resolved to the same carrier/load pair contribute to one candidate history through supporting, superseding, or conflicting links.

### Current candidate facts

Current availability, conditions, and quotes are derived from explicit, chronologically comparable evidence. A later explicit statement may supersede an earlier one; omission never erases a previous explicit statement.

Events are comparable only when both have reliable `occurred_at` values or when later content explicitly identifies itself as a correction. Import or `received_at` order never proves business chronology. Unresolved contradictory evidence moves the candidate to clarification and prevents a conflicting quote from becoming current.

### Compliance assessment

Compliance uses a versioned code-owned policy, initially `goodlane_demo_eligibility_v1`. A policy change requires code, tests, a new version, and reassessment; prompts and AI cannot change the rules.

The assessment evaluates:

- Authority: policy v1 recognizes `ACTIVE` (pass); `INACTIVE`, `REVOKED`, and `SUSPENDED` (fail); `CONDITIONAL` (unknown — requires review, never silently blocked or passed); null or unrecognized (unknown). The dataset's two `CONDITIONAL` carriers (Mountain State Transport MC 1198743, FRONTIER HAULING LLC MC 885432) are named test cases.
- Safety: `Satisfactory` passes, `Unsatisfactory` fails, `Conditional` is unknown, and null or unrecognized is unknown.
- Insurance: expiry on or after the load pickup date passes, earlier expiry fails, and missing expiry is unknown.

Any explicit component failure makes compliance fail. With no failure, an unknown required component produces needs review; all required components passing produces pass. Insurance is compared with the load pickup date—not the real system date or demo as-of date. Onboarding remains outside compliance so a non-onboarded carrier is not described as unsafe.

Carrier free-text `notes` are context only: they are never a compliance or eligibility input, and no deterministic rule parses them. They are surfaced verbatim on the Carrier Profile and the Load Workspace candidate card so directives such as "Do not book until verified" appear beside the deterministic status for the broker to weigh. The dataset's do-not-book carriers land in review or blocked states through structured fields alone; the visible note makes the reasoning obvious rather than accidental.

### Eligibility assessment and status

Eligibility combines verified identity, compliance, exact P0 equipment compatibility, availability, important conditions, and onboarding. Each assessment preserves an immutable snapshot of the facts and policy used.

Final status follows this precedence:

1. `Blocked` — explicit compliance failure, equipment mismatch, or explicit unavailability.
2. `Needs compliance review` — no blocker, but authority, safety, or insurance is unknown.
3. `Needs clarification` — compliance is acceptable, but equipment, availability, chronology, or an important condition is missing, conditional, or conflicting.
4. `Needs onboarding` — the carrier otherwise qualifies but is known not to be onboarded.
5. `Eligible` — identity, compliance, equipment, and availability pass and the carrier is onboarded.

Conditional availability passes only when deterministic code can prove that the condition fits the normalized load pickup window. Equipment substitution is never assumed in P0.

A quote is not required for eligibility. An eligible carrier without a comparable current quote remains visible but cannot win a best-rate result. A clearly blocked carrier is a successful deterministic product outcome rather than a failed ingestion job; review statuses require broker attention, while eligible and clearly blocked outcomes may complete normally.

### Market-rate context

The Load Workspace selects the latest `MarketRateHistory` row whose week is on or before the load pickup date and whose directional lane and equipment exactly match the load. If no defensible row exists, the page explains that historical context is unavailable instead of substituting another lane or equipment type.

The page may show:

- Load offered all-in rate and calculated rate per mile when rate and distance are known.
- Latest historical minimum, average, and maximum rate per mile.
- The offered rate's computed position relative to the historical band — below minimum, within the band, or above maximum — whenever both the offered per-mile rate and the band exist. This makes a deliberately underpriced load a stated fact rather than something a reader might gloss over.
- Comparable carrier quotes and their difference from the offered and historical rates.
- Clear missing or incompatible-basis labels when a comparison cannot be made.

Historical rates provide context only. They are not proof of today's market price and do not automatically set a recommended negotiation rate.

### Best rate and strongest candidate

Best-rate queries calculate two separate values at read time:

- Lowest quoted rate overall: the lowest comparable explicit current carrier quote or counteroffer, displayed with that candidate's eligibility status.
- Lowest eligible quoted rate: the lowest comparable explicit current quote or counteroffer from an `Eligible` candidate.

All-in quotes compare directly. Conversion works in both directions and only when the load distance is known and positive: a per-mile quote may be converted to all-in, and an all-in quote may be expressed per-mile for comparison against per-mile market history — the direction that dominates this dataset, where every quoted amount is all-in and all market context is per-mile. When distance is unknown, the quote-versus-history comparison shows the incompatible-basis label instead. Hourly, unknown-basis, incompatible-currency, ambiguous, inferred, broker-reference, and chronology-conflicted amounts remain visible but are excluded from a lowest-rate comparison.

Best carrier is a different question. Deterministic ordering first requires verified identity and `Eligible` status, then prefers:

1. A comparable explicit current quote.
2. Lower quoted amount.
3. Higher known reliability.
4. Greater completed-load history.
5. Faster known response time.
6. Stable carrier identifier as the final tie-breaker.

Missing performance values sort after known values and never become zero. No `is_best_rate`, `is_best_carrier`, or opaque weighted score is stored. The UI explains the facts and tradeoffs and never claims the carrier was selected, contacted, or booked; the broker remains the final decision-maker.

### Reassessment and failure behavior

A new immutable assessment is created when identity is corrected; availability, equipment, or conditions change; carrier authority, safety, insurance, or onboarding changes; load equipment or pickup timing changes; or the policy version changes. Quote-only changes update commercial ordering and require a new eligibility assessment only when they change an eligibility-relevant ambiguity or condition.

These calculations run synchronously during deterministic finalization because they use local typed data and make no provider calls. An unexpected calculation or persistence error fails the current ingestion execution without publishing a partial assessment. Missing business data produces an explainable review state rather than a technical failure.

### Tests

P0 tests cover:

- Candidate aggregation and supporting, superseding, and conflicting inquiry history.
- Reliable chronology, explicit correction, omission behavior, and unresolved cross-source conflicts.
- Authority, safety, and insurance pass/fail/unknown outcomes, including insurance exactly on the pickup date.
- Compliance precedence and separation of onboarding from safety/compliance.
- Equipment match/mismatch/unknown and availability confirmed/unavailable/conditional/missing/conflicting.
- Exact final-status precedence for blocked, compliance review, clarification, onboarding, and eligible.
- Eligible candidates without quotes and blocked candidates remaining successful product outcomes.
- Exact lane/equipment market-history lookup and latest week on or before pickup, including unavailable context.
- Offered and quoted rate-per-mile calculations, comparable conversion in both directions (per-mile to all-in and all-in to per-mile), the offered rate's band position (below minimum, within band, above maximum — including load 29372501 as the below-minimum named case), and exclusion of incompatible bases or currencies.
- Lowest overall rate remaining separate from lowest eligible rate.
- Strongest-candidate ordering, deterministic ties, and missing performance values sorting after known values.
- Immutable policy/fact snapshots, reassessment triggers, current pointers, and atomic rollback.
- Proof that no AI/provider call or stored best flag participates in compliance, eligibility, rate, or ranking decisions.

### 3G acceptance criteria

- Every resolved carrier/load candidate receives an explainable versioned compliance and eligibility result.
- Explicit blockers remain distinct from unknown compliance, missing operational information, and onboarding work.
- Every decision cites the facts, policy version, and reasons that produced it.
- Market context uses only defensible matching historical data and clearly reports when it is unavailable.
- Best overall rate, best eligible rate, and strongest carrier remain separate deterministic answers.
- Ambiguous, non-comparable, or chronologically unresolved quotes cannot become a false best rate.
- Missing data is never converted to zero, and no AI model or opaque score controls eligibility or ranking.
- The product recommends nothing automatically and leaves final carrier selection to the broker.

## 9. Step 3H — Drafting and Assistant Tools

### Product goal

Let the broker ask operational questions and prepare grounded carrier responses without manually combining load, carrier, inquiry, compliance, and rate information. The system explains and drafts; it never sends an email, changes master data or compliance, contacts a carrier, books capacity, or chooses a carrier for the broker.

Drafting and assistant requests are bounded synchronous Django operations so the user receives an immediate result or a visible failure. They do not use Celery and cannot continue after the web request times out.

### Inputs and outputs

| Area | Primary records |
|---|---|
| Grounded context | `Load`, `Inquiry`, `CarrierLoadCandidate`, `Carrier`, current quotes and assessments, `EvidenceSpan`, `MarketRateHistory` |
| Drafting | `DraftResponse`, `DraftEvidenceLink`, `AIOperation`, `AIProviderCall` |
| Assistant | `AssistantConversation`, `AssistantMessage`, `AssistantRun`, `ToolExecution`, `AssistantCitation`, `AIOperation`, `AIProviderCall` |
| Observability | Prompt/model identity, Langfuse trace, latency, token usage, estimated cost, and safe errors |

Only authenticated users may create drafts or conversations. Every operation records the active snapshot and relevant load/inquiry scope so later review can reconstruct what the model was allowed to see.

### Response drafting

The broker selects a current inquiry and one controlled draft type: provide rate, negotiate rate, request information, confirm next steps, decline, or defer. Application code builds a bounded immutable context from the selected inquiry, load, carrier, current quote, assessment reasons, missing information, and supporting evidence.

The generated result contains an editable subject and body. The original generated content and context snapshot remain preserved when the broker edits, copies, discards, or regenerates a draft. `DraftEvidenceLink` connects significant facts used in the draft to stable inquiry, load, carrier, quote, assessment, email, call, or evidence-span identifiers.

Draft rules are:

- Respect deterministic blockers and never imply that a blocked or uncertain carrier is approved.
- Use only current comparable rates and clearly label negotiation positions.
- Ask for missing information rather than inventing it.
- Exclude internal notes unless the application explicitly marks a field safe for the selected draft.
- Never claim that an email was sent, a carrier was contacted, a rate was accepted, or capacity was booked.
- Provide no `sent` state. P0 supports edit and copy; a mail-client link remains optional and cannot record delivery.

Draft generation uses the configured drafting prompt/model and one bounded structured provider request. A provider timeout or invalid result produces a visible failed operation and no misleading partial draft.

### Assistant scope and supported questions

The assistant is one global product capability available from the Dashboard, Unified Inbox, and Load Workspace. Opening it from a load creates load scope; opening it elsewhere permits approved cross-load and lane-level questions within the active snapshot.

It supports questions such as:

- Which carriers are available or eligible for this load?
- Who offered the lowest overall and lowest eligible rate?
- Which carrier appears strongest overall, and why is that different from best rate?
- Which candidates are blocked, need compliance review, or need clarification?
- What information is missing or conflicting?
- Did a carrier ask about weight, pickup, equipment, or another load detail?
- Summarize responses for a load, lane, or equipment type.

Operational claims require current application retrieval. Conversation history may provide conversational context but cannot replace a fresh tool result for mutable load, carrier, quote, or assessment state: citation validation is scoped to the **current run's** successful tool executions, so an answer whose significant claims rest only on a prior turn's results fails validation and forces a re-fetch — a broker correction or reassessment between turns can therefore never be papered over by stale history.

### Read-only assistant tools

P0 exposes only these application-owned read tools:

- `get_load`
- `get_load_inquiries`
- `search_inquiries`
- `get_carrier_profile`
- `get_carrier_history`
- `get_candidate_assessment`
- `get_market_rate_context`

Tool schemas, authorization, scope checks, result limits, and deterministic queries are typed application code rather than prompt-controlled behavior. Tool arguments use strict schemas and are validated again before execution. The model receives no arbitrary SQL, shell, web access, write tool, or external-action capability.

Each call creates a `ToolExecution` with the validated arguments, outcome, safe summary, and stable result-record identifiers. Tool results return bounded structured facts and evidence identifiers. Emails, transcripts, and other untrusted source text remain data and cannot alter tool permissions, system rules, or deterministic decisions.

### Assistant execution and citations

1. Persist the authenticated user's message and create a running `AssistantRun` with an immutable scope snapshot.
2. Resolve the assistant prompt through the agreed Langfuse, last-known-good cache, and bundled emergency fallback sequence.
3. Call the configured model with only the tools allowed for the current scope.
4. Validate, authorize, execute, and record each requested tool call; return explicit tool failures to the model rather than fabricating a result.
5. Stop when the model produces a grounded answer or when the configured six-iteration/45-second application budget is reached.
6. Validate significant claims and citation identifiers against records returned by this run's successful tool executions; prior turns' tool results never satisfy validation.
7. Persist the completed assistant message and ordered `AssistantCitation` rows, or record a visible failed/timed-out run.

Significant availability, rate, eligibility, compliance, ranking, and recommendation claims require citations. The assistant must distinguish facts, deterministic decisions, missing data, conflicts, and explanatory inference. An unsupported or invalid citation may be corrected only within the remaining bounded loop; otherwise the run fails visibly rather than displaying an unsupported operational answer.

### Safety and failure behavior

- Tool results cannot override compliance or eligibility rules from Step 3G.
- Best rate and strongest carrier remain separate answers and retain their deterministic qualifiers.
- The assistant characterizes the load's offered rate relative to market only through the computed band-position fact from Step 3G (below minimum, within band, above maximum), never through its own judgment — an underpriced load must be reported as below market, not glossed.
- A tool error is explicit. The assistant may answer only the supported portion and must state what could not be retrieved.
- Provider rate limits and transient errors use retries only within the overall interactive time budget.
- Timeout changes the durable run/message state and stops processing; no hidden Celery continuation occurs.
- Langfuse delivery failure does not invalidate an otherwise successfully persisted product answer or draft.
- Hidden reasoning, private prompts, secrets, and unrestricted raw tool/provider payloads are never shown in the product UI or application logs.
- Every answer and draft remains advisory. P0 has no tool or endpoint for email sending, carrier booking, compliance changes, onboarding changes, inquiry approval, or master-data edits.

### Tests

P0 tests cover:

- Every draft type with correct inquiry/load/carrier context and stable evidence links.
- Draft behavior for blockers, missing information, ambiguous rates, and conflicting evidence.
- Generated versus edited content, copy/discard status, immutable context, and absence of a sent state.
- Global and load-scoped conversations with active-snapshot isolation and authorization.
- Strict tool argument validation, bounded results, deterministic queries, and one `ToolExecution` per call.
- Required availability, compliance, missing-information, best-rate, strongest-carrier, cross-load, and lane-level questions.
- Correct distinction between lowest overall rate, lowest eligible rate, and strongest candidate.
- Significant claims requiring valid citations returned by successful tools.
- Tool failures, invalid citations, provider failures, and timeouts producing safe visible outcomes without fabricated facts.
- Six-iteration and 45-second bounds, including proof that processing does not continue in Celery.
- Untrusted email/transcript instructions being unable to expand tools, scope, or permissions.
- Proof that no assistant or drafting path can send, book, approve, modify compliance/onboarding, or edit master data.
- Prompt/model/version, cost, latency, tool calls, and trace correlation without sensitive logging or hidden-reasoning display.

### 3H acceptance criteria

- The broker can generate, edit, copy, and discard grounded response drafts without the application sending them.
- The assistant answers core load, carrier, rate, compliance, missing-information, and lane-level questions through approved read-only tools.
- Significant operational claims cite stable application evidence and preserve uncertainty and blockers.
- Tool schemas, permissions, scope, and deterministic decisions remain application-controlled rather than prompt-controlled.
- Interactive operations always complete, fail, or time out within explicit bounds and leave durable audit records.
- Best rate, best eligible rate, and strongest carrier remain distinct, explainable conclusions.
- No draft, assistant answer, tool, or UI state implies that an external action occurred.

## 10. Step 3I — Langfuse Observability, Operational Metrics, and Offline Evaluation

### Product goal

Make every AI-assisted result explainable in the interview and measurable during development: which prompt/model ran, what provider calls occurred, how long and costly they were, which tools and evidence supported the result, where failures occurred, and whether a prompt change improved or regressed quality.

The Goodlane Dashboard shows only executive operational metrics. Detailed prompts, traces, tool calls, generations, costs, experiment comparisons, scores, and failure analysis live in Langfuse Cloud and the reproducible local evaluation report. Langfuse is observability infrastructure, not the product system of record, and its outage cannot stop ingestion, review, drafting, or assistant workflows.

### Records and surfaces

| Area | Primary records or surface |
|---|---|
| Operational usage | `AIOperation`, `AIProviderCall` |
| Evaluation | `EvaluationRun`, `EvaluationCaseResult`, `EvaluationScore` |
| Product summary | Dashboard queries over PostgreSQL records |
| Detailed AI inspection | Langfuse traces, observations, prompt versions, datasets, experiments, and scores |
| Reproducible artifact | Version-controlled gold data and generated local evaluation report |

`AIOperation` represents one logical transcription, extraction, draft, assistant turn, or evaluation case. Each actual provider request or retry creates an `AIProviderCall` with provider/model, prompt identity when applicable, timing, usage, estimated cost, pricing snapshot, status, safe error, correlation ID, and Langfuse identifiers.

### Trace structure and correlation

One Langfuse trace represents one self-contained unit of work:

- One ingestion pipeline execution for an email or call.
- One assistant turn; the conversation ID groups turns as a Langfuse session.
- One draft-generation request.
- One evaluation case; the evaluation run groups its case traces.

Stable named observations represent transcription, extraction, tool retrieval, deterministic reconciliation/assessment context, and final generation. Provider generations include the exact model, usage, cost, latency, prompt version, and output status; assistant tool observations are nested under the turn that requested them.

The same application correlation ID links PostgreSQL records, Celery/web logs, provider calls, and Langfuse. Traces include safe business dimensions such as environment, operational/evaluation category, operation type, origin, source channel, snapshot/version, prompt source, and stable record identifiers. Development, evaluation, and deployed-demo traces use distinct environments/tags so their metrics do not mix.

Every terminal AI operation performs a best-effort Langfuse flush before exposing a trace link. A trace link appears only when a safe identifier is available; otherwise the product links to the project or reports that detailed tracing is unavailable. Trace export or flush failure is recorded safely but never changes an otherwise successful business result.

### Prompt management and release

Langfuse Prompt Management remains the runtime source of truth. Extraction, assistant, and drafting generations link the exact resolved prompt object/version to their trace so quality, latency, and cost can be compared by prompt version.

Runtime resolution remains:

1. Prompt with the Langfuse `production` label.
2. Process-level last-known-good cached prompt.
3. Valid bundled emergency fallback from the repository.
4. Controlled AI-operation failure if no valid prompt exists.

Every provider call records prompt name, version or template hash, and source (`langfuse`, `cache`, or `local_fallback`). Tool schemas, extraction schemas, authorization, and deterministic policies remain code-owned and cannot be modified through a prompt.

Prompt changes follow a reviewed release flow: update and validate the bundled fallback, publish an immutable Langfuse candidate, run the representative offline evaluation, compare it with the accepted baseline, promote the approved version to `production`, and record enough version/checksum information to verify fallback alignment. A candidate is not promoted when it introduces a critical unsupported claim, breaks deterministic schema/tool requirements, or materially regresses an agreed core metric without explicit review.

### Payload visibility and safety

Langfuse is intentionally the authorized detailed inspection surface for the POC. A deployment setting controls whether prompt inputs and outputs are captured. The interview demo enables payload capture so the reviewer can inspect extraction, tool, and drafting behavior; a future sensitive deployment can disable payload capture while retaining identifiers, timing, model, usage, cost, status, and scores.

Application and Railway logs never contain prompt bodies, emails, transcripts, audio, credentials, full tool results, or provider responses. Langfuse credentials and provider keys are never added to trace metadata. Reviewer access uses the Langfuse Viewer role or presenter screen share rather than sharing credentials.

### Executive Dashboard metrics

The P0 Dashboard queries PostgreSQL directly and displays only decision-useful summary values:

- Total operational AI cost and cost by extraction, transcription, drafting, and assistant category.
- Average AI cost per completed inquiry.
- Token usage and processed audio minutes.
- Total inputs processed: communications ingested, counted distinctly from AI operations (one call input produces several AI operations).
- Operation success/failure counts and processing success rate.
- Average and p95 latency by operation type.
- Review volume and common safe failure/review categories.
- Prompt cache/local-fallback usage count.
- Active production prompt versions when known.
- Latest completed evaluation score/summary and completion time.
- Link to Langfuse when configured.

Evaluation usage and cost are displayed separately and excluded from operational totals. Metrics with no trustworthy denominator or source render as unavailable rather than zero. P0 needs no separate evaluation page, analytics warehouse, or materialized metric layer.

### Cost accounting

Provider-returned usage is preferred when available. Each `AIProviderCall` preserves the unit-price snapshot and pricing version used to estimate cost so historical values are not recalculated when pricing changes. OpenAI token usage and Deepgram audio duration/cost contribute to the owning `AIOperation`; retries remain separately visible.

Langfuse generation observations receive the exact model and available usage/cost data for detailed analysis. PostgreSQL remains authoritative for the executive dashboard and for separating operational, evaluation, and retry cost. All displayed amounts are labelled estimates unless an authoritative billed amount is available.

### Offline gold set and execution

The repository contains the fixed hand-labelled evaluation set under `evaluations/gold`, with schemas and reports in sibling directories. It uses representative dataset emails and calls only; manual and future simulation inputs are excluded. Labels are created by reading the actual email or listening to the WAV independently of unreliable dataset annotations such as supplied intent, equipment, or quoted-rate metadata.

The evaluation runner pins the dataset/checksum, Git commit, extraction schema, prompt version, model, compliance policy, evaluator configuration, and pricing snapshot. `make eval` is an explicit potentially paid workflow and never runs automatically in normal CI, deployment, startup, or seeding. CI runs only deterministic evaluator/schema tests with fixtures and mocked providers.

For call cases, the primary prompt experiment may reuse a versioned reviewed transcript to isolate extraction quality and avoid repeatedly purchasing transcription. A separately requested end-to-end mode starts from the WAV and evaluates transcription plus extraction. The report clearly identifies which mode produced each result.

### Evaluation scores and failure analysis

Deterministic evaluators cover load reference, carrier/MC identity, equipment, availability, intent, rate value and semantic role, evidence status, entity resolution, compliance result, required-field completeness, citation coverage, tool-selection correctness, and unsupported-claim rate. LLM-as-a-judge is limited to genuinely qualitative measures such as summary usefulness or draft appropriateness; human review remains available for disputed qualitative scores.

Every case records expected versus actual output, stage, duration, cost, trace ID when available, deterministic and qualitative scores, and controlled failure categories. Every failed case must explain:

- What was expected and what happened.
- Which stage failed.
- The likely failure category, such as transcription, identifier extraction, rate-role confusion, equipment normalization, entity resolution, evidence mismatch, unsupported claim, policy logic, or tool retrieval.
- What prompt, schema, normalization, matching, policy, dataset label, or test should be improved.

The local report is the required reproducible submission artifact. When Langfuse is configured, the runner also creates or reuses the evaluation dataset, emits one trace per case, attaches scores, and records the experiment/run identity so candidate prompt versions can be compared in the UI. Langfuse synchronization failure leaves the local run/report usable with a visible remote-sync warning.

### Tests

P0 tests cover:

- One logical `AIOperation` with one `AIProviderCall` per actual provider request/retry and correct correlation.
- Stable trace/observation names, assistant session grouping, prompt linkage, tags, environment, model, usage, cost, and safe identifiers.
- Langfuse, cache, and bundled prompt resolution plus controlled missing-prompt failure.
- Langfuse outage/flush failure never changing a successful product outcome.
- Payload-capture enabled/disabled behavior and absence of sensitive content from application logs.
- Cost aggregation, retry cost, pricing snapshots, operational/evaluation separation, and unavailable rather than invented metrics.
- Executive Dashboard totals, averages, latest evaluation summary, prompt versions, and fallback counts.
- Gold-set schema, stable source IDs, independent labels, and exclusion of manual/simulation data.
- Reproducible pinned evaluation configuration and no paid provider calls from normal CI.
- Deterministic metric calculations, bounded qualitative judging, and call extraction versus end-to-end modes.
- Per-case failure analysis and required suggested improvement in the generated report.
- Optional Langfuse dataset/experiment/score synchronization with a usable local report when remote sync fails.

### 3I acceptance criteria

- Every AI workflow is traceable across product records, provider calls, logs, prompts, and Langfuse without making Langfuse a runtime dependency.
- The Dashboard shows understandable operational cost, latency, success, review, prompt, and evaluation summaries without exposing detailed payloads.
- Prompt versions are linked to generations and promoted only through reviewed evaluation.
- Cost and usage distinguish operation categories, retries, and evaluation from normal demo activity.
- The fixed evaluation workflow is independent, reproducible, version-pinned, and excluded from automatic CI/deployment spend.
- Every evaluation failure explains the expected/actual difference, failing stage, likely category, and proposed improvement.
- Langfuse provides the detailed interview surface for traces, tool calls, prompt comparisons, experiment scores, and cost while the local report remains the durable submission artifact.

## 11. Step 3J — Django Project Structure and Admin Inspection

### Goal

Keep the POC understandable and maintainable without concentrating unrelated behavior in large Django modules. Code is organized first by business domain and then by responsibility. Django Admin provides an authenticated inspection surface for every P0 record so the developer can follow data from import or manual submission through AI processing, inquiry reconciliation, eligibility, drafting, assistant execution, and evaluation.

This section defines organization and discoverability rather than prescribing implementation commits. The coding agent owns the detailed implementation sequence while preserving the agreed domain and service boundaries.

### Project-level structure

```text
config/
    settings/
    urls.py
    celery.py
    wsgi.py

apps/
    common/
    accounts/
    dashboard/
    freight/
    ingestion/
    inquiries/
    assistant/
    evaluation/

templates/
static/
prompts/
evaluations/
    gold/
    schemas/
    reports/
goodlane-dataset/
manage.py
```

Domain ownership is:

| Django app | Responsibility |
|---|---|
| `common` | Shared base types, errors, time/correlation utilities, and infrastructure that has no business-domain owner |
| `accounts` | Demo authentication and authenticated-user concerns |
| `dashboard` | Executive metrics and product landing views |
| `freight` | Dataset snapshots, imports, loads, lanes, carriers, contacts, equipment, and market-rate history |
| `ingestion` | Communications, email/audio details, jobs, storage, transcripts, extraction, and provider ingestion adapters |
| `inquiries` | Canonical inquiries, evidence, quotes, matching, candidate history, compliance, eligibility, ranking, and broker review |
| `assistant` | Drafts, conversations, messages, runs, read-only tools, executions, and citations |
| `evaluation` | Evaluation runs, case results, scores, evaluators, and report orchestration |

An app creates only the packages it needs. Empty boilerplate packages are not required, and generic catch-all files such as `utils.py` should be replaced with specifically named modules owned by the appropriate domain.

### Internal Django app structure

A substantial app follows this pattern:

```text
apps/ingestion/
    __init__.py
    apps.py
    urls.py
    migrations/

    models/
        __init__.py
        communication_event.py
        email_content.py
        call_recording.py
        ingestion_job.py
        transcript.py
        transcript_segment.py
        extraction_run.py

    admin/
        __init__.py
        communications.py
        jobs.py
        transcripts.py
        extractions.py

    forms/
    views/
    services/
    selectors/
    tasks/
    schemas/
    integrations/
```

Each Django model has a clearly named file inside its app's `models` package rather than being placed in a large `models.py`. `models/__init__.py` imports and re-exports every model so Django's app registry and migration framework discover them normally. Model relationships may use Django string references where needed to avoid circular imports.

The same responsibility rule applies to other packages:

- `views` is organized by product page or workflow, such as manual email, manual audio, job status, Inbox, Load Workspace, and Inquiry Review.
- `services` owns state-changing application use cases and transaction boundaries.
- `selectors` owns reusable read-only queries for pages, assistant tools, and metrics.
- `tasks` owns Celery entry points and passes only stable record identifiers to services.
- `schemas` owns internal commands, provider contracts, AI structured outputs, and tool argument/result shapes.
- `integrations` owns OpenAI, Deepgram, Langfuse, and storage adapters behind application interfaces.
- Django forms own browser-input validation.

P0 does not create a Django REST Framework `serializers` package because it has no REST API. A serializers layer is added only when an actual external API contract requires it.

### Admin coverage

Every P0 model is registered in Django Admin, including:

- Dataset snapshots and import batches.
- Loads, lanes, carriers, contacts, equipment, preferred lanes, and market rates.
- Communications, emails, call recordings, ingestion jobs, transcripts, segments, and extraction runs.
- Inquiries, intents, questions, evidence, assessments, quotes, carrier/load matches, and review reasons/actions.
- Carrier-load candidates, inquiry history, compliance and eligibility assessments, and assessment reasons.
- Drafts and draft-evidence links.
- Assistant conversations, messages, runs, tool executions, and citations.
- AI operations and individual provider calls.
- Evaluation runs, cases, and scores.

Each useful admin list exposes compact operational columns such as stable identifier, origin/source, status, related job/load/carrier/inquiry, prompt/model identity when applicable, cost/latency when applicable, and lifecycle timestamps. Admin configuration supplies relevant search, filters, ordering, pagination, `select_related`/`prefetch_related`, and links to important related records so inspection does not create avoidable N+1 query behavior.

Large child collections such as transcript segments, provider calls, evidence links, and evaluation cases use linked filtered lists or paginated views rather than unbounded inlines. Small one-to-one or bounded child records may use admin inlines when they materially improve inspection.

### Admin visibility and safety

Django Admin is a developer/interviewer inspection surface, not an alternate broker workflow or unrestricted database editor.

- Imported reference data, immutable source evidence, transcripts, extraction runs, evidence, AI operations/provider calls, assessments, review history, and evaluation results are read-only in admin.
- Delete actions are disabled for immutable, audit, and evidence records.
- Broker corrections and approvals continue through Inquiry Review so domain invariants, review actions, reconciliation, and reassessment remain intact.
- Large email bodies, transcripts, safe provider metadata, and JSON results appear escaped, formatted, and read-only on detail pages rather than flooding list pages.
- Audio is accessed through the authenticated application playback endpoint rather than a public bucket URL.
- Credentials, private storage locations, secrets, hidden prompts, and unrestricted raw provider payloads are never rendered by admin configuration.
- Admin access requires an explicitly created Django superuser. Demo credentials come from the approved environment/setup workflow and are never hard-coded or created as an import/startup side effect.

The admin site may use lightweight Goodlane branding and clear model grouping, but visual customization cannot delay the functional P0 product.

### Step 3 completion

Steps 3A through 3J now define the P0 ingestion boundaries, normalization, orchestration, email and call processing, reconciliation, deterministic decisions, assistant/drafting behavior, observability/evaluation, Django organization, and admin inspection surface. The P1 simulator remains intentionally deferred. Step 3 is complete and ready to guide implementation.

---
