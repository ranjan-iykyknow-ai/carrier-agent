"""Deterministic candidate assessment (spec 3G).

Runs synchronously during finalization: local typed data, no provider calls.
Every run creates immutable ComplianceAssessment/EligibilityAssessment rows
with reasons and fact snapshots, then moves the candidate's current pointers.
"""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.candidates.models import (
    CandidateAssessmentReason,
    CarrierLoadCandidate,
    ComplianceAssessment,
    EligibilityAssessment,
)
from apps.candidates.policy import (
    POLICY_VERSION,
    assess_authority,
    assess_insurance,
    assess_safety,
    compliance_overall,
)
from apps.inquiries.models import CarrierQuote, Inquiry


@dataclass
class CurrentFacts:
    """Current position derived from explicit, chronologically comparable evidence."""

    availability: str | None = None
    availability_conflict: bool = False
    equipment_type_id: str | None = None
    equipment_conflict: bool = False
    current_quote: CarrierQuote | None = None
    quote_conflict: bool = False
    identity_verified: bool = False


def _latest_explicit(observations):
    """observations: [(occurred_at | None, value)] with explicit values only.

    Returns (value, conflict). A later explicit statement supersedes an earlier
    one only when every statement carries a reliable occurred_at; contradictory
    statements without comparable chronology are a conflict, never a guess.
    """
    if not observations:
        return None, False
    values = {value for _, value in observations}
    if len(values) == 1:
        return observations[0][1], False
    if any(occurred is None for occurred, _ in observations):
        return None, True
    ordered = sorted(observations, key=lambda item: item[0])
    latest_time = ordered[-1][0]
    latest_values = {value for occurred, value in ordered if occurred == latest_time}
    if len(latest_values) > 1:
        return None, True
    return ordered[-1][1], False


def derive_current_facts(candidate: CarrierLoadCandidate) -> CurrentFacts:
    inquiries = list(
        Inquiry.objects.filter(candidate_links__candidate=candidate)
        .select_related("communication_event")
        .order_by("created_at")
    )
    facts = CurrentFacts()
    facts.identity_verified = any(
        inquiry.carrier_resolution_status == "verified"
        and inquiry.load_resolution_status == "verified"
        for inquiry in inquiries
    )

    availability_obs = [
        (inquiry.communication_event.occurred_at, inquiry.availability_status)
        for inquiry in inquiries
        if inquiry.availability_status != Inquiry.AvailabilityStatus.NOT_STATED
    ]
    facts.availability, facts.availability_conflict = _latest_explicit(availability_obs)

    equipment_obs = [
        (inquiry.communication_event.occurred_at, inquiry.equipment_type_id)
        for inquiry in inquiries
        if inquiry.equipment_type_id is not None
    ]
    facts.equipment_type_id, facts.equipment_conflict = _latest_explicit(equipment_obs)

    quotes = list(
        CarrierQuote.objects.filter(
            inquiry__in=inquiries,
            is_current=True,
            evidence_status="explicit",
            quote_type__in=[
                CarrierQuote.QuoteType.CARRIER_QUOTE,
                CarrierQuote.QuoteType.CARRIER_COUNTEROFFER,
            ],
        ).select_related("inquiry__communication_event")
    )
    quote_obs = [(quote.inquiry.communication_event.occurred_at, quote.id) for quote in quotes]
    current_quote_id, facts.quote_conflict = _latest_explicit(quote_obs)
    if current_quote_id is not None:
        facts.current_quote = next(q for q in quotes if q.id == current_quote_id)
    return facts


def assess_candidate(
    candidate: CarrierLoadCandidate,
    *,
    triggering_inquiry=None,
    triggering_review_action=None,
) -> EligibilityAssessment:
    carrier, load = candidate.carrier, candidate.load
    facts = derive_current_facts(candidate)
    now = timezone.now()

    authority = assess_authority(carrier.authority_status)
    safety = assess_safety(carrier.safety_rating)
    insurance = assess_insurance(carrier.insurance_expiry, load.pickup_date)
    overall = compliance_overall(authority, safety, insurance)

    identity = "pass" if facts.identity_verified else "unknown"

    if load.equipment_type_id is None or (
        facts.equipment_type_id is None and not facts.equipment_conflict
    ):
        equipment = "unknown"
    elif facts.equipment_conflict:
        equipment = "unknown"
    elif facts.equipment_type_id == load.equipment_type_id:
        equipment = "pass"
    else:
        equipment = "fail"

    if facts.availability == Inquiry.AvailabilityStatus.CONFIRMED:
        availability = "pass"
    elif facts.availability == Inquiry.AvailabilityStatus.UNAVAILABLE:
        availability = "fail"
    else:
        # conditional / not stated / conflicting / unresolved-chronology: P0 has
        # no prover that a stated condition fits the pickup window.
        availability = "unknown"

    if carrier.onboarded is True:
        onboarding = "pass"
    elif carrier.onboarded is False:
        onboarding = "fail"
    else:
        onboarding = "unknown"

    if overall == "fail" or equipment == "fail" or availability == "fail":
        final_status = EligibilityAssessment.FinalStatus.BLOCKED
    elif overall == "needs_review":
        final_status = EligibilityAssessment.FinalStatus.NEEDS_COMPLIANCE_REVIEW
    elif (
        equipment != "pass"
        or availability != "pass"
        or identity != "pass"
        or facts.quote_conflict
        or onboarding == "unknown"
    ):
        final_status = EligibilityAssessment.FinalStatus.NEEDS_CLARIFICATION
    elif onboarding == "fail":
        final_status = EligibilityAssessment.FinalStatus.NEEDS_ONBOARDING
    else:
        final_status = EligibilityAssessment.FinalStatus.ELIGIBLE

    with transaction.atomic():
        compliance = ComplianceAssessment.objects.create(
            candidate=candidate,
            policy_version=POLICY_VERSION,
            pickup_date_used=load.pickup_date,
            authority_result=authority,
            safety_result=safety,
            insurance_result=insurance,
            overall_result=overall,
            carrier_facts_snapshot={
                "authority_status": carrier.authority_status,
                "safety_rating": carrier.safety_rating,
                "insurance_expiry": str(carrier.insurance_expiry or ""),
                "onboarded": carrier.onboarded,
            },
            evaluated_at=now,
            triggering_inquiry=triggering_inquiry,
        )
        assessment = EligibilityAssessment.objects.create(
            candidate=candidate,
            compliance_assessment=compliance,
            policy_version=POLICY_VERSION,
            identity_result=identity,
            equipment_result=equipment,
            availability_result=availability,
            onboarding_result=onboarding,
            final_status=final_status,
            input_snapshot={
                "availability": facts.availability,
                "availability_conflict": facts.availability_conflict,
                "equipment_type_id": str(facts.equipment_type_id or ""),
                "equipment_conflict": facts.equipment_conflict,
                "load_equipment_type_id": str(load.equipment_type_id or ""),
                "quote_conflict": facts.quote_conflict,
                "identity_verified": facts.identity_verified,
            },
            evaluated_at=now,
            triggering_inquiry=triggering_inquiry,
            triggering_review_action=triggering_review_action,
        )
        _record_reasons(
            assessment, authority, safety, insurance, equipment, availability, onboarding, facts
        )
        candidate.current_quote = facts.current_quote
        candidate.current_compliance_assessment = compliance
        candidate.current_eligibility_assessment = assessment
        candidate.last_activity_at = now
        candidate.save(
            update_fields=[
                "current_quote",
                "current_compliance_assessment",
                "current_eligibility_assessment",
                "last_activity_at",
                "updated_at",
            ]
        )
    return assessment


_REASON_TABLE = {
    ("authority", "fail"): ("authority_inactive", "blocker"),
    ("authority", "unknown"): ("authority_unknown", "review"),
    ("safety", "fail"): ("safety_unacceptable", "blocker"),
    ("safety", "unknown"): ("safety_unknown", "review"),
    ("insurance", "fail"): ("insurance_expired", "blocker"),
    ("insurance", "unknown"): ("insurance_expiry_unknown", "review"),
    ("equipment", "fail"): ("equipment_mismatch", "blocker"),
    ("availability", "fail"): ("carrier_unavailable", "blocker"),
    ("onboarding", "fail"): ("carrier_not_onboarded", "review"),
}


def _record_reasons(
    assessment, authority, safety, insurance, equipment, availability, onboarding, facts
):
    def add(component, code, severity, observed=None, required=None):
        CandidateAssessmentReason.objects.create(
            eligibility_assessment=assessment,
            component=component,
            code=code,
            severity=severity,
            observed_value=observed,
            required_value=required,
        )

    carrier = assessment.candidate.carrier
    load = assessment.candidate.load
    for component, result, observed, required in (
        ("authority", authority, carrier.authority_status, "ACTIVE"),
        ("safety", safety, carrier.safety_rating, "Satisfactory"),
        (
            "insurance",
            insurance,
            str(carrier.insurance_expiry or ""),
            f">= {load.pickup_date}",
        ),
        ("onboarding", onboarding, str(carrier.onboarded), "true"),
    ):
        mapped = _REASON_TABLE.get((component, result))
        if mapped:
            add(component, mapped[0], mapped[1], observed, required)

    if equipment == "fail":
        add(
            "equipment",
            "equipment_mismatch",
            "blocker",
            str(facts.equipment_type_id or ""),
            str(load.equipment_type_id or ""),
        )
    elif facts.equipment_conflict:
        add("equipment", "equipment_conflicting", "review")
    elif equipment == "unknown":
        add("equipment", "equipment_unknown", "review")

    if availability == "fail":
        add("availability", "carrier_unavailable", "blocker")
    elif facts.availability_conflict:
        add("availability", "availability_conflicting", "review")
    elif facts.availability == Inquiry.AvailabilityStatus.CONDITIONAL:
        add("availability", "availability_conditional", "review")
    elif availability == "unknown":
        add("availability", "availability_unknown", "review")

    if facts.quote_conflict:
        add("quote", "quote_ambiguous", "warning")
    elif facts.current_quote is None:
        add("quote", "quote_missing", "information")
