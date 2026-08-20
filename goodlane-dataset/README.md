# Goodlane Freight Dataset
### Technical Interview Exercise — Candidate Package

---

## What's Included

| File / Folder | Format | Records | Description |
|---|---|---|---|
| `carrier_emails.json` | JSON | 274 emails | Inbound carrier inquiries across active and historical loads |
| `carrier_profiles.json` | JSON | 48 carriers | Carrier master data: MC numbers, equipment, lanes, compliance status, history |
| `loads.csv` | CSV | 50 loads | Active and historical load board: origin, destination, equipment, rate, status |
| `rate_history.csv` | CSV | 720 rows | Weekly historical rate data by lane and equipment type (Dec 2025–May 2026) |
| `call_recordings/` | WAV | 55 files | Recorded broker–carrier phone conversations (60–90 seconds each) |

---

## File Details

### `carrier_emails.json`
Inbound emails from carriers responding to posted loads. Each record includes:
- `email_id`, `timestamp`, `from_name`, `from_email`, `to_email`
- `subject`, `body`
- `mc_number` — carrier's MC number as provided in the email (may be missing or inconsistent)
- `load_reference` — load ID referenced in the email
- `equipment_mentioned`, `rate_quoted_usd`, `intent`

Emails range from terse one-liners to full negotiation threads. Some contain errors, missing fields, or references to the wrong load number. How you normalize and reason over them is part of what we're evaluating.

### `carrier_profiles.json`
Master carrier records. Fields include:
- `mc_number`, `dot_number`, `company_name`, `primary_contact`, `email`, `phone`
- `equipment_types`, `preferred_lanes`, `home_base_zip`
- `factoring_company`, `payment_terms_preference`
- `reliability_score`, `loads_completed_with_goodlane`, `avg_response_time_hours`
- `insurance_expiry`, `authority_status`, `safety_rating`
- `onboarded` (bool), `notes`

Not all records are complete. Some carriers have missing MC numbers, expired insurance, conditional authority status, or unknown company names. These are real data quality issues your agent will need to handle.

### `loads.csv`
The active and historical load board. Fields include:
- `load_id`, origin/destination (city, state, zip), `distance_miles`
- `equipment_type`, `weight_lbs` (sometimes blank)
- `pickup_date`, `pickup_window`, `delivery_date`
- `offered_rate_usd`, `status` (open / covered / delivered / cancelled)
- `shipper_name`, `internal_notes`

Statuses: `open` loads are active. `delivered` and `covered` loads include historical email threads showing how they were negotiated and filled.

### `rate_history.csv`
Weekly average market rates by lane and equipment type. Fields:
- `week_start`, `origin_state`, `destination_state`, `equipment_type`
- `avg_rate_per_mile`, `min_rate_per_mile`, `max_rate_per_mile`
- `load_volume` — number of loads that week on that lane

Use this to give your agent context on whether a carrier's quoted rate is above or below market.

### `call_recordings/`
55 WAV audio files of broker–carrier phone conversations. File naming convention:
```
call_NNN_<type>.wav
```
Types: `rate_negotiation`, `availability_check`, `compliance_check`, `load_details`, `voicemail`

Recordings are 60–90 seconds each. They include natural speech patterns: filler words, pauses, and occasionally garbled carrier identification numbers. Some callers also appear in `carrier_emails.json` — whether you can identify and reconcile them is part of the challenge.

---

## Known Data Messiness (Intentional)

This data is intentionally imperfect in ways that reflect real broker operations:

- **Carrier emails** use inconsistent formatting: some are terse single lines, others are multi-paragraph
- **MC numbers** may appear in email bodies as plain text, formatted with dashes, or not at all
- **Weight fields** in loads are sometimes blank — shipper hasn't confirmed
- **Pickup windows** are inconsistently formatted (ranges, single times, "morning", blank)
- **Call recordings** include filler words, cross-talk, and spoken MC numbers that may be garbled or corrected mid-sentence
- **Some carrier profiles** have null fields for MC number, DOT number, or insurance expiry
- **Authority status** is not always ACTIVE — check before booking

Handling this gracefully is part of what we're assessing.

---

## Getting Started

You'll receive this dataset as part of the exercise brief. Refer to the exercise document for full requirements. At a minimum your agent should be able to:

1. Ingest and normalize data from both `carrier_emails.json` and the `call_recordings/` WAV files
2. Answer questions like: *"Which carriers have confirmed availability for PA-NJ Box Truck loads this week?"* or *"What's the best rate on offer for load #29372450?"*
3. Draft a response email to a carrier quoting a rate or confirming next steps
4. Support at least one tool call to retrieve load or carrier context on demand

See the exercise README for full technical requirements and deliverable format.

---

*Goodlane Logistics — Confidential — May 2026*
