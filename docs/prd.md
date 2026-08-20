# Goodlane Freight Carrier Agent

## Product Requirements Document

**Status:** Product scope approved; technical design pending  
**Document version:** 1.2  
**Prepared for:** Goodlane Founding Engineer technical exercise  
**Primary user:** Freight broker or dispatcher  
**Product type:** Low-traffic, production-minded demonstration application

---

## 1. Executive Summary

Freight brokers receive carrier responses through fragmented channels such as email, phone calls, and voicemail. They must manually determine which load a carrier is discussing, identify the carrier, extract availability and pricing, check equipment and compliance, compare the offer with market context, and decide how to respond.

The Goodlane Freight Carrier Agent will consolidate those communications into a load-centered workspace. It will ingest email and call data, normalize both channels into a shared inquiry format, retrieve carrier and market context, identify missing or risky information, answer broker questions, and draft responses. The broker will remain the final decision-maker.

The demonstration will use the provided Goodlane dataset as a simulated transportation-management system, inbox, call system, carrier directory, compliance source, and rate-history service. It will demonstrate a complete workflow from an existing open load through carrier review and response drafting. It will not book a carrier, send an email, or operate a shipment.

---

## 2. Product Context

### 2.1 Participants

#### Shipper

An organization that needs goods transported. The shipper provides the freight details and asks a broker to arrange transportation.

#### Freight broker or dispatcher

The primary product user. The broker receives the shipper's request, publishes or manages the load, finds a suitable carrier, evaluates risk and price, communicates with carriers, and makes the final selection.

#### Carrier

A trucking company or independent transportation provider that owns or operates the equipment and driver used to move the freight. Carriers respond to posted loads through email, calls, or voicemail.

#### Goodlane

The parent brokerage and technology platform supporting independent freight agents with operational infrastructure, compliance information, market context, and AI-assisted workflows.

### 2.2 Simplified Business Flow

1. A shipper asks a freight broker to move goods.
2. The broker creates or receives a load.
3. Carriers respond to the load through email or phone.
4. The broker evaluates availability, equipment, price, compliance, and reliability.
5. The broker negotiates with or confirms a carrier.
6. The selected carrier physically moves the goods.

This product addresses steps 3 through 5. The provided dataset begins after the load already exists.

---

## 3. Problem Statement

Carrier communications are unstructured and distributed across systems. Brokers currently spend significant time:

- Reading short or inconsistent carrier emails.
- Listening to calls and voicemails.
- Identifying carriers from names, email addresses, phone numbers, or MC numbers.
- Matching communications to loads.
- Extracting availability, equipment, questions, and rate quotes.
- Detecting conflicting or missing information.
- Checking authority, safety, insurance, and onboarding status.
- Comparing carrier quotes with the offered rate and historical market rates.
- Repeating the same context when drafting replies.

This manual process slows carrier selection, increases the chance of missed information, and makes it difficult for a broker to handle additional volume safely.

---

## 4. Product Goals

### 4.1 Primary Goals

1. Give a broker one workspace for an open load and its carrier communications.
2. Normalize inbound email and call information into a consistent carrier-inquiry structure.
3. Retrieve load, carrier, compliance, and market-rate context on demand.
4. Clearly distinguish eligible, blocked, uncertain, and incomplete carrier options.
5. Preserve source evidence and uncertainty instead of presenting unsupported conclusions.
6. Help the broker understand the best next action without removing human control.
7. Draft a grounded carrier response that the broker can review and copy.
8. Demonstrate measurable AI quality, cost, latency, tracing, and prompt versioning.

### 4.2 Demonstration Goals

1. Show a coherent broker workflow rather than a generic chatbot.
2. Demonstrate real email and audio processing using the provided dataset.
3. Show clear boundaries between AI interpretation and deterministic business rules.
4. Remain understandable and defensible during a technical walkthrough.
5. Keep the application easy to extend during the live interview.

---

## 5. Non-Goals for the MVP

The MVP will not:

- Receive real shipper requests.
- Post loads to a real freight marketplace.
- Connect to a live TMS.
- Connect to a live email inbox or phone system.
- Automatically send carrier emails.
- Automatically book or reject a carrier.
- Track the physical shipment.
- Handle proof of delivery.
- Handle billing, settlement, factoring, claims, or payments.
- Replace carrier-compliance professionals or broker judgment.
- Provide high-scale, multi-tenant infrastructure.
- Attempt a polished consumer-grade interface.

---

## 6. Dataset Scope

The provided dataset contains:

| Source | Records | Product role |
|---|---:|---|
| Carrier emails | 274 | Simulated inbound broker inbox |
| Carrier profiles | 48 | Simulated carrier master, compliance, and performance source |
| Loads | 50 | Simulated load board or TMS |
| Rate-history rows | 720 | Simulated market-rate context |
| Call recordings | 55 | Simulated calls and voicemails |

Load statuses include:

- 8 open loads.
- 16 covered loads.
- 22 delivered loads.
- 4 cancelled loads.

Known dataset conditions include:

- Rates embedded in message text while structured rate fields are empty.
- Inconsistent or incorrect equipment labels.
- Missing weight and pickup-window data.
- Missing or garbled MC and DOT numbers.
- Conditional or unknown carrier authority and safety status.
- Missing insurance-expiry information.
- Non-onboarded carriers.
- Terse emails, negotiations, questions, factoring messages, and problems.
- Rate-negotiation, availability, compliance, load-detail, and voicemail recordings.
- Carriers appearing through both email and phone.
- Low-confidence or conflicting information requiring human review.

---

## 7. Primary User Journey

1. The broker signs in using the supplied demo credentials.
2. The broker opens the Operations Dashboard.
3. The broker filters to open loads and selects one needing coverage.
4. The Load Workspace shows the load, carrier communications, market context, and attention items.
5. The broker reviews normalized carrier inquiries across email and calls.
6. The broker opens uncertain evidence when a field is missing or contradictory.
7. The system applies deterministic carrier-compliance and compatibility checks.
8. The broker asks the assistant which carriers appear suitable and why.
9. The assistant retrieves the relevant load, carrier, inquiry, compliance, and rate context using tools.
10. The assistant returns a grounded answer with evidence, uncertainty, and blockers.
11. The broker opens a carrier profile for additional history and compliance context.
12. The broker selects a carrier inquiry and generates a response draft.
13. The broker edits and copies the draft or opens it through a mail client link.
14. The broker remains responsible for any real-world decision or communication.

### 7.1 Manual Ingestion Demonstration Journey

1. The broker or interviewer opens the Manual Ingestion Lab.
2. The user pastes or uploads an email, or uploads a supported audio recording.
3. The application validates the input and creates an ingestion job.
4. The page shows a simple processing state while the job runs in the background.
5. For audio, the system transcribes the recording before structured extraction.
6. The system extracts normalized inquiry fields and attempts to match a carrier and load.
7. Deterministic validation checks identifiers, equipment, compliance, and data completeness.
8. The user reviews the result: extracted fields, confidence, warnings, and source evidence.
9. The completed result opens in Inquiry Review and, when matched, becomes visible in the related Load Workspace; unmatched or uncertain results land in the needs-review queue.
10. If matching is uncertain, the user can correct the carrier or load without losing the original machine output.

---

## 8. Final MVP Page Scope

### 8.1 Login

#### User sees

- Goodlane Freight Carrier Agent identity.
- Email and password fields.
- Clear validation errors.

#### User can

- Sign in using demo credentials.
- Sign out from any authenticated page.

#### Requirements

- Credentials must not be embedded in source code.
- Passwords must use the application's standard secure password handling.
- The demo account may be created from environment-provided credentials during deployment or setup.
- The application must provide a safe example environment file without real secrets.

### 8.2 Operations Dashboard

#### User sees

- Load counts by status.
- Open loads requiring attention.
- Route, equipment, pickup date, offered rate, inquiry count, and current status.
- Counts of eligible carriers, compliance warnings, and low-confidence records when available.
- A compact AI Operations summary.
- A control for the optional live simulation when enabled.

#### User can

- Filter loads by status.
- Search by load ID, route, or equipment.
- Open the Load Workspace.
- Open the broker assistant for cross-load and lane-level questions.
- Start the optional curated simulation.

#### AI Operations summary

The dashboard should show executive-level information only:

- Total inputs processed.
- Successful processing percentage.
- Records requiring human review.
- Average and p95 processing latency.
- Total estimated AI cost.
- Average AI cost per inquiry.
- Latest offline evaluation score.
- Active prompt or experiment version.
- Last evaluation time.
- Link to full Langfuse details when configured.

Missing metrics must display as unavailable; the application must not invent values.

### 8.3 Unified Inbox

#### User sees

- Email, call, and voicemail inquiries in a common list.
- Communication time, source, carrier, load, intent, processing state, and confidence.
- Unmatched or uncertain records.
- Processing errors or pending records.

#### User can

- Filter by communication source, intent, load, carrier, confidence, or processing state.
- Search by load ID, MC number, carrier name, email, or phone.
- Open the corresponding Load Workspace.
- Open an Inquiry Review.
- Open the broker assistant.
- Identify records requiring human attention.

#### Supported communication intents

- Availability confirmation.
- Rate inquiry.
- Rate counteroffer or negotiation.
- Request for load details.
- Compliance information.
- Factoring or payment information.
- Operational problem or exception.
- Terse or ambiguous response.

### 8.4 Load Workspace

The Load Workspace is the core product and demonstration page.

#### Load summary

The user sees:

- Load ID and status.
- Origin and destination.
- Distance.
- Equipment requirement.
- Weight, including missing status.
- Pickup and delivery dates and windows.
- Offered rate and rate per mile.
- Shipper and internal notes when available.

#### Market context

The user sees:

- Current offered rate.
- Offered rate per mile.
- Latest relevant historical average rate.
- Relevant historical minimum and maximum.
- Carrier quotes compared with market context.
- An explanation when sufficient matching history is unavailable.

Rate intelligence will be embedded in this workspace rather than implemented as a standalone MVP page.

#### Carrier candidates

For each carrier inquiry, the user sees:

- Carrier identity and MC number when known.
- Communication source.
- Availability.
- Equipment offered or mentioned.
- Current quoted rate when available.
- Relevant question or requested information.
- Compliance and compatibility status.
- Reliability and history when available.
- Extraction confidence.
- Access to original evidence.

Candidate statuses are:

- **Eligible:** Deterministic checks pass and required information is sufficiently complete.
- **Needs clarification:** The carrier may be suitable, but important information is missing or contradictory.
- **Blocked:** A deterministic rule prevents recommendation, such as unacceptable authority, safety, or equipment.
- **Unknown:** Available evidence is insufficient for a reliable determination.

#### Communication timeline

The user sees related emails, calls, transcripts, quotes, questions, and draft actions in chronological order.

#### Broker assistant

The assistant is a single global capability rather than a per-page feature. It is reachable from the Dashboard, the Unified Inbox, and the Load Workspace. When opened from a Load Workspace it is pre-scoped to that load; when opened elsewhere it answers cross-load and lane-level questions such as "Which carriers have confirmed availability for PA-NJ Box Truck loads this week?"

The user can ask questions such as:

- Which carriers are available for this load?
- Who offered the best rate?
- Which carriers pass compliance checks?
- What information is missing?
- Did anyone ask about weight or pickup details?
- Summarize the carrier responses.
- Which carriers require clarification before proceeding?

Assistant answers must:

- Retrieve relevant structured context using approved tools.
- Respect deterministic compliance decisions.
- Identify supporting email, call, transcript, load, carrier, or rate evidence.
- Clearly state uncertainty and missing information.
- Avoid claiming that a carrier was booked or contacted.

#### Response drafting

The broker can generate a draft to:

- Provide the offered rate.
- Negotiate a rate.
- Request missing information.
- Confirm next steps.
- Decline or defer an inquiry.

The user can edit and copy the draft. An optional mail-client link may be provided. The application will not send the email.

### 8.5 Inquiry Review

#### For an email

The user sees:

- Original subject and body.
- Sender and time.
- Original dataset metadata.
- AI-extracted structured fields.
- Confidence and warnings.

#### For a call or voicemail

The user sees:

- Audio playback.
- Transcript.
- Uncertain transcript regions when available.
- AI-extracted structured fields.
- Candidate carrier and load matches.
- Confidence and warnings.

#### User can

- Correct carrier identity.
- Correct load reference.
- Approve or reject the extraction.
- Replay audio or inspect raw email evidence.

Corrections should preserve the original machine output for auditability.

Field-level editing of equipment, availability, intent, rate, or question — and marking individual values unknown — is deferred to P1. The original message or transcript is always displayed beside the extracted fields, so an incorrect field value never hides the underlying evidence.

### 8.6 Carrier Profile

#### User sees

- Company and primary contact information.
- MC and DOT numbers.
- Equipment types and preferred lanes.
- Authority status.
- Insurance expiry.
- Safety rating.
- Onboarding status.
- Factoring and payment preference when available.
- Reliability score, completed-load count, and response time.
- Historical loads, emails, calls, quotes, and notes.
- Missing or contradictory information.

#### User can

- Understand why a carrier is eligible, blocked, or uncertain.
- Review communication and load history.
- Return to the related load or inquiry.

### 8.7 Manual Ingestion Lab

The Manual Ingestion Lab is a P0 demonstration and operational page. It allows the interviewer to submit new unstructured input and observe it pass through the same pipeline used for the provided dataset.

#### Email input

The user can:

- Paste sender, subject, and body content.
- Upload a supported email artifact when enabled.
- Optionally provide a suspected load reference or carrier identity without making either mandatory.
- Preview the input before processing.

The first implementation must support direct form entry. Support for `.eml` or additional email artifact formats may be added if time permits.

#### Audio input

The user can:

- Upload a supported audio recording.
- See accepted type, size, and duration limits before upload.
- Play back the uploaded source after successful validation.
- Submit the audio for background transcription and extraction.

The first implementation must support the dataset's WAV format. Additional audio formats are optional.

#### Processing status

The pipeline internally runs the same steps used for the preloaded dataset (validation, duplicate detection, transcription for audio, extraction, carrier and load matching, deterministic validation, persistence). The page, however, shows only a single job status:

- Queued.
- Processing.
- Completed.
- Needs review.
- Failed.

Status is persisted on one job record and read through simple polling, so progress survives a page refresh. Per-stage progress display, per-stage timings, and per-stage retry are deliberately out of scope for the MVP and deferred to P1. A failed job is retried as a whole.

The application must not display hidden chain-of-thought, private system instructions, secrets, or unrestricted raw tool payloads. Detailed trace inspection will remain available in Langfuse.

#### Completed result

After processing, the user sees:

- Original source evidence.
- Transcript for audio.
- Extracted carrier, load, availability, equipment, rate, intent, and questions.
- Match confidence and alternative candidates when applicable.
- Deterministic compliance and compatibility results.
- Links to Inquiry Review, the matched Load Workspace, the matched Carrier Profile, and the corresponding Langfuse trace when configured.

#### Error and uncertainty handling

The page must:

- Reject unsupported or oversized files with a clear explanation.
- Preserve valid uploads even when downstream processing fails.
- Allow safe retry of a failed job as a whole; per-stage retry is not required.
- Avoid duplicate inquiries when the same artifact is submitted repeatedly.
- Route uncertain matches to human review rather than silently guessing.
- Remain understandable when Langfuse is unavailable.

---

## 9. Data Processing Requirements

### 9.1 Email Processing

The system must:

- Ingest every provided email record.
- Preserve the original email content and metadata.
- Normalize MC and load identifiers without discarding the original values.
- Extract carrier identity, load reference, availability, equipment, rate, intent, and questions.
- Detect conflicts between provided metadata and message content.
- Preserve confidence and source evidence for extracted fields.
- Avoid treating an absent quote as a zero-dollar quote.

### 9.2 Audio Processing

The system must:

- Ingest every provided WAV recording.
- Preserve the original recording and metadata.
- Produce and store a transcript.
- Extract the same normalized inquiry fields used for email.
- Support garbled or corrected identifiers without silently guessing.
- Record transcription and extraction status independently.
- Allow failed or uncertain records to be retried or reviewed.

### 9.3 Cross-Channel Reconciliation

The system should:

- Match inquiries using MC number, email, phone, company, contact, load reference, and other available evidence.
- Recognize that one carrier may appear through both email and phone.
- Avoid merging records solely because of a weak name similarity.
- Preserve each source event even when events are associated with the same carrier and load.

### 9.4 Idempotency and Failure Handling

The system must:

- Avoid duplicating records when ingestion is rerun.
- Record processing errors.
- Support safe retry of failed external calls.
- Distinguish pending, successful, uncertain, and failed processing states.
- Remain usable when optional observability services are unavailable.

### 9.5 Manual Input Processing

The system must:

- Use the same normalization boundary for uploaded and preloaded records.
- Validate MIME type, extension, size, and audio duration using server-side checks.
- Assign an immutable source identifier or content fingerprint for duplicate detection.
- Persist a single durable job status so progress survives page refreshes.
- Run long transcription and extraction work outside the web request.
- Make the final normalized record available to the standard Inbox, Inquiry Review, Load Workspace, and Carrier Profile experiences.
- Keep any interviewer-provided data separate or clearly labeled so it does not contaminate the fixed offline evaluation dataset.

---

## 10. AI and Deterministic Responsibility Boundary

### 10.1 AI Responsibilities

AI may perform:

- Audio transcription.
- Unstructured field extraction.
- Intent classification.
- Natural-language question interpretation.
- Tool selection.
- Evidence-grounded summarization.
- Response drafting.
- Qualitative evaluation where deterministic scoring is insufficient.

### 10.2 Deterministic Responsibilities

Normal application logic must perform:

- Identifier normalization.
- Database retrieval and filtering.
- Date and insurance-expiry comparison.
- Authority and safety-rule enforcement.
- Equipment compatibility checks.
- Onboarding checks.
- Rate-per-mile calculation.
- Market-history lookup.
- Carrier eligibility status calculation.
- Exact-match evaluation metrics.
- Authentication and authorization.

### 10.3 Human Responsibilities

The broker must remain responsible for:

- Correcting uncertain information.
- Selecting a carrier.
- Approving a response.
- Sending any real communication.
- Booking or rejecting a carrier.
- Handling exceptions outside the available evidence.

---

## 11. Retrieval and Agent Requirements

The assistant must support tool-based retrieval for at least:

- Load details.
- Carrier inquiries associated with a load.
- Carrier inquiries filtered by lane, equipment type, and date range across loads.
- Carrier profile and history.
- Carrier compliance and compatibility status.
- Relevant historical market-rate context.

The exact tool implementation will be defined in the technical blueprint.

Agent responses must:

- Use retrieved data rather than unsupported model knowledge.
- Report when required information is missing.
- Never override deterministic blockers.
- Identify the evidence supporting significant claims.
- Handle tool errors gracefully.
- Avoid exposing internal secrets or raw system instructions.

---

## 12. Evaluation and Observability Requirements

### 12.1 Application Surface

The product will not include a standalone evaluation page in the MVP. The Dashboard will show only executive-level AI Operations metrics.

### 12.2 Langfuse Surface

Detailed prompt management, traces, generations, tool calls, experiment comparisons, costs, latency, and evaluation scores will be demonstrated through Langfuse during the interview.

The product must not depend on Langfuse availability for its primary workflow.

### 12.3 Offline Evaluation

The repository must include a reproducible offline evaluation dataset and runner for at least one core workflow.

The recommended primary workflow is normalized inquiry extraction from representative emails and calls.

Evaluation ground truth must be a hand-labeled gold set of approximately 30 emails and 10 calls, stored in the repository. Labels must be created by reading the raw message or listening to the audio, independently of the dataset's provided metadata fields. The dataset's `equipment_mentioned`, `intent`, and `rate_quoted_usd` fields are intentionally unreliable and must not be used as ground truth.

Expected fields include:

- Load reference.
- Carrier or MC identity.
- Availability.
- Equipment.
- Rate quote.
- Intent.
- Question or requested information.

Recommended deterministic metrics include:

- Exact-match accuracy for load ID and MC number.
- Rate extraction accuracy.
- Equipment accuracy.
- Availability accuracy.
- Intent accuracy.
- Required-field completeness.
- Compliance-decision accuracy.
- Tool-selection correctness where applicable.

Qualitative evaluators may assess:

- Groundedness.
- Unsupported claims.
- Usefulness of summaries.
- Appropriateness of response drafts.

Evaluation results should be reproducible locally. When Langfuse is configured, experiment traces and scores should also be available there.

### 12.4 Observability Data

Relevant AI operations should record, where available:

- Operation type.
- Prompt and model version.
- Input source and non-sensitive identifiers.
- Tool calls.
- Latency.
- Token or usage information.
- Estimated cost.
- Processing status.
- Error category.
- Evaluation scores.

Sensitive payload capture must be configurable.

---

## 13. Live Simulation - P1 Enhancement

The simulator is a high-value demonstration enhancement but is not required for MVP completion.

### 13.1 Initial State

The deployed application should preload:

- All carrier profiles.
- All market-rate history.
- Historical loads and processed inquiries.
- Most open loads.
- Previously generated transcripts and normalized records.

One curated load scenario may be reserved for simulation instead of partially loading the entire dataset.

### 13.2 Simulation Flow

When the broker selects **Start Live Simulation**:

1. A curated open load appears.
2. The user is taken to its Load Workspace.
3. Carrier email and call events arrive sequentially.
4. Each event passes through the same ingestion and normalization boundary as other data.
5. Pending and completed processing states become visible.
6. Carrier candidates and attention flags update as events are processed.
7. The broker asks the assistant to analyze the evolving load.
8. The broker generates a grounded response draft.

### 13.3 Simulation Constraints

- The simulation must be deterministic and resettable for demonstration purposes.
- It must not create duplicate records across repeated runs.
- UI updates may use simple polling; WebSockets are not an MVP requirement.
- Previously processed transcripts may be replayed to avoid dependence on external API latency during the interview.
- The project must accurately document what is replayed versus processed live.
- The simulator must not delay delivery of the core product.

---

## 14. Security, Privacy, and Reliability

The MVP must:

- Keep credentials and API keys outside source control.
- Provide example configuration without real secrets.
- Use secure password storage and normal session controls.
- Avoid logging secrets.
- Make external trace payload capture configurable.
- Document whether raw emails, transcripts, and carrier information are sent to external services.
- Use timeouts and bounded retries for external calls.
- Fail safely when transcription, LLM, or observability services are unavailable.
- Prevent AI outputs from directly booking or contacting carriers.
- Restrict manual uploads to authenticated users.
- Enforce conservative file-size and audio-duration limits.
- Store uploaded artifacts outside directly executable paths.
- Avoid trusting browser-provided MIME types without server-side validation.

---

## 15. Product Success Measures

The demonstration is successful when:

1. A broker can understand the state of an open load without manually opening every raw record.
2. Email and call inquiries appear in a consistent, evidence-backed format.
3. Compliance and compatibility problems are visible and explainable.
4. Market context is available within the load rather than requiring separate research.
5. The assistant answers core broker questions using retrieved data and tools.
6. The broker can inspect and correct uncertain AI output.
7. The broker can produce a useful response draft without the application sending it.
8. At least one core workflow has a visible, reproducible evaluation score.
9. Prompt, trace, latency, and cost information can be demonstrated through Langfuse.
10. The application remains functional if Langfuse is unavailable.

---

## 16. MVP Acceptance Criteria

### Authentication

- [ ] An interviewer can sign in using documented demo credentials.
- [ ] Secrets are excluded from the repository.
- [ ] Authentication uses secure password handling and sessions.

### Dataset and Ingestion

- [ ] The provided loads, carriers, rate history, emails, and recordings can be ingested.
- [ ] Rerunning ingestion does not create duplicate source records.
- [ ] Original email and audio evidence is preserved.
- [ ] Call recordings have stored transcripts or explicit processing errors.
- [ ] Email and call records produce a shared normalized inquiry representation.

### Broker Workflow

- [ ] The Dashboard shows and filters load statuses.
- [ ] The Unified Inbox displays email and call records with processing states.
- [ ] A broker can open a Load Workspace.
- [ ] The Load Workspace displays load, market, carrier, compliance, and communication context.
- [ ] The user can inspect raw evidence and extraction confidence.
- [ ] The user can review carrier compliance and history.
- [ ] The assistant answers at least the required availability and best-rate questions, including lane-level questions that span multiple loads.
- [ ] The assistant performs at least one explicit tool call.
- [ ] Significant recommendations include evidence and respect deterministic blockers.
- [ ] The broker can generate, edit, and copy a response draft.
- [ ] The application does not send or book anything.

### Manual Ingestion Lab

- [ ] An authenticated user can submit a carrier email through a manual form.
- [ ] An authenticated user can upload a supported WAV recording.
- [ ] Invalid or oversized input is rejected clearly.
- [ ] Long-running processing occurs outside the web request.
- [ ] The page shows a durable job status (queued, processing, completed, needs review, or failed) and recoverable errors.
- [ ] The completed result includes source evidence, extracted fields, confidence, and validation results.
- [ ] The completed inquiry appears in the normal product workflow.
- [ ] Repeated submission does not create duplicate source inquiries.
- [ ] The page exposes only safe result summaries without revealing hidden reasoning, secrets, or private instructions.
- [ ] A Langfuse trace link appears only when configured and available.

### Quality and Operations

- [ ] A small offline evaluation dataset covers one core workflow.
- [ ] Evaluation ground truth is hand-labeled and independent of the dataset's provided metadata fields.
- [ ] The evaluation runner reports scores and failed cases.
- [ ] Dashboard metrics show real values or an unavailable state.
- [ ] Langfuse integration is optional at runtime.
- [ ] When configured, prompts, traces, tool calls, latency, cost, and scores can be demonstrated in Langfuse.
- [ ] External-service failures produce visible, recoverable states.

### Delivery

- [ ] A working deployed application is available.
- [ ] Source code and meaningful version-control history are available.
- [ ] Setup, architecture decisions, assumptions, tradeoffs, evaluation, and limitations are documented.
- [ ] The application is straightforward to extend during the live technical session.

---

## 17. Prioritization

### P0 - Required MVP

- Login.
- Operations Dashboard.
- Unified Inbox.
- Load Workspace.
- Inquiry Review.
- Carrier Profile.
- Manual Ingestion Lab for email form input and WAV upload.
- Email ingestion and normalization.
- Audio transcription and normalization.
- Deterministic compliance and market-rate tools.
- Grounded assistant questions and answers.
- Response drafting and copy workflow.
- Offline evaluation for one core workflow.
- Executive AI Operations metrics.
- Optional-at-runtime Langfuse tracing and prompt management.
- Deployment and documentation.

### P1 - Add After the Core Product Is Stable

- Curated live simulation.
- `.eml` and additional audio-format upload support.
- Per-stage ingestion progress display with timings, retry, and cost detail.
- Richer executive metrics.
- Mail-client link.
- Additional evaluation workflows.
- More extensive human correction controls (field-level editing of equipment, availability, intent, rate, and question; marking values unknown).
- Improved carrier reconciliation across channels.
- Small visual rate trend.
- Additional filters and dashboard polish.

### P2 - Future Product Extensions

- Standalone Rates Explorer.
- Carrier directory and advanced search.
- Live TMS integration.
- Live email and phone integrations.
- Approved email sending.
- Carrier booking workflow.
- Shipment tracking and exception management.
- Billing, factoring, claims, and settlement.
- Multi-tenant organizations and roles.
- Production alerting and operational dashboards.
- Online evaluation and user-feedback loops.

---

## 18. Deferred Technical Decisions

The following will be decided in the technical blueprint rather than this PRD:

- Application framework and project structure.
- Database and schema implementation.
- Background-job implementation.
- UI interaction approach.
- LLM and transcription providers.
- Agent orchestration approach.
- Structured-output mechanism.
- Prompt definitions.
- Evaluation runner implementation.
- Langfuse integration details.
- Deployment platform.
- Testing libraries and CI/CD.
- Caching, polling, and simulation scheduling.

---

## 19. Current Assumptions

- The dataset is synthetic or approved for use in the interview exercise.
- The deployed application will be low traffic.
- The interviewer will use a shared demo account.
- Historical data can be preprocessed before the interview.
- The broker is the only operational persona required for the MVP.
- The broker remains the final decision-maker.
- The application will not contact or book carriers.
- Detailed AI inspection will occur in Langfuse during screen sharing.
- The simulator will be implemented only after the core workflow is stable.

---

## 20. Product Decisions Recorded

1. Rate intelligence will be embedded in the Load Workspace for the MVP.
2. Detailed quality and evaluation analysis will live in Langfuse rather than a dedicated product page.
3. The Dashboard will show only executive-level AI Operations metrics.
4. Authentication will use a single environment-configured demo account with secure application password handling.
5. Email output will stop at editable draft and copy or optional mail-client handoff.
6. Carrier booking, sending, phone integration, and other external actions are future work.
7. The dataset will be preloaded; one curated scenario may be reserved for a sequential live simulation.
8. The broker will remain responsible for all real-world decisions.
9. AI will interpret unstructured content; deterministic code will enforce business and compliance rules.
10. Technical framework, libraries, and deployment decisions will be documented separately.
11. Manual email and WAV injection will be included in P0 to demonstrate the real ingestion pipeline interactively.
12. The application will show a simple durable job status and safe result summaries, while detailed traces remain in Langfuse.
13. The broker assistant is a single global capability with lane-level query tools, pre-scoped to a load when opened from a Load Workspace.
14. Offline evaluation ground truth will be hand-labeled and independent of the dataset's provided metadata fields.
15. The Manual Ingestion Lab shows a single job status; per-stage progress display and per-stage retry are deferred to P1.
16. P0 human corrections are limited to carrier and load re-assignment plus approve/reject; field-level editing is deferred to P1.
