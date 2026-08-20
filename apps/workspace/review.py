"""Broker review actions (spec 2E).

Each action is one transaction: lock and validate the inquiry, capture the prior
state, update the canonical resolution, create or select a broker-verified match,
write the append-only InquiryReviewAction, reconcile candidates, run deterministic
reassessment, and recompute the job's terminal review state. Source evidence and
ExtractionRun output are never changed; a broker cannot override a deterministic
compliance blocker from here.
"""

from django.db import transaction
from django.utils import timezone

from apps.candidates.engine import assess_candidate
from apps.candidates.models import CandidateInquiry, CarrierLoadCandidate
from apps.comms.models import IngestionJob
from apps.inquiries.models import (
    Inquiry,
    InquiryCarrierMatch,
    InquiryLoadMatch,
    InquiryReviewReason,
)
from apps.workspace.models import InquiryReviewAction


class ReviewError(Exception):
    def __init__(self, code: str, summary: str):
        super().__init__(summary)
        self.code = code
        self.summary = summary


# Reasons a given correction addresses; everything else stays pending.
CARRIER_REASON_CODES = {
    InquiryReviewReason.Code.MISSING_MC,
    InquiryReviewReason.Code.GARBLED_MC,
    InquiryReviewReason.Code.WEAK_CARRIER_MATCH,
    InquiryReviewReason.Code.CONFLICTING_CARRIER_IDENTITY,
}
LOAD_REASON_CODES = {
    InquiryReviewReason.Code.AMBIGUOUS_LOAD_REFERENCE,
    InquiryReviewReason.Code.CONFLICTING_LOAD_REFERENCE,
}


def _state_snapshot(inquiry: Inquiry) -> dict:
    return {
        "review_status": inquiry.review_status,
        "carrier_id": str(inquiry.carrier_id) if inquiry.carrier_id else None,
        "load_id": str(inquiry.load_id) if inquiry.load_id else None,
        "carrier_resolution_status": inquiry.carrier_resolution_status,
        "load_resolution_status": inquiry.load_resolution_status,
    }


def _locked(inquiry: Inquiry) -> Inquiry:
    return (
        Inquiry.objects.select_for_update(of=("self",))
        .select_related("communication_event")
        .get(pk=inquiry.pk)
    )


def _write_action(locked, action_type, actor_label, note, before, **fields):
    return InquiryReviewAction.objects.create(
        inquiry=locked,
        action_type=action_type,
        actor_label=actor_label,
        reason=note,
        before_snapshot=before,
        after_snapshot=_state_snapshot(locked),
        **fields,
    )


def _resolve_pending_reasons(locked, now, codes=None):
    reasons = locked.review_reasons.filter(
        resolved_at__isnull=True, severity=InquiryReviewReason.Severity.REVIEW
    )
    if codes is not None:
        reasons = reasons.filter(code__in=codes)
    reasons.update(resolved_at=now)


def _derived_review_status(locked) -> str:
    pending = locked.review_reasons.filter(
        resolved_at__isnull=True, severity=InquiryReviewReason.Severity.REVIEW
    ).exists()
    return Inquiry.ReviewStatus.NEEDS_REVIEW if pending else Inquiry.ReviewStatus.APPROVED


def _recompute_job(locked) -> None:
    """Mirror finalization: the job's terminal review flag follows its inquiries.

    Only completed <-> needs_review may flip; queued/processing/failed belong to
    the pipeline lifecycle and are never touched by a review action.
    """
    job = IngestionJob.objects.filter(communication_event=locked.communication_event).first()
    if job is None or job.status not in (
        IngestionJob.Status.COMPLETED,
        IngestionJob.Status.NEEDS_REVIEW,
    ):
        return
    statuses = set(
        Inquiry.objects.filter(communication_event=locked.communication_event).values_list(
            "review_status", flat=True
        )
    )
    target = (
        IngestionJob.Status.NEEDS_REVIEW
        if Inquiry.ReviewStatus.NEEDS_REVIEW in statuses
        else IngestionJob.Status.COMPLETED
    )
    if job.status != target:
        job.status = target
        job.save(update_fields=["status", "updated_at"])


def _reassess_linked_candidates(locked, action) -> None:
    links = CandidateInquiry.objects.filter(inquiry=locked).select_related("candidate")
    for link in links:
        assess_candidate(link.candidate, triggering_inquiry=locked, triggering_review_action=action)


def _reconcile_candidate(locked, action, *, previous_carrier=None, previous_load=None) -> None:
    """Move the inquiry between candidate aggregates after an identity correction."""
    carrier_id, load_id = locked.carrier_id, locked.load_id
    if carrier_id is None or load_id is None:
        return
    event = locked.communication_event
    stale = CarrierLoadCandidate.objects.none()
    if previous_carrier is not None and previous_carrier.id != carrier_id:
        stale = CarrierLoadCandidate.objects.filter(carrier=previous_carrier, load_id=load_id)
    if previous_load is not None and previous_load.id != load_id:
        stale = CarrierLoadCandidate.objects.filter(carrier_id=carrier_id, load=previous_load)
    for candidate in stale:
        CandidateInquiry.objects.filter(candidate=candidate, inquiry=locked).delete()
        assess_candidate(candidate, triggering_inquiry=locked, triggering_review_action=action)

    candidate, _ = CarrierLoadCandidate.objects.get_or_create(
        carrier_id=carrier_id,
        load_id=load_id,
        defaults={
            "first_seen_at": event.occurred_at or event.received_at,
            "last_activity_at": event.occurred_at or event.received_at,
        },
    )
    CandidateInquiry.objects.get_or_create(
        candidate=candidate,
        inquiry=locked,
        defaults={"relationship": CandidateInquiry.Relationship.SUPPORTING},
    )
    assess_candidate(candidate, triggering_inquiry=locked, triggering_review_action=action)


# ---------------------------------------------------------------------------


def approve_extraction(inquiry, *, actor_label: str, note: str = "") -> InquiryReviewAction:
    with transaction.atomic():
        locked = _locked(inquiry)
        if locked.review_status not in (
            Inquiry.ReviewStatus.UNREVIEWED,
            Inquiry.ReviewStatus.NEEDS_REVIEW,
        ):
            raise ReviewError("invalid_state", f"cannot approve a {locked.review_status} inquiry")
        before = _state_snapshot(locked)
        _resolve_pending_reasons(locked, timezone.now())
        locked.review_status = Inquiry.ReviewStatus.APPROVED
        locked.save(update_fields=["review_status", "updated_at"])
        action = _write_action(
            locked, InquiryReviewAction.ActionType.APPROVE_EXTRACTION, actor_label, note, before
        )
        _recompute_job(locked)
    return action


def reject_extraction(inquiry, *, actor_label: str, note: str = "") -> InquiryReviewAction:
    with transaction.atomic():
        locked = _locked(inquiry)
        if locked.review_status == Inquiry.ReviewStatus.REJECTED:
            raise ReviewError("invalid_state", "inquiry is already rejected")
        before = _state_snapshot(locked)
        _resolve_pending_reasons(locked, timezone.now())
        locked.review_status = Inquiry.ReviewStatus.REJECTED
        locked.save(update_fields=["review_status", "updated_at"])
        action = _write_action(
            locked, InquiryReviewAction.ActionType.REJECT_EXTRACTION, actor_label, note, before
        )
        # A rejected inquiry no longer feeds operational facts; reassess.
        _reassess_linked_candidates(locked, action)
        _recompute_job(locked)
    return action


def reopen_review(inquiry, *, actor_label: str, note: str = "") -> InquiryReviewAction:
    with transaction.atomic():
        locked = _locked(inquiry)
        if locked.review_status not in (
            Inquiry.ReviewStatus.APPROVED,
            Inquiry.ReviewStatus.REJECTED,
        ):
            raise ReviewError("invalid_state", "only a decided inquiry can be reopened")
        was_rejected = locked.review_status == Inquiry.ReviewStatus.REJECTED
        before = _state_snapshot(locked)
        locked.review_status = Inquiry.ReviewStatus.NEEDS_REVIEW
        locked.save(update_fields=["review_status", "updated_at"])
        action = _write_action(
            locked, InquiryReviewAction.ActionType.REOPEN_REVIEW, actor_label, note, before
        )
        if was_rejected:
            # Its facts count again; reassess the aggregates that include it.
            _reassess_linked_candidates(locked, action)
        _recompute_job(locked)
    return action


def correct_carrier(inquiry, new_carrier, *, actor_label: str, note: str = ""):
    with transaction.atomic():
        locked = _locked(inquiry)
        if locked.review_status == Inquiry.ReviewStatus.REJECTED:
            raise ReviewError("invalid_state", "reopen the rejected inquiry before correcting it")
        event = locked.communication_event
        if new_carrier.dataset_snapshot_id != event.dataset_snapshot_id:
            raise ReviewError("snapshot_mismatch", "carrier belongs to a different snapshot")
        before = _state_snapshot(locked)
        previous = locked.carrier
        now = timezone.now()

        # The broker's pick replaces any earlier selection, which becomes an
        # auditable rejected proposal rather than being deleted.
        for match in locked.carrier_matches.filter(is_selected=True).exclude(carrier=new_carrier):
            match.is_selected = False
            match.status = InquiryCarrierMatch.Status.REJECTED
            match.reviewed_at = now
            match.save(update_fields=["is_selected", "status", "reviewed_at", "updated_at"])
        match, _ = InquiryCarrierMatch.objects.get_or_create(
            inquiry=locked,
            carrier=new_carrier,
            defaults={
                "match_tier": "exact",
                "primary_method": InquiryCarrierMatch.PrimaryMethod.BROKER_CONFIRMED,
            },
        )
        match.status = InquiryCarrierMatch.Status.VERIFIED
        match.is_selected = True
        match.selection_source = InquiryCarrierMatch.SelectionSource.BROKER
        match.reviewed_at = now
        match.save(
            update_fields=["status", "is_selected", "selection_source", "reviewed_at", "updated_at"]
        )

        locked.carrier = new_carrier
        locked.carrier_resolution_status = Inquiry.ResolutionStatus.VERIFIED
        _resolve_pending_reasons(locked, now, codes=CARRIER_REASON_CODES)
        locked.review_status = _derived_review_status(locked)
        locked.save(
            update_fields=["carrier", "carrier_resolution_status", "review_status", "updated_at"]
        )
        action = _write_action(
            locked,
            InquiryReviewAction.ActionType.CORRECT_CARRIER,
            actor_label,
            note,
            before,
            previous_carrier=previous,
            new_carrier=new_carrier,
        )
        _reconcile_candidate(locked, action, previous_carrier=previous)
        _recompute_job(locked)
    return action


def correct_load(inquiry, new_load, *, actor_label: str, note: str = ""):
    with transaction.atomic():
        locked = _locked(inquiry)
        if locked.review_status == Inquiry.ReviewStatus.REJECTED:
            raise ReviewError("invalid_state", "reopen the rejected inquiry before correcting it")
        event = locked.communication_event
        if new_load.dataset_snapshot_id != event.dataset_snapshot_id:
            raise ReviewError("snapshot_mismatch", "load belongs to a different snapshot")
        before = _state_snapshot(locked)
        previous = locked.load
        now = timezone.now()

        for match in locked.load_matches.filter(is_selected=True).exclude(load=new_load):
            match.is_selected = False
            match.status = InquiryLoadMatch.Status.REJECTED
            match.reviewed_at = now
            match.save(update_fields=["is_selected", "status", "reviewed_at", "updated_at"])
        match, _ = InquiryLoadMatch.objects.get_or_create(
            inquiry=locked,
            load=new_load,
            defaults={
                "match_tier": "exact",
                "primary_method": InquiryLoadMatch.PrimaryMethod.CORRECTED_REFERENCE,
            },
        )
        match.status = InquiryLoadMatch.Status.VERIFIED
        match.is_selected = True
        match.selection_source = InquiryLoadMatch.SelectionSource.BROKER
        match.reviewed_at = now
        match.save(
            update_fields=["status", "is_selected", "selection_source", "reviewed_at", "updated_at"]
        )

        locked.load = new_load
        locked.load_resolution_status = Inquiry.ResolutionStatus.VERIFIED
        _resolve_pending_reasons(locked, now, codes=LOAD_REASON_CODES)
        locked.review_status = _derived_review_status(locked)
        locked.save(update_fields=["load", "load_resolution_status", "review_status", "updated_at"])
        action = _write_action(
            locked,
            InquiryReviewAction.ActionType.CORRECT_LOAD,
            actor_label,
            note,
            before,
            previous_load=previous,
            new_load=new_load,
        )
        _reconcile_candidate(locked, action, previous_load=previous)
        _recompute_job(locked)
    return action
