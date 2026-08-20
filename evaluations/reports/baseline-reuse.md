# Evaluation report — baseline-reuse-2

- Mode: **reuse** · cases 35
- Dataset: goodlane-v1 (5f19f00fa23c…)
- Git: 7b1b817ac5d9 · schema extraction-v1 · policy goodlane_demo_eligibility_v1 · pricing pricing-2026-08
- Prompts: {"extraction-email": {"version": "1", "source": "langfuse"}, "extraction-call": {"version": "1", "source": "langfuse"}}
- Model: gpt-5.6-luna
- Cost: none (reuse mode)

## Overall score: 0.8987

| Metric | Passed | Failed | N/A | Accuracy |
|---|---:|---:|---:|---:|
| availability | 16 | 19 | 0 | 0.4571 |
| carrier_resolution | 14 | 1 | 20 | 0.9333 |
| equipment | 34 | 1 | 0 | 0.9714 |
| grounding_rate | 34 | 0 | 1 | 1.0 |
| intent | 23 | 12 | 0 | 0.6571 |
| load_reference | 35 | 0 | 0 | 1.0 |
| load_resolution | 35 | 0 | 0 | 1.0 |
| mc_number | 30 | 1 | 4 | 0.9677 |
| rate_role | 19 | 0 | 16 | 1.0 |
| rate_value | 35 | 0 | 0 | 1.0 |

## Cases needing attention

### call-call_001_rate_negotiation — failed
- Categories: availability, entity_resolution, identifier_extraction, intent_classification
- What happened: intent: expected rate_negotiation, got availability; availability: expected conditional, got not_stated; mc_number: expected 776491, got None; carrier_resolution: expected verified, got needs_review
- Improve: Add intent definitions/examples to the prompt. Sharpen explicit-vs-conditional availability definitions. Tighten identifier examples in the prompt or normalization. Inspect matcher signals; the identifier may have been missed upstream.

### call-call_002_rate_negotiation — failed
- Categories: availability, intent_classification
- What happened: intent: expected rate_negotiation, got availability; availability: expected conditional, got not_stated
- Improve: Add intent definitions/examples to the prompt. Sharpen explicit-vs-conditional availability definitions.

### call-call_003_rate_negotiation — failed
- Categories: availability
- What happened: availability: expected conditional, got not_stated
- Improve: Sharpen explicit-vs-conditional availability definitions.

### call-call_004_rate_negotiation — failed
- Categories: availability
- What happened: availability: expected conditional, got not_stated
- Improve: Sharpen explicit-vs-conditional availability definitions.

### call-call_005_rate_negotiation — failed
- Categories: availability
- What happened: availability: expected conditional, got not_stated
- Improve: Sharpen explicit-vs-conditional availability definitions.

### call-call_006_rate_negotiation — failed
- Categories: availability
- What happened: availability: expected conditional, got not_stated
- Improve: Sharpen explicit-vs-conditional availability definitions.

### call-call_007_rate_negotiation — failed
- Categories: availability
- What happened: availability: expected conditional, got not_stated
- Improve: Sharpen explicit-vs-conditional availability definitions.

### call-call_008_rate_negotiation — failed
- Categories: availability
- What happened: availability: expected conditional, got not_stated
- Improve: Sharpen explicit-vs-conditional availability definitions.

### call-call_009_rate_negotiation — failed
- Categories: availability, intent_classification
- What happened: intent: expected rate_negotiation, got availability; availability: expected conditional, got not_stated
- Improve: Add intent definitions/examples to the prompt. Sharpen explicit-vs-conditional availability definitions.

### call-call_010_rate_negotiation — failed
- Categories: availability
- What happened: availability: expected conditional, got not_stated
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

### email-CE0010 — failed
- Categories: availability
- What happened: availability: expected confirmed, got conditional
- Improve: Sharpen explicit-vs-conditional availability definitions.

### email-CE0011 — failed
- Categories: intent_classification
- What happened: intent: expected rate_negotiation, got availability
- Improve: Add intent definitions/examples to the prompt.

### email-CE0012 — failed
- Categories: intent_classification
- What happened: intent: expected confirmation, got availability
- Improve: Add intent definitions/examples to the prompt.

### email-CE0021 — failed
- Categories: availability, intent_classification
- What happened: intent: expected rate_negotiation, got rate_quote; availability: expected conditional, got not_stated
- Improve: Add intent definitions/examples to the prompt. Sharpen explicit-vs-conditional availability definitions.

### email-CE0024 — failed
- Categories: availability, equipment_normalization
- What happened: availability: expected conditional, got not_stated; equipment: expected box_truck, got None -> None
- Improve: Sharpen explicit-vs-conditional availability definitions. Extend equipment synonyms (aliases or prompt).

### email-CE0027 — failed
- Categories: availability
- What happened: availability: expected conditional, got not_stated
- Improve: Sharpen explicit-vs-conditional availability definitions.

### email-CE0032 — failed
- Categories: availability
- What happened: availability: expected conditional, got not_stated
- Improve: Sharpen explicit-vs-conditional availability definitions.

### email-CE0035 — failed
- Categories: availability
- What happened: availability: expected conditional, got not_stated
- Improve: Sharpen explicit-vs-conditional availability definitions.

### email-CE0037 — failed
- Categories: intent_classification
- What happened: intent: expected availability, got general_inquiry
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
- Categories: availability
- What happened: availability: expected conditional, got not_stated
- Improve: Sharpen explicit-vs-conditional availability definitions.

