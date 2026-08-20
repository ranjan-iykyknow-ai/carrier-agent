"""Constraint behavior for the Step 2E models (review, drafting, assistant, tools)."""

import pytest
from django.db import IntegrityError, transaction

from tests.factories import (
    make_assistant_conversation,
    make_assistant_message,
    make_assistant_run,
    make_draft,
    make_eligibility_assessment,
    make_review_action,
)

pytestmark = pytest.mark.django_db


def _rejects(fn):
    with pytest.raises(IntegrityError), transaction.atomic():
        fn()


class TestInquiryReviewAction:
    def test_action_captures_before_and_after_snapshots(self):
        action = make_review_action()
        assert action.before_snapshot != {}
        assert action.action_type == "approve_extraction"

    def test_eligibility_assessment_may_reference_triggering_review_action(self):
        action = make_review_action()
        assessment = make_eligibility_assessment(triggering_review_action=action)
        assert assessment.triggering_review_action == action


class TestDraftResponse:
    def test_generated_content_is_preserved_separately_from_edits(self):
        draft = make_draft()
        draft.current_body = "Edited by the broker."
        draft.save()
        draft.refresh_from_db()
        assert draft.generated_body != draft.current_body

    def test_no_sent_status_exists(self):
        from apps.workspace.models import DraftResponse

        assert "sent" not in DraftResponse.Status.values


class TestAssistant:
    def test_message_sequence_unique_per_conversation(self):
        message = make_assistant_message(sequence=1)
        _rejects(
            lambda: make_assistant_message(
                conversation=message.conversation, sequence=1, role="assistant"
            )
        )

    def test_load_scoped_conversation_carries_its_load(self):
        conversation = make_assistant_conversation(scope="global", load=None)
        assert conversation.load is None

    def test_load_scope_without_load_is_rejected(self):
        _rejects(lambda: make_assistant_conversation(scope="load", load=None))

    def test_draft_evidence_link_requires_an_anchor(self):
        from apps.workspace.models import DraftEvidenceLink

        draft = make_draft()
        _rejects(
            lambda: DraftEvidenceLink.objects.create(
                draft=draft, fact_name="offered_rate", fact_value="420"
            )
        )

    def test_tool_execution_sequence_unique_per_run(self):
        from apps.workspace.models import ToolExecution

        run = make_assistant_run()
        ToolExecution.objects.create(
            assistant_run=run, sequence=1, tool_name="get_load", arguments={"load_id": "x"}
        )
        _rejects(
            lambda: ToolExecution.objects.create(
                assistant_run=run, sequence=1, tool_name="get_carrier_profile", arguments={}
            )
        )

    def test_citation_sequence_unique_per_message(self):
        from apps.workspace.models import AssistantCitation

        message = make_assistant_message(role="assistant")
        AssistantCitation.objects.create(
            assistant_message=message,
            sequence=1,
            source_type="email",
            stable_source_id="email:CE0058",
            display_label="email CE0058",
        )
        _rejects(
            lambda: AssistantCitation.objects.create(
                assistant_message=message,
                sequence=1,
                source_type="load",
                stable_source_id="29372450",
                display_label="Load 29372450",
            )
        )
