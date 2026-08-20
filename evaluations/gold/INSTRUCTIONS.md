# Gold-label instructions (Goodlane carrier-agent eval set)

You are hand-labelling carrier communications for an extraction evaluation gold set.
Read ONLY the message content given (subject + body for emails; transcript segments
for calls). Never guess: a fact not stated in the content is null. Dataset metadata
does not exist for you.

For each case produce a JSON object:

{
  "case_id": "<copy from source>",
  "external_source_id": "<copy>",
  "channel": "email" | "call",
  "labels": {
    "primary_intent": one of:
        "availability"   (carrier offers/asks about covering a load or announces truck availability),
        "rate_quote"     (carrier states their price, unprompted by a broker rate),
        "rate_negotiation" (carrier responds to a broker rate with a counter/pushback),
        "load_detail_question" (mainly asking about load details: weight, window, address...),
        "compliance"     (insurance/authority/safety paperwork topics),
        "confirmation"   (confirming an already-agreed load/pickup),
        "decline", "factoring_or_payment", "problem_or_exception", "general_inquiry", "other",
    "availability": "confirmed" (explicit yes) | "conditional" (yes tied to a stated condition,
        e.g. a required rate or timing) | "unavailable" (explicit no) | "not_stated",
    "equipment_code": "box_truck" | "sprinter_van" | "flatbed" | "refrigerated" | null
        (synonyms: reefer=refrigerated; cargo van/sprinter=sprinter_van; box/straight truck=box_truck;
         26ft box=box_truck; flat bed=flatbed; null when no equipment is mentioned in the content),
    "load_reference_digits": "29372450"-style digits of a load/reference number stated in the
        content, else null. A lane description ("the Philly run") is NOT a reference.
    "mc_number_digits": digits of an MC number stated in the content, else null.
        If an MC is stated but garbled/unreadable (e.g. "500And38700And72", words mixed in,
        wrong length, transcription mush), set null here and mc_stated_but_garbled true.
    "mc_stated_but_garbled": true | false,
    "current_carrier_position": {"amount": "395", "role": "carrier_quote" | "carrier_counteroffer",
        "basis": "all_in" | "per_mile" | "hourly" | "unknown"} — the carrier's FINAL own asking
        price in this message (their last stated position). null when the carrier states no own price.
        role is carrier_counteroffer when it responds to a broker's number, else carrier_quote.
    "broker_rate_mentioned": "420" | null — a broker-posted rate the message references,
    "multi_inquiry": true when the message clearly concerns MULTIPLE separate loads, else false
  },
  "notes": "one line: anything tricky (garbled audio, contradictions, sarcasm, uncertainty)"
}

Rules:
- Amounts: digits only, no $ or commas; keep cents only if stated ("2.80" per-mile is fine).
- For calls: low-confidence segments (conf below 0.7) exist; still label what a careful human
  concludes from listening; use notes for uncertainty. Speaker labels are NOT identities —
  work out who the carrier is from what is said.
- Be conservative: explicit beats inferred. availability "confirmed" requires an actual yes.
- Every case in your batch must appear in your output exactly once.
