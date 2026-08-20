# Evaluation report — baseline-fresh-v2

- Mode: **fresh** · cases 35
- Dataset: goodlane-v1 (5f19f00fa23c…)
- Git: 7b1b817ac5d9 · schema extraction-v1 · policy goodlane_demo_eligibility_v1 · pricing pricing-2026-08
- Prompts: {"extraction-email": {"version": "1", "source": "langfuse"}, "extraction-call": {"version": "1", "source": "langfuse"}}
- Model: gpt-5.6-luna
- Cost: $0.031279

## Overall score: 0.8459

| Metric | Passed | Failed | N/A | Accuracy |
|---|---:|---:|---:|---:|
| availability | 19 | 15 | 0 | 0.5588 |
| equipment | 34 | 0 | 0 | 1.0 |
| grounding_rate | 34 | 0 | 0 | 1.0 |
| intent | 12 | 22 | 0 | 0.3529 |
| load_reference | 34 | 0 | 0 | 1.0 |
| mc_number | 29 | 1 | 4 | 0.9667 |
| rate_role | 16 | 2 | 16 | 0.8889 |
| rate_value | 34 | 0 | 0 | 1.0 |

## Cases needing attention

### call-call_001_rate_negotiation — failed
- Categories: availability
- What happened: availability: expected conditional, got not_stated
- Improve: Sharpen explicit-vs-conditional availability definitions.

### call-call_002_rate_negotiation — failed
- Categories: availability, intent_classification
- What happened: intent: expected rate_negotiation, got availability; availability: expected conditional, got not_stated
- Improve: Add intent definitions/examples to the prompt. Sharpen explicit-vs-conditional availability definitions.

### call-call_003_rate_negotiation — failed
- Categories: availability
- What happened: availability: expected conditional, got not_stated
- Improve: Sharpen explicit-vs-conditional availability definitions.

### call-call_004_rate_negotiation — failed
- Categories: availability, intent_classification
- What happened: intent: expected rate_negotiation, got availability; availability: expected conditional, got not_stated
- Improve: Add intent definitions/examples to the prompt. Sharpen explicit-vs-conditional availability definitions.

### call-call_005_rate_negotiation — failed
- Categories: availability, intent_classification
- What happened: intent: expected rate_negotiation, got general_inquiry; availability: expected conditional, got not_stated
- Improve: Add intent definitions/examples to the prompt. Sharpen explicit-vs-conditional availability definitions.

### call-call_006_rate_negotiation — failed
- Categories: availability, intent_classification, rate_role_confusion
- What happened: intent: expected rate_negotiation, got availability; availability: expected conditional, got not_stated; rate_role: expected carrier_counteroffer, got carrier_quote
- Improve: Add intent definitions/examples to the prompt. Sharpen explicit-vs-conditional availability definitions. Add contrastive rate-role examples to the prompt.

### call-call_007_rate_negotiation — failed
- Categories: availability, intent_classification
- What happened: intent: expected rate_negotiation, got availability; availability: expected conditional, got not_stated
- Improve: Add intent definitions/examples to the prompt. Sharpen explicit-vs-conditional availability definitions.

### call-call_008_rate_negotiation — failed
- Categories: availability, intent_classification
- What happened: intent: expected rate_negotiation, got availability; availability: expected conditional, got not_stated
- Improve: Add intent definitions/examples to the prompt. Sharpen explicit-vs-conditional availability definitions.

### call-call_009_rate_negotiation — failed
- Categories: availability, identifier_extraction, intent_classification
- What happened: intent: expected rate_negotiation, got availability; availability: expected conditional, got not_stated; mc_number: expected 901234, got None
- Improve: Add intent definitions/examples to the prompt. Sharpen explicit-vs-conditional availability definitions. Tighten identifier examples in the prompt or normalization.

### call-call_010_rate_negotiation — failed
- Categories: availability, intent_classification
- What happened: intent: expected rate_negotiation, got availability; availability: expected conditional, got not_stated
- Improve: Add intent definitions/examples to the prompt. Sharpen explicit-vs-conditional availability definitions.

### email-CE0001 — failed
- Categories: availability
- What happened: availability: expected confirmed, got not_stated
- Improve: Sharpen explicit-vs-conditional availability definitions.

### email-CE0002 — failed
- Categories: intent_classification
- What happened: intent: expected confirmation, got availability
- Improve: Add intent definitions/examples to the prompt.

### email-CE0004 — failed
- Categories: intent_classification
- What happened: intent: expected confirmation, got availability
- Improve: Add intent definitions/examples to the prompt.

### email-CE0006 — failed
- Categories: availability, intent_classification
- What happened: intent: expected rate_negotiation, got availability; availability: expected conditional, got confirmed
- Improve: Add intent definitions/examples to the prompt. Sharpen explicit-vs-conditional availability definitions.

### email-CE0007 — failed
- Categories: intent_classification
- What happened: intent: expected confirmation, got availability
- Improve: Add intent definitions/examples to the prompt.

### email-CE0009 — failed
- Categories: intent_classification
- What happened: intent: expected confirmation, got availability
- Improve: Add intent definitions/examples to the prompt.

### email-CE0011 — failed
- Categories: intent_classification, rate_role_confusion
- What happened: intent: expected rate_negotiation, got availability; rate_role: expected carrier_counteroffer, got carrier_quote
- Improve: Add intent definitions/examples to the prompt. Add contrastive rate-role examples to the prompt.

### email-CE0012 — failed
- Categories: intent_classification
- What happened: intent: expected confirmation, got availability
- Improve: Add intent definitions/examples to the prompt.

### email-CE0019 — failed
- Categories: intent_classification
- What happened: intent: expected confirmation, got availability
- Improve: Add intent definitions/examples to the prompt.

### email-CE0021 — failed
- Categories: intent_classification
- What happened: intent: expected rate_negotiation, got availability
- Improve: Add intent definitions/examples to the prompt.

### email-CE0024 — errored
- Categories: —
- What happened: RuntimeError: extraction failed schema validation: inquiries.0.rates.1.amount: decimal_parsing
- Improve: —

### email-CE0032 — failed
- Categories: availability
- What happened: availability: expected conditional, got not_stated
- Improve: Sharpen explicit-vs-conditional availability definitions.

### email-CE0035 — failed
- Categories: intent_classification
- What happened: intent: expected rate_negotiation, got availability
- Improve: Add intent definitions/examples to the prompt.

### email-CE0037 — failed
- Categories: intent_classification
- What happened: intent: expected availability, got general_inquiry
- Improve: Add intent definitions/examples to the prompt.

### email-CE0043 — failed
- Categories: intent_classification
- What happened: intent: expected confirmation, got availability
- Improve: Add intent definitions/examples to the prompt.

### email-CE0046 — failed
- Categories: intent_classification
- What happened: intent: expected problem_or_exception, got load_detail_question
- Improve: Add intent definitions/examples to the prompt.

### email-CE0085 — failed
- Categories: availability
- What happened: availability: expected conditional, got not_stated
- Improve: Sharpen explicit-vs-conditional availability definitions.

### email-CE0090 — failed
- Categories: availability, intent_classification
- What happened: intent: expected rate_negotiation, got availability; availability: expected conditional, got confirmed
- Improve: Add intent definitions/examples to the prompt. Sharpen explicit-vs-conditional availability definitions.

