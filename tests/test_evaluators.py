"""Deterministic evaluators (spec 3I): explainable pass/fail per field, no providers."""

import pytest

from apps.aiops.evaluators import (
    aggregate_metrics,
    evaluate_extraction,
    evaluate_resolution,
    normalize_equipment,
)

pytestmark = pytest.mark.django_db


def proposal(**overrides):
    item = {
        "carrier_name": "Blue Ridge Transport",
        "carrier_name_evidence": {"source_part": "body", "excerpt": "Blue Ridge Transport"},
        "mc_number": "MC-712843",
        "mc_number_evidence": {"source_part": "body", "excerpt": "MC 712843"},
        "load_reference": "#29372450",
        "load_reference_evidence": {"source_part": "body", "excerpt": "29372450"},
        "equipment": "26ft box truck",
        "equipment_evidence": {"source_part": "body", "excerpt": "box truck"},
        "availability": "confirmed",
        "availability_evidence": {"source_part": "body", "excerpt": "we can do it"},
        "intents": ["rate_negotiation", "availability"],
        "rates": [
            {
                "amount": "240",
                "currency": "USD",
                "role": "broker_rate_reference",
                "basis": "all_in",
                "evidence": None,
            },
            {
                "amount": "280",
                "currency": "USD",
                "role": "carrier_counteroffer",
                "basis": "all_in",
                "evidence": None,
            },
        ],
    }
    item.update(overrides)
    return item


def labels(**overrides):
    base = {
        "primary_intent": "rate_negotiation",
        "availability": "confirmed",
        "equipment_code": "box_truck",
        "load_reference_digits": "29372450",
        "mc_number_digits": "712843",
        "mc_stated_but_garbled": False,
        "current_carrier_position": {
            "amount": "280",
            "role": "carrier_counteroffer",
            "basis": "all_in",
        },
        "broker_rate_mentioned": "240",
        "multi_inquiry": False,
    }
    base.update(overrides)
    return base


def by_metric(scores):
    return {score.metric: score for score in scores}


class TestNormalizeEquipment:
    def test_synonyms_map_to_canonical_codes(self):
        assert normalize_equipment("reefer") == "refrigerated"
        assert normalize_equipment("26ft Box Truck") == "box_truck"
        assert normalize_equipment("Sprinter van") == "sprinter_van"
        assert normalize_equipment("flat bed") == "flatbed"
        assert normalize_equipment(None) is None
        assert normalize_equipment("gooseneck trailer") is None


class TestEvaluateExtraction:
    def test_perfect_case_passes_every_metric(self):
        scores = by_metric(evaluate_extraction(proposal(), labels()))
        for name in (
            "intent",
            "availability",
            "equipment",
            "load_reference",
            "mc_number",
            "rate_value",
            "rate_role",
        ):
            assert scores[name].passed is True, name
        assert scores["grounding_rate"].value == 1.0

    def test_missed_mc_fails_identifier_metric(self):
        scores = by_metric(
            evaluate_extraction(proposal(mc_number=None, mc_number_evidence=None), labels())
        )
        assert scores["mc_number"].passed is False
        assert scores["mc_number"].category == "identifier_extraction"

    def test_garbled_mc_is_not_applicable(self):
        scores = by_metric(
            evaluate_extraction(
                proposal(mc_number=None, mc_number_evidence=None),
                labels(mc_number_digits=None, mc_stated_but_garbled=True),
            )
        )
        assert scores["mc_number"].passed is None

    def test_rate_role_confusion_is_its_own_failure(self):
        wrong_role = proposal(
            rates=[
                {
                    "amount": "280",
                    "currency": "USD",
                    "role": "carrier_quote",
                    "basis": "all_in",
                    "evidence": None,
                }
            ]
        )
        scores = by_metric(evaluate_extraction(wrong_role, labels()))
        assert scores["rate_value"].passed is True
        assert scores["rate_role"].passed is False
        assert scores["rate_role"].category == "rate_role_confusion"

    def test_missing_rate_position_fails_value_and_skips_role(self):
        no_carrier_rate = proposal(
            rates=[
                {
                    "amount": "240",
                    "currency": "USD",
                    "role": "broker_rate_reference",
                    "basis": "all_in",
                    "evidence": None,
                }
            ]
        )
        scores = by_metric(evaluate_extraction(no_carrier_rate, labels()))
        assert scores["rate_value"].passed is False
        assert scores["rate_role"].passed is None

    def test_no_position_expected_and_none_extracted_passes(self):
        quiet = proposal(rates=[])
        quiet_labels = labels(current_carrier_position=None, broker_rate_mentioned=None)
        scores = by_metric(evaluate_extraction(quiet, quiet_labels))
        assert scores["rate_value"].passed is True

    def test_grounding_rate_drops_when_evidence_is_missing(self):
        ungrounded = proposal(mc_number_evidence=None, equipment_evidence=None)
        scores = by_metric(evaluate_extraction(ungrounded, labels()))
        assert scores["grounding_rate"].value == 0.5
        assert scores["grounding_rate"].passed is False


class TestEvaluateResolution:
    def test_verified_expected_when_identifiers_resolve(self):
        scores = by_metric(
            evaluate_resolution(
                carrier_status="verified",
                load_status="verified",
                labels=labels(),
                mc_in_directory=True,
                load_in_directory=True,
            )
        )
        assert scores["carrier_resolution"].passed is True
        assert scores["load_resolution"].passed is True

    def test_missed_verification_fails_entity_resolution(self):
        scores = by_metric(
            evaluate_resolution(
                carrier_status="needs_review",
                load_status="unmatched",
                labels=labels(),
                mc_in_directory=True,
                load_in_directory=True,
            )
        )
        assert scores["carrier_resolution"].passed is False
        assert scores["carrier_resolution"].category == "entity_resolution"
        assert scores["load_resolution"].passed is False

    def test_no_reference_expects_unmatched(self):
        quiet = labels(load_reference_digits=None, mc_number_digits=None)
        scores = by_metric(
            evaluate_resolution(
                carrier_status="needs_review",
                load_status="unmatched",
                labels=quiet,
                mc_in_directory=False,
                load_in_directory=False,
            )
        )
        assert scores["load_resolution"].passed is True
        # No MC stated: carrier may still verify via envelope email — not applicable.
        assert scores["carrier_resolution"].passed is None


class TestAggregate:
    def test_aggregates_accuracy_and_overall_score(self):
        case_a = evaluate_extraction(proposal(), labels())
        case_b = evaluate_extraction(proposal(mc_number=None, mc_number_evidence=None), labels())
        summary = aggregate_metrics([case_a, case_b])
        assert summary["metrics"]["mc_number"]["accuracy"] == 0.5
        assert summary["metrics"]["intent"]["accuracy"] == 1.0
        assert 0 < summary["score"] <= 1.0
        assert summary["metrics"]["mc_number"]["failed"] == 1
