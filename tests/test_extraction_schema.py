"""The shared versioned extraction contract (spec 3B): strict, safe, evidence-pointing."""

from decimal import Decimal

import pytest

from apps.inquiries.extraction_schema import (
    SCHEMA_VERSION,
    ExtractionValidationError,
    parse_extraction,
)


def proposal(**overrides):
    base = {
        "carrier_name": "Blue Ridge Transport",
        "carrier_name_evidence": {"source_part": "body", "excerpt": "Blue Ridge"},
        "mc_number": "712843",
        "mc_number_evidence": {"source_part": "body", "excerpt": "MC 712843"},
        "dot_number": None,
        "contact_email": None,
        "contact_phone": None,
        "load_reference": "29372450",
        "load_reference_evidence": {"source_part": "subject", "excerpt": "29372450"},
        "equipment": "box truck",
        "equipment_evidence": {"source_part": "body", "excerpt": "box truck"},
        "availability": "confirmed",
        "availability_evidence": {"source_part": "body", "excerpt": "We can do"},
        "intents": ["rate_negotiation"],
        "rates": [
            {
                "amount": "400",
                "currency": "USD",
                "role": "carrier_counteroffer",
                "basis": "all_in",
                "evidence": {"source_part": "body", "excerpt": "$400 all-in"},
            }
        ],
        "questions": [],
        "conditions": None,
        "conditions_evidence": None,
        "summary": "Carrier counteroffers $400 all-in for load 29372450.",
    }
    base.update(overrides)
    return base


def payload(*proposals):
    return {"inquiries": list(proposals) or [proposal()]}


class TestParsing:
    def test_valid_payload_parses_with_typed_amounts(self):
        parsed = parse_extraction(payload())
        inquiry = parsed.inquiries[0]
        assert inquiry.mc_number == "712843"
        assert inquiry.rates[0].amount == Decimal("400")
        assert inquiry.rates[0].role == "carrier_counteroffer"
        assert SCHEMA_VERSION == "extraction-v1"

    def test_missing_values_stay_none_never_invented(self):
        parsed = parse_extraction(
            payload(
                proposal(
                    carrier_name=None,
                    carrier_name_evidence=None,
                    mc_number=None,
                    mc_number_evidence=None,
                    load_reference=None,
                    load_reference_evidence=None,
                    equipment=None,
                    equipment_evidence=None,
                    availability="not_stated",
                    availability_evidence=None,
                    rates=[],
                )
            )
        )
        inquiry = parsed.inquiries[0]
        assert inquiry.carrier_name is None
        assert inquiry.availability == "not_stated"
        assert inquiry.rates == []

    def test_at_least_one_inquiry_required(self):
        with pytest.raises(ExtractionValidationError):
            parse_extraction({"inquiries": []})

    def test_uncontrolled_vocabulary_is_rejected(self):
        with pytest.raises(ExtractionValidationError):
            parse_extraction(payload(proposal(availability="maybe")))
        with pytest.raises(ExtractionValidationError):
            parse_extraction(payload(proposal(intents=["vibes"])))

    def test_non_numeric_rate_amount_is_rejected(self):
        bad = proposal()
        bad["rates"][0]["amount"] = "four hundred"
        with pytest.raises(ExtractionValidationError):
            parse_extraction(payload(bad))

    def test_negative_rate_amount_is_rejected(self):
        bad = proposal()
        bad["rates"][0]["amount"] = "-400"
        with pytest.raises(ExtractionValidationError):
            parse_extraction(payload(bad))

    def test_rate_roles_are_preserved_distinctly(self):
        both = proposal(
            rates=[
                {
                    "amount": "240",
                    "currency": "USD",
                    "role": "broker_rate_reference",
                    "basis": "all_in",
                    "evidence": {"source_part": "body", "excerpt": "not at $240"},
                },
                {
                    "amount": "280",
                    "currency": "USD",
                    "role": "carrier_counteroffer",
                    "basis": "all_in",
                    "evidence": {"source_part": "body", "excerpt": "floor on this lane is $280"},
                },
            ]
        )
        parsed = parse_extraction(payload(both))
        roles = [r.role for r in parsed.inquiries[0].rates]
        assert roles == ["broker_rate_reference", "carrier_counteroffer"]

    def test_garbage_shape_is_rejected_with_safe_error(self):
        with pytest.raises(ExtractionValidationError) as excinfo:
            parse_extraction({"not": "the schema"})
        assert "inquiries" in str(excinfo.value)
